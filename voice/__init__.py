"""Voice pipeline assembly (Pipecat).

Wires Deepgram STT -> Groq LLM (with core's tools) -> Deepgram Aura TTS.
Uses `core` but stays transport-agnostic: the transport is injected and no
web framework is imported here. Run the returned task with a PipelineRunner
from the server layer (Prompt 5).
"""

from .config import VoiceConfigError, VoiceSettings, load_voice_settings
from .pipeline import (
    TransportLike,
    build_pipeline_task,
    build_services,
)
from .tool_call_sanitizer import ToolCallLeakSanitizer

__all__ = [
    "VoiceSettings",
    "VoiceConfigError",
    "load_voice_settings",
    "TransportLike",
    "build_services",
    "build_pipeline_task",
    "ToolCallLeakSanitizer",
]
