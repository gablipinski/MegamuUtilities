import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from pointer_relocator import discover_saved_pointers, parse_hex_address, rebase_address, save_pointer_offsets


def test_parse_hex_address_accepts_prefixed_and_plain_values():
    assert parse_hex_address('0x7FF600001234') == 0x7FF600001234
    assert parse_hex_address('7ff600001234') == 0x7FF600001234


def test_rebase_address_preserves_module_relative_offset():
    assert rebase_address(0x140012340, 0x140000000, 0x7FF700000000) == 0x7FF700012340


def test_rebase_address_rejects_address_below_module_base():
    with pytest.raises(ValueError):
        rebase_address(0x13FFFF000, 0x140000000, 0x7FF700000000)


def test_discover_saved_pointers_reads_literal_pointer_entries(tmp_path):
    scan_file = tmp_path / 'ExampleApp' / 'src' / 'scan_addresses.py'
    scan_file.parent.mkdir(parents=True)
    scan_file.write_text(
        "SCAN_ADDRESSES = [{'name': 'HP', 'type': 'pointer', 'module': 'Game.dll', "
        "'base_offset': '0x100', 'offsets': ['0x8'], 'description': 'Health'}]",
        encoding='utf-8',
    )

    pointers = discover_saved_pointers(tmp_path)

    assert len(pointers) == 1
    assert pointers[0].label == 'ExampleApp: HP'
    assert pointers[0].offsets == ('0x8',)

    repaired = pointers[0].__class__(
        app_name=pointers[0].app_name,
        source_path=pointers[0].source_path,
        name=pointers[0].name,
        module=pointers[0].module,
        base_offset=pointers[0].base_offset,
        offsets=('0x10',),
        description=pointers[0].description,
    )
    save_pointer_offsets(repaired)
    assert '"0x10"' in scan_file.read_text(encoding='utf-8')