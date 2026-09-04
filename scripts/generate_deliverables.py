from __future__ import annotations

import argparse
import csv
import json
import math
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from xml.sax.saxutils import escape

try:
    from scripts.analyze_batch import CSV_FIELDS, csv_row, request_json
    from scripts.evaluate_consistency import ALLOWED_DECISIONS, validate_result
except ModuleNotFoundError:  # Direct execution: python scripts/generate_deliverables.py
    from analyze_batch import CSV_FIELDS, csv_row, request_json
    from evaluate_consistency import ALLOWED_DECISIONS, validate_result


EVAL_FIELDS = (
    "window_id",
    "constrained_decision",
    "abstain",
    "decision_allowed",
    "evidence_present",
    "trace_complete",
    "guardrail_compliant",
    "llm_execution_valid",
    "contract_valid",
    "evaluation_scope",
    "context_source",
    "result_stage",
    "violations",
)


def _number(value: Any) -> float | None:
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return None
    return parsed if math.isfinite(parsed) else None


def _read_csv(path: Path) -> list[dict[str, str]]:
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        return list(csv.DictReader(handle))


def _evaluation_row(result: dict[str, Any]) -> dict[str, object]:
    violations = validate_result(result)
    decision = result.get("constrained_decision")
    attempts = result.get("llm_attempts")
    successes = result.get("llm_successes")
    llm_execution_valid = (
        isinstance(attempts, int)
        and isinstance(successes, int)
        and 0 <= successes <= attempts
    )
    guardrail_compliant = not result.get("guardrail_applied") or (
        decision == "INSUFFICIENT_DATA"
        and result.get("abstain") is True
        and result.get("llm_invoked") is False
        and attempts == 0
    )
    return {
        "window_id": result.get("window_id"),
        "constrained_decision": decision,
        "abstain": result.get("abstain"),
        "decision_allowed": decision in ALLOWED_DECISIONS,
        "evidence_present": bool(result.get("evidence")),
        "trace_complete": bool(result.get("tool_trace"))
        and bool(result.get("reasoning_trace")),
        "guardrail_compliant": guardrail_compliant,
        "llm_execution_valid": llm_execution_valid,
        "contract_valid": not violations,
        "evaluation_scope": "CONTRACT_SAFETY_TRACE_NOT_ACCURACY",
        "context_source": result.get("context_source"),
        "result_stage": result.get("result_stage"),
        "violations": " | ".join(violations),
    }


def _points(
    values: list[float | None],
    left: float,
    top: float,
    width: float,
    height: float,
    low: float,
    high: float,
) -> str:
    last = max(len(values) - 1, 1)
    output = []
    for index, value in enumerate(values):
        if value is None:
            continue
        x = left + width * index / last
        y = top + height - height * (value - low) / (high - low)
        output.append(f"{x:.1f},{y:.1f}")
    return " ".join(output)


def _range_label(values: list[float | None]) -> str:
    finite = [value for value in values if value is not None]
    if not finite:
        return "no data"
    return f"{min(finite):.4g} … {max(finite):.4g}"


def render_window_svg(
    result: dict[str, Any],
    feature_row: dict[str, str],
    timeseries: list[dict[str, str]],
    output_path: Path,
) -> None:
    start = _number(feature_row.get("t_start_s")) or 0.0
    end = _number(feature_row.get("t_end_s")) or start
    samples = [
        row
        for row in timeseries
        if (timestamp := _number(row.get("t_from_fbg_start_s"))) is not None
        and start <= timestamp < end
    ]
    fbg = [_number(row.get("fbg_delta_lambda_nm")) for row in samples]
    pitch = [_number(row.get("pitch_deg")) for row in samples]
    roll = [_number(row.get("roll_deg")) for row in samples]
    armed = [_number(row.get("armed")) for row in samples]
    airborne = [_number(row.get("is_airborne")) for row in samples]
    panels = [
        ("FBG Δλ (nm)", [(fbg, "#2dd4bf")]),
        ("Real attitude (deg)", [(pitch, "#60a5fa"), (roll, "#f59e0b")]),
        ("Flight state (0/1)", [(armed, "#a78bfa"), (airborne, "#fb7185")]),
    ]
    width, height = 1200, 760
    left, plot_width, panel_height = 125, 1020, 145
    decision = escape(str(result.get("constrained_decision", "UNKNOWN")))
    window_id = escape(str(result.get("window_id", "UNKNOWN")))
    source = escape(str(result.get("context_source", "UNKNOWN")))
    abstain = "YES" if result.get("abstain") else "NO"
    accent = "#fb7185" if result.get("abstain") else "#38bdf8"
    lines = [
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" viewBox="0 0 {width} {height}">',
        '<rect width="100%" height="100%" fill="#07111f"/>',
        '<style>text{font-family:Arial,Noto Sans TC,sans-serif;fill:#dce8f7}.muted{fill:#94a9c2}.grid{stroke:#223750;stroke-width:1}.axis{stroke:#49617e;stroke-width:1}</style>',
        f'<rect x="35" y="28" width="1130" height="86" rx="14" fill="#101e31" stroke="{accent}" stroke-width="2"/>',
        f'<text x="62" y="61" font-size="24" font-weight="700">{window_id} — {decision}</text>',
        f'<text x="62" y="91" font-size="14" class="muted">Context: {source} · Abstain: {abstain} · Window: {start:.3f}–{end:.3f} s</text>',
    ]
    top = 150
    for panel_index, (label, series) in enumerate(panels):
        y0 = top + panel_index * 185
        lines.append(f'<rect x="35" y="{y0-24}" width="1130" height="174" rx="12" fill="#0d1a2a" stroke="#223750"/>')
        lines.append(f'<text x="52" y="{y0+4}" font-size="14" font-weight="700">{escape(label)}</text>')
        for grid_index in range(5):
            gy = y0 + panel_height * grid_index / 4
            lines.append(f'<line x1="{left}" y1="{gy:.1f}" x2="{left+plot_width}" y2="{gy:.1f}" class="grid"/>')
        lines.append(f'<line x1="{left}" y1="{y0}" x2="{left}" y2="{y0+panel_height}" class="axis"/>')
        combined = [value for values, _ in series for value in values]
        lines.append(f'<text x="52" y="{y0+30}" font-size="11" class="muted">{escape(_range_label(combined))}</text>')
        finite = [value for value in combined if value is not None]
        low, high = (min(finite), max(finite)) if finite else (0.0, 1.0)
        if low == high:
            low -= 1.0
            high += 1.0
        for values, color in series:
            points = _points(values, left, y0, plot_width, panel_height, low, high)
            if points:
                lines.append(f'<polyline points="{points}" fill="none" stroke="{color}" stroke-width="2.2" stroke-linejoin="round"/>')
        lines.append(f'<text x="{left}" y="{y0+164}" font-size="11" class="muted">{start:.3f}s</text>')
        lines.append(f'<text x="{left+plot_width-45}" y="{y0+164}" font-size="11" class="muted">{end:.3f}s</text>')
    lines.extend(
        [
            '<rect x="55" y="700" width="14" height="4" fill="#2dd4bf"/><text x="77" y="706" font-size="12">FBG Δλ</text>',
            '<rect x="180" y="700" width="14" height="4" fill="#60a5fa"/><text x="202" y="706" font-size="12">Pitch</text>',
            '<rect x="285" y="700" width="14" height="4" fill="#f59e0b"/><text x="307" y="706" font-size="12">Roll</text>',
            '<rect x="380" y="700" width="14" height="4" fill="#a78bfa"/><text x="402" y="706" font-size="12">Armed</text>',
            '<rect x="485" y="700" width="14" height="4" fill="#fb7185"/><text x="507" y="706" font-size="12">Airborne</text>',
            '<text x="55" y="735" font-size="11" class="muted">Development result using verified real-state context; not a UAV fault diagnosis.</text>',
            '</svg>',
        ]
    )
    output_path.write_text("\n".join(lines), encoding="utf-8")


def generate_deliverables(
    base_url: str,
    output_dir: Path,
    window_features_csv: Path,
    timeseries_csv: Path,
    figure_windows: list[str],
    selected_windows: list[str] | None = None,
    limit: int | None = None,
    timeout_s: float = 90.0,
) -> dict[str, Any]:
    base_url = base_url.rstrip("/")
    listing = request_json(f"{base_url}/v1/windows", timeout_s=timeout_s)
    available = [str(item) for item in listing.get("window_ids", [])]
    window_ids = selected_windows or available
    if limit is not None:
        window_ids = window_ids[:limit]
    if not window_ids:
        raise RuntimeError("No windows selected")

    output_dir.mkdir(parents=True, exist_ok=True)
    figures_dir = output_dir / "window_figures"
    figures_dir.mkdir(parents=True, exist_ok=True)
    output_csv = output_dir / "llm_window_outputs.csv"
    trace_jsonl = output_dir / "agent_trace.jsonl"
    eval_csv = output_dir / "llm_eval.csv"

    started = time.monotonic()
    results: list[dict[str, Any]] = []
    with trace_jsonl.open("w", encoding="utf-8") as trace_handle:
        for index, window_id in enumerate(window_ids, start=1):
            result = request_json(
                f"{base_url}/v1/analyze",
                {"window_id": window_id},
                timeout_s=timeout_s,
            )
            results.append(result)
            trace_handle.write(json.dumps(result, ensure_ascii=False) + "\n")
            print(
                f"[{index}/{len(window_ids)}] {window_id}: "
                f"{result.get('constrained_decision')} abstain={result.get('abstain')}"
            )

    with output_csv.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=CSV_FIELDS)
        writer.writeheader()
        writer.writerows(csv_row(result) for result in results)

    eval_rows = [_evaluation_row(result) for result in results]
    with eval_csv.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=EVAL_FIELDS)
        writer.writeheader()
        writer.writerows(eval_rows)

    feature_rows = _read_csv(window_features_csv)
    features = {str(row.get("window_id", "")).upper(): row for row in feature_rows}
    timeseries = _read_csv(timeseries_csv)
    results_by_id = {str(result.get("window_id", "")).upper(): result for result in results}
    figure_paths: list[str] = []
    for window_id in figure_windows:
        normalized = window_id.upper()
        if normalized not in results_by_id:
            raise RuntimeError(f"Figure window {normalized} was not analyzed")
        if normalized not in features:
            raise RuntimeError(f"Figure window {normalized} is absent from window_features.csv")
        figure_path = figures_dir / f"{normalized}_verified_real_state.svg"
        render_window_svg(
            results_by_id[normalized], features[normalized], timeseries, figure_path
        )
        figure_paths.append(str(figure_path))

    return {
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "context_source": "VERIFIED_REAL_STATE",
        "result_stage": "DEVELOPMENT",
        "windows_completed": len(results),
        "contract_valid": sum(bool(row["contract_valid"]) for row in eval_rows),
        "elapsed_seconds": round(time.monotonic() - started, 3),
        "outputs": {
            "llm_window_outputs": str(output_csv),
            "agent_trace": str(trace_jsonl),
            "llm_eval": str(eval_csv),
            "window_figures": figure_paths,
        },
        "evaluation_note": (
            "llm_eval.csv evaluates contract, safety and trace completeness; "
            "it does not report classification accuracy without validated labels."
        ),
    }


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Generate the lab-requested restricted-LLM deliverables"
    )
    parser.add_argument("--url", default="http://127.0.0.1:8000")
    parser.add_argument("--output-dir", type=Path, default=Path("results/deliverables"))
    parser.add_argument(
        "--window-features", type=Path, default=Path("data/window_features.csv")
    )
    parser.add_argument(
        "--timeseries", type=Path, default=Path("data/synchronized_timeseries.csv")
    )
    parser.add_argument(
        "--figure-windows", nargs=3, default=["W003", "W004", "W027"]
    )
    parser.add_argument("--windows", nargs="+")
    parser.add_argument("--limit", type=int)
    parser.add_argument("--timeout", type=float, default=90.0)
    args = parser.parse_args()
    if args.limit is not None and args.limit <= 0:
        parser.error("--limit must be greater than zero")
    summary = generate_deliverables(
        args.url,
        args.output_dir,
        args.window_features,
        args.timeseries,
        args.figure_windows,
        args.windows,
        args.limit,
        args.timeout,
    )
    print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
