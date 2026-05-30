"""Transport layer — the ONE swappable piece.

Web/WebRTC now. Mobile app reuses this same WebRTC transport unchanged.
Real phone calls (Twilio) become a sibling file here later, with NO
changes to `core/` or `voice/`. See `transport/web.py` SWAP SEAM.

This layer imports Pipecat transport pieces only — never `voice/` or
`core/`. The server depends on the names re-exported here.
"""

from .web import (
    SmallWebRTCConnection,
    SmallWebRTCRequest,
    SmallWebRTCRequestHandler,
    create_web_transport,
    ice_servers_config,
    to_rtc_ice_servers,
    web_transport_params,
)

__all__ = [
    "SmallWebRTCConnection",
    "SmallWebRTCRequest",
    "SmallWebRTCRequestHandler",
    "create_web_transport",
    "ice_servers_config",
    "to_rtc_ice_servers",
    "web_transport_params",
]
