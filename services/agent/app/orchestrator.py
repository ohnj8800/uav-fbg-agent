from __future__ import annotations

import json
import threading
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Protocol

from .planner import ALLOWED_DECISIONS, Planner


class ToolClient(Protocol):
    def call_tool(self, tool: str, window_id: str) -> dict[str, Any]: ...


class AuditLog:
    def __init__(self, path: Path) -> None:
        self.path = Path(path)
        self._lock = threading.Lock()

    def append(self, record: dict[str, Any]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        line = json.dumps(record, ensure_ascii=False, separators=(",", ":"))
        with self._lock, self.path.open("a", encoding="utf-8") as handle:
            handle.write(line + "\n")


class AgentOrchestrator:
    def __init__(
        self,
        client: ToolClient,
        planner: Planner,
        audit_log: AuditLog,
        max_steps: int = 6,
    ) -> None:
        self.client = client
        self.planner = planner
        self.audit_log = audit_log
        self.max_steps = max_steps

    @staticmethod
    def _structured_evidence(
        quality: dict[str, Any], observations: dict[str, Any]
    ) -> dict[str, Any]:
        analysis_evidence = observations.get("get_evidence", {})
        raw_metrics = analysis_evidence.get("fbg_metrics", {})
        quality_metrics = quality.get("fbg_metrics", {})
        context = analysis_evidence.get("flight_context") or observations.get(
            "get_context"
        )

        real_flight_context = None
        if context:
            attitude = context.get("attitude", {})
            real_flight_context = {
                "t_start_s": context.get("t_start_s"),
                "t_end_s": context.get("t_end_s"),
                "flight_phase": context.get("flight_phase"),
                "mode_name": context.get("mode_name"),
                "armed_fraction": context.get("armed_fraction"),
                "airborne_fraction": context.get("airborne_fraction"),
                "roll_mean_deg": attitude.get("roll_mean_deg"),
                "roll_std_deg": attitude.get("roll_std_deg"),
                "pitch_mean_deg": attitude.get("pitch_mean_deg"),
                "pitch_std_deg": attitude.get("pitch_std_deg"),
                "events_in_window": context.get("events_in_window", []),
            }

        return {
            "fbg": {
                "validity_ratio": quality.get("fbg_validity_ratio"),
                "validity_threshold": quality.get("threshold"),
                "std_nm": raw_metrics.get(
                    "fbg_delta_std_nm", quality_metrics.get("std_nm")
                ),
                "rms_nm": raw_metrics.get(
                    "fbg_delta_rms_nm", quality_metrics.get("rms_nm")
                ),
                "p2p_nm": raw_metrics.get(
                    "fbg_delta_p2p_nm", quality_metrics.get("p2p_nm")
                ),
            },
            "real_flight_context": real_flight_context,
        }

    def analyze(self, window_id: str) -> dict[str, Any]:
        self.planner.begin_request()
        request_id = str(uuid.uuid4())
        quality = self.client.call_tool("check_quality", window_id)
        observations: dict[str, Any] = {"check_quality": quality}
        tools_called = ["check_quality"]
        planner_trace: list[dict[str, str]] = [
            {
                "requested": "check_quality",
                "executed": "check_quality",
                "reason": "mandatory-preflight",
            }
        ]
        planner_invoked = False

        if quality.get("sufficient", False):
            for _ in range(self.max_steps):
                planner_invoked = True
                action = self.planner.next_action(window_id, observations, tools_called)

                if action == "finalize":
                    if "get_evidence" not in observations:
                        planner_trace.append(
                            {
                                "requested": "finalize",
                                "executed": "get_evidence",
                                "reason": "mandatory-evidence-before-finalize",
                            }
                        )
                        observations["get_evidence"] = self.client.call_tool(
                            "get_evidence", window_id
                        )
                        tools_called.append("get_evidence")
                    break
                if action not in {
                    "check_quality",
                    "get_context",
                    "compare_neighbors",
                    "get_evidence",
                }:
                    raise RuntimeError(f"Planner produced unsupported action: {action}")

                planner_trace.append(
                    {"requested": action, "executed": action, "reason": "planner"}
                )

                # Avoid repeated calls when a model loops.
                if action in observations:
                    for fallback_action in (
                        "get_context",
                        "compare_neighbors",
                        "get_evidence",
                    ):
                        if fallback_action not in observations:
                            action = fallback_action
                            planner_trace[-1]["executed"] = action
                            planner_trace[-1]["reason"] = "loop-prevention"
                            break
                    else:
                        break

                observations[action] = self.client.call_tool(action, window_id)
                tools_called.append(action)

        canonical_window_id = str(quality.get("window_id", window_id))

        guardrail_applied = not quality.get("sufficient", False)
        if guardrail_applied:
            decision = "INSUFFICIENT_DATA"
            evidence = [
                f"FBG validity is {quality.get('fbg_validity_ratio', 0.0):.2f}",
                f"Required threshold is {quality.get('threshold', 0.0):.2f}",
                "The deterministic safety rule blocked interpretation",
            ]
        else:
            if "get_evidence" not in observations:
                planner_trace.append(
                    {
                        "requested": "finalize",
                        "executed": "get_evidence",
                        "reason": "mandatory-evidence-before-finalize",
                    }
                )
                observations["get_evidence"] = self.client.call_tool(
                    "get_evidence", window_id
                )
                tools_called.append("get_evidence")
            planner_trace.append(
                {
                    "requested": "finalize",
                    "executed": "finalize",
                    "reason": "evidence-complete",
                }
            )
            final = self.planner.finalize(window_id, observations)
            decision = final.get("decision")
            evidence = final.get("evidence") or observations["get_evidence"].get(
                "rule_reasons", []
            )
            if decision not in ALLOWED_DECISIONS:
                raise RuntimeError(f"Invalid final decision: {decision!r}")

        timestamp = datetime.now(timezone.utc).isoformat()
        runtime = self.planner.runtime_metadata()
        if not planner_invoked:
            runtime["effective_backend"] = "not_invoked"
        result = {
            "request_id": request_id,
            "timestamp_utc": timestamp,
            "window_id": canonical_window_id,
            "decision": decision,
            "evidence": evidence,
            "evidence_data": self._structured_evidence(quality, observations),
            "tools_called": tools_called,
            "guardrail_applied": guardrail_applied,
            "planner_backend": self.planner.backend_name,
            "planner_model": self.planner.model_name,
            "planner_invoked": planner_invoked,
            "llm_invoked": runtime["llm_attempts"] > 0,
            "llm_attempts": runtime["llm_attempts"],
            "llm_successes": runtime["llm_successes"],
            "llm_succeeded": runtime["llm_succeeded"],
            "fallback_used": runtime["fallback_used"],
            "effective_backend": runtime["effective_backend"],
            "planner_warnings": runtime["warnings"],
            "planner_trace": planner_trace,
        }
        self.audit_log.append({**result, "observations": observations})
        return result
