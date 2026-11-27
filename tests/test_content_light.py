"""Static HDR10 light levels, metadata rewriting and independent PQ regressions."""
from array import array
import io
import json
import math
import os
from pathlib import Path
import struct
import subprocess
import tempfile
import threading
import unittest

import engine
import hdr10


def reference_nits(y, u=512, v=512):
    """BT.2020 nonconstant YCbCr matrix and ST 2084 EOTF in double precision."""
    yp, cb, cr = (y - 64) / 876, (u - 512) / 896, (v - 512) / 896
    red = yp + 2 * (1 - .2627) * cr
    blue = yp + 2 * (1 - .0593) * cb
    green = (yp - .2627 * red - .0593 * blue) / (1 - .2627 - .0593)
    prime = min(1.0, max(0.0, red, blue, green)) ** (32 / 2523)
    return 10000 * (max(prime - 3424 / 4096, 0) / (2413 / 128 - 2392 / 128 * prime)) ** (16384 / 2610)


def plane(code, count):
    return array("H", [code]) * count


class ContentLight(unittest.TestCase):
    def test_replace_only_base_light_messages(self):
        old_cll = (144, struct.pack(">HH", 1000, 400))
        other = (270, b"\x00\x00\x00\x01\x02" * 75)
        mixed = hdr10._make_sei(b"\x4e\x01", [old_cll, other, old_cll])
        enhancement = hdr10._make_sei(b"\x4e\x09", [old_cll])
        suffix = hdr10._make_sei(b"\x50\x01", [old_cll])
        vcl, rpu = b"\x26\x01\x80\x99", b"\x7c\x01\x19\x80"
        units = [mixed, enhancement, vcl, suffix, rpu, vcl]
        with tempfile.TemporaryDirectory() as td:
            src, dst = Path(td, "test.hevc"), Path(td, "new.hevc")
            src.write_bytes(b"".join(b"\0\0\1" + s for s in units))
            hdr10.write_content_light(src, dst, (629, 301))
            with dst.open("rb") as reader:
                actual = [n for _, n in hdr10._units(reader, None)]
            cll = hdr10._make_sei(b"\x4e\x01", [(144, struct.pack(">HH", 629, 301))])
            self.assertEqual(actual, [hdr10._make_sei(b"\x4e\x01", [other]), enhancement, cll,
                                      vcl, rpu, cll, vcl])
            self.assertEqual(list(hdr10._messages(actual[0])), [other])
            with self.assertRaises(FileExistsError):
                hdr10.write_content_light(src, dst, (0, 0))
            event = threading.Event()
            event.set()
            cancelled = Path(td, "cancelled.hevc")
            with self.assertRaises(InterruptedError):
                hdr10.write_content_light(src, cancelled, (0, 0), event)
            self.assertFalse(cancelled.exists())

    def test_split_annex_b_boundaries(self):
        for trailing in (0, 1, 2, 3, 4, 5):
            first = b"\x02\x01" + b"a" * (1024*1024 - trailing - 6)
            for code in (b"\x00\x00\x01", b"\x00\x00\x00\x01"):
                stream = b"\x00\x00\x00\x01" + first + code + b"\x26\x01\x80"
                self.assertEqual(b"".join(a+b for a,b in hdr10._units(io.BytesIO(stream),None)), stream)

    @unittest.skipUnless(os.environ.get("LUMAFRAME_TEST_HDR10_LIGHT") == "1", "requires bundled FFmpeg")
    def test_decode_measure_replace_and_recheck_every_frame(self):
        width, height = 130, 74
        n = width * height
        ff = [engine._tool("ffmpeg"), "-nostdin", "-v", "error"]
        run = engine._Runner(None)
        with tempfile.TemporaryDirectory() as td:
            tmp = Path(td)
            for idx, (y, cb, cr) in enumerate([(64,512,512), (77,510,523), (511,512,512),
                                               (350,420,720), (250,770,481), (692,430,484)]):
                with self.subTest(y=y, cb=cb, cr=cr):
                    src, dst = tmp / f"{idx}.hevc", tmp / f"{idx}-new.hevc"
                    lum = plane(y, n)
                    base = lum.tobytes() + plane(cb, n//4).tobytes() + plane(cr, n//4).tobytes()
                    lum[0] = 940
                    highlight = lum.tobytes() + plane(cb, n//4).tobytes() + plane(cr, n//4).tobytes()
                    raw = base + highlight
                    args = [*ff, "-f", "rawvideo", "-pix_fmt", "yuv420p10le", "-s", f"{width}x{height}",
                        "-color_primaries", "bt2020", "-colorspace", "bt2020nc", "-color_trc", "smpte2084",
                        "-color_range", "tv", "-i", "pipe:0", "-color_primaries", "bt2020", "-colorspace", "bt2020nc",
                        "-color_trc", "smpte2084", "-color_range", "tv", "-c:v", "libx265", "-preset", "ultrafast",
                        "-x265-params", "lossless=1:bframes=0:keyint=1:max-cll=2000,123:colorprim=9:"
                        "transfer=16:colormatrix=9:log-level=error", src]
                    subprocess.run(args, input=raw, check=True)
                    peak, base_nits = reference_nits(940, cb, cr), reference_nits(y, cb, cr)
                    events = []
                    measured = hdr10.measure_content_light(src,(width,height),None,2,tmp,run,
                                                          progress=lambda *event: events.append(event))
                    self.assertEqual(events[0], (0, 2, "light"))
                    self.assertEqual(events[-1][:2], (2, 2))
                    self.assertEqual(measured,(math.ceil(peak),math.ceil((peak+(n-1)*base_nits)/n)))
                    masked = hdr10.measure_content_light(src,(width,height),(1,2,1,0),2,tmp,run)
                    self.assertEqual(masked,(math.ceil(base_nits),)*2)
                    hdr10.write_content_light(src,dst,measured)
                    probe = json.loads(run([engine._tool("ffprobe"),"-v","error","-show_frames",
                        "-show_entries","frame=side_data_list","-of","json",dst]))["frames"]
                    for frame in probe:
                        lights = [s for s in frame["side_data_list"] if s["side_data_type"]=="Content light level metadata"]
                        self.assertEqual([(s["max_content"],s["max_average"]) for s in lights], [measured])
                    self.assertEqual(subprocess.check_output([*ff,"-i",dst,"-pix_fmt","yuv420p10le",
                        "-fps_mode","passthrough","-f","rawvideo","-"]),raw)
