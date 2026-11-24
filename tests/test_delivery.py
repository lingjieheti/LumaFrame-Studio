import hashlib
from pathlib import Path
import sys
import tempfile
import threading
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import engine


class DeliveryChecks(unittest.TestCase):
    def test_native_copy_detects_mutation_in_independent_readback(self):
        with tempfile.TemporaryDirectory() as folder:
            source = Path(folder) / "source.mkv"
            target = Path(folder) / "result.mkv"
            source.write_bytes(bytes(range(256)) * 33_000)
            progress = lambda *_: None
            with target.open("xb") as copy:
                expected = engine._hash_container(source, None, progress, copy_to=copy)
            with target.open("rb") as copy:
                self.assertEqual(expected, hashlib.file_digest(copy, "sha256").hexdigest())
            with target.open("r+b") as copy:
                copy.seek(-1, 2)
                copy.write(b"\0")
            self.assertNotEqual(engine._hash_container(target, None, progress), expected)
            cancel = threading.Event()
            cancel.set()
            with self.assertRaises(InterruptedError):
                engine._hash_container(target, cancel, progress)

    @patch.object(engine, "_tool", new=lambda name: name)
    def test_packet_timestamps_are_streamed_and_checked_in_display_or_coded_order(self):
        def probe(lines):
            def emit(args, callback, *, collect):
                self.assertFalse(collect)
                for line in lines:
                    callback(line)
            return emit

        frames = ["0.000000", "0.083417", "0.041708"]
        self.assertEqual(engine._times("video", 0, probe(frames)), [0, 41_708, 83_417])
        self.assertEqual(engine._times("video", 0, probe(frames), coded_order=True),
                         [0, 83_417, 41_708])
        for invalid in ([], ["N/A"], ["0", "0.000000"]):
            with self.subTest(invalid=invalid), self.assertRaises(ValueError):
                engine._times("video", 0, probe(invalid))


if __name__ == "__main__":
    unittest.main()
