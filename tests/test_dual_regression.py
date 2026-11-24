"""Ensure streamed dual-layer demux propagates binary data, errors and cancellation."""

from pathlib import Path
import subprocess
import sys
import tempfile
from threading import Event
from types import SimpleNamespace
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import profile7


class DemuxPipeTests(unittest.TestCase):
    def test_binary_round_trip(self):
        pattern = bytes(range(256)) * 4096
        with tempfile.TemporaryDirectory() as folder:
            target = Path(folder) / "received.bin"
            profile7.run_pipe(
                [sys.executable, "-c", "import sys; sys.stdout.buffer.write(bytes(range(256))*4096)"],
                [
                    sys.executable,
                    "-c",
                    "import pathlib,sys; pathlib.Path(sys.argv[1]).write_bytes(sys.stdin.buffer.read())",
                    target,
                ],
                SimpleNamespace(cancel=None),
                folder,
            )
            self.assertEqual(target.read_bytes(), pattern)
            self.assertEqual(list(Path(folder).iterdir()), [target])

    def test_child_failure_and_cancellation_reap_both_processes(self):
        quiet = [sys.executable, "-c", "import time; time.sleep(60)"]
        bad = [sys.executable, "-c", "import sys; sys.stderr.write('deliberate failure'); sys.exit(7)"]
        start = subprocess.Popen
        for direction in ("upstream", "downstream", "missing", "cancel"):
            with self.subTest(direction=direction), tempfile.TemporaryDirectory() as folder:
                cancel = Event()
                children = []

                def record_start(*args, **kwargs):
                    process = start(*args, **kwargs)
                    children.append(process)
                    if direction == "cancel" and len(children) == 2:
                        cancel.set()
                    return process

                source = bad if direction == "upstream" else quiet
                sink = bad if direction == "downstream" else quiet
                expected = (RuntimeError, "deliberate failure")
                if direction == "cancel":
                    expected = (InterruptedError, "取消")
                elif direction == "missing":
                    sink = [str(Path(folder) / "absent.exe")]
                    expected = (FileNotFoundError, ".*")
                with patch.object(profile7.subprocess, "Popen", side_effect=record_start):
                    with self.assertRaisesRegex(*expected):
                        profile7.run_pipe(source, sink, SimpleNamespace(cancel=cancel), folder)
                self.assertEqual(len(children), 1 if direction == "missing" else 2)
                self.assertTrue(all(child.poll() is not None for child in children))
                self.assertEqual(list(Path(folder).iterdir()), [])


if __name__ == "__main__":
    unittest.main()
