import hashlib
from pathlib import Path
import subprocess
import tempfile
import threading
import unittest
from unittest.mock import patch
import zipfile

import cinema_lut
import engine


class ModelImport(unittest.TestCase):
    def test_bundled_model_first_use_repair_and_unavailable(self):
        with tempfile.TemporaryDirectory() as temp:
            folder = Path(temp) / "model"
            with patch.object(cinema_lut, "_folder", return_value=folder):
                self.assertTrue(cinema_lut.ensure_auto_model())
                self.assertEqual(set(p.name for p in folder.iterdir()), set(cinema_lut._FILES))
                (folder / "cinema.onnx").write_bytes(b"stale cache")
                self.assertTrue(cinema_lut.ensure_auto_model())
                self.assertTrue(cinema_lut._verify(folder / "cinema.onnx", cinema_lut._FILES["cinema.onnx"]))
                unavailable = Path(temp) / "damaged.zip"
                unavailable.write_bytes(b"broken model bundle")
                (folder / "bases.f32").unlink()
                with patch.object(cinema_lut, "_BUNDLED_PACK", unavailable):
                    self.assertFalse(cinema_lut.ensure_auto_model())

    def test_rejected_pack_and_repair_leave_other_files_alone(self):
        data = b"small offline model fixture"
        files = {"known.bin": (len(data), hashlib.sha256(data).hexdigest())}
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            pack = root / "pack.zip"
            folder = root / "models" / "cinema"
            with patch.object(cinema_lut, "_folder", return_value=folder), patch.object(cinema_lut, "_FILES", files):
                with self.assertRaisesRegex(ValueError, "缺少"):
                    cinema_lut.require_model()
                with zipfile.ZipFile(pack,"w") as archive:
                    archive.writestr("known.bin", data)
                    archive.writestr("../extra", b"x")
                with self.assertRaisesRegex(ValueError, "校验失败"):
                    cinema_lut.install_model(pack)
                self.assertFalse(folder.exists())
                with zipfile.ZipFile(pack, "w") as archive:
                    archive.writestr("known.bin", data)
                self.assertEqual(cinema_lut.install_model(pack), folder)
                (folder / "keep.txt").write_text("keep", encoding="ascii")
                (folder / "known.bin").write_bytes(b"damaged")
                self.assertEqual(cinema_lut.install_model(pack), folder)
                self.assertEqual((folder / "keep.txt").read_bytes(), b"keep")
                with zipfile.ZipFile(pack, "w") as archive:
                    archive.writestr("known.bin", b"x" * len(data))
                with self.assertRaisesRegex(ValueError, "校验失败"):
                    cinema_lut.install_model(pack)
                self.assertEqual(cinema_lut.require_model(), folder)


class FramePlan(unittest.TestCase):
    def test_grouping_and_cancel_before_model_load(self):
        self.assertEqual(cinema_lut._indices([dict(start=0,length=3),dict(start=3,length=1)],4),[[0,1,2],[3]])
        with self.assertRaises(ValueError):
            cinema_lut._indices([dict(start=0,length=3),dict(start=4,length=1)], 4)
        cancel = threading.Event()
        cancel.set()
        with self.assertRaises(InterruptedError):
            cinema_lut.prepare_grades(None,None,None,None,None,engine._Runner(cancel))

    def test_large_balanced_expression_is_read_from_file(self):
        try:
            ff = engine._tool("ffmpeg")
        except FileNotFoundError as exc:
            self.skipTest(str(exc))
        positions = list(range(2, 30002, 3))
        with tempfile.TemporaryDirectory() as temp:
            script = Path(temp) / "select.txt"
            script.write_text(f"select='{cinema_lut._selection(positions)}'",encoding="ascii")
            self.assertGreater(script.stat().st_size,32767)
            out = subprocess.check_output([ff, "-v", "error", "-f", "lavfi", "-i", "color=s=16x16:r=1:d=10",
                       "-/filter:v", script, "-fps_mode", "passthrough", "-f", "framehash", "-"])
            self.assertEqual(len([r for r in out.splitlines() if r.startswith(b"0,")]),3)

    def test_unselected_timeline_needs_no_model(self):
        shots = [dict(start=0, length=2, auto_cinema=False)]
        with patch.object(cinema_lut, "require_model", side_effect=AssertionError("unused")):
            basis, records = cinema_lut.prepare_grades(None, None, [0, 10], shots, None,
                                                       engine._Runner(None), selected_only=True)
        self.assertIsNone(basis)
        self.assertEqual(records, shots)


class NativeModel(unittest.TestCase):
    def test_vfr_offset_odd_crop_range_and_hold(self):
        try:
            cinema_lut.require_model()
            ff = engine._tool("ffmpeg")
            engine._tool("ffprobe")
        except (FileNotFoundError, ValueError) as exc:
            self.skipTest(str(exc))
        with tempfile.TemporaryDirectory() as temp:
            folder = Path(temp)
            mv = folder / "input.mkv"
            base = "nullsrc=size=37x29:r=24:d=0.5,format=yuv444p10le,geq=lum=512:cb=512:cr=512"
            for tag in ("pc","tv"):
                subprocess.run([ff,"-y","-v","error","-f","lavfi","-i",base,
                    "-vf","setpts='N+7+if(gte(N,4),2,0)'","-color_range",tag, "-color_primaries","bt709",
                    "-colorspace","bt709","-color_trc","bt709","-c:v","ffv1","-fps_mode","passthrough",mv],check=True)
                run = engine._Runner(None)
                v = engine._video(engine._probe(mv,run))
                pts = sorted(engine._times(mv,v["index"],run))
                split = [dict(start=0,length=1),dict(start=1,length=len(pts)-1)]
                updates = []
                _, rec = cinema_lut.prepare_grades(mv,v,pts,split,folder,run,
                    active_area=(1,1,3,0),progress=lambda a,b:updates.append((a,b)))
                self.assertEqual(updates[0], (0,len(pts)))
                self.assertEqual(updates[-1], (len(pts),len(pts)))
                self.assertEqual(rec[0]["weights"],rec[1]["weights"])
                self.assertEqual(rec[0]["representative"],0)
                self.assertAlmostEqual(rec[0]["means"][0], 512/1023 if tag=="pc" else (512-64)/876, delta=0.02)
                flagged = [dict(split[0], auto_cinema=False), dict(split[1], auto_cinema=True)]
                _, selected = cinema_lut.prepare_grades(mv,v,pts,flagged,folder,run,
                    active_area=(1,1,3,0), selected_only=True)
                self.assertEqual(selected[0], flagged[0])
                self.assertEqual(selected[1], dict(rec[1], auto_cinema=True))
                with self.assertRaisesRegex(ValueError,"源画面不一致"):
                    cinema_lut.prepare_grades(mv,v,[p+20 for p in pts],split,folder,run)
                self.assertFalse(list(folder.glob("cinema_grade_*")))
