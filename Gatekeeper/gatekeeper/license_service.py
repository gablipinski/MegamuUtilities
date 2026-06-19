from __future__ import annotations

import base64
import json
import re
from datetime import date, datetime, timedelta
from pathlib import Path

from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding
from sqlalchemy.orm import Session

from .config import settings
from .models import IssuedLicense, LicenseRequest, Product, ProductRelease, User


def sanitize_segment(value: str) -> str:
    sanitized = re.sub(r'[^A-Za-z0-9._-]+', '_', value.strip())
    sanitized = sanitized.strip('._-')
    return sanitized or 'item'


def normalize_machine_id(machine_id: str) -> str:
    return machine_id.upper().strip()


def seed_default_products(db: Session) -> None:
    products = [
        ('guardtower', 'Guardtower', settings.workspace_root / 'Guardtower'),
        ('watchtower', 'Watchtower', settings.workspace_root / 'Watchtower'),
        ('siegetower', 'Siegetower', settings.workspace_root / 'Siegetower'),
    ]

    for slug, display_name, app_root in products:
        product = db.query(Product).filter(Product.slug == slug).first()
        if product is None:
            product = Product(
                slug=slug,
                display_name=display_name,
                app_root_path=str(app_root),
                private_key_path=str(app_root / 'licenses' / 'keys' / 'private_key.pem'),
                is_active=True,
            )
            db.add(product)
        else:
            product.display_name = display_name
            product.app_root_path = str(app_root)
            product.private_key_path = str(app_root / 'licenses' / 'keys' / 'private_key.pem')
            product.is_active = True
    db.commit()


def bootstrap_admin_user(db: Session, email: str, password_hash: str, display_name: str) -> User:
    user = db.query(User).filter(User.email == email).first()
    if user is None:
        user = User(
            email=email,
            password_hash=password_hash,
            display_name=display_name,
            is_admin=True,
            is_active=True,
        )
        db.add(user)
        db.commit()
        db.refresh(user)
    else:
        user.is_admin = True
        user.is_active = True
        db.commit()
    return user


def generate_license_file(
    *,
    product: Product,
    machine_id: str,
    issued_to: str,
    expiry_date: date | None,
) -> Path:
    private_key_path = Path(product.private_key_path)
    if not private_key_path.exists():
        raise FileNotFoundError(f'Private key not found for {product.display_name}: {private_key_path}')

    private_key_pem = private_key_path.read_bytes()
    private_key = serialization.load_pem_private_key(private_key_pem, password=None)
    expiry_str = expiry_date.isoformat() if expiry_date else ''
    payload_data = {
        'expiry': expiry_str,
        'issued_to': issued_to,
        'machine_id': machine_id,
    }
    payload = json.dumps(payload_data, sort_keys=True).encode('utf-8')
    signature = private_key.sign(
        payload,
        padding.PSS(
            mgf=padding.MGF1(hashes.SHA256()),
            salt_length=padding.PSS.MAX_LENGTH,
        ),
        hashes.SHA256(),
    )

    license_data = dict(payload_data)
    license_data['signature'] = base64.b64encode(signature).decode('ascii')

    month_folder = datetime.utcnow().strftime('%Y-%m')
    target_dir = settings.generated_licenses_dir / product.slug / month_folder
    target_dir.mkdir(parents=True, exist_ok=True)
    file_name = f'{sanitize_segment(issued_to)}_{sanitize_segment(machine_id)}_license.dat'
    final_path = target_dir / file_name
    final_path.write_text(json.dumps(license_data, indent=2), encoding='utf-8')
    return final_path


def approve_request(
    db: Session,
    *,
    request_row: LicenseRequest,
    admin: User,
    expiry_date: date | None,
    admin_note: str,
) -> IssuedLicense:
    if request_row.status == 'approved' and request_row.issued_license is not None:
        return request_row.issued_license

    product = request_row.product
    machine = request_row.machine
    user = request_row.user
    machine_id = normalize_machine_id(machine.machine_id)
    issued_to = user.display_name or user.email
    if expiry_date is None and settings.default_license_days > 0:
        expiry_date = date.today() + timedelta(days=settings.default_license_days)

    file_path = generate_license_file(
        product=product,
        machine_id=machine_id,
        issued_to=issued_to,
        expiry_date=expiry_date,
    )

    license_row = IssuedLicense(
        request=request_row,
        user=user,
        product=product,
        machine=machine,
        issued_to=issued_to,
        expiry_date=expiry_date,
        file_path=str(file_path),
    )
    request_row.status = 'approved'
    request_row.admin_note = admin_note.strip()
    request_row.reviewed_by = admin
    request_row.reviewed_at = datetime.utcnow()
    db.add(license_row)
    db.commit()
    db.refresh(license_row)
    return license_row


def reject_request(db: Session, *, request_row: LicenseRequest, admin: User, admin_note: str) -> None:
    request_row.status = 'rejected'
    request_row.admin_note = admin_note.strip()
    request_row.reviewed_by = admin
    request_row.reviewed_at = datetime.utcnow()
    db.commit()


def latest_releases_by_product(db: Session) -> dict[int, ProductRelease]:
    releases = db.query(ProductRelease).filter(ProductRelease.is_latest.is_(True)).all()
    return {release.product_id: release for release in releases}
