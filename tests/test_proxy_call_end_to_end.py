"""End-to-end proxy-call test (Prompt 11).

Drives the EXACT path a voice turn takes during a real call: the LLM
emits tool calls, the `ToolDispatcher` executes them against the bound
BookingRequest, and the outcome is written to disk. Then we re-open a
FRESH repository against the same on-disk SQLite file and verify the
row reflects the call result — proving real persistence, not just
in-memory state.

Three scenarios mirror the three `record_*` outcomes the agent can emit:
CONFIRMED, DECLINED, FOLLOWUP. Audio is not exercised here; the latency
probe and the manual runbook cover that.
"""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from core.agent import ToolDispatcher
from core.booking import (
    BookingRequestService,
    BookingRequestStatus,
    SqlAlchemyBookingRequestRepository,
    init_db,
)

UTC = timezone.utc


@pytest.fixture
def db_url(tmp_path) -> str:
    """A real on-disk SQLite file (not in-memory) so we can prove persistence."""
    return f"sqlite:///{tmp_path / 'e2e.db'}"


def _seed_request(now: datetime) -> int:
    svc = BookingRequestService(now_fn=lambda: now)
    view = svc.seed_test_request()
    return view.id


def test_confirmed_call_persists_outcome_to_disk(db_url):
    """Happy path: receptionist offers a slot, agent confirms, row CONFIRMED."""
    init_db(db_url)
    now = datetime(2026, 6, 1, 10, 0, tzinfo=UTC)
    request_id = _seed_request(now)

    svc = BookingRequestService(now_fn=lambda: now)
    disp = ToolDispatcher(svc, booking_request_id=request_id)

    # === Conversation, replayed as the tool calls the LLM would emit ===
    info = disp.dispatch("get_caller_info", {})
    assert info["ok"] and info["caller"]["full_name"] == "Md Aabid Hussain"

    appt = disp.dispatch("get_appointment_request", {})
    assert appt["ok"]
    assert appt["appointment"]["reason"] == "general health checkup"
    assert appt["appointment"]["preferred_time_of_day"] == "afternoon"

    confirmed = disp.dispatch(
        "record_appointment_confirmed",
        {
            "scheduled_time": "2026-06-05T14:30:00Z",
            "confirmation_number": "CCH-2026-001",
            "notes": "Bring photo ID and insurance card.",
        },
    )
    assert confirmed["ok"]

    # === Prove persistence: fresh repository, same on-disk file ===
    row = SqlAlchemyBookingRequestRepository().get(request_id)
    assert row is not None
    assert row.status is BookingRequestStatus.CONFIRMED
    assert row.outcome_scheduled_time == datetime(2026, 6, 5, 14, 30, tzinfo=UTC)
    assert row.outcome_confirmation_number == "CCH-2026-001"
    assert "photo ID" in row.outcome_notes
    assert row.full_name == "Md Aabid Hussain"  # unchanged


def test_declined_call_persists_reason_to_disk(db_url):
    """Receptionist can't accommodate — agent records DECLINED + reason."""
    init_db(db_url)
    now = datetime(2026, 6, 1, 10, 0, tzinfo=UTC)
    request_id = _seed_request(now)
    disp = ToolDispatcher(
        BookingRequestService(now_fn=lambda: now),
        booking_request_id=request_id,
    )

    disp.dispatch("get_caller_info", {})  # agent answers a question
    out = disp.dispatch(
        "record_appointment_declined",
        {"reason": "No afternoon slots open this week."},
    )
    assert out["ok"]

    row = SqlAlchemyBookingRequestRepository().get(request_id)
    assert row is not None
    assert row.status is BookingRequestStatus.DECLINED
    assert "afternoon slots" in row.outcome_notes
    assert row.outcome_scheduled_time is None
    assert row.outcome_confirmation_number is None


def test_followup_call_keeps_request_active(db_url):
    """Receptionist says 'we'll call back' — record FOLLOWUP, stay active."""
    init_db(db_url)
    now = datetime(2026, 6, 1, 10, 0, tzinfo=UTC)
    request_id = _seed_request(now)
    disp = ToolDispatcher(
        BookingRequestService(now_fn=lambda: now),
        booking_request_id=request_id,
    )

    disp.dispatch("get_caller_info", {})
    out = disp.dispatch(
        "record_appointment_followup",
        {"notes": "Front-desk will call back tomorrow morning."},
    )
    assert out["ok"]

    fresh = SqlAlchemyBookingRequestRepository()
    row = fresh.get(request_id)
    assert row is not None
    assert row.status is BookingRequestStatus.IN_PROGRESS  # still active
    assert "call back" in row.outcome_notes

    # And `latest_active()` still surfaces this row — a follow-up call
    # picks up where the last one left off.
    active = fresh.latest_active()
    assert active is not None and active.id == request_id


def test_meeting_confirmed_persists_without_dob(db_url):
    """A non-medical (meeting) booking confirms + persists, and never has a DOB."""
    init_db(db_url)
    now = datetime(2026, 6, 1, 10, 0, tzinfo=UTC)
    svc = BookingRequestService(now_fn=lambda: now)
    r = svc.create(
        full_name="Bob Roy",
        phone="+1-555-1000",
        appointment_reason="quarterly sync",
        appointment_type="meeting",
        target_hospital_name="Acme Corp",
    )
    assert r.ok and r.request is not None
    rid = r.request.id

    disp = ToolDispatcher(BookingRequestService(now_fn=lambda: now), booking_request_id=rid)
    appt = disp.dispatch("get_appointment_request", {})
    assert appt["appointment"]["appointment_type"] == "meeting"

    out = disp.dispatch("record_appointment_confirmed", {"scheduled_time": "2026-06-05T14:30:00Z"})
    assert out["ok"]

    row = SqlAlchemyBookingRequestRepository().get(rid)
    assert row is not None
    assert row.status is BookingRequestStatus.CONFIRMED
    assert row.date_of_birth is None  # never collected for a meeting


def test_confirmed_row_is_no_longer_active_for_a_fresh_call(db_url):
    """After CONFIRMED, latest_active() should ignore the row; seed makes a new one."""
    init_db(db_url)
    now = datetime(2026, 6, 1, 10, 0, tzinfo=UTC)
    request_id = _seed_request(now)
    disp = ToolDispatcher(
        BookingRequestService(now_fn=lambda: now),
        booking_request_id=request_id,
    )
    disp.dispatch(
        "record_appointment_confirmed",
        {"scheduled_time": "2026-06-05T14:30:00Z"},
    )

    repo = SqlAlchemyBookingRequestRepository()
    assert repo.latest_active() is None

    # Seeding again creates a fresh PENDING row (not the confirmed one).
    svc = BookingRequestService(now_fn=lambda: now)
    next_view = svc.seed_test_request()
    assert next_view.id != request_id
    assert next_view.status == "pending"
