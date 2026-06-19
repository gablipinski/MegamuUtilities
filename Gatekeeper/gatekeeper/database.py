from __future__ import annotations

from sqlalchemy import create_engine
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
