import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))

from main import OutpostUI
from common_components import pointer_offset_fallback_candidates


class DefaultPointerValuesTests(unittest.TestCase):
    def test_ui_uses_gameassembly_hp_and_sd_defaults(self):
        ui = OutpostUI()
        try:
            self.assertEqual(ui.hp_module_var.get(), 'GameAssembly.dll')
            self.assertEqual(ui.hp_base_var.get(), '0x054A3188')
            self.assertEqual(ui.hp_offsets_var.get(), '0xB8, 0x0, 0x210, 0x1B0, 0x28, 0x80, 0x3C')

            self.assertEqual(ui.sd_module_var.get(), 'GameAssembly.dll')
            self.assertEqual(ui.sd_base_var.get(), '0x054C6AA0')
            self.assertEqual(ui.sd_offsets_var.get(), '0xD0, 0xB8, 0x0, 0x210, 0x1B8, 0x20, 0x4C')
        finally:
            ui.root.destroy()

    def test_pointer_offset_fallback_candidates_include_nearby_hops(self):
        candidates = pointer_offset_fallback_candidates([0x4C, 0x20, 0x1B8, 0x210])
        self.assertIn([0x4C, 0x20, 0x1B8, 0x210], candidates)
        self.assertTrue(any(candidate[1] in {0x20, 0x18, 0x10, 0x28, 0x30} for candidate in candidates))


if __name__ == '__main__':
    unittest.main()
