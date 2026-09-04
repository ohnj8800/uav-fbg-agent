from __future__ import annotations

import json
import threading
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Protocol

from .planner import ALLOWED_DECISIONS, PROMPT_VERSION, Planner


CONTEXT_SOURCE = "REAL_LOG"
RESULT_STAGE = "DEVELOPMENT"


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
                "context_validity": context.get("context_validity"),
                "reliable_overlap_start_s": context.get("reliable_overlap_start_s"),
                "flight_phase": context.get("flight_phase"),
                "mode_name": context.get("mode_name"),
                "armed_fraction": context.get("armed_fraction"),
                "airborne_fraction": context.get("airborne_fraction"),
                "roll_mean_deg": attitude.get("roll_mean_deg"),
                "roll_std_deg": attitude.get("roll_std_deg"),
                "pitch_mean_deg": attitude.get("pitch_mean_deg"),
                "pitch_std_deg": attitude.get("pitch_std_deg"),
                "rates": context.get("rates", {}),
                "control_error": context.get("control_error", {}),
                "motor_output": context.get("motor_output", {}),
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
            "context_source": CONTEXT_SOURCE,
            "flight_context": real_flight_context,
            "real_flight_context": real_flight_context,
        }

    @staticmethod
    def _tool_trace(planner_trace: list[dict[str, Any]]) -> list[dict[str, Any]]:
        return [
            {
                "step": index,
                "requested_tool": item["requested"],
                "executed_tool": item["executed"],
                "reason_code": item["reason"].replace("-", "_").upper(),
                "reason_summary": item.get("reason_summary"),
                "status": "COMPLETED",
            }
            for index, item in enumerate(planner_trace, start=1)
        ]

    def analyze(self, window_id: str) -> dict[str, Any]:
        self.planner.begin_request()
        request_id = str(uuid.uuid4())
        quality = self.client.call_tool("check_quality", window_id)
        observations: dict[str, Any] = {"check_quality": quality}
        tools_called = ["check_quality"]
        planner_trace: list[dict[str, Any]] = [
            {
                "requested": "check_quality",
                "executed": "check_quality",
                "reason": "mandatory-preflight",
            }
        ]
        planner_invoked = False
        context = self.client.call_tool("get_context", window_id)
        observations["get_context"] = context
        tools_called.append("get_context")
        planner_trace.append(
            {
                "requested": "get_context",
                "executed": "get_context",
                "reason": "mandatory-context-preflight",
            }
        )
        quality_blocked = not quality.get("sufficient", False)
        context_blocked = context.get("context_validity") == "INVALID"

        if not quality_blocked and not context_blocked:
            for _ in range(self.max_steps):
                planner_invoked = True
                action = self.planner.next_action(window_id, observations, tools_called)
                action_reason = self.planner.consume_action_reason()

                if action == "finalize":
                    if "get_evidence" not in observations:
                        planner_trace.append(
                            {
                                "requested": "finalize",
                                "executed": "get_evidence",
                                "reason": "mandatory-evidence-before-finalize",
                                "reason_summary": action_reason,
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
                    {
                        "requested": action,
                        "executed": action,
                        "reason": "planner",
                        "reason_summary": action_reason,
                    }
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

        guardrail_applied = quality_blocked or context_blocked
        if quality_blocked:
            decision = "INSUFFICIENT_DATA"
            abstain_reason = "FBG_VALIDITY_BELOW_THRESHOLD"
            evidence = [
                f"FBG validity is {quality.get('fbg_validity_ratio', 0.0):.2f}",
                f"Required threshold is {quality.get('threshold', 0.0):.2f}",
                "The deterministic safety rule blocked interpretation",
            ]
        elif context_blocked:
            decision = "INSUFFICIENT_DATA"
            abstain_reason = "REAL_LOG_CONTEXT_UNAVAILABLE"
            evidence = [
                f"FBG validity is {quality.get('fbg_validity_ratio', 0.0):.2f}",
                "Reliable REAL_LOG overlap begins at approximately 7.1 s",
                "The deterministic context rule blocked interpretation",
            ]
        else:
            abstain_reason = None
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
            if decision == "INSUFFICIENT_DATA":
                abstain_reason = "INSUFFICIENT_EVIDENCE"

        timestamp = datetime.now(timezone.utc).isoformat()
        runtime = self.planner.runtime_metadata()
        if not planner_invoked:
            runtime["effective_backend"] = "not_invoked"
        abstain = decision == "INSUFFICIENT_DATA"
        context_validity = context.get("context_validity", "UNKNOWN")
        fbg_evidence = [
            (
                f"FBG validity {quality.get('fbg_validity_ratio', 0.0):.2f} "
                f"{'passed' if quality.get('sufficient') else 'failed'} threshold "
                f"{quality.get('threshold', 0.0):.2f}"
            )
        ]
        metrics = quality.get("fbg_metrics", {})
        fbg_evidence.append(
            "FBG window metrics: "
            f"STD={metrics.get('std_nm')}, RMS={metrics.get('rms_nm')}, "
            f"P2P={metrics.get('p2p_nm')} nm"
        )
        context_evidence = [
            f"REAL_LOG context validity is {context_validity}",
            (
                f"Flight phase={context.get('flight_phase')}, "
                f"armed_fraction={context.get('armed_fraction')}, "
                f"airborne_fraction={context.get('airborne_fraction')}"
            ),
        ]

        reason_codes = {
            "FBG_VALIDITY_BELOW_THRESHOLD": "FBG_VALIDITY_BELOW_THRESHOLD",
            "REAL_LOG_CONTEXT_UNAVAILABLE": "REAL_LOG_CONTEXT_UNAVAILABLE",
        }
        if abstain_reason:
            reason_code = reason_codes.get(abstain_reason, "INSUFFICIENT_EVIDENCE")
        elif decision == "TRANSITION_ASSOCIATED":
            reason_code = "REAL_LOG_TRANSITION_ASSOCIATED"
        elif decision == "NOT_ATTRIBUTABLE":
            reason_code = "FBG_VARIABILITY_NOT_ATTRIBUTABLE"
        else:
            reason_code = "STATE_CONTEXT_CONSISTENT"
        tool_trace = self._tool_trace(planner_trace)
        reasoning_trace = [
            {
                "stage": "QUALITY_PREFLIGHT",
                "rule": "FBG_VALIDITY_THRESHOLD",
                "actual": quality.get("fbg_validity_ratio"),
                "threshold": quality.get("threshold"),
                "outcome": "BLOCK" if quality_blocked else "PASS",
            },
            {
                "stage": "CONTEXT_PREFLIGHT",
                "rule": "REAL_LOG_CONTEXT_AVAILABILITY",
                "actual": context_validity,
                "threshold": "PARTIAL_OR_VALID",
                "outcome": "BLOCK" if context_blocked else "PASS",
            },
            *[
                {
                    "stage": "TOOL_SELECTION",
                    "requested_tool": item["requested"],
                    "executed_tool": item["executed"],
                    "reason_code": item["reason"].replace("-", "_").upper(),
                    "reason_summary": item.get("reason_summary"),
                }
                for item in planner_trace
            ],
            {
                "stage": "CONSTRAINED_DECISION",
                "source": (
                    "DETERMINISTIC_GUARDRAIL"
                    if guardrail_applied
                    else "RESTRICTED_PLANNER"
                ),
                "allowed_decisions": sorted(ALLOWED_DECISIONS),
                "outcome": decision,
            },
        ]
        result = {
            "request_id": request_id,
            "timestamp_utc": timestamp,
            "window_id": canonical_window_id,
            "decision": decision,
            "constrained_decision": decision,
            "evidence": evidence,
            "evidence_fbg": fbg_evidence,
            "evidence_context": context_evidence,
            "evidence_data": self._structured_evidence(quality, observations),
            "abstain": abstain,
            "abstained": abstain,
            "abstain_reason": abstain_reason,
            "reason_code": reason_code,
            "context_source": CONTEXT_SOURCE,
            "context_validity": context_validity,
            "result_stage": RESULT_STAGE,
            "tools_called": tools_called,
            "guardrail_applied": guardrail_applied,
            "planner_backend": self.planner.backend_name,
            "planner_model": self.planner.model_name,
            "model_name": self.planner.model_name or self.planner.backend_name,
            "prompt_version": PROMPT_VERSION,
            "planner_invoked": planner_invoked,
            "llm_invoked": runtime["llm_attempts"] > 0,
            "llm_attempts": runtime["llm_attempts"],
            "llm_successes": runtime["llm_successes"],
            "llm_succeeded": runtime["llm_succeeded"],
            "fallback_used": runtime["fallback_used"],
            "effective_backend": runtime["effective_backend"],
            "planner_warnings": runtime["warnings"],
            "planner_trace": planner_trace,
            "tool_trace": tool_trace,
            "reasoning_trace": reasoning_trace,
        }
        self.audit_log.append({**result, "observations": observations})
        return result
