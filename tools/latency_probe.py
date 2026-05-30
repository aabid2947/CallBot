"""Real-API latency probe — `python -m tools.latency_probe`.

Measures the three speech/LLM legs that dominate perceived delay, WITHOUT
needing a microphone:

  1. TTS   : Deepgram Aura synthesizes a sentence  -> time to full audio
  2. STT   : that audio is sent back to Deepgram   -> time to transcript
  3. LLM   : Groq answers a booking question       -> time to FIRST token
             (time-to-first-token is what the caller actually feels)

It talks to the real APIs over plain HTTPS (httpx), so it is version-stable.
If keys are missing it prints guidance and exits 0 (so it is safe in CI).

Perceived first-response delay ≈ STT + LLM(first token) + TTS(first audio).
This probe reports full-response times (upper bound); streaming in the live
pipeline makes the felt latency lower. Numbers vary with network/region.
"""

from __future__ import annotations

import sys
import time

import httpx

from voice.config import VoiceSettings

SENTENCE = (
    "Hi, this is Md Aabid Hussain. I'm calling to book a general health "
    "checkup, ideally tomorrow afternoon."
)
LLM_SYSTEM = (
    "You are calling a hospital on behalf of Md Aabid Hussain. Speak AS "
    "Md Aabid Hussain in the first person. Be calm, polite, concise — "
    "spoken-style, one short sentence."
)
LLM_QUESTION = (
    "Hi, thanks for calling City Care Hospital. Can I get your full name "
    "and date of birth, please?"
)


def _probe_tts(s: VoiceSettings) -> tuple[float, bytes]:
    t0 = time.perf_counter()
    r = httpx.post(
        f"https://api.deepgram.com/v1/speak?model={s.tts_voice}",
        headers={"Authorization": f"Token {s.deepgram_api_key}"},
        json={"text": SENTENCE},
        timeout=30,
    )
    r.raise_for_status()
    return (time.perf_counter() - t0) * 1000, r.content


def _probe_stt(s: VoiceSettings, audio: bytes) -> tuple[float, str]:
    t0 = time.perf_counter()
    r = httpx.post(
        f"https://api.deepgram.com/v1/listen?model={s.stt_model}&smart_format=true",
        headers={
            "Authorization": f"Token {s.deepgram_api_key}",
            "Content-Type": "audio/wav",
        },
        content=audio,
        timeout=30,
    )
    r.raise_for_status()
    alts = r.json()["results"]["channels"][0]["alternatives"]
    transcript = alts[0]["transcript"] if alts else ""
    return (time.perf_counter() - t0) * 1000, transcript


def _probe_llm(s: VoiceSettings) -> tuple[float, float]:
    """Return (time-to-first-token ms, total ms) for a streamed completion."""
    t0 = time.perf_counter()
    ttft = None
    with httpx.stream(
        "POST",
        "https://api.groq.com/openai/v1/chat/completions",
        headers={"Authorization": f"Bearer {s.groq_api_key}"},
        json={
            "model": s.llm_model,
            "stream": True,
            "messages": [
                {"role": "system", "content": LLM_SYSTEM},
                {"role": "user", "content": LLM_QUESTION},
            ],
        },
        timeout=30,
    ) as resp:
        resp.raise_for_status()
        for line in resp.iter_lines():
            if line.startswith("data:") and line[5:].strip() not in ("", "[DONE]"):
                if ttft is None:
                    ttft = (time.perf_counter() - t0) * 1000
    total = (time.perf_counter() - t0) * 1000
    return (ttft or total), total


def main() -> int:
    s = VoiceSettings.from_env()
    if not (s.groq_api_key and s.deepgram_api_key):
        print(
            "Skipping latency probe: set GROQ_API_KEY and DEEPGRAM_API_KEY in "
            ".env to measure real round-trip latency.\n"
            "(Free keys: https://console.groq.com , https://console.deepgram.com)"
        )
        return 0

    print(f"Models: LLM={s.llm_model}  STT={s.stt_model}  TTS={s.tts_voice}\n")
    try:
        tts_ms, audio = _probe_tts(s)
        stt_ms, transcript = _probe_stt(s, audio)
        llm_ttft_ms, llm_total_ms = _probe_llm(s)
    except httpx.HTTPStatusError as e:
        print(f"API error ({e.response.status_code}): {e.response.text[:200]}")
        return 1

    perceived = stt_ms + llm_ttft_ms + tts_ms
    print(f"  TTS  (Aura, full audio)      : {tts_ms:7.0f} ms")
    print(f"  STT  (Deepgram, transcript)  : {stt_ms:7.0f} ms"
          f'   -> "{transcript}"')
    print(f"  LLM  (Groq, first token)     : {llm_ttft_ms:7.0f} ms")
    print(f"  LLM  (Groq, full answer)     : {llm_total_ms:7.0f} ms")
    print("  " + "-" * 44)
    print(f"  ~Perceived first-response    : {perceived:7.0f} ms"
          "  (STT + LLM first token + TTS)")
    if perceived > 1500:
        print("\n  NOTE: >1.5s. Try a smaller/faster Groq model, a closer "
              "region, or check your network.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
