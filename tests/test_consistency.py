from __future__ import annotations

import unittest

from scripts.evaluate_consistency import summarize_window, validate_result


def valid_result(decision: str = "STATE_CONSISTENT") -> dict[str, object]:
    return {
        "decision": decision,
        "constrained_decision": decision,
        "abstain": decision == "INSUFFICIENT_DATA",
        "abstain_reason": (
            "FBG_VALIDITY_BELOW_THRESHOLD"
            if decision == "INSUFFICIENT_DATA"
            else None
        ),
        "context_source": "VERIFIED_REAL_STATE",
        "result_stage": "DEVELOPMENT",
        "evidence": ["Supplied evidence supports the interpretation"],
        "evidence_data": {
            "fbg": {
                "validity_ratio": 1.0,
                "validity_threshold": 0.8,
                "std_nm": 0.1,
                "rms_nm": 0.2,
                "p2p_nm": 0.3,
            },
            "real_flight_context": {"flight_phase": "airborne"},
        },
        "guardrail_applied": False,
        "llm_invoked": True,
        "planner_backend": "ollama",
        "planner_model": "qwen3:8b",
        "llm_attempts": 2,
        "llm_successes": 2,
        "llm_succeeded": True,
        "fallback_used": False,
        "effective_backend": "ollama",
        "planner_warnings": [],
        "tools_called": ["check_quality", "get_evidence"],
        "tool_trace": [{"step": 1, "executed_tool": "check_quality"}],
        "reasoning_trace": [{"stage": "QUALITY_PREFLIGHT", "outcome": "PASS"}],
    }


class ConsistencyValidationTest(unittest.TestCase):
    def test_valid_interpretation_contract(self) -> None:
        self.assertEqual(validate_result(valid_result()), [])

    def test_guardrail_override_is_reported(self) -> None:
        result = valid_result("STATE_CONSISTENT")
        result["guardrail_applied"] = True
        result["llm_invoked"] = True
        violations = validate_result(result)
        self.assertTrue(any("INSUFFICIENT_DATA" in item for item in violations))
        self.assertTrue(any("LLM must not run" in item for item in violations))

    def test_decision_disagreement_is_measured(self) -> None:
        results = [
            valid_result("STATE_CONSISTENT"),
            valid_result("STATE_CONSISTENT"),
            valid_result("TRANSITION_ASSOCIATED"),
        ]
        summary = summarize_window("W001", 3, results, [])
        self.assertFalse(summary["consistent"])
        self.assertAlmostEqual(summary["agreement_rate"], 0.6667)
        self.assertTrue(summary["contract_valid"])


if __name__ == "__main__":
    unittest.main()
