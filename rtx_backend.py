"""Offline HDR processing through NVIDIA's native packed 10-bit VFX interface."""

import contextlib
import ctypes as ct
import hashlib
import importlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.request
import zipfile

from ai import app_dir, nvenc_command


WHEEL = "nvidia_vfx-0.2.0.0-cp312-abi3-win_amd64.whl"
URL = "https://pypi.nvidia.com/nvidia-vfx/" + WHEEL
SHA256 = "cebaa12411f7e22b39293205c5c4d07b8073d14ca1e0e7727ee4375207a36445"


def sdk_cache():
    cache = Path(os.environ.get("LOCALAPPDATA", tempfile.gettempdir())) / "CinemaUpscale"
    return cache / "nvidia-vfx-0.2.0.0"


def check_cancel():
    cancel = os.environ.get("CINEMA_RTX_CANCEL")
    if cancel and Path(cancel).exists():
        raise InterruptedError("已取消 RTX 处理。")


def load_sdk(scratch=None):
    target = sdk_cache()
    if not target.joinpath("READY").is_file():
        print("首次使用 RTX 超分，正在从 NVIDIA 下载并验证 VFX 运行库（416 MiB）…", flush=True)
        target.parent.mkdir(parents=True, exist_ok=True)
        # Keep the large download with this render's scratch so the GUI can
        # clean it even if Windows has to force-stop the worker.
        with tempfile.TemporaryDirectory(prefix="nvvfx-", dir=scratch or target.parent) as tmp:
            tmp = Path(tmp)
            wheel = tmp / WHEEL
            digest = hashlib.sha256()
            try:
                with urllib.request.urlopen(URL, timeout=25) as response, wheel.open("wb") as out:
                    while block := response.read(1 << 20):
                        check_cancel()
                        digest.update(block)
                        out.write(block)
            except OSError as exc:
                if isinstance(exc, InterruptedError):
                    raise
                raise RuntimeError(f"NVIDIA VFX 下载失败，请检查网络或代理后重试。{exc}") from exc
            if digest.hexdigest() != SHA256:
                raise RuntimeError("NVIDIA VFX 文件校验失败，请重新运行。")
            staged = tmp / "sdk"
            with zipfile.ZipFile(wheel) as archive:
                for member in archive.infolist():
                    check_cancel()
                    archive.extract(member, staged)
            (staged / "READY").write_text(SHA256, encoding="ascii")
            try:
                staged.rename(target)
            except OSError as exc:
                if not target.joinpath("READY").is_file():
                    # When the movie and platform cache use different drives,
                    # copy to the cache's filesystem before the atomic rename.
                    if getattr(exc, "winerror", None) != 17:
                        raise
                    with tempfile.TemporaryDirectory(prefix="stage-", dir=target.parent) as transit:
                        copy = Path(transit) / "sdk"
                        shutil.copytree(staged, copy)
                        check_cancel()
                        try:
                            copy.rename(target)
                        except OSError:
                            if not target.joinpath("READY").is_file():
                                raise
    sys.path.insert(0, str(target))
    return importlib.import_module("nvvfx"), target / "nvvfx" / "libs"


class DLDevice(ct.Structure):
    _fields_ = [("device_type", ct.c_int), ("device_id", ct.c_int)]


class DLType(ct.Structure):
    _fields_ = [("code", ct.c_uint8), ("bits", ct.c_uint8), ("lanes", ct.c_uint16)]


class DLTensor(ct.Structure):
    _fields_ = [
        ("data", ct.c_void_p),
        ("device", DLDevice),
        ("ndim", ct.c_int),
        ("dtype", DLType),
        ("shape", ct.POINTER(ct.c_int64)),
        ("strides", ct.POINTER(ct.c_int64)),
        ("byte_offset", ct.c_uint64),
    ]


class DLManagedTensor(ct.Structure):
    _fields_ = [("tensor", DLTensor), ("manager_ctx", ct.c_void_p), ("deleter", ct.c_void_p)]


_name = b"dltensor"
_new_capsule = ct.pythonapi.PyCapsule_New
_new_capsule.argtypes = [ct.c_void_p, ct.c_char_p, ct.c_void_p]
_new_capsule.restype = ct.py_object
_get_capsule = ct.pythonapi.PyCapsule_GetPointer
_get_capsule.argtypes = [ct.py_object, ct.c_char_p]
_get_capsule.restype = ct.POINTER(DLManagedTensor)


@ct.CFUNCTYPE(None, ct.c_void_p)
def _borrowed(_tensor):
    # The owner frees the input allocation after the last blocking SDK call.
    pass


class GPUFrame:
    """Own one reusable CUDA allocation. Expose it as a borrowed CUDA uint32 DLPack frame."""

    def __init__(self, libs, width, height):
        self.shape = (height, width)
        self.size = width * height * 4
        self.cuda = ct.WinDLL(str(libs / "cudart64_12.dll"))
        self.cuda.cudaGetErrorString.restype = ct.c_char_p
        self.ptr = ct.c_void_p()
        self.check(self.cuda.cudaSetDevice(0))
        self.check(self.cuda.cudaMalloc(ct.byref(self.ptr), ct.c_size_t(self.size)))
        self._dims = (ct.c_int64 * 2)(*self.shape)
        self._strides = (ct.c_int64 * 2)(width, 1)
        self._tensor = DLManagedTensor(
            DLTensor(self.ptr, DLDevice(2, 0), 2, DLType(1, 32, 1), self._dims, self._strides, 0),
            None,
            ct.cast(_borrowed, ct.c_void_p),
        )

    def __dlpack_device__(self):
        return (2, 0)

    def __dlpack__(self, stream=None, **kwargs):
        return _new_capsule(ct.addressof(self._tensor), _name, None)

    def check(self, status):
        if status:
            raise RuntimeError(f"CUDA {status}: {self.cuda.cudaGetErrorString(status).decode()}")

    def upload(self, buffer):
        src = (ct.c_ubyte * self.size).from_buffer(buffer)
        self.check(self.cuda.cudaMemcpy(self.ptr, src, ct.c_size_t(self.size), 1))

    def download(self, capsule, buffer, width, height):
        tensor = _get_capsule(capsule, _name).contents.tensor
        if (
            tensor.ndim != 2
            or tuple(tensor.shape[:2]) != (height, width)
            or tensor.device.device_type != 2
            or tensor.dtype.code != 1
            or tensor.dtype.bits != 32
            or tensor.dtype.lanes != 1
        ):
            raise RuntimeError("NVIDIA VFX 返回了意外的颜色位深或尺寸。")
        if tensor.strides and tensor.strides[1] != 1:
            raise RuntimeError("NVIDIA VFX 输出像素排列不受支持。")
        pitch = (tensor.strides[0] if tensor.strides else width) * 4
        row = ct.c_size_t(width * 4)
        dst = (ct.c_ubyte * len(buffer)).from_buffer(buffer)
        self.check(
            self.cuda.cudaMemcpy2D(
                dst,
                row,
                ct.c_void_p(tensor.data + tensor.byte_offset),
                ct.c_size_t(pitch),
                row,
                ct.c_size_t(height),
                2,
            )
        )

    def close(self):
        self.cuda.cudaFree(self.ptr)


class ChildJob:
    """Contain the worker, codec tools and any NGX runtime helper processes."""

    def __init__(self):
        k = self.kernel = ct.WinDLL("kernel32", use_last_error=True)
        k.CreateJobObjectW.argtypes = [ct.c_void_p, ct.c_wchar_p]
        k.CreateJobObjectW.restype = ct.c_void_p
        k.SetInformationJobObject.argtypes = [ct.c_void_p, ct.c_int, ct.c_void_p, ct.c_ulong]
        k.AssignProcessToJobObject.argtypes = [ct.c_void_p, ct.c_void_p]
        k.CloseHandle.argtypes = [ct.c_void_p]
        self.handle = k.CreateJobObjectW(None, None)
        if not self.handle:
            raise ct.WinError(ct.get_last_error())
        # JOBOBJECT_EXTENDED_LIMIT_INFORMATION (native Win64 ABI). The
        # LimitFlags member in its BasicLimitInformation starts at byte 16.
        limits = ct.create_string_buffer(144)
        ct.c_uint32.from_buffer(limits, 16).value = 0x2000  # JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
        if not k.SetInformationJobObject(self.handle, 9, limits, ct.sizeof(limits)):
            error = ct.get_last_error()
            self.close()
            raise ct.WinError(error)

    def contain_current(self):
        self.kernel.GetCurrentProcess.restype = ct.c_void_p
        if not self.kernel.AssignProcessToJobObject(self.handle, self.kernel.GetCurrentProcess()):
            error = ct.get_last_error()
            self.close()
            raise ct.WinError(error)

    def close(self):
        self.kernel.CloseHandle(self.handle)


def bundled_tool(name):
    path = app_dir() / "bin" / (name + ".exe")
    if path.is_file():
        return str(path)
    found = shutil.which(name)
    if not found:
        raise FileNotFoundError(path)
    return found


def read_frame(stream, frame):
    dest = memoryview(frame)
    used = 0
    while used < len(frame):
        length = stream.readinto(dest[used:])
        if length == 0:
            if used:
                raise ValueError("视频解码器输出了不完整的一帧。")
            return False
        used += length
    return True


def decoder_input(params, hidden):
    src = params["source"]
    raw = params.get("fel_input")
    if raw is not None:
        from felbake import pq12_input_args

        if src != "pipe:0" or params.get("sdr_to_hdr"):
            raise ValueError("FEL 流式合成只支持从管道读入 PQ12。")
        # The composer supplies the same PQ12 pixels and chroma siting used
        # in its lossless file path. Probing a live pipe would consume frames.
        stream = {
            "width": raw["width"], "height": raw["height"], "avg_frame_rate": raw["fps"],
            "color_range": "tv", "pix_fmt": "yuv420p12le",
        }
        return stream, [*pq12_input_args(raw["width"], raw["height"], raw["fps"]), "-i", src]
    probe = subprocess.check_output(
        [
            bundled_tool("ffprobe"), "-v", "error", "-select_streams", "V:0", "-show_entries",
            "stream=width,height,avg_frame_rate,r_frame_rate,color_range,pix_fmt:stream_side_data=dv_profile",
            "-of", "json", src,
        ],
        creationflags=hidden,
    )
    return json.loads(probe)["streams"][0], ["-i", src]


def run_worker(params):
    # Windows closes this handle on process exit. Explicitly closing it would
    # kill the worker itself before it can report the final encode status.
    job = ChildJob()
    job.contain_current()
    hidden = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    env = os.environ.copy()
    w, h = params["width"], params["height"]
    stream, input_args = decoder_input(params, hidden)
    rate = stream.get("avg_frame_rate")
    if not rate or rate == "0/0":
        rate = stream["r_frame_rate"]
    iw, ih = stream["width"], stream["height"]
    if iw >= w and ih >= h:
        raise ValueError("RTX 超分需要至少一个方向的画面尺寸放大。")
    nvvfx, libs = load_sdk(Path(params["output"]).resolve().parent)
    ffmpeg = [bundled_tool("ffmpeg"), "-hide_banner", "-nostdin", "-loglevel", "error"]
    # RGB10A2 expects red in bits 0..9. FFmpeg calls exactly that layout x2bgr10le.
    source_range = "full" if stream.get("color_range") == "pc" else "limited"
    init_args, source_filter = [], ""
    if params.get("sdr_to_hdr"):
        from hdr import sdr_filter

        init_args, expansion = sdr_filter(iw, ih, iw, ih, "reference")
        full = source_range == "full" or stream["pix_fmt"].startswith("yuvj")
        tagging = "setparams=range=" + ("full" if full else "limited")
        source_filter = (
            tagging + ":colorspace=bt709:color_primaries=bt709:color_trc=bt709," + expansion + ","
        )
        source_range = "limited"
    elif any(side.get("dv_profile") == 5 for side in stream.get("side_data_list", [])):
        from profile5 import decode_profile5_filter

        init_args, color_decode = decode_profile5_filter(iw, ih, iw, ih)
        source_filter = color_decode + ","
        source_range = "limited"
    decoder = [
        *ffmpeg,
        *init_args,
        "-noautorotate",
        *input_args,
        "-map",
        "0:V:0",
        "-an",
        "-sn",
        "-dn",
        "-vf",
        source_filter
        + f"scale=in_color_matrix=bt2020:in_range={source_range}:out_range=full:flags=bicubic+accurate_rnd+full_chroma_int,format=x2bgr10le",
        "-fps_mode",
        "passthrough",
        "-f",
        "rawvideo",
        "pipe:1",
    ]
    converter = [
        *ffmpeg,
        "-f",
        "rawvideo",
        "-pixel_format",
        "x2bgr10le",
        "-video_size",
        f"{w}x{h}",
        "-framerate",
        rate,
        "-i",
        "pipe:0",
        "-vf",
        "scale=out_color_matrix=bt2020:out_chroma_loc=left:in_range=full:out_range=limited:flags=bicubic+accurate_rnd+full_chroma_int,format=yuv420p10le",
        "-strict",
        "-1",
        "-f",
        "yuv4mpegpipe",
        "pipe:1",
    ]
    encoder = nvenc_command(
        "-",
        params["output"],
        w,
        h,
        params["rpu"],
        mode="_scaled",
        quality=params["quality"],
        max_bitrate=params["max_bitrate"],
        mastering=params["mastering"],
        max_cll=params["max_cll"],
    )
    print(
        f"NVIDIA RTX Video Super Resolution | VFX {nvvfx.get_sdk_version()} | HIGHBITRATE_ULTRA "
        f"| RGB10A2 {iw}x{ih} -> {w}x{h}",
        flush=True,
    )
    created = []
    with contextlib.ExitStack() as stack:
        gpu = GPUFrame(libs, iw, ih)
        stack.callback(gpu.close)
        effect = stack.enter_context(
            nvvfx.VideoSuperRes(
                quality=nvvfx.QualityLevel.HIGHBITRATE_ULTRA,
                device=0,
                image_encoding=nvvfx.ImageEncoding.RGB10A2,
            )
        )
        effect.output_width, effect.output_height = w, h
        effect.load()
        try:
            dec = subprocess.Popen(decoder, stdout=subprocess.PIPE, creationflags=hidden, env=env)
            created.append(dec)
            conv = subprocess.Popen(
                converter, stdin=subprocess.PIPE, stdout=subprocess.PIPE, creationflags=hidden, env=env
            )
            created.append(conv)
            enc = subprocess.Popen(encoder, stdin=conv.stdout, creationflags=hidden, env=env)
            created.append(enc)
            conv.stdout.close()
            buf_in, buf_out = bytearray(iw * ih * 4), bytearray(w * h * 4)
            count = 0
            last_progress = time.monotonic()
            while read_frame(dec.stdout, buf_in):
                check_cancel()
                gpu.upload(buf_in)
                gpu.download(effect.run(gpu).image, buf_out, w, h)
                conv.stdin.write(buf_out)
                count += 1
                now = time.monotonic()
                if now - last_progress >= 0.8:
                    print(f"progress_frame={count}", flush=True)
                    last_progress = now
            print(f"progress_frame={count}", flush=True)
            conv.stdin.close()
            if any(status != 0 for status in [dec.wait(), conv.wait(), enc.wait()]) or not count:
                raise RuntimeError("RTX 解码、色彩转换或 HEVC 编码未成功，请查看上方日志。")
        finally:
            for child in reversed(created):
                if child.poll() is None:
                    child.kill()
                child.wait()
                for pipe in (child.stdin, child.stdout):
                    if pipe and not pipe.closed:
                        with contextlib.suppress(OSError):
                            pipe.close()
    return 0
