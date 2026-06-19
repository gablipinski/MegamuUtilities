from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import FileResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session

from .dependencies import get_db, get_current_user, pop_flashes, push_flash
from .license_service import latest_releases_by_product, normalize_machine_id
from .models import IssuedLicense, LicenseRequest, Machine, Product, ProductAccessGrant, ProductAccessRequest, ProductRelease, User


router = APIRouter()


def register_user_routes(templates: Jinja2Templates) -> APIRouter:
    @router.get('/')
    def home(request: Request, db: Session = Depends(get_db)):
        user = request.session.get('user_id')
        if user:
            return RedirectResponse('/app', status_code=303)
        return RedirectResponse('/login', status_code=303)

    @router.get('/app')
    def dashboard(request: Request, db: Session = Depends(get_db)):
        current_user = get_current_user(request, db)
        products = db.query(Product).filter(Product.is_active.is_(True)).order_by(Product.display_name.asc()).all()
        machines = db.query(Machine).filter(Machine.user_id == current_user.id).order_by(Machine.created_at.desc()).all()
        access_requests = (
            db.query(ProductAccessRequest)
            .filter(ProductAccessRequest.user_id == current_user.id)
            .order_by(ProductAccessRequest.created_at.desc())
            .all()
        )
        access_grants = (
            db.query(ProductAccessGrant)
            .filter(ProductAccessGrant.user_id == current_user.id, ProductAccessGrant.is_active.is_(True))
            .all()
        )
        granted_product_ids = {row.product_id for row in access_grants}
        requests = (
            db.query(LicenseRequest)
            .filter(LicenseRequest.user_id == current_user.id)
            .order_by(LicenseRequest.created_at.desc())
            .all()
        )
        issued_licenses = (
            db.query(IssuedLicense)
            .filter(IssuedLicense.user_id == current_user.id)
            .order_by(IssuedLicense.created_at.desc())
            .all()
        )
        latest_releases = latest_releases_by_product(db)
        approved_product_ids = {license_row.product_id for license_row in issued_licenses}

        return templates.TemplateResponse(
            request,
            'dashboard.html',
            {
                'page_title': 'User Dashboard',
                'current_user': current_user,
                'products': products,
                'machines': machines,
                'access_requests': access_requests,
                'granted_product_ids': granted_product_ids,
                'requests': requests,
                'issued_licenses': issued_licenses,
                'latest_releases': latest_releases,
                'approved_product_ids': approved_product_ids,
                'flashes': pop_flashes(request),
            },
        )

    @router.post('/app/access-requests')
    def create_access_request(
        request: Request,
        product_id: int = Form(...),
        requested_note: str = Form(''),
        db: Session = Depends(get_db),
    ):
        current_user = get_current_user(request, db)
        product = db.query(Product).filter(Product.id == product_id, Product.is_active.is_(True)).first()
        if product is None:
            push_flash(request, 'error', 'Invalid product selection.')
            return RedirectResponse('/app', status_code=303)

        existing_grant = (
            db.query(ProductAccessGrant)
            .filter(
                ProductAccessGrant.user_id == current_user.id,
                ProductAccessGrant.product_id == product.id,
                ProductAccessGrant.is_active.is_(True),
            )
            .first()
        )
        if existing_grant is not None:
            push_flash(request, 'success', f'Access already approved for {product.display_name}. You can download the installer.')
            return RedirectResponse('/app', status_code=303)

        existing_pending = (
            db.query(ProductAccessRequest)
            .filter(
                ProductAccessRequest.user_id == current_user.id,
                ProductAccessRequest.product_id == product.id,
                ProductAccessRequest.status == 'pending',
            )
            .first()
        )
        if existing_pending is not None:
            push_flash(request, 'error', f'A pending access request for {product.display_name} already exists.')
            return RedirectResponse('/app', status_code=303)

        request_row = ProductAccessRequest(
            user_id=current_user.id,
            product_id=product.id,
            requested_note=requested_note.strip(),
            status='pending',
        )
        db.add(request_row)
        db.commit()
        push_flash(request, 'success', f'Access request submitted for {product.display_name}.')
        return RedirectResponse('/app', status_code=303)

    @router.post('/app/machines')
    def add_machine(
        request: Request,
        label: str = Form(...),
        machine_id: str = Form(...),
        db: Session = Depends(get_db),
    ):
        current_user = get_current_user(request, db)
        normalized_machine_id = normalize_machine_id(machine_id)
        if not normalized_machine_id:
            push_flash(request, 'error', 'Machine ID is required.')
            return RedirectResponse('/app', status_code=303)

        existing = (
            db.query(Machine)
            .filter(Machine.user_id == current_user.id, Machine.machine_id == normalized_machine_id)
            .first()
        )
        if existing is not None:
            push_flash(request, 'error', 'This machine ID is already registered.')
            return RedirectResponse('/app', status_code=303)

        machine = Machine(
            user_id=current_user.id,
            label=label.strip() or normalized_machine_id,
            machine_id=normalized_machine_id,
            is_active=True,
        )
        db.add(machine)
        db.commit()
        push_flash(request, 'success', 'Machine registered successfully.')
        return RedirectResponse('/app', status_code=303)

    @router.post('/app/requests')
    def create_request(
        request: Request,
        product_id: int = Form(...),
        machine_id_ref: int = Form(...),
        requested_note: str = Form(''),
        db: Session = Depends(get_db),
    ):
        current_user = get_current_user(request, db)
        product = db.query(Product).filter(Product.id == product_id, Product.is_active.is_(True)).first()
        machine = db.query(Machine).filter(Machine.id == machine_id_ref, Machine.user_id == current_user.id).first()
        if product is None or machine is None:
            push_flash(request, 'error', 'Invalid product or machine selection.')
            return RedirectResponse('/app', status_code=303)

        grant = (
            db.query(ProductAccessGrant)
            .filter(
                ProductAccessGrant.user_id == current_user.id,
                ProductAccessGrant.product_id == product.id,
                ProductAccessGrant.is_active.is_(True),
            )
            .first()
        )
        if grant is None:
            push_flash(request, 'error', 'Request product access first, then submit machine license request.')
            return RedirectResponse('/app', status_code=303)

        existing_pending = (
            db.query(LicenseRequest)
            .filter(
                LicenseRequest.user_id == current_user.id,
                LicenseRequest.product_id == product.id,
                LicenseRequest.machine_id_ref == machine.id,
                LicenseRequest.status == 'pending',
            )
            .first()
        )
        if existing_pending is not None:
            push_flash(request, 'error', 'A pending request for this product and machine already exists.')
            return RedirectResponse('/app', status_code=303)

        request_row = LicenseRequest(
            user_id=current_user.id,
            product_id=product.id,
            machine_id_ref=machine.id,
            requested_note=requested_note.strip(),
            status='pending',
        )
        db.add(request_row)
        db.commit()
        push_flash(request, 'success', 'Machine license request submitted.')
        return RedirectResponse('/app', status_code=303)

    @router.get('/app/licenses/{license_id}/download')
    def download_license(license_id: int, request: Request, db: Session = Depends(get_db)):
        current_user = get_current_user(request, db)
        license_row = (
            db.query(IssuedLicense)
            .filter(IssuedLicense.id == license_id, IssuedLicense.user_id == current_user.id)
            .first()
        )
        if license_row is None:
            raise HTTPException(status_code=404, detail='License not found')
        file_path = Path(license_row.file_path)
        if not file_path.exists():
            raise HTTPException(status_code=404, detail='Generated license file is missing')
        return FileResponse(path=file_path, filename='license.dat', media_type='application/octet-stream')

    @router.get('/app/releases/{release_id}/download')
    def download_installer(release_id: int, request: Request, db: Session = Depends(get_db)):
        current_user = get_current_user(request, db)
        release = db.query(ProductRelease).filter(ProductRelease.id == release_id).first()
        if release is None:
            raise HTTPException(status_code=404, detail='Release not found')
        has_access = (
            db.query(ProductAccessGrant)
            .filter(
                ProductAccessGrant.user_id == current_user.id,
                ProductAccessGrant.product_id == release.product_id,
                ProductAccessGrant.is_active.is_(True),
            )
            .first()
        )
        if has_access is None:
            raise HTTPException(status_code=403, detail='You do not have access to this installer')
        file_path = Path(release.installer_path)
        if not file_path.exists():
            raise HTTPException(status_code=404, detail='Installer file is missing')
        return FileResponse(path=file_path, filename=release.original_filename, media_type='application/octet-stream')

    return router
