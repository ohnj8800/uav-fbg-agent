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

    def test_w008_context_uses_window_level_state_statistics(self) -> None:
        context = self.engine.context("W008")
        self.assertEqual(context["context_source"], "REAL_LOG")
        self.assertEqual(context["context_validity"], "VALID")
        self.assertAlmostEqual(context["airborne_fraction"], 0.5)
        self.assertNotIn("events_in_window", context)

    def test_raw_flight_events_are_reference_only(self) -> None:
        plot = self.engine.visualization("W008")
        self.assertEqual(plot["context_source"], "REAL_LOG_REFERENCE_ONLY")
        self.assertTrue(any(event["event"] == "flight_onset" for event in plot["events"]))


if __name__ == "__main__":
    unittest.main()
