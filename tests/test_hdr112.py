import os
from pathlib import Path
import struct
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import engine
import hdr


class SourceSignals(unittest.TestCase):
    def test_in_band_rpu_overrides_explicit_sdr_labels(self):
        stream = dict(index=0, width=1920, height=1080, codec_name="hevc", codec_type="video",
                      pix_fmt="yuv420p10le", avg_frame_rate="24/1", color_range="tv",
                      color_space="bt709", color_transfer="bt709", color_primaries="bt709")
        for markers, accepted in [('{"side_data_type":"Dolby Vision RPU Data"}', False),
                                  ('{"side_data_type":"HDR Dynamic Metadata SMPTE2094-40"}', False),
                                  ('{"frames":[{}]}', True)]:
            with self.subTest(markers=markers), patch.object(engine, "_tool", return_value="ffprobe"):
                info = engine._inspect("tagged.ts", {"streams": [stream]}, lambda args: markers)
                self.assertEqual(info["supported"], accepted)


@unittest.skipUnless(os.environ.get("LUMAFRAME_TEST_VULKAN") == "1", "requires NVIDIA Vulkan and bundled FFmpeg")
class PQColorPatches(unittest.TestCase):
    def test_saturated_colors_and_neutral_have_the_same_pq_peak(self):
        # Exact PQ peak 0.75 for each patch before limited-range YCbCr rounding.
        colors = ((0.75, 0, 0), (0, 0.75, 0), (0, 0, 0.75), (0.75,) * 3)
        with tempfile.TemporaryDirectory() as folder:
            for index, (red, green, blue) in enumerate(colors):
                y = 0.2627 * red + 0.678 * green + 0.0593 * blue
                values = (64 + 876 * y, 512 + 448 * (blue-y) / (1-0.0593),
                          512 + 448 * (red-y) / (1-0.2627))
                frame = b"".join(struct.pack("<H", round(value)) * size
                                 for value, size in zip(values, (480*270, 240*135, 240*135)))
                clip = Path(folder) / f"patch-{index}.hevc"
                tags = ["-color_primaries", "bt2020", "-colorspace", "bt2020nc",
                        "-color_trc", "smpte2084", "-color_range", "tv"]
                subprocess.run([engine._tool("ffmpeg"), "-v", "error", "-f", "rawvideo",
                                "-pixel_format", "yuv420p10le", "-video_size", "480x270",
                                "-framerate", "24", *tags, "-i", "pipe:0", *tags,
                                "-c:v", "libx265", "-x265-params", "lossless=1:log-level=error",
                                clip], input=frame*3, check=True)
                scenes = hdr.measure_output(clip, folder, engine._Runner(None))
                self.assertEqual([(s["start"], s["length"]) for s in scenes], [(0, 3)])
                for key in ("min_pq", "avg_pq", "max_pq"):
                    self.assertAlmostEqual(scenes[0][key], 3071, delta=5)


if __name__ == "__main__":
    unittest.main()
