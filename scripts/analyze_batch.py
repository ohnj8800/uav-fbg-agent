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


CSV_FIELDS = (
    "window_id",
    "context_source",
    "context_validity",
    "fbg_validity_ratio",
    "decision",
    "abstained",
    "evidence_fbg",
    "evidence_context",
    "reason_code",
    "model_name",
    "prompt_version",
    "constrained_decision",
    "abstain",
    "abstain_reason",
    "result_stage",
    "fbg_std_nm",
    "fbg_rms_nm",
    "fbg_p2p_nm",
    "flight_phase",
    "armed_fraction",
    "airborne_fraction",
    "roll_mean_deg",
    "pitch_mean_deg",
    "flight_events",
    "guardrail_applied",
    "planner_invoked",
    "llm_invoked",
    "planner_backend",
    "planner_model",
    "effective_backend",
    "llm_attempts",
    "llm_successes",
    "llm_succeeded",
    "fallback_used",
    "tools_called",
    "evidence",
    "request_id",
    "timestamp_utc",
)


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


def csv_row(result: dict[str, Any]) -> dict[str, object]:
    structured = result.get("evidence_data", {})
    fbg = structured.get("fbg") or {}
    flight = structured.get("flight_context") or structured.get("real_flight_context") or {}
    flight_events = []
    for event in flight.get("events_in_window", []):
        event_name = event.get("event", "unknown")
        event_time = event.get("t_s")
        if isinstance(event_time, (int, float)):
            flight_events.append(f"{event_name}@{event_time:.3f}s")
        else:
            flight_events.append(str(event_name))
    return {
        "window_id": result.get("window_id"),
        "context_source": result.get("context_source"),
        "context_validity": result.get("context_validity"),
        "fbg_validity_ratio": fbg.get("validity_ratio"),
        "decision": result.get("decision"),
        "abstained": result.get("abstained", result.get("abstain")),
        "evidence_fbg": " | ".join(result.get("evidence_fbg", [])),
        "evidence_context": " | ".join(result.get("evidence_context", [])),
        "reason_code": result.get("reason_code"),
        "model_name": result.get("model_name", result.get("planner_model")),
        "prompt_version": result.get("prompt_version"),
        "constrained_decision": result.get("constrained_decision", result.get("decision")),
        "abstain": result.get("abstain"),
        "abstain_reason": result.get("abstain_reason"),
        "result_stage": result.get("result_stage"),
        "fbg_std_nm": fbg.get("std_nm"),
        "fbg_rms_nm": fbg.get("rms_nm"),
        "fbg_p2p_nm": fbg.get("p2p_nm"),
        "flight_phase": flight.get("flight_phase"),
        "armed_fraction": flight.get("armed_fraction"),
        "airborne_fraction": flight.get("airborne_fraction"),
        "roll_mean_deg": flight.get("roll_mean_deg"),
        "pitch_mean_deg": flight.get("pitch_mean_deg"),
        "flight_events": " | ".join(flight_events),
        "guardrail_applied": result.get("guardrail_applied"),
        "planner_invoked": result.get("planner_invoked"),
        "llm_invoked": result.get("llm_invoked"),
        "planner_backend": result.get("planner_backend"),
        "planner_model": result.get("planner_model"),
        "effective_backend": result.get("effective_backend"),
        "llm_attempts": result.get("llm_attempts"),
        "llm_successes": result.get("llm_successes"),
        "llm_succeeded": result.get("llm_succeeded"),
        "fallback_used": result.get("fallback_used"),
        "tools_called": " | ".join(result.get("tools_called", [])),
        "evidence": " | ".join(result.get("evidence", [])),
        "request_id": result.get("request_id"),
        "timestamp_utc": result.get("timestamp_utc"),
    }


def run_batch(
    base_url: str,
    output_dir: Path,
    limit: int | None = None,
    timeout_s: float = 90.0,
) -> dict[str, Any]:
    base_url = base_url.rstrip("/")
    listing = request_json(f"{base_url}/v1/windows", timeout_s=timeout_s)
    window_ids = [str(item) for item in listing.get("window_ids", [])]
    if limit is not None:
        window_ids = window_ids[:limit]
    if not window_ids:
        raise RuntimeError("The service returned no window IDs")

    output_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    jsonl_path = output_dir / f"batch_{stamp}.jsonl"
    csv_path = output_dir / f"batch_{stamp}.csv"
    summary_path = output_dir / f"batch_{stamp}_summary.json"

    started = time.monotonic()
    results: list[dict[str, Any]] = []
    errors: list[dict[str, str]] = []
    with jsonl_path.open("w", encoding="utf-8") as jsonl_handle:
        for index, window_id in enumerate(window_ids, start=1):
            try:
                result = request_json(
                    f"{base_url}/v1/analyze",
                    {"window_id": window_id},
                    timeout_s=timeout_s,
                )
                results.append(result)
                jsonl_handle.write(json.dumps(result, ensure_ascii=False) + "\n")
                print(f"[{index}/{len(window_ids)}] {window_id}: {result.get('decision')}")
            except RuntimeError as exc:
                error = {"window_id": window_id, "error": str(exc)}
                errors.append(error)
                jsonl_handle.write(json.dumps(error, ensure_ascii=False) + "\n")
                print(f"[{index}/{len(window_ids)}] {window_id}: ERROR")

    with csv_path.open("w", encoding="utf-8-sig", newline="") as csv_handle:
        writer = csv.DictWriter(csv_handle, fieldnames=CSV_FIELDS)
        writer.writeheader()
        writer.writerows(csv_row(result) for result in results)

    decisions = Counter(str(result.get("decision", "UNKNOWN")) for result in results)
    summary: dict[str, Any] = {
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "base_url": base_url,
        "requested": len(window_ids),
        "completed": len(results),
        "failed": len(errors),
        "decision_counts": dict(sorted(decisions.items())),
        "planner_backends": dict(
            sorted(Counter(str(r.get("planner_backend", "unknown")) for r in results).items())
        ),
        "planner_models": dict(
            sorted(Counter(str(r.get("planner_model") or "none") for r in results).items())
        ),
        "effective_backends": dict(
            sorted(Counter(str(r.get("effective_backend", "unknown")) for r in results).items())
        ),
        "fallback_results": sum(bool(r.get("fallback_used")) for r in results),
        "fully_successful_llm_results": sum(
            r.get("llm_succeeded") is True for r in results
        ),
        "llm_invocations": sum(bool(r.get("llm_invoked")) for r in results),
        "guardrail_blocks": sum(bool(r.get("guardrail_applied")) for r in results),
        "elapsed_seconds": round(time.monotonic() - started, 3),
        "outputs": {
            "jsonl": str(jsonl_path),
            "csv": str(csv_path),
            "summary": str(summary_path),
        },
        "errors": errors,
    }
    summary_path.write_text(
        json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return summary


def main() -> None:
    parser = argparse.ArgumentParser(description="Analyze UAV/FBG windows in sequence")
    parser.add_argument("--url", default="http://127.0.0.1:8000")
    parser.add_argument("--output-dir", type=Path, default=Path("results"))
    parser.add_argument("--limit", type=int)
    parser.add_argument("--timeout", type=float, default=90.0)
    args = parser.parse_args()
    if args.limit is not None and args.limit <= 0:
        parser.error("--limit must be greater than zero")

    summary = run_batch(args.url, args.output_dir, args.limit, args.timeout)
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    if summary["failed"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
