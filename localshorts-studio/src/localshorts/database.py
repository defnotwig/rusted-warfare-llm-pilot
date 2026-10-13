"""SQLite WAL database initialization, session management, and non-destructive schema migrations."""

from __future__ import annotations

from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

from sqlalchemy import event, text
from sqlmodel import Session, SQLModel, create_engine, select

from localshorts.config import ensure_directories, settings
from localshorts.logging_config import get_logger
from localshorts.models.db_models import (
    AppSettings,
    CreatorBaseline,
    SchemaMigration,
    WatchedTopic,
    utc_now_iso,
)

logger = get_logger("database")

DEFAULT_WATCHED_TOPICS: list[dict[str, str]] = [
    {
        "name": "MrBeast",
        "category": "creator",
        "youtube_channel_id": "UCX6OQ3DkcsbYNE6H8uQQuVA",
        "search_query": "MrBeast challenge viral",
    },
    {
        "name": "IShowSpeed",
        "category": "creator",
        "youtube_channel_id": "UCWsDFcIhY2DBi3GB5uykGXA",
        "search_query": "IShowSpeed stream viral",
    },
    {
        "name": "Kai Cenat",
        "category": "creator",
        "youtube_channel_id": "UCvC4D8onUfXzvjTOM-dBfEA",
        "search_query": "Kai Cenat stream event",
    },
    {
        "name": "Sidemen",
        "category": "creator",
        "youtube_channel_id": "UCDogdKl7t7NHzQ95aEwkdMw",
        "search_query": "Sidemen Sunday challenge",
    },
    {
        "name": "AMP",
        "category": "creator",
        "youtube_channel_id": "UCJbYdyufHR-cxOuY96KIoqA",
        "search_query": "AMP group creator video",
    },
    {
        "name": "AI & Humanoid Robots",
        "category": "tech",
        "youtube_channel_id": "",
        "search_query": "humanoid robot AI demonstration breakthrough",
    },
    {
        "name": "Viral Creator Challenges",
        "category": "event",
        "youtube_channel_id": "",
        "search_query": "YouTuber world record challenge viral moment",
    },
    {
        "name": "Unusual Viral Technology",
        "category": "tech",
        "youtube_channel_id": "",
        "search_query": "viral technology invention demonstration",
    },
]

_ENGINE = None
_CURRENT_DB_PATH: Path | None = None


def get_engine(db_path: Path | None = None):
    """Return or create the SQLite engine with WAL mode enabled."""
    global _ENGINE, _CURRENT_DB_PATH
    target_path = (db_path or settings.db_path).resolve()
    if _ENGINE is not None and _CURRENT_DB_PATH == target_path:
        return _ENGINE

    ensure_directories()
    target_path.parent.mkdir(parents=True, exist_ok=True)
    sqlite_url = f"sqlite:///{target_path.as_posix()}"
    engine = create_engine(
        sqlite_url,
        connect_args={"check_same_thread": False, "timeout": 30},
        echo=False,
    )

    @event.listens_for(engine, "connect")
    def _set_sqlite_pragma(dbapi_connection, _connection_record):
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA journal_mode=WAL;")
        cursor.execute("PRAGMA synchronous=NORMAL;")
        cursor.execute("PRAGMA foreign_keys=ON;")
        cursor.close()

    _ENGINE = engine
    _CURRENT_DB_PATH = target_path
    return _ENGINE


def reset_engine_for_tests(db_path: Path) -> None:
    """Point database engine at an isolated test SQLite file."""
    global _ENGINE, _CURRENT_DB_PATH
    if _ENGINE is not None:
        _ENGINE.dispose()
    _ENGINE = None
    _CURRENT_DB_PATH = None
    settings.db_path = db_path
    get_engine(db_path)


@contextmanager
def get_session(db_path: Path | None = None) -> Iterator[Session]:
    """Yield a managed SQLModel session."""
    engine = get_engine(db_path)
    with Session(engine, expire_on_commit=False) as session:
        yield session


def _run_forward_migrations(session: Session) -> None:
    """Apply non-destructive forward schema migrations and record version."""
    migrations = [
        (1, "Initial LocalShorts Studio v0.1.0 schema (WAL, 9 core tables)"),
        (2, "Ensure outlier_multiplier and source_confidence columns exist on trend_items & short_jobs"),
    ]
    for version, description in migrations:
        existing = session.get(SchemaMigration, version)
        if not existing:
            if version == 2:
                # Safe idempotent column additions if upgrading an older database
                for stmt in (
                    "ALTER TABLE trend_items ADD COLUMN outlier_multiplier REAL DEFAULT 1.0",
                    "ALTER TABLE trend_items ADD COLUMN source_confidence INTEGER DEFAULT 70",
                    "ALTER TABLE short_jobs ADD COLUMN source_confidence INTEGER DEFAULT 80",
                ):
                    try:
                        session.exec(text(stmt))
                    except Exception:
                        pass
            session.add(
                SchemaMigration(
                    version=version,
                    applied_at=utc_now_iso(),
                    description=description,
                )
            )
    session.commit()


def init_db(db_path: Path | None = None) -> None:
    """Initialize SQLite tables, run forward migrations, and seed default settings/topics if empty."""
    engine = get_engine(db_path)
    SQLModel.metadata.create_all(engine)

    with get_session(db_path) as session:
        _run_forward_migrations(session)

        # Ensure singleton AppSettings row (id=1) exists
        app_cfg = session.get(AppSettings, 1)
        if not app_cfg:
            app_cfg = AppSettings(id=1)
            session.add(app_cfg)
            session.commit()

        # Seed default watched topics if table is empty
        existing_topics = session.exec(select(WatchedTopic)).first()
        if not existing_topics:
            for item in DEFAULT_WATCHED_TOPICS:
                session.add(
                    WatchedTopic(
                        name=item["name"],
                        category=item["category"],
                        youtube_channel_id=item["youtube_channel_id"],
                        search_query=item["search_query"],
                        enabled=True,
                    )
                )
                if item["category"] == "creator":
                    if not session.get(CreatorBaseline, item["name"]):
                        session.add(
                            CreatorBaseline(
                                creator_name=item["name"],
                                typical_views_1h=80000.0,
                                typical_views_24h=750000.0,
                                typical_mentions_1h=2.0,
                                sample_count=3,
                            )
                        )
            session.commit()
    logger.info("SQLite database initialized and verified at %s", db_path or settings.db_path)


def get_app_settings(db_path: Path | None = None) -> AppSettings:
    """Retrieve the persisted AppSettings singleton."""
    with get_session(db_path) as session:
        cfg = session.get(AppSettings, 1)
        if not cfg:
            init_db(db_path)
            cfg = session.get(AppSettings, 1)
        assert cfg is not None
        return cfg
