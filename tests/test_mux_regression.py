"""MP4 auxiliary-track and variable-frame-duration regressions."""

from pathlib import Path
import struct
import sys
import tempfile
import unittest
from unittest.mock import patch
from xml.etree import ElementTree

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import engine


class MovieTimeline(unittest.TestCase):
    def test_final_duration_belongs_to_last_displayed_picture(self):
        def probe(args, callback, *, collect):
            self.assertIn("packet=pts_time,duration_time:packet_side_data=", args)
            for line in ["0.000000,0.040000", "0.080000,0.400000", "0.040000,0.040000"]:
                callback(line)

        with patch.object(engine, "_tool", return_value="ffprobe"):
            pts, end = engine._times("video", 0, probe, coded_order=True, with_end=True)
        self.assertEqual(pts, [0, 80_000, 40_000])
        self.assertEqual(end, 480_000)

    def test_only_quicktime_chapter_references_identify_chapter_streams(self):
        def box(tag, body):
            return struct.pack(">I4s", len(body) + 8, tag) + body

        streams = [dict(codec_type="data", codec_tag_string="text", id=hex(n)) for n in (2, 3)]
        data = {"streams": streams, "chapters": [{"start": 0}], "format": {"format_name": "mov,mp4"}}
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "tracks.mp4"
            refs = box(b"chap", struct.pack(">I", 3)) + box(b"vdep", struct.pack(">I", 2))
            path.write_bytes(box(b"mdat", b"ignored") + box(b"moov", box(b"trak", box(b"tref", refs))))
            self.assertEqual(engine._chapter_streams(path, data), [streams[1]])

    def test_last_chapter_end_and_unicode_title_are_written_explicitly(self):
        chapter = {"start": 200, "end": 400, "time_base": "1/1000", "tags": {"title": "字幕 & 终章"}}
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "chapters.xml"
            engine._chapter_xml(path, [chapter], "zho")
            root = ElementTree.parse(path)
            self.assertEqual(root.findtext(".//ChapterTimeEnd"), "00:00:00.400000000")
            self.assertEqual(root.findtext(".//ChapterString"), chapter["tags"]["title"])

    def test_aac_preroll_correction_includes_delayed_audio_and_subtitle(self):
        info = {"fps": "24", "target_width": 3840, "target_height": 2160}
        tracks = [dict(type=kind, id=i, codec=codec, properties={})
                  for i, (kind, codec) in enumerate([
                      ("video", "HEVC"), ("audio", "AAC"), ("audio", "AAC"), ("subtitles", "Timed Text")
                  ])]
        original = {"tracks": tracks, "container": {"type": "MP4/QuickTime", "properties": {}}}
        packets = [[{"pts_time": "-0.023220", "side_data_list": [{"skip_samples": 1024}]}],
                   [{"pts_time": "0.176009"}]]
        with patch.object(engine, "_tool", return_value="mkvmerge"):
            args = []
            engine._mux("in.mp4", "picture.hevc", "out.mkv", info, "pts", original, args.extend, packets)
        shifts = [args[i + 1] for i, value in enumerate(args) if value == "--sync"]
        self.assertEqual(shifts, ["1:-23", "2:-23", "3:-23"])

    def test_converted_subtitles_check_all_visible_cues(self):
        def data(codec, index):
            return {"streams": [dict(index=index, codec_type="subtitle", codec_name=codec)]}

        src, dst = data("mov_text", 1), data("subrip", 2)
        before = [[(100_000, 100_000), (300_000, 100_000)]]
        after = [[(100_220, 100_000), (325_000, 100_000)]]
        with patch.object(engine, "_text_cues", side_effect=[before, after]):
            with self.assertRaisesRegex(ValueError, "字幕"):
                engine._verify_other(src, dst, {"tracks": []}, {"tracks": []}, [], False,
                                     "input.mp4", "output.mkv", None)


if __name__ == "__main__":
    unittest.main()
