# ApkaPG AI Outbound Calling & Lead Collection System

An AI-powered outbound calling and lead-collection backend built with **Python 3.12** and **FastAPI**. It initiates outbound phone calls to PG owners via **Dograh AI** (using Dograh's built-in **Vobiz** telephony, **Groq** LLM, **Sarvam** TTS, and configured STT), tracks call lifecycles, extracts structured lead outcomes, and saves leads locally in **JSON** and **CSV** for future ingestion by the existing ApkaPG production application.

> **Strictly In-Scope Only:** This project only initiates calls, collects lead outcomes, and exports JSON/CSV files locally. It does **not** connect to SQL Server, `PGProduction`, or `PGMaster`, and does **not** register, verify, or publish PG listings.

---

## 1. Architecture Overview

```text
Operator Dashboard (/) or API Client
        │
        ▼
FastAPI Backend (Port 8001)
   ├── Single Call & Sequential Batch Runner
   ├── Opt-Out (Do Not Call) Pre-Check
   └── Local Atomic Storage (JSON + CSV under data/)
        │
        │ POST /api/v1/public/agent/workflow/{workflow_uuid}
        ▼
Dograh AI (Port 8000 API / Port 3010 UI)
   ├── Telephony: Vobiz (Outbound PSTN Calling)
   ├── LLM: Groq
   ├── TTS: Sarvam AI
   └── STT: Configured Dograh/LiveKit Transcriber
        │
        │ Post-Call Webhook (POST /api/calls/webhook)
        │  or Manual / Batch Fallback Sync (POST /api/calls/{id}/sync)
        ▼
FastAPI Extraction & Persistence
   ├── data/calls/call_history.json
   ├── data/leads/leads.json & leads.csv
   ├── data/transcripts/call_transcripts.json
   └── data/batches/batches.json
```

---

## 2. Project Structure

```text
apkapg-ai-calling/
├── app/
│   ├── main.py                      # FastAPI entrypoint, /health, /api/system/status
│   ├── config.py                    # Environment variable settings
│   ├── api/
│   │   ├── auth.py                  # X-API-Key, webhook secret, service factories
│   │   ├── batches.py               # Sequential batch API (/api/batches*)
│   │   ├── calls.py                 # Calling API (/api/calls*)
│   │   ├── leads.py                 # Leads & JSON/CSV export API (/api/leads*)
│   │   ├── ui.py                    # Built-in localhost dashboard & UI proxy routes
│   │   └── webhooks.py              # Idempotent Dograh webhook endpoint
│   ├── models/
│   │   ├── batch.py                 # Batch & item Pydantic schemas
│   │   ├── call.py                  # Call lifecycle & transcript schemas
│   │   └── lead.py                  # Structured lead & outcome schemas
│   ├── services/
│   │   ├── batch_service.py         # CSV/text parser + one-by-one batch runner
│   │   ├── calling_service.py       # Call orchestration, webhook & sync handling
│   │   ├── dograh_service.py        # Dograh REST API client
│   │   ├── export_service.py        # Filtered lead queries & JSON/CSV exports
│   │   ├── extraction_service.py    # Normalizes gathered_context without inventing data
│   │   └── vobiz_service.py         # Vobiz status normalization & mode checks
│   ├── static/
│   │   └── index.html               # Built-in operator dashboard UI
│   ├── storage/
│   │   ├── csv_storage.py           # Atomic, consistent-column CSV writer
│   │   ├── file_lock.py             # Cross-platform thread + sidecar file locks
│   │   └── json_storage.py          # Atomic JSON repositories
│   └── utils/
│       ├── logger.py                # Console logging with automatic secret redaction
│       └── validators.py            # E.164 / Indian phone normalization
├── dograh/
│   ├── apkapg_minimal_workflow.json # Active minimal PG owner check workflow (importable)
│   ├── workflow_guide.md            # Full 9-node conversation workflow reference
│   └── webhook_payload_template.json# Webhook JSON template for Dograh
├── data/                            # Local runtime data (gitignored)
│   ├── batches/batches.json
│   ├── calls/call_history.json
│   ├── leads/leads.json & leads.csv
│   └── transcripts/call_transcripts.json
├── tests/                           # Pytest automated test suite (34 tests)
├── .env.example
├── .gitignore
└── requirements.txt
```

---

## 3. Prerequisites

- **Python 3.12**
- **Dograh AI** self-hosted and running (API on port `8000`, UI on port `3010`)
- **Vobiz** account with an outbound phone number & Auth ID/Token
- **Groq** API key & **Sarvam AI** API key (configured inside Dograh)
- **STT (Speech-to-Text)** configured inside Dograh/LiveKit

> **Important Port Note:** Dograh AI uses port `8000`, so this FastAPI backend is configured on port **`8001`** by default.

---

## 4. Setting Up Dograh AI (Step by Step)

Follow these steps in the Dograh dashboard (**`http://localhost:3010`**):

### 4.1 Configure Providers (LLM, TTS, STT)
1. Open **Providers** in the Dograh sidebar.
2. Add/verify your **Groq** key (LLM), **Sarvam** key (TTS), and existing **STT** provider.

### 4.2 Configure Vobiz Telephony
1. Open **Telephony** (`http://localhost:3010/telephony-configurations`) in the Dograh sidebar.
2. Select **Vobiz** and enter your Vobiz **Auth ID** (e.g. `MA_...`), **Auth Token**, and **From Number** (`+91...`).
3. Ensure Dograh has a reachable public endpoint/tunnel (`BACKEND_API_ENDPOINT` in Dograh's `.env`) whenever making real live phone calls so Vobiz can deliver voice & call callbacks to Dograh.

### 4.3 Create a Dograh API Key (`dgr_...`)
1. Open **Developers → API Keys** in the Dograh sidebar.
2. Click **Create API Key** and copy the key starting with **`dgr_...`**.
   - *Note:* Use an **API Key (`dgr_...`)**, **not** a Dograh *Service Key*. The `dgr_` key authenticates our FastAPI backend against Dograh's REST API (`X-API-Key`).

### 4.4 Import & Publish the Voice Workflow
Two workflow references are provided in `dograh/`:
- **`dograh/apkapg_minimal_workflow.json`** *(active minimal flow)*: Confirms if the person is a PG owner/manager first (`owner_role`), then asks only for their name (`owner_name`) and PG name (`pg_name`). Extraction is enabled on every node so partial data is saved even if the caller hangs up midway.
- **`dograh/workflow_guide.md`** *(full 9-node reference)*: Complete blueprint if you want to expand the agent later to also collect city, locality, room counts, interest, and callback consent.

To set up the agent in Dograh:
1. Import **`dograh/apkapg_minimal_workflow.json`** in Dograh UI.
2. In the agent's **Model** settings:
   - **LLM**: Groq (`llama-3.3-70b-versatile` recommended, **Temperature = `0.1` or `0.2`** to prevent hallucinations).
   - **TTS**: Sarvam (`bulbul:v3` or `bulbul:v4-flash`, Indian voice).
   - **STT**: Use your configured Dograh/LiveKit STT provider.
3. Open the **Webhook** node and verify:
   - **Method**: `POST`
   - **Endpoint URL**:
     - If Dograh runs in Docker on Windows/Mac: `http://host.docker.internal:8001/api/calls/webhook`
     - If Dograh runs in Docker on Linux (Azure VM): `http://host.docker.internal:8001/api/calls/webhook` *(requires `extra_hosts: ["host.docker.internal:host-gateway"]` in Dograh's `docker-compose.yml`)*
     - If Dograh runs directly on the host without Docker: `http://127.0.0.1:8001/api/calls/webhook`
   - **Custom Header**: `X-Webhook-Secret` = same value as `DOGRAH_WEBHOOK_SECRET` in `.env`
4. Click **Save** and **Publish**.

### 4.5 Get `DOGRAH_WORKFLOW_UUID` and `DOGRAH_WORKFLOW_ID`
Run this command with your `dgr_...` key while Dograh is running:

```cmd
curl -H "X-API-Key: YOUR_dgr_API_KEY" http://127.0.0.1:8000/api/v1/workflow/fetch
```

Find your published agent in the JSON output and note:
- `"workflow_uuid"` → set as `DOGRAH_WORKFLOW_UUID` in `.env`
- `"id"` → set as `DOGRAH_WORKFLOW_ID` in `.env`

---

## 5. Backend Installation & Configuration

### 5.1 Setup Virtual Environment (Windows CMD)
```cmd
cd apkapg-ai-calling
py -3.12 -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
copy .env.example .env
```
*(On Linux/macOS: `python3.12 -m venv .venv && source .venv/bin/activate && pip install -r requirements.txt && cp .env.example .env`.)*

### 5.2 Environment Variables (`.env`)

| Variable | Required | Description / Where to get it |
|---|---|---|
| `PORT` | Yes | `8001` (keeps clear of Dograh on `8000`) |
| `APP_ENV` | Yes | `development` locally, `production` on server |
| `API_AUTH_KEY` | Yes | Strong secret you invent; required in `X-API-Key` for external API calls |
| `DATA_DIR` | Yes | `data` (folder where JSON & CSV files are stored) |
| `MOCK_PROVIDERS` | Yes | `true` for safe testing without dialing real phones; `false` for live Dograh/Vobiz calls |
| `DOGRAH_API_BASE_URL` | Yes | `http://127.0.0.1:8000` |
| `DOGRAH_API_KEY` | For live calls | `dgr_...` key from Dograh → Developers → API Keys |
| `DOGRAH_WORKFLOW_UUID` | For live calls | Published agent `workflow_uuid` from `/api/v1/workflow/fetch` |
| `DOGRAH_WORKFLOW_ID` | Recommended | Numeric workflow `id` (enables `/sync` fallback polling) |
| `DOGRAH_USE_DRAFT_TEST` | No | `false` (set `true` only to test an unpublished draft in Dograh) |
| `DOGRAH_TELEPHONY_CONFIG_ID` | No | Leave blank to use Dograh's default Vobiz config |
| `DOGRAH_WEBHOOK_SECRET` | Recommended | Secret string matching `X-Webhook-Secret` on Dograh's Webhook node |
| `VOBIZ_MODE` | Yes | `dograh_managed` |
| `VOBIZ_AUTH_ID` | Optional | Reference Vobiz Auth ID (`MA_...`) |
| `VOBIZ_CALLER_ID` | Optional | Reference Vobiz From Number (`+91...`) |

---

## 6. Running & Testing

### 6.1 Run Automated Tests (Safe Mock Mode)
```cmd
pytest -v
```
All **34 tests** should pass (covering health checks, phone validation, thread/process-safe JSON & CSV locking, duplicate webhook protection, opt-out blocking, extraction rules, UI routes, and sequential batch calling).

### 6.2 Start the Backend Server
```cmd
uvicorn app.main:app --host 0.0.0.0 --port 8001 --workers 1
```
*(Use `--workers 1` so the in-memory sequential `BatchRunnerManager` keeps consistent state. Add `--reload` during local development.)*

### 6.3 Open the Built-In Operator Dashboard
Open **`http://127.0.0.1:8001/`** in your browser:
- **Initiate Single Call:** Dial one phone number with optional Owner Name, PG Name, and Campaign ID.
- **Automated Batch Calling:** Upload a `.csv` file (or paste numbers one per line), set gap seconds between calls, and click **Start Sequential Batch**. It dials **one number at a time**, waits for each call to finish before dialing the next, skips `do_not_call` numbers, and supports **Pause / Resume / Stop**.
- **Collected Leads:** Filter by outcome, inspect full extracted JSON, and download **JSON** or **CSV**.
- **Call History:** Inspect lifecycle events (`initiated → ringing → answered → completed/failed`) and click **Sync** anytime to pull run data directly from Dograh.

> **Security Note:** Dashboard routes (`/ui/*`) do not ask for an API key in the browser and **only accept requests from `localhost` (`127.0.0.1`)**. External API callers must hit `/api/*` with `X-API-Key`.

---

## 7. API Endpoints Reference

Interactive Swagger docs are available at **`http://127.0.0.1:8001/docs`**.

| Method | Endpoint | Auth | Description |
|---|---|---|---|
| `GET` | `/` | Localhost UI | Built-in operator dashboard |
| `GET` | `/health` | None | Service & local storage writability check |
| `GET` | `/api/system/status` | None | Live Dograh reachability & Vobiz config status |
| `POST` | `/api/calls/initiate` | `X-API-Key` | Initiate a single outbound call (blocks `do_not_call`) |
| `GET` | `/api/calls` | None | List all call records |
| `GET` | `/api/calls/{call_id}` | None | Get call details and event history |
| `POST` | `/api/calls/{call_id}/sync` | `X-API-Key` | Pull latest run status & `gathered_context` from Dograh |
| `POST` | `/api/calls/webhook` | `DOGRAH_WEBHOOK_SECRET` | Idempotent receiver for Dograh/Vobiz call events |
| `POST` | `/api/batches` | `X-API-Key` | Upload CSV/list and start sequential calling |
| `GET` | `/api/batches` | None | List all batches |
| `GET` | `/api/batches/{batch_id}` | None | Get batch status & per-number progress |
| `POST` | `/api/batches/{batch_id}/pause` | `X-API-Key` | Pause a running batch |
| `POST` | `/api/batches/{batch_id}/resume` | `X-API-Key` | Resume a paused batch |
| `POST` | `/api/batches/{batch_id}/stop` | `X-API-Key` | Stop a batch and cancel remaining pending numbers |
| `GET` | `/api/leads` | None | List leads (filters: `outcome`, `interested`, `do_not_call`, `campaign_id`) |
| `GET` | `/api/leads/{call_id}` | None | Get a single structured lead |
| `GET` | `/api/leads/export/json` | None | Download leads as JSON |
| `GET` | `/api/leads/export/csv` | None | Download leads as CSV |

---

## 8. Local Data Storage & Extraction Rules

All data is persisted atomically using cross-platform file locking (`app/storage/file_lock.py`):
- **`data/leads/leads.json`** & **`data/leads/leads.csv`** — Structured lead and outcome records.
- **`data/calls/call_history.json`** — Every call attempt, status transition, and processed webhook key.
- **`data/transcripts/call_transcripts.json`** — Conversation transcripts/URLs kept separate from lead records.
- **`data/batches/batches.json`** — Sequential batch queue and per-number outcomes.

Extraction guarantees (`app/services/extraction_service.py`):
- Uncollected fields stay `null` (JSON) / blank (CSV)—values are never invented.
- Unrendered Mustache placeholders (e.g. `{{gathered_context.city}}`) and `"N/A"`/`"unknown"` are normalized to `null`.
- Duplicate webhook deliveries (matching `X-Dograh-Delivery-Id`, `event_id`, or payload hash) are ignored.
- Subsequent bare telephony hangup callbacks never overwrite already-extracted lead fields.
- Any phone number with `do_not_call = true` is automatically blocked from future single or batch calls.

---

## 9. Troubleshooting

| Issue | Cause & Fix |
|---|---|
| Server fails to start on `8000` | Dograh uses port `8000`. Keep `PORT=8001` in `.env` and start Uvicorn with `--port 8001`. |
| Call completes in Dograh, but `leads.json` doesn't update | 1) Ensure Dograh Webhook URL is `http://host.docker.internal:8001/api/calls/webhook` (not `127.0.0.1` when Dograh is in Docker).<br>2) Ensure FastAPI was started with `--host 0.0.0.0`.<br>3) Click **Sync** in Call History (requires `DOGRAH_WORKFLOW_ID` in `.env`). |
| `401 Unauthorized` on `/api/calls/webhook` | `X-Webhook-Secret` in Dograh's Webhook node does not match `DOGRAH_WEBHOOK_SECRET` in `.env`. |
| `409 Conflict` when dialing a number | That number has `do_not_call: true` in `data/leads/leads.json` (opt-out protection). |
| `405 Method Not Allowed` on `/api/calls/webhook` in browser | Browser sends `GET`; `/api/calls/webhook` only accepts `POST` JSON payloads. |
| Agent talks too much or hallucinates | Check `dograh/apkapg_minimal_workflow.json` is published, `globalNode` is present, and Groq Temperature is `0.1`–`0.2`. |

---