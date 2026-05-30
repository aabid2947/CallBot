"""Print Groq rate-limit headers for the configured GROQ_API_KEY.

Sends one tiny chat-completion request and prints all x-ratelimit-* response
headers, so you can see remaining tokens/requests before exposing the agent
to a live tester. No tokens of meaningful size are consumed.

Usage:
    .\\.venv\\Scripts\\python.exe -m tools.groq_limits
    .\\.venv\\Scripts\\python.exe -m tools.groq_limits llama-3.1-8b-instant
"""

from __future__ import annotations

import os
import sys

import httpx
from dotenv import load_dotenv

DEFAULT_MODEL = "llama-3.3-70b-versatile"


def main() -> int:
    load_dotenv()
    key = os.environ.get("GROQ_API_KEY")
    if not key:
        print("GROQ_API_KEY not set (.env missing or empty)")
        return 2

    model = sys.argv[1] if len(sys.argv) > 1 else DEFAULT_MODEL
    print(f"Probing Groq rate limits for model: {model}")

    resp = httpx.post(
        "https://api.groq.com/openai/v1/chat/completions",
        headers={
            "Authorization": f"Bearer {key}",
            "Content-Type": "application/json",
        },
        json={
            "model": model,
            "messages": [{"role": "user", "content": "hi"}],
            "max_tokens": 5,
        },
        timeout=15.0,
    )

    print(f"HTTP {resp.status_code}")
    rate = {k: v for k, v in resp.headers.items() if "ratelimit" in k.lower()}
    if not rate:
        print("(no x-ratelimit-* headers in response)")
    else:
        width = max(len(k) for k in rate)
        for k in sorted(rate):
            print(f"  {k.ljust(width)}  {rate[k]}")

    if resp.status_code >= 400:
        print("\nResponse body:")
        print(resp.text)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
