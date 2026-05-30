"""Pipecat pipeline factory.

Assembles: transport.in -> Deepgram STT -> Groq LLM (with core tools) ->
Deepgram Aura TTS -> transport.out, with a shared LLM context.

Transport-agnostic by design: the transport is **injected**. This module
never constructs a web/WebRTC/Twilio transport and imports no web framework
(that is the `transport/` + `server/` layers' job, Prompt 5).
"""

from __future__ import annotations

from datetime import datetime
from typing import Protocol, runtime_checkable

from pipecat.adapters.schemas.function_schema import FunctionSchema
from pipecat.adapters.schemas.tools_schema import ToolsSchema
from pipecat.pipeline.pipeline import Pipeline
from pipecat.pipeline.task import PipelineParams, PipelineTask
from pipecat.processors.aggregators.llm_response_universal import (
    LLMContextAggregatorPair,
)
from pipecat.processors.frame_processor import FrameProcessor
from pipecat.services.deepgram.stt import DeepgramSTTService
from pipecat.services.deepgram.tts import DeepgramTTSService
from pipecat.services.groq.llm import GroqLLMService
from pipecat.services.llm_service import FunctionCallParams, LLMContext

from core.agent import TOOL_SCHEMAS, ToolDispatcher, build_system_prompt
from core.booking import BookingRequestService

from .config import VoiceSettings, load_voice_settings


@runtime_checkable
class TransportLike(Protocol):
    """Minimal structural contract for an injected transport.

    Keeping this structural (not a Pipecat base class) is what keeps the
    voice layer transport-agnostic and unit-testable with a stub.
    """

    def input(self) -> FrameProcessor: ...
    def output(self) -> FrameProcessor: ...


def _function_schemas() -> list[FunctionSchema]:
    """Convert core's OpenAI-style tool schemas into Pipecat FunctionSchemas."""
    schemas: list[FunctionSchema] = []
    for tool in TOOL_SCHEMAS:
        fn = tool["function"]
        params = fn["parameters"]
        schemas.append(
            FunctionSchema(
                name=fn["name"],
                description=fn["description"],
                properties=params.get("properties", {}),
                required=params.get("required", []),
            )
        )
    return schemas


def _register_tools(llm: GroqLLMService, dispatcher: ToolDispatcher) -> None:
    """Wire every core tool to the dispatcher via one async handler."""

    async def handler(params: FunctionCallParams) -> None:
        # dispatcher never raises and returns a JSON-serialisable dict,
        # so the model can always recover and keep talking.
        result = dispatcher.dispatch(params.function_name, params.arguments)
        await params.result_callback(result)

    for tool in TOOL_SCHEMAS:
        llm.register_function(tool["function"]["name"], handler)


def build_services(
    settings: VoiceSettings,
) -> tuple[DeepgramSTTService, GroqLLMService, DeepgramTTSService]:
    """Construct the STT/LLM/TTS services tuned for low latency."""
    stt = DeepgramSTTService(
        api_key=settings.deepgram_api_key,
        settings=DeepgramSTTService.Settings(
            model=settings.stt_model,
            language="en-US",
            interim_results=True,  # stream partials -> faster turn-taking
            smart_format=True,
        ),
    )
    llm = GroqLLMService(
        api_key=settings.groq_api_key,
        settings=GroqLLMService.Settings(model=settings.llm_model),
    )
    tts = DeepgramTTSService(
        api_key=settings.deepgram_api_key,
        settings=DeepgramTTSService.Settings(voice=settings.tts_voice),
    )
    return stt, llm, tts


def build_pipeline_task(
    transport: TransportLike,
    *,
    booking_request_id: int,
    settings: VoiceSettings | None = None,
    booking_requests: BookingRequestService | None = None,
    caller_name: str | None = None,
    target_hospital_name: str | None = None,
    now: datetime | None = None,
) -> PipelineTask:
    """Build a ready-to-run PipelineTask around an injected transport.

    `booking_request_id` is REQUIRED: it identifies the BookingRequest the
    agent will represent for this entire call. The server (Prompt 10)
    resolves it via `BookingRequestService.latest_active()` and passes it,
    together with `caller_name` + `target_hospital_name` from the same
    row, so the persona is correctly personalised. Voice itself does not
    query the DB during build — the dispatcher's tools do, at call time.

    Pure assembly — nothing is started here, so it can still be tested in
    isolation with a stub transport and any int id.

    Barge-in / interruptions are on by default in Pipecat; effective
    barge-in additionally requires the injected transport to provide a VAD
    analyzer (Silero). That is configured on the transport in Prompt 5.
    """
    settings = settings or load_voice_settings()
    dispatcher = ToolDispatcher(
        booking_requests or BookingRequestService(),
        booking_request_id=booking_request_id,
    )

    stt, llm, tts = build_services(settings)
    _register_tools(llm, dispatcher)

    context = LLMContext(
        messages=[
            {
                "role": "system",
                "content": build_system_prompt(
                    caller_name=caller_name,
                    target_hospital_name=target_hospital_name or settings.business_name,
                    now=now,
                ),
            }
        ],
        tools=ToolsSchema(standard_tools=_function_schemas()),
    )
    aggregators = LLMContextAggregatorPair(context)

    pipeline = Pipeline(
        [
            transport.input(),
            stt,
            aggregators.user(),
            llm,
            tts,
            transport.output(),
            aggregators.assistant(),
        ]
    )

    return PipelineTask(
        pipeline,
        params=PipelineParams(
            enable_metrics=True,
            enable_usage_metrics=True,
            report_only_initial_ttfb=True,
        ),
        # The test client is audio-only and opens no WebRTC data channel.
        # We don't use RTVI; without this Pipecat floods logs with
        # "Data channel not ready, queuing message" and a 10s warning.
        enable_rtvi=False,
    )
