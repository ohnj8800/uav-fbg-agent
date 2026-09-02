from __future__ import annotations

import argparse
import csv
import json
import time
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


ALLOWED_DECISIONS = {
    "STATE_CONSISTENT",
    "TRANSITION_ASSOCIATED",
    "NOT_ATTRIBUTABLE_TO_FLIGHT_STATE",
    "INSUFFICIENT_DATA",
}


def request_json(
    url: str, payload: dict[str, object] | None = None, timeout_s: float = 90.0
) -> dict[str, Any]:
    data = None if payload is None else json.dumps(payload).encode("utf-8")
    request = Request(
        url,
        data=data,
        headers={"Content-Type": "application/json"} if data else {},
        method="POST" if data else "GET",
    )
    try:
        with urlopen(request, timeout=timeout_s) as response:
            return json.loads(response.read().decode("utf-8"))
    except HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"HTTP {exc.code} from {url}: {detail}") from exc
    except (URLError, TimeoutError) as exc:
        raise RuntimeError(f"Cannot reach {url}: {exc}") from exc


def validate_result(result: dict[str, Any]) -> list[str]:
    violations: list[str] = []
    decision = result.get("decision")
    if decision not in ALLOWED_DECISIONS:
        violations.append(f"decision is not allowed: {decision!r}")

    planner_backend = result.get("planner_backend")
    if planner_backend not in {"heuristic", "ollama"}:
        violations.append(f"planner_backend is not recognized: {planner_backend!r}")
    if planner_backend == "ollama" and not result.get("planner_model"):
        violations.append("Ollama interpretation must record planner_model")

    evidence = result.get("evidence")
    if not isinstance(evidence, list) or not all(
        isinstance(item, str) for item in evidence
    ):
        violations.append("evidence must be a list of strings")

    structured = result.get("evidence_data")
    if not isinstance(structured, dict):
        violations.append("evidence_data must be an object")
        structured = {}
    fbg = structured.get("fbg")
    if not isinstance(fbg, dict):
        violations.append("evidence_data.fbg must be an object")
    else:
        for field in ("validity_ratio", "validity_threshold", "std_nm", "rms_nm", "p2p_nm"):
            if field not in fbg:
                violations.append(f"evidence_data.fbg.{field} is missing")

    if result.get("guardrail_applied"):
        if decision != "INSUFFICIENT_DATA":
            violations.append("guardrail decision must be INSUFFICIENT_DATA")
        if result.get("llm_invoked"):
            violations.append("LLM must not run after the quality guardrail blocks a window")
        if result.get("tools_called") != ["check_quality"]:
            violations.append("a blocked window may only call check_quality")
    elif not isinstance(structured.get("real_flight_context"), dict):
        violations.append("valid interpretation requires real_flight_context")

    return violations


def summarize_window(
    window_id: str,
    repeats: int,
    results: list[dict[str, Any]],
    errors: list[str],
) -> dict[str, Any]:
    decisions = Counter(str(result.get("decision")) for result in results)
    violations = [
        f"run {index}: {violation}"
        for index, result in enumerate(results, start=1)
        for violation in validate_result(result)
    ]
    successful = len(results)
    agreement_rate = max(decisions.values(), default=0) / successful if successful else 0.0
    return {
        "window_id": window_id,
        "requested_repeats": repeats,
        "successful_repeats": successful,
        "decision_counts": dict(sorted(decisions.items())),
        "agreement_rate": round(agreement_rate, 4),
        "consistent": successful == repeats and len(decisions) == 1,
        "contract_valid": not violations and not errors,
        "violations": violations,
        "errors": errors,
        "tool_sequences": [result.get("tools_called", []) for result in results],
        "planner_backends": sorted(
            {str(result.get("planner_backend")) for result in results}
        ),
        "planner_models": sorted(
            {str(result.get("planner_model")) for result in results}
        ),
    }


def run_evaluation(
    base_url: str,
    output_dir: Path,
    repeats: int = 3,
    window_ids: list[str] | None = None,
    limit: int | None = None,
    timeout_s: float = 90.0,
) -> dict[str, Any]:
    base_url = base_url.rstrip("/")
    if window_ids is None:
        listing = request_json(f"{base_url}/v1/windows", timeout_s=timeout_s)
        window_ids = [str(item) for item in listing.get("window_ids", [])]
    if limit is not None:
        window_ids = window_ids[:limit]
    if not window_ids:
        raise RuntimeError("No window IDs were selected")

    output_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    raw_path = output_dir / f"consistency_{stamp}.jsonl"
    csv_path = output_dir / f"consistency_{stamp}.csv"
    summary_path = output_dir / f"consistency_{stamp}_summary.json"
    started = time.monotonic()
    window_summaries: list[dict[str, Any]] = []

    with raw_path.open("w", encoding="utf-8") as raw_handle:
        for window_index, window_id in enumerate(window_ids, start=1):
            results: list[dict[str, Any]] = []
            errors: list[str] = []
            for repeat_index in range(1, repeats + 1):
                try:
                    result = request_json(
                        f"{base_url}/v1/analyze",
                        {"window_id": window_id},
                        timeout_s=timeout_s,
                    )
                    results.append(result)
                    raw_handle.write(
                        json.dumps(
                            {"repeat": repeat_index, **result}, ensure_ascii=False
                        )
                        + "\n"
                    )
                    print(
                        f"[{window_index}/{len(window_ids)}] {window_id} "
                        f"run {repeat_index}/{repeats}: {result.get('decision')}"
                    )
                except RuntimeError as exc:
                    errors.append(f"run {repeat_index}: {exc}")
                    print(
                        f"[{window_index}/{len(window_ids)}] {window_id} "
                        f"run {repeat_index}/{repeats}: ERROR"
                    )
            window_summaries.append(
                summarize_window(window_id, repeats, results, errors)
            )

    with csv_path.open("w", encoding="utf-8-sig", newline="") as csv_handle:
        fieldnames = (
            "window_id",
            "requested_repeats",
            "successful_repeats",
            "decision_counts",
            "agreement_rate",
            "consistent",
            "contract_valid",
            "violations",
            "errors",
            "tool_sequences",
            "planner_backends",
            "planner_models",
        )
        writer = csv.DictWriter(csv_handle, fieldnames=fieldnames)
        writer.writeheader()
        for item in window_summaries:
            writer.writerow(
                {
                    **item,
                    "decision_counts": json.dumps(item["decision_counts"]),
                    "violations": " | ".join(item["violations"]),
                    "errors": " | ".join(item["errors"]),
                    "tool_sequences": json.dumps(item["tool_sequences"]),
                    "planner_backends": json.dumps(item["planner_backends"]),
                    "planner_models": json.dumps(item["planner_models"]),
                }
            )

    agreement_values = [item["agreement_rate"] for item in window_summaries]
    summary = {
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "base_url": base_url,
        "windows_evaluated": len(window_summaries),
        "repeats_per_window": repeats,
        "total_requests": len(window_summaries) * repeats,
        "consistent_windows": sum(item["consistent"] for item in window_summaries),
        "contract_valid_windows": sum(
            item["contract_valid"] for item in window_summaries
        ),
        "mean_agreement_rate": round(
            sum(agreement_values) / len(agreement_values), 4
        ),
        "elapsed_seconds": round(time.monotonic() - started, 3),
        "outputs": {
            "raw_jsonl": str(raw_path),
            "csv": str(csv_path),
            "summary": str(summary_path),
        },
        "windows": window_summaries,
    }
    summary_path.write_text(
        json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return summary


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Repeat UAV/FBG interpretations and validate the output contract"
    )
    parser.add_argument("--url", default="http://127.0.0.1:8000")
    parser.add_argument("--output-dir", type=Path, default=Path("results"))
    parser.add_argument("--windows", nargs="+")
    parser.add_argument("--limit", type=int)
    parser.add_argument("--repeats", type=int, default=3)
    parser.add_argument("--timeout", type=float, default=90.0)
    args = parser.parse_args()
    if args.repeats <= 0:
        parser.error("--repeats must be greater than zero")
    if args.limit is not None and args.limit <= 0:
        parser.error("--limit must be greater than zero")

    summary = run_evaluation(
        args.url,
        args.output_dir,
        args.repeats,
        args.windows,
        args.limit,
        args.timeout,
    )
    print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
