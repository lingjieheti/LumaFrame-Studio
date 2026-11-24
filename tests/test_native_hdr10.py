import struct
import unittest

from hdr10 import NativeMetadata


def sei(*messages):
    data = b"".join(bytes((kind, len(value))) + value for kind, value in messages) + b"\x80"
    # Encode the RBSP so the tests exercise escaped zeros in mastering levels.
    escaped = bytearray()
    zeros = 0
    for value in data:
        if zeros == 2 and value < 4:
            escaped.append(3)
            zeros = 0
        escaped.append(value)
        zeros = zeros + 1 if value == 0 else 0
    return b"\x4e\x01" + escaped


class NativeLevels(unittest.TestCase):
    master = (137, bytes(16) + struct.pack(">II", 40_000_000, 50))
    lights = (144, struct.pack(">HH", 2548, 595))

    def test_static_levels_are_read_with_their_original_units(self):
        metadata = NativeMetadata()
        metadata.observe(39, sei(self.master))
        self.assertEqual(metadata.level6(), dict(mastering_max=4000, mastering_min=50, max_cll=0, max_fall=0))
        metadata.observe(39, sei(self.master, self.lights))
        self.assertEqual(metadata.level6()["max_fall"], 595)
        self.assertEqual(metadata.level6()["max_cll"], 2548)
        # Recurring parameter sets may repeat identical static values.
        metadata.observe(39, sei(self.lights, self.master))
        with self.assertRaises(ValueError):
            metadata.observe(40, sei((144, struct.pack(">HH", 4000, 1000))))

    def test_unknown_master_and_late_dynamic_metadata_are_rejected(self):
        metadata = NativeMetadata()
        with self.assertRaises(ValueError):
            metadata.level6()
        metadata.observe(39, sei(self.master))
        for kind, unit in [(62, b"\x7c\x01"), (1, b"\x02\x09"),
                           (39, sei((4, bytes.fromhex("b5003c00010401")))),
                           (39, b"\x4e\x01\x89\x18\xff\x80")]:
            with self.subTest(kind=kind, unit=unit), self.assertRaises(ValueError):
                metadata.observe(kind, unit)
