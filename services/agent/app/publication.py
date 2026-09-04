from __future__ import annotations

import csv
import json
from collections import Counter
from pathlib import Path
from typing import Any


REPRESENTATIVE_WINDOWS = ("W003", "W004", "W027")


class PublicationResultsError(RuntimeError):
    pass


def _csv_bool(value: Any) -> bool:
    return str(value).strip().lower() == "true"


def load_publication_results(directory: Path) -> dict[str, Any]:
    directory = Path(directory)
    outputs_path = directory / "llm_window_outputs.csv"
    trace_path = directory / "agent_trace.jsonl"
    eval_path = directory / "llm_eval.csv"
    missing = [
        path.name
        for path in (outputs_path, trace_path, eval_path)
        if not path.is_file()
    ]
    if missing:
        raise PublicationResultsError(
            "Generate deliverables before opening publication view; missing: "
            + ", ".join(missing)
        )

    with outputs_path.open("r", encoding="utf-8-sig", newline="") as handle:
        rows = list(csv.DictReader(handle))
    with eval_path.open("r", encoding="utf-8-sig", newline="") as handle:
        eval_rows = list(csv.DictReader(handle))
    traces: dict[str, dict[str, Any]] = {}
    with trace_path.open("r", encoding="utf-8") as handle:
        for line_number, line in enumerate(handle, start=1):
            if not line.strip():
                continue
            try:
                item = json.loads(line)
            except json.JSONDecodeError as exc:
                raise PublicationResultsError(
                    f"Invalid agent_trace.jsonl line {line_number}: {exc}"
                ) from exc
            window_id = str(item.get("window_id", "")).upper()
            if window_id:
                traces[window_id] = item

    selected: list[dict[str, Any]] = []
    for window_id in REPRESENTATIVE_WINDOWS:
        try:
            selected.append(traces[window_id])
        except KeyError as exc:
            raise PublicationResultsError(
                f"agent_trace.jsonl does not contain required {window_id}"
            ) from exc

    decisions = Counter(str(row.get("constrained_decision")) for row in rows)
    llm_invocations = sum(_csv_bool(row.get("llm_invoked")) for row in rows)
    abstentions = sum(_csv_bool(row.get("abstain")) for row in rows)
    fallbacks = sum(_csv_bool(row.get("fallback_used")) for row in rows)
    valid_contracts = sum(
        str(row.get("contract_valid", "")).lower() == "true" for row in eval_rows
    )
    context_sources = {
        str(row.get("context_source", "")).strip() for row in rows if row.get("context_source")
    }
    result_stages = {
        str(row.get("result_stage", "")).strip() for row in rows if row.get("result_stage")
    }
    planner_models = {
        str(item.get("planner_model", "")).strip()
        for item in traces.values()
        if item.get("planner_model")
    }
    return {
        "context_source": next(iter(context_sources), "UNKNOWN")
        if len(context_sources) <= 1
        else "MIXED",
        "result_stage": next(iter(result_stages), "UNKNOWN")
        if len(result_stages) <= 1
        else "MIXED",
        "planner_model": next(iter(planner_models), None)
        if len(planner_models) <= 1
        else "MIXED",
        "representative_windows": selected,
        "window_outputs": [
            {
                "window_id": str(row.get("window_id", "")).upper(),
                "constrained_decision": row.get("constrained_decision"),
                "abstain": _csv_bool(row.get("abstain")),
                "llm_invoked": _csv_bool(row.get("llm_invoked")),
                "fallback_used": _csv_bool(row.get("fallback_used")),
            }
            for row in rows
        ],
        "summary": {
            "windows": len(rows),
            "llm_invocations": llm_invocations,
            "abstentions": abstentions,
            "fallbacks": fallbacks,
            "contract_valid": valid_contracts,
            "decision_counts": dict(sorted(decisions.items())),
            "evaluation_scope": "CONTRACT_SAFETY_TRACE_NOT_ACCURACY",
        },
    }
