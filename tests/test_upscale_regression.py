import json
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from ai import nvenc_command
from felbake import pq12_input_args
from rtx_backend import decoder_input


class StreamedFelInput(unittest.TestCase):
    def test_raw_worker_uses_composer_geometry_rate_and_chroma_without_reading_pipe(self):
        with patch("ai.nvenc_exe", return_value=Path(__file__)):
            cmd = nvenc_command("pipe:0", "encoded.hevc", 3840, 2160, "scaled.rpu",
                                mode="rtx", fel_input=(1920, 1080, "24000/1001"))
        params = json.loads(cmd[-1])
        with patch("rtx_backend.subprocess.check_output", side_effect=AssertionError("pipe consumed")):
            stream, input_args = decoder_input(params, 0)
        self.assertEqual(input_args, [*pq12_input_args(1920, 1080, "24000/1001"), "-i", "pipe:0"])
        self.assertEqual(stream["avg_frame_rate"], "24000/1001")
        self.assertEqual(stream["pix_fmt"], "yuv420p12le")
        self.assertEqual(input_args[input_args.index("-chroma_sample_location") + 1], "topleft")
        self.assertEqual(params["rpu"], "scaled.rpu")

    def test_raw_pq_cannot_enter_sdr_expansion(self):
        with patch("ai.nvenc_exe", return_value=Path(__file__)), self.assertRaises(ValueError):
            nvenc_command("pipe:0", "encoded.hevc", 3840, 2160, None,
                          mode="rtx", fel_input=(1920, 1080, "24"), sdr_to_hdr=True)

    def test_file_input_still_uses_its_own_dolby_metadata(self):
        file_stream = {"width": 1920, "height": 1080, "side_data_list": [{"dv_profile": 5}]}
        with patch("rtx_backend.bundled_tool", return_value="test-ffprobe"), patch(
            "rtx_backend.subprocess.check_output",
            return_value=json.dumps({"streams": [file_stream]}).encode(),
        ) as probe:
            stream, arguments = decoder_input({"source": "movie.mp4"}, 0)
        self.assertEqual(arguments, ["-i", "movie.mp4"])
        self.assertEqual(stream, file_stream)
        self.assertEqual(probe.call_args.args[0][-1], "movie.mp4")


if __name__ == "__main__":
    unittest.main()
