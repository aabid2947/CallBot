"""FastAPI server: wires transport -> voice pipeline -> core.

This is the outermost layer (app glue for THIS project's web test
deployment). It is the only place the transport, the voice pipeline, and
the booking DB are assembled together. Swapping transports (mobile/phone)
only changes the `transport/` import + connection factory used here.

Logging: `configure_logging()` runs at import so the ENTIRE call flow
(this module + Pipecat's loguru output) is written to `logs/`. Each call
is tagged with its WebRTC `pc_id` so a session is easy to follow / debug.
"""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from datetime import date
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from loguru import logger
from pipecat.frames.frames import LLMRunFrame
from pipecat.pipeline.runner import PipelineRunner

from core.booking import BookingRequestService, init_db
from server.logging_setup import configure_logging
from transport import (
    SmallWebRTCConnection,
    SmallWebRTCRequest,
    SmallWebRTCRequestHandler,
    create_web_transport,
    ice_servers_config,
    to_rtc_ice_servers,
)
from voice import build_pipeline_task, load_voice_settings

# Configure logging as early as possible so nothing in the flow is missed.
_LOG_FILE = configure_logging()

_TESTCLIENT_DIR = Path(__file__).resolve().parent.parent / "testclient"


@asynccontextmanager
async def _lifespan(app: FastAPI):
    logger.info("=== VoiceStream server starting === (log file: {})", _LOG_FILE)
    # Fail fast on missing keys / DB before accepting any traffic.
    try:
        settings = load_voice_settings()
    except Exception:
        logger.opt(exception=True).error(
            "Startup aborted: voice settings invalid (missing API keys?)"
        )
        raise
    init_db()
    app.state.settings = settings
    app.state.requests = BookingRequestService()  # shared; repo is stateless
    app.state.ice_servers = ice_servers_config()
    app.state.webrtc = SmallWebRTCRequestHandler(
        ice_servers=to_rtc_ice_servers(app.state.ice_servers),
    )
    app.state.sessions: set[asyncio.Task] = set()
    turn_count = sum(
        1 for s in app.state.ice_servers
        if str(s.get("urls", "")).startswith("turn")
        or any(str(u).startswith("turn") for u in (s.get("urls") if isinstance(s.get("urls"), list) else []))
    )
    logger.info(
        "Server ready | LLM={} | STT={} | TTS={} | ICE: {} STUN, {} TURN",
        settings.llm_model,
        settings.stt_model,
        settings.tts_voice,
        len(app.state.ice_servers) - turn_count,
        turn_count,
    )
    if turn_count == 0:
        logger.warning(
            "No TURN server configured (TURN_URLS unset). Cross-network "
            "callers may fail to establish audio. See instruction.md."
        )
    try:
        yield
    finally:
        logger.info("Server shutting down; cancelling {} live session(s)",
                    len(app.state.sessions))
        for t in list(app.state.sessions):
            t.cancel()
        await app.state.webrtc.close()
        logger.info("=== VoiceStream server stopped ===")


def create_app() -> FastAPI:
    app = FastAPI(title="VoiceStream", lifespan=_lifespan)

    def _make_connection_handler(
        *,
        request_id: int,
        caller_name: str,
        target_hospital_name: str | None,
    ):
        """Closure that binds an active BookingRequest to a fresh call."""

        async def _on_new_connection(connection: SmallWebRTCConnection) -> None:
            pc_id = getattr(connection, "pc_id", "?")
            call = logger.bind(pc_id=pc_id, req_id=request_id)
            try:
                call.info(
                    "[call {} req={}] new WebRTC connection; building pipeline",
                    pc_id, request_id,
                )
                transport = create_web_transport(connection)
                task = build_pipeline_task(
                    transport,
                    booking_request_id=request_id,
                    settings=app.state.settings,
                    booking_requests=app.state.requests,
                    caller_name=caller_name,
                    target_hospital_name=target_hospital_name,
                )

                @transport.event_handler("on_client_connected")
                async def _greet(_t, _client):
                    call.info(
                        "[call {} req={}] client connected; sending greeting",
                        pc_id, request_id,
                    )
                    await task.queue_frames([LLMRunFrame()])

                @transport.event_handler("on_client_disconnected")
                async def _bye(_t, _client):
                    call.info(
                        "[call {} req={}] client disconnected",
                        pc_id, request_id,
                    )

                runner = PipelineRunner(handle_sigint=False)
                run_task = asyncio.create_task(runner.run(task))
                app.state.sessions.add(run_task)

                def _done(t: asyncio.Task) -> None:
                    app.state.sessions.discard(t)
                    if t.cancelled():
                        call.info(
                            "[call {} req={}] pipeline cancelled (shutdown/idle)",
                            pc_id, request_id,
                        )
                    elif t.exception() is not None:
                        call.opt(exception=t.exception()).error(
                            "[call {} req={}] pipeline task CRASHED",
                            pc_id, request_id,
                        )
                    else:
                        call.info(
                            "[call {} req={}] pipeline finished cleanly",
                            pc_id, request_id,
                        )

                run_task.add_done_callback(_done)
                call.info(
                    "[call {} req={}] pipeline running", pc_id, request_id
                )
            except Exception:
                call.opt(exception=True).error(
                    "[call {} req={}] failed to start pipeline for connection",
                    pc_id, request_id,
                )
                raise

        return _on_new_connection

    @app.get("/health")
    async def health() -> JSONResponse:
        return JSONResponse({"status": "ok"})

    @app.get("/api/ice_servers")
    async def ice_servers() -> dict[str, Any]:
        """ICE servers (STUN + optional TURN) for the browser test client.

        The client fetches this and passes it to RTCPeerConnection so its
        candidate gathering matches the server's — essential for callers
        on remote networks, where TURN is needed to relay audio.
        """
        return {"iceServers": app.state.ice_servers}

    @app.post("/api/offer")
    async def offer(body: dict) -> dict:
        """WebRTC SDP offer -> answer. Non-trickle (client sends full SDP).

        On a FRESH negotiation we bind the agent to the most recent active
        `BookingRequest` (so the agent knows whose info to use). If there
        is none, return 503 with guidance — the agent has nothing to call
        about until a request is seeded or POSTed.
        """
        logger.info("/api/offer received (type={})", body.get("type"))
        try:
            request = SmallWebRTCRequest.from_dict(body)
        except Exception as exc:  # malformed offer
            logger.warning("Rejected malformed offer: {}", exc)
            raise HTTPException(status_code=400, detail=f"Bad offer: {exc}")

        incoming_pc_id = body.get("pc_id")
        if not incoming_pc_id:
            # Fresh negotiation -> resolve + claim the active request now.
            active = app.state.requests.latest_active()
            if active is None:
                msg = (
                    "No active booking request. POST one to "
                    "/api/booking_requests or run scripts/seed_test_request.py."
                )
                logger.warning(msg)
                raise HTTPException(status_code=503, detail=msg)
            progress = app.state.requests.mark_in_progress(active.id)
            if not progress.ok:
                logger.error(
                    "Failed to mark request {} in progress: {}",
                    active.id, progress.message,
                )
                raise HTTPException(status_code=503, detail=progress.message)
            on_connection = _make_connection_handler(
                request_id=active.id,
                caller_name=active.full_name,
                target_hospital_name=active.target_hospital_name,
            )
            logger.info(
                "/api/offer bound to booking_request id={} ({})",
                active.id, active.full_name,
            )
        else:
            # Renegotiation of an existing peer -> the handler reuses the
            # existing connection and does not invoke our callback. Pass a
            # no-op closure since the binding already happened on the
            # original offer.
            async def on_connection(_conn):  # pragma: no cover - not invoked
                return None

        try:
            answer = await app.state.webrtc.handle_web_request(
                request, on_connection
            )
        except Exception:
            logger.opt(exception=True).error("WebRTC negotiation failed")
            raise HTTPException(status_code=500, detail="WebRTC negotiation failed")
        if answer is None:
            logger.error("WebRTC handler produced no SDP answer")
            raise HTTPException(status_code=500, detail="No SDP answer produced")
        logger.info("/api/offer answered (pc_id={})", answer.get("pc_id"))
        return answer

    @app.post("/api/booking_requests")
    async def create_booking_request(body: dict[str, Any]) -> dict[str, Any]:
        """Phase-1 hand-off: another microservice POSTs a BookingRequest here.

        Minimal validation; the BookingRequestService enforces the rest and
        returns a structured failure we surface as HTTP 400.
        """
        full_name = (body.get("full_name") or "").strip()
        appointment_reason = (body.get("appointment_reason") or "").strip()
        dob_raw = body.get("date_of_birth")
        phone = (body.get("phone") or "").strip()
        if not (full_name and appointment_reason and dob_raw and phone):
            raise HTTPException(
                status_code=400,
                detail="full_name, appointment_reason, date_of_birth, and phone are required.",
            )
        try:
            dob = date.fromisoformat(dob_raw)
        except (TypeError, ValueError):
            raise HTTPException(
                status_code=400,
                detail="date_of_birth must be an ISO date (YYYY-MM-DD).",
            )

        def _parse_optional_date(field: str) -> date | None:
            raw = body.get(field)
            if raw in (None, ""):
                return None
            try:
                return date.fromisoformat(raw)
            except (TypeError, ValueError):
                raise HTTPException(
                    status_code=400,
                    detail=f"{field} must be an ISO date (YYYY-MM-DD).",
                )

        result = app.state.requests.create(
            full_name=full_name,
            date_of_birth=dob,
            phone=phone,
            appointment_reason=appointment_reason,
            email=body.get("email"),
            address=body.get("address"),
            insurance_provider=body.get("insurance_provider"),
            insurance_member_id=body.get("insurance_member_id"),
            is_new_patient=bool(body.get("is_new_patient", True)),
            preferred_date_window_start=_parse_optional_date(
                "preferred_date_window_start"
            ),
            preferred_date_window_end=_parse_optional_date(
                "preferred_date_window_end"
            ),
            preferred_time_of_day=body.get("preferred_time_of_day", "any"),
            preferred_doctor=body.get("preferred_doctor"),
            department=body.get("department"),
            notes=body.get("notes"),
            target_hospital_name=body.get("target_hospital_name"),
        )
        if not result.ok or result.request is None:
            logger.warning(
                "Rejected booking request: {} ({})",
                result.message, result.error,
            )
            raise HTTPException(status_code=400, detail=result.message)
        logger.info(
            "New booking request id={} for {!r}",
            result.request.id, result.request.full_name,
        )
        return {"id": result.request.id, "status": result.request.status}

    # Serve the throwaway test client (Prompt 6) if present. Mounted last so
    # it never shadows the API routes above.
    if _TESTCLIENT_DIR.is_dir():
        app.mount(
            "/",
            StaticFiles(directory=str(_TESTCLIENT_DIR), html=True),
            name="testclient",
        )

    return app


app = create_app()
