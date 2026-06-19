from __future__ import annotations

import os
import re
from dataclasses import dataclass
from pathlib import Path


BASE_DIR = Path(__file__).resolve().parent.parent
WORKSPACE_ROOT = Path(os.environ.get('GATEKEEPER_WORKSPACE_ROOT', str(BASE_DIR.parent))).resolve()


def parse_bool_env(value: str, default: bool = False) -> bool:
    raw = (value or '').strip().lower()
    if not raw:
        return default
    return raw in {'1', 'true', 'yes', 'on'}


def normalize_mac(mac: str) -> str:
    cleaned = re.sub(r'[^0-9A-Fa-f]', '', mac or '')
    if len(cleaned) != 12:
        return ''
    upper = cleaned.upper()
    return ':'.join(upper[i : i + 2] for i in range(0, 12, 2))


def parse_mac_list(raw_value: str) -> tuple[str, ...]:
    raw_items = [segment.strip() for segment in (raw_value or '').replace(';', ',').split(',')]
    unique: list[str] = []
    for item in raw_items:
        if not item:
            continue
        normalized = normalize_mac(item)
        if normalized and normalized not in unique:
            unique.append(normalized)
    return tuple(unique)


@dataclass(frozen=True)
class Settings:
    base_dir: Path = BASE_DIR
    workspace_root: Path = WORKSPACE_ROOT
    data_dir: Path = BASE_DIR / 'data'
    installers_dir: Path = BASE_DIR / 'data' / 'installers'
    generated_licenses_dir: Path = BASE_DIR / 'data' / 'generated_licenses'
    db_path: Path = BASE_DIR / 'data' / 'gatekeeper.db'
    secret_key: str = os.environ.get('GATEKEEPER_SECRET_KEY', 'change-me-before-production')
    default_license_days: int = int(os.environ.get('GATEKEEPER_DEFAULT_LICENSE_DAYS', '365'))
    bootstrap_admin_email: str = os.environ.get('GATEKEEPER_BOOTSTRAP_ADMIN_EMAIL', '').strip().lower()
    bootstrap_admin_password: str = os.environ.get('GATEKEEPER_BOOTSTRAP_ADMIN_PASSWORD', '')
    enforce_admin_mac: bool = parse_bool_env(os.environ.get('GATEKEEPER_ENFORCE_ADMIN_MAC', '0'))
    admin_allowed_macs: tuple[str, ...] = parse_mac_list(os.environ.get('GATEKEEPER_ADMIN_ALLOWED_MACS', ''))
    host: str = os.environ.get('GATEKEEPER_HOST', '127.0.0.1')
    port: int = int(os.environ.get('GATEKEEPER_PORT', '8000'))


settings = Settings()


def ensure_runtime_dirs() -> None:
    settings.data_dir.mkdir(parents=True, exist_ok=True)
    settings.installers_dir.mkdir(parents=True, exist_ok=True)
    settings.generated_licenses_dir.mkdir(parents=True, exist_ok=True)
