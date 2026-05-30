"""Agent brain: persona/prompts, LLM tool schemas, tool dispatcher.

Phase 2 — proxy caller. The agent talks to a hospital on behalf of a user
and is bound to a single `BookingRequest` for the whole call session.
LLM-provider-agnostic; no network, Pipecat, web, or transport imports —
another project can import this with `core` alone.
"""

from .dispatcher import ToolDispatcher
from .prompts import (
    DEFAULT_CALLER_NAME,
    DEFAULT_TARGET_HOSPITAL,
    build_system_prompt,
)
from .tools import (
    GET_APPOINTMENT_REQUEST,
    GET_CALLER_INFO,
    RECORD_APPOINTMENT_CONFIRMED,
    RECORD_APPOINTMENT_DECLINED,
    RECORD_APPOINTMENT_FOLLOWUP,
    TOOL_SCHEMAS,
)

__all__ = [
    "ToolDispatcher",
    "build_system_prompt",
    "DEFAULT_CALLER_NAME",
    "DEFAULT_TARGET_HOSPITAL",
    "TOOL_SCHEMAS",
    "GET_CALLER_INFO",
    "GET_APPOINTMENT_REQUEST",
    "RECORD_APPOINTMENT_CONFIRMED",
    "RECORD_APPOINTMENT_DECLINED",
    "RECORD_APPOINTMENT_FOLLOWUP",
]
