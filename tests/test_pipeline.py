from __future__ import annotations

import csv
import tempfile
import unittest
from pathlib import Path
from typing import Any

from services.agent.app.orchestrator import AgentOrchestrator, AuditLog
from services.agent.app.planner import HeuristicPlanner, Planner
from services.analysis.app.engine import AnalysisEngine
from services.analysis.app.repository import CsvRepository


WINDOW_FIELDS = [
    "window_id",
    "t_start_s",
    "t_end_s",
    "flight_phase_majority",
    "armed_fraction",
    "airborne_fraction",
    "mode_name_majority",
    "n_fbg_timestamp_rows",
    "n_fbg_valid_samples",
    "fbg_validity_ratio",
    "fbg_missing_fraction",
    "fbg_delta_mean_nm",
    "fbg_delta_std_nm",
    "fbg_delta_rms_nm",
    "fbg_delta_p2p_nm",
    "roll_mean_deg",
    "roll_std_deg",
    "pitch_mean_deg",
    "pitch_std_deg",
    "yaw_mean_deg",
    "pidr_err_rms",
    "pidp_err_rms",
]


class InProcessClient:
    def __init__(self, engine: AnalysisEngine) -> None:
        self.engine = engine

    def call_tool(self, tool: str, window_id: str) -> dict[str, Any]:
        functions = {
            "check_quality": self.engine.quality,
            "get_context": self.engine.context,
            "compare_neighbors": self.engine.neighbors,
            "get_evidence": self.engine.evidence,
        }
        return functions[tool](window_id)


class FailIfInvokedPlanner(Planner):
    backend_name = "ollama"

    def next_action(
        self, window_id: str, observations: dict[str, Any], tools_called: list[str]
    ) -> str:
        raise AssertionError("planner must not run when quality is insufficient")

    def finalize(
        self, window_id: str, observations: dict[str, Any]
    ) -> dict[str, Any]:
        raise AssertionError("finalizer must not run when quality is insufficient")


class PipelineTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        base = Path(self.temp_dir.name)
        windows_path = base / "windows.csv"
        timeseries_path = base / "timeseries.csv"

        windows = [
            {
                "window_id": "W001",
                "t_start_s": 0,
                "t_end_s": 2,
                "flight_phase_majority": "preflight",
                "armed_fraction": 0,
                "airborne_fraction": 0,
                "mode_name_majority": "LOITER",
                "n_fbg_timestamp_rows": 20,
                "n_fbg_valid_samples": 20,
                "fbg_validity_ratio": 1,
                "fbg_missing_fraction": 0,
                "fbg_delta_mean_nm": 0,
                "fbg_delta_std_nm": 0.001,
                "fbg_delta_rms_nm": 0.001,
                "fbg_delta_p2p_nm": 0.003,
            },
            {
                "window_id": "W002",
                "t_start_s": 2,
                "t_end_s": 4,
                "flight_phase_majority": "armed_ground",
                "armed_fraction": 1,
                "airborne_fraction": 0.5,
                "mode_name_majority": "LOITER",
                "n_fbg_timestamp_rows": 20,
                "n_fbg_valid_samples": 20,
                "fbg_validity_ratio": 1,
                "fbg_missing_fraction": 0,
                "fbg_delta_mean_nm": 0.02,
                "fbg_delta_std_nm": 0.05,
                "fbg_delta_rms_nm": 0.06,
                "fbg_delta_p2p_nm": 0.30,
            },
            {
                "window_id": "W003",
                "t_start_s": 4,
                "t_end_s": 6,
                "flight_phase_majority": "airborne",
                "armed_fraction": 1,
                "airborne_fraction": 1,
                "mode_name_majority": "LOITER",
                "n_fbg_timestamp_rows": 20,
                "n_fbg_valid_samples": 6,
                "fbg_validity_ratio": 0.30,
                "fbg_missing_fraction": 0.70,
                "fbg_delta_mean_nm": 0.03,
                "fbg_delta_std_nm": 0.23,
                "fbg_delta_rms_nm": 0.24,
                "fbg_delta_p2p_nm": 0.58,
            },
        ]
        with windows_path.open("w", encoding="utf-8", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=WINDOW_FIELDS)
            writer.writeheader()
            writer.writerows(windows)

        timeseries_fields = [
            "t_from_fbg_start_s",
            "timestamp_local_iso",
            "flight_phase",
            "event_arm",
            "event_flight_onset",
            "event_land",
            "event_disarm",
        ]
        timeseries = [
            {
                "t_from_fbg_start_s": 3,
                "timestamp_local_iso": "2026-08-03T14:22:22+08:00",
                "flight_phase": "armed_ground",
                "event_arm": 0,
                "event_flight_onset": 1,
                "event_land": 0,
                "event_disarm": 0,
            }
        ]
        with timeseries_path.open("w", encoding="utf-8", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=timeseries_fields)
            writer.writeheader()
            writer.writerows(timeseries)

        repository = CsvRepository(windows_path, timeseries_path)
        self.engine = AnalysisEngine(repository, validity_threshold=0.80)
        self.audit_path = base / "audit.jsonl"

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def test_invalid_fbg_is_blocked(self) -> None:
        quality = self.engine.quality("W003")
        self.assertFalse(quality["sufficient"])
        self.assertEqual(
            self.engine.evidence("W003")["rule_suggestion"],
            "INSUFFICIENT_DATA",
        )

    def test_flight_event_produces_transition_suggestion(self) -> None:
        result = self.engine.evidence("W002")
        self.assertEqual(result["rule_suggestion"], "TRANSITION_ASSOCIATED")
        self.assertEqual(result["flight_context"]["events_in_window"][0]["event"], "flight_onset")

    def test_agent_enforces_guardrail_and_writes_audit_log(self) -> None:
        orchestrator = AgentOrchestrator(
            client=InProcessClient(self.engine),
            planner=HeuristicPlanner(),
            audit_log=AuditLog(self.audit_path),
        )
        result = orchestrator.analyze("W003")
        self.assertEqual(result["decision"], "INSUFFICIENT_DATA")
        self.assertTrue(result["guardrail_applied"])
        self.assertEqual(result["tools_called"], ["check_quality"])
        self.assertFalse(result["planner_invoked"])
        self.assertFalse(result["llm_invoked"])
        self.assertEqual(result["planner_trace"][0]["reason"], "mandatory-preflight")
        self.assertTrue(self.audit_path.exists())

    def test_invalid_fbg_never_invokes_llm_planner(self) -> None:
        orchestrator = AgentOrchestrator(
            client=InProcessClient(self.engine),
            planner=FailIfInvokedPlanner(),
            audit_log=AuditLog(self.audit_path),
        )
        result = orchestrator.analyze("W003")
        self.assertEqual(result["decision"], "INSUFFICIENT_DATA")
        self.assertFalse(result["planner_invoked"])
        self.assertFalse(result["llm_invoked"])

    def test_agent_calls_multiple_tools_for_valid_window(self) -> None:
        orchestrator = AgentOrchestrator(
            client=InProcessClient(self.engine),
            planner=HeuristicPlanner(),
            audit_log=AuditLog(self.audit_path),
        )
        result = orchestrator.analyze("W002")
        self.assertEqual(result["decision"], "TRANSITION_ASSOCIATED")
        self.assertIn("get_context", result["tools_called"])
        self.assertIn("compare_neighbors", result["tools_called"])
        self.assertIn("get_evidence", result["tools_called"])
        self.assertTrue(result["planner_invoked"])
        self.assertFalse(result["llm_invoked"])

    def test_window_listing_uses_normalized_ids(self) -> None:
        self.assertEqual(
            self.engine.list_windows(),
            {"count": 3, "window_ids": ["W001", "W002", "W003"]},
        )


if __name__ == "__main__":
    unittest.main()
