from __future__ import annotations

from typing import Any

from .repository import CsvRepository, normalize_window_id, number


DECISIONS = {
    "STATE_CONSISTENT",
    "TRANSITION_ASSOCIATED",
    "NOT_ATTRIBUTABLE_TO_FLIGHT_STATE",
    "INSUFFICIENT_DATA",
}


class AnalysisEngine:
    def __init__(self, repository: CsvRepository, validity_threshold: float = 0.80) -> None:
        if not 0.0 <= validity_threshold <= 1.0:
            raise ValueError("validity_threshold must be between 0 and 1")
        self.repository = repository
        self.validity_threshold = validity_threshold

    def quality(self, window_id: str | int) -> dict[str, Any]:
        row = self.repository.get_window(window_id)
        ratio = number(row.get("fbg_validity_ratio")) or 0.0
        return {
            "window_id": normalize_window_id(window_id),
            "fbg_validity_ratio": ratio,
            "threshold": self.validity_threshold,
            "sufficient": ratio >= self.validity_threshold,
            "n_timestamp_rows": int(number(row.get("n_fbg_timestamp_rows")) or 0),
            "n_valid_samples": int(number(row.get("n_fbg_valid_samples")) or 0),
            "missing_fraction": number(row.get("fbg_missing_fraction")),
        }

    def context(self, window_id: str | int) -> dict[str, Any]:
        row = self.repository.get_window(window_id)
        start_s = number(row.get("t_start_s")) or 0.0
        end_s = number(row.get("t_end_s")) or start_s
        return {
            "window_id": normalize_window_id(window_id),
            "t_start_s": start_s,
            "t_end_s": end_s,
            "flight_phase": row.get("flight_phase_majority"),
            "mode_name": row.get("mode_name_majority"),
            "armed_fraction": number(row.get("armed_fraction")),
            "airborne_fraction": number(row.get("airborne_fraction")),
            "events_in_window": self.repository.events_for_interval(start_s, end_s),
            "attitude": {
                "roll_mean_deg": number(row.get("roll_mean_deg")),
                "roll_std_deg": number(row.get("roll_std_deg")),
                "pitch_mean_deg": number(row.get("pitch_mean_deg")),
                "pitch_std_deg": number(row.get("pitch_std_deg")),
                "yaw_mean_deg": number(row.get("yaw_mean_deg")),
            },
            "control_error": {
                "roll_pid_rms": number(row.get("pidr_err_rms")),
                "pitch_pid_rms": number(row.get("pidp_err_rms")),
            },
        }

    @staticmethod
    def _window_summary(row: dict[str, str]) -> dict[str, Any]:
        return {
            "window_id": normalize_window_id(row["window_id"]),
            "t_start_s": number(row.get("t_start_s")),
            "t_end_s": number(row.get("t_end_s")),
            "flight_phase": row.get("flight_phase_majority"),
            "armed_fraction": number(row.get("armed_fraction")),
            "airborne_fraction": number(row.get("airborne_fraction")),
            "fbg_validity_ratio": number(row.get("fbg_validity_ratio")),
            "fbg_delta_rms_nm": number(row.get("fbg_delta_rms_nm")),
            "fbg_delta_std_nm": number(row.get("fbg_delta_std_nm")),
            "fbg_delta_p2p_nm": number(row.get("fbg_delta_p2p_nm")),
        }

    def neighbors(self, window_id: str | int, radius: int = 1) -> dict[str, Any]:
        return {
            "window_id": normalize_window_id(window_id),
            "radius": radius,
            "windows": [
                self._window_summary(row)
                for row in self.repository.get_neighbors(window_id, radius)
            ],
        }

    def evidence(self, window_id: str | int) -> dict[str, Any]:
        row = self.repository.get_window(window_id)
        quality = self.quality(window_id)
        context = self.context(window_id)
        metrics = {
            "fbg_delta_mean_nm": number(row.get("fbg_delta_mean_nm")),
            "fbg_delta_std_nm": number(row.get("fbg_delta_std_nm")),
            "fbg_delta_rms_nm": number(row.get("fbg_delta_rms_nm")),
            "fbg_delta_p2p_nm": number(row.get("fbg_delta_p2p_nm")),
        }
        baseline = self.repository.phase_baseline(
            context["flight_phase"], "fbg_delta_rms_nm", self.validity_threshold
        )
        robust_z = None
        if (
            metrics["fbg_delta_rms_nm"] is not None
            and baseline["median"] is not None
            and baseline["mad"] not in (None, 0.0)
        ):
            robust_z = (
                0.6745
                * (metrics["fbg_delta_rms_nm"] - baseline["median"])
                / baseline["mad"]
            )

        suggestion, reasons = self._rule_suggestion(
            quality=quality,
            context=context,
            robust_z=robust_z,
        )
        return {
            "window_id": normalize_window_id(window_id),
            "quality": quality,
            "flight_context": context,
            "fbg_metrics": metrics,
            "phase_baseline": baseline,
            "robust_z": robust_z,
            "rule_suggestion": suggestion,
            "rule_reasons": reasons,
        }

    @staticmethod
    def _rule_suggestion(
        quality: dict[str, Any], context: dict[str, Any], robust_z: float | None
    ) -> tuple[str, list[str]]:
        if not quality["sufficient"]:
            return (
                "INSUFFICIENT_DATA",
                [
                    f"FBG validity {quality['fbg_validity_ratio']:.2f} is below "
                    f"threshold {quality['threshold']:.2f}"
                ],
            )

        armed = context.get("armed_fraction")
        airborne = context.get("airborne_fraction")
        transition_fraction = any(
            value is not None and 0.0 < value < 1.0 for value in (armed, airborne)
        )
        if context.get("events_in_window") or transition_fraction:
            reasons = ["Flight-state transition is present in the window"]
            reasons.extend(
                f"Event {event['event']} occurred at {event['t_s']:.3f} s"
                for event in context.get("events_in_window", [])
            )
            return "TRANSITION_ASSOCIATED", reasons

        if robust_z is not None and abs(robust_z) >= 3.5:
            return (
                "NOT_ATTRIBUTABLE_TO_FLIGHT_STATE",
                [f"FBG RMS differs from the same-phase baseline (robust z={robust_z:.2f})"],
            )

        return (
            "STATE_CONSISTENT",
            ["No flight transition or unexplained large FBG deviation was established"],
        )

