# ApkaPG AI Outbound Calling & Lead Collection System

Lightweight FastAPI backend that initiates outbound AI calls to PG owners through **Dograh AI** (using Dograh's configured **Vobiz** telephony, **Groq** LLM, **Sarvam** TTS, and existing STT), collects structured lead outcomes, and saves leads locally in **JSON** and **CSV** for future use by the ApkaPG production application.

> **Scope Note:** This service only collects and exports leads. It does not connect to or modify any production SQL database or PG registration system.

---

## Project Structure

- `app/main.py` — FastAPI application, `/health`, `/api/system/status`, middleware
- `app/config.py` — Environment configuration (`Settings`)
- `app/api/calls.py` — Outbound call initiation, listing, detail, Dograh run sync
- `app/api/webhooks.py` — Idempotent webhook receiver (`POST /api/calls/webhook`)
- `app/api/leads.py` — Lead listing, filtering, JSON/CSV export endpoints
- `app/services/` — Calling orchestration, Dograh client, Vobiz status mapping, extraction, export
- `app/storage/` — Thread/process-locked atomic JSON and CSV storage
- `dograh/` — 9-node Dograh AI conversation guide and webhook payload template
- `data/` — Local runtime storage (`leads/`, `calls/`, `transcripts/`)

---

## Quick Start (Windows)

```cmd
py -3.12 -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
copy .env.example .env
```

Edit `.env` to set `API_AUTH_KEY`, `DOGRAH_API_KEY`, `DOGRAH_WORKFLOW_UUID`, and `DOGRAH_WEBHOOK_SECRET`. Keep `MOCK_PROVIDERS=true` for safe local testing without placing real calls.

### Run Automated Tests

```cmd
pytest -v
```

### Run Backend Server

```cmd
uvicorn app.main:app --host 127.0.0.1 --port 8001 --reload
```

- Health check: `http://127.0.0.1:8001/health`
- System status: `http://127.0.0.1:8001/api/system/status`
- Interactive API docs: `http://127.0.0.1:8001/docs`

---

## API Endpoints

| Method | Path | Auth | Purpose |
|---|---|---|---|
| `GET` | `/health` | None | Service & storage readiness check |
| `GET` | `/api/system/status` | None | Dograh & Vobiz configuration/reachability status |
| `POST` | `/api/calls/initiate` | `X-API-Key` | Initiate an outbound call |
| `GET` | `/api/calls` | None | List all call records |
| `GET` | `/api/calls/{call_id}` | None | Get single call record & event history |
| `POST` | `/api/calls/{call_id}/sync` | `X-API-Key` | Pull latest run status/context from Dograh |
| `POST` | `/api/calls/webhook` | Webhook Secret | Receive Dograh/Vobiz call & extraction webhooks |
| `GET` | `/api/leads` | None | List/filter collected leads |
| `GET` | `/api/leads/{call_id}` | None | Get single lead record |
| `GET` | `/api/leads/export/json` | None | Download leads as JSON |
| `GET` | `/api/leads/export/csv` | None | Download leads as CSV |