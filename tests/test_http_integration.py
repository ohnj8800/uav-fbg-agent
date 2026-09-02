from __future__ import annotations

import json
import tempfile
import threading
import unittest
from pathlib import Path
from urllib.request import Request, urlopen

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

    def test_w008_end_to_end_transition(self) -> None:
        result = self.analyze("W008")
        self.assertEqual(result["decision"], "TRANSITION_ASSOCIATED")
        self.assertFalse(result["guardrail_applied"])
        self.assertEqual(
            result["tools_called"],
            ["check_quality", "get_context", "compare_neighbors", "get_evidence"],
        )


if __name__ == "__main__":
    unittest.main()

