"""Logging setup contract (does not mutate global sinks during the suite).

We assert the wiring contract only — creating the log dir and returning a
timestamped run-file path — without reconfiguring loguru/stdlib globally
(server.app already configures real logging at import time; reconfiguring
here would destabilise other tests).
"""

from __future__ import annotations

import re

from server.logging_setup import configure_logging


def test_creates_log_dir_and_returns_run_file(tmp_path):
    run_file = configure_logging(log_dir=tmp_path)
    assert run_file.parent == tmp_path
    assert tmp_path.is_dir()
    assert re.fullmatch(r"voicestream-\d{8}-\d{6}\.log", run_file.name)


def test_idempotent(tmp_path):
    a = configure_logging(log_dir=tmp_path)
    b = configure_logging(log_dir=tmp_path)
    # Same contract on repeat calls; never raises, always returns a path.
    assert a.parent == tmp_path and b.parent == tmp_path
    assert a.name.startswith("voicestream-") and b.name.startswith("voicestream-")
