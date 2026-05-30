"""Repository abstraction for the booking domain.

Callers depend on the abstract `BookingRequestRepository`, never on
SQLAlchemy directly, so the storage engine can be swapped without
touching the service layer. Data-access only: no business rules
(transitions, validation, etc.) live here — those belong to the
`BookingRequestService` in `proxy_service.py`.
"""

from __future__ import annotations

import abc
from datetime import datetime

from sqlalchemy import select

from .db import session_scope
from .models import BookingRequest, BookingRequestStatus


class BookingRequestRepository(abc.ABC):
    """Storage-agnostic access for the user's booking requests."""

    @abc.abstractmethod
    def add(self, request: BookingRequest) -> BookingRequest: ...

    @abc.abstractmethod
    def get(self, request_id: int) -> BookingRequest | None: ...

    @abc.abstractmethod
    def list_pending(self) -> list[BookingRequest]: ...

    @abc.abstractmethod
    def latest_active(self) -> BookingRequest | None:
        """Most recent request in PENDING or IN_PROGRESS (the one a fresh
        outbound call should bind to). None if there is no active request."""

    @abc.abstractmethod
    def update_status(
        self, request_id: int, status: BookingRequestStatus
    ) -> BookingRequest | None: ...

    @abc.abstractmethod
    def record_outcome(
        self,
        request_id: int,
        *,
        scheduled_time: datetime | None = None,
        confirmation_number: str | None = None,
        notes: str | None = None,
        status: BookingRequestStatus | None = None,
    ) -> BookingRequest | None:
        """Set outcome fields (any subset). If `status` is given, transition
        atomically in the same transaction so a row never sits with an
        outcome but a stale status."""


class SqlAlchemyBookingRequestRepository(BookingRequestRepository):
    """SQLAlchemy-backed booking-request repository (data access only)."""

    def add(self, request: BookingRequest) -> BookingRequest:
        with session_scope() as s:
            s.add(request)
            s.flush()  # populate the autoincrement id
        return request

    def get(self, request_id: int) -> BookingRequest | None:
        with session_scope() as s:
            return s.get(BookingRequest, request_id)

    def list_pending(self) -> list[BookingRequest]:
        stmt = (
            select(BookingRequest)
            .where(BookingRequest.status == BookingRequestStatus.PENDING)
            .order_by(BookingRequest.created_at)
        )
        with session_scope() as s:
            return list(s.scalars(stmt).all())

    def latest_active(self) -> BookingRequest | None:
        stmt = (
            select(BookingRequest)
            .where(
                BookingRequest.status.in_(
                    [
                        BookingRequestStatus.PENDING,
                        BookingRequestStatus.IN_PROGRESS,
                    ]
                )
            )
            .order_by(BookingRequest.created_at.desc())
            .limit(1)
        )
        with session_scope() as s:
            return s.scalars(stmt).first()

    def update_status(
        self, request_id: int, status: BookingRequestStatus
    ) -> BookingRequest | None:
        with session_scope() as s:
            request = s.get(BookingRequest, request_id)
            if request is None:
                return None
            request.status = status
            s.flush()
            return request

    def record_outcome(
        self,
        request_id: int,
        *,
        scheduled_time: datetime | None = None,
        confirmation_number: str | None = None,
        notes: str | None = None,
        status: BookingRequestStatus | None = None,
    ) -> BookingRequest | None:
        with session_scope() as s:
            request = s.get(BookingRequest, request_id)
            if request is None:
                return None
            if scheduled_time is not None:
                request.outcome_scheduled_time = scheduled_time
            if confirmation_number is not None:
                request.outcome_confirmation_number = confirmation_number
            if notes is not None:
                request.outcome_notes = notes
            if status is not None:
                request.status = status
            s.flush()
            return request
