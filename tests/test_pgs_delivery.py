"""PGS display and clear packets must survive a remux on their original timeline."""
import json
from pathlib import Path
import struct
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import engine


def display(timestamp_ms, number, visible, luma=235):
    """Write a 2 × 2 PGS bitmap, or a composition that clears the previous one."""
    def segment(kind, payload):
        clock = timestamp_ms * 90
        return b"PG" + struct.pack(">IIBH", clock, clock, kind, len(payload)) + payload

    composition = struct.pack(">HHBHBBBB", 1920, 1080, 0x10, number,
                              0x80 if visible else 0, 0, 0, int(visible))
    if visible:
        composition += struct.pack(">HBBHH", 0, 0, 0, 500, 400)
    parts = [segment(0x16, composition)]
    if visible:
        parts += [
            segment(0x17, struct.pack(">BBHHHH", 1, 0, 500, 400, 2, 2)),
            segment(0x14, bytes([0, 0, 1, luma, 128, 128, 255])),
            segment(0x15, bytes.fromhex("000000c000000c000200020101000001010000")),
        ]
    return b"".join(parts) + segment(0x80, b"")


class PgsDelivery(unittest.TestCase):
    def test_only_unrepresentable_timestamps_may_round_to_a_matroska_tick(self):
        packet = (35_123, 35_123, 15_454, b"payload")
        quantized = (35_100, 35_100, 15_500, b"payload")
        self.assertTrue(engine._same_pgs([packet], [quantized]))
        for bad in [(36_100, 35_100, 15_500, b"payload"), (35_100, 35_100, 16_500, b"payload"),
                    (35_100, 35_100, 15_500, b"other image")]:
            self.assertFalse(engine._same_pgs([packet], [bad]))
        self.assertFalse(engine._same_pgs([quantized], [(35_200, 35_100, 15_500, b"payload")]))

    def test_missing_hash_is_not_an_empty_cue(self):
        def probe(args, callback, *, collect):
            callback("1,2.000000,2.000000,N/A,30,8512,")
        with patch.object(engine, "_tool", return_value="ffprobe"), self.assertRaisesRegex(ValueError, "PGS"):
            engine._times("input", 0, probe, pgs={1: []})

    def test_real_bilingual_remux_uses_packets_and_detects_changed_clear_image_or_language(self):
        try:
            ff, probe, merge = [engine._tool(name) for name in ("ffmpeg", "ffprobe", "mkvmerge")]
        except FileNotFoundError as error:
            self.skipTest(str(error))
        run = engine._Runner(None)
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            video, source, same = [root / (x + ".mkv") for x in ("video", "source", "same")]
            run([ff, "-v", "error", "-f", "lavfi", "-i", "testsrc2=size=320x180:rate=24:duration=9",
                 "-c:v", "mpeg4", "-q:v", "3", video])

            def make(path, *, clear_time=4318, luma=235, language="zho"):
                sub = path.with_suffix(".sup")
                sub.write_bytes(display(2878, 1, True, luma) + display(clear_time, 2, False)
                                + display(7891, 3, True) + display(8182, 4, False))
                run([merge, "--timestamp-scale", "100000", "-o", path, video, "--language",
                     "0:" + language, sub, "--language", "0:eng", sub])

            def inspect(path, probe_size):
                info = json.loads(run([probe, "-v", "error", "-probesize", probe_size,
                                       "-show_streams", "-show_chapters", "-of", "json", path]))
                rows = engine._pgs_tracks(info)
                engine._times(path, 0, run, with_end=True, pgs=rows)
                return info, engine._mkv_inventory(path, run), rows

            base = root / "bilingual.mkv"
            make(base)
            run([ff, "-v", "error", "-i", base, "-map", "0", "-c", "copy", "-metadata:s:s:0",
                 "language=zho", source])
            run([merge, "--timestamp-scale", "100000", "-o", same, "--language", "1:chi", source])
            src, inventory, original_packets = inspect(source, "5000000")

            def verify(path):
                out, after, packets = inspect(path, "32768")
                engine._verify_other(src, out, inventory, after, [], False, source, path, run,
                                     pgs_packets=(original_packets, packets))
                return out, packets

            out, packets = verify(same)
            self.assertEqual(src["streams"][1]["tags"]["language"], "zho")
            self.assertEqual(out["streams"][1]["tags"]["language"], "chi")
            self.assertNotEqual(src["streams"][1]["start_time"], out["streams"][1]["start_time"])
            self.assertEqual([len(rows) for rows in packets.values()], [4, 4])
            self.assertEqual(packets[1][0][0], 2_878_000)
            for change in (dict(clear_time=4319), dict(luma=234), dict(language="fra")):
                with self.subTest(change=change):
                    bad = root / "changed.mkv"
                    make(bad, **change)
                    with self.assertRaisesRegex(ValueError, "PGS|副轨属性"):
                        verify(bad)
                    bad.unlink()


if __name__ == "__main__":
    unittest.main()
