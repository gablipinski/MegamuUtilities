from __future__ import annotations

from sqlalchemy import create_engine, text
from sqlalchemy.orm import declarative_base, sessionmaker

from .config import ensure_runtime_dirs, settings


ensure_runtime_dirs()

SQLALCHEMY_DATABASE_URL = f'sqlite:///{settings.db_path.as_posix()}'

engine = create_engine(
    SQLALCHEMY_DATABASE_URL,
    connect_args={'check_same_thread': False},
)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()


def ensure_runtime_schema() -> None:
    """Apply lightweight schema upgrades for SQLite deployments without migrations."""
    with engine.begin() as conn:
        columns = {
            row[1]
            for row in conn.execute(text("PRAGMA table_info(users)"))
        }
        if 'admin_totp_enabled' not in columns:
            conn.execute(text("ALTER TABLE users ADD COLUMN admin_totp_enabled BOOLEAN DEFAULT 0"))
        if 'admin_totp_secret' not in columns:
            conn.execute(text("ALTER TABLE users ADD COLUMN admin_totp_secret VARCHAR(64)"))
