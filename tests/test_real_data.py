from __future__ import annotations

import unittest
from pathlib import Path

from services.analysis.app.engine import AnalysisEngine
from services.analysis.app.repository import CsvRepository


class RealDataSmokeTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        base = Path(__file__).resolve().parents[1] / "data"
        windows = base / "window_features.csv"
        timeseries = base / "synchronized_timeseries.csv"
        if not windows.exists() or not timeseries.exists():
            raise unittest.SkipTest("Lab CSV files are not present")
        cls.engine = AnalysisEngine(CsvRepository(windows, timeseries), 0.80)

    def test_w027_is_blocked_by_real_quality_value(self) -> None:
        quality = self.engine.quality("W027")
        self.assertAlmostEqual(quality["fbg_validity_ratio"], 0.30)
        self.assertFalse(quality["sufficient"])

    def test_w008_contains_flight_onset(self) -> None:
        context = self.engine.context("W008")
        self.assertTrue(
            any(event["event"] == "flight_onset" for event in context["events_in_window"])
        )


if __name__ == "__main__":
    unittest.main()

