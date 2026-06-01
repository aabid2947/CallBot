"""LLM tool/function schemas for the Phase 2 proxy caller.

Plain OpenAI-style dicts — no SDK imports — so any OpenAI-compatible
provider (Groq, OpenAI, etc.) can consume them. Each schema is bound at
runtime by the `ToolDispatcher` to a specific BookingRequest, so the
LLM never has to pass ids around.
"""

from __future__ import annotations

# Tool name constants (single source of truth shared with the dispatcher).
GET_CALLER_INFO = "get_caller_info"
GET_APPOINTMENT_REQUEST = "get_appointment_request"
RECORD_APPOINTMENT_CONFIRMED = "record_appointment_confirmed"
RECORD_APPOINTMENT_DECLINED = "record_appointment_declined"
RECORD_APPOINTMENT_FOLLOWUP = "record_appointment_followup"
END_CALL = "end_call"

_ISO = "ISO 8601 timestamp, e.g. 2026-06-09T15:00:00Z. Assume UTC."

TOOL_SCHEMAS: list[dict] = [
    {
        "type": "function",
        "function": {
            "name": GET_CALLER_INFO,
            "description": (
                "Recall the caller's personal details (full name, date of "
                "birth, phone, email, address, insurance, patient type). "
                "Call this before answering receptionist questions about "
                "WHO is calling. No arguments."
            ),
            "parameters": {"type": "object", "properties": {}, "required": []},
        },
    },
    {
        "type": "function",
        "function": {
            "name": GET_APPOINTMENT_REQUEST,
            "description": (
                "Recall WHAT appointment to book (reason, preferred date "
                "window, time of day, doctor or department, notes, target "
                "hospital). Call this before negotiating times. No arguments."
            ),
            "parameters": {"type": "object", "properties": {}, "required": []},
        },
    },
    {
        "type": "function",
        "function": {
            "name": RECORD_APPOINTMENT_CONFIRMED,
            "description": (
                "Call this exactly once, AFTER reading the proposed time "
                "back to the receptionist and getting a clear yes. Records "
                "the confirmed appointment time and ends the booking."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "scheduled_time": {
                        "type": "string",
                        "description": f"Confirmed appointment start. {_ISO}",
                    },
                    "confirmation_number": {
                        "type": "string",
                        "description": "Confirmation number the receptionist gave, if any.",
                    },
                    "notes": {
                        "type": "string",
                        "description": "Any extra instructions the receptionist mentioned (location, what to bring).",
                    },
                },
                "required": ["scheduled_time"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": RECORD_APPOINTMENT_DECLINED,
            "description": (
                "Call this when the hospital cannot accommodate the "
                "appointment (no slots, doesn't take that insurance, "
                "wrong department, etc.). Records why and ends the call."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "reason": {
                        "type": "string",
                        "description": "One-sentence reason the receptionist gave.",
                    },
                },
                "required": ["reason"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": RECORD_APPOINTMENT_FOLLOWUP,
            "description": (
                "Call this when there is no clear resolution yet (e.g. "
                "'we'll call you back', 'someone else handles that'). "
                "Records a short note; the request stays active."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "notes": {
                        "type": "string",
                        "description": "Short note of what was agreed / what is pending.",
                    },
                },
                "required": ["notes"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": END_CALL,
            "description": (
                "Hang up and end the phone call. Call this once the call is "
                "finished — after you have recorded exactly one outcome and "
                "said a brief goodbye. Do NOT call it before recording an "
                "outcome unless the other person has clearly hung up or ended "
                "the call. No arguments."
            ),
            "parameters": {"type": "object", "properties": {}, "required": []},
        },
    },
]
