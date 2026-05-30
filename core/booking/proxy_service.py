"""Booking-request business logic (proxy caller).

`BookingRequestService` owns the rules for the user's booking requests:
who can transition to what state, what the agent records when it returns
from a call, and a seed helper for local development. Returns
**structured results** (no strings as the API surface, no ORM leakage) so
any caller — voice, the Phase-1 intake microservice, tests — can consume
it without depending on SQLAlchemy.
"""

from __future__ import annotations

import enum
from collections.abc import Callable
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone

from .models import BookingRequest, BookingRequestStatus
from .repository import (
    BookingRequestRepository,
    SqlAlchemyBookingRequestRepository,
)


VALID_TIMES_OF_DAY = ("morning", "afternoon", "evening", "any")
SEED_NAME = "Md Aabid Hussain"
_ACTIVE_STATUSES = (
    BookingRequestStatus.PENDING,
    BookingRequestStatus.IN_PROGRESS,
)


# --------------------------------------------------------------------------- #
# Structured result types
# --------------------------------------------------------------------------- #
class BookingRequestError(str, enum.Enum):
    INVALID_INPUT = "invalid_input"
    NOT_FOUND = "not_found"
    INVALID_TRANSITION = "invalid_transition"


@dataclass(frozen=True)
class BookingRequestView:
    """Plain serialisable snapshot of a BookingRequest (no ORM coupling)."""

    id: int
    full_name: str
    date_of_birth: date
    phone: str
    email: str | None
    address: str | None
    insurance_provider: str | None
    insurance_member_id: str | None
    is_new_patient: bool
    appointment_reason: str
    preferred_date_window_start: date | None
    preferred_date_window_end: date | None
    preferred_time_of_day: str
    preferred_doctor: str | None
    department: str | None
    notes: str | None
    target_hospital_name: str | None
    status: str
    outcome_scheduled_time: datetime | None
    outcome_confirmation_number: str | None
    outcome_notes: str | None

    @staticmethod
    def of(r: BookingRequest) -> "BookingRequestView":
        return BookingRequestView(
            id=r.id,
            full_name=r.full_name,
            date_of_birth=r.date_of_birth,
            phone=r.phone,
            email=r.email,
            address=r.address,
            insurance_provider=r.insurance_provider,
            insurance_member_id=r.insurance_member_id,
            is_new_patient=r.is_new_patient,
            appointment_reason=r.appointment_reason,
            preferred_date_window_start=r.preferred_date_window_start,
            preferred_date_window_end=r.preferred_date_window_end,
            preferred_time_of_day=r.preferred_time_of_day,
            preferred_doctor=r.preferred_doctor,
            department=r.department,
            notes=r.notes,
            target_hospital_name=r.target_hospital_name,
            status=r.status.value,
            outcome_scheduled_time=r.outcome_scheduled_time,
            outcome_confirmation_number=r.outcome_confirmation_number,
            outcome_notes=r.outcome_notes,
        )


@dataclass(frozen=True)
class BookingRequestResult:
    """Outcome of a BookingRequestService operation."""

    ok: bool
    error: BookingRequestError | None = None
    message: str = ""
    request: BookingRequestView | None = None

    @staticmethod
    def success(request: BookingRequestView, message: str = "") -> "BookingRequestResult":
        return BookingRequestResult(ok=True, message=message, request=request)

    @staticmethod
    def failure(error: BookingRequestError, message: str) -> "BookingRequestResult":
        return BookingRequestResult(ok=False, error=error, message=message)


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _aware(dt: datetime) -> datetime:
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


# --------------------------------------------------------------------------- #
# Service
# --------------------------------------------------------------------------- #
class BookingRequestService:
    """All booking-request business rules. Transport-agnostic.

    The repository is constructor-injected for testability; the default is
    the SQLAlchemy implementation, so `BookingRequestService()` Just Works
    after `init_db()`.
    """

    def __init__(
        self,
        requests: BookingRequestRepository | None = None,
        now_fn: Callable[[], datetime] = _utcnow,
    ) -> None:
        self._reqs = requests or SqlAlchemyBookingRequestRepository()
        self._now = now_fn

    # ---- create / read --------------------------------------------------- #
    def create(
        self,
        *,
        full_name: str,
        date_of_birth: date,
        phone: str,
        appointment_reason: str,
        email: str | None = None,
        address: str | None = None,
        insurance_provider: str | None = None,
        insurance_member_id: str | None = None,
        is_new_patient: bool = True,
        preferred_date_window_start: date | None = None,
        preferred_date_window_end: date | None = None,
        preferred_time_of_day: str = "any",
        preferred_doctor: str | None = None,
        department: str | None = None,
        notes: str | None = None,
        target_hospital_name: str | None = None,
    ) -> BookingRequestResult:
        name = (full_name or "").strip()
        if not name:
            return BookingRequestResult.failure(
                BookingRequestError.INVALID_INPUT, "A full name is required."
            )
        if not isinstance(date_of_birth, date):
            return BookingRequestResult.failure(
                BookingRequestError.INVALID_INPUT,
                "Date of birth is required (date).",
            )
        if not (phone or "").strip():
            return BookingRequestResult.failure(
                BookingRequestError.INVALID_INPUT, "A phone number is required."
            )
        if not (appointment_reason or "").strip():
            return BookingRequestResult.failure(
                BookingRequestError.INVALID_INPUT,
                "An appointment reason is required.",
            )
        tod = (preferred_time_of_day or "any").lower()
        if tod not in VALID_TIMES_OF_DAY:
            return BookingRequestResult.failure(
                BookingRequestError.INVALID_INPUT,
                f"preferred_time_of_day must be one of {VALID_TIMES_OF_DAY}.",
            )
        if (
            preferred_date_window_start
            and preferred_date_window_end
            and preferred_date_window_end < preferred_date_window_start
        ):
            return BookingRequestResult.failure(
                BookingRequestError.INVALID_INPUT,
                "Preferred date window end is before its start.",
            )

        request = BookingRequest(
            full_name=name,
            date_of_birth=date_of_birth,
            phone=phone.strip(),
            email=email,
            address=address,
            insurance_provider=insurance_provider,
            insurance_member_id=insurance_member_id,
            is_new_patient=is_new_patient,
            appointment_reason=appointment_reason.strip(),
            preferred_date_window_start=preferred_date_window_start,
            preferred_date_window_end=preferred_date_window_end,
            preferred_time_of_day=tod,
            preferred_doctor=preferred_doctor,
            department=department,
            notes=notes,
            target_hospital_name=target_hospital_name,
        )
        created = self._reqs.add(request)
        return BookingRequestResult.success(
            BookingRequestView.of(created), "Booking request created."
        )

    def get(self, request_id: int) -> BookingRequestView | None:
        row = self._reqs.get(request_id)
        return BookingRequestView.of(row) if row is not None else None

    def latest_active(self) -> BookingRequestView | None:
        row = self._reqs.latest_active()
        return BookingRequestView.of(row) if row is not None else None

    # ---- transitions ----------------------------------------------------- #
    def _require_active(
        self, request_id: int
    ) -> tuple[BookingRequest | None, BookingRequestResult | None]:
        """Fetch + assert the row is in an active status. Returns
        (row, error_result_or_None)."""
        row = self._reqs.get(request_id)
        if row is None:
            return None, BookingRequestResult.failure(
                BookingRequestError.NOT_FOUND, "No booking request with that id."
            )
        if row.status not in _ACTIVE_STATUSES:
            return None, BookingRequestResult.failure(
                BookingRequestError.INVALID_TRANSITION,
                f"Cannot transition from status '{row.status.value}'.",
            )
        return row, None

    def mark_in_progress(self, request_id: int) -> BookingRequestResult:
        row, err = self._require_active(request_id)
        if err is not None:
            return err
        assert row is not None
        if row.status is BookingRequestStatus.IN_PROGRESS:
            return BookingRequestResult.success(
                BookingRequestView.of(row), "Already in progress."
            )
        updated = self._reqs.update_status(
            request_id, BookingRequestStatus.IN_PROGRESS
        )
        assert updated is not None
        return BookingRequestResult.success(
            BookingRequestView.of(updated), "Marked in progress."
        )

    def record_confirmed(
        self,
        request_id: int,
        scheduled_time: datetime,
        confirmation_number: str | None = None,
        notes: str | None = None,
    ) -> BookingRequestResult:
        row, err = self._require_active(request_id)
        if err is not None:
            return err
        scheduled = _aware(scheduled_time)
        updated = self._reqs.record_outcome(
            request_id,
            scheduled_time=scheduled,
            confirmation_number=confirmation_number,
            notes=notes,
            status=BookingRequestStatus.CONFIRMED,
        )
        assert updated is not None
        return BookingRequestResult.success(
            BookingRequestView.of(updated), "Appointment confirmed."
        )

    def record_declined(
        self, request_id: int, reason: str
    ) -> BookingRequestResult:
        if not (reason or "").strip():
            return BookingRequestResult.failure(
                BookingRequestError.INVALID_INPUT,
                "A decline reason is required.",
            )
        row, err = self._require_active(request_id)
        if err is not None:
            return err
        updated = self._reqs.record_outcome(
            request_id,
            notes=reason.strip(),
            status=BookingRequestStatus.DECLINED,
        )
        assert updated is not None
        return BookingRequestResult.success(
            BookingRequestView.of(updated), "Appointment declined."
        )

    def record_followup(
        self, request_id: int, notes: str
    ) -> BookingRequestResult:
        """Receptionist gave no resolution (e.g. 'we'll call back'). Keep
        the request active (IN_PROGRESS) and stash notes for the next try."""
        if not (notes or "").strip():
            return BookingRequestResult.failure(
                BookingRequestError.INVALID_INPUT, "Follow-up notes are required."
            )
        row, err = self._require_active(request_id)
        if err is not None:
            return err
        updated = self._reqs.record_outcome(
            request_id,
            notes=notes.strip(),
            status=BookingRequestStatus.IN_PROGRESS,
        )
        assert updated is not None
        return BookingRequestResult.success(
            BookingRequestView.of(updated), "Follow-up recorded."
        )

    # ---- dev convenience ------------------------------------------------- #
    def seed_test_request(self) -> BookingRequestView:
        """Insert the **Md Aabid Hussain** stub for local Phase-2 testing.

        Idempotent in the common dev case: if an active request with the
        same full name exists, return it; otherwise create one. Once the
        seeded row reaches a terminal state (CONFIRMED/DECLINED/FAILED), a
        subsequent call creates a fresh active row.
        """
        current = self._reqs.latest_active()
        if current is not None and current.full_name == SEED_NAME:
            return BookingRequestView.of(current)

        today = self._now().date()
        result = self.create(
            full_name=SEED_NAME,
            date_of_birth=date(2000, 1, 15),
            phone="+91-9876-543210",
            email="md.aabid.test@example.com",
            address="221B Baker Street, Bengaluru, Karnataka",
            insurance_provider="Test Insurance Co.",
            insurance_member_id="TIC-TEST-00001",
            is_new_patient=True,
            appointment_reason="general health checkup",
            preferred_date_window_start=today,
            preferred_date_window_end=today + timedelta(days=7),
            preferred_time_of_day="afternoon",
            target_hospital_name="City Care Hospital",
            notes="Seeded test request — Phase 2 dev only.",
        )
        # create() only fails on invalid input, which is fully controlled here.
        assert result.ok and result.request is not None
        return result.request
