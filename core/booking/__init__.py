"""Booking domain: persistence + business logic for the proxy-caller flow.

Transport- and web-agnostic. Public API is re-exported here so callers
import from `core.booking` rather than internal modules.

The agent calls a hospital on behalf of a user; each call is bound to a
`BookingRequest` describing who is calling and what to book. Outcomes
(confirmed / declined / follow-up) land back on the same row.
"""

from .db import init_db, session_scope
from .models import BookingRequest, BookingRequestStatus
from .proxy_service import (
    SEED_NAME,
    VALID_TIMES_OF_DAY,
    BookingRequestError,
    BookingRequestResult,
    BookingRequestService,
    BookingRequestView,
)
from .repository import (
    BookingRequestRepository,
    SqlAlchemyBookingRequestRepository,
)

__all__ = [
    "init_db",
    "session_scope",
    "BookingRequest",
    "BookingRequestStatus",
    "BookingRequestRepository",
    "SqlAlchemyBookingRequestRepository",
    "BookingRequestService",
    "BookingRequestError",
    "BookingRequestResult",
    "BookingRequestView",
    "VALID_TIMES_OF_DAY",
    "SEED_NAME",
]
