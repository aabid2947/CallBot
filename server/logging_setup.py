"""Centralised logging for the voice-call flow.

Pipecat logs via **loguru** (STT/LLM/TTS, turns, function calls, transport
lifecycle). This module adds a DEBUG file sink under ``logs/`` so the entire
call flow is captured for inspection and post-mortem debugging, and routes
the stdlib ``logging`` (FastAPI/uvicorn + our server) into the same files.

Secret safety: loguru's ``diagnose`` is forced OFF. With it on, exception
frames would dump local variables — which here include GROQ/DEEPGRAM API
keys and DB URLs. ``backtrace`` is kept on (full stack, no variable values),
which is what we actually need to debug errors.
"""

from __future__ import annotations

import logging
import sys
from datetime import datetime, timezone
from pathlib import Path

from loguru import logger

_PROJECT_ROOT = Path(__file__).resolve().parent.parent
_DEFAULT_LOG_DIR = _PROJECT_ROOT / "logs"

_configured = False

_FILE_FORMAT = (
    "{time:YYYY-MM-DD HH:mm:ss.SSS} | {level: <8} | "
    "{name}:{function}:{line} | {message}"
)
_CONSOLE_FORMAT = (
    "<green>{time:HH:mm:ss.SSS}</green> | <level>{level: <8}</level> | "
    "<cyan>{name}</cyan>:<cyan>{line}</cyan> - <level>{message}</level>"
)


class _InterceptHandler(logging.Handler):
    """Send stdlib logging records (uvicorn, fastapi, our `logging`) to loguru."""

    def emit(self, record: logging.LogRecord) -> None:
        try:
            level = logger.level(record.levelname).name
        except ValueError:
            level = record.levelno
        frame, depth = logging.currentframe(), 2
        while frame and frame.f_code.co_filename == logging.__file__:
            frame = frame.f_back
            depth += 1
        logger.opt(depth=depth, exception=record.exc_info).log(
            level, record.getMessage()
        )


def configure_logging(
    log_dir: Path | str | None = None,
    *,
    console_level: str = "INFO",
    file_level: str = "DEBUG",
) -> Path:
    """Configure console + rotating file logging. Idempotent.

    Returns the path of the per-run log file. The console stays readable
    (INFO); the file captures the full DEBUG flow.
    """
    global _configured

    directory = Path(log_dir) if log_dir is not None else _DEFAULT_LOG_DIR
    directory.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    run_file = directory / f"voicestream-{stamp}.log"

    if _configured:
        return run_file

    logger.remove()  # drop Pipecat's default handler; we re-add our own.
    logger.add(
        sys.stderr,
        level=console_level,
        format=_CONSOLE_FORMAT,
        backtrace=True,
        diagnose=False,
    )
    # Per-run file: full DEBUG flow of the whole call.
    logger.add(
        str(run_file),
        level=file_level,
        format=_FILE_FORMAT,
        backtrace=True,
        diagnose=False,  # never dump locals -> never leak API keys
        enqueue=True,  # safe across the pipeline's async tasks/threads
    )
    # Rolling aggregate file so history survives across runs.
    logger.add(
        str(directory / "voicestream.log"),
        level=file_level,
        format=_FILE_FORMAT,
        rotation="20 MB",
        retention="14 days",
        compression="zip",
        backtrace=True,
        diagnose=False,
        enqueue=True,
    )

    # Route stdlib logging (uvicorn/fastapi/our `logging`) into loguru.
    logging.basicConfig(handlers=[_InterceptHandler()], level=0, force=True)
    for name in (
        "uvicorn",
        "uvicorn.error",
        "uvicorn.access",
        "fastapi",
        "aiortc",
        "aioice",
    ):
        lg = logging.getLogger(name)
        lg.handlers = [_InterceptHandler()]
        lg.propagate = False

    _configured = True
    logger.info("Logging configured -> {} (file level={})", run_file, file_level)
    return run_file
