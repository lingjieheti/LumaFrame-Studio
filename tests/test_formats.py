import copy
import sys
import unittest
from fractions import Fraction
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import dovi
import engine
from profile5 import validate_profile5


class FormatChecks(unittest.TestCase):
    def setUp(self):
        self.stream = dict(
            codec_name="hevc",
            pix_fmt="yuv420p10le",
            color_range="tv",
            color_transfer="smpte2084",
            color_space="bt2020nc",
            color_primaries="bt2020",
            side_data_list=[
                dict(
                    side_data_type="DOVI configuration record",
                    dv_profile=8,
                    dv_bl_signal_compatibility_id=1,
                    bl_present_flag=1,
                    el_present_flag=0,
                    rpu_present_flag=1,
                )
            ],
        )

    def test_profile_81_requires_matching_base_and_metadata(self):
        self.assertEqual(dovi.validate_profile(self.stream)["dv_profile"], 8)
        for field, value in [
            ("el_present_flag", 1),
            ("rpu_present_flag", 0),
            ("dv_bl_signal_compatibility_id", 4),
        ]:
            sample = copy.deepcopy(self.stream)
            sample["side_data_list"][0][field] = value
            with self.assertRaises(ValueError):
                dovi.validate_profile(sample)
        self.stream["color_transfer"] = "bt709"
        with self.assertRaises(ValueError):
            dovi.validate_profile(self.stream)

    def test_p5_requires_its_own_path(self):
        rec = self.stream["side_data_list"][0]
        rec.update(dv_profile=5, dv_bl_signal_compatibility_id=0)
        self.assertEqual(validate_profile5(self.stream)["dv_profile"], 5)
        with self.assertRaises(ValueError):
            dovi.validate_profile(self.stream)

    def test_widescreen_and_existing_dci(self):
        for w, h, out in [
            (1920, 1080, (3840, 2160)),
            (2560, 1080, (3840, 1620)),
            (4096, 2160, (4096, 2160)),
        ]:
            self.assertEqual(engine._geometry(w, h, Fraction(1)), out)


if __name__ == "__main__":
    unittest.main()
