"""
Seed a Md Aabid Hussain BookingRequest into the booking DB.

Use this when you don't yet have the Phase-1 intake microservice
posting to /api/booking_requests. Idempotent: if an active request
under that name already exists, the existing row is returned; once it
reaches a terminal state, a fresh PENDING row is created.

Usage (from the project root, same env/DB as the server):
    python ./scripts/seed_test_request.py
"""

from __future__ import annotations

import sys

from core.booking import BookingRequestService, init_db
from core.config import get_settings


def main() -> int:
    init_db()  # honours DATABASE_URL (same as the server)
    view = BookingRequestService().seed_test_request()
    print(f"Seeded booking request into {get_settings().database_url}")
    print(f"  id        : {view.id}")
    print(f"  name      : {view.full_name}")
    print(f"  status    : {view.status}")
    print(f"  reason    : {view.appointment_reason}")
    print(f"  hospital  : {view.target_hospital_name}")
    print(f"  pref. time: {view.preferred_time_of_day}  "
          f"({view.preferred_date_window_start} -> "
          f"{view.preferred_date_window_end})")
    print(
        "\nThe next /api/offer (browser test client) will bind to this row "
        "automatically."
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
