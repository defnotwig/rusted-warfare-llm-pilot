"""Integration test for SQLite WAL initialization, forward migrations, and default topic seeding."""

from __future__ import annotations

from pathlib import Path

from sqlalchemy import text
from sqlmodel import select

from localshorts.database import get_app_settings, get_session, init_db, reset_engine_for_tests
from localshorts.models.db_models import CreatorBaseline, SchemaMigration, WatchedTopic


def test_sqlite_wal_and_forward_migrations(tmp_path: Path) -> None:
    db_file = tmp_path / "integration_test.db"
    reset_engine_for_tests(db_file)
    init_db(db_file)

    cfg = get_app_settings(db_file)
    assert cfg.id == 1
    assert cfg.ollama_num_ctx == 4096

    with get_session(db_file) as session:
        wal_mode = session.exec(text("PRAGMA journal_mode;")).scalar()
        assert str(wal_mode).lower() == "wal"

        migrations = list(session.exec(select(SchemaMigration)).all())
        assert len(migrations) >= 2

        topics = list(session.exec(select(WatchedTopic)).all())
        assert len(topics) >= 5
        names = {t.name for t in topics}
        assert "MrBeast" in names
        assert "IShowSpeed" in names

        baselines = list(session.exec(select(CreatorBaseline)).all())
        assert len(baselines) >= 3
