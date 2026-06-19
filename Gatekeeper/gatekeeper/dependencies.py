from __future__ import annotations

from typing import Generator

from fastapi import HTTPException, Request, status
from sqlalchemy.orm import Session

from .database import SessionLocal
from .models import User


def get_db() -> Generator[Session, None, None]:
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def push_flash(request: Request, level: str, message: str) -> None:
    flashes = request.session.get('_flashes', [])
    flashes.append({'level': level, 'message': message})
    request.session['_flashes'] = flashes


def pop_flashes(request: Request) -> list[dict[str, str]]:
    flashes = request.session.pop('_flashes', [])
    return flashes if isinstance(flashes, list) else []


def get_current_user(request: Request, db: Session) -> User:
    user_id = request.session.get('user_id')
    if not user_id:
        raise HTTPException(status_code=status.HTTP_303_SEE_OTHER, headers={'Location': '/login'})
    user = db.query(User).filter(User.id == int(user_id), User.is_active.is_(True)).first()
    if user is None:
        request.session.clear()
        raise HTTPException(status_code=status.HTTP_303_SEE_OTHER, headers={'Location': '/login'})
    return user


def get_current_user_optional(request: Request, db: Session) -> User | None:
    user_id = request.session.get('user_id')
    if not user_id:
        return None
    return db.query(User).filter(User.id == int(user_id), User.is_active.is_(True)).first()


def require_admin(request: Request, db: Session) -> User:
    user = get_current_user(request, db)
    if not user.is_admin:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail='Admin access required')
    return user
