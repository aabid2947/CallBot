"""Offline self-test for test_call_flows' assertion engine (no network / no key).

Feeds synthetic `call` objects (good + buggy) through each scenario's checks and
asserts the verdicts. Run from voicestream/ (pure stdlib — any python works):
    python -m tools._selftest_call_flows
"""
from __future__ import annotations

from tools.test_call_flows import (
    SCENARIOS, T_CONFIRM, T_DECLINE, T_END,
)


def evals(scenario, call):
    return {lab: bool(fn(call)) for lab, fn in scenario.get("checks", [])}


def mk(tools, agent_lines=None, ended=False, overflow=False):
    """tools = list of (turn, name); agent_lines = spoken strings."""
    return {
        "tools": [{"turn": t, "name": n, "result": {"ok": True}} for t, n in tools],
        "agent_lines": [s.lower() for s in (agent_lines or ["hello, i'm calling to book."])],
        "ended": ended, "overflow": overflow, "events": [],
    }


BY = {s["id"]: s for s in SCENARIOS}
fails = []


def expect(sid, call, want_pass: bool, note=""):
    res = evals(BY[sid], call)
    ok = all(res.values()) if want_pass else not all(res.values())
    if not ok:
        fails.append(f"{sid}: want_pass={want_pass} {note} -> {res}")
    print(f"  {'OK ' if ok else 'XX '} {sid} (all_pass={all(res.values())}) {note}")


print("GOOD calls (correct behaviour -> all checks pass):")
# C1: get info during greeting, read-back at line 3 (no record), confirm at line 4, end_call.
expect("C1", mk([(0, "get_caller_info"), (0, "get_appointment_request"),
                 (4, T_CONFIRM), (4, T_END)], ended=True), True)
# C2: only info lookups, never a confirm, call stays open.
expect("C2", mk([(0, "get_caller_info"), (2, "get_caller_info")], ended=False), True)
# C3: one confirm + end_call (the bait line never triggers a second record).
expect("C3", mk([(3, "get_appointment_request"), (4, T_CONFIRM), (4, T_END)], ended=True), True)
# C4: one decline + end_call.
expect("C4", mk([(2, T_DECLINE), (2, T_END)], ended=True), True)
# C5: books a service appt, ends; no clinical fabrication asserted here.
expect("C5", mk([(3, T_CONFIRM), (3, T_END)], ended=True), True)
# C6: read-back at line 2, a question at line 3, NO confirm.
expect("C6", mk([(0, "get_caller_info")], ended=False), True)

print("\nBAD calls (the bugs -> engine MUST flag):")
# #1: fabricated a time and booked though none was proposed.
expect("C2", mk([(2, T_CONFIRM), (2, T_END)], ended=True), False, "(fabricated booking)")
# confirmed too early — at line 3 (the read-back turn), before the 'yes' at line 4.
expect("C1", mk([(3, T_CONFIRM), (3, T_END)], ended=True), False, "(confirmed before yes)")
# #3 flailing: multiple record_* attempts (terminal-state hammering).
expect("C3", mk([(4, T_CONFIRM), (4, T_CONFIRM), (4, T_DECLINE), (4, T_END)], ended=True),
       False, "(flailing: many records)")
# #2: recorded an outcome but never ended -> re-greet loop risk.
expect("C1", mk([(4, T_CONFIRM)], ended=False), False, "(never ended after outcome)")
# tool syntax leaked into spoken text (Llama plain-text tool bug).
expect("C1", mk([(4, T_CONFIRM), (4, T_END)],
                agent_lines=['<function=record_appointment_confirmed>{"scheduled_time":"..."}'],
                ended=True), False, "(leaked tool syntax)")
# C6 over-eager confirm: treated the question as a yes.
expect("C6", mk([(2, T_CONFIRM), (2, T_END)], ended=True), False, "(confirmed on a non-yes)")
# tool loop overflow (never terminated).
expect("C2", mk([(2, "get_caller_info")], overflow=True), False, "(loop overflow)")

print("\n" + ("ALL SELF-TESTS PASSED" if not fails else f"{len(fails)} SELF-TEST FAILURE(S):"))
for f in fails:
    print("  !!", f)
raise SystemExit(1 if fails else 0)
