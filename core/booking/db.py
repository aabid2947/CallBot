"""Database engine/session management for the booking domain.

The engine is built from `DATABASE_URL` (via core config), so swapping
SQLite -> Postgres later is a config change, not a code change. The schema
is auto-created on first use.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager

from sqlalchemy import Engine, create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from core.config import get_settings

from .models import Base

_engine: Engine | None = None
_Session: sessionmaker[Session] | None = None


def _normalize_url(database_url: str) -> str:
    """Accept the URL shapes hosted providers hand out.

    Supabase (and others) often print `postgres://...`, but SQLAlchemy 1.4+
    only accepts `postgresql://...`. Normalise it so users can paste the
    connection string verbatim.
    """
    if database_url.startswith("postgres://"):
        return "postgresql://" + database_url[len("postgres://"):]
    return database_url


def _make_engine(database_url: str) -> Engine:
    database_url = _normalize_url(database_url)
    is_sqlite = database_url.startswith("sqlite")
    # `check_same_thread` only applies to SQLite; harmless otherwise.
    connect_args = {"check_same_thread": False} if is_sqlite else {}

    # In-memory SQLite lives inside a single connection. Without a shared
    # static pool, every new session would get its own empty database.
    is_memory = is_sqlite and (
        ":memory:" in database_url or database_url == "sqlite://"
    )
    kwargs: dict = {"future": True, "connect_args": connect_args}
    if is_memory:
        kwargs["poolclass"] = StaticPool
    elif not is_sqlite:
        # Hosted Postgres (e.g. Supabase) drops idle connections and the
        # pooler recycles them. Validate connections before use and don't
        # keep them long enough to be killed mid-flight.
        kwargs["pool_pre_ping"] = True
        kwargs["pool_recycle"] = 1800

    return create_engine(database_url, **kwargs)


def init_db(database_url: str | None = None) -> Engine:
    """Initialise (or re-initialise) the engine and create the schema.

    Safe to call repeatedly. `create_all` is idempotent. Passing an explicit
    `database_url` (e.g. an in-memory SQLite URL in tests) overrides config.
    """
    global _engine, _Session

    url = database_url or get_settings().database_url
    _engine = _make_engine(url)
    _Session = sessionmaker(bind=_engine, expire_on_commit=False, future=True)
    Base.metadata.create_all(_engine)
    return _engine


def get_engine() -> Engine:
    """Return the engine, initialising lazily from config on first use."""
    if _engine is None:
        init_db()
    assert _engine is not None
    return _engine


def get_sessionmaker() -> sessionmaker[Session]:
    """Return the session factory, initialising lazily on first use."""
    if _Session is None:
        init_db()
    assert _Session is not None
    return _Session


@contextmanager
def session_scope() -> Iterator[Session]:
    """Transactional session: commit on success, rollback on error."""
    session = get_sessionmaker()()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()
