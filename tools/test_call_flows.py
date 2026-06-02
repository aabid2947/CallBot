"""Text harness for the VoiceStream proxy-caller BRAIN (no audio / WebRTC / Pipecat).

Drives the REAL agent loop — build_system_prompt + TOOL_SCHEMAS + ToolDispatcher
+ Groq function-calling — against a scripted "receptionist", bound to an
IN-MEMORY booking (no live DB, no real phone call). You see exactly what the
agent would SAY and which tools it calls, and the script asserts the call-flow
behaviours we care about:

  * no fabricated time / no booking a slot the receptionist never proposed (#1)
  * confirm-before-record (a question is NOT a "yes")
  * end_call after recording an outcome — no re-greeting / re-pitch loop (#2)
  * no terminal flailing: at most ONE outcome, tool loop terminates (#3)
  * no tool syntax leaking into spoken text (the Llama-on-Groq plain-text bug)

This is the cheap, deterministic way to debug call behaviour and to verify the
prompt/tool/dispatcher fixes WITHOUT spinning up Deepgram + WebRTC. The agent
(Groq) is the stochastic part, so each scenario runs N times (`--repeat`) and we
report a PASS-RATE — a single green run proves nothing.

Run from voicestream/ (needs GROQ_API_KEY in env or .env; uses the production
Groq model). The booking is in-memory SQLite — nothing is written to Supabase and
no call is placed.
    python -m tools.test_call_flows                 # all scenarios x3
    python -m tools.test_call_flows --repeat 5
    python -m tools.test_call_flows --list
    python -m tools.test_call_flows --scenario C2   # just one (the fabrication repro)
    python -m tools.test_call_flows --gap 3         # seconds between receptionist lines

NOTE: top-level imports are stdlib only so the assertion engine can be unit-tested
offline (see _selftest_call_flows.py); httpx + core are imported lazily.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
from datetime import datetime, timezone

for _stream in (sys.stdout, sys.stderr):  # Windows cp1252 consoles choke on em-dashes
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[union-attr]
    except Exception:  # noqa: BLE001
        pass

OUT_DIR = os.path.dirname(os.path.abspath(__file__))
TRANSCRIPT_FILE = os.path.join(OUT_DIR, "call_test_transcript.md")
RESULTS_FILE = os.path.join(OUT_DIR, "call_test_results.json")

GROQ_URL = "https://api.groq.com/openai/v1/chat/completions"
DEFAULT_MODEL = os.getenv("LLM_MODEL", "llama-3.3-70b-versatile")

# Tool names (plain strings = the assertion engine needs no core import).
T_CALLER = "get_caller_info"
T_APPT = "get_appointment_request"
T_CONFIRM = "record_appointment_confirmed"
T_DECLINE = "record_appointment_declined"
T_FOLLOWUP = "record_appointment_followup"
T_END = "end_call"
_RECORD_TOOLS = (T_CONFIRM, T_DECLINE, T_FOLLOWUP)

# If any of these show up in SPOKEN text, the model leaked a tool call into speech
# (the Llama-on-Groq plain-text-tool-call bug) — the TTS would read it aloud.
_LEAK = ("function=", "<function", "record_appointment", "get_caller_info",
         "get_appointment_request", '"scheduled_time"', "end_call", "tool_call")


# --------------------------------------------------------------------------- #
# Assertion engine. A check is (label, fn); fn(call) -> bool. `call` is the dict
# produced by _run_call: agent_lines[], tools[{turn,name,...}], ended, overflow.
# --------------------------------------------------------------------------- #
def _tcount(call: dict, name: str) -> int:
    return sum(1 for t in call["tools"] if t["name"] == name)


def _records(call: dict) -> list:
    return [t for t in call["tools"] if t["name"] in _RECORD_TOOLS]


def _has(text: str, subs) -> bool:
    return any(s in text for s in subs)


def calls(name: str, label: str | None = None):
    return (label or f"calls {name}", lambda C: _tcount(C, name) > 0)


def never_calls(name: str, label: str | None = None):
    return (label or f"never calls {name}", lambda C: _tcount(C, name) == 0)


def at_most(name: str, n: int, label: str | None = None):
    return (label or f"{name} called <= {n}x", lambda C: _tcount(C, name) <= n)


def single_outcome(label: str | None = None):
    return (label or "records <=1 outcome (no flailing)", lambda C: len(_records(C)) <= 1)


def no_outcome(label: str | None = None):
    return (label or "records no outcome", lambda C: len(_records(C)) == 0)


def ends_call(label: str | None = None):
    return (label or "ends the call (end_call)", lambda C: bool(C["ended"]))


def does_not_end(label: str | None = None):
    return (label or "keeps the call open", lambda C: not C["ended"])


def loop_terminates(label: str | None = None):
    return (label or "tool loop terminates (no flailing overflow)", lambda C: not C["overflow"])


def clean_speech(label: str | None = None):
    return (label or "no tool syntax leaks into speech",
            lambda C: all(not _has(line, _LEAK) for line in C["agent_lines"]))


def confirm_not_before(k: int, label: str | None = None):
    """No record_appointment_confirmed fires before receptionist line k (1-indexed)."""
    return (label or f"does NOT confirm before receptionist line {k}",
            lambda C: all(t["turn"] >= k for t in C["tools"] if t["name"] == T_CONFIRM))


def ends_after_outcome(label: str | None = None):
    def fn(C):
        recs = _records(C)
        if not recs:
            return True
        ends = [t["turn"] for t in C["tools"] if t["name"] == T_END]
        return bool(ends) and max(ends) >= max(t["turn"] for t in recs)
    return (label or "ends the call after recording the outcome", fn)


# --------------------------------------------------------------------------- #
# Scenarios. The agent greets first (turn 0, no input — mirrors the pipeline's
# LLMRunFrame on connect); then each `receptionist` line is delivered in order.
# `booking` seeds the in-memory BookingRequest the agent represents.
# --------------------------------------------------------------------------- #
SCENARIOS: list[dict] = [
    {"id": "C1", "title": "medical happy path -> confirm",
     "expect": "Receptionist proposes a time; agent reads it back, records ONLY after the 'yes', "
               "then says goodbye + end_call.",
     "booking": {"appointment_type": "medical"},
     "receptionist": [
         "Hello, City Care Hospital, how can I help you?",
         "Sure. What is the appointment for?",
         "Okay. We have an opening next Tuesday at 3 PM. Does that work?",
         "Great, you're all booked. Your confirmation number is C C 7 7.",
         "See you then. Goodbye.",
     ],
     "checks": [calls(T_CONFIRM, "records the confirmation"),
                confirm_not_before(4),
                at_most(T_CONFIRM, 1),
                single_outcome(),
                ends_call(),
                ends_after_outcome(),
                clean_speech(),
                loop_terminates()]},

    {"id": "C2", "title": "no time proposed -> must NOT fabricate/book (#1)",
     "expect": "Receptionist never proposes a time (only asks questions). The agent must NOT invent a "
               "slot and must NOT call record_appointment_confirmed.",
     "booking": {"appointment_type": "medical"},
     "receptionist": [
         "Hello?",
         "Can I get your name, please?",
         "And what is this regarding?",
         "Let me check the calendar, one moment.",
         "Hmm, what is your date of birth?",
     ],
     "checks": [never_calls(T_CONFIRM, "does NOT book a fabricated time"),
                no_outcome(),
                does_not_end(),
                clean_speech(),
                loop_terminates()]},

    {"id": "C3", "title": "post-confirmation -> no flailing / no re-greet (#2,#3)",
     "expect": "After confirming, more chatter arrives. The agent must record exactly once and end the "
               "call — not retry record_* or restart the pitch.",
     "booking": {"appointment_type": "medical"},
     "receptionist": [
         "Hello, how can I help?",
         "What is it regarding?",
         "We have Monday at 10 AM. Does that work?",
         "Perfect, you're confirmed.",
         "Is there anything else I can help you with today?",
     ],
     "checks": [single_outcome(),
                at_most(T_CONFIRM, 1),
                ends_call(),
                ends_after_outcome(),
                clean_speech(),
                loop_terminates()]},

    {"id": "C4", "title": "cannot accommodate -> decline",
     "expect": "Receptionist can't help; agent records a decline once, says goodbye, end_call.",
     "booking": {"appointment_type": "medical"},
     "receptionist": [
         "Hello, City Care.",
         "I'm sorry, we are completely booked this month and not accepting new appointments.",
         "Apologies for the inconvenience. Goodbye.",
     ],
     "checks": [calls(T_DECLINE, "records a decline"),
                never_calls(T_CONFIRM, "does NOT confirm"),
                single_outcome(),
                ends_call(),
                ends_after_outcome(),
                clean_speech(),
                loop_terminates()]},

    {"id": "C5", "title": "non-medical -> don't invent clinical details",
     "expect": "Service booking has no DOB/insurance. When asked for DOB the agent must not fabricate one; "
               "it should still be able to book a proposed time.",
     "booking": {"appointment_type": "service", "reason": "tutoring session",
                 "target": "Bright Tutors"},
     "receptionist": [
         "Hello, Bright Tutors.",
         "Sure. What is your date of birth for our records?",
         "No problem. We have Thursday at 5 PM. Does that work?",
         "Booked. See you Thursday.",
     ],
     "checks": [clean_speech(),
                loop_terminates(),
                single_outcome(),
                ends_call()]},

    {"id": "C6", "title": "ambiguous confirmation -> a question is NOT a yes",
     "expect": "After the read-back the receptionist asks a QUESTION instead of confirming. The agent "
               "must NOT record the appointment (the AIVA/VoiceStream over-eager-confirm bug).",
     "booking": {"appointment_type": "medical"},
     "receptionist": [
         "Hello, how can I help?",
         "We have Friday at 2 PM. Does that work?",
         "Before I confirm, can you verify the patient's phone number for me?",
     ],
     "checks": [never_calls(T_CONFIRM, "does NOT confirm on a non-yes"),
                no_outcome(),
                clean_speech(),
                loop_terminates()]},
]


# --------------------------------------------------------------------------- #
# Live agent loop (lazy imports: httpx + core)
# --------------------------------------------------------------------------- #
def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _parse_env(path: str) -> dict:
    out: dict = {}
    if not os.path.exists(path):
        return out
    with open(path, encoding="utf-8", errors="replace") as fh:
        for line in fh:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, v = line.split("=", 1)
            out[k.strip()] = v.strip().strip('"').strip("'")
    return out


def collect_groq_keys() -> list[str]:
    """All Groq keys from voicestream/.env + backend/.env + live env, de-duped.

    Groq rate limits are PER-ORG, so rotating keys only adds headroom if they
    belong to different organizations — but rotating never hurts.
    """
    merged: dict = {}
    here = os.path.join(os.getcwd(), ".env")
    backend = os.path.normpath(os.path.join(os.getcwd(), "..", "backend", ".env"))
    for p in (here, backend):
        merged.update(_parse_env(p))
    merged.update({k: v for k, v in os.environ.items() if k.startswith("GROQ_API_KEY")})
    keys: list[str] = []
    for name in ["GROQ_API_KEY"] + [f"GROQ_API_KEY_{i}" for i in range(1, 9)]:
        v = (merged.get(name) or "").strip()
        if v and v not in keys:
            keys.append(v)
    return keys


def _retry_after_seconds(r) -> float:
    """How long Groq tells us to wait — from the retry-after header or the
    'try again in 6.5s' hint in the 429 body."""
    ra = r.headers.get("retry-after")
    if ra:
        try:
            return float(ra)
        except ValueError:
            pass
    m = re.search(r"try again in ([\d.]+)\s*s", r.text)
    return float(m.group(1)) if m else 0.0


def _groq_chat(messages: list, keys: list, model: str, temperature):
    """Call Groq, rotating across keys. Tries EVERY key before sleeping, so a
    key that's TPM-limited fails over to another org's budget instead of
    blocking. Only sleeps (honoring retry-after) when all keys are limited."""
    import httpx

    payload = {"model": model, "messages": messages, "tool_choice": "auto"}
    if temperature is not None:
        payload["temperature"] = temperature
    from core.agent import TOOL_SCHEMAS

    payload["tools"] = TOOL_SCHEMAS
    last = None
    for round_i in range(8):  # up to 8 wait-rounds before giving up
        max_retry = 0.0
        for key in keys:
            try:
                r = httpx.post(GROQ_URL, headers={"Authorization": f"Bearer {key}"},
                               json=payload, timeout=90)
            except Exception as exc:  # noqa: BLE001 - network blip, try next key
                last = f"network: {exc}"
                continue
            if r.status_code == 200:
                return r.json()["choices"][0]["message"]
            last = f"HTTP {r.status_code}: {r.text[:140]}"
            if r.status_code in (429, 500, 502, 503, 504, 520):
                max_retry = max(max_retry, _retry_after_seconds(r))
                continue  # try the next key before sleeping
            r.raise_for_status()
        wait = min(70.0, max_retry or (3.0 * (round_i + 1)))  # all keys limited -> wait it out
        time.sleep(wait)
    raise RuntimeError(f"Groq call failed after retries: {last}")


def _agent_turn(messages: list, dispatcher, keys: list, model: str, temperature,
                call: dict, rcpt_turn: int, max_rounds: int = 8) -> str:
    """Run one agent turn: call the LLM, execute any tool calls, repeat until it
    returns spoken text with no tool calls (or calls end_call). Mirrors the voice
    layer — end_call ends the turn instead of going to the dispatcher."""
    from core.agent import END_CALL

    spoken: list[str] = []
    for _ in range(max_rounds):
        msg = _groq_chat(messages, keys, model, temperature)
        messages.append(msg)
        content = (msg.get("content") or "").strip()
        if content:
            spoken.append(content)
        tool_calls = msg.get("tool_calls") or []
        if not tool_calls:
            break
        ended = False
        for tc in tool_calls:
            fn = tc.get("function", {})
            name = fn.get("name", "")
            try:
                args = json.loads(fn.get("arguments") or "{}")
            except Exception:  # noqa: BLE001
                args = {}
            if name == END_CALL:
                call["ended"] = True
                ended = True
                call["tools"].append({"turn": rcpt_turn, "name": name})
                messages.append({"role": "tool", "tool_call_id": tc.get("id"),
                                 "content": json.dumps({"ok": True, "message": "Call ended."})})
                continue
            result = dispatcher.dispatch(name, args)
            call["tools"].append({"turn": rcpt_turn, "name": name, "args": args, "result": result})
            messages.append({"role": "tool", "tool_call_id": tc.get("id"), "content": json.dumps(result)})
        if ended:
            break
    else:
        call["overflow"] = True
    return " ".join(spoken).strip()


def _seed_booking(svc, scenario: dict) -> int:
    from datetime import date, timedelta

    b = scenario.get("booking", {})
    atype = b.get("appointment_type", "medical")
    today = _utcnow().date()
    kw = dict(
        full_name=b.get("full_name", "Md Aabid Hussain"),
        phone=b.get("phone", "+1-555-0100"),
        appointment_reason=b.get("reason", "general health checkup"),
        appointment_type=atype,
        target_hospital_name=b.get("target", "City Care Hospital"),
        preferred_date_window_start=today,
        preferred_date_window_end=today + timedelta(days=14),
        preferred_time_of_day=b.get("time_of_day", "afternoon"),
    )
    if atype == "medical":
        kw.update(date_of_birth=b.get("dob", date(1990, 1, 1)),
                  insurance_provider=b.get("insurance", "Acme Health"),
                  insurance_member_id=b.get("member_id", "AH-12345"))
    result = svc.create(**kw)
    assert result.ok and result.request is not None, result.message
    return result.request.id


def _run_call(scenario: dict, keys: list, model: str, temperature, gap: float) -> dict:
    from core.agent import ToolDispatcher, build_system_prompt
    from core.booking import BookingRequestService, init_db

    init_db("sqlite://")  # fresh in-memory DB per run — isolated, no Supabase
    svc = BookingRequestService()
    booking_id = _seed_booking(svc, scenario)
    dispatcher = ToolDispatcher(svc, booking_request_id=booking_id)

    b = scenario.get("booking", {})
    system = build_system_prompt(
        caller_name=b.get("full_name", "Md Aabid Hussain"),
        target_hospital_name=b.get("target", "City Care Hospital"),
        appointment_type=b.get("appointment_type", "medical"),
        now=_utcnow(),
    )
    messages: list = [{"role": "system", "content": system}]
    call = {"id": scenario["id"], "agent_lines": [], "tools": [], "ended": False,
            "overflow": False, "events": []}

    def _emit_agent(text: str, rcpt_turn: int) -> None:
        call["agent_lines"].append(text.lower())
        recent = [t["name"] for t in call["tools"] if t["turn"] == rcpt_turn]
        tag = f"  [tools: {', '.join(recent)}]" if recent else ""
        print(f"    AGENT> {text or '(silence)'}{tag}")
        call["events"].append(("AGENT", text, recent))

    # Greeting (agent speaks first, before any receptionist line).
    _emit_agent(_agent_turn(messages, dispatcher, keys, model, temperature, call, 0), 0)

    for i, line in enumerate(scenario["receptionist"], start=1):
        if call["ended"]:
            print(f"    (call ended; not delivering: {line!r})")
            break
        messages.append({"role": "user", "content": line})
        print(f"    RECEPTIONIST> {line}")
        call["events"].append(("RECEPTIONIST", line, []))
        _emit_agent(_agent_turn(messages, dispatcher, keys, model, temperature, call, i), i)
        if i < len(scenario["receptionist"]):
            time.sleep(gap)
    return call


# --------------------------------------------------------------------------- #
# Runner / reporting
# --------------------------------------------------------------------------- #
def _ts() -> str:
    return datetime.now(timezone.utc).astimezone().strftime("%Y-%m-%d %H:%M:%S")


def _append(path: str, text: str) -> None:
    with open(path, "a", encoding="utf-8") as fh:
        fh.write(text)


def _save_results(records: list) -> None:
    with open(RESULTS_FILE, "w", encoding="utf-8") as fh:
        json.dump(records, fh, indent=2, ensure_ascii=False, default=str)


def run_scenario(scenario: dict, keys: list, model: str, temperature, gap: float, repeat: int) -> dict:
    sid = scenario["id"]
    checks = scenario.get("checks", [])
    labels = [lab for lab, _ in checks]
    print(f"\n=== {sid} — {scenario['title']}  x{repeat} ===")
    print(f"    expect: {scenario['expect']}")

    pass_counts = {lab: 0 for lab in labels}
    runs: list = []
    full_passes = 0
    for run_no in range(1, repeat + 1):
        if repeat > 1:
            print(f"  -- run {run_no}/{repeat} --")
        try:
            call = _run_call(scenario, keys, model, temperature, gap)
            results = {lab: bool(fn(call)) for lab, fn in checks}
        except Exception as exc:  # noqa: BLE001 - one bad run shouldn't abort the suite
            print(f"    !! run errored: {exc}")
            call = {"error": str(exc), "tools": [], "agent_lines": [], "events": [],
                    "ended": False, "overflow": False}
            results = {lab: False for lab in labels}
        for lab, ok in results.items():
            pass_counts[lab] += int(ok)
        run_ok = all(results.values()) if results else False
        full_passes += int(run_ok)
        runs.append({"run": run_no, "checks": results, "passed": run_ok,
                     "tools": [t["name"] for t in call["tools"]],
                     "ended": call["ended"], "overflow": call["overflow"],
                     "events": call["events"]})
        if repeat > 1:
            print(f"     -> {'PASS' if run_ok else 'FAIL'} ({sum(results.values())}/{len(results)} checks)")
        if run_no < repeat:
            time.sleep(gap)

    rate = full_passes / repeat if repeat else 0.0
    verdict = "PASS" if rate == 1.0 else ("FAIL" if rate == 0.0 else "FLAKY")
    print(f"    {verdict}  pass-rate {full_passes}/{repeat}")
    for lab in labels:
        mark = "OK " if pass_counts[lab] == repeat else "XX "
        print(f"      [{mark}] {pass_counts[lab]}/{repeat}  {lab}")

    # transcript (sample run = the first)
    block = [f"\n\n## {sid} — {scenario['title']}  ·  **{verdict}** ({full_passes}/{repeat})",
             f"_expect:_ {scenario['expect']}  ·  _at:_ {_ts()}", "", "**Checks:**"]
    for lab in labels:
        ok = pass_counts[lab] == repeat
        block.append(f"- {'✅' if ok else '❌'} `{pass_counts[lab]}/{repeat}` {lab}")
    block.append("\n**Sample call:**")
    for ev in runs[0]["events"]:
        who, text, tools = ev
        tag = f"  _[tools: {', '.join(tools)}]_" if tools else ""
        block.append(f"- **{who}:** {text}{tag}")
    _append(TRANSCRIPT_FILE, "\n".join(block) + "\n")

    return {"id": sid, "title": scenario["title"], "repeat": repeat,
            "pass_rate": round(rate, 3), "full_passes": full_passes,
            "verdict": verdict, "check_pass_counts": pass_counts, "runs": runs}


def main() -> int:
    ap = argparse.ArgumentParser(description="Text-test the VoiceStream proxy-caller brain (no audio).")
    ap.add_argument("--model", default=DEFAULT_MODEL)
    ap.add_argument("--repeat", type=int, default=3, help="runs per scenario (flake detection)")
    ap.add_argument("--gap", type=float, default=2.0, help="seconds between receptionist lines (rate limit)")
    ap.add_argument("--temperature", type=float, default=None,
                    help="override sampling temp (default: Groq default, matching production)")
    ap.add_argument("--scenario", default=None, help="run only this scenario id (e.g. C2)")
    ap.add_argument("--list", action="store_true", help="list scenarios and exit")
    args = ap.parse_args()

    if args.list:
        for s in SCENARIOS:
            print(f"  {s['id']:4s} {len(s['receptionist'])} receptionist line(s)  -- {s['title']}")
        return 0

    from core.config import get_settings

    get_settings()  # triggers core.config's load_dotenv() so .env vars reach os.environ
    keys = collect_groq_keys()
    if not keys:
        print("No GROQ_API_KEY found (voicestream/.env or backend/.env). Aborting.", file=sys.stderr)
        return 2
    print(f"Using {len(keys)} Groq key(s): {', '.join(k[:6] + '…' + k[-4:] for k in keys)}")

    scenarios = SCENARIOS
    if args.scenario:
        scenarios = [s for s in SCENARIOS if s["id"].lower() == args.scenario.lower()]
        if not scenarios:
            print(f"Unknown scenario {args.scenario!r}; use --list.", file=sys.stderr)
            return 2

    repeat = max(1, args.repeat)
    _append(TRANSCRIPT_FILE, f"# VoiceStream call-flow (text) transcript\n_model:_ {args.model}  ·  "
                             f"_repeat:_ {repeat}  ·  _started:_ {_ts()}\n")
    print(f"Model: {args.model}  ·  repeat {repeat}  ·  gap {args.gap}s  ·  in-memory booking (no Supabase, no calls)")

    records = []
    for s in scenarios:
        records.append(run_scenario(s, keys, args.model, args.temperature, args.gap, repeat))
        _save_results(records)

    clean = sum(1 for r in records if r["verdict"] == "PASS")
    flaky = sum(1 for r in records if r["verdict"] == "FLAKY")
    failed = sum(1 for r in records if r["verdict"] == "FAIL")
    print(f"\nDONE. {len(records)} scenario(s): {clean} PASS, {flaky} FLAKY, {failed} FAIL.")
    for r in records:
        if r["verdict"] != "PASS":
            print(f"  {r['verdict']:5s} {r['id']}: {r['full_passes']}/{r['repeat']}  ({r['title']})")
    print(f"Transcript: {TRANSCRIPT_FILE}")
    print(f"Results JSON: {RESULTS_FILE}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
