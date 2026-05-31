# Instructions for future sessions

Read this before doing any work. It is the standing brief: current state
of the codebase, conventions, and gotchas. Update it whenever you learn
something a future session must know. Keep it tight — per-prompt history
belongs in commits, not here.

## What this project is

VoiceStream is a voice AI **proxy caller**: it calls a hospital on a
user's behalf and books an appointment for them, speaking AS the user in
first person. Each call is bound to a `BookingRequest` row that says who
to represent and what to book. Outcomes (confirmed/declined/follow-up)
land back on the same row.

A separate **Phase-1 intake microservice** (not in this repo) writes the
`BookingRequest` rows. This repo builds Phase 2 — the outbound call.

Built as a **reusable microservice**: the `core` package must be
installable on its own (just `SQLAlchemy` + `python-dotenv`), so the
Phase-1 service and any other consumer can depend on it without dragging
in Pipecat / FastAPI / WebRTC.

## The build plan

`prompt.md` holds the ordered build plan. Execute **one prompt per
session**, in order. Prompts 0-11 complete (foundation through
proxy-caller migration). The full suite is the source of truth for green.

## The one rule that must never break

Dependencies point inward only:

```
testclient/ → server/ → transport/ → voice/ → core/
core/ imports NOTHING from voice/, transport/, server/, testclient/.
```

Before finishing any core-touching change, ask: *"Could an unrelated
project import `core` and use it without dragging in the web layer?"*
If not, redesign. The subprocess layer-isolation test enforces this.

## Decided stack (do not change without explicit instruction)

- Python (venv-managed) · Pipecat 1.2.1 (voice pipeline) · Groq (LLM) ·
  Deepgram (STT) · Deepgram Aura (TTS) · SQLite by default behind a
  repository abstraction (Supabase/Postgres swap is config-only) · WebRTC
  web transport (mobile app = same transport; real phone = Twilio file
  added later as a one-file swap).

## Always work inside the virtual environment

Never install packages globally. At the start of every session, activate `.venv`.

```powershell
# Create (one time)
python -m venv .venv

# Activate (every session) — Windows PowerShell
.\.venv\Scripts\Activate.ps1
#   cmd:          .\.venv\Scripts\activate.bat
#   macOS/Linux:  source .venv/bin/activate

# Install deps (after activating)
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe -m pip install -e .[dev,web]
#   add [postgres] for Supabase

# Run tests
.\.venv\Scripts\python.exe -m pytest
```

If PowerShell blocks activation, the `.venv\Scripts\python.exe` form used
above works without activating and is the safe default for scripted
commands.

## Bookkeeping you must maintain every change

- **`repo_structure.md`** — update the tree + one-line description
  whenever a file/folder is added, moved, or removed.
- **`instruction.md`** (this file) — append gotchas and decisions a
  future session must know. Trim when something becomes obsolete.
- **Secrets** — only via `.env` (git-ignored). Update `.env.example`
  whenever a new env variable is introduced.

## Conventions

- Core config is read only from env via `core/config.py`. Do not add
  web/HTTP settings there — those belong in `server/`.
- End every change green (`pytest -q`). Report the actual output before
  declaring done.

## Core booking domain (`core.booking`)

Public API (re-exported from `core.booking`):

- `BookingRequest` (model) + `BookingRequestStatus`
  (`pending / in_progress / confirmed / declined / failed`).
- `BookingRequestRepository` (abstract) + `SqlAlchemyBookingRequestRepository`.
  Data-access only. `record_outcome(...)` accepts an optional `status=`
  so outcome + transition land in a single SQL transaction.
- `BookingRequestService` (`core/booking/proxy_service.py`):
  `create`, `get`, `latest_active`, `mark_in_progress`,
  `record_confirmed`, `record_declined`, `record_followup`,
  `seed_test_request`. Returns `BookingRequestResult` + `BookingRequestView`.
- Transitions: only `PENDING` and `IN_PROGRESS` are "active";
  `mark_in_progress` is idempotent; `record_confirmed/declined/followup`
  refuse to operate on a terminal row. Naive datetimes are coerced to UTC.
- `seed_test_request()` inserts the **Md Aabid Hussain** stub. Idempotent
  while an active request with the seed name exists; creates a fresh row
  once the previous one is terminal.
- `init_db()` builds the engine from `DATABASE_URL`. In-memory SQLite
  (`sqlite://`) uses a `StaticPool` (tests rely on this). For non-SQLite
  URLs the engine gets `pool_pre_ping=True` + `pool_recycle=1800` so
  hosted-Postgres idle drops never surface as errors. `postgres://` is
  auto-normalised to `postgresql://` so Supabase URIs paste verbatim.
- All datetimes are timezone-aware UTC via the `UtcDateTime` decorator
  (SQLite would otherwise return naive). Always pass aware UTC datetimes.
- **Feature 4 (AIVA / general appointments).** `BookingRequest` is now
  general-purpose: `appointment_type` ∈ `medical | meeting | service | other`;
  `date_of_birth` (and insurance fields) are required ONLY for `medical`
  (enforced in `proxy_service.create` and `POST /api/booking_requests`).
  AIVA-owned, nullable columns drive + map the call: `caller_user_id`,
  `aiva_chat_id`, `scheduled_call_at`, `contact_info`, `target_phone`,
  `call_triggered_at`, `outcome_notified_at`. The booking DB is SHARED with
  AIVA (one Supabase Postgres): fresh DB → `init_db()` create_all; existing
  table → run `scripts/upgrade_booking_schema.sql`. `/api/offer` now binds an
  optional `request_id` (query or body; else `latest_active()`) and allows only
  ONE live call (409 otherwise); the persona + `get_caller_info` adapt to
  `appointment_type` (clinical fields only for `medical`); `record_*` outcomes
  persist to the shared row.

## Agent brain (`core.agent`) — proxy caller

- `build_system_prompt(*, caller_name=, target_hospital_name=, now=)` —
  spoken-style first-person persona. The agent speaks AS the caller, uses
  ONLY tool-returned facts, reads back the proposed time, and calls
  exactly ONE `record_*` outcome per call.
- `TOOL_SCHEMAS` — 5 OpenAI-style tools (no SDK imports), works with Groq
  or any OpenAI-compatible model:
  `get_caller_info`, `get_appointment_request`,
  `record_appointment_confirmed`, `record_appointment_declined`,
  `record_appointment_followup`.
- `ToolDispatcher(service, booking_request_id=None)` — bound to a single
  request for the whole call session; the LLM never juggles ids. Unbound
  → `{"error": "not_bound"}`. Bad id → `not_found`. Same "never raises;
  always JSON-serialisable dict" contract.
- ISO 8601 datetimes (with `Z`) are parsed to aware UTC by the dispatcher.

## Voice pipeline (`voice`) — Pipecat assembly

- Pipecat **pinned to 1.2.1**, optional `voice` / `web` extras only — NOT
  a `core` dependency.
- `build_pipeline_task(transport, *, booking_request_id, ...)` — id is
  **required**. The transport is structurally-injected (`TransportLike`
  protocol), so voice never builds a web/Twilio transport.
- Pipeline order: `transport.input -> Deepgram STT -> ctx.user -> Groq
  LLM -> Deepgram Aura TTS -> transport.output -> ctx.assistant`.
- Pipecat 1.2.1 specifics already handled (do not "fix" back): use the
  non-deprecated `Service.Settings(...)` (NOT `live_options=` / `model=` /
  `voice=` kwargs); context is `LLMContext` from
  `pipecat.services.llm_service`; aggregators via
  `LLMContextAggregatorPair`; tools as `FunctionSchema` / `ToolsSchema`;
  tool handler is `async def(params: FunctionCallParams)` →
  `await params.result_callback(...)`.
- `enable_rtvi=False` on the task — the audio-only test client opens no
  data channel; without this Pipecat floods the log with "data channel"
  warnings. Do not re-enable RTVI without giving the client a data channel.
- `load_voice_settings()` fails fast with an actionable message if
  `GROQ_API_KEY` / `DEEPGRAM_API_KEY` are missing.
- Defaults (override via `.env`): LLM `llama-3.3-70b-versatile`, STT
  `nova-3`, TTS voice `aura-2-thalia-en`, `BUSINESS_NAME`.

## Transport (`transport`) — the ONE swappable layer

`transport/web.py` builds a Pipecat `SmallWebRTCTransport`. It imports
Pipecat only — never `core`, `voice`, or `server` (enforced by the
isolation test). To change how the agent reaches the other party you
only touch this folder:

- *Mobile app* = NO change. Same WebRTC; only the client UI differs.
- *Real phone* = add `transport/twilio.py` (`FastAPIWebsocketTransport` +
  `TwilioFrameSerializer`, 8 kHz µ-law) exposing `create_phone_transport(...)`
  of the same shape, and point the server's connection handler at it.
  `core/` and `voice/` stay untouched. See SWAP SEAM doc in
  `transport/web.py`.

## Server (`server`) — FastAPI glue

- `create_app()`. Routes:
  - `GET /health` → `{"status": "ok"}`.
  - `POST /api/booking_requests` — Phase-1 intake hand-off. Required
    fields: `full_name`, `appointment_reason`, `date_of_birth` (ISO date),
    `phone`. Returns `{id, status}`. 400 on bad input.
  - `POST /api/offer` — WebRTC SDP offer/answer (non-trickle). On a fresh
    negotiation: resolves `BookingRequestService.latest_active()`, **503
    if none**, calls `mark_in_progress(id)`, then binds the pipeline to
    that id + the resolved `caller_name` / `target_hospital_name`.
    Renegotiation offers (carrying `pc_id`) skip the lookup.
- Lifespan fails fast via `load_voice_settings()` + `init_db()`. A shared
  `BookingRequestService` lives on `app.state.requests`.
- Bot greets first: `transport.on_client_connected` → `LLMRunFrame`.
- Pipeline-task exceptions are captured into the logs (used to be
  silently swallowed). Look for `CRASHED` in `logs/voicestream.log`.

## Logs

- `server/logging_setup.py` `configure_logging()` runs at `server.app`
  import. Writes the full DEBUG flow to `logs/`:
  - `logs/voicestream-<UTC timestamp>.log` — one file per server run.
  - `logs/voicestream.log` — rolling aggregate (20 MB rotation, 14-day
    retention, zip-compressed).
  - Console stays at INFO; files are DEBUG.
- Every call line carries `[call <pc_id> req=<id>]`. `grep req=<n>` to
  follow one caller end-to-end.
- Secret safety: loguru `diagnose=False` (never dumps locals → API keys
  / DB URL never hit the log); `backtrace=True` keeps full stacks. Do NOT
  set `diagnose=True`.
- The logging-setup tests assert only the wiring contract; they must not
  reconfigure global sinks (would destabilise the suite).

## Switching the DB to Supabase / Postgres (config + driver only)

1. `pip install -e ".[postgres]"`.
2. Supabase → Connect → copy the **Session pooler** URI (IPv4, port
   5432). The Transaction pooler (6543) is for serverless/short-lived.
3. `.env`: `DATABASE_URL=...?sslmode=require` (URL-encode special chars
   in the password).
4. `python ./scripts/seed_test_request.py` — schema auto-creates on
   first use; row is inserted.
5. Verify: `python -c "from core.booking import init_db; init_db(); print('db ok')"`.

There is no SQLite-only code outside `db.py` (verified: models use
generic SQLAlchemy types, `UtcDateTime` works on Postgres `timestamptz`,
the enum is stored as a string).

## Exposing the server publicly (`scripts/tunnel.py`)

- `python scripts/tunnel.py` is the one-command "go live": it starts the
  server as a child process (`python -m server`), polls `/health` until
  ready (timeout 120s; Pipecat import is slow), then opens the ngrok
  tunnel. Ctrl+C tears down both. If the server exits during startup
  (usually missing `GROQ_API_KEY` / `DEEPGRAM_API_KEY`) the script
  reports it and exits 1 instead of hanging.
- ngrok upstream MUST be `127.0.0.1:<PORT>`, never `localhost:<PORT>`.
  Some machines' ngrok agent can't DNS-resolve "localhost"
  (ERR_NGROK_8012). Do not change
  `ngrok.connect(f"127.0.0.1:{PORT}", "http")` back to a bare port.
- Console-encoding gotcha (Windows cp1252): keep this script ASCII-only.
  Unicode box-drawing chars crash with `UnicodeEncodeError`.

## Testing across networks (remote tester / different country)

WebRTC signaling goes through HTTPS (the tunnel), but the **audio is P2P
UDP**. Across two different NATs the only thing that works is **relay
through a TURN server**. STUN alone (the default) only helps when both
peers are on direct-ish networks.

Empirically: a Kenyan tester tried to call and the server's `/api/offer`
answered fine, but ICE never reached `SUCCEEDED` — 35s of binding
requests with no replies — and the call hung silently. That is the
classic "no TURN" failure mode.

### Configure TURN (the proper fix; the server + browser both use the same list)

Put TURN credentials in `.env`:

```env
TURN_URLS=turn:openrelay.metered.ca:80,turn:openrelay.metered.ca:443,turn:openrelay.metered.ca:443?transport=tcp
TURN_USERNAME=openrelayproject
TURN_CREDENTIAL=openrelayproject
```

The server constructs its `SmallWebRTCRequestHandler` with these and
exposes them at `GET /api/ice_servers`; the browser test client fetches
that URL before creating its `RTCPeerConnection`. **Both sides match**,
so when direct UDP fails the audio is relayed through TURN.

Free options (in order of reliability):
- **Open Relay (Metered)** — anonymous endpoint above. Zero signup,
  sometimes flaky but fine for development.
- **Metered.ca** signup — free 50 GB/month, your own credentials.
- **Cloudflare Realtime TURN** — generous free tier, requires CF account.
- **expressturn.com** — free signup tier.

The server logs `ICE: 1 STUN, 1 TURN` on startup when configured; logs
`No TURN server configured` as a warning otherwise.

### Quick alternative — Tailscale (no TURN, free, two-trusted-parties)

If your remote tester can install Tailscale and join your tailnet, both
machines see each other on `100.x.x.x` and ICE finds a direct pair via
WireGuard — no TURN needed. Tell them to open the Tailscale IP of your
machine (e.g. `http://100.x.x.x:8000/`). Works because Tailscale
candidates appear during ICE gathering.

### TURN_URLS format gotcha (real bug we hit)

Each URL **must** start with `turn:`, `turns:`, `stun:`, or `stuns:`.
Without the scheme it is silently dropped — startup logs
`ICE: 1 STUN, 0 TURN` and the call hangs across NATs even though the env
looks set. `transport/web.py::ice_servers_config()` now logs a loud
**ERROR** at startup listing the bad entries; do NOT remove that check.

Wrong: `TURN_URLS=free.expressturn.com:3478`
Right: `TURN_URLS=turn:free.expressturn.com:3478` (or `relay1.expressturn.com`
       — copy the exact hostname from the provider's dashboard).

### Verify TURN before involving a remote tester (relay-only self-test)

The test client supports `?relay` on the URL — opens with
`iceTransportPolicy: "relay"`, which forces the browser to use ONLY TURN
candidates (drops host + srflx). Strictly more demanding than a real
remote call:

1. Restart server; confirm startup log shows `ICE: N STUN, N TURN`
   (NOT `0 TURN` and NOT the warning).
2. Open `http://localhost:8000/?relay` — a yellow banner confirms
   relay-only mode is active.
3. Click **Pick up**. If the agent greets you, TURN works end-to-end and
   a remote tester will also succeed.
4. If it hangs, TURN is misconfigured — check the trickle-ice tool below.

Independent TURN sanity check (no code, just a browser):
<https://webrtc.github.io/samples/src/content/peerconnection/trickle-ice/>.
Paste your TURN URL/username/credential, click "Gather candidates".
If you see entries with `type=relay`, the credentials are valid; if only
`host` shows up, the TURN server rejected them or is unreachable.

### When even TURN fails

- Check `/api/ice_servers` from the remote browser — does it return both
  STUN + TURN entries with the `turn:` scheme intact?
- `chrome://webrtc-internals` → look for `relay` candidates in the
  gathered list and a `relay`-typed selected candidate pair.
- Server log: `Connection(N) ICE completed` on success. If you see
  `IN_PROGRESS` for 30+ seconds with no `SUCCEEDED`, no usable candidate
  pair was found — TURN is missing, mistyped (no `turn:` prefix), or the
  TURN server itself is unreachable from one of the parties.

## Live-call diagnostics — WebRTC media caveat (confirmed empirically)

Over the ngrok tunnel the page loads and negotiates, but audio is
choppy/garbled (`read_audio_frame: Timeout: No audio frame received`,
fragmented STT, idle-timeout). WebRTC media is P2P UDP and does not flow
through ngrok; across different networks/NATs it will not connect
without a TURN server. **For a clean test, use
<http://localhost:8000/>** — media is direct and reliable. Do not "fix"
this in code; it is a network-path issue.

## Manual live-voice runbook

Requires real keys + a microphone. The hospital is YOU in a browser.

1. `copy .env.example .env`, fill `GROQ_API_KEY` and `DEEPGRAM_API_KEY`.
2. (Optional) `python -m tools.latency_probe` — sanity-check keys +
   latency.
3. `python ./scripts/seed_test_request.py` — inserts the Md Aabid Hussain
   `BookingRequest` so the next call has someone to represent.
   (In production, the Phase-1 microservice POSTs to
   `/api/booking_requests` instead.)
4. `python -m server` (or `python ./scripts/tunnel.py` for public URL),
   open <http://localhost:8000/>, click **Pick up**.
5. Play the hospital receptionist. The agent will speak as Md Aabid
   Hussain, answer your questions from the seeded info, and try to book.
6. Verify the row updated:
   `python -c "from core.booking import init_db, SqlAlchemyBookingRequestRepository as R; init_db(); print(R().get(1))"`

## Latency

- `python -m tools.latency_probe` measures real Deepgram TTS, Deepgram
  STT, and Groq LLM time-to-first-token over HTTPS (no mic). Skips with
  guidance and exit 0 if keys are absent.
- Tuning levers (priority order):
  1. Groq model (`LLM_MODEL`): biggest knob. `llama-3.3-70b-versatile`
     is the quality default; switch to a smaller/faster Groq model if
     first token > ~400 ms.
  2. Streaming is already on (STT interim + LLM tokens + Aura TTS) — the
     felt latency is first-token/first-audio, well below the probe's
     full-response numbers.
  3. Region/network: use a host close to Groq/Deepgram regions.
  4. Barge-in tightness: add a Silero VAD analyzer on the web transport
     in `transport/web.py` if needed (basic STT-endpoint turn detection
     works without it).

## Layer guardrails

Live in `tests/test_layer_isolation.py` and run in a **subprocess**
(order-independent). Do not re-add in-process `sys.modules` guardrails —
they false-fail once another test imports Pipecat.
