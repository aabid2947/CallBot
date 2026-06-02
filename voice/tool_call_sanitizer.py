"""FrameProcessor that rescues tool calls the LLM leaked as plain text.

Sits in the pipeline between the LLM and the TTS. Llama 3.3 on Groq sometimes
writes a tool call inside the spoken `content` instead of returning structured
`tool_calls` (CLAUDE.md bug #5 / voice_flow_problem.md V1). When that happens the
real tool never runs and TTS reads the raw `<function=...>` JSON aloud, freezing
the call. This processor:

1. Buffers the assistant text of each LLM response.
2. Detects leaked tool calls (`core.agent.extract_leaked_tool_calls`).
3. Strips them so TTS only ever speaks clean text.
4. Fires the REAL tool: `end_call` ends the task (EndTaskFrame upstream, exactly
   like the structured path in `pipeline.py`); every other tool is dispatched
   against the same `ToolDispatcher` so the outcome is recorded, not lost.

The structured-tool path is untouched: this only acts when it finds leaked
syntax in text.
"""

from __future__ import annotations

from loguru import logger
from pipecat.frames.frames import (
    EndTaskFrame,
    Frame,
    LLMFullResponseEndFrame,
    LLMFullResponseStartFrame,
    LLMTextFrame,
    TextFrame,
)
from pipecat.processors.frame_processor import FrameDirection, FrameProcessor

from core.agent import END_CALL, ToolDispatcher, extract_leaked_tool_calls


class ToolCallLeakSanitizer(FrameProcessor):
    """Strip leaked tool-call syntax from spoken text and fire the real tool."""

    def __init__(self, dispatcher: ToolDispatcher) -> None:
        super().__init__()
        self._dispatcher = dispatcher
        self._buffer = ""
        self._buffering = False

    async def process_frame(self, frame: Frame, direction: FrameDirection) -> None:
        await super().process_frame(frame, direction)

        if isinstance(frame, LLMFullResponseStartFrame):
            self._buffering = True
            self._buffer = ""
            await self.push_frame(frame, direction)
            return

        if isinstance(frame, LLMFullResponseEndFrame):
            await self._flush(direction)
            self._buffering = False
            await self.push_frame(frame, direction)
            return

        # Withhold the LLM's text while a response is streaming so a leak split
        # across chunks is still caught; release the cleaned text on flush.
        if self._buffering and isinstance(frame, TextFrame):
            self._buffer += frame.text
            return

        # A standalone text frame (no response framing) — sanitize it inline.
        if isinstance(frame, TextFrame):
            cleaned, calls = extract_leaked_tool_calls(frame.text)
            if calls:
                if cleaned:
                    await self.push_frame(LLMTextFrame(cleaned), direction)
                await self._fire(calls)
            else:
                await self.push_frame(frame, direction)
            return

        await self.push_frame(frame, direction)

    async def _flush(self, direction: FrameDirection) -> None:
        text, self._buffer = self._buffer, ""
        if not text:
            return
        cleaned, calls = extract_leaked_tool_calls(text)
        if cleaned:
            await self.push_frame(LLMTextFrame(cleaned), direction)
        if calls:
            await self._fire(calls)

    async def _fire(self, calls: list[tuple[str, dict]]) -> None:
        """Execute leaked tool calls: end the call for `end_call`, otherwise run
        the tool through the dispatcher so the outcome is recorded."""
        for name, args in calls:
            if name == END_CALL:
                logger.warning("Recovered leaked end_call from spoken text; ending call.")
                await self.push_frame(EndTaskFrame(), FrameDirection.UPSTREAM)
                continue
            logger.warning("Recovered leaked tool call {!r} from spoken text; dispatching.", name)
            try:
                self._dispatcher.dispatch(name, args)
            except Exception:  # dispatcher is no-raise, but never let this kill the call
                logger.exception("Failed to dispatch recovered tool call {!r}", name)
