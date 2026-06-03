"""
One-time RSA-2048 key pair generator for Watchtower licensing.

Run this ONCE before your first production build.
It will:
    1. Generate private_key.pem in licenses/keys (NEVER distribute this).
  2. Embed the matching public key directly into src/license_manager.py.

Usage:
    python tools/generate_keys.py

WARNING: Regenerating keys invalidates ALL previously issued licenses.
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

try:
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric import rsa
except ImportError:
    print('[X] cryptography package not installed.')
    print('    Run: pip install cryptography')
    sys.exit(1)

TOOLS_DIR = Path(__file__).parent
PROJECT_ROOT = TOOLS_DIR.parent
PRIVATE_KEY_PATH = PROJECT_ROOT / 'licenses' / 'keys' / 'private_key.pem'
LICENSE_MANAGER_PATH = PROJECT_ROOT / 'src' / 'license_manager.py'


def main() -> None:
    if PRIVATE_KEY_PATH.exists():
        answer = input(
            f'\n[!] {PRIVATE_KEY_PATH.name} already exists.\n'
            '    Regenerating will INVALIDATE all existing licenses.\n'
            '    Type YES to continue, anything else to abort: '
        ).strip()
        if answer != 'YES':
            print('[OK] Aborted. Existing keys are unchanged.')
            return

    print('\n[1/3] Generating RSA-2048 key pair...')
    private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    public_key = private_key.public_key()

    private_pem = private_key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    )
    public_pem = public_key.public_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PublicFormat.SubjectPublicKeyInfo,
    )

    PRIVATE_KEY_PATH.parent.mkdir(parents=True, exist_ok=True)
    print(f'[2/3] Saving private key to {PRIVATE_KEY_PATH} ...')
    PRIVATE_KEY_PATH.write_bytes(private_pem)

    print(f'[3/3] Patching public key into {LICENSE_MANAGER_PATH} ...')
    source = LICENSE_MANAGER_PATH.read_text(encoding='utf-8')

    # Replace the entire _PUBLIC_KEY_PEM = b"""...""" block.
    new_block = f'_PUBLIC_KEY_PEM = b"""\\\n{public_pem.decode()}"""'
    new_source = re.sub(
        r'_PUBLIC_KEY_PEM = b""".*?"""',
        new_block,
        source,
        flags=re.DOTALL,
    )

    if new_source == source:
        print('\n[X] Could not locate _PUBLIC_KEY_PEM in license_manager.py.')
        print('    Patch manually with this public key:')
        print(public_pem.decode())
        return

    LICENSE_MANAGER_PATH.write_text(new_source, encoding='utf-8')

    print('\n[OK] Keys generated successfully.')
    print(f'     Private key : {PRIVATE_KEY_PATH}')
    print(f'     Public key  : embedded in {LICENSE_MANAGER_PATH}')
    print()
    print('[!] IMPORTANT:')
    print('    - Keep private_key.pem SECRET. Never include it in a build or commit.')
    print('    - Back it up securely. If lost, you cannot issue new licenses.')
    print('    - Rebuild Watchtower.exe after running this tool.')


if __name__ == '__main__':
    main()
