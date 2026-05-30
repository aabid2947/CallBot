"""
Run the VoiceStream project AND expose it publicly in one command.

This starts the server (`python -m server`) as a child process, waits for
it to become healthy, then opens an ngrok HTTPS tunnel to it and prints the
public URLs. Ctrl+C stops both the tunnel and the server.

Usage:
    python ./scripts/tunnel.py
    (or: .venv\\Scripts\\python.exe scripts\\tunnel.py)

Requirements:
    - NGROK_AUTHTOKEN set (in .env or the environment).
      Free token: https://dashboard.ngrok.com/get-started/your-authtoken
    - GROQ_API_KEY and DEEPGRAM_API_KEY in .env (the server fails fast on
      startup without them — this script will tell you if that happens).

NOTE on WebRTC over a tunnel (read this):
    ngrok forwards the HTTP **signaling** (the page + POST /api/offer), so
    the test client loads and negotiates through the public URL. But WebRTC
    *media* (the actual audio) is peer-to-peer UDP and does NOT flow through
    ngrok. Across different networks/NATs the audio may fail to connect
    unless a TURN server is configured. For a reliable test on ONE machine
    you don't need a tunnel at all -- just open http://localhost:8000/
    (browsers allow the mic on localhost). Use the tunnel for same-Wi-Fi
    phone testing or sharing a quick demo.
"""

from __future__ import annotations

import os
import signal
import subprocess
import sys
import time

try:
    # Load .env so NGROK_AUTHTOKEN / API keys / PORT live in one place.
    from dotenv import load_dotenv

    load_dotenv()
except ImportError:  # dotenv is a dev convenience only
    pass

import httpx
from pyngrok import conf, ngrok

# Match the server's port resolution (server/__main__.py defaults to 8000).
PORT = int(os.environ.get("PORT", "8000"))
HEALTH_URL = f"http://127.0.0.1:{PORT}/health"
# The server imports Pipecat on startup, which is slow (~10-20s observed,
# slower on first run / modest machines). Keep this generous.
SERVER_BOOT_TIMEOUT_S = 120


def _wait_for_server(proc: subprocess.Popen) -> bool:
    """Poll /health until the server is up. Returns False if it died first."""
    deadline = time.time() + SERVER_BOOT_TIMEOUT_S
    while time.time() < deadline:
        if proc.poll() is not None:
            # Server exited during startup (most commonly: missing API keys).
            print(
                f"\n[tunnel] The server exited during startup (code "
                f"{proc.returncode}).\n"
                "[tunnel] Most likely GROQ_API_KEY / DEEPGRAM_API_KEY are not "
                "set in .env.\n"
                "[tunnel] Fix .env and re-run. (See the server log above.)",
                file=sys.stderr,
            )
            return False
        try:
            if httpx.get(HEALTH_URL, timeout=2).status_code == 200:
                return True
        except httpx.HTTPError:
            pass
        time.sleep(1)
    print(
        f"[tunnel] Server did not become healthy within "
        f"{SERVER_BOOT_TIMEOUT_S}s.",
        file=sys.stderr,
    )
    return False


def main() -> int:
    token = os.environ.get("NGROK_AUTHTOKEN")
    if not token:
        print(
            "[tunnel] NGROK_AUTHTOKEN is not set.\n"
            "         1. Sign up (free): https://dashboard.ngrok.com/signup\n"
            "         2. Copy your authtoken from the dashboard\n"
            "         3. Put NGROK_AUTHTOKEN=<your-token> in .env\n"
            '            (or in PowerShell: $env:NGROK_AUTHTOKEN = "<token>")\n'
            "         Then re-run this script.",
            file=sys.stderr,
        )
        return 1

    conf.get_default().auth_token = token

    # 1. Start the project (server inherits this console for its logs).
    print(f"[tunnel] starting the server on port {PORT} (python -m server) ...")
    server = subprocess.Popen(
        [sys.executable, "-m", "server"],
        cwd=os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
        env={**os.environ, "PORT": str(PORT)},
    )

    public_url: str | None = None
    stop = {"flag": False}

    def handle(_sig, _frame):
        stop["flag"] = True

    signal.signal(signal.SIGINT, handle)
    signal.signal(signal.SIGTERM, handle)

    try:
        # 2. Wait until it is actually serving.
        if not _wait_for_server(server):
            return 1
        print("[tunnel] server is healthy.")

        # 3. Expose it. Use the IP, NOT "localhost": on some machines the
        # ngrok agent cannot DNS-resolve "localhost" (ERR_NGROK_8012:
        # "lookup localhost ... no such host"). 127.0.0.1 needs no DNS.
        upstream = f"127.0.0.1:{PORT}"
        print(f"[tunnel] opening ngrok HTTPS tunnel -> http://{upstream} ...")
        tunnel = ngrok.connect(upstream, "http")
        public_url = tunnel.public_url
        if public_url.startswith("http://"):
            public_url = "https://" + public_url[len("http://"):]

        print("-" * 45)
        print(f"[tunnel] public URL    : {public_url}")
        print(f"[tunnel] test client   : {public_url}/")
        print(f"[tunnel] health check  : {public_url}/health")
        print(f"[tunnel] WebRTC offer  : {public_url}/api/offer  (POST)")
        print(f"[tunnel] Open {public_url}/ on your phone, click Talk.")
        print("[tunnel] If audio never connects across networks, that is the")
        print("[tunnel] WebRTC-media/NAT caveat (see this file's docstring).")
        print("-" * 45)
        print("[tunnel] LIVE. Press Ctrl+C to stop the tunnel and the server.")

        # 4. Stay up until Ctrl+C, or until the server dies on its own.
        while not stop["flag"]:
            if server.poll() is not None:
                print(
                    f"\n[tunnel] server stopped unexpectedly (code "
                    f"{server.returncode}). Shutting down.",
                    file=sys.stderr,
                )
                return 1
            time.sleep(1)
        return 0
    finally:
        print("\n[tunnel] shutting down ...")
        try:
            if public_url:
                ngrok.disconnect(public_url)
        except Exception:
            pass
        ngrok.kill()
        if server.poll() is None:
            server.terminate()
            try:
                server.wait(timeout=10)
            except subprocess.TimeoutExpired:
                server.kill()
        print("[tunnel] done.")


if __name__ == "__main__":
    sys.exit(main())
