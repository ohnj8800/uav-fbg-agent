# Local flight data

Place the two files supplied by the lab in this directory using these names:

- `window_features.csv`
- `synchronized_timeseries.csv`

`window_features.csv` is the fixed interpretation input. Until a validated DT context exists,
the real-state fields in the supplied window and synchronized files are used as the development
context and every output is labeled `VERIFIED_REAL_STATE` / `DEVELOPMENT`.

The CSV files are intentionally ignored by Git. Only `analysis-service` mounts this directory;
`agent-service` must not receive a data volume.
