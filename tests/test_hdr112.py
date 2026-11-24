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
    def test_reference_white_and_highlight_expansion_preserve_neutral_chroma(self):
        width, height = 192, 64
        codes = (64, 502, 852, 940)
        row = b"".join(struct.pack("<H", code) * (width // len(codes)) for code in codes)
        frame = row * height + struct.pack("<H", 512) * (width * height // 2)
        results = {}
        for mapping in ("reference", "expand"):
            init, graph = hdr.sdr_filter(width, height, width, height, "reference", mapping=mapping)
            command = [engine._tool("ffmpeg"), "-v", "error", *init, "-f", "rawvideo",
                       "-pixel_format", "yuv420p10le", "-video_size", f"{width}x{height}",
                       "-color_range", "tv", "-i", "pipe:0", "-vf", graph,
                       "-frames:v", "1", "-f", "rawvideo", "pipe:1"]
            decoded = subprocess.run(command, input=frame, capture_output=True, check=True).stdout
            luma = memoryview(decoded[:width * height * 2]).cast("H")
            chroma = memoryview(decoded[width * height * 2:]).cast("H")
            cols = [width // 8 + i * width // 4 for i in range(4)]
            results[mapping] = [luma[(height // 2) * width + x] for x in cols]
            for plane in (0, width * height // 4):
                for x in cols:
                    self.assertEqual(chroma[plane + (height // 4) * (width // 2) + x // 2], 512)
        # 203 and 1000 cd/m² are limited-range 10-bit PQ codes 573 and 723.
        self.assertAlmostEqual(results["reference"][-1], 573, delta=2)
        self.assertAlmostEqual(results["expand"][-1], 723, delta=2)
        self.assertEqual(results["reference"][:2], results["expand"][:2])

    def test_native_detail_at_last_pixel_and_partial_tiles(self):
        # All channels are neutral, so the independently constructed luma is
        # also PQ MaxRGB. Include a wide frame and non-divisible tile sizes.
        for width, height in ((970, 546), (3840, 960)):
            with self.subTest(size=(width, height)), tempfile.TemporaryDirectory() as folder:
                pixels = width * height
                neutral = struct.pack("<H", 512) * (pixels // 2)
                luma = bytearray(struct.pack("<H", 105) * pixels)
                struct.pack_into("<H", luma, 0, 64)
                struct.pack_into("<H", luma, len(luma) - 2, 723)
                clip = Path(folder) / "edge.hevc"
                tags = ["-color_primaries", "bt2020", "-colorspace", "bt2020nc",
                        "-color_trc", "smpte2084", "-color_range", "tv"]
                subprocess.run([engine._tool("ffmpeg"), "-v", "error", "-f", "rawvideo",
                                "-pixel_format", "yuv420p10le", "-video_size", f"{width}x{height}",
                                *tags, "-i", "pipe:0", *tags, "-c:v", "libx265",
                                "-x265-params", "lossless=1:log-level=error", clip],
                               input=luma + neutral, check=True)
                shot, = hdr.measure_output(clip, folder, engine._Runner(None))
                self.assertEqual((shot["start"], shot["length"]), (0, 1))
                for key, code in (("min_pq", 64), ("max_pq", 723), ("avg_pq", 105)):
                    self.assertAlmostEqual(shot[key], round((code - 64) * 4095 / 876), delta=4)

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
