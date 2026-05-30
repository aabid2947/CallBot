"""Web (WebRTC) transport adapter — THE swappable I/O layer.

This file is the *only* place that knows how audio physically reaches the
agent. It imports Pipecat transport pieces but NOT `voice/` or `core/`, so
it stays a pure I/O concern. The server wires this to the voice pipeline.

================================ SWAP SEAM ================================
To change how customers connect, you only touch THIS folder — never
`core/` or `voice/`:

  * Mobile app  -> NO change. A mobile client speaks WebRTC just like the
                   browser; reuse `create_web_transport` as-is. Only the
                   client UI differs (that's a frontend, not this layer).

  * Real phone  -> Add a sibling file `transport/twilio.py` that builds a
    (Twilio)       `FastAPIWebsocketTransport` with a `TwilioFrameSerializer`
                   (Pipecat: pipecat.transports.websocket + the Twilio
                   serializer), expose a `create_phone_transport(...)` with
                   the same shape as `create_web_transport`, and point the
                   server's connection handler at it. Audio there is 8 kHz
                   µ-law; set the serializer + sample rate accordingly.
                   `core/` and `voice/` remain untouched.
==========================================================================
"""

from __future__ import annotations

import os

from aiortc.rtcconfiguration import RTCIceServer
from loguru import logger

_VALID_ICE_SCHEMES = ("turn:", "turns:", "stun:", "stuns:")
from pipecat.transports.base_transport import TransportParams
from pipecat.transports.smallwebrtc.connection import SmallWebRTCConnection
from pipecat.transports.smallwebrtc.request_handler import (
    SmallWebRTCRequest,
    SmallWebRTCRequestHandler,
)
from pipecat.transports.smallwebrtc.transport import SmallWebRTCTransport

# Re-exported so the server depends on this layer's API, not Pipecat
# internals directly — keeps the swap localised to this folder.
__all__ = [
    "SmallWebRTCConnection",
    "SmallWebRTCRequest",
    "SmallWebRTCRequestHandler",
    "create_web_transport",
    "ice_servers_config",
    "to_rtc_ice_servers",
    "web_transport_params",
]


_DEFAULT_STUN = "stun:stun.l.google.com:19302"


def ice_servers_config() -> list[dict]:
    """Return WebRTC ICE servers as JSON-shaped dicts (browser + server use the same list).

    Always includes a public STUN server. If `TURN_URLS` is set in the
    environment (comma-separated), adds that TURN entry with optional
    `TURN_USERNAME` / `TURN_CREDENTIAL`. TURN is required for callers on
    NATs / different networks (no relay = no audio across the public
    internet).
    """
    servers: list[dict] = [{"urls": _DEFAULT_STUN}]
    raw = (os.getenv("TURN_URLS") or "").strip()
    if raw:
        urls = [u.strip() for u in raw.split(",") if u.strip()]
        bad = [u for u in urls if not u.lower().startswith(_VALID_ICE_SCHEMES)]
        if bad:
            # Don't silently accept malformed URLs — they would be ignored by
            # both aiortc and the browser, so the call would fail to relay
            # while the server logs "TURN configured". Make it loud.
            logger.error(
                "TURN_URLS contains entries without a turn:/turns:/stun:/stuns: "
                "scheme: {}. Prefix each URL with 'turn:' (or 'turns:' for TLS). "
                "Example: TURN_URLS=turn:relay1.expressturn.com:3478",
                bad,
            )
            urls = [u for u in urls if u not in bad]
        if urls:
            entry: dict = {"urls": urls if len(urls) > 1 else urls[0]}
            if (u := os.getenv("TURN_USERNAME")):
                entry["username"] = u
            if (c := os.getenv("TURN_CREDENTIAL")):
                entry["credential"] = c
            servers.append(entry)
    return servers


def to_rtc_ice_servers(servers: list[dict]) -> list[RTCIceServer]:
    """Convert the JSON config to aiortc's RTCIceServer for Pipecat's handler."""
    return [
        RTCIceServer(
            urls=s["urls"],
            username=s.get("username"),
            credential=s.get("credential"),
        )
        for s in servers
    ]


def web_transport_params() -> TransportParams:
    """Audio-only params tuned for a low-latency voice call.

    No video. `audio_in_passthrough` keeps raw audio flowing to the STT
    service. (Barge-in/VAD tuning is deferred to Prompt 7 hardening.)
    """
    return TransportParams(
        audio_in_enabled=True,
        audio_out_enabled=True,
        audio_in_passthrough=True,
        video_in_enabled=False,
        video_out_enabled=False,
    )


def create_web_transport(
    connection: SmallWebRTCConnection,
) -> SmallWebRTCTransport:
    """Build the Pipecat WebRTC transport for one negotiated connection."""
    return SmallWebRTCTransport(
        webrtc_connection=connection,
        params=web_transport_params(),
    )
