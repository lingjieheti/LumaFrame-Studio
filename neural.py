"""GPU filtering with explicitly tagged input/output and replaceable libplacebo shaders."""

from pathlib import Path

ROOT = Path(__file__).resolve().parent
SHADER = ROOT / "shaders" / "FSRCNNX_x2_16-0-4-1.glsl"


def filter_path(path):
    # Escape both FFmpeg's option parser and its filtergraph grammar.
    value = Path(path).resolve().as_posix()
    for char in ("\\", "'", ":"):
        value = value.replace(char, "\\" + char)
    return "".join("\\" + c if c in "\\'[],;" else c for c in value)


def gpu_filter(width, height, target_width, target_height, *, neural, inverse, shader_override=None):
    init = ["-init_hw_device", "vulkan=lf:,vendor_id=0x10de", "-filter_hw_device", "lf"]
    params = [
        f"w={int(target_width)}",
        f"h={int(target_height)}",
        "format=yuv420p10le",
        "colorspace=bt2020nc",
        "color_primaries=bt2020",
        "color_trc=smpte2084",
        "range=tv",
        "apply_dolbyvision=0",
        "deband=0",
        "upscaler=spline36",
        "antiringing=0.75",
        "peak_detect=0",
        "dithering=blue",
        "dither_temporal=0",
    ]
    if shader_override is not None:
        params.append("custom_shader_path=" + filter_path(shader_override))
    elif neural and min(target_width / width, target_height / height) > 1.30:
        if not SHADER.is_file():
            raise RuntimeError("缺少 FSRCNNX 模型文件。请重新解压完整软件包。")
        params.append("custom_shader_path=" + filter_path(SHADER))
    if inverse:
        params.extend(["tonemapping=bt.2446a", "inverse_tonemapping=1"])
    vf = "hwupload,libplacebo=" + ":".join(params) + ",hwdownload,format=yuv420p10le"
    return init, vf


def ff_filter(width, height, target_width, target_height):
    return gpu_filter(width, height, target_width, target_height, neural=True, inverse=False)
