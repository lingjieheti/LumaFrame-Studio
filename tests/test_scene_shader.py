"""Check the bundled renderer applies the grade to the intended displayed frame."""
from array import array
import os
import subprocess
import tempfile
import unittest

from hdr import sdr_filter, write_scene_shader
from neural import ROOT


class SceneShaderTests(unittest.TestCase):
    def test_invalid_plan(self):
        with tempfile.TemporaryDirectory() as work:
            for plan in ([], [dict(start=1, length=5, strength=0.5, high_key=0)],
                         [dict(start=0, length=2, strength=float("nan"), high_key=0)]):
                with self.assertRaises(ValueError):
                    write_scene_shader(work, plan)

    @unittest.skipUnless(os.environ.get("LUMAFRAME_TEST_VULKAN") == "1", "requires NVIDIA Vulkan")
    def test_broad_white_keeps_a_monotonic_shoulder_and_the_clipped_light_core(self):
        ramp = "nullsrc=s=256x16,format=yuv444p,geq=lum='16+219*X/255':cb=128:cr=128"
        with tempfile.TemporaryDirectory() as work:
            shader = write_scene_shader(work, [dict(start=0, length=1, strength=0.08, high_key=0)])
            rows = {}
            for mode in ("expand", "scene"):
                init, graph = sdr_filter(256, 16, 256, 16, "reference", mapping=mode,
                                         scene_shader=shader, output_rgb=True)
                cmd = [str(ROOT / "bin" / "ffmpeg.exe"), "-v", "error", *init, "-f", "lavfi", "-i", ramp,
                       "-vf", graph, "-frames:v", "1", "-f", "rawvideo", "-"]
                rows[mode] = array("H", subprocess.check_output(cmd))[256 * 8:256 * 9]
            row = rows["scene"]
            self.assertTrue(all(a <= b + 1 for a, b in zip(row, row[1:])))
            self.assertEqual(row[:190], rows["expand"][:190])
            self.assertLess(row[232], rows["expand"][232] - 600)
            self.assertLessEqual(abs(row[-1] - rows["expand"][-1]), 1)

    @unittest.skipUnless(os.environ.get("LUMAFRAME_TEST_VULKAN") == "1", "requires NVIDIA Vulkan")
    def test_changes_on_exact_frame_in_reference_and_neural_graphs(self):
        # The same light-grey patch should have three distinct PQ levels. A VFR
        # gap crosses the first boundary; no output frame may shift or repeat.
        plan = [dict(start=0, length=4, strength=1, high_key=1), dict(start=4, length=5, strength=0, high_key=0),
                dict(start=9, length=6, strength=0.5, high_key=0)]
        with tempfile.TemporaryDirectory() as work:
            for mode in ("reference", "neural"):
                with self.subTest(mode=mode):
                    shader = write_scene_shader(work, plan, neural=mode == "neural")
                    init, graph = sdr_filter(64, 48, 128, 96, mode, mapping="scene", scene_shader=shader,
                                             output_rgb=True)
                    cmd = [str(ROOT / "bin" / "ffmpeg.exe"), "-hide_banner", "-loglevel", "error", *init,
                           "-f", "lavfi", "-i", "color=c=0xeaeaea:s=64x48:r=24:d=0.6", "-vf",
                           "setpts='if(lt(N,4),N,N+2)*1/(24*TB)'," + graph,
                           "-fps_mode", "passthrough", "-f", "rawvideo", "-"]
                    decoded = subprocess.run(cmd, capture_output=True)
                    self.assertEqual(decoded.returncode, 0, decoded.stderr.decode(errors="replace"))
                    self.assertEqual(decoded.stderr, b"")
                    pixels = array("H", decoded.stdout)
                    samples = list(pixels[64 * 128 + 60::128 * 96 * 3])
                    self.assertEqual(len(samples), 15)
                    for shot in plan:
                        start, length = shot["start"], shot["length"]
                        self.assertLessEqual(max(samples[start:start + length]) - min(samples[start:start + length]), 1)
                    self.assertGreater(samples[0], samples[9] + 400)
                    self.assertGreater(samples[9], samples[4] + 180)
