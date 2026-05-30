"""BookingRequestService business-rule tests (Prompt 8).

Deterministic clock; in-memory SQLite per test. Verifies input validation,
status transitions, outcome recording, and seed-helper idempotency.
"""

from __future__ import annotations

from datetime import date, datetime, timezone

import pytest

from core.booking import (
    SEED_NAME,
    BookingRequestError,
    BookingRequestService,
    BookingRequestStatus,
    BookingRequestView,
    SqlAlchemyBookingRequestRepository,
    init_db,
)

UTC = timezone.utc
NOW = datetime(2026, 6, 1, 10, 0, tzinfo=UTC)


@pytest.fixture(autouse=True)
def fresh_db():
    init_db("sqlite://")
    yield


@pytest.fixture
def svc() -> BookingRequestService:
    return BookingRequestService(now_fn=lambda: NOW)


def _make(svc: BookingRequestService, **overrides) -> BookingRequestView:
    fields = dict(
        full_name="Alice Example",
        date_of_birth=date(1990, 1, 1),
        phone="+11111",
        appointment_reason="general checkup",
    )
    fields.update(overrides)
    r = svc.create(**fields)
    assert r.ok and r.request is not None, r
    return r.request


# --------------------------------------------------------------------------- #
# Create + validation
# --------------------------------------------------------------------------- #
def test_create_happy_returns_view_not_orm(svc):
    r = svc.create(
        full_name="Md Aabid Hussain",
        date_of_birth=date(2000, 1, 15),
        phone="+91-9876-543210",
        appointment_reason="general health checkup",
    )
    assert r.ok and isinstance(r.request, BookingRequestView)
    assert r.request.id is not None
    assert r.request.status == "pending"


@pytest.mark.parametrize(
    "overrides",
    [
        {"full_name": "   "},
        {"phone": ""},
        {"appointment_reason": "  "},
        {"preferred_time_of_day": "midnight"},  # not in the allowed set
        {
            "preferred_date_window_start": date(2026, 6, 10),
            "preferred_date_window_end": date(2026, 6, 1),  # end before start
        },
    ],
)
def test_create_rejects_invalid_input(svc, overrides):
    base = dict(
        full_name="Test",
        date_of_birth=date(1990, 1, 1),
        phone="+1",
        appointment_reason="x",
    )
    base.update(overrides)
    r = svc.create(**base)
    assert not r.ok and r.error is BookingRequestError.INVALID_INPUT


def test_create_normalises_time_of_day_case(svc):
    r = svc.create(
        full_name="X",
        date_of_birth=date(1990, 1, 1),
        phone="+1",
        appointment_reason="x",
        preferred_time_of_day="Afternoon",
    )
    assert r.ok and r.request.preferred_time_of_day == "afternoon"


# --------------------------------------------------------------------------- #
# get / latest_active
# --------------------------------------------------------------------------- #
def test_get_and_latest_active(svc):
    assert svc.latest_active() is None

    older = _make(svc, full_name="Older")
    newer = _make(svc, full_name="Newer")
    latest = svc.latest_active()
    assert latest is not None and latest.id == newer.id
    assert svc.get(older.id) is not None
    assert svc.get(99999) is None


# --------------------------------------------------------------------------- #
# Transitions
# --------------------------------------------------------------------------- #
def test_mark_in_progress_from_pending(svc):
    req = _make(svc)
    r = svc.mark_in_progress(req.id)
    assert r.ok and r.request.status == "in_progress"


def test_mark_in_progress_is_idempotent(svc):
    req = _make(svc)
    svc.mark_in_progress(req.id)
    r = svc.mark_in_progress(req.id)  # second call from IN_PROGRESS
    assert r.ok and r.request.status == "in_progress"
    assert "in progress" in r.message.lower()


def test_mark_in_progress_rejects_terminal(svc):
    req = _make(svc)
    svc.record_confirmed(req.id, scheduled_time=NOW.replace(hour=15))
    r = svc.mark_in_progress(req.id)
    assert not r.ok and r.error is BookingRequestError.INVALID_TRANSITION


def test_mark_in_progress_not_found(svc):
    r = svc.mark_in_progress(404)
    assert not r.ok and r.error is BookingRequestError.NOT_FOUND


def test_record_confirmed_sets_status_and_outcome(svc):
    req = _make(svc)
    when = NOW.replace(hour=15, minute=30)
    r = svc.record_confirmed(req.id, when, confirmation_number="C-1", notes="ok")
    assert r.ok
    assert r.request.status == "confirmed"
    assert r.request.outcome_scheduled_time == when
    assert r.request.outcome_confirmation_number == "C-1"
    assert r.request.outcome_notes == "ok"


def test_record_confirmed_coerces_naive_to_utc(svc):
    req = _make(svc)
    naive = datetime(2026, 6, 1, 15, 30)  # tz-naive
    r = svc.record_confirmed(req.id, naive)
    assert r.ok
    assert r.request.outcome_scheduled_time.tzinfo is not None


def test_record_confirmed_from_terminal_rejected(svc):
    req = _make(svc)
    svc.record_confirmed(req.id, scheduled_time=NOW.replace(hour=15))
    r = svc.record_confirmed(req.id, scheduled_time=NOW.replace(hour=16))
    assert not r.ok and r.error is BookingRequestError.INVALID_TRANSITION


def test_record_declined_sets_reason_in_notes(svc):
    req = _make(svc)
    r = svc.record_declined(req.id, "no afternoon slots this week")
    assert r.ok
    assert r.request.status == "declined"
    assert "afternoon" in r.request.outcome_notes


def test_record_declined_requires_reason(svc):
    req = _make(svc)
    r = svc.record_declined(req.id, "   ")
    assert not r.ok and r.error is BookingRequestError.INVALID_INPUT


def test_record_followup_keeps_request_active(svc):
    req = _make(svc)
    r = svc.record_followup(req.id, "they will call back tomorrow")
    assert r.ok
    assert r.request.status == "in_progress"
    assert "tomorrow" in r.request.outcome_notes

    # Second follow-up still leaves us in_progress + updates notes.
    r2 = svc.record_followup(req.id, "still pending")
    assert r2.ok and r2.request.status == "in_progress"
    assert r2.request.outcome_notes == "still pending"


def test_record_followup_requires_notes(svc):
    req = _make(svc)
    r = svc.record_followup(req.id, "")
    assert not r.ok and r.error is BookingRequestError.INVALID_INPUT


def test_record_followup_rejects_terminal(svc):
    req = _make(svc)
    svc.record_confirmed(req.id, scheduled_time=NOW.replace(hour=15))
    r = svc.record_followup(req.id, "later")
    assert not r.ok and r.error is BookingRequestError.INVALID_TRANSITION


# --------------------------------------------------------------------------- #
# Seed helper
# --------------------------------------------------------------------------- #
def test_seed_test_request_creates_md_aabid_hussain(svc):
    seeded = svc.seed_test_request()
    assert seeded.full_name == SEED_NAME == "Md Aabid Hussain"
    assert seeded.status == "pending"
    assert seeded.appointment_reason == "general health checkup"
    assert seeded.preferred_time_of_day == "afternoon"
    assert seeded.target_hospital_name  # non-empty placeholder


def test_seed_test_request_is_idempotent_while_active(svc):
    first = svc.seed_test_request()
    second = svc.seed_test_request()
    assert first.id == second.id

    # Marking in_progress is still active -> still idempotent.
    svc.mark_in_progress(first.id)
    third = svc.seed_test_request()
    assert third.id == first.id
    assert third.status == "in_progress"


def test_seed_test_request_recreates_after_terminal(svc):
    first = svc.seed_test_request()
    svc.record_confirmed(first.id, NOW.replace(hour=15))
    second = svc.seed_test_request()
    assert second.id != first.id  # fresh seed once the previous reached terminal
    assert second.status == "pending"
