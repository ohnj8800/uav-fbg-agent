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

    def analyze(self, window_id: str) -> dict[str, Any]:
        request_id = str(uuid.uuid4())
        observations: dict[str, Any] = {}
        tools_called: list[str] = []
        planner_trace: list[dict[str, str]] = []

        for _ in range(self.max_steps):
            action = self.planner.next_action(window_id, observations, tools_called)

            # Mandatory guardrail: an LLM cannot skip the quality check.
            if "check_quality" not in observations and action != "check_quality":
                planner_trace.append(
                    {"requested": action, "executed": "check_quality", "reason": "guardrail"}
                )
                action = "check_quality"
            else:
                planner_trace.append(
                    {"requested": action, "executed": action, "reason": "planner"}
                )

            if action == "finalize":
                break
            if action not in {
                "check_quality",
                "get_context",
                "compare_neighbors",
                "get_evidence",
            }:
                raise RuntimeError(f"Planner produced unsupported action: {action}")

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

            quality = observations.get("check_quality", {})
            if quality and not quality.get("sufficient", False):
                break

        quality = observations.get("check_quality")
        if quality is None:
            quality = self.client.call_tool("check_quality", window_id)
            observations["check_quality"] = quality
            tools_called.append("check_quality")
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
                observations["get_evidence"] = self.client.call_tool(
                    "get_evidence", window_id
                )
                tools_called.append("get_evidence")
            final = self.planner.finalize(window_id, observations)
            decision = final.get("decision")
            evidence = final.get("evidence") or observations["get_evidence"].get(
                "rule_reasons", []
            )
            if decision not in ALLOWED_DECISIONS:
                raise RuntimeError(f"Invalid final decision: {decision!r}")

        timestamp = datetime.now(timezone.utc).isoformat()
        result = {
            "request_id": request_id,
            "timestamp_utc": timestamp,
            "window_id": canonical_window_id,
            "decision": decision,
            "evidence": evidence,
            "tools_called": tools_called,
            "guardrail_applied": guardrail_applied,
            "planner_backend": self.planner.backend_name,
            "planner_trace": planner_trace,
        }
        warning = getattr(self.planner, "last_warning", None)
        if warning:
            result["warning"] = warning
        self.audit_log.append({**result, "observations": observations})
        return result
