from __future__ import annotations

import re
from datetime import date
from pathlib import Path

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import JSONResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session
from datetime import datetime

from .dependencies import get_db, pop_flashes, push_flash, require_admin
from .license_service import (
    approve_request,
    import_release_from_workspace,
    latest_releases_by_product,
    read_workspace_release_info,
    reject_request,
    _parse_all_release_notes,
)
from .models import IssuedLicense, LicenseRequest, Product, ProductAccessGrant, ProductAccessRequest, User


router = APIRouter(prefix='/admin')


def register_admin_routes(templates: Jinja2Templates) -> APIRouter:
    @router.get('')
    def admin_dashboard(request: Request, db: Session = Depends(get_db)):
        admin_user = require_admin(request, db)
        active_access_grants = (
            db.query(ProductAccessGrant)
            .filter(ProductAccessGrant.is_active.is_(True))
            .order_by(ProductAccessGrant.created_at.desc())
            .all()
        )
        pending_access_requests = (
            db.query(ProductAccessRequest)
            .filter(ProductAccessRequest.status == 'pending')
            .order_by(ProductAccessRequest.created_at.asc())
            .all()
        )
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
        workspace_info = {p.id: read_workspace_release_info(p) for p in products}
        workspace_all_notes = {
            p.id: _parse_all_release_notes(Path(p.app_root_path) / 'RELEASE_NOTES.md')
            for p in products
        }
        return templates.TemplateResponse(
            request,
            'admin_dashboard.html',
            {
                'page_title': 'Admin Dashboard',
                'current_user': admin_user,
                'active_access_grants': active_access_grants,
                'pending_access_requests': pending_access_requests,
                'pending_requests': pending_requests,
                'recent_requests': recent_requests,
                'recent_licenses': recent_licenses,
                'products': products,
                'users': users,
                'latest_releases': latest_releases,
                'workspace_info': workspace_info,
                'workspace_all_notes': workspace_all_notes,
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

    @router.post('/access-requests/{request_id}/approve')
    def approve_access_request(
        request_id: int,
        request: Request,
        admin_note: str = Form(''),
        db: Session = Depends(get_db),
    ):
        admin_user = require_admin(request, db)
        request_row = db.query(ProductAccessRequest).filter(ProductAccessRequest.id == request_id).first()
        if request_row is None:
            push_flash(request, 'error', 'Access request not found.')
            return RedirectResponse('/admin', status_code=303)
        request_row.status = 'approved'
        request_row.admin_note = admin_note.strip()
        request_row.reviewed_by = admin_user
        request_row.reviewed_at = datetime.utcnow()

        grant = (
            db.query(ProductAccessGrant)
            .filter(
                ProductAccessGrant.user_id == request_row.user_id,
                ProductAccessGrant.product_id == request_row.product_id,
            )
            .first()
        )
        if grant is None:
            grant = ProductAccessGrant(
                user_id=request_row.user_id,
                product_id=request_row.product_id,
                is_active=True,
                granted_by=admin_user,
            )
            db.add(grant)
        else:
            grant.is_active = True
            grant.granted_by = admin_user
        db.commit()
        push_flash(request, 'success', 'Access request approved. User can now download installer.')
        return RedirectResponse('/admin', status_code=303)

    @router.post('/access-requests/{request_id}/reject')
    def reject_access_request(
        request_id: int,
        request: Request,
        admin_note: str = Form(...),
        db: Session = Depends(get_db),
    ):
        admin_user = require_admin(request, db)
        request_row = db.query(ProductAccessRequest).filter(ProductAccessRequest.id == request_id).first()
        if request_row is None:
            push_flash(request, 'error', 'Access request not found.')
            return RedirectResponse('/admin', status_code=303)
        request_row.status = 'rejected'
        request_row.admin_note = admin_note.strip()
        request_row.reviewed_by = admin_user
        request_row.reviewed_at = datetime.utcnow()
        db.commit()
        push_flash(request, 'success', 'Access request rejected.')
        return RedirectResponse('/admin', status_code=303)

    @router.post('/access-grants/{grant_id}/revoke')
    def revoke_access_grant(
        grant_id: int,
        request: Request,
        admin_note: str = Form(''),
        db: Session = Depends(get_db),
    ):
        admin_user = require_admin(request, db)
        grant = db.query(ProductAccessGrant).filter(ProductAccessGrant.id == grant_id).first()
        if grant is None:
            push_flash(request, 'error', 'Access grant not found.')
            return RedirectResponse('/admin', status_code=303)
        if not grant.is_active:
            push_flash(request, 'error', 'Access grant is already inactive.')
            return RedirectResponse('/admin', status_code=303)

        grant.is_active = False

        # Mark any pending access request as revoked for traceability.
        pending_access_request = (
            db.query(ProductAccessRequest)
            .filter(
                ProductAccessRequest.user_id == grant.user_id,
                ProductAccessRequest.product_id == grant.product_id,
                ProductAccessRequest.status == 'pending',
            )
            .order_by(ProductAccessRequest.created_at.desc())
            .first()
        )
        if pending_access_request is not None:
            pending_access_request.status = 'rejected'
            pending_access_request.admin_note = admin_note.strip() or 'Access revoked by admin.'
            pending_access_request.reviewed_by = admin_user
            pending_access_request.reviewed_at = datetime.utcnow()

        db.commit()
        push_flash(request, 'success', 'Access revoked. User can no longer download installer or license files for this product.')
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

    @router.post('/releases/import/{product_id}')
    def import_release(product_id: int, request: Request, db: Session = Depends(get_db)):
        require_admin(request, db)
        product = db.query(Product).filter(Product.id == product_id, Product.is_active.is_(True)).first()
        is_async_request = request.headers.get('x-requested-with', '').lower() == 'xmlhttprequest'
        if product is None:
            if is_async_request:
                return JSONResponse({'ok': False, 'message': 'Product not found.'}, status_code=404)
            push_flash(request, 'error', 'Product not found.')
            return RedirectResponse('/admin', status_code=303)
        try:
            release = import_release_from_workspace(db, product)
            message = f'Imported {product.display_name} {release.version}. Binary is ready for download.'
            if is_async_request:
                return JSONResponse(
                    {
                        'ok': True,
                        'product_id': product.id,
                        'product_name': product.display_name,
                        'version': release.version,
                        'message': message,
                    }
                )
            push_flash(request, 'success',
                       f'Imported {product.display_name} {release.version} from workspace. Previous binaries replaced.')
        except Exception as exc:
            if is_async_request:
                return JSONResponse({'ok': False, 'message': f'Import failed for {product.display_name}: {exc}'}, status_code=400)
            push_flash(request, 'error', f'Import failed for {product.display_name}: {exc}')
        return RedirectResponse('/admin', status_code=303)

    return router
