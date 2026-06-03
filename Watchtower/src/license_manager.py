"""
Hardware-bound license validation for Watchtower.

License flow:
  1. User runs the app -> sees their Machine ID in the activation dialog.
  2. User sends Machine ID to the distributor.
  3. Distributor runs: python tools/generate_license.py <machine_id> <name>
  4. Distributor sends license.dat to the user.
    5. User places license.dat in %APPDATA%\\Watchtower\\ (shown in the dialog).
"""
from __future__ import annotations

import base64
import hashlib
import json
import os
import subprocess
import sys
import uuid
from datetime import date
from pathlib import Path

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding

# ─────────────────────────────────────────────────────────────────────────────
# Embedded public key.
# Run tools/generate_keys.py ONCE before your first build — it will
# automatically patch this constant with the generated key.
# ─────────────────────────────────────────────────────────────────────────────
_PUBLIC_KEY_PEM = b"""\
REPLACE_WITH_OUTPUT_FROM_tools/generate_keys.py
"""


def get_license_path() -> Path:
    """Return the expected location of license.dat.

    - Compiled exe  : %APPDATA%\\Watchtower\\license.dat  (user-writable)
    - Development   : <project_root>/license.dat
    """
    if getattr(sys, 'frozen', False):
        appdata = Path(os.environ.get('APPDATA', Path.home() / 'AppData' / 'Roaming'))
        target_dir = appdata / 'Watchtower'
        target_dir.mkdir(parents=True, exist_ok=True)
        return target_dir / 'license.dat'
    return Path(__file__).parent.parent / 'license.dat'


def _wmic(args: list[str]) -> str:
    """Run a wmic command and return the first data line, or '' on failure."""
    try:
        result = subprocess.run(
            ['wmic'] + args,
            capture_output=True,
            text=True,
            timeout=5,
            creationflags=0x08000000,  # CREATE_NO_WINDOW
        )
        lines = [ln.strip() for ln in result.stdout.splitlines() if ln.strip()]
        return lines[1] if len(lines) > 1 else ''
    except Exception:
        return ''


def get_machine_id() -> str:
    """Return a stable hardware-bound ID formatted as XXXX-XXXX-XXXX-XXXX."""
    cpu_id = _wmic(['cpu', 'get', 'ProcessorId'])
    disk_serial = _wmic(['diskdrive', 'get', 'SerialNumber'])
    mac_int = uuid.getnode()
    raw = f'{cpu_id}|{disk_serial}|{mac_int}'
    digest = hashlib.sha256(raw.encode('utf-8')).hexdigest().upper()
    return f'{digest[0:4]}-{digest[4:8]}-{digest[8:12]}-{digest[12:16]}'


def validate_license(license_path: Path) -> tuple[bool, str]:
    """Validate a license.dat file.

    Returns (is_valid, message).  Message contains success info or a
    human-readable explanation of why validation failed.
    """
    if b'REPLACE_WITH_OUTPUT' in _PUBLIC_KEY_PEM:
        return False, (
            'Application is not properly configured for distribution.\n'
            'Run tools/generate_keys.py to set up signing keys before building.'
        )

    if not license_path.exists():
        machine_id = get_machine_id()
        return False, (
            f'License file not found.\n\n'
            f'Your Machine ID:\n{machine_id}\n\n'
            'Send this ID to the software distributor to receive your license.dat'
        )

    try:
        data = json.loads(license_path.read_text(encoding='utf-8'))
        machine_id: str = data.get('machine_id', '')
        issued_to: str = data.get('issued_to', '')
        expiry_str: str = data.get('expiry', '')
        sig_b64: str = data.get('signature', '')

        local_id = get_machine_id()
        if machine_id != local_id:
            return False, (
                f'This license belongs to a different machine.\n\n'
                f'License machine ID : {machine_id}\n'
                f'Your machine ID    : {local_id}'
            )

        if expiry_str:
            expiry = date.fromisoformat(expiry_str)
            if date.today() > expiry:
                return False, (
                    f'License expired on {expiry_str}.\n'
                    'Contact the distributor for a renewal.'
                )

        payload = json.dumps(
            {'expiry': expiry_str, 'issued_to': issued_to, 'machine_id': machine_id},
            sort_keys=True,
        ).encode('utf-8')

        signature = base64.b64decode(sig_b64)
        public_key = serialization.load_pem_public_key(_PUBLIC_KEY_PEM)
        public_key.verify(  # type: ignore[union-attr]
            signature,
            payload,
            padding.PSS(
                mgf=padding.MGF1(hashes.SHA256()),
                salt_length=padding.PSS.MAX_LENGTH,
            ),
            hashes.SHA256(),
        )
        expiry_note = f' · expires {expiry_str}' if expiry_str else ''
        return True, f'Licensed to: {issued_to}{expiry_note}'

    except InvalidSignature:
        return False, 'License signature is invalid or has been tampered with.'
    except Exception as exc:
        return False, f'License validation error: {exc}'
