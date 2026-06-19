from __future__ import annotations

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session

from .admin_access import can_admin_login_from_request
from .dependencies import get_db, get_current_user_optional, pop_flashes, push_flash
from .models import User
from .security import hash_password, verify_password


router = APIRouter()


def register_auth_routes(templates: Jinja2Templates) -> APIRouter:
    @router.get('/login')
    def login_page(request: Request, db: Session = Depends(get_db)):
        current_user = get_current_user_optional(request, db)
        if current_user is not None:
            return RedirectResponse('/app', status_code=303)
        return templates.TemplateResponse(
            request,
            'login.html',
            {'flashes': pop_flashes(request), 'page_title': 'Login'},
        )

    @router.post('/login')
    def login_submit(
        request: Request,
        email: str = Form(...),
        password: str = Form(...),
        db: Session = Depends(get_db),
    ):
        normalized_login = email.strip().lower()
        user = db.query(User).filter(User.email == normalized_login).first()
        if user is None or not user.is_active or not verify_password(password, user.password_hash):
            push_flash(request, 'error', 'Invalid email or password.')
            return RedirectResponse('/login', status_code=303)

        if user.is_admin:
            allowed, message = can_admin_login_from_request(request)
            if not allowed:
                push_flash(request, 'error', message)
                return RedirectResponse('/login', status_code=303)

        request.session['user_id'] = user.id
        push_flash(request, 'success', 'Login successful.')
        return RedirectResponse('/admin' if user.is_admin else '/app', status_code=303)

    @router.get('/register')
    def register_page(request: Request, db: Session = Depends(get_db)):
        current_user = get_current_user_optional(request, db)
        if current_user is not None:
            return RedirectResponse('/app', status_code=303)
        return templates.TemplateResponse(
            request,
            'register.html',
            {'flashes': pop_flashes(request), 'page_title': 'Register'},
        )

    @router.post('/register')
    def register_submit(
        request: Request,
        display_name: str = Form(...),
        email: str = Form(...),
        password: str = Form(...),
        db: Session = Depends(get_db),
    ):
        normalized_email = email.strip().lower()
        if db.query(User).filter(User.email == normalized_email).first() is not None:
            push_flash(request, 'error', 'An account with this email already exists.')
            return RedirectResponse('/register', status_code=303)

        user = User(
            display_name=display_name.strip() or normalized_email,
            email=normalized_email,
            password_hash=hash_password(password),
            is_admin=False,
            is_active=True,
        )
        db.add(user)
        db.commit()
        request.session['user_id'] = user.id
        push_flash(request, 'success', 'Account created successfully.')
        return RedirectResponse('/app', status_code=303)

    @router.post('/logout')
    def logout(request: Request):
        request.session.clear()
        push_flash(request, 'success', 'You have been logged out.')
        return RedirectResponse('/login', status_code=303)

    return router
