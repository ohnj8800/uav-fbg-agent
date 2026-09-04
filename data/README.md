# Local flight data

Place the two files supplied by the lab in this directory using these names:

- `window_features.csv`
- `synchronized_timeseries.csv`

`window_features.csv` is the fixed 2-s interpretation input, including precomputed FBG evidence,
validity and synchronized REAL_LOG flight statistics. Every current output is labeled
`REAL_LOG` / `DEVELOPMENT`.

`synchronized_timeseries.csv` is reference/debug data for waveform, event lookup and
representative-case plotting only. Do not recompute another feature set from it for the LLM.

`dt_context_dev.csv` is not an Agent input. Keep it only as development/schema reference until
full closed-loop validation and FBG alignment are complete.

The CSV files are intentionally ignored by Git. Only `analysis-service` mounts this directory;
`agent-service` must not receive a data volume.
