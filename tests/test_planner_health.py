from __future__ import annotations

import json
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from services.agent.app.planner import OllamaPlanner


class OllamaTagsHandler(BaseHTTPRequestHandler):
    def log_message(self, format: str, *args: object) -> None:
        pass

    def do_GET(self) -> None:  # noqa: N802
        if self.path != "/api/tags":
            self.send_error(404)
            return
        body = json.dumps(
            {"models": [{"name": "qwen3:8b", "model": "qwen3:8b"}]}
        ).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


class PlannerHealthTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.server = ThreadingHTTPServer(("127.0.0.1", 0), OllamaTagsHandler)
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()
        cls.base_url = f"http://127.0.0.1:{cls.server.server_address[1]}"

    @classmethod
    def tearDownClass(cls) -> None:
        cls.server.shutdown()
        cls.server.server_close()

    def test_configured_model_is_ready(self) -> None:
        health = OllamaPlanner(self.base_url, "qwen3:8b").health()
        self.assertTrue(health["reachable"])
        self.assertTrue(health["ready"])
        self.assertTrue(health["model_available"])
        self.assertEqual(health["model"], "qwen3:8b")

    def test_missing_model_is_not_ready(self) -> None:
        health = OllamaPlanner(self.base_url, "missing:8b").health()
        self.assertTrue(health["reachable"])
        self.assertFalse(health["ready"])
        self.assertFalse(health["model_available"])


if __name__ == "__main__":
    unittest.main()
