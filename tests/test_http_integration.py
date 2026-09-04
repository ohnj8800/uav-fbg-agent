from __future__ import annotations

import json
import tempfile
import threading
import unittest
from pathlib import Path
from urllib.request import Request, urlopen

from scripts.analyze_batch import run_batch
from scripts.generate_deliverables import generate_deliverables
from services.agent.app.config import Settings as AgentSettings
from services.agent.app.server import create_server as create_agent_server
from services.analysis.app.config import Settings as AnalysisSettings
from services.analysis.app.server import create_server as create_analysis_server


class HttpIntegrationTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        base = Path(__file__).resolve().parents[1]
        windows = base / "data" / "window_features.csv"
        timeseries = base / "data" / "synchronized_timeseries.csv"
        if not windows.exists() or not timeseries.exists():
            raise unittest.SkipTest("Lab CSV files are not present")

        cls.temp_dir = tempfile.TemporaryDirectory()
        cls.publication_dir = Path(cls.temp_dir.name) / "publication-deliverables"
        cls.analysis_server = create_analysis_server(
            AnalysisSettings(
                window_features_csv=windows,
                synchronized_timeseries_csv=timeseries,
                fbg_validity_threshold=0.80,
                host="127.0.0.1",
                port=0,
            )
        )
        analysis_port = cls.analysis_server.server_address[1]
        cls.analysis_thread = threading.Thread(
            target=cls.analysis_server.serve_forever, daemon=True
        )
        cls.analysis_thread.start()

        cls.agent_server = create_agent_server(
            AgentSettings(
                analysis_base_url=f"http://127.0.0.1:{analysis_port}",
                llm_mode="heuristic",
                max_steps=6,
                audit_log_path=Path(cls.temp_dir.name) / "audit.jsonl",
                deliverables_dir=cls.publication_dir,
                host="127.0.0.1",
                port=0,
            )
        )
        cls.agent_port = cls.agent_server.server_address[1]
        cls.agent_thread = threading.Thread(
            target=cls.agent_server.serve_forever, daemon=True
        )
        cls.agent_thread.start()

    @classmethod
    def tearDownClass(cls) -> None:
        cls.agent_server.shutdown()
        cls.analysis_server.shutdown()
        cls.agent_server.server_close()
        cls.analysis_server.server_close()
        cls.temp_dir.cleanup()

    @classmethod
    def analyze(cls, window_id: str) -> dict[str, object]:
        request = Request(
            f"http://127.0.0.1:{cls.agent_port}/v1/analyze",
            data=json.dumps({"window_id": window_id}).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urlopen(request, timeout=5) as response:
            return json.loads(response.read().decode("utf-8"))

    def test_w027_end_to_end_guardrail(self) -> None:
        result = self.analyze("W027")
        self.assertEqual(result["decision"], "INSUFFICIENT_DATA")
        self.assertTrue(result["guardrail_applied"])
        self.assertEqual(result["tools_called"], ["check_quality"])
        self.assertFalse(result["planner_invoked"])
        self.assertFalse(result["llm_invoked"])
        self.assertEqual(result["llm_attempts"], 0)
        self.assertEqual(result["effective_backend"], "not_invoked")
        self.assertAlmostEqual(
            result["evidence_data"]["fbg"]["validity_ratio"], 0.30
        )
        self.assertIsNone(result["evidence_data"]["real_flight_context"])
        self.assertIsNone(result["planner_model"])

    def test_w008_end_to_end_transition(self) -> None:
        result = self.analyze("W008")
        self.assertEqual(result["decision"], "TRANSITION_ASSOCIATED")
        self.assertFalse(result["guardrail_applied"])
        self.assertEqual(
            result["tools_called"],
            ["check_quality", "get_context", "compare_neighbors", "get_evidence"],
        )
        self.assertTrue(result["planner_invoked"])
        self.assertFalse(result["llm_invoked"])
        self.assertEqual(result["effective_backend"], "heuristic")
        self.assertEqual(
            result["evidence_data"]["real_flight_context"]["flight_phase"],
            "armed_ground",
        )
        self.assertIsNotNone(result["evidence_data"]["fbg"]["rms_nm"])
        self.assertIsNone(result["planner_model"])

    def test_health_reports_planner_readiness(self) -> None:
        with urlopen(
            f"http://127.0.0.1:{self.agent_port}/health", timeout=5
        ) as response:
            result = json.loads(response.read().decode("utf-8"))
        self.assertEqual(result["status"], "ok")
        self.assertEqual(result["analysis_service"], "ok")
        self.assertEqual(result["planner"]["backend"], "heuristic")
        self.assertTrue(result["planner"]["ready"])
        self.assertIsNone(result["planner"]["model"])

    def test_agent_lists_windows_without_exposing_csv(self) -> None:
        with urlopen(
            f"http://127.0.0.1:{self.agent_port}/v1/windows", timeout=5
        ) as response:
            result = json.loads(response.read().decode("utf-8"))
        self.assertEqual(result["count"], len(result["window_ids"]))
        self.assertIn("W008", result["window_ids"])
        self.assertIn("W027", result["window_ids"])

    def test_web_interface_and_visualization_are_available(self) -> None:
        with urlopen(f"http://127.0.0.1:{self.agent_port}/", timeout=5) as response:
            html = response.read().decode("utf-8")
        self.assertIn("UAV / FBG Restricted Interpretation", html)
        with urlopen(
            f"http://127.0.0.1:{self.agent_port}/v1/windows/W004/visualization",
            timeout=5,
        ) as response:
            plot = json.loads(response.read().decode("utf-8"))
        self.assertEqual(plot["window_id"], "W004")
        self.assertEqual(plot["context_source"], "VERIFIED_REAL_STATE")
        self.assertGreater(len(plot["samples"]), 0)
        with urlopen(
            f"http://127.0.0.1:{self.agent_port}/paper", timeout=5
        ) as response:
            publication_html = response.read().decode("utf-8")
        self.assertIn("Contextual interpretation examples", publication_html)
        self.assertIn("FBG evidence + verified flight-state context", publication_html)
        self.assertNotIn("Windows processed", publication_html)

    def test_batch_client_writes_csv_jsonl_and_summary(self) -> None:
        output_dir = Path(self.temp_dir.name) / "batch-test"
        summary = run_batch(
            f"http://127.0.0.1:{self.agent_port}", output_dir, limit=2, timeout_s=5
        )
        self.assertEqual(summary["requested"], 2)
        self.assertEqual(summary["completed"], 2)
        self.assertEqual(summary["failed"], 0)
        self.assertEqual(sum(summary["decision_counts"].values()), 2)
        for output_path in summary["outputs"].values():
            self.assertTrue(Path(output_path).exists())

    def test_requested_deliverables_and_three_figures_are_created(self) -> None:
        base = Path(__file__).resolve().parents[1]
        output_dir = self.publication_dir
        summary = generate_deliverables(
            f"http://127.0.0.1:{self.agent_port}",
            output_dir,
            base / "data" / "window_features.csv",
            base / "data" / "synchronized_timeseries.csv",
            ["W003", "W004", "W027"],
            selected_windows=["W003", "W004", "W027"],
            timeout_s=5,
        )
        self.assertEqual(summary["windows_completed"], 3)
        self.assertEqual(summary["contract_valid"], 3)
        self.assertTrue((output_dir / "llm_window_outputs.csv").exists())
        self.assertTrue((output_dir / "agent_trace.jsonl").exists())
        self.assertTrue((output_dir / "llm_eval.csv").exists())
        self.assertEqual(len(summary["outputs"]["window_figures"]), 3)
        for figure in summary["outputs"]["window_figures"]:
            self.assertIn("<svg", Path(figure).read_text(encoding="utf-8"))
        with urlopen(
            f"http://127.0.0.1:{self.agent_port}/v1/publication", timeout=5
        ) as response:
            publication = json.loads(response.read().decode("utf-8"))
        self.assertEqual(publication["summary"]["windows"], 3)
        self.assertEqual(publication["summary"]["contract_valid"], 3)
        self.assertEqual(
            [row["window_id"] for row in publication["representative_windows"]],
            ["W003", "W004", "W027"],
        )


if __name__ == "__main__":
    unittest.main()
