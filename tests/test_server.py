"""Server boot + WebRTC negotiation + intake-endpoint tests (Prompts 5/6/10).

No audio is exchanged. We verify:
  - the server boots; /health works; the test client is served
  - bad offers are rejected
  - /api/offer rejects with 503 when no active BookingRequest exists
  - with an active request, a real SDP offer gets a valid SDP answer
  - POST /api/booking_requests (Phase-1 intake hand-off) happy + 400 paths
"""

from __future__ import annotations

import asyncio

import pytest
from aiortc import RTCPeerConnection
from fastapi.testclient import TestClient

from server.app import create_app

_MIN_BOOKING_REQUEST = {
    "full_name": "Md Aabid Hussain",
    "date_of_birth": "2000-01-15",
    "phone": "+91-9876-543210",
    "appointment_reason": "general health checkup",
    "target_hospital_name": "City Care Hospital",
    "preferred_time_of_day": "afternoon",
}


@pytest.fixture(autouse=True)
def dummy_keys(monkeypatch, tmp_path):
    # Lifespan validates keys + inits DB; provide throwaway values.
    monkeypatch.setenv("GROQ_API_KEY", "test-groq")
    monkeypatch.setenv("DEEPGRAM_API_KEY", "test-deepgram")
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{tmp_path / 'srv.db'}")
    # The developer's real .env may have TURN_* set for live testing;
    # strip them here so "default" tests see a clean STUN-only config.
    # Individual tests that need TURN re-set these via monkeypatch.
    for var in ("TURN_URLS", "TURN_USERNAME", "TURN_CREDENTIAL"):
        monkeypatch.delenv(var, raising=False)


@pytest.fixture
def client():
    """TestClient with an EMPTY booking-request table."""
    with TestClient(create_app()) as c:
        yield c


@pytest.fixture
def seeded_client(client):
    """TestClient where a fresh BookingRequest is already pending — i.e. an
    /api/offer would find an active row to bind to."""
    r = client.post("/api/booking_requests", json=_MIN_BOOKING_REQUEST)
    assert r.status_code == 200, r.text
    return client


# --------------------------------------------------------------------------- #
# Basics
# --------------------------------------------------------------------------- #
def test_health(client):
    r = client.get("/health")
    assert r.status_code == 200
    assert r.json() == {"status": "ok"}


def test_bad_offer_is_rejected(client):
    r = client.post("/api/offer", json={"not": "an-offer"})
    assert r.status_code == 400


def test_testclient_page_is_served_at_root(client):
    r = client.get("/")
    assert r.status_code == 200
    assert "text/html" in r.headers["content-type"]
    assert "test client" in r.text.lower()


# --------------------------------------------------------------------------- #
# WebRTC negotiation depends on an active BookingRequest
# --------------------------------------------------------------------------- #
async def _make_browser_like_offer() -> dict:
    pc = RTCPeerConnection()
    pc.addTransceiver("audio", direction="sendrecv")
    offer = await pc.createOffer()
    await pc.setLocalDescription(offer)  # aiortc gathers ICE here (non-trickle)
    desc = pc.localDescription
    result = {"sdp": desc.sdp, "type": desc.type}
    await pc.close()
    return result


def test_offer_without_active_request_returns_503(client):
    offer = asyncio.run(_make_browser_like_offer())
    r = client.post("/api/offer", json=offer)
    assert r.status_code == 503
    assert "booking" in r.json()["detail"].lower()


def test_webrtc_session_can_be_negotiated(seeded_client):
    offer = asyncio.run(_make_browser_like_offer())
    r = seeded_client.post("/api/offer", json=offer)
    assert r.status_code == 200, r.text
    answer = r.json()
    assert answer.get("type") == "answer"
    assert "v=0" in answer.get("sdp", "")  # a real SDP body
    assert answer.get("pc_id")


# --------------------------------------------------------------------------- #
# POST /api/booking_requests (Phase-1 hand-off)
# --------------------------------------------------------------------------- #
def test_create_booking_request_happy_path(client):
    r = client.post("/api/booking_requests", json=_MIN_BOOKING_REQUEST)
    assert r.status_code == 200, r.text
    body = r.json()
    assert isinstance(body["id"], int)
    assert body["status"] == "pending"


@pytest.mark.parametrize(
    "patch, expect_in_detail",
    [
        ({"full_name": ""}, "required"),
        ({"appointment_reason": ""}, "required"),
        ({"phone": ""}, "required"),
        ({"date_of_birth": "not-a-date"}, "ISO date"),
    ],
    ids=["no_name", "no_reason", "no_phone", "bad_dob"],
)
def test_create_booking_request_400_on_bad_input(client, patch, expect_in_detail):
    payload = {**_MIN_BOOKING_REQUEST, **patch}
    r = client.post("/api/booking_requests", json=payload)
    assert r.status_code == 400
    assert expect_in_detail.lower() in r.json()["detail"].lower()


def test_ice_servers_endpoint_returns_stun_by_default(client):
    r = client.get("/api/ice_servers")
    assert r.status_code == 200
    servers = r.json()["iceServers"]
    assert len(servers) == 1
    assert "stun:" in str(servers[0]["urls"])
    assert "username" not in servers[0]


def test_ice_servers_endpoint_includes_turn_when_configured(
    monkeypatch, tmp_path
):
    monkeypatch.setenv("GROQ_API_KEY", "test-groq")
    monkeypatch.setenv("DEEPGRAM_API_KEY", "test-deepgram")
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{tmp_path / 'srv2.db'}")
    monkeypatch.setenv(
        "TURN_URLS", "turn:openrelay.metered.ca:80,turn:openrelay.metered.ca:443"
    )
    monkeypatch.setenv("TURN_USERNAME", "openrelayproject")
    monkeypatch.setenv("TURN_CREDENTIAL", "openrelayproject")
    with TestClient(create_app()) as c:
        servers = c.get("/api/ice_servers").json()["iceServers"]
    assert len(servers) == 2
    turn = servers[1]
    assert turn["username"] == "openrelayproject"
    assert turn["credential"] == "openrelayproject"
    assert "turn:openrelay.metered.ca:80" in str(turn["urls"])


def test_create_booking_request_rejects_bad_optional_date(client):
    payload = {
        **_MIN_BOOKING_REQUEST,
        "preferred_date_window_start": "not-a-date",
    }
    r = client.post("/api/booking_requests", json=payload)
    assert r.status_code == 400
    assert "iso date" in r.json()["detail"].lower()
