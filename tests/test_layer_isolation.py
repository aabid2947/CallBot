"""Architecture guardrail: dependencies point inward only.

Each check runs in a FRESH subprocess so it tests a module's own import
graph, independent of what other tests in this process have imported.
"""

from __future__ import annotations

import json
import subprocess
import sys

import pytest

_CHECK = """
import sys, json
for _m in {modules!r}:
    __import__(_m)
forbidden = {forbidden!r}
leaked = [m for m in sys.modules
          if any(m == f or m.startswith(f + ".") for f in forbidden)]
print(json.dumps(sorted(leaked)))
"""

CASES = [
    # core must not pull in voice/transport/server/pipecat/web at all.
    (
        "core.booking",
        ["core.booking.models", "core.booking.db", "core.booking.repository",
         "core.booking.proxy_service"],
        ["voice", "transport", "server", "testclient", "pipecat", "fastapi"],
    ),
    (
        "core.agent",
        ["core.agent.prompts", "core.agent.tools", "core.agent.dispatcher"],
        ["voice", "transport", "server", "testclient", "pipecat", "fastapi"],
    ),
    # voice may use pipecat, but must stay free of any web framework / outer layers.
    (
        "voice",
        ["voice.config", "voice.pipeline"],
        ["fastapi", "starlette", "transport", "server", "testclient"],
    ),
    # transport is pure I/O: must never import our core/voice/server/frontend.
    # (fastapi/starlette are intentionally NOT forbidden here: Pipecat's own
    # smallwebrtc request handler imports fastapi.HTTPException — that is a
    # Pipecat dependency, not this layer reaching into our web app.)
    (
        "transport",
        ["transport.web"],
        ["core", "voice", "server", "testclient"],
    ),
]


@pytest.mark.parametrize("label,modules,forbidden", CASES, ids=[c[0] for c in CASES])
def test_layer_has_no_forbidden_imports(label, modules, forbidden):
    code = _CHECK.format(modules=modules, forbidden=forbidden)
    proc = subprocess.run(
        [sys.executable, "-c", code],
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert proc.returncode == 0, f"{label} import failed:\n{proc.stderr}"
    leaked = json.loads(proc.stdout.strip().splitlines()[-1])
    assert leaked == [], f"{label} pulled in forbidden modules: {leaked}"
