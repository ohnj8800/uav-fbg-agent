from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class Settings:
    window_features_csv: Path
    synchronized_timeseries_csv: Path
    fbg_validity_threshold: float = 0.80
    host: str = "0.0.0.0"
    port: int = 8001

    @classmethod
    def from_env(cls) -> "Settings":
        return cls(
            window_features_csv=Path(
                os.getenv("WINDOW_FEATURES_CSV", "data/window_features.csv")
            ),
            synchronized_timeseries_csv=Path(
                os.getenv(
                    "SYNCHRONIZED_TIMESERIES_CSV",
                    "data/synchronized_timeseries.csv",
                )
            ),
            fbg_validity_threshold=float(
                os.getenv("FBG_VALIDITY_THRESHOLD", "0.80")
            ),
            host=os.getenv("ANALYSIS_HOST", "0.0.0.0"),
            port=int(os.getenv("ANALYSIS_PORT", "8001")),
        )

