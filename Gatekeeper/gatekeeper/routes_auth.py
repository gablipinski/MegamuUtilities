from __future__ import annotations

import base64
import io

import pyotp
import qrcode

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session

from .admin_access import can_admin_login_from_request
from .dependencies import get_db, get_current_user_optional, pop_flashes, push_flash
from .models import User
from .security import hash_password, verify_password


router = APIRouter()


def _clear_admin_2fa_session(request: Request) -> None:
    request.session.pop('pending_admin_user_id', None)
    request.session.pop('pending_admin_totp_secret', None)


def _complete_login(request: Request, user: User) -> RedirectResponse:
    _clear_admin_2fa_session(request)
    request.session['user_id'] = user.id
    push_flash(request, 'success', 'Login successful.')
    return RedirectResponse('/admin' if user.is_admin else '/app', status_code=303)


def _get_pending_admin(request: Request, db: Session) -> User | None:
    pending_id = request.session.get('pending_admin_user_id')
    if not pending_id:
        return None
    return db.query(User).filter(User.id == int(pending_id), User.is_admin.is_(True), User.is_active.is_(True)).first()


def _build_qr_code_data_uri(payload: str) -> str:
    qr = qrcode.QRCode(version=1, box_size=6, border=2)
    qr.add_data(payload)
    qr.make(fit=True)
    image = qr.make_image(fill_color='black', back_color='white')
    buffer = io.BytesIO()
    image.save(buffer, format='PNG')
    encoded = base64.b64encode(buffer.getvalue()).decode('ascii')
    return f'data:image/png;base64,{encoded}'


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

            _clear_admin_2fa_session(request)
            request.session['pending_admin_user_id'] = user.id
            if user.admin_totp_enabled and user.admin_totp_secret:
                return RedirectResponse('/admin/2fa/verify', status_code=303)
            request.session['pending_admin_totp_secret'] = pyotp.random_base32()
            return RedirectResponse('/admin/2fa/setup', status_code=303)

        return _complete_login(request, user)

    @router.get('/admin/2fa/setup')
    def admin_2fa_setup_page(request: Request, db: Session = Depends(get_db)):
        user = _get_pending_admin(request, db)
        if user is None:
            push_flash(request, 'error', 'Start login again to continue admin verification.')
            return RedirectResponse('/login', status_code=303)
        if user.admin_totp_enabled and user.admin_totp_secret:
            return RedirectResponse('/admin/2fa/verify', status_code=303)

        secret = request.session.get('pending_admin_totp_secret')
        if not secret:
            secret = pyotp.random_base32()
            request.session['pending_admin_totp_secret'] = secret
        otp_uri = pyotp.TOTP(secret).provisioning_uri(name=user.email, issuer_name='Gatekeeper')
        qr_code_data_uri = _build_qr_code_data_uri(otp_uri)
        return templates.TemplateResponse(
            request,
            'admin_2fa_setup.html',
            {
                'page_title': 'Admin 2FA Setup',
                'otp_secret': secret,
                'otp_uri': otp_uri,
                'qr_code_data_uri': qr_code_data_uri,
                'flashes': pop_flashes(request),
            },
        )

    @router.post('/admin/2fa/setup')
    def admin_2fa_setup_submit(
        request: Request,
        otp_code: str = Form(...),
        db: Session = Depends(get_db),
    ):
        user = _get_pending_admin(request, db)
        if user is None:
            push_flash(request, 'error', 'Start login again to continue admin verification.')
            return RedirectResponse('/login', status_code=303)

        secret = request.session.get('pending_admin_totp_secret')
        if not secret:
            push_flash(request, 'error', '2FA setup session expired. Please sign in again.')
            return RedirectResponse('/login', status_code=303)

        normalized_code = ''.join(ch for ch in otp_code if ch.isdigit())
        if not pyotp.TOTP(secret).verify(normalized_code, valid_window=1):
            push_flash(request, 'error', 'Invalid 2FA code. Check your Authenticator app and try again.')
            return RedirectResponse('/admin/2fa/setup', status_code=303)

        user.admin_totp_secret = secret
        user.admin_totp_enabled = True
        db.commit()

        push_flash(request, 'success', 'Admin 2FA enabled successfully.')
        return _complete_login(request, user)

    @router.get('/admin/2fa/verify')
    def admin_2fa_verify_page(request: Request, db: Session = Depends(get_db)):
        user = _get_pending_admin(request, db)
        if user is None:
            push_flash(request, 'error', 'Start login again to continue admin verification.')
            return RedirectResponse('/login', status_code=303)
        if not user.admin_totp_enabled or not user.admin_totp_secret:
            return RedirectResponse('/admin/2fa/setup', status_code=303)
        return templates.TemplateResponse(
            request,
            'admin_2fa_verify.html',
            {
                'page_title': 'Admin 2FA Verification',
                'flashes': pop_flashes(request),
            },
        )

    @router.post('/admin/2fa/verify')
    def admin_2fa_verify_submit(
        request: Request,
        otp_code: str = Form(...),
        db: Session = Depends(get_db),
    ):
        user = _get_pending_admin(request, db)
        if user is None:
            push_flash(request, 'error', 'Start login again to continue admin verification.')
            return RedirectResponse('/login', status_code=303)
        if not user.admin_totp_enabled or not user.admin_totp_secret:
            return RedirectResponse('/admin/2fa/setup', status_code=303)

        normalized_code = ''.join(ch for ch in otp_code if ch.isdigit())
        if not pyotp.TOTP(user.admin_totp_secret).verify(normalized_code, valid_window=1):
            push_flash(request, 'error', 'Invalid 2FA code. Please try again.')
            return RedirectResponse('/admin/2fa/verify', status_code=303)

        return _complete_login(request, user)

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
