"""Proxy-caller agent tests (Prompt 9): schemas, dispatcher, prompt builder."""

from __future__ import annotations

import json
from datetime import date, datetime, timezone

import pytest

from core.agent import TOOL_SCHEMAS, ToolDispatcher, build_system_prompt
from core.agent.dispatcher import (
    ERR_INVALID_ARGUMENTS,
    ERR_NOT_BOUND,
    ERR_NOT_FOUND,
    ERR_UNKNOWN_TOOL,
)
from core.booking import BookingRequestService, init_db

UTC = timezone.utc
NOW = datetime(2026, 6, 1, 10, 0, tzinfo=UTC)


@pytest.fixture(autouse=True)
def db_with_request():
    init_db("sqlite://")


def _seed() -> tuple[BookingRequestService, int]:
    svc = BookingRequestService(now_fn=lambda: NOW)
    r = svc.create(
        full_name="Md Aabid Hussain",
        date_of_birth=date(2000, 1, 15),
        phone="+91-9876-543210",
        email="md.aabid.test@example.com",
        address="221B Baker Street",
        insurance_provider="Test Insurance Co.",
        insurance_member_id="TIC-1",
        appointment_reason="general health checkup",
        preferred_date_window_start=date(2026, 6, 1),
        preferred_date_window_end=date(2026, 6, 8),
        preferred_time_of_day="afternoon",
        target_hospital_name="City Care Hospital",
        notes="seed",
    )
    assert r.ok and r.request is not None
    return svc, r.request.id


def _seed_meeting() -> tuple[BookingRequestService, int]:
    svc = BookingRequestService(now_fn=lambda: NOW)
    r = svc.create(
        full_name="Bob Roy",
        phone="+1-555-1000",
        appointment_reason="quarterly sync",
        appointment_type="meeting",
        target_hospital_name="Acme Corp",
        contact_info="reach me at bob@example.com",
        scheduled_call_at=NOW,
    )
    assert r.ok and r.request is not None
    return svc, r.request.id


@pytest.fixture
def disp() -> ToolDispatcher:
    svc, rid = _seed()
    return ToolDispatcher(svc, booking_request_id=rid)


# --------------------------------------------------------------------------- #
# Schemas
# --------------------------------------------------------------------------- #
def test_schemas_are_openai_shaped_and_map_to_handlers(disp):
    names = set()
    for schema in TOOL_SCHEMAS:
        assert schema["type"] == "function"
        fn = schema["function"]
        assert {"name", "description", "parameters"} <= fn.keys()
        assert fn["parameters"]["type"] == "object"
        names.add(fn["name"])
    assert names == set(disp._handlers)
    assert names == {
        "get_caller_info",
        "get_appointment_request",
        "record_appointment_confirmed",
        "record_appointment_declined",
        "record_appointment_followup",
        "end_call",
    }


# --------------------------------------------------------------------------- #
# Bound-session happy paths
# --------------------------------------------------------------------------- #
def test_get_caller_info_returns_bound_request_data(disp):
    out = disp.dispatch("get_caller_info", {})
    assert out["ok"] is True
    caller = out["caller"]
    assert caller["full_name"] == "Md Aabid Hussain"
    assert caller["date_of_birth"] == "2000-01-15"
    assert caller["phone"] == "+91-9876-543210"
    assert caller["insurance_provider"] == "Test Insurance Co."
    assert caller["is_new_patient"] is True
    assert json.dumps(out)  # JSON-serialisable


def test_get_appointment_request_returns_request_details(disp):
    out = disp.dispatch("get_appointment_request", {})
    assert out["ok"] is True
    appt = out["appointment"]
    assert appt["reason"] == "general health checkup"
    assert appt["preferred_time_of_day"] == "afternoon"
    assert appt["target_hospital_name"] == "City Care Hospital"
    assert appt["preferred_date_window_start"] == "2026-06-01"


def test_record_appointment_confirmed_updates_to_confirmed(disp):
    out = disp.dispatch(
        "record_appointment_confirmed",
        {
            "scheduled_time": "2026-06-09T15:00:00Z",
            "confirmation_number": "CCH-2026-001",
            "notes": "bring photo ID",
        },
    )
    assert out["ok"] is True
    req = out["request"]
    assert req["status"] == "confirmed"
    assert req["confirmation_number"] == "CCH-2026-001"
    assert req["scheduled_time"].startswith("2026-06-09T15:00")


def test_record_appointment_declined(disp):
    out = disp.dispatch(
        "record_appointment_declined",
        {"reason": "Doctor not available this week"},
    )
    assert out["ok"] is True
    assert out["request"]["status"] == "declined"
    assert "Doctor not available" in out["request"]["outcome_notes"]


def test_record_appointment_followup(disp):
    out = disp.dispatch(
        "record_appointment_followup",
        {"notes": "Receptionist will call back tomorrow morning"},
    )
    assert out["ok"] is True
    assert out["request"]["status"] == "in_progress"
    assert "call back" in out["request"]["outcome_notes"]


def test_end_call_acks(disp):
    # Core only acknowledges; the real hang-up happens in the voice layer.
    out = disp.dispatch("end_call", {})
    assert out["ok"] is True
    assert json.dumps(out)  # JSON-serialisable


def test_arguments_accepted_as_json_string(disp):
    out = disp.dispatch(
        "record_appointment_confirmed",
        json.dumps({"scheduled_time": "2026-06-09T15:00:00Z"}),
    )
    assert out["ok"] is True and out["request"]["status"] == "confirmed"


# --------------------------------------------------------------------------- #
# Failure modes — structured, never exceptions
# --------------------------------------------------------------------------- #
def test_second_record_signals_already_recorded(disp):
    # First call confirms; any further record_* must NOT surface a raw
    # invalid_transition (which the model misreads as "retry / try another
    # tool"). It gets a clear "already recorded — end the call" signal instead.
    disp.dispatch(
        "record_appointment_confirmed",
        {"scheduled_time": "2026-06-09T15:00:00Z"},
    )
    # A second confirm, AND a different record_* tool, both terminate cleanly.
    for name, args in (
        ("record_appointment_confirmed", {"scheduled_time": "2026-06-10T15:00:00Z"}),
        ("record_appointment_declined", {"reason": "changed my mind"}),
        ("record_appointment_followup", {"notes": "call back"}),
    ):
        out = disp.dispatch(name, args)
        assert out["ok"] is True
        assert out["already_recorded"] is True
        assert "end_call" in out["message"]
        assert out["request"]["status"] == "confirmed"  # outcome unchanged
        assert json.dumps(out)  # JSON-serialisable


def test_unknown_tool(disp):
    out = disp.dispatch("teleport_caller", {})
    assert out["ok"] is False and out["error"] == ERR_UNKNOWN_TOOL


def test_invalid_json_arguments(disp):
    out = disp.dispatch("record_appointment_declined", "{not json")
    assert out["ok"] is False and out["error"] == ERR_INVALID_ARGUMENTS


def test_missing_required_argument(disp):
    out = disp.dispatch("record_appointment_confirmed", {})
    assert out["ok"] is False and out["error"] == ERR_INVALID_ARGUMENTS


def test_bad_datetime(disp):
    out = disp.dispatch(
        "record_appointment_confirmed",
        {"scheduled_time": "next tuesday-ish"},
    )
    assert out["ok"] is False and out["error"] == ERR_INVALID_ARGUMENTS


def test_unbound_session_returns_not_bound():
    svc, _ = _seed()
    unbound = ToolDispatcher(svc, booking_request_id=None)
    for name in (
        "get_caller_info",
        "get_appointment_request",
        "record_appointment_confirmed",
        "record_appointment_declined",
        "record_appointment_followup",
    ):
        out = unbound.dispatch(name, {})
        assert out["ok"] is False and out["error"] == ERR_NOT_BOUND


def test_bound_to_nonexistent_id_returns_not_found():
    svc, _ = _seed()
    bad = ToolDispatcher(svc, booking_request_id=999_999)
    out = bad.dispatch("get_caller_info", {})
    assert out["ok"] is False and out["error"] == ERR_NOT_FOUND


# --------------------------------------------------------------------------- #
# Prompt builder
# --------------------------------------------------------------------------- #
def test_system_prompt_injects_caller_target_and_time():
    p = build_system_prompt(
        caller_name="Md Aabid Hussain",
        target_hospital_name="City Care Hospital",
        now=NOW,
    )
    assert "Md Aabid Hussain" in p
    assert "City Care Hospital" in p
    assert "2026-06-01T10:00:00+00:00" in p
    assert "spoken aloud" in p.lower()        # voice-style guidance present
    assert "first person" in p.lower()        # persona discipline present
    assert "get_caller_info" in p             # tool the agent must know about


def test_system_prompt_falls_back_to_defaults_when_unspecified():
    p = build_system_prompt(now=NOW)
    assert "the caller" in p
    assert "the hospital" in p


def test_system_prompt_has_wind_down_and_end_call():
    p = build_system_prompt(now=NOW).lower()
    assert "end_call" in p                 # the agent knows how to hang up
    assert "do not start the conversation over" in p
    assert "do not re-introduce yourself" in p


def test_system_prompt_does_not_followup_on_a_hold():
    """V2: a pause / 'one moment' / 'let me check' is NOT a reason to record a
    followup and end the call — the persona must say to wait and keep it open."""
    p = build_system_prompt(now=NOW).lower()
    assert "a pause is not an outcome" in p
    assert "one moment" in p and "let me check" in p
    # followup is reserved for a genuine non-resolution
    assert "record_appointment_followup` only" in p or "followup` only" in p
    assert "keep the call open" in p


def test_system_prompt_forbids_inventing_times():
    p = build_system_prompt(now=NOW).lower()
    # Must not invent/propose a time, and a non-yes is not confirmation.
    assert "invent, guess, or propose an appointment time" in p
    assert "is not confirmation" in p
    # The old concrete example time was being parroted into live calls as a
    # fabricated slot — it must not appear verbatim in the prompt anymore.
    assert "tuesday the ninth" not in p


# --------------------------------------------------------------------------- #
# Appointment-type adaptation (Feature 4)
# --------------------------------------------------------------------------- #
def test_caller_info_omits_clinical_fields_for_non_medical():
    svc, rid = _seed_meeting()
    out = ToolDispatcher(svc, booking_request_id=rid).dispatch("get_caller_info", {})
    assert out["ok"] is True
    caller = out["caller"]
    assert caller["full_name"] == "Bob Roy"
    assert caller["contact_info"] == "reach me at bob@example.com"
    assert "date_of_birth" not in caller
    assert "insurance_provider" not in caller


def test_appointment_request_exposes_type(disp):
    out = disp.dispatch("get_appointment_request", {})
    assert out["appointment"]["appointment_type"] == "medical"


def test_system_prompt_medical_mentions_clinical_details():
    p = build_system_prompt(
        caller_name="A", target_hospital_name="H", appointment_type="medical", now=NOW
    ).lower()
    assert "date of birth" in p and "insurance" in p


def test_system_prompt_meeting_drops_clinical_details():
    p = build_system_prompt(
        caller_name="A", target_hospital_name="H", appointment_type="meeting", now=NOW
    ).lower()
    assert "keep it general" in p
    assert "do not bring up date of birth" in p
