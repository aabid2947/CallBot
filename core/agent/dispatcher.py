"""Tool-call dispatcher for the Phase 2 proxy caller.

The dispatcher is constructed with a `BookingRequestService` AND a
`booking_request_id`, bound at session start by the server. Every tool
operates on the bound request — the LLM never has to pass ids around.

It NEVER raises for caller/model mistakes (unknown tool, bad JSON, bad
datetime, missing field, unbound session). Those come back as
``{"ok": false, "error": ..., "message": ...}`` so the model can recover
and keep the conversation going.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any

from core.booking import (
    BookingRequestResult,
    BookingRequestService,
    BookingRequestView,
)

from . import tools

# Stable dispatcher-level error codes (distinct from BookingRequestError values).
ERR_UNKNOWN_TOOL = "unknown_tool"
ERR_INVALID_ARGUMENTS = "invalid_arguments"
ERR_NOT_BOUND = "not_bound"
ERR_NOT_FOUND = "not_found"


def _err(error: str, message: str) -> dict[str, Any]:
    return {"ok": False, "error": error, "message": message}


def _date_or_none(d) -> str | None:
    return d.isoformat() if d is not None else None


def _dt_or_none(dt) -> str | None:
    return dt.isoformat() if dt is not None else None


def _view_to_summary(v: BookingRequestView) -> dict[str, Any]:
    """Compact, JSON-safe snapshot for post-update tool results."""
    return {
        "id": v.id,
        "full_name": v.full_name,
        "status": v.status,
        "scheduled_time": _dt_or_none(v.outcome_scheduled_time),
        "confirmation_number": v.outcome_confirmation_number,
        "outcome_notes": v.outcome_notes,
    }


def _request_result_to_dict(r: BookingRequestResult) -> dict[str, Any]:
    return {
        "ok": r.ok,
        "error": r.error.value if r.error else None,
        "message": r.message,
        "request": _view_to_summary(r.request) if r.request else None,
    }


def _parse_dt(value: Any, field: str) -> datetime:
    """Parse an ISO 8601 string to an aware UTC datetime, or raise ValueError."""
    if not isinstance(value, str):
        raise ValueError(f"'{field}' must be an ISO 8601 string.")
    text = value.strip().replace("Z", "+00:00")
    try:
        dt = datetime.fromisoformat(text)
    except ValueError as exc:
        raise ValueError(f"'{field}' is not a valid ISO 8601 timestamp.") from exc
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def _require(args: dict, name: str) -> Any:
    if name not in args or args[name] in (None, ""):
        raise ValueError(f"Missing required argument '{name}'.")
    return args[name]


class ToolDispatcher:
    """Executes tool calls against an injected `BookingRequestService`,
    bound to a single `booking_request_id` for the whole call session."""

    def __init__(
        self,
        service: BookingRequestService,
        booking_request_id: int | None = None,
    ) -> None:
        self._svc = service
        self._request_id = booking_request_id
        self._handlers = {
            tools.GET_CALLER_INFO: self._get_caller_info,
            tools.GET_APPOINTMENT_REQUEST: self._get_appointment_request,
            tools.RECORD_APPOINTMENT_CONFIRMED: self._record_confirmed,
            tools.RECORD_APPOINTMENT_DECLINED: self._record_declined,
            tools.RECORD_APPOINTMENT_FOLLOWUP: self._record_followup,
        }

    @property
    def booking_request_id(self) -> int | None:
        return self._request_id

    def dispatch(
        self, name: str, arguments: dict[str, Any] | str | None
    ) -> dict[str, Any]:
        """Run a tool call. `arguments` may be a dict or a JSON string."""
        handler = self._handlers.get(name)
        if handler is None:
            return _err(ERR_UNKNOWN_TOOL, f"Unknown tool '{name}'.")
        if self._request_id is None:
            return _err(
                ERR_NOT_BOUND,
                "No booking request is bound to this session.",
            )

        if arguments is None:
            args: dict[str, Any] = {}
        elif isinstance(arguments, str):
            try:
                args = json.loads(arguments or "{}")
            except json.JSONDecodeError:
                return _err(ERR_INVALID_ARGUMENTS, "Arguments were not valid JSON.")
        else:
            args = arguments

        if not isinstance(args, dict):
            return _err(ERR_INVALID_ARGUMENTS, "Arguments must be an object.")

        try:
            return handler(args)
        except ValueError as exc:
            return _err(ERR_INVALID_ARGUMENTS, str(exc))

    # ---- handlers (assume bound; ValueError -> invalid_arguments) -------- #
    def _bound_view(self) -> BookingRequestView | dict[str, Any]:
        assert self._request_id is not None  # checked in dispatch()
        view = self._svc.get(self._request_id)
        if view is None:
            return _err(
                ERR_NOT_FOUND,
                f"Booking request {self._request_id} not found.",
            )
        return view

    def _get_caller_info(self, _args: dict) -> dict[str, Any]:
        view = self._bound_view()
        if isinstance(view, dict):  # error
            return view
        caller: dict[str, Any] = {
            "full_name": view.full_name,
            "phone": view.phone,
            "email": view.email,
            "address": view.address,
            "contact_info": view.contact_info,
        }
        # Clinical fields are only relevant (and only collected) for medical
        # appointments; for meeting/service/other the agent must not raise them.
        if (view.appointment_type or "medical").lower() == "medical":
            caller.update(
                {
                    "date_of_birth": _date_or_none(view.date_of_birth),
                    "insurance_provider": view.insurance_provider,
                    "insurance_member_id": view.insurance_member_id,
                    "is_new_patient": view.is_new_patient,
                }
            )
        return {"ok": True, "message": "Caller info.", "caller": caller}

    def _get_appointment_request(self, _args: dict) -> dict[str, Any]:
        view = self._bound_view()
        if isinstance(view, dict):
            return view
        return {
            "ok": True,
            "message": "Appointment details.",
            "appointment": {
                "appointment_type": view.appointment_type,
                "reason": view.appointment_reason,
                "preferred_date_window_start": _date_or_none(
                    view.preferred_date_window_start
                ),
                "preferred_date_window_end": _date_or_none(
                    view.preferred_date_window_end
                ),
                "preferred_time_of_day": view.preferred_time_of_day,
                "preferred_doctor": view.preferred_doctor,
                "department": view.department,
                "notes": view.notes,
                "target_hospital_name": view.target_hospital_name,
            },
        }

    def _record_confirmed(self, args: dict) -> dict[str, Any]:
        scheduled = _parse_dt(_require(args, "scheduled_time"), "scheduled_time")
        return _request_result_to_dict(
            self._svc.record_confirmed(
                self._request_id,  # type: ignore[arg-type]
                scheduled,
                confirmation_number=args.get("confirmation_number") or None,
                notes=args.get("notes") or None,
            )
        )

    def _record_declined(self, args: dict) -> dict[str, Any]:
        reason = _require(args, "reason")
        return _request_result_to_dict(
            self._svc.record_declined(self._request_id, reason)  # type: ignore[arg-type]
        )

    def _record_followup(self, args: dict) -> dict[str, Any]:
        notes = _require(args, "notes")
        return _request_result_to_dict(
            self._svc.record_followup(self._request_id, notes)  # type: ignore[arg-type]
        )
