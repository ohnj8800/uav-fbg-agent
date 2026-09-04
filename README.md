# UAV/FBG Restricted LLM Agent

雙 Docker 的受限制 LLM Agent 飛行資料判讀系統。資料分析服務獨佔 CSV，
Agent 只能透過白名單工具取得 evidence，並輸出固定四類判讀結果。

This project interprets whether FBG changes are attributable to real DataFlash flight state.
It does not classify UAV anomalies or diagnose faults.

## Current scope

- `analysis-service`: CSV parsing, window lookup, flight events, neighbor comparison,
  FBG quality guardrail and deterministic evidence.
- `agent-service`: restricted tool planning, optional local Ollama backend, structured output,
  decision validation, browser interface and JSONL audit trail.
- Mandatory preflight rule: when `fbg_validity_ratio < FBG_VALIDITY_THRESHOLD`, the request is
  stopped before the planner/LLM is called and the decision is always `INSUFFICIENT_DATA`.

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
scripts/              single-window and batch command-line clients
results/              local batch outputs; ignored by Git
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

Each interpretation separately records `llm_attempts`, `llm_successes`, `llm_succeeded`,
`fallback_used`, `effective_backend` and all `planner_warnings`. Therefore an Ollama-configured
request that actually used deterministic fallback cannot be mistaken for a successful LLM result.

Check both data-service and configured planner/model readiness:

```powershell
Invoke-RestMethod http://127.0.0.1:8000/health | ConvertTo-Json -Depth 5
```

For Ollama, the health response records the configured model name, endpoint reachability and
whether that exact model is installed. A reachable service with a missing model is reported as
`degraded`; deterministic fallback remains available for interpretation requests.

## 6. HTTP API

Analyze a window:

```http
POST /v1/analyze
Content-Type: application/json

{"window_id":"W008"}
```

The response includes `decision`, human-readable `evidence`, deterministic `evidence_data`,
`constrained_decision`, `abstain`, `abstain_reason`, `context_source`, `tools_called`,
`tool_trace`, `reasoning_trace`, `guardrail_applied`, `planner_invoked`, `llm_invoked` and
`request_id`. `planner_backend` and `planner_model` identify the configured interpretation
backend. `evidence_data` directly exposes the FBG validity/STD/RMS/P2P and verified real-state
flight phase/roll/pitch used for interpretation. Full observations are written to
`runtime/audit.jsonl` for traceability. The reasoning trace contains concise, auditable rule and
tool-selection reasons; it does not store private chain-of-thought.

List the windows available through the restricted service boundary:

```http
GET /v1/windows
```

The endpoint returns IDs only. It does not expose the CSV path or raw rows to the Agent container.

## 7. Web interface

After starting the Docker services, open:

```text
http://127.0.0.1:8000/
```

The interface shows service/model readiness, lets the user select and analyze one window, plots
the bounded window series, and displays the constrained decision, abstention status, evidence,
tool/reasoning trace and structured JSON. The UI calls only `agent-service`; it never mounts or
opens the CSV files directly.

## 8. Batch analysis

Start with a short Ollama run to confirm latency and outputs:

```powershell
python scripts\analyze_batch.py --limit 5
```

Analyze every available window:

```powershell
python scripts\analyze_batch.py
```

Each run creates three ignored local files under `results/`:

- `batch_<time>.jsonl`: complete traceable result for each window.
- `batch_<time>.csv`: compact table suitable for Excel and plots.
- `batch_<time>_summary.json`: counts, failures, elapsed time and output paths.

Windows rejected by the quality preflight do not invoke Ollama. For a fast deterministic baseline,
set `LLM_MODE=heuristic` in `.env` and recreate `agent-service` before running the batch.

## 9. Lab deliverables

Generate the exact files requested by the lab and three representative real-window figures:

```powershell
python scripts\generate_deliverables.py
```

This creates the following ignored local outputs under `results/deliverables/`:

- `llm_window_outputs.csv`: one constrained decision, evidence set and abstention status per
  window.
- `agent_trace.jsonl`: complete structured tool, rule, model and execution trace per window.
- `llm_eval.csv`: contract, safety, abstention and trace-completeness evaluation. Without
  validated reference labels, this file does not claim classification accuracy.
- `window_figures/W003_verified_real_state.svg`
- `window_figures/W004_verified_real_state.svg`
- `window_figures/W027_verified_real_state.svg`

The current result stage is explicitly recorded as `DEVELOPMENT`, with
`context_source=VERIFIED_REAL_STATE`. These are the deliverable results for the present project
stage; they must not be represented as validated-DT results.

### Publication Figure c view

After generating the deliverables and starting Docker Compose, open the detailed inspection UI
at <http://127.0.0.1:8000/> or the white publication dashboard at
<http://127.0.0.1:8000/paper>. The publication view reads the generated CSV and JSONL files; its
counts and representative traces are not hard-coded.

Create a reproducible 1800 × 650 PNG screenshot with Chrome or Microsoft Edge:

```powershell
powershell -ExecutionPolicy Bypass -File scripts\capture_publication_view.ps1
```

The image is written to
`results/deliverables/figure_c_publication_dashboard.png`. The publication panel intentionally
keeps only an enlarged FBG waveform, one verified flight-state context statement and one
constrained decision for each of W003, W004 and W027. Detailed metrics, model execution fields
and complete traces remain available in the main interface and deliverable files rather than
being crowded into the paper figure.

## 10. Interpretation consistency evaluation

Repeat representative interpretations and verify the fixed four-class contract, structured
evidence and deterministic quality guardrail:

```powershell
python scripts\evaluate_consistency.py --windows W003 W004 W027 --repeats 3
```

The evaluator reports per-window decision agreement, tool sequences and any contract violation.
This measures interpretation stability and rule compliance; it is not anomaly-detection accuracy.
Its CSV, JSONL and summary JSON outputs are written under the ignored `results/` directory.

## 11. Git workflow

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

## Scope and data limitations

- The supplied files cover one flight of about 178 seconds.
- The four decisions describe FBG-to-flight-state attribution, not normal/anomaly or fault labels.
- FBG validity is about 61%; W027 has a validity ratio of 0.30.
- The synchronized FBG rate is about 9.9 Hz. High-frequency motor/propeller vibration analysis may
  require the original, non-downsampled FBG data.

