# VoiceStream call-flow problems — fix brief

Hand-off doc for a fresh Claude session in the **voicestream** repo. Each problem has the
**symptom** (real transcript evidence), the **root cause** (file + line), a **proposed
fix**, and **how to verify**. Fix **V1 first — it's the call-killer.**

---

## How these were found (context — read before fixing)

- Tester: [`tools/test_call_flows.py`](tools/test_call_flows.py) — a TEXT harness for the
  proxy-caller **brain** (no audio/WebRTC/Pipecat): it runs the real
  `build_system_prompt` + `TOOL_SCHEMAS` + `ToolDispatcher` + **Groq function-calling**
  against a scripted "receptionist", bound to an in-memory booking. Scenario IDs C1–C6.
- Model/stack: production `llama-3.3-70b-versatile` on Groq — **the same brain the live
  pipeline uses**, so findings transfer. The harness calls Groq's OpenAI-compatible
  endpoint with `tools` + `tool_choice:auto`, exactly as Pipecat's `GroqLLMService` does.
- ⚠️ **Groq free-tier reliability:** Groq rate-limits **per org** on tokens/minute (TPM),
  and the big persona + 6-tool schema makes each call token-heavy. The harness now rotates
  2 keys + honors `retry-after`, but some runs still error out → **C2/C4 have only 1 valid
  run each and C5 errored on both.** Treat the failures as *real but thin* — re-run on a
  fresh Groq window (or a paid tier) at `--repeat 3` for solid pass-rates.

Re-verify any fix (from `voicestream/`, needs `GROQ_API_KEY` in `.env`):
```
python -m tools.test_call_flows --scenario C2 --repeat 3 --gap 4
python -m tools.test_call_flows --scenario C4 --repeat 3 --gap 4
python -m tools.test_call_flows --repeat 3 --gap 4        # full C1-C6
```

---

## Already FIXED (verified — do NOT re-fix)

The three flaws originally diagnosed from `logs/voicestream.log` are fixed in code and
**confirmed by this run** (C1 + C3 passed clean, 2/2, zero issues):
- **#1 hallucinated read-back / booking a fabricated time** — prompt tightened; C1/C3 read
  the time back and only record after a real "yes". ✅
- **#2 no hang-up → re-greeting loop** — `end_call` tool + wind-down; C1/C3 end the call. ✅
- **#3 terminal flailing** — dispatcher returns an `already_recorded` signal; no repeats. ✅

(See `core/agent/{prompts.py,tools.py,dispatcher.py}` + `voice/pipeline.py`.) The problems
below are what the harness surfaced **on top of** those.

---

## Scorecard (this run, partial — Groq errors noted)

| Scenario | Result | Note |
|---|---|---|
| C1 happy path | ✅ PASS 2/2 | clean |
| C2 no-time-proposed | ❌ FAIL | 1 valid run failed (V1+V2); 1 Groq error |
| C3 post-confirmation | ✅ PASS 2/2 | clean — no flailing, ends call |
| C4 decline | ❌ FAIL | 1 valid run failed (V1); 1 Groq error |
| C5 non-medical | ⚪ no data | both runs Groq-errored — re-run |
| C6 ambiguous-confirm | ⏳ pending | (running when this was written) |

---

## V1 — Tool calls emitted as PLAIN TEXT in the spoken line ✅ FIXED (2026-06-03)

> **FIXED** — the sanitizing FrameProcessor is implemented as proposed.
> - New PURE module `core/agent/tool_text.py` → `extract_leaked_tool_calls(text)` returns
>   `(cleaned_text, [(name, args)])`. Parses all three leak syntaxes —
>   `<function=NAME>{json}</function>`, `<function(NAME {json})`, and bare `NAME({json})` —
>   with a string-aware balanced-brace JSON reader (nested braces aren't truncated) and a
>   `KNOWN_TOOLS` guard so ordinary parenthetical speech ("call you back (around noon)") is
>   never stripped. Exported from `core.agent`.
> - New `voice/tool_call_sanitizer.py` → `ToolCallLeakSanitizer(FrameProcessor)`, inserted in
>   `voice/pipeline.py` between the LLM and TTS. It buffers each assistant response, strips any
>   leaked call from the spoken text, and fires the REAL tool: `end_call` → `EndTaskFrame`
>   upstream (identical to the structured path); `record_*`/`get_*` → the bound `ToolDispatcher`
>   so the outcome is recorded, not lost. The structured path is untouched.
> - Prompt hardened one notch (`core/agent/prompts.py`): "If you ever need a tool, CALL it as a
>   function — never type its name, arguments, or JSON as words you say."
> - Tests: `tests/test_tool_call_sanitizer.py` — extractor matrix (both evidence strings, nested
>   braces, multi-leak, no-false-strip) + processor tests (text stripped + `end_call` ends the
>   task; leaked `record_appointment_declined` reaches the dispatcher with parsed args). Pure
>   extractor logic validated locally; run the processor tests in the VoiceStream venv (needs
>   Pipecat). ⚠️ Live only after `git pull && sudo systemctl restart callbot` on the VPS.
>
> NOTE: the text harness still reports raw leaks — it measures the model, not the pipeline (see
> the note at the bottom of this section). Rely on the processor test for the fix.

**This is the live-call killer.** Llama 3.3 on Groq intermittently writes a tool call as
**text inside `content`** instead of returning a structured `tool_calls` object. When that
happens: (a) the tool **never executes** (the outcome is never recorded / the call never
ends), and (b) the raw `<function=…>` string **leaks into the spoken text** — in a real
call Deepgram TTS reads the JSON aloud and the call freezes. This is the known **bug #5 in
[CLAUDE.md](CLAUDE.md)** ("Llama 3.3 on Groq occasionally emits tool calls as PLAIN TEXT").

**Evidence**
```
C2 run2  AGENT> Great, thank you so much — have a good day! <function=end_call></function>
         → tools actually fired: [record_appointment_followup]   ended: FALSE
         → 'end_call' leaked as text → call never ended; speech-leak check failed

C4 run1  AGENT> <function(record_appointment_declined {"reason": "The hospital is completely
                booked for the month and not accepting new appointments"})
         → tools actually fired: [end_call]   (the DECLINE never recorded)
         → outcome leaked as text → 'records a decline' check failed
```
Two different syntaxes leak: `<function=NAME></function>` and `<function(NAME {json})`.

**Root cause** — model behaviour (Groq Llama 3.3), not our code. The system prompt already
forbids it ([`core/agent/prompts.py`](core/agent/prompts.py) — "NEVER write the tool name,
ISO timestamp, JSON braces, 'function='… in the spoken content"), which reduces but does
**not** eliminate it. Prompt alone cannot fix a model that ignores it ~10-20% of the time.

**Proposed fix — a sanitizing FrameProcessor (the escalation CLAUDE.md bug #5 names)**
Add a Pipecat processor between the LLM and TTS that intercepts the assistant text, and:
1. **Detects** leaked tool calls via regex — both `<function=NAME>{...}</function>` and
   `<function(NAME {...})` (and bare `NAME({...})`). Parse NAME + JSON args.
2. **Strips** the matched span from the text frame so TTS never speaks it.
3. **Synthesizes a real tool call** → routes NAME+args to the existing
   [`ToolDispatcher`](core/agent/dispatcher.py) (and, for `end_call`, pushes the
   `EndTaskFrame` exactly like [`voice/pipeline.py`](voice/pipeline.py) already does for the
   structured path), then feeds the tool result back so the model continues.
- Wire it in [`voice/pipeline.py`](voice/pipeline.py) `build_pipeline_task()` — insert the
  processor in the `Pipeline([... llm, <here>, tts ...])` list, after the LLM, before TTS.
- Keep the existing structured-tool path working; the processor only acts when it finds
  leaked syntax in text. Reuse the same dispatcher + `end_call` handling so behaviour is
  identical whether the call came structured or as text.
- Also harden the prompt one notch (a one-line, blunt "If you ever need a tool, CALL it —
  never type its name or JSON") as defense-in-depth.

**Verify** — C4 must actually fire `record_appointment_declined` (structured, not text) and
its spoken line must be clean; C2 must not leak `end_call` as text. Add a unit test for the
processor: feed it `"...bye <function=end_call></function>"` → asserts text stripped + an
end signal emitted; feed `<function(record_appointment_declined {...})` → asserts dispatcher
called with the parsed args.

> NOTE on the harness: the text harness can't synthesize the processor (it has no Pipecat
> pipeline), so even after the fix, `tools/test_call_flows.py` will still SEE the raw leak
> for runs where the model misbehaves — its `clean_speech` check is measuring the *model*,
> not the pipeline. The real fix lives in the pipeline; treat the harness's V1 flags as
> "how often the model leaks", and rely on a **processor unit test** for the fix itself.

---

## V2 — Premature give-up: records a followup when nothing is resolved ✅ FIXED (2026-06-03)

> **FIXED** — tightened the followup rule in `core/agent/prompts.py`. The persona now states
> explicitly that **a pause is NOT an outcome**: on "one moment" / "let me check the calendar" /
> "hold on" / a question, the agent records nothing and keeps the call open (waits or answers).
> `record_appointment_followup` is reserved for a **genuine** non-resolution where nothing more
> can happen on this call ("we'll call you back", "call the X department", "book online", "can't
> do this over the phone"). Regression test `test_system_prompt_does_not_followup_on_a_hold` in
> `tests/test_proxy_agent_dispatcher.py`. ⚠️ Live only after redeploy on the VPS.

**Symptom** — C2's receptionist only said *"Let me check the calendar, one moment,"* yet the

**Symptom** — C2's receptionist only said *"Let me check the calendar, one moment,"* yet the
agent recorded `record_appointment_followup` and tried to end the call:
```
C2 run2  RECE> Let me check the calendar, one moment.
         AGENT> Great, thank you so much — have a good day!   [record_appointment_followup]
```
A hold/"one moment" is **not** a non-resolution — the agent should wait (stay silent or
acknowledge) and let the receptionist come back with a time. Recording a followup here ends
the booking prematurely. (C2's whole point: with no time proposed, record **no** outcome.)

**Root cause** — the booking-flow persona treats any lull as a reason to wrap up with a
followup. The `record_appointment_followup` guidance in
[`core/agent/prompts.py`](core/agent/prompts.py) is too loose.

**Proposed fix** — tighten the followup rule in the persona: only call
`record_appointment_followup` when the receptionist gives an **actual** non-resolution
("we'll call you back", "you'll need to call another department", "we can't do it over the
phone"). A pause, "one moment", "let me check", or a question is **not** a reason to record
anything — wait briefly or answer, and keep the call open. (Pairs naturally with the V1 fix,
since the spurious followup rode along with a leaked `end_call`.)

**Verify** — C2 records no outcome and does not end while the receptionist is still working.

---

## Suggested fix order
1. **V1** — the sanitizing processor. This is the only one that breaks *real* calls (TTS
   reading JSON, outcomes silently lost). Highest priority by far.
2. **V2** — tighten the followup rule. Small prompt change.
3. **Re-run** `tools/test_call_flows.py` on a non-throttled Groq window at `--repeat 3` to
   get clean C2/C4/C5/C6 data (this run's C5 errored, C6 was pending). Confirm C1–C6 green.

After fixes, redeploy on the VPS (`git pull && sudo systemctl restart callbot`) and run a
real call from `script.md`, watching `logs/voicestream.log` for any remaining `<function=`
leaks.
