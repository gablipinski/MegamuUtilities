"""
License generator for Watchtower.

Generates a hardware-bound license.dat file for a specific user machine.
Keep this tool private — never distribute it.

Usage:
    python tools/generate_license.py <machine_id> <name> [options]

Examples:
    python tools/generate_license.py A3F2-9C1B-D47E-8801 "Player One"
    python tools/generate_license.py A3F2-9C1B-D47E-8801 "Player One" --expiry 2027-06-01
    python tools/generate_license.py A3F2-9C1B-D47E-8801 "Player One" --expiry never
    python tools/generate_license.py A3F2-9C1B-D47E-8801 "Player One" --output licenses/player_one.dat
"""
from __future__ import annotations

import argparse
import base64
import json
import sys
from datetime import date, timedelta
from pathlib import Path

try:
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import padding
except ImportError:
    print('[X] cryptography package not installed. Run: pip install cryptography')
    sys.exit(1)

TOOLS_DIR = Path(__file__).parent
PRIVATE_KEY_PATH = TOOLS_DIR / 'private_key.pem'


def generate_license(
    machine_id: str,
    issued_to: str,
    expiry: str,
    output_path: Path,
) -> None:
    if not PRIVATE_KEY_PATH.exists():
        print(f'[X] Private key not found: {PRIVATE_KEY_PATH}')
        print('    Run tools/generate_keys.py first.')
        sys.exit(1)

    private_key_pem = PRIVATE_KEY_PATH.read_bytes()
    private_key = serialization.load_pem_private_key(private_key_pem, password=None)

    payload = json.dumps(
        {'expiry': expiry, 'issued_to': issued_to, 'machine_id': machine_id},
        sort_keys=True,
    ).encode('utf-8')

    signature = private_key.sign(  # type: ignore[union-attr]
        payload,
        padding.PSS(
            mgf=padding.MGF1(hashes.SHA256()),
            salt_length=padding.PSS.MAX_LENGTH,
        ),
        hashes.SHA256(),
    )

    license_data = {
        'machine_id': machine_id,
        'issued_to': issued_to,
        'expiry': expiry,
        'signature': base64.b64encode(signature).decode(),
    }

    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(license_data, indent=2), encoding='utf-8')

    expiry_display = expiry if expiry else 'Never'
    print(f'\n[OK] License created: {output_path}')
    print(f'     Machine ID  : {machine_id}')
    print(f'     Issued to   : {issued_to}')
    print(f'     Expires     : {expiry_display}')
    print('\n[>] Send this license.dat file to the user.')
    print(f'    They must place it in: %APPDATA%\\Watchtower\\license.dat')


def main() -> None:
    parser = argparse.ArgumentParser(
        description='Generate a Watchtower license for a specific machine.',
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument('machine_id', help='Target machine ID (e.g. A3F2-9C1B-D47E-8801)')
    parser.add_argument('name', help='Licensee name or identifier')
    parser.add_argument(
        '--expiry',
        default=(date.today() + timedelta(days=365)).isoformat(),
        help='Expiry date as YYYY-MM-DD, or "never" for no expiry (default: 1 year from today)',
    )
    parser.add_argument(
        '--output',
        default='license.dat',
        help='Output file path (default: license.dat in current directory)',
    )
    args = parser.parse_args()

    expiry = '' if args.expiry.lower() == 'never' else args.expiry

    generate_license(
        machine_id=args.machine_id.upper().strip(),
        issued_to=args.name,
        expiry=expiry,
        output_path=Path(args.output),
    )


if __name__ == '__main__':
    main()
