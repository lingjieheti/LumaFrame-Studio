"""Profile 7 layout and evidence checks that do not require a GPU or private footage."""

from pathlib import Path
import struct
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import profile7


def box(tag, payload):
    return struct.pack(">I4s", len(payload) + 8, tag) + payload


def sample_track(track_id, base=None):
    header = box(b"tkhd", bytes(12) + track_id.to_bytes(4, "big"))
    reference = box(b"tref", box(b"vdep", base.to_bytes(4, "big"))) if base is not None else b""
    return box(b"trak", header + reference)


class DualLayerChecks(unittest.TestCase):
    def test_separate_tracks_need_explicit_dependency_and_dolby_configuration(self):
        base = dict(index=1, id="0x9", codec_name="hevc")
        config = dict(
            side_data_type="DOVI configuration record",
            dv_profile=7,
            el_present_flag=1,
            rpu_present_flag=1,
            bl_present_flag=0,
        )
        enhancement = dict(
            index=0, id="0x7", codec_name="hevc", codec_tag_string="dvhe", side_data_list=[config]
        )
        videos = [enhancement, base]
        data = {"format": {"format_name": "mov,mp4"}}
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "links.mp4"
            path.write_bytes(box(b"moov", sample_track(9) + sample_track(7, 9)))
            self.assertEqual(profile7.track_pair(path, data, videos), (base, enhancement))
            self.assertIsNone(
                profile7.track_pair(path, data, [dict(enhancement, codec_tag_string="hev1"), base])
            )
            self.assertIsNone(
                profile7.track_pair(path, data, [enhancement, dict(base, side_data_list=[config])])
            )
            path.write_bytes(box(b"moov", sample_track(9) + sample_track(7, 8)))
            self.assertIsNone(profile7.track_pair(path, data, videos))

    def test_profile7_rpu_count_and_mixed_layers(self):
        def summary(kind, frames):
            return lambda _: f"Summary:\n  Frames: {frames}\n  Profile: 7 ({kind})\n"

        for kind in ("MEL", "FEL", "FEL, MEL"):
            with patch.object(profile7.dovi, "find_dovi_tool", return_value="tool"):
                self.assertEqual(profile7.classify("rpu", 2, summary(kind, 2)), (kind, 2))
                with self.assertRaises(ValueError):
                    profile7.classify("rpu", 3, summary(kind, 2))

    def test_native_verifier_detects_changed_picture_and_rpu(self):
        def write(path, pictures, data):
            units = [
                bytes([32 << 1, 1, 7]),
                *(bytes([2, 1, 128, n]) for n in pictures),
                bytes([62 << 1, 1, data]),
            ]
            path.write_bytes(b"".join(b"\x00\x00\x01" + u for u in units))
            return profile7.signature(path, None)

        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "layer.hevc"
            baseline = write(path, [1, 2], 33)
            self.assertEqual(baseline[0], 2)
            self.assertNotEqual(baseline, write(path, [2, 1], 33))
            self.assertNotEqual(baseline, write(path, [1, 2], 34))


if __name__ == "__main__":
    unittest.main()
