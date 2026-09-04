from __future__ import annotations

import argparse
import csv
import math
import os
from pathlib import Path
from typing import Any

os.environ.setdefault("MPLCONFIGDIR", str(Path("runtime/matplotlib").resolve()))

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.patches import FancyBboxPatch


COLORS = {
    "STATE_CONSISTENT": "#167c80",
    "TRANSITION_ASSOCIATED": "#315f9d",
    "NOT_ATTRIBUTABLE": "#596575",
    "INSUFFICIENT_DATA": "#c45a42",
}


def _read_csv(path: Path) -> list[dict[str, str]]:
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        return list(csv.DictReader(handle))


def _number(value: Any) -> float | None:
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return None
    return parsed if math.isfinite(parsed) else None


def _truthy(value: Any) -> bool:
    return str(value).strip().lower() == "true"


def _normalized_window_id(value: str) -> str:
    text = value.strip().upper().removeprefix("W")
    return f"W{int(text):03d}"


def _jitter(count: int, width: float = 0.20) -> list[float]:
    if count <= 1:
        return [0.0] * count
    return [(-width + 2 * width * i / (count - 1)) for i in range(count)]


def _window_samples(
    timeseries: list[dict[str, str]], start: float, end: float
) -> tuple[list[float], list[float | None]]:
    times: list[float] = []
    values: list[float | None] = []
    for row in timeseries:
        timestamp = _number(row.get("t_from_fbg_start_s"))
        if timestamp is None or not start <= timestamp < end:
            continue
        times.append(timestamp)
        valid = (_number(row.get("fbg_valid")) or 0.0) != 0.0
        values.append(_number(row.get("fbg_delta_lambda_nm")) if valid else None)
    return times, values


def _plot_card(
    ax: Any,
    position: int,
    output: dict[str, str],
    feature: dict[str, str],
    timeseries: list[dict[str, str]],
) -> None:
    decision = output.get("decision") or output.get("constrained_decision") or "UNKNOWN"
    color = COLORS.get(decision, "#596575")
    window_id = _normalized_window_id(output["window_id"])
    start = float(feature["t_start_s"])
    end = float(feature["t_end_s"])
    midpoint = (start + end) / 2
    validity = float(feature["fbg_validity_ratio"])
    context_validity = output.get("context_validity", "UNKNOWN")
    abstained = _truthy(output.get("abstained", output.get("abstain")))

    ax.set_axis_off()
    ax.add_patch(
        FancyBboxPatch(
            (0.01, 0.01), 0.98, 0.98,
            boxstyle="round,pad=0.018,rounding_size=0.035",
            linewidth=1.35, edgecolor="#9aa5b1", facecolor="#ffffff",
            transform=ax.transAxes, clip_on=False,
        )
    )
    headings = {
        "STATE_CONSISTENT": "Steady state",
        "TRANSITION_ASSOCIATED": "Context transition",
        "NOT_ATTRIBUTABLE": "Not attributable",
        "INSUFFICIENT_DATA": "Insufficient FBG quality",
    }
    ax.text(0.5, 0.92, f"{position}. {headings.get(decision, decision)}", ha="center", va="top", color=color, fontsize=10.5, fontweight="bold", transform=ax.transAxes)
    ax.text(0.5, 0.82, f"{window_id} · t = {midpoint:.0f} s", ha="center", va="top", fontsize=8.5, color="#374151", transform=ax.transAxes)

    spark = ax.inset_axes([0.12, 0.54, 0.76, 0.19])
    times, values = _window_samples(timeseries, start, end)
    if times:
        spark.plot(times, values, color=color if not abstained else "#6b7280", linewidth=1.45)
    spark.set_xlim(start, end)
    spark.set_xticks([])
    spark.set_yticks([])
    for spine in spark.spines.values():
        spine.set_visible(False)

    fbg_mark = "✓" if validity >= 0.80 else "×"
    fbg_color = "#167c80" if validity >= 0.80 else "#c2413b"
    ax.text(0.09, 0.43, fbg_mark, fontsize=11, fontweight="bold", color=fbg_color, transform=ax.transAxes)
    ax.text(0.17, 0.43, f"FBG validity: {validity:.2f}", fontsize=8.1, color="#27323d", transform=ax.transAxes)
    context_mark = "✓" if context_validity in {"VALID", "PARTIAL"} else "×"
    context_color = "#167c80" if context_mark == "✓" else "#c2413b"
    ax.text(0.09, 0.34, context_mark, fontsize=11, fontweight="bold", color=context_color, transform=ax.transAxes)
    ax.text(0.17, 0.34, f"REAL_LOG context: {context_validity.lower()}", fontsize=8.1, color="#27323d", transform=ax.transAxes)
    ax.annotate("", xy=(0.5, 0.23), xytext=(0.5, 0.29), xycoords="axes fraction", arrowprops={"arrowstyle": "->", "color": "#5f6b76", "lw": 1.0})
    ax.text(0.5, 0.15, decision, ha="center", va="center", color=color, fontsize=8.0, fontweight="bold", transform=ax.transAxes, bbox={"boxstyle": "round,pad=0.42", "fc": "#f8fafc", "ec": color, "lw": 1.0})
    if abstained:
        ax.text(0.5, 0.055, "LLM bypassed", ha="center", fontsize=8, color="#374151", transform=ax.transAxes)


def render_publication_figure(
    output_csv: Path,
    window_features_csv: Path,
    timeseries_csv: Path,
    representative_windows: list[str] | tuple[str, str, str],
    png_path: Path,
    svg_path: Path,
) -> None:
    outputs = {_normalized_window_id(row["window_id"]): row for row in _read_csv(output_csv)}
    features = {_normalized_window_id(row["window_id"]): row for row in _read_csv(window_features_csv)}
    timeseries = _read_csv(timeseries_csv)
    selected = [_normalized_window_id(item) for item in representative_windows]
    missing = [item for item in selected if item not in outputs or item not in features]
    if missing:
        raise ValueError("Representative windows are missing: " + ", ".join(missing))

    fig = plt.figure(figsize=(15.2, 5.4), constrained_layout=False)
    grid = fig.add_gridspec(1, 2, width_ratios=[1.02, 1.58], left=0.045, right=0.985, top=0.84, bottom=0.14, wspace=0.08)
    ax = fig.add_subplot(grid[0, 0])
    phases = [("preflight", "Preflight", "#70b7b7"), ("airborne", "Airborne", "#218f92"), ("landing", "Landing", "#93c9ca")]
    phase_values = {
        phase: sorted(
            [
            float(row["fbg_delta_rms_nm"])
            for row in features.values()
            if row.get("flight_phase_majority") == phase
            and _number(row.get("fbg_delta_rms_nm")) is not None
            and (_number(row.get("fbg_validity_ratio")) or 0.0) >= 0.80
            ]
        )
        for phase, _, _ in phases
    }
    global_maximum = max(
        (max(values) for values in phase_values.values() if values), default=0.01
    )
    for index, (phase, label, color) in enumerate(phases, start=1):
        values = phase_values[phase]
        if not values:
            continue
        xs = [index + offset for offset in _jitter(len(values))]
        ax.scatter(xs, values, s=30, color=color, edgecolor="#146b6f", linewidth=0.55, alpha=0.80, zorder=2)
        median = sorted(values)[len(values) // 2] if len(values) % 2 else sum(sorted(values)[len(values)//2-1:len(values)//2+1]) / 2
        ax.hlines(median, index - 0.27, index + 0.27, color="#111827", linewidth=3, zorder=3)
        ax.text(index, max(values) + global_maximum * 0.045, f"median {median:.3f} nm\nn={len(values)}", ha="center", va="bottom", fontsize=8.5, color="#26323d")
    ax.set_xticks([1, 2, 3], [item[1] for item in phases])
    ax.set_ylabel("Window-level FBG RMS (nm)")
    ax.set_title("State-conditioned FBG variability", fontsize=13, fontweight="bold", pad=12)
    ax.grid(axis="y", color="#d7dde3", linewidth=0.8, alpha=0.8)
    ax.spines[["top", "right"]].set_visible(False)
    ax.set_ylim(0, global_maximum * 1.18)
    ax.text(0.0, -0.18, "One point = one valid 2-s window; black bar = median", transform=ax.transAxes, fontsize=8.5, color="#596575")

    cards = grid[0, 1].subgridspec(1, 3, wspace=0.16)
    for position, window_id in enumerate(selected, start=1):
        card = fig.add_subplot(cards[0, position - 1])
        _plot_card(card, position, outputs[window_id], features[window_id], timeseries)
    fig.text(0.73, 0.89, "Constrained contextual interpretation", ha="center", fontsize=13, fontweight="bold")
    fig.text(0.012, 0.965, "c", fontsize=24, fontweight="bold", va="top")
    fig.text(0.5, 0.035, "REAL_LOG context from window_features.csv; synchronized timeseries is used only for waveform display.", ha="center", fontsize=8.5, color="#596575")
    png_path.parent.mkdir(parents=True, exist_ok=True)
    svg_path.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(png_path, dpi=300, facecolor="white")
    fig.savefig(svg_path, facecolor="white")
    plt.close(fig)


def main() -> None:
    parser = argparse.ArgumentParser(description="Generate the Python/Matplotlib paper Figure c")
    parser.add_argument("--outputs", type=Path, default=Path("results/deliverables/llm_window_outputs.csv"))
    parser.add_argument("--window-features", type=Path, default=Path("data/window_features.csv"))
    parser.add_argument("--timeseries", type=Path, default=Path("data/synchronized_timeseries.csv"))
    parser.add_argument("--windows", nargs=3, default=["W031", "W008", "W065"])
    parser.add_argument("--png", type=Path, default=Path("results/deliverables/figure_c_contextual_interpretation.png"))
    parser.add_argument("--svg", type=Path, default=Path("results/deliverables/figure_c_contextual_interpretation.svg"))
    args = parser.parse_args()
    render_publication_figure(args.outputs, args.window_features, args.timeseries, args.windows, args.png, args.svg)
    print(f"Created {args.png}")
    print(f"Created {args.svg}")


if __name__ == "__main__":
    main()
