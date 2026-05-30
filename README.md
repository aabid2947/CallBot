# VoiceStream

A voice AI **proxy caller**: it calls a hospital on a user's behalf and
books an appointment for them, speaking as the user in first person and
answering the receptionist's questions from the user's saved details.

Built as a **reusable microservice**: the `core` package
(`BookingRequestService` + the agent brain) has no voice/web/transport
dependencies, so the Phase-1 intake microservice — and any other consumer
— can install it and call directly. The voice pipeline + WebRTC server in
this repo are the test deployment for Phase 2 (the outbound call).

## Free accounts you need

No credit card required for either.

| Service | Used for | Get a key |
|---|---|---|
| **Groq** | LLM (the agent brain) | https://console.groq.com |
| **Deepgram** | Speech-to-text + Aura TTS voice | https://console.deepgram.com |

## Setup

```powershell
# 1. Create + activate the virtual environment
python -m venv .venv
.\.venv\Scripts\Activate.ps1          # Windows PowerShell
#   cmd:          .\.venv\Scripts\activate.bat
#   macOS/Linux:  source .venv/bin/activate

# 2. Install dependencies (into the venv)
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe -m pip install -e ".[dev,web]"

# 3. Configure secrets
copy .env.example .env                # then edit .env and paste your two keys
```

`[dev,web]` adds the web transport + server (FastAPI/uvicorn/WebRTC). A
project that only wants the booking API can install plain `core` with no
voice/web dependencies. `[postgres]` adds the driver for Supabase.

## Run the agent (web roleplay)

```powershell
# Seed the test BookingRequest for "Md Aabid Hussain" so the call has
# someone to represent. (Replace with a real POST /api/booking_requests
# from your Phase-1 microservice in production.)
.\.venv\Scripts\python.exe .\scripts\seed_test_request.py

.\.venv\Scripts\python.exe -m server      # serves on http://localhost:8000
```

Open <http://localhost:8000/> — **you play the hospital receptionist**.
Click **Pick up**, allow the microphone, and answer the agent as it tries
to book the appointment.

- `GET /health` → `{"status": "ok"}`
- `POST /api/offer` → WebRTC SDP offer/answer (503 if no active request)
- `POST /api/booking_requests` → Phase-1 intake hand-off (creates a row)
- `/` → the throwaway browser test client (dev only)

The complete call flow lands in **`logs/`** — `voicestream-<timestamp>.log`
per run (DEBUG: STT/LLM/TTS, tool calls, turns, crashes with traceback) and
a rolling `voicestream.log`. Each call is tagged `[call <pc_id> req=<id>]`.
To debug, open the newest file or `grep` for `CRASHED` / the pc_id.

### One command to run it live (server + public tunnel)

```powershell
python .\scripts\tunnel.py      # needs NGROK_AUTHTOKEN + API keys in .env
```

This starts the server, waits for it to be healthy, then opens an ngrok
HTTPS tunnel and prints the public URLs. Ctrl+C stops both.

**Caveat:** ngrok forwards the HTTP signaling so the page loads and
negotiates, but WebRTC *audio* is peer-to-peer UDP and does not flow
through ngrok — across different networks the audio may not connect
without a TURN server. For a reliable single-machine test just use
<http://localhost:8000/> (no tunnel needed; browsers allow the mic on
localhost).

## How another project consumes the core

The microservice boundary is the **`core` package only**
(`BookingRequestService` + the agent brain). It has no voice, web,
transport, or Pipecat dependencies — install just `core` and import it:

```bash
pip install /path/to/voicestream         # installs voicestream-core (core only)
# its only runtime deps: SQLAlchemy, python-dotenv
```

### As the Phase-1 intake microservice (write a request)

```python
from datetime import date
from core.booking import BookingRequestService, init_db

init_db()                                # uses DATABASE_URL (default: local SQLite)

svc = BookingRequestService()
result = svc.create(
    full_name="Md Aabid Hussain",
    date_of_birth=date(2000, 1, 15),
    phone="+91-9876-543210",
    appointment_reason="general health checkup",
    insurance_provider="Test Insurance Co.",
    target_hospital_name="City Care Hospital",
    preferred_time_of_day="afternoon",
)
# result.ok / result.error (BookingRequestError) / result.request (BookingRequestView)
```

You can also POST it to a running server:

```bash
curl -X POST http://localhost:8000/api/booking_requests \
  -H "Content-Type: application/json" \
  -d '{"full_name":"Md Aabid Hussain","date_of_birth":"2000-01-15",
       "phone":"+91-9876-543210","appointment_reason":"general health checkup"}'
# {"id": 1, "status": "pending"}
```

Either way, the next `/api/offer` (browser test client) binds the proxy
call to the most recent active row automatically.

### As a custom agent host (drive the proxy call with your own LLM)

```python
from core.agent import (
    ToolDispatcher, TOOL_SCHEMAS, build_system_prompt,
)
from core.booking import BookingRequestService

svc = BookingRequestService()
request_id = svc.latest_active().id      # whichever row to call about

system = build_system_prompt(
    caller_name="Md Aabid Hussain",
    target_hospital_name="City Care Hospital",
)                                         # spoken-style persona
# Feed `system` + TOOL_SCHEMAS to any OpenAI-compatible model (Groq, OpenAI, ...).

# Route each tool call the model emits:
disp = ToolDispatcher(svc, booking_request_id=request_id)
out = disp.dispatch("get_caller_info", {})    # JSON-serialisable dict, never raises
# {"ok": True, "caller": {"full_name": "Md Aabid Hussain", ...}}
```

**Entry points:** `core.booking.BookingRequestService` (+
`BookingRequestError`, `BookingRequestResult`, `BookingRequestView`,
`BookingRequestStatus`), `core.agent.ToolDispatcher`,
`core.agent.TOOL_SCHEMAS`, `core.agent.build_system_prompt`.
**Required env for core:** `DATABASE_URL` (optional; defaults to
`sqlite:///voicestream.db`). The Groq/Deepgram keys are only needed by
the *voice* layer, not by the core.

### Use Supabase / Postgres instead of SQLite

No code changes — it's a config + driver swap:

```powershell
.\.venv\Scripts\python.exe -m pip install -e ".[postgres]"
# In .env, point DATABASE_URL at your Supabase Session-pooler URI:
#   DATABASE_URL=postgresql://postgres.<ref>:<PW>@aws-0-<region>.pooler.supabase.com:5432/postgres?sslmode=require
.\.venv\Scripts\python.exe .\scripts\seed_test_request.py
```

Tables auto-create on first use. `postgres://` is auto-normalised to
`postgresql://`, and the engine adds connection health-checks/recycling so
Supabase's idle-connection drops don't break the app. See `.env.example`
and `instruction.md` for pooler/SSL details.

## Architecture (one rule)

```
testclient/ → server/ → transport/ → voice/ → core/
```

Dependencies point inward only. `core/` imports nothing from the outer
layers, which is what makes it reusable elsewhere. Moving from web to a
mobile app needs no change; moving to real phone calls is a single added
transport file (`transport/twilio.py` SWAP SEAM).
