from __future__ import annotations

import re
from datetime import date
from pathlib import Path

from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session

from .config import settings
from .dependencies import get_db, pop_flashes, push_flash, require_admin
from .license_service import approve_request, latest_releases_by_product, reject_request, sanitize_segment
from .models import IssuedLicense, LicenseRequest, Product, ProductRelease, User


router = APIRouter(prefix='/admin')


def register_admin_routes(templates: Jinja2Templates) -> APIRouter:
    @router.get('')
    def admin_dashboard(request: Request, db: Session = Depends(get_db)):
        admin_user = require_admin(request, db)
        pending_requests = (
            db.query(LicenseRequest)
            .filter(LicenseRequest.status == 'pending')
            .order_by(LicenseRequest.created_at.asc())
            .all()
        )
        recent_requests = (
            db.query(LicenseRequest)
            .order_by(LicenseRequest.created_at.desc())
            .limit(20)
            .all()
        )
        recent_licenses = (
            db.query(IssuedLicense)
            .order_by(IssuedLicense.created_at.desc())
            .limit(20)
            .all()
        )
        products = db.query(Product).filter(Product.is_active.is_(True)).order_by(Product.display_name.asc()).all()
        users = db.query(User).order_by(User.created_at.desc()).limit(30).all()
        latest_releases = latest_releases_by_product(db)
        return templates.TemplateResponse(
            request,
            'admin_dashboard.html',
            {
                'page_title': 'Admin Dashboard',
                'current_user': admin_user,
                'pending_requests': pending_requests,
                'recent_requests': recent_requests,
                'recent_licenses': recent_licenses,
                'products': products,
                'users': users,
                'latest_releases': latest_releases,
                'flashes': pop_flashes(request),
            },
        )

    @router.post('/requests/{request_id}/approve')
    def approve_license_request(
        request_id: int,
        request: Request,
        expiry_date: str = Form(''),
        admin_note: str = Form(''),
        db: Session = Depends(get_db),
    ):
        admin_user = require_admin(request, db)
        request_row = db.query(LicenseRequest).filter(LicenseRequest.id == request_id).first()
        if request_row is None:
            push_flash(request, 'error', 'Request not found.')
            return RedirectResponse('/admin', status_code=303)

        parsed_expiry = None
        if expiry_date.strip():
            try:
                parsed_expiry = date.fromisoformat(expiry_date.strip())
            except ValueError:
                push_flash(request, 'error', 'Invalid expiry date format. Use YYYY-MM-DD.')
                return RedirectResponse('/admin', status_code=303)

        try:
            approve_request(
                db,
                request_row=request_row,
                admin=admin_user,
                expiry_date=parsed_expiry,
                admin_note=admin_note,
            )
        except Exception as exc:
            push_flash(request, 'error', f'Could not approve request: {exc}')
            return RedirectResponse('/admin', status_code=303)

        push_flash(request, 'success', 'Request approved and license generated.')
        return RedirectResponse('/admin', status_code=303)

    @router.post('/requests/{request_id}/reject')
    def reject_license_request(
        request_id: int,
        request: Request,
        admin_note: str = Form(...),
        db: Session = Depends(get_db),
    ):
        admin_user = require_admin(request, db)
        request_row = db.query(LicenseRequest).filter(LicenseRequest.id == request_id).first()
        if request_row is None:
            push_flash(request, 'error', 'Request not found.')
            return RedirectResponse('/admin', status_code=303)
        reject_request(db, request_row=request_row, admin=admin_user, admin_note=admin_note)
        push_flash(request, 'success', 'Request rejected.')
        return RedirectResponse('/admin', status_code=303)

    @router.post('/users/{user_id}/toggle-admin')
    def toggle_admin(user_id: int, request: Request, db: Session = Depends(get_db)):
        admin_user = require_admin(request, db)
        target = db.query(User).filter(User.id == user_id).first()
        if target is None:
            push_flash(request, 'error', 'User not found.')
            return RedirectResponse('/admin', status_code=303)
        if target.id == admin_user.id:
            push_flash(request, 'error', 'You cannot change your own admin flag here.')
            return RedirectResponse('/admin', status_code=303)
        target.is_admin = not target.is_admin
        db.commit()
        push_flash(request, 'success', f'Updated admin status for {target.email}.')
        return RedirectResponse('/admin', status_code=303)

    @router.post('/releases')
    async def upload_release(
        request: Request,
        product_id: int = Form(...),
        version: str = Form(...),
        notes: str = Form(''),
        is_latest: str = Form('on'),
        installer_file: UploadFile = File(...),
        db: Session = Depends(get_db),
    ):
        require_admin(request, db)
        product = db.query(Product).filter(Product.id == product_id, Product.is_active.is_(True)).first()
        if product is None:
            push_flash(request, 'error', 'Invalid product selected.')
            return RedirectResponse('/admin', status_code=303)

        file_name = installer_file.filename or 'installer.bin'
        version_slug = sanitize_segment(version)
        target_dir = settings.installers_dir / product.slug / version_slug
        target_dir.mkdir(parents=True, exist_ok=True)
        target_path = target_dir / sanitize_segment(file_name)
        file_bytes = await installer_file.read()
        if not file_bytes:
            push_flash(request, 'error', 'Installer upload was empty.')
            return RedirectResponse('/admin', status_code=303)
        target_path.write_bytes(file_bytes)

        mark_latest = str(is_latest).lower() in {'on', 'true', '1', 'yes'}
        if mark_latest:
            db.query(ProductRelease).filter(ProductRelease.product_id == product.id).update({'is_latest': False})

        release = ProductRelease(
            product_id=product.id,
            version=version.strip(),
            notes=notes.strip(),
            original_filename=file_name,
            installer_path=str(target_path),
            is_latest=mark_latest,
        )
        db.add(release)
        db.commit()
        push_flash(request, 'success', f'Uploaded installer for {product.display_name} {version.strip()}.')
        return RedirectResponse('/admin', status_code=303)

    return router
