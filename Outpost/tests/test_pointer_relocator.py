import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))

from pointer_relocator import parse_hex_address, rebase_address


def test_parse_hex_address_accepts_prefixed_and_plain_values():
    assert parse_hex_address('0x7FF600001234') == 0x7FF600001234
    assert parse_hex_address('7ff600001234') == 0x7FF600001234


def test_rebase_address_preserves_module_relative_offset():
    assert rebase_address(0x140012340, 0x140000000, 0x7FF700000000) == 0x7FF700012340


def test_rebase_address_rejects_address_below_module_base():
    with pytest.raises(ValueError):
        rebase_address(0x13FFFF000, 0x140000000, 0x7FF700000000)