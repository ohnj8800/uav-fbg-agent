from __future__ import annotations

import unittest
from typing import Any
from urllib.error import URLError

from services.agent.app.planner import OllamaPlanner


class FailingOllamaPlanner(OllamaPlanner):
    def _generate_json(self, prompt: str) -> dict[str, Any]:
        raise URLError("simulated offline model")


class SuccessfulOllamaPlanner(OllamaPlanner):
    def _generate_json(self, prompt: str) -> dict[str, Any]:
        if "Select exactly one next action" in prompt:
            return {"action": "finalize", "reason": "enough evidence"}
        return {"decision": "STATE_CONSISTENT", "evidence": ["grounded"]}


class PlannerRuntimeTest(unittest.TestCase):
    def test_successful_calls_are_counted(self) -> None:
        planner = SuccessfulOllamaPlanner("http://unused", "qwen3:8b")
        planner.begin_request()
        action = planner.next_action("W001", {"check_quality": {}}, [])
        result = planner.finalize("W001", {"get_evidence": {}})
        runtime = planner.runtime_metadata()
        self.assertEqual(action, "finalize")
        self.assertEqual(result["decision"], "STATE_CONSISTENT")
        self.assertEqual(runtime["llm_attempts"], 2)
        self.assertEqual(runtime["llm_successes"], 2)
        self.assertTrue(runtime["llm_succeeded"])
        self.assertFalse(runtime["fallback_used"])
        self.assertEqual(runtime["effective_backend"], "ollama")

    def test_failed_calls_record_fallback_without_losing_warnings(self) -> None:
        planner = FailingOllamaPlanner("http://unused", "qwen3:8b")
        planner.begin_request()
        action = planner.next_action(
            "W001", {"check_quality": {"sufficient": True}}, ["check_quality"]
        )
        planner.finalize(
            "W001",
            {
                "get_evidence": {
                    "rule_suggestion": "STATE_CONSISTENT",
                    "rule_reasons": ["deterministic fallback"],
                }
            },
        )
        runtime = planner.runtime_metadata()
        self.assertEqual(action, "get_context")
        self.assertEqual(runtime["llm_attempts"], 2)
        self.assertEqual(runtime["llm_successes"], 0)
        self.assertFalse(runtime["llm_succeeded"])
        self.assertTrue(runtime["fallback_used"])
        self.assertEqual(runtime["effective_backend"], "heuristic_fallback")
        self.assertEqual(len(runtime["warnings"]), 2)


if __name__ == "__main__":
    unittest.main()
