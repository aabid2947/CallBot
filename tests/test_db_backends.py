"""DB backend portability (Supabase/Postgres switch).

No live database is needed: SQLAlchemy does not connect until the engine
is used, so we can assert URL normalisation + engine options statically.
"""

from __future__ import annotations

from core.booking.db import _make_engine, _normalize_url


def test_postgres_scheme_is_normalized():
    assert _normalize_url("postgres://u:p@h:5432/db") == "postgresql://u:p@h:5432/db"
    # Already-correct / other schemes are untouched.
    assert _normalize_url("postgresql://u:p@h/db") == "postgresql://u:p@h/db"
    assert _normalize_url("sqlite:///voicestream.db") == "sqlite:///voicestream.db"


def test_postgres_engine_is_hardened_for_hosted_db():
    eng = _make_engine(
        "postgres://postgres.ref:secret@aws-0-x.pooler.supabase.com:5432/postgres"
    )
    assert eng.dialect.name == "postgresql"        # normalized + psycopg2
    assert eng.pool._pre_ping is True              # validate before use
    assert eng.pool._recycle == 1800               # don't outlive idle drop


def test_sqlite_file_engine_unchanged():
    eng = _make_engine("sqlite:///some.db")
    assert eng.dialect.name == "sqlite"
    assert eng.pool._pre_ping is False             # not applied to sqlite
