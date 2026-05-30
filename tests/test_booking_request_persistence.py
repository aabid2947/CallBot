"""BookingRequest persistence-layer tests (Prompt 8).

In-memory SQLite, fresh schema per test. Pure data access — no business
rules are exercised here.
"""

from __future__ import annotations

from datetime import date, datetime, timezone

import pytest

from core.booking import (
    BookingRequest,
    BookingRequestStatus,
    SqlAlchemyBookingRequestRepository,
    init_db,
)

UTC = timezone.utc


def _row(**overrides) -> BookingRequest:
    """Build a BookingRequest with sensible required-field defaults."""
    defaults = dict(
        full_name="Alice Example",
        date_of_birth=date(1990, 1, 1),
        phone="+11111",
        appointment_reason="general checkup",
    )
    defaults.update(overrides)
    return BookingRequest(**defaults)


@pytest.fixture(autouse=True)
def fresh_db():
    init_db("sqlite://")
    yield


@pytest.fixture
def repo() -> SqlAlchemyBookingRequestRepository:
    return SqlAlchemyBookingRequestRepository()


def test_add_assigns_id_and_get_round_trips(repo):
    created = repo.add(_row(full_name="Md Aabid Hussain", phone="+91-9876-543210"))
    assert created.id is not None
    assert created.status is BookingRequestStatus.PENDING  # column default

    fetched = repo.get(created.id)
    assert fetched is not None
    assert fetched.full_name == "Md Aabid Hussain"
    assert fetched.phone == "+91-9876-543210"
    assert fetched.appointment_reason == "general checkup"


def test_get_missing_returns_none(repo):
    assert repo.get(999) is None


def test_list_pending_orders_by_created_at_and_filters(repo):
    a = repo.add(_row(full_name="A"))
    b = repo.add(_row(full_name="B"))
    repo.update_status(a.id, BookingRequestStatus.CONFIRMED)

    pending = repo.list_pending()
    assert [r.full_name for r in pending] == ["B"]


def test_update_status_returns_updated_or_none(repo):
    a = repo.add(_row(full_name="X"))
    moved = repo.update_status(a.id, BookingRequestStatus.IN_PROGRESS)
    assert moved is not None
    assert moved.status is BookingRequestStatus.IN_PROGRESS

    assert repo.update_status(404, BookingRequestStatus.CONFIRMED) is None


def test_record_outcome_writes_specified_fields(repo):
    a = repo.add(_row(full_name="O"))
    scheduled = datetime(2026, 6, 5, 14, 0, tzinfo=UTC)
    updated = repo.record_outcome(
        a.id,
        scheduled_time=scheduled,
        confirmation_number="ACME-001",
        notes="see receptionist",
    )
    assert updated is not None
    assert updated.outcome_scheduled_time == scheduled
    assert updated.outcome_confirmation_number == "ACME-001"
    assert updated.outcome_notes == "see receptionist"
    # No status passed -> status unchanged.
    assert updated.status is BookingRequestStatus.PENDING


def test_record_outcome_with_status_flips_atomically(repo):
    a = repo.add(_row(full_name="Atomic"))
    updated = repo.record_outcome(
        a.id,
        scheduled_time=datetime(2026, 6, 5, 14, 0, tzinfo=UTC),
        status=BookingRequestStatus.CONFIRMED,
    )
    assert updated is not None
    assert updated.status is BookingRequestStatus.CONFIRMED
    assert updated.outcome_scheduled_time is not None


def test_record_outcome_missing_id_returns_none(repo):
    assert repo.record_outcome(404, notes="anything") is None


def test_latest_active_picks_most_recent_active(repo):
    assert repo.latest_active() is None  # empty -> None

    older = repo.add(_row(full_name="Older"))
    newer = repo.add(_row(full_name="Newer"))
    # newer should win (most recent by created_at).
    latest = repo.latest_active()
    assert latest is not None and latest.id == newer.id

    # Once newer is terminal, older becomes the active winner.
    repo.update_status(newer.id, BookingRequestStatus.CONFIRMED)
    latest = repo.latest_active()
    assert latest is not None and latest.id == older.id


def test_latest_active_ignores_terminal_states(repo):
    a = repo.add(_row(full_name="Done"))
    repo.update_status(a.id, BookingRequestStatus.DECLINED)
    assert repo.latest_active() is None


def test_latest_active_includes_in_progress(repo):
    a = repo.add(_row(full_name="Active"))
    repo.update_status(a.id, BookingRequestStatus.IN_PROGRESS)
    latest = repo.latest_active()
    assert latest is not None and latest.id == a.id
