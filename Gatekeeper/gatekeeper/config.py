from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


BASE_DIR = Path(__file__).resolve().parent.parent
WORKSPACE_ROOT = Path(os.environ.get('GATEKEEPER_WORKSPACE_ROOT', str(BASE_DIR.parent))).resolve()


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
    host: str = os.environ.get('GATEKEEPER_HOST', '127.0.0.1')
    port: int = int(os.environ.get('GATEKEEPER_PORT', '8000'))


settings = Settings()


def ensure_runtime_dirs() -> None:
    settings.data_dir.mkdir(parents=True, exist_ok=True)
    settings.installers_dir.mkdir(parents=True, exist_ok=True)
    settings.generated_licenses_dir.mkdir(parents=True, exist_ok=True)
