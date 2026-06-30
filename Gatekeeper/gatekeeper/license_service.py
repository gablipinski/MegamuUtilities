from __future__ import annotations

import base64
import json
import re
import shutil
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from pathlib import Path

from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding
from sqlalchemy.orm import Session

from .config import settings
from .models import IssuedLicense, LicenseRequest, Product, ProductRelease, User


@dataclass
class WorkspaceReleaseInfo:
    version: str
    patch_notes: str          # formatted bullet list
    installer_path: Path | None
    error: str = ''


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
    previous_license = (
        db.query(IssuedLicense)
        .filter(
            IssuedLicense.user_id == user.id,
            IssuedLicense.product_id == product.id,
            IssuedLicense.request_id != request_row.id,
        )
        .order_by(IssuedLicense.created_at.desc(), IssuedLicense.id.desc())
        .first()
    )
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

    previous_license_file_path: Path | None = None
    if previous_license is not None:
        previous_license_file_path = Path(previous_license.file_path)
        db.delete(previous_license)

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

    # When regenerated for the same machine/user/month, the path can be reused.
    if previous_license_file_path is not None and previous_license_file_path != file_path and previous_license_file_path.exists():
        try:
            previous_license_file_path.unlink()
        except OSError:
            pass

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


def _extract_release_notes_section(release_notes_path: Path, version: str) -> str:
    all_sections = _parse_all_release_notes(release_notes_path)
    return all_sections.get(version, '')


def _parse_all_release_notes(release_notes_path: Path) -> dict[str, str]:
    """Return a mapping of version string -> notes body for every ## section in RELEASE_NOTES.md."""
    if not release_notes_path.exists():
        return {}
    try:
        text = release_notes_path.read_text(encoding='utf-8')
    except OSError:
        return {}

    result: dict[str, str] = {}
    current_version: str | None = None
    current_lines: list[str] = []

    for line in text.splitlines():
        if line.startswith('## '):
            if current_version is not None:
                result[current_version] = '\n'.join(current_lines).strip()
            tokens = line[3:].split()
            current_version = tokens[0] if tokens else None
            current_lines = []
        elif current_version is not None:
            current_lines.append(line)

    if current_version is not None:
        result[current_version] = '\n'.join(current_lines).strip()

    return result


def _extract_version_from_binary_name(file_name: str) -> str:
    """Extract semantic version from installer filename (e.g. Guardtower_Setup_1.0.8.exe)."""
    if not file_name:
        return ''
    match = re.search(r'(\d+\.\d+\.\d+(?:\.\d+)?)', file_name)
    return match.group(1) if match else ''


def read_workspace_release_info(product: Product) -> WorkspaceReleaseInfo:
    """Read release_info.json and locate the newest installer exe from the product workspace folder."""
    app_root = Path(product.app_root_path)
    info_path = app_root / 'release_info.json'
    if not info_path.exists():
        return WorkspaceReleaseInfo(version='', patch_notes='', installer_path=None,
                                    error='release_info.json not found in workspace')
    try:
        data = json.loads(info_path.read_text(encoding='utf-8'))
    except Exception as exc:
        return WorkspaceReleaseInfo(version='', patch_notes='', installer_path=None,
                                    error=f'Could not parse release_info.json: {exc}')

    installer_output = app_root / 'installer_output'
    exe_files = sorted(installer_output.glob('*.exe'), key=lambda p: p.stat().st_mtime, reverse=True) \
        if installer_output.exists() else []
    installer_path = exe_files[0] if exe_files else None

    version_from_release_info = (data.get('version') or '').strip()
    version_from_binary = _extract_version_from_binary_name(installer_path.name if installer_path else '')
    version = version_from_binary or version_from_release_info
    if not version:
        return WorkspaceReleaseInfo(
            version='',
            patch_notes='',
            installer_path=installer_path,
            error='Could not detect version from installer filename or release_info.json',
        )

    notes = _extract_release_notes_section(app_root / 'RELEASE_NOTES.md', version)

    return WorkspaceReleaseInfo(version=version, patch_notes=notes, installer_path=installer_path)


def import_release_from_workspace(db: Session, product: Product) -> ProductRelease:
    """Copy the current workspace installer into Gatekeeper storage and register it as the only release."""
    info = read_workspace_release_info(product)
    if info.error:
        raise ValueError(info.error)
    if info.installer_path is None:
        raise FileNotFoundError(f'No .exe found in {product.app_root_path}/installer_output/')

    # Wipe old stored binaries for this product.
    product_root_dir = settings.installers_dir / product.slug
    if product_root_dir.exists():
        shutil.rmtree(product_root_dir)

    version_slug = sanitize_segment(info.version)
    target_dir = product_root_dir / version_slug
    target_dir.mkdir(parents=True, exist_ok=True)
    target_path = target_dir / info.installer_path.name
    shutil.copy2(info.installer_path, target_path)

    db.query(ProductRelease).filter(ProductRelease.product_id == product.id).delete()

    release = ProductRelease(
        product_id=product.id,
        version=info.version,
        notes=info.patch_notes,
        original_filename=info.installer_path.name,
        installer_path=str(target_path),
        is_latest=True,
    )
    db.add(release)
    db.commit()
    db.refresh(release)
    return release
