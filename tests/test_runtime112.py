"""RTX package cache recovery and concurrent initialization on Windows."""

import base64
import hashlib
import io
import os
from pathlib import Path
import sys
import tempfile
import threading
import time
import unittest
from unittest.mock import Mock, patch
import zipfile

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import rtx_backend as rtx
from felbake import _verify_rtx_frames


@unittest.skipUnless(os.name == "nt", "the NVIDIA VFX wheel and install lock are for Windows")
class RuntimeCache(unittest.TestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.cache = Path(folder.name) / "cache" / "nvidia-vfx-0.2.0.0"
        self.scratch = Path(folder.name) / "job"
        self.scratch.mkdir()
        content = b"verified NVIDIA package fixture"
        payload = "nvvfx/libs/test.dll"
        self.library = self.cache / payload
        self.content = content
        value = base64.urlsafe_b64encode(hashlib.sha256(content).digest()).rstrip(b"=").decode()
        record_path = "nvidia_vfx-0.2.0.0.dist-info/RECORD"
        record = f"{payload},sha256={value},{len(content)}\n{record_path},,\n".encode()
        archive = io.BytesIO()
        with zipfile.ZipFile(archive, "w") as wheel:
            wheel.writestr(payload, content)
            wheel.writestr(record_path, record)
        self.wheel = archive.getvalue()
        for attr, replacement in (
            ("sdk_cache", lambda: self.cache),
            ("SHA256", hashlib.sha256(self.wheel).hexdigest()),
            ("RECORD_SHA256", hashlib.sha256(record).hexdigest()),
        ):
            p = patch.object(rtx, attr, replacement, create=True)
            p.start()
            self.addCleanup(p.stop)
        p = patch.object(rtx.importlib, "import_module", return_value=object())
        p.start()
        self.addCleanup(p.stop)
        self.addCleanup(lambda: sys.path.__setitem__(slice(None), [
            value for value in sys.path if value != str(self.cache)
        ]))

    def test_repair_incomplete_existing_dir_and_damaged_dll(self):
        self.cache.mkdir(parents=True)
        (self.cache / "interrupted").touch()
        with patch.object(rtx.urllib.request, "urlopen", side_effect=lambda *a, **k: io.BytesIO(self.wheel)) as get:
            rtx.load_sdk(self.scratch)
            self.assertEqual(self.library.read_bytes(), self.content)
            self.library.write_bytes(b"x" * len(self.content))
            rtx.load_sdk(self.scratch)
            self.assertEqual(self.library.read_bytes(), self.content)
            rtx.load_sdk(self.scratch)
            self.assertEqual(get.call_count, 2)

    def test_two_renders_share_one_download(self):
        began = threading.Event()
        def slow_download(*args, **kwargs):
            began.set()
            time.sleep(0.15)
            return io.BytesIO(self.wheel)

        from concurrent.futures import ThreadPoolExecutor
        with patch.object(rtx.urllib.request, "urlopen", side_effect=slow_download) as get:
            with ThreadPoolExecutor(2) as pool:
                first = pool.submit(rtx.load_sdk, self.scratch)
                self.assertTrue(began.wait(2))
                second = pool.submit(rtx.load_sdk, self.scratch)
                first.result(5)
                second.result(5)
            self.assertEqual(get.call_count, 1)

    def test_cancel_or_corrupt_download_does_not_publish(self):
        cancel = self.scratch / "cancel"
        cancel.touch()
        with patch.dict(os.environ, CINEMA_RTX_CANCEL=str(cancel)):
            with patch.object(rtx.urllib.request, "urlopen", return_value=io.BytesIO(self.wheel)):
                with self.assertRaises(InterruptedError):
                    rtx.load_sdk(self.scratch)
        with patch.object(rtx.urllib.request, "urlopen", return_value=io.BytesIO(b"interrupted")):
            with self.assertRaisesRegex(RuntimeError, "校验"):
                rtx.load_sdk(self.scratch)
        self.assertFalse(self.cache.exists())


class RTXVerification(unittest.TestCase):
    def test_hardware_decode_and_cpu_fallback_both_check_all_frames(self):
        progress = "frame=12\nprogress=continue\nframe=24\nprogress=end\n"
        run = Mock(return_value=progress)
        _verify_rtx_frames("sample.hevc", 24, "ffmpeg", run)
        self.assertIn("-hwaccel", run.call_args.args[0])
        run = Mock(side_effect=[RuntimeError("NVDEC unavailable"), progress])
        _verify_rtx_frames("sample.hevc", 24, "ffmpeg", run)
        self.assertNotIn("-hwaccel", run.call_args.args[0])
        self.assertIn("-xerror", run.call_args.args[0])
        for result in ("frame=23\nprogress=end", "frame=24\nprogress=continue"):
            with self.assertRaisesRegex(ValueError, "帧数"):
                _verify_rtx_frames("sample.hevc", 24, "ffmpeg", Mock(return_value=result))


if __name__ == "__main__":
    unittest.main()
