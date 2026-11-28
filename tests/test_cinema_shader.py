from array import array
from pathlib import Path
import os
import subprocess
import tempfile
import threading
import unittest

from cinema_shader import write_cinema_shader
from hdr import sdr_filter, write_scene_shader
from neural import ROOT


def record(start, length, basis=0, *, bright=False, headroom=0):
    return dict(start=start, length=length, strength=1.0, high_key=1.0 if bright else 0.0,
                means=[0.95 if bright else 0.4] * 3, headroom=headroom,
                weights=[[float(i == basis) for i in range(5)] for _ in range(3)])


class CinemaShader(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.folder = Path(temp.name)
        self.basis = self.folder / "basis.f32"
        # Constant tables in different basis slots make a wrong shot, axis,
        # or layer offset visible without redistributing any model weights.
        values = array("f")
        for branch in range(3):
            for slot in range(5):
                for channel in range(3):
                    value = 0.2 + slot * 0.11 + branch * 0.025 + channel * 0.012
                    values.extend(array("f", [value]) * 17**3)
        self.basis.write_bytes(values.tobytes())

    def test_long_shot_lists_share_luts(self):
        small = write_cinema_shader(self.folder, self.basis, [record(0, 1)]).stat().st_size
        records = [record(i, 1) for i in range(5000)]
        large = write_cinema_shader(self.folder, self.basis, records).stat().st_size
        self.assertLess(large - small, 1_200_000)
        records[-1]["start"] += 1
        with self.assertRaises(ValueError):
            write_cinema_shader(self.folder, self.basis, records)
        cancel = threading.Event()
        cancel.set()
        with self.assertRaises(InterruptedError):
            write_cinema_shader(self.folder, self.basis, records, cancel=cancel)

    @unittest.skipUnless(os.environ.get("LUMAFRAME_TEST_VULKAN") == "1", "requires NVIDIA Vulkan")
    def test_vfr_bounds_high_key_and_superwhite_fallback(self):
        # Four cuts with a non-uniform input timeline. Middle grey should
        # select learned tables in shots 1/3 and the existing curve in 2/4.
        records = [record(0, 3), record(3, 2, bright=True), record(5, 3, 3),
                   record(8, 4, headroom=0.9)]
        for mode in ("reference", "neural"):
            with self.subTest(mode=mode):
                neural = mode == "neural"
                width, height = (128, 96) if neural else (64, 48)
                inputs = ["-f", "lavfi", "-i", "color=c=gray:s=64x48:r=24:d=0.5"]
                def center(shader, mapping="scene"):
                    init, vf = sdr_filter(64, 48, width, height, mode, mapping=mapping,
                                          scene_shader=shader, output_rgb=True)
                    graph = "setpts='if(lt(N,5),N,N+3)*1/(24*TB)'," + vf
                    cmd = [str(ROOT / "bin/ffmpeg.exe"), "-v", "error", *init, *inputs,
                           "-vf", graph, "-fps_mode", "passthrough", "-f", "rawvideo", "-"]
                    process = subprocess.run(cmd, capture_output=True)
                    self.assertEqual(process.returncode, 0, process.stderr.decode(errors="replace"))
                    pixels = array("H", process.stdout)
                    return list(pixels[(height//2)*width + width//2::width*height*3])
                old = center(write_scene_shader(self.folder, records, neural=neural))
                actual = center(write_cinema_shader(self.folder, self.basis, records, neural=neural))
                # Disable only the first shot. It has ordinary luma and no reserve,
                # so equality here depends on the automatic routing flag alone.
                unselected = {k: v for k, v in records[0].items() if k not in ("means", "weights")}
                mixed = [dict(unselected, auto_cinema=False), *records[1:]]
                auto = center(write_cinema_shader(self.folder, self.basis, mixed, neural=neural), "auto")
                self.assertEqual(auto[:3], old[:3])
                self.assertEqual(auto[3:], actual[3:])
                self.assertEqual(len(actual), 12)
                self.assertEqual(actual[3:5], old[3:5])
                self.assertEqual(actual[8:], old[8:])
                self.assertTrue(all(x == actual[0] for x in actual[:3]))
                self.assertTrue(all(x == actual[5] for x in actual[5:8]))
                self.assertGreater(actual[5], actual[0] + 10000)

    @unittest.skipUnless(os.environ.get("LUMAFRAME_TEST_VULKAN") == "1", "requires NVIDIA Vulkan")
    def test_hdr_model_output_respects_display_target_and_retains_black(self):
        # A model returning 10,000-nit white for every input is an extreme
        # check of the shoulder and the fades that protect absolute black.
        self.basis.write_bytes((array("f", [1.0]) * (3 * 5 * 3 * 17**3)).tobytes())
        shot = record(0, 1)
        shader = write_cinema_shader(self.folder, self.basis, [shot])
        init, vf = sdr_filter(64, 48, 64, 48, "reference", mapping="scene", scene_shader=shader, output_rgb=True)
        cmd = [str(ROOT / "bin/ffmpeg.exe"), "-v", "error", *init, "-f", "lavfi", "-i",
               "nullsrc=s=64x48,format=yuv444p,geq=lum='if(lt(X,32),16,235)':cb=128:cr=128",
               "-vf", vf, "-frames:v", "1", "-f", "rawvideo", "-"]
        pixels = array("H", subprocess.check_output(cmd))
        # ST 2084: 980 nits = 0.750, 1000 nits = 0.751827.
        self.assertLess(pixels[64*24 + 16], 30)
        self.assertGreater(pixels[64*24 + 48] / 65535, 0.748)
        self.assertLess(pixels[64*24 + 48] / 65535, 0.7521)
