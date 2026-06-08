"""V1 — leaked-tool-call rescue.

Two layers:
- `extract_leaked_tool_calls` (pure, no Pipecat): parses the leak syntaxes the
  model emits and returns cleaned text + structured calls.
- `ToolCallLeakSanitizer` (Pipecat FrameProcessor): strips the leak from spoken
  text and fires the real tool (end_call -> EndTaskFrame upstream; record_* ->
  dispatcher).
"""

from __future__ import annotations

from core.agent import (
    END_CALL,
    RECORD_APPOINTMENT_CONFIRMED,
    RECORD_APPOINTMENT_DECLINED,
    extract_leaked_tool_calls,
)


# --------------------------------------------------------------------------- #
# pure extractor
# --------------------------------------------------------------------------- #
def test_no_leak_passes_through_unchanged():
    text = "So that's next Tuesday at three — is that right?"
    cleaned, calls = extract_leaked_tool_calls(text)
    assert cleaned == text
    assert calls == []


def test_strips_function_eq_close_tag_form():
    # C2 run2 evidence
    text = "Great, thank you so much — have a good day! <function=end_call></function>"
    cleaned, calls = extract_leaked_tool_calls(text)
    assert "<function" not in cleaned and "end_call" not in cleaned
    assert cleaned == "Great, thank you so much — have a good day!"
    assert calls == [(END_CALL, {})]


def test_strips_function_colon_close_tag_form():
    # req=18 evidence: Llama leaked end_call with a COLON separator, which the
    # sanitizer used to miss -> the tag was spoken aloud and the call ended rough.
    text = "<function:end_call></function>"
    cleaned, calls = extract_leaked_tool_calls(text)
    assert "<function" not in cleaned and "end_call" not in cleaned
    assert cleaned == ""
    assert calls == [(END_CALL, {})]


def test_strips_function_paren_form_with_args():
    # C4 run1 evidence (unclosed paren form)
    text = (
        '<function(record_appointment_declined {"reason": "The hospital is '
        'completely booked for the month and not accepting new appointments"})'
    )
    cleaned, calls = extract_leaked_tool_calls(text)
    assert cleaned == ""
    assert len(calls) == 1
    name, args = calls[0]
    assert name == RECORD_APPOINTMENT_DECLINED
    assert args["reason"].startswith("The hospital is completely booked")


def test_strips_function_eq_with_json_body():
    text = (
        'Perfect. <function=record_appointment_confirmed>'
        '{"scheduled_time": "2026-06-09T15:00:00Z"}</function>'
    )
    cleaned, calls = extract_leaked_tool_calls(text)
    assert cleaned == "Perfect."
    assert calls == [(RECORD_APPOINTMENT_CONFIRMED, {"scheduled_time": "2026-06-09T15:00:00Z"})]


def test_strips_bare_paren_form():
    cleaned, calls = extract_leaked_tool_calls("Okay, bye now. end_call()")
    assert cleaned == "Okay, bye now."
    assert calls == [(END_CALL, {})]


def test_does_not_strip_ordinary_parenthetical():
    text = "I'll call you back later (around noon) if that's okay."
    cleaned, calls = extract_leaked_tool_calls(text)
    assert cleaned == text  # 'back'/'noon' are not tool names
    assert calls == []


def test_balanced_braces_in_json_args_not_truncated():
    text = (
        '<function=record_appointment_confirmed>'
        '{"scheduled_time": "2026-06-09T15:00:00Z", "notes": "bring form {A}"}'
        '</function>'
    )
    cleaned, calls = extract_leaked_tool_calls(text)
    assert cleaned == ""
    assert calls[0][1]["notes"] == "bring form {A}"


def test_multiple_leaks_in_one_message():
    text = 'Thanks! record_appointment_declined({"reason": "no slots"}) <function=end_call></function>'
    cleaned, calls = extract_leaked_tool_calls(text)
    assert cleaned == "Thanks!"
    assert [c[0] for c in calls] == [RECORD_APPOINTMENT_DECLINED, END_CALL]


# --------------------------------------------------------------------------- #
# Pipecat FrameProcessor (driven with asyncio.run — no pytest-asyncio dep)
# --------------------------------------------------------------------------- #
class _FakeDispatcher:
    def __init__(self):
        self.calls = []

    def dispatch(self, name, args):
        self.calls.append((name, args))
        return {"ok": True}


def _run_response(proc, text: str):
    """Feed one LLM response (start -> text -> end) through the processor and
    return (pushed_frames, direction-tagged). push_frame is captured."""
    import asyncio

    from pipecat.frames.frames import (
        LLMFullResponseEndFrame,
        LLMFullResponseStartFrame,
        LLMTextFrame,
    )
    from pipecat.processors.frame_processor import FrameDirection

    pushed: list = []

    async def capture(frame, direction=FrameDirection.DOWNSTREAM):
        pushed.append((frame, direction))

    proc.push_frame = capture  # type: ignore[assignment]

    async def drive():
        await proc.process_frame(LLMFullResponseStartFrame(), FrameDirection.DOWNSTREAM)
        await proc.process_frame(LLMTextFrame(text), FrameDirection.DOWNSTREAM)
        await proc.process_frame(LLMFullResponseEndFrame(), FrameDirection.DOWNSTREAM)

    asyncio.run(drive())
    return pushed


def test_processor_strips_text_and_ends_call():
    from pipecat.frames.frames import EndTaskFrame, TextFrame
    from pipecat.processors.frame_processor import FrameDirection

    from voice import ToolCallLeakSanitizer

    disp = _FakeDispatcher()
    proc = ToolCallLeakSanitizer(disp)
    pushed = _run_response(proc, "Great, thanks — bye! <function=end_call></function>")

    text_frames = [f for f, _ in pushed if isinstance(f, TextFrame)]
    assert text_frames and "end_call" not in text_frames[0].text
    assert text_frames[0].text == "Great, thanks — bye!"
    assert any(isinstance(f, EndTaskFrame) and d == FrameDirection.UPSTREAM for f, d in pushed)
    assert disp.calls == []  # end_call ends the task; it is not dispatched


def test_processor_dispatches_leaked_record():
    from voice import ToolCallLeakSanitizer

    disp = _FakeDispatcher()
    proc = ToolCallLeakSanitizer(disp)
    _run_response(proc, '<function(record_appointment_declined {"reason": "fully booked"})')

    assert disp.calls == [(RECORD_APPOINTMENT_DECLINED, {"reason": "fully booked"})]
