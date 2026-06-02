# CLAUDE.md — standing brief for any Claude session in this repo

Read this first. Then read [instruction.md](instruction.md) (build conventions
and the layer rule) and [repo_structure.md](repo_structure.md) (live file map).
Together these three files are the source of truth for everything not
obvious from the code.

If you're updating something a future session needs to know about, edit one
of these three files in the same PR — do not write a new doc.

---

## What this project is

**CallBot / VoiceStream is a voice-AI proxy caller.** It calls a hospital
on a user's behalf and books an appointment for them, speaking AS the
user in the first person. Each call is bound to one `BookingRequest`
row in the DB; outcomes (`confirmed` / `declined` / `followup`) land
back on that row.

A separate Phase-1 intake microservice (not in this repo) writes
`BookingRequest` rows. This repo builds Phase 2 — the outbound call.

**Feature 4 (2026 update).** AIVA is now that Phase-1 intake. `BookingRequest`
is general-purpose (`appointment_type`: medical | meeting | service | other;
DOB/insurance required only for `medical`) and carries AIVA-owned columns
(`caller_user_id`, `aiva_chat_id`, `scheduled_call_at`, `contact_info`,
`target_phone`, `call_triggered_at`, `outcome_notified_at`). The booking DB is
SHARED with AIVA (same Supabase Postgres via `DATABASE_URL`): fresh DB →
create_all; existing table → `scripts/upgrade_booking_schema.sql`. Phase-2 voice
changes are DONE: `/api/offer` accepts an optional `request_id` (binds that row;
falls back to `latest_active()`) and enforces ONE concurrent call (409 otherwise);
the agent persona + `get_caller_info` adapt to `appointment_type` (clinical details
only for `medical`); outcomes persist back to the SAME shared row via the existing
`record_*` flow (now that `DATABASE_URL` points at Supabase).

The `core/` package is designed to be installable on its own with only
`SQLAlchemy` + `python-dotenv`, so any other Python service can import
the booking domain without pulling in Pipecat / FastAPI / WebRTC.

---

## The one rule that must never break

Dependencies point INWARD only:

```
testclient/  →  server/  →  transport/  →  voice/  →  core/
core/ imports NOTHING from voice/, transport/, server/, testclient/.
transport/ imports Pipecat only — never core/, voice/, or server/.
```

Enforced in `tests/test_layer_isolation.py` (runs each check in a fresh
subprocess so test ordering can't false-fail it). Before finishing any
`core/`-touching change, ask: *"Could an unrelated project import `core`
and use it without dragging in the web layer?"* If not, redesign.

---

## Stack (pinned; do not change without explicit instruction)

- Python 3.11 (venv-managed; AL2023 default `python3` is 3.9 — too old)
- Pipecat **1.2.1** (voice pipeline)
- Groq (LLM, default `llama-3.3-70b-versatile`)
- Deepgram (STT `nova-3`, TTS `aura-2-thalia-en`)
- SQLite by default; Supabase/Postgres is a config-only swap via `DATABASE_URL`
- WebRTC web transport (`SmallWebRTCTransport` via Pipecat)
- Self-hosted **coturn** on the same VPS for TURN relay
- FastAPI + uvicorn (server layer)
- nginx (reverse proxy, TLS termination)
- Let's Encrypt (auto-renew via `certbot-renew.timer`)

---

## Current production deployment

**VPS:** AWS EC2 `t3.micro` in `eu-north-1` (Stockholm). Public IP
`13.60.193.150`, hostname `callbot.duckdns.org`. 2 vCPU, 916 MB RAM,
8 GB disk, 1 GB swapfile added at deploy time. Amazon Linux 2023.

**Live URL:** https://callbot.duckdns.org/

**SSH:**
```bash
ssh -i ~/Downloads/aabid.pem ec2-user@13.60.193.150
```

**Code lives at:** `/home/ec2-user/CallBot/` (a git clone of
`github.com/aabid2947/CallBot`). `.env` is in that directory; never
committed.

### Services on the box (all systemd)

| Service              | Role                                                    | Limits / notes                                                                  |
| -------------------- | ------------------------------------------------------- | ------------------------------------------------------------------------------- |
| `callbot.service`    | The Python server (`python -m server`)                  | CPUQuota=160%, MemoryHigh=550M, MemoryMax=700M, TasksMax=200                    |
| `nginx.service`      | TLS termination + reverse proxy to `127.0.0.1:8000`     | Standard nginx; config at `/etc/nginx/conf.d/callbot.conf`                       |
| `coturn.service`     | Self-hosted TURN relay                                  | Config at `/etc/coturn/turnserver.conf` (NOT `/etc/turnserver.conf` — that path is wrong on AL2023) |
| `guardrail.service`  | Resource watchdog for free-tier safety                  | Polices SHELL-launched processes only; exempts everything in `system.slice/*.service` via cgroup check |
| `certbot-renew.timer`| Auto-renews the Let's Encrypt cert                       | Default schedule (twice a day, only renews if < 30 days left)                   |

### AWS Security Group inbound rules (required)

| Port range  | Protocol | Purpose                          |
| ----------- | -------- | -------------------------------- |
| 22          | TCP      | SSH                              |
| 80          | TCP      | HTTP (Let's Encrypt + redirect)  |
| 443         | TCP      | HTTPS (the site)                 |
| 3478        | UDP      | TURN UDP                         |
| 3478        | TCP      | TURN TCP                         |
| 5349        | TCP      | TURNS (TURN over TLS)            |
| 49152-65535 | UDP      | coturn relay range               |

All sources `0.0.0.0/0`. Missing any of the bottom four = WebRTC ICE never completes for cross-network callers.

### How the box was set up

`scripts/deploy_vps.sh` is idempotent and reads everything from `.env`.
Run as `ec2-user` from the repo root. Phases: dnf packages → load .env →
DNS check (DuckDNS one-shot update if token present) → swap → venv +
deps → seed → systemd unit → nginx → Let's Encrypt → guardrail. Re-run
any time to apply changes; nothing is destructive.

`scripts/install_coturn.sh` adds EPEL, installs coturn, generates
long-lived static credentials, writes the right config, opens
`/etc/coturn/turnserver.conf`, enables the service, and updates `.env`
with the local TURN URL. Re-runnable.

`scripts/guardrail.sh` is a bash watchdog with `install / run / status /
uninstall` modes. The cgroup check in `is_protected()` is what keeps it
from fighting `callbot.service`.

### `.env` required keys (runtime)

```
GROQ_API_KEY=
DEEPGRAM_API_KEY=
TURN_URLS=turn:callbot.duckdns.org:3478?transport=udp,turn:callbot.duckdns.org:3478?transport=tcp,turns:callbot.duckdns.org:5349?transport=tcp
TURN_USERNAME=callbot
TURN_CREDENTIAL=<random 32 chars; canonical copy at /etc/coturn/callbot.cred chmod 600>
```

Optional deploy-time keys:
```
DUCKDNS_TOKEN=   # only used by deploy_vps.sh for the one-shot DNS update
LE_EMAIL=        # required for certbot to issue the TLS cert
```

Optional tuning (have sane defaults):
```
LLM_MODEL=, STT_MODEL=, TTS_VOICE=, BUSINESS_NAME=, DATABASE_URL=, HOST=, PORT=
```

### Common ops commands (also in `commands.md`)

```bash
# Redeploy after code change
ssh -i ~/Downloads/aabid.pem ec2-user@13.60.193.150 \
    'cd ~/CallBot && git pull && sudo systemctl restart callbot'

# Live logs
sudo journalctl -u callbot -f                              # service level
tail -F ~/CallBot/logs/voicestream.log                     # full DEBUG, every transcript + tool call

# Pull the rich log to laptop for analysis
scp -i ~/Downloads/aabid.pem ec2-user@13.60.193.150:~/CallBot/logs/voicestream.log .

# Health
curl -I https://callbot.duckdns.org/health

# Relay-only self-test (strictest pre-test before remote testers)
https://callbot.duckdns.org/?relay

# Inspect a booking row
~/CallBot/.venv/bin/python -c "from core.booking import init_db, SqlAlchemyBookingRequestRepository as R; init_db(); print(R().get(4))"
```

---

## Bugs already fixed (so future you doesn't reintroduce)

1. **AL2023 lacks `epel-release` because no `redhat-release`.** Fix:
   write `/etc/yum.repos.d/epel.repo` directly with the EPEL 9 metalink.
   Already handled in `scripts/install_coturn.sh`.
2. **coturn config path on AL2023 is `/etc/coturn/turnserver.conf`,
   NOT `/etc/turnserver.conf`.** The systemd unit hardcodes that path.
   Writing to the wrong path produces a running coturn that ignores
   your config (auto-discovers everything, looks fine in logs, but
   doesn't honor `external-ip` / `lt-cred-mech` / auth). Always use
   the variable `$CONF_FILE` in the install script.
3. **AL2023 has no cron by default.** Either install `cronie` first or
   skip cron entirely. The DuckDNS auto-update cron was removed from
   `deploy_vps.sh` because EC2 IPs don't change unless you stop/start
   the instance — and the cost of keeping the instance running 24/7
   is zero on free tier.
4. **Pipecat 1.2.1 pulls in `opencv-python` which needs X11 libs at
   import time.** Servers don't have X11. Fix: either install
   `libxcb libX11 libSM libICE libXext libXrender mesa-libGL` from dnf,
   OR pip-swap to `opencv-python-headless`. The deploy script tries the
   pip swap, but if it doesn't stick (Pipecat re-installs the GUI build
   on next `pip install -e .[web]`), the dnf libs are the fallback.
5. **Llama 3.3 on Groq occasionally emits tool calls as PLAIN TEXT
   inside `content` instead of as structured `tool_calls`.** The TTS
   reads the JSON aloud ("function record appointment confirmed
   scheduled time two zero two six dash zero five dash three one T...")
   and the call freezes because the actual tool was never invoked.
   **FIXED (2026-06-03, voice_flow_problem.md V1):** the escalation is now
   implemented — `voice/tool_call_sanitizer.py` adds a `ToolCallLeakSanitizer`
   FrameProcessor between the LLM and TTS that buffers each assistant
   response, detects leaked calls via `core/agent/tool_text.py`
   `extract_leaked_tool_calls()` (handles `<function=NAME>{json}</function>`,
   `<function(NAME {json})`, and bare `NAME({json})`), STRIPS them from the
   spoken text, and fires the REAL tool (`end_call` -> EndTaskFrame upstream,
   same as the structured path; `record_*`/`get_*` -> the bound
   `ToolDispatcher`). Wired in `voice/pipeline.py`. The prompt still forbids
   leaking (now with a blunt one-liner) as defense-in-depth. Note: the TEXT
   harness `tools/test_call_flows.py` has no Pipecat pipeline, so it still SEES
   the raw model leak — its `clean_speech` check measures the *model*, not the
   pipeline; the real fix is covered by `tests/test_tool_call_sanitizer.py`.
6. **Third-party TURN providers (openrelay, metered.ca free tier) drop
   ICE consent-freshness packets after ~30 seconds**, killing calls
   right when conversation starts. Self-hosted coturn on the same VPS
   removes the server-side network hop entirely and is the long-term
   fix. Don't switch back to a hosted TURN without confirming you've
   solved consent freshness.
7. **PowerShell mangles JSON escaping in `curl.exe -d '...'`.** Use a
   Python one-liner or a .py file in `tools/` instead. Already covered
   by `tools/groq_limits.py` for the live rate-limit probe.

---

## Conventions

- **Reading from env**: only via `core/config.py` for core settings.
  Web/HTTP settings belong in `server/`. Voice settings in
  `voice/config.py`.
- **Naming**: keep public APIs flat — re-export the surface from
  `core/booking/__init__.py` and `core/agent/__init__.py`. External
  callers should never need a sub-import.
- **Tests**: every change must end green with `pytest -q`. Report the
  actual output before declaring done.
- **Comments**: write none unless the why is non-obvious. Don't restate
  what the code says. Don't write multi-paragraph docstrings.
- **Don't add features beyond what the task requires.** No half-finished
  implementations. No "while I'm here" cleanups bundled into bug fixes.
- **Update [repo_structure.md](repo_structure.md)** whenever you add,
  move, or remove a file or folder, in the same change. One-line
  description per entry.
- **Update [instruction.md](instruction.md)** when you discover a
  gotcha or decision a future session must know.

---

## Session-norms the user has expressed

- They push to GitHub themselves. Do not `git push` proactively after
  edits unless they explicitly ask.
- They prefer terse, action-oriented responses with concrete commands
  over explanation. Sentence-per-sentence is too much; one tight
  message is right.
- They are time-pressured (Kenyan client demo). Lead with the fix, not
  the theory.
- They are Windows + PowerShell on the laptop, Linux on the VPS.
  Remember PowerShell's quirks (no `&&` chaining, JSON escape hell with
  `curl.exe`, single-vs-double quote semantics).
- Skip TodoWrite for single-step fixes — they consider it noise.

---

## What is NOT in scope for this repo

- **The Phase-1 intake microservice.** Lives in a separate repo.
  Writes `BookingRequest` rows here either via `core.booking` import
  or via `POST /api/booking_requests`.
- **Real phone (Twilio) transport.** Architecturally allowed — would
  be a sibling of `transport/web.py` named `transport/twilio.py`,
  exposing `create_phone_transport(...)` of the same shape. Server
  picks one at startup. Not yet implemented.
- **Multi-tenant / multi-call concurrency.** Server is sized for ONE
  concurrent caller. Two simultaneous calls on `t3.micro` will OOM.
  For real production scale, upgrade to `t3.small` (2 GB) minimum.
