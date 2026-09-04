from __future__ import annotations

import csv
import math
import statistics
from pathlib import Path
from typing import Any


EVENT_COLUMNS = (
    "event_arm",
    "event_flight_onset",
    "event_land",
    "event_disarm",
)


def _number(value: Any) -> float | None:
    if value is None or value == "":
        return None
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return None
    return parsed if math.isfinite(parsed) else None


def _truthy_number(value: Any) -> bool:
    parsed = _number(value)
    return parsed is not None and parsed != 0.0


def normalize_window_id(window_id: str | int) -> str:
    text = str(window_id).strip().upper()
    if text.startswith("W"):
        text = text[1:]
    if not text.isdigit():
        raise ValueError(f"Invalid window_id: {window_id!r}")
    return f"W{int(text):03d}"


class CsvRepository:
    """Read window inputs and keep raw synchronized rows for reference plots only."""

    def __init__(self, window_features_csv: Path, timeseries_csv: Path) -> None:
        self.window_features_csv = Path(window_features_csv)
        self.timeseries_csv = Path(timeseries_csv)
        self.windows = self._read_csv(self.window_features_csv)
        self.timeseries = self._read_csv(self.timeseries_csv)
        self._window_index = {
            normalize_window_id(row["window_id"]): index
            for index, row in enumerate(self.windows)
        }
        self.events = self._collect_events()

    @staticmethod
    def _read_csv(path: Path) -> list[dict[str, str]]:
        if not path.exists():
            raise FileNotFoundError(f"CSV file not found: {path}")
        with path.open("r", encoding="utf-8-sig", newline="") as handle:
            return list(csv.DictReader(handle))

    def _collect_events(self) -> list[dict[str, Any]]:
        events: list[dict[str, Any]] = []
        for row in self.timeseries:
            for column in EVENT_COLUMNS:
                if _truthy_number(row.get(column)):
                    events.append(
                        {
                            "event": column.removeprefix("event_"),
                            "t_s": _number(row.get("t_from_fbg_start_s")),
                            "timestamp": row.get("timestamp_local_iso"),
                            "flight_phase": row.get("flight_phase"),
                        }
                    )
        return events

    def get_window(self, window_id: str | int) -> dict[str, str]:
        normalized = normalize_window_id(window_id)
        try:
            return self.windows[self._window_index[normalized]]
        except KeyError as exc:
            raise KeyError(f"Unknown window_id: {normalized}") from exc

    def list_window_ids(self) -> list[str]:
        return [normalize_window_id(row["window_id"]) for row in self.windows]

    def get_neighbors(
        self, window_id: str | int, radius: int = 1
    ) -> list[dict[str, str]]:
        normalized = normalize_window_id(window_id)
        if normalized not in self._window_index:
            raise KeyError(f"Unknown window_id: {normalized}")
        index = self._window_index[normalized]
        start = max(0, index - max(0, radius))
        stop = min(len(self.windows), index + max(0, radius) + 1)
        return self.windows[start:stop]

    def events_for_interval(self, start_s: float, end_s: float) -> list[dict[str, Any]]:
        return [
            event
            for event in self.events
            if event["t_s"] is not None and start_s <= event["t_s"] < end_s
        ]

    def timeseries_for_interval(
        self, start_s: float, end_s: float
    ) -> list[dict[str, str]]:
        rows: list[dict[str, str]] = []
        for row in self.timeseries:
            timestamp = _number(row.get("t_from_fbg_start_s"))
            if timestamp is not None and start_s <= timestamp < end_s:
                rows.append(row)
        return rows

    def phase_baseline(
        self, phase: str, metric: str, validity_threshold: float
    ) -> dict[str, float | int | None]:
        values: list[float] = []
        for row in self.windows:
            if row.get("flight_phase_majority") != phase:
                continue
            validity = _number(row.get("fbg_validity_ratio"))
            value = _number(row.get(metric))
            if validity is None or validity < validity_threshold or value is None:
                continue
            values.append(value)

        if not values:
            return {"count": 0, "median": None, "mad": None}
        median = statistics.median(values)
        mad = statistics.median(abs(value - median) for value in values)
        return {"count": len(values), "median": median, "mad": mad}


number = _number
