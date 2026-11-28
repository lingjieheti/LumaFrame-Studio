"""Choose output and picture processing from the probed stream and local GPU."""

import ctypes
from functools import lru_cache
import sys


@lru_cache(maxsize=1)
def nvidia_gpu_name():
    """Read CUDA device zero, the device used by the RTX worker, without downloading the SDK."""
    if sys.platform != "win32":
        return ""
    try:
        # Use the system driver even if an unrelated DLL is on the media path.
        driver = ctypes.WinDLL("nvcuda.dll", winmode=0x800)
        device = ctypes.c_int()
        name = ctypes.create_string_buffer(256)
        if (driver.cuInit(0) or driver.cuDeviceGet(ctypes.byref(device), 0)
                or driver.cuDeviceGetName(name, len(name), device)):
            return ""
        return name.value.decode("utf-8", errors="replace").strip()
    except (OSError, AttributeError):
        return ""


def output_targets(info):
    """The first supported delivery format is the automatic choice."""
    if info.get("can_keep7"):
        return ["keep7", "preserve"]
    if info["source_type"] == "dv":
        return ["preserve"]
    if info["source_type"] == "hdr10" and (info["width"], info["height"]) == (
        info["target_width"], info["target_height"]
    ):
        return ["newdv"]
    return ["newdv", "hdr10"]


def recommended_mode(info, target, *, gpu_name=None):
    """Preserve native pictures and apply super-resolution only to a real enlargement."""
    if target == "keep7":
        # The engine's keep7 reference route copies both native layers.
        return "reference"
    if target == "preserve" and info.get("can_copy8"):
        return "copy"
    width, height = info["width"], info["height"]
    target_width, target_height = info["target_width"], info["target_height"]
    if target == "newdv" and info["source_type"] == "hdr10" and (width, height) == (target_width, target_height):
        return "copy"
    ratio = min(target_width / width, target_height / height)
    if ratio <= 1:
        # Includes native MEL base-layer preservation and FEL reconstruction.
        return "reference"
    if gpu_name is None:
        gpu_name = nvidia_gpu_name()
    name = gpu_name.upper()
    if "RTX" in name.split():
        return "rtx"
    if ratio > 1.30 and name.removeprefix("NVIDIA ").startswith(("GEFORCE ", "QUADRO ", "TITAN ")):
        # FSRCNNX uses NVIDIA Vulkan, but does not require the RTX Video SDK.
        return "neural"
    return "reference"
