# VoiceStream — Build Prompts

This file is the **build plan**. Each numbered section below is a self-contained prompt
to give to a fresh Claude session, **one at a time, in order**. Do not run more than
one prompt per session unless told to. Each prompt ends with a "Definition of done"
that must be satisfied before moving to the next.

---

## Project summary (read before any prompt)

VoiceStream is a **voice AI customer-support agent that books appointments**.
It is being built as a **reusable microservice**: the core (agent brain + booking
logic + DB) must be usable from any other project with zero coupling to how audio
is transported or to this specific test frontend.

**Decided stack (do not change without explicit instruction):**
- Language: **Python**
- Voice pipeline framework: **Pipecat**
- STT: **Deepgram**
- LLM: **Groq** (fast, free tier)
- TTS: **Deepgram Aura**
- DB: **SQLite** locally now, behind a repository abstraction so Postgres is a drop-in later
- Transport now: **web-to-web (WebRTC, browser)**
- Transport later: mobile app (same WebRTC) and real phone calls (Twilio) — must be a
  one-file swap, never a rewrite
- Booking: LLM **function/tool calling** into the booking service, persisted to the DB

**Hard architectural rule (applies to every prompt):**
The codebase is split into three concentric layers. Dependencies only point inward:

```
[ test frontend ]  →  [ web transport layer ]  →  [ REUSABLE CORE ]
   (this project        (this project only)        (agent brain + booking
    only, throwaway)                                 + db; no voice transport,
                                                      no web, no Pipecat-only
                                                      assumptions leaking out)
```

- The **core** must never import the web layer, the transport, or Pipecat-transport code.
- The **core** must be importable as a normal Python package by an unrelated project.
- The **test frontend** is explicitly throwaway and must be isolated in its own folder.

---

## Global rules — apply these in EVERY prompt

1. **Maintain `repo_structure.md`.** Whenever you create, move, or delete a file or
   folder, update `repo_structure.md` in the same change. It contains the full tree
   plus a **one-line description per file** of what it does. Never leave it stale.
2. **Maintain `instruction.md`.** This is the standing brief for future Claude sessions:
   conventions, gotchas, "always make sure of X", how to run things, what NOT to do.
   Append/refine it whenever you learn something a future session must know.
3. **Respect the layer rule** above. If a task would make the core depend on the web
   or transport layer, stop and redesign — the core stays clean.
4. **Small, verifiable steps.** Finish only what the current prompt asks. Do not
   scaffold future prompts' work early.
5. **Every prompt ends green.** Run the relevant check (tests / lint / app boots)
   and report the actual result before declaring done.
6. **Always work inside the virtual environment.** At the start of every session,
   activate `.venv` before running Python, tests, or installing anything. Never
   install packages globally. (Prompt 0 creates the venv; activation steps live
   in `instruction.md`.)
7. **Secrets via `.env` only.** Never hardcode keys. Keep `.env` git-ignored and
   `.env.example` updated with every new variable.
8. **Reusability check.** Before finishing any core-touching prompt, ask: "Could
   another project `pip install`/import this core and use it without dragging in
   the web layer or this frontend?" If not, fix it.

---

## Prompt 0 — Project foundation & guardrails

**Goal:** Create the skeleton, the bookkeeping docs, and the dependency setup. No
business logic yet.

**Tasks:**
- **Set up a Python virtual environment first**, before anything else:
  - Create it: `python -m venv .venv`
  - Activate it (Windows PowerShell): `.\.venv\Scripts\Activate.ps1`
    (Windows cmd: `.\.venv\Scripts\activate.bat` · macOS/Linux: `source .venv/bin/activate`)
  - Upgrade pip, then install deps into the venv (never globally).
  - Ensure `.venv/` is git-ignored.
  - Record the exact create + activate + install commands in `instruction.md` and
    `README.md` so every future session and teammate starts inside the venv.
- Create the project structure reflecting the 3-layer architecture, e.g.:
  ```
  voicestream/
  ├── core/                  # REUSABLE microservice core (no web, no transport)
  │   ├── __init__.py
  │   ├── booking/           # booking domain + persistence
  │   ├── agent/             # agent brain: prompts, tool schemas, conversation policy
  │   └── config.py          # core config loaded from env (no web concerns)
  ├── voice/                 # Pipecat pipeline assembly (uses core; transport-agnostic)
  ├── transport/             # the ONE swappable layer (web/webrtc now)
  ├── server/                # FastAPI app wiring transport + voice for THIS project
  ├── testclient/            # throwaway test frontend (web-to-web)
  ├── tests/
  ├── .env.example
  ├── .gitignore
  ├── pyproject.toml         # core packaged as an importable distribution
  ├── requirements.txt
  ├── repo_structure.md
  ├── instruction.md
  └── README.md
  ```
- Set up `pyproject.toml` so `core/` is an installable package (the microservice
  boundary). Web/transport/testclient are app-level, not part of the published core.
- Create `.gitignore` (ignore `.env`, `*.db`, `__pycache__`, venv, etc.).
- Create `.env.example` with placeholders: `GROQ_API_KEY`, `DEEPGRAM_API_KEY`,
  `DATABASE_URL` (default `sqlite:///voicestream.db`).
- Write the **initial `repo_structure.md`** (full tree + one-liners).
- Write the **initial `instruction.md`**: the layer rule, stack, "update
  repo_structure.md on every file change", how to set up the venv, how to run tests.
- Write a short `README.md`: what this is, the free accounts needed (Groq, Deepgram),
  how to fill `.env`.

**Definition of done:** Folder structure exists, `core` is importable as a package,
`repo_structure.md` and `instruction.md` exist and are accurate, no business logic yet.

---

## Prompt 1 — Booking persistence layer (core)

**Goal:** The database layer of the reusable core. Pure data, no voice/web.

**Tasks:**
- In `core/booking/`, define DB models with SQLAlchemy: at minimum `Appointment`
  (id, customer_name, customer_phone/email, start_time, end_time, status, notes,
  created_at) and whatever availability concept you choose (e.g. business hours
  or pre-generated slots).
- Implement a **repository abstraction** (interface + SQLite-backed implementation)
  so the DB engine can be swapped (Postgres later) without touching callers.
- Auto-create the SQLite schema on first use; DB path comes from `DATABASE_URL`.
- Write unit tests against an in-memory/temp SQLite DB.
- Update `repo_structure.md` and `instruction.md`.

**Definition of done:** `pytest` green for the persistence layer; no import of web/
transport/Pipecat anywhere in `core/booking/`.

---

## Prompt 2 — Booking domain service (core)

**Goal:** The business logic, transport-agnostic and independently testable.

**Tasks:**
- In `core/booking/`, implement a `BookingService` with clean methods, e.g.:
  `find_available_slots(date_range)`, `book_appointment(...)`,
  `reschedule_appointment(...)`, `cancel_appointment(...)`.
- Enforce rules: no double-booking, no past times, validate within business hours,
  sane input validation; return structured results (not strings) suitable for any caller.
- Thorough unit tests for happy paths and edge cases (double-book, past date, etc.).
- Update `repo_structure.md` and `instruction.md`.

**Definition of done:** `BookingService` fully unit-tested and usable by importing
`core` alone, with no voice or web dependency.

---

## Prompt 3 — Agent brain (core)

**Goal:** The conversation intelligence: persona, prompts, and the tool schemas that
let an LLM drive the booking service. Still no Pipecat, no web.

**Tasks:**
- In `core/agent/`, write the system prompt / persona (concise, natural, asks for
  the details needed to book, confirms before committing).
- Define LLM **tool/function schemas** that map 1:1 to `BookingService` methods,
  plus the dispatcher that executes a tool call and returns a structured result.
- Keep this LLM-provider-agnostic at the schema level (OpenAI-style function specs
  work with Groq). No network calls here — just schemas + dispatch logic.
- Unit-test the dispatcher (given a tool call → correct BookingService call → result).
- Update `repo_structure.md` and `instruction.md`.

**Definition of done:** The core now exposes "give me an LLM tool call, I'll execute
booking and return a result" with tests, importable standalone.

---

## Prompt 4 — Voice pipeline assembly

**Goal:** Wire Pipecat: Deepgram STT → Groq LLM (with core's tools) → Deepgram Aura
TTS. Transport-agnostic — no web/Twilio specifics here.

**Tasks:**
- In `voice/`, build a pipeline factory that assembles the Pipecat pipeline using
  the core's prompt + tool schemas + tool dispatcher.
- Configure for low latency (streaming STT, interruptions/barge-in enabled).
- Accept an injected transport (do not construct the web transport here).
- Document required env keys; fail fast with a clear message if missing.
- Update `repo_structure.md` and `instruction.md`.

**Definition of done:** Pipeline can be constructed in isolation given a mock/abstract
transport; no web framework imported in `voice/`.

---

## Prompt 5 — Web transport + server (this project)

**Goal:** The single swappable transport (WebRTC web) and the FastAPI server that
serves it. This is the only layer that changes for mobile/phone later.

**Tasks:**
- In `transport/`, implement the web/WebRTC transport adapter for Pipecat. Keep the
  Twilio/phone path as a clearly-marked TODO seam (one file to add later).
- In `server/`, a FastAPI app: session/negotiation endpoint(s), health check, and
  wiring transport → voice pipeline → core.
- Document in `instruction.md` exactly which file to swap for mobile vs phone.
- Update `repo_structure.md`.

**Definition of done:** Server boots locally, a WebRTC session can be negotiated
(verified without full audio if needed), core/voice untouched by transport specifics.

---

## Prompt 6 — Throwaway test frontend (this project only)

**Goal:** Minimal browser page to actually talk to the agent for local testing.

**Tasks:**
- In `testclient/`, a single minimal HTML+JS page: a "Talk" button that opens the
  mic, connects via WebRTC to the server, plays back agent audio. No build step,
  no framework. Clearly comment that this is **test-only, not part of the product**.
- Add run instructions to `README.md` and `instruction.md`.
- Update `repo_structure.md`.

**Definition of done:** Open page locally → click Talk → speak → agent responds.
Frontend lives only in `testclient/` and nothing in `core/` depends on it.

---

## Prompt 7 — End-to-end booking test & hardening

**Tasks:**
- Manual + scripted end-to-end test: spoken conversation results in a row in the
  SQLite DB with correct data; verify reschedule/cancel by voice.
- Measure and note round-trip latency; tune for minimal delay (streaming, model
  choices, interruption handling) and record findings in `instruction.md`.
- Add a short "How another project consumes the core" section to `README.md`
  (import path, the BookingService/agent entry points, required env).
- Final pass on `repo_structure.md` accuracy.

**Definition of done:** A real voice conversation books a real DB appointment with
low perceived latency; docs let a teammate reuse the core elsewhere.

---

## After all prompts

Future changes must still obey the Global Rules: keep the core clean and reusable,
keep `repo_structure.md` and `instruction.md` current, transport stays a one-file swap.

---

# Migration — Phase 2 (proxy-caller agent)

The product flips. The agent is no longer a receptionist taking calls into our
business. It is now a **proxy caller** that calls a hospital on behalf of a
user and books an appointment for them, **speaking as if it is the user**
(name: **Md Aabid Hussain** for test data). The hospital receptionist is the
other party (human or bot); the agent answers their questions using the
user's saved info, then records the outcome.

Phase 1 (collecting the user's info) is handled by **a separate microservice**
that writes `BookingRequest` rows into our core. **Only Phase 2 is built here.**
Use seeded test data for now.

All previous Global Rules still apply (venv, layer rule, `repo_structure.md`
and `instruction.md` upkeep, secrets via `.env`, reusability check). Two
extras specific to this migration:

- **Each migration prompt ends green.** Old tests stay green until the prompt
  that intentionally retires the code they cover; the same prompt replaces
  them with new tests.
- **`core` must stay importable by other projects.** The Phase-1 intake
  microservice will `pip install` this core and call
  `BookingRequestService` to save requests — so the new service is part of
  the published microservice surface, not the server/transport layer.

The legacy receptionist domain (`Appointment`, `BookingService`, the
"appointment in our calendar" tools and persona) is being **replaced**, not
extended. It is removed entirely in Prompt 11 once nothing depends on it.

---

## Prompt 8 — New domain: `BookingRequest` + persistence + service

**Goal:** Land the new core domain (data model + business service) alongside
the legacy receptionist code without disturbing it. End green.

**Tasks:**
- In `core/booking/` add a new model `BookingRequest` (single table; outcome
  fields embedded for now). Minimum columns: `id`, `full_name`,
  `date_of_birth` (date), `phone`, `email` (nullable), `address` (nullable),
  `insurance_provider` (nullable), `insurance_member_id` (nullable),
  `is_new_patient` (bool, default true), `appointment_reason` (text),
  `preferred_date_window_start` / `_end` (date, nullable),
  `preferred_time_of_day` (str: morning/afternoon/evening/any),
  `preferred_doctor` (nullable), `department` (nullable), `notes` (nullable),
  `target_hospital_name` (nullable), `status` (enum:
  `PENDING / IN_PROGRESS / CONFIRMED / DECLINED / FAILED`),
  `outcome_scheduled_time` (UTC datetime, nullable),
  `outcome_confirmation_number` (nullable), `outcome_notes` (nullable),
  `created_at`, `updated_at`. Use the existing `UtcDateTime` decorator and
  generic SQLAlchemy types (must work on Postgres/Supabase too).
- Add `BookingRequestRepository` (abstract) + `SqlAlchemyBookingRequestRepository`
  with: `add(...)`, `get(id)`, `list_pending()`, `update_status(id, status)`,
  `record_outcome(id, *, scheduled_time?, confirmation_number?, notes?)`,
  `latest_active()` (the request a fresh call should bind to). Data-access
  only — no business rules.
- Add `BookingRequestService` (`core.booking.proxy_service` or similar — do
  NOT collide with the legacy `BookingService` symbol):
  - `create(...)` — for the Phase-1 microservice or seed script
  - `get(id)` / `latest_active()`
  - `mark_in_progress(id)` (called when the outbound session starts)
  - `record_confirmed(id, scheduled_time, confirmation_number?, notes?)`
  - `record_declined(id, reason)`
  - `record_followup(id, notes)`
  - Structured `Result` / view types, matching the existing pattern (no ORM
    leakage to callers).
- Add a tiny seed helper `BookingRequestService.seed_test_request()` that
  inserts the **Md Aabid Hussain** stub (DOB, phone, dummy insurance,
  reason "general health checkup", preferred window next 7 days afternoon,
  status PENDING). Idempotent (don't duplicate on re-run).
- Re-export the new public API from `core.booking` alongside the legacy one.
- Unit tests (`tests/test_booking_request_persistence.py` +
  `tests/test_booking_request_service.py`) — happy paths + transitions
  (mark_in_progress -> CONFIRMED, DECLINED, FAILED only valid from
  IN_PROGRESS/PENDING; record_confirmed sets scheduled_time + status).

**Definition of done:** new tests green; the entire pre-existing suite
still green; `core/booking/` has no import of voice/transport/server;
seed helper inserts the Md Aabid Hussain row and is idempotent.

---

## Prompt 9 — New agent persona + tools + dispatcher (REPLACES the old)

**Goal:** Replace the receptionist agent with the impersonating proxy caller.
The user explicitly chose to speak as the user directly (not disclose AI);
encode that in the persona honestly but neutrally.

**Tasks:**
- Rewrite `core/agent/prompts.py` `build_system_prompt(...)` to a new persona:
  - The agent is making an OUTBOUND call to a hospital on behalf of the user.
  - It speaks AS the user (uses their full name in first person).
  - Tone: calm, polite, concise; spoken-style (no markdown, no lists).
  - It answers receptionist questions using ONLY the data tools return —
    never invents facts (no fake insurance numbers, no fake DOB).
  - Reads back the proposed appointment time, confirms it, then calls
    `record_appointment_confirmed`. If declined / unclear, calls the
    appropriate `record_*` tool before ending the call.
  - Include the current UTC time so the agent can resolve relative times
    the receptionist proposes.
- Replace `core/agent/tools.py` `TOOL_SCHEMAS` with the new toolset
  (OpenAI-style schemas, no SDK imports):
  - `get_caller_info` — returns name, DOB, phone, email, address, insurance,
    patient_type. No arguments.
  - `get_appointment_request` — returns reason, preferred date window,
    preferred time of day, preferred doctor, department, notes,
    target_hospital_name. No arguments.
  - `record_appointment_confirmed(scheduled_time, confirmation_number?,
    notes?)`
  - `record_appointment_declined(reason)`
  - `record_appointment_followup(notes)`
- Rewrite `core/agent/dispatcher.py` `ToolDispatcher` to be constructed with
  a `BookingRequestService` **and a bound `booking_request_id`** (set at
  session start by the server). Tools then operate on the bound request —
  the LLM never passes ids around. Keep the "never raises; always returns
  a JSON-serialisable dict; structured `{ok, error, message, ...}` on
  failures" contract.
- Delete the old agent tests; write `tests/test_proxy_agent_dispatcher.py`
  covering: tool schemas shape, `get_caller_info` and `get_appointment_request`
  return the bound request's data, `record_appointment_confirmed` updates
  the row to CONFIRMED with scheduled_time + confirmation #, declined/followup
  set the right status, bad args / unknown tool produce structured errors,
  prompt builder injects business name (now: hospital target name) + UTC
  time + the caller's name into the system prompt.

**Definition of done:** new agent suite green; existing booking and
infrastructure tests still green; `core.agent` still imports nothing from
voice/transport/server; the layer-isolation guardrail still passes.

---

## Prompt 10 — Voice + server wired to the new agent; intake HTTP + seed script

**Goal:** Make a real call use the new persona/tools, and give Phase 1
something to post into (so the future intake microservice can integrate).

**Tasks:**
- `voice/pipeline.py` `build_pipeline_task(...)` accepts a
  `booking_request_id: int` (required for proxy calls), constructs the
  `ToolDispatcher` bound to that request, and feeds the persona's
  `caller_name` + `target_hospital_name` into the system prompt. Keep the
  transport injected (no transport coupling).
- `server/app.py` on each new WebRTC connection:
  - resolve the active `BookingRequest` via
    `BookingRequestService.latest_active()` (or 404 if none); call
    `mark_in_progress(id)`; build the pipeline with that id.
  - Per-call logs include the booking-request id alongside `pc_id`.
- Add a JSON endpoint `POST /api/booking_requests` on the server (NOT in
  core/transport): accepts the BookingRequest payload, calls
  `BookingRequestService.create(...)`, returns `{id, status}`. This is the
  hand-off seam for the Phase-1 microservice. Validate input minimally
  (full_name + appointment_reason + DOB required); return 400 with a clear
  message on bad input.
- Add `scripts/seed_test_request.py` that calls
  `BookingRequestService().seed_test_request()` so a developer can immediately
  test Phase 2 without the intake microservice. Print the row id + key
  fields.
- Update test client copy briefly so it's clear this session is the agent
  CALLING a hospital (so the human tester knows to play the receptionist).
- Update `tests/test_voice_pipeline.py` and `tests/test_server.py`:
  pipeline build now requires a booking_request_id; server tests seed a
  request in the test DB before negotiating; add a test for
  `POST /api/booking_requests` (happy path + 400 on missing fields).

**Definition of done:** full suite green. `python ./scripts/seed_test_request.py`
inserts the Md Aabid Hussain row. `python -m server` (or `tunnel.py`) starts
a call that binds to that row; logs show `[call <pc_id> req=<n>]`.

---

## Prompt 11 — End-to-end proxy call test + retire the legacy domain

**Goal:** Prove the proxy-caller flow end-to-end and delete the old
receptionist domain so the codebase reflects the real product.

**Tasks:**
- New scripted E2E test `tests/test_proxy_call_end_to_end.py`: seed the
  Md Aabid Hussain request, build a `ToolDispatcher` bound to it, simulate
  a receptionist conversation by feeding the EXACT tool calls a Groq LLM
  would emit (call `get_caller_info`, `get_appointment_request`, then
  `record_appointment_confirmed` with a slot the receptionist "offered"),
  then re-open a FRESH repository against the same on-disk SQLite file and
  assert the row is CONFIRMED with the right scheduled_time and
  confirmation number — proving real persistence. Also cover a DECLINED
  and a FOLLOWUP path.
- Update the latency-probe script's sample LLM question so it represents
  the new flow (a receptionist question the agent should answer using
  `get_caller_info`).
- **Retire the legacy receptionist domain:** delete
  `core/booking/service.py` (the old `BookingService`),
  `core/booking/models.py::Appointment` + the `BusinessHours` model,
  the appointment/business-hours repository classes,
  `tests/test_booking_persistence.py`, `tests/test_booking_service.py`,
  `tests/test_booking_bypass.py`, `scripts/seed_hours.py`, and the
  `BYPASS_AVAILABILITY` env flag from `core/config.py` and `.env.example`.
  (`tests/test_agent_dispatcher.py` and
  `tests/test_end_to_end_booking.py` were already deleted in Prompt 9
  when the old agent was replaced.)
  Remove their re-exports from `core/booking/__init__.py`. Anything that
  imported them must move to the new API or be deleted (the tunnel /
  manual runbook references in `instruction.md` and `README.md` get
  rewritten for the new flow).
- Final reusability pass: `README.md` "How another project consumes the
  core" rewritten — entry points are now `BookingRequestService` +
  the proxy `ToolDispatcher` / `TOOL_SCHEMAS` / `build_system_prompt`.
  Phase-1 microservice usage example.
- Final accuracy pass on `repo_structure.md` (verify against `git ls-files`
  or `find`).

**Definition of done:** full suite green; only the new tests cover booking;
no reference to `Appointment` / `BookingService` / business hours remains
outside historical commit log; `core` still importable standalone (layer
guardrail passes); the README example shows another project consuming
`BookingRequestService` to write a request, which our server then dials.
