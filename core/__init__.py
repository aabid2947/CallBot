"""VoiceStream reusable core.

This package is the transport-agnostic, web-agnostic microservice core:
the booking domain, persistence, and the agent brain. It must NOT import
anything from `voice/`, `transport/`, `server/`, or `testclient/`.

Another project can depend on this package alone.
"""

__version__ = "0.0.1"
