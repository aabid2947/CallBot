# Repository structure

> **Keep this file current.** Every time a file/folder is added, moved, or
> removed, update this tree and its one-line description in the same change.

```
voicestream/
├── core/                       # REUSABLE microservice core (no web, no transport, no Pipecat)
│   ├── __init__.py             # Package marker + version; states the no-outer-deps rule
│   ├── config.py               # Core-only settings loaded from env (.env): API keys, DATABASE_URL
│   ├── booking/                # Booking-request domain: persistence + business logic
│   │   ├── __init__.py         # Re-exports the public booking API
│   │   ├── models.py           # SQLAlchemy: BookingRequest + BookingRequestStatus + UtcDateTime
│   │   ├── db.py               # Engine/session from DATABASE_URL (SQLite or Supabase/Postgres)
│   │   ├── repository.py       # BookingRequestRepository (abstract) + SQLAlchemy impl
│   │   └── proxy_service.py    # BookingRequestService: rules, transitions, seed_test_request
│   └── agent/                  # Agent brain: proxy-caller persona + tools + dispatcher
│       ├── __init__.py         # Re-exports the public agent API
│       ├── prompts.py          # build_system_prompt: speaks AS the caller, first-person
│       ├── tools.py            # OpenAI-style TOOL_SCHEMAS: 5 proxy-call tools
│       └── dispatcher.py       # ToolDispatcher: bound to BookingRequestService + request_id
├── voice/                      # Pipecat pipeline assembly; uses core, transport-agnostic
│   ├── __init__.py             # Re-exports the public voice API
│   ├── config.py               # VoiceSettings + load_voice_settings() fail-fast key validation
│   └── pipeline.py             # build_pipeline_task(): Deepgram STT -> Groq LLM -> Aura TTS
├── transport/                  # The ONE swappable layer: web/WebRTC now, phone later
│   ├── __init__.py             # Re-exports the transport API used by the server
│   └── web.py                  # SmallWebRTC transport + SWAP SEAM (mobile/phone guidance)
├── server/                     # FastAPI app gluing transport+voice+core for THIS project
│   ├── __init__.py             # Exposes app / create_app
│   ├── app.py                  # FastAPI: /health, /api/offer, /api/booking_requests, per-call wiring + logs
│   ├── logging_setup.py        # loguru DEBUG file sink in logs/ + stdlib intercept (secret-safe)
│   └── __main__.py             # `python -m server` entrypoint (uvicorn, HOST/PORT env)
├── testclient/                 # Throwaway local test frontend — NOT part of product
│   ├── index.html              # Single-page WebRTC test client (no build, no framework)
│   └── README.md               # States this folder is test-only; how to run it
├── tools/                      # Dev tools (app-level, not in publishable core)
│   ├── __init__.py             # Tools package marker
│   └── latency_probe.py        # `python -m tools.latency_probe` real-API latency
├── scripts/                    # Standalone ops scripts (app-level)
│   ├── tunnel.py               # One command: starts the server + opens an ngrok tunnel
│   └── seed_test_request.py    # Seed the Md Aabid Hussain BookingRequest for Phase-2 testing
├── tests/                      # Test suite
│   ├── __init__.py             # Tests package marker
│   ├── test_booking_request_persistence.py  # BookingRequest repo (CRUD, latest_active, outcome)
│   ├── test_booking_request_service.py      # BookingRequestService rules + transitions + seed
│   ├── test_proxy_agent_dispatcher.py       # Persona + tool schemas + dispatcher behaviour
│   ├── test_voice_pipeline.py               # Pipeline isolation (stub transport)
│   ├── test_server.py                       # Boot, WebRTC negotiation, /api/booking_requests
│   ├── test_proxy_call_end_to_end.py        # Full proxy call -> on-disk persistence
│   ├── test_db_backends.py                  # Postgres/Supabase URL normalize + engine hardening
│   ├── test_logging_setup.py                # logging_setup wiring contract
│   └── test_layer_isolation.py              # Subprocess guardrail: deps point inward only
├── logs/                       # Runtime call-flow logs (git-ignored, auto-created)
├── .venv/                      # Local virtual environment (git-ignored)
├── .env.example                # Template for secrets; copy to .env and fill in
├── .gitignore                  # Ignores .env, *.db, .venv, logs, caches, build artifacts
├── pyproject.toml              # Packages ONLY `core` as the microservice boundary; pytest config
├── requirements.txt            # Dependency list (core + voice + web + dev tools)
├── prompt.md                   # The ordered build plan (one prompt per session)
├── repo_structure.md           # THIS file — live map of the repo
├── instruction.md              # Standing brief for future sessions (rules, gotchas, commands)
├── script.md                   # Live-call test runbook: edge cases to exercise as the receptionist
└── README.md                   # What this is, accounts needed, setup, reuse example
```

## Layer dependency rule (must always hold)

```
testclient/  →  server/  →  transport/ ─┐
                                        ├─→  voice/  →  core/
                              (core/ depends on NOTHING above it)
```

`core/` never imports `voice/`, `transport/`, `server/`, or `testclient/`.
`transport/` imports Pipecat only — never `core/`, `voice/`, or `server/`.
Enforced by `tests/test_layer_isolation.py` (runs each check in a fresh
subprocess so test-ordering can't false-fail it).
