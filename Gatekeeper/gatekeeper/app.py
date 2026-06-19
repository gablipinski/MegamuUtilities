from __future__ import annotations

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session
from starlette.middleware.sessions import SessionMiddleware

from .config import ensure_runtime_dirs, settings
from .database import Base, SessionLocal, engine, ensure_runtime_schema
from .license_service import bootstrap_admin_user, seed_default_products
from .routes_admin import register_admin_routes
from .routes_auth import register_auth_routes
from .routes_user import register_user_routes
from .security import hash_password


ensure_runtime_dirs()

app = FastAPI(title='Gatekeeper', version='0.1.0')
app.add_middleware(SessionMiddleware, secret_key=settings.secret_key, same_site='lax')
app.mount('/static', StaticFiles(directory=str(settings.base_dir / 'gatekeeper' / 'static')), name='static')

templates = Jinja2Templates(directory=str(settings.base_dir / 'gatekeeper' / 'templates'))

app.include_router(register_auth_routes(templates))
app.include_router(register_user_routes(templates))
app.include_router(register_admin_routes(templates))


@app.on_event('startup')
def on_startup() -> None:
    Base.metadata.create_all(bind=engine)
    ensure_runtime_schema()
    db: Session = SessionLocal()
    try:
        seed_default_products(db)
        if settings.bootstrap_admin_email and settings.bootstrap_admin_password:
            bootstrap_admin_user(
                db,
                email=settings.bootstrap_admin_email,
                password_hash=hash_password(settings.bootstrap_admin_password),
                display_name='Gatekeeper Admin',
            )
    finally:
        db.close()
