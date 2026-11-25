"""Regression tests for the bounded, lossless RTX worker transport."""
from concurrent.futures import Future
from contextlib import ExitStack, redirect_stdout
from unittest import mock
import ctypes as ct
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import time
import unittest

import ai
import rtx_backend as rtx


class FastTransport(unittest.TestCase):
    def test_option_only_changes_rtx_transport(self):
        with mock.patch.object(Path, "is_file", return_value=True):
            base = ["input.mkv", "out.hevc", 3840, 2076, "rpu.bin"]
            standard = json.loads(ai.nvenc_command(*base, mode="rtx")[-1])
            fast = json.loads(ai.nvenc_command(*base, mode="rtx", speed="fast")[-1])
            self.assertEqual(fast.pop("speed"), "fast")
            self.assertEqual(fast, standard)
            with self.assertRaises(ValueError):
                ai.nvenc_command(*base, mode="rtx", speed="unknown")

    def test_partial_writes_and_closed_converter(self):
        class ShortWriter(io.BytesIO):
            def write(self, buffer):
                return super().write(buffer[:3])
        sink = ShortWriter()
        rtx._write_frame(sink, b"0123456789")
        self.assertEqual(sink.getvalue(), b"0123456789")
        with self.assertRaises(BrokenPipeError):
            rtx._write_frame(mock.Mock(write=mock.Mock(return_value=0)), b"abc")
        cancelled = Future()
        with mock.patch.object(rtx, "check_cancel", side_effect=InterruptedError), self.assertRaises(InterruptedError):
            rtx._wait_write(cancelled)

    @unittest.skipUnless(os.name == "nt","requires Windows pipe API")
    def test_large_pipe_allocation_falls_back_to_standard(self):
        with mock.patch("_winapi.CreatePipe", side_effect=OSError(8,"resource limit")), ExitStack() as stack:
            src, dst = rtx._frame_pipe(stack)
            dst.write(b"abc")
            dst.close()
            self.assertEqual(src.read(), b"abc")

    def test_pinned_memory_unavailable_keeps_pixel_storage(self):
        frame = rtx.GPUFrame.__new__(rtx.GPUFrame)
        for error in (2, 801):
            with self.subTest(error=error), ExitStack() as cleanup:
                frame.cuda = mock.Mock()
                frame.cuda.cudaHostAlloc.return_value = error
                self.assertEqual(frame.host_buffer(32, cleanup), bytearray(32))
                frame.cuda.cudaGetLastError.assert_called_once()
                frame.cuda.cudaFreeHost.assert_not_called()

    @unittest.skipUnless(os.name == "nt","requires Windows pipe API")
    def test_real_child_pipes_at_eof_failure_and_cancellation(self):
        # Replace GPU operations with a constant pattern. Use real OS pipes
        # and children, including a blocked consumer, to exercise teardown.
        actual_popen = subprocess.Popen
        width, height, count = 256, 128, 96
        input_size = width * height * 4
        output_size = 2 * width * height * 4
        self.assertGreater(output_size * count, 4 << 20)
        with tempfile.TemporaryDirectory() as folder:
            flag = Path(folder, "cancel")
            for scenario in ("ok","early_exit","cancel"):
                processes = []
                flag.unlink(missing_ok=True)
                def spawn(args, **kwargs):
                    if args[0] == "encoder":
                        code = {"ok": "import sys; data=sys.stdin.buffer.read(); "
                                      f"assert len(data) == {output_size*count} and data[0] == 33 and data[-1] == 33",
                                "early_exit": "import sys;sys.exit(7)",
                                "cancel": "import time;time.sleep(20)"}[scenario]
                    elif "-map" in args:
                        code = f"import sys; f=sys.stdout.buffer; b=bytes({input_size});\nfor i in range({count}):f.write(b)"
                    else:
                        code = "import sys,shutil;shutil.copyfileobj(sys.stdin.buffer,sys.stdout.buffer,65536)"
                    child = actual_popen([sys.executable, "-c",code], stderr=subprocess.DEVNULL, **kwargs)
                    processes.append(child)
                    return child

                class FakeGPU:
                    def __init__(self,*_): pass
                    def close(self): pass
                    def host_buffer(self,size,stack): return bytearray(size)
                    def upload(self, data): pass
                    def download(self, capsule, out, w, h):
                        out[:] = b"!" * len(out)
                sdk = mock.MagicMock()
                sdk.QualityLevel.HIGHBITRATE_ULTRA.name = "HIGHBITRATE_ULTRA"
                src = {"width":width,"height":height,"avg_frame_rate":"24/1","pix_fmt":"yuv420p10le","color_range":"tv"}
                config = dict(source="source",output=str(Path(folder,"out.hevc")),width=width*2,height=height,
                              rpu=None,quality=18,max_bitrate=60000,mastering=None,max_cll=None,speed="fast")
                with self.subTest(scenario=scenario), ExitStack() as patches:
                    # The desktop worker sends UTF-8 text to its parent. Capture
                    # it independently of the test runner's Windows code page.
                    progress = patches.enter_context(redirect_stdout(io.StringIO()))
                    def patch(target, **kwargs):
                        return patches.enter_context(mock.patch.object(rtx,target,**kwargs))
                    patch("ChildJob")
                    patch("GPUFrame",new=FakeGPU)
                    patch("load_sdk",return_value=(sdk,Path(folder)))
                    patch("decoder_input",return_value=(src,["-i","source"]))
                    patch("bundled_tool",return_value="ffmpeg")
                    patch("nvenc_command",return_value=["encoder"])
                    patches.enter_context(mock.patch.object(subprocess,"Popen",side_effect=spawn))
                    patches.enter_context(mock.patch.dict(os.environ,{"CINEMA_RTX_CANCEL":str(flag)}))
                    start = time.monotonic()
                    if scenario == "ok":
                        self.assertEqual(rtx.run_worker(config),0)
                        self.assertIn(f"progress_frame={count}", progress.getvalue().splitlines())
                    elif scenario == "early_exit":
                        with self.assertRaises((OSError,RuntimeError)):
                            rtx.run_worker(config)
                    else:
                        trigger = threading.Timer(.3, flag.touch)
                        trigger.start()
                        try:
                            with self.assertRaises(InterruptedError):
                                rtx.run_worker(config)
                        finally:
                            trigger.cancel()
                            trigger.join()
                    self.assertLess(time.monotonic()-start,8)
                    self.assertEqual(len(processes),3)
                    self.assertTrue(all(p.poll() is not None for p in processes))
