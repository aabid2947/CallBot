"""SQLAlchemy ORM models for the booking domain.

Pure persistence types. No business rules here (those live in
`BookingRequestService`) and no web/voice/transport imports.
"""

from __future__ import annotations

import enum
from datetime import date, datetime, timezone

from sqlalchemy import Boolean, Date, DateTime, Enum, Integer, String, Text
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column
from sqlalchemy.types import TypeDecorator


def _utcnow() -> datetime:
    """Timezone-aware UTC now (stored as UTC everywhere)."""
    return datetime.now(timezone.utc)


class UtcDateTime(TypeDecorator):
    """Datetime that is always stored and returned as timezone-aware UTC.

    SQLite has no native tz support and would otherwise return naive
    datetimes. This guarantees the whole core sees aware UTC values
    regardless of backend (SQLite or Postgres/Supabase).
    """

    impl = DateTime(timezone=True)
    cache_ok = True

    def process_bind_param(self, value, dialect):
        if value is None:
            return None
        if value.tzinfo is None:
            value = value.replace(tzinfo=timezone.utc)
        return value.astimezone(timezone.utc)

    def process_result_value(self, value, dialect):
        if value is None:
            return None
        if value.tzinfo is None:
            return value.replace(tzinfo=timezone.utc)
        return value.astimezone(timezone.utc)


class Base(DeclarativeBase):
    """Declarative base for all booking models."""


class BookingRequestStatus(str, enum.Enum):
    """Lifecycle of a single booking request the agent is calling about."""

    PENDING = "pending"           # created by intake; no call attempt yet
    IN_PROGRESS = "in_progress"   # an outbound call session is/was active
    CONFIRMED = "confirmed"       # hospital confirmed a time
    DECLINED = "declined"         # hospital could not accommodate
    FAILED = "failed"             # call could not complete (system error)


class BookingRequest(Base):
    """A user's request for the agent to book an appointment somewhere.

    Single table; outcome columns are embedded for simplicity. If we ever
    need multiple call attempts per request, split outcomes into a child
    table — the schema is forward-compatible.

    Times are timezone-aware UTC via `UtcDateTime`. Pure persistence type:
    no business rules live here (those are in `BookingRequestService`).
    """

    __tablename__ = "booking_requests"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)

    # Identity / contact (what the agent says when posing as the user).
    full_name: Mapped[str] = mapped_column(String(200), nullable=False)
    date_of_birth: Mapped[date] = mapped_column(Date, nullable=False)
    phone: Mapped[str] = mapped_column(String(50), nullable=False)
    email: Mapped[str | None] = mapped_column(String(254), nullable=True)
    address: Mapped[str | None] = mapped_column(String(500), nullable=True)
    insurance_provider: Mapped[str | None] = mapped_column(String(200), nullable=True)
    insurance_member_id: Mapped[str | None] = mapped_column(String(100), nullable=True)
    is_new_patient: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True
    )

    # What the agent is trying to book.
    appointment_reason: Mapped[str] = mapped_column(Text, nullable=False)
    preferred_date_window_start: Mapped[date | None] = mapped_column(
        Date, nullable=True
    )
    preferred_date_window_end: Mapped[date | None] = mapped_column(
        Date, nullable=True
    )
    preferred_time_of_day: Mapped[str] = mapped_column(
        String(20), nullable=False, default="any"
    )  # morning | afternoon | evening | any
    preferred_doctor: Mapped[str | None] = mapped_column(String(200), nullable=True)
    department: Mapped[str | None] = mapped_column(String(200), nullable=True)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    target_hospital_name: Mapped[str | None] = mapped_column(
        String(200), nullable=True
    )

    # Lifecycle + outcome.
    status: Mapped[BookingRequestStatus] = mapped_column(
        Enum(BookingRequestStatus, native_enum=False, length=20),
        nullable=False,
        default=BookingRequestStatus.PENDING,
        index=True,
    )
    outcome_scheduled_time: Mapped[datetime | None] = mapped_column(
        UtcDateTime, nullable=True
    )
    outcome_confirmation_number: Mapped[str | None] = mapped_column(
        String(100), nullable=True
    )
    outcome_notes: Mapped[str | None] = mapped_column(Text, nullable=True)

    created_at: Mapped[datetime] = mapped_column(
        UtcDateTime, nullable=False, default=_utcnow
    )
    updated_at: Mapped[datetime] = mapped_column(
        UtcDateTime, nullable=False, default=_utcnow, onupdate=_utcnow
    )

    def __repr__(self) -> str:  # pragma: no cover - debug aid
        return (
            f"<BookingRequest id={self.id} {self.full_name!r} "
            f"{self.status.value}>"
        )
