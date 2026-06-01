"""Voice pipeline isolation tests (Prompt 4).

These never hit the network: services are constructed with dummy keys, and
the transport is a stub. We assert the pipeline assembles and that the
voice layer pulls in no web framework.
"""

from __future__ import annotations

import pytest
from pipecat.pipeline.task import PipelineTask
from pipecat.processors.frame_processor import FrameProcessor

from core.agent import TOOL_SCHEMAS
from core.booking import init_db
from voice import VoiceConfigError, VoiceSettings, build_pipeline_task
from voice.pipeline import _function_schemas

DUMMY = VoiceSettings(
    groq_api_key="test-groq",
    deepgram_api_key="test-deepgram",
    llm_model="llama-3.3-70b-versatile",
    stt_model="nova-3",
    tts_voice="aura-2-thalia-en",
    business_name="Test Clinic",
)


class StubTransport:
    """Structurally satisfies TransportLike without any real I/O."""

    def input(self) -> FrameProcessor:
        return FrameProcessor()

    def output(self) -> FrameProcessor:
        return FrameProcessor()


def test_missing_keys_fail_fast(monkeypatch):
    monkeypatch.delenv("GROQ_API_KEY", raising=False)
    monkeypatch.delenv("DEEPGRAM_API_KEY", raising=False)
    from voice.config import load_voice_settings

    with pytest.raises(VoiceConfigError) as exc:
        load_voice_settings()
    msg = str(exc.value)
    assert "GROQ_API_KEY" in msg and "DEEPGRAM_API_KEY" in msg
    assert ".env" in msg  # actionable guidance


def test_function_schemas_match_core_tools():
    schemas = _function_schemas()
    assert len(schemas) == len(TOOL_SCHEMAS) == 6
    assert {s.name for s in schemas} == {
        t["function"]["name"] for t in TOOL_SCHEMAS
    }


def test_build_pipeline_task_in_isolation():
    init_db("sqlite://")
    # Voice build does not query the DB at construction; the dispatcher's
    # tools do, at call time. A dummy id is enough to exercise assembly.
    task = build_pipeline_task(
        StubTransport(),
        booking_request_id=1,
        settings=DUMMY,
    )
    assert isinstance(task, PipelineTask)


# Layer guardrail (voice must not import a web framework) is covered in
# test_layer_isolation.py via a fresh subprocess.
