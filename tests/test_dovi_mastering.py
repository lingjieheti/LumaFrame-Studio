"""HDR10 fallback derived from constant Dolby Vision CM v4 metadata."""

import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import profile5


class MasteringFallback(unittest.TestCase):
    light = dict(max_display_mastering_luminance=1000, min_display_mastering_luminance=1,
                 max_content_light_level=900, max_frame_average_light_level=300)
    cinema = dict(length=1, source_primary_index=5)
    custom = dict(length=17, source_primary_index=255, source_primary_red_x=22282,
                  source_primary_red_y=10483, source_primary_green_x=8683,
                  source_primary_green_y=22609, source_primary_blue_x=4915,
                  source_primary_blue_y=1966, source_primary_white_x=10246, source_primary_white_y=10780)

    def derive(self, gamuts, lights=None, source_master=None):
        def export(argv):
            self.assertEqual(argv[argv.index("--levels-format") + 1], "json")
            for item in argv[argv.index("--levels") + 1].split(","):
                level, name = item.split("=")
                rows = (lights or [self.light]) if level == "level6" else gamuts
                Path(name).write_text(json.dumps([dict(frame=n, **row) for n, row in enumerate(rows)]), "utf-8")

        with tempfile.TemporaryDirectory() as temp, patch.object(profile5, "find_dovi_tool", return_value="dv"):
            return profile5.static_from_rpu("frames.rpu", temp, export, source_master=source_master)

    def test_custom_xy_units_and_cinema_white(self):
        self.assertEqual(self.derive([self.custom]), (
            "G(13250,34500)B(7500,3000)R(34001,15996)WP(15635,16449)L(10000000,1)", "900,300"))
        self.assertIn("WP(15700,17550)", self.derive([self.cinema])[0])

    def test_changing_primaries_or_peak_preserves_independent_constant_fields(self):
        self.assertEqual(self.derive([self.cinema, self.custom]), (None, "900,300"))
        self.assertEqual(self.derive([self.custom, dict(self.custom, source_primary_red_x=22000)]),
                         (None, "900,300"))
        master, cll = self.derive([self.cinema], [self.light, dict(self.light, max_content_light_level=600)])
        self.assertIn("L(10000000,1)", master)
        self.assertIsNone(cll)

    def test_missing_l9_can_reuse_independently_signalled_source_primaries(self):
        self.assertEqual(self.derive([], source_master="G(1,2)B(3,4)R(5,6)WP(7,8)L(9000000,5)"),
                         ("G(1,2)B(3,4)R(5,6)WP(7,8)L(10000000,1)", "900,300"))


if __name__ == "__main__":
    unittest.main()
