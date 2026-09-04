from __future__ import annotations

import csv
import json
import tempfile
import unittest
from pathlib import Path

from services.agent.app.publication import (
    PublicationResultsError,
    load_publication_results,
)


class PublicationResultsTest(unittest.TestCase):
    def test_loads_real_deliverable_contract(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            output_fields = (
                "window_id",
                "constrained_decision",
                "abstain",
                "llm_invoked",
                "fallback_used",
                "context_source",
                "result_stage",
            )
            output_rows = [
                {
                    "window_id": "W003",
                    "constrained_decision": "NOT_ATTRIBUTABLE_TO_FLIGHT_STATE",
                    "abstain": False,
                    "llm_invoked": True,
                    "fallback_used": False,
                    "context_source": "VERIFIED_REAL_STATE",
                    "result_stage": "DEVELOPMENT",
                },
                {
                    "window_id": "W004",
                    "constrained_decision": "TRANSITION_ASSOCIATED",
                    "abstain": False,
                    "llm_invoked": True,
                    "fallback_used": False,
                    "context_source": "VERIFIED_REAL_STATE",
                    "result_stage": "DEVELOPMENT",
                },
                {
                    "window_id": "W027",
                    "constrained_decision": "INSUFFICIENT_DATA",
                    "abstain": True,
                    "llm_invoked": False,
                    "fallback_used": False,
                    "context_source": "VERIFIED_REAL_STATE",
                    "result_stage": "DEVELOPMENT",
                },
            ]
            with (root / "llm_window_outputs.csv").open(
                "w", encoding="utf-8", newline=""
            ) as handle:
                writer = csv.DictWriter(handle, fieldnames=output_fields)
                writer.writeheader()
                writer.writerows(output_rows)
            with (root / "llm_eval.csv").open(
                "w", encoding="utf-8", newline=""
            ) as handle:
                writer = csv.DictWriter(handle, fieldnames=("window_id", "contract_valid"))
                writer.writeheader()
                writer.writerows(
                    {"window_id": row["window_id"], "contract_valid": True}
                    for row in output_rows
                )
            with (root / "agent_trace.jsonl").open("w", encoding="utf-8") as handle:
                for row in output_rows:
                    trace = {
                        **row,
                        "planner_model": "qwen3:8b",
                        "evidence": ["test evidence"],
                        "evidence_data": {"fbg": {"validity_ratio": 1.0}},
                        "tool_trace": [{"executed_tool": "check_quality"}],
                    }
                    handle.write(json.dumps(trace) + "\n")

            payload = load_publication_results(root)

            self.assertEqual(payload["context_source"], "VERIFIED_REAL_STATE")
            self.assertEqual(payload["result_stage"], "DEVELOPMENT")
            self.assertEqual(payload["planner_model"], "qwen3:8b")
            self.assertEqual(payload["summary"]["windows"], 3)
            self.assertEqual(payload["summary"]["llm_invocations"], 2)
            self.assertEqual(payload["summary"]["abstentions"], 1)
            self.assertEqual(payload["summary"]["contract_valid"], 3)
            self.assertEqual(
                [item["window_id"] for item in payload["representative_windows"]],
                ["W003", "W004", "W027"],
            )

    def test_missing_deliverables_are_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaises(PublicationResultsError):
                load_publication_results(Path(directory))


if __name__ == "__main__":
    unittest.main()
