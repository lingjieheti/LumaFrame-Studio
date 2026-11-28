import unittest
from unittest.mock import patch

from encode_policy import output_targets, recommended_mode


def source(kind, width=1920, height=1080, **flags):
    return dict(source_type=kind, width=width, height=height,
                target_width=3840, target_height=2160, **flags)


class AutomaticPictureRoute(unittest.TestCase):
    def test_native_signal_and_enhancement_layers(self):
        # Preserve/copy routes work without initializing a GPU.
        formats = [
            (source("dv", 3840, 2160, dv_profile=7, enhancement_layer=layer, can_keep7=True),
             ["keep7", "preserve"], ["reference", "reference"]) for layer in ("FEL", "MEL")
        ] + [
            (source("dv", 3840, 2160, dv_profile=8, can_copy8=True), ["preserve"], ["copy"]),
            (source("dv", 3840, 2160, dv_profile=5), ["preserve"], ["reference"]),
            (source("hdr10", 3840, 2160), ["newdv"], ["copy"]),
            (source("sdr", 3840, 2160), ["newdv", "hdr10"], ["reference", "reference"]),
        ]
        with patch("encode_policy.nvidia_gpu_name", side_effect=AssertionError("unnecessary hardware probe")):
            for info, targets, modes in formats:
                with self.subTest(info=info):
                    self.assertEqual(output_targets(info), targets)
                    self.assertEqual([recommended_mode(info, target) for target in targets], modes)

    def test_enlargement_and_hardware(self):
        for kind, profile in [("dv", 5), ("dv", 7), ("dv", 8), ("sdr", None), ("hdr10", None)]:
            info = source(kind, dv_profile=profile)
            targets = ["preserve"] if kind == "dv" else ["newdv", "hdr10"]
            self.assertEqual(output_targets(info), targets)
            for target in targets:
                for gpu, mode in [("NVIDIA GeForce RTX 5080", "rtx"), ("Quadro RTX 4000", "rtx"),
                                  ("NVIDIA GeForce GTX 1660 Ti", "neural"), ("", "reference"),
                                  ("AMD Radeon Graphics", "reference")]:
                    with self.subTest(kind=kind, target=target, gpu=gpu):
                        self.assertEqual(recommended_mode(info, target, gpu_name=gpu), mode)

    def test_partial_scaling_and_neural_threshold(self):
        with patch("encode_policy.nvidia_gpu_name", return_value="NVIDIA GeForce RTX 5080"):
            self.assertEqual(recommended_mode(source("dv", 4096, 2304), "preserve"), "reference")
            self.assertEqual(recommended_mode(source("sdr", 3840, 1080), "newdv"), "reference")
            self.assertEqual(recommended_mode(source("sdr", 3200, 1800), "newdv"), "rtx")
        self.assertEqual(recommended_mode(source("sdr", 3200, 1800), "newdv", gpu_name="GeForce GTX 1080"), "reference")
