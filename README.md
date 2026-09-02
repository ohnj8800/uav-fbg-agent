# UAV/FBG Restricted LLM Agent

雙 Docker 的受限制 LLM Agent 飛行資料判讀系統。資料分析服務獨佔 CSV，
Agent 只能透過白名單工具取得 evidence，並輸出固定四類判讀結果。

## Current scope

- `analysis-service`: CSV parsing, window lookup, flight events, neighbor comparison,
  FBG quality guardrail and deterministic evidence.
- `agent-service`: restricted tool planning, optional local Ollama backend, structured output,
  decision validation and JSONL audit trail.
- Mandatory rule: when `fbg_validity_ratio < FBG_VALIDITY_THRESHOLD`, the final decision is
  always `INSUFFICIENT_DATA`. The LLM cannot override it.

Allowed decisions:

- `STATE_CONSISTENT`
- `TRANSITION_ASSOCIATED`
- `NOT_ATTRIBUTABLE_TO_FLIGHT_STATE`
- `INSUFFICIENT_DATA`

CUDA VMD and VMDNet are intentionally not part of this first vertical slice. They can later be
added as analysis-service tools without allowing the LLM to access raw CSV files.

## Repository layout

```text
services/analysis/    deterministic data and rule service
services/agent/       restricted agent and Ollama adapter
data/                 local CSV files; ignored by Git
runtime/              audit logs; ignored by Git
scripts/              command-line client
tests/                unit and real-data smoke tests
docker-compose.yml    two-container deployment
```

## 1. Windows development environment

Recommended host environment:

- Windows 11
- WSL2 Ubuntu or PowerShell
- Python 3.12
- Git
- Docker Desktop with WSL2 backend
- Ollama on the Windows host when enabling the real local model

Create and activate the virtual environment in PowerShell:

```powershell
py -3.12 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m unittest discover -s tests -v
```

In WSL2/Linux:

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m unittest discover -s tests -v
```

The current code uses only Python's standard library, so no `pip install` step is required.

## 2. Prepare the data

Place the lab files under `data/` and rename them exactly as follows:

```text
data/window_features.csv
data/synchronized_timeseries.csv
```

The files are excluded from Git and mounted read-only into `analysis-service`. They are never
mounted into `agent-service`.

## 3. Run locally without Docker

Terminal 1:

```bash
export WINDOW_FEATURES_CSV=data/window_features.csv
export SYNCHRONIZED_TIMESERIES_CSV=data/synchronized_timeseries.csv
python -m services.analysis.app.server
```

Terminal 2:

```bash
export ANALYSIS_BASE_URL=http://localhost:8001
export LLM_MODE=heuristic
python -m services.agent.app.server
```

Terminal 3:

```bash
python scripts/analyze_window.py W027
```

The `heuristic` backend is deterministic and intended for integration testing. It uses exactly the
same restricted tool contract as the Ollama planner.

## 4. Run the two Docker services

Copy the environment template and start the services:

```bash
cp .env.example .env
docker compose up --build
```

Only `agent-service` is published on port `8000`. `analysis-service` remains on the internal Docker
network.

Test W027:

```bash
python scripts/analyze_window.py W027
```

Expected guardrail result with the supplied data:

```json
{
  "window_id": "W027",
  "decision": "INSUFFICIENT_DATA",
  "guardrail_applied": true
}
```

## 5. Enable the local LLM

First confirm the deterministic pipeline works. Then run Ollama on the host, pull the configured
model, and change `.env`:

```dotenv
LLM_MODE=ollama
OLLAMA_BASE_URL=http://host.docker.internal:11434
OLLAMA_MODEL=qwen3:8b
```

The planner uses temperature 0, an action whitelist and JSON-only outputs. If Ollama is unavailable
or returns invalid JSON, the service records a warning and falls back to the deterministic planner.
The deterministic FBG quality guardrail remains authoritative in every mode.

## 6. HTTP API

Analyze a window:

```http
POST /v1/analyze
Content-Type: application/json

{"window_id":"W008"}
```

The response includes `decision`, `evidence`, `tools_called`, `planner_trace`,
`guardrail_applied` and `request_id`. Full observations are written to
`runtime/audit.jsonl` for traceability.

## 7. Git workflow

```bash
git status
git add .
git commit -m "feat: initialize restricted UAV FBG agent"
git branch -M main
git remote add origin <YOUR_GITHUB_REPOSITORY_URL>
git push -u origin main
```

Do not force-add the CSV files. Verify they remain ignored before pushing:

```bash
git status --ignored
```

## Known data limitations

- The supplied files cover one flight of about 178 seconds.
- No explicit anomaly or fault ground-truth labels are present.
- FBG validity is about 61%; W027 has a validity ratio of 0.30.
- The synchronized FBG rate is about 9.9 Hz. High-frequency motor/propeller vibration analysis may
  require the original, non-downsampled FBG data.

