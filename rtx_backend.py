"""Offline HDR processing through NVIDIA's native packed 10-bit VFX interface."""

import base64
import contextlib
import csv
import ctypes as ct
import errno
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
from concurrent.futures import ThreadPoolExecutor

from ai import app_dir, nvenc_command


WHEEL = "nvidia_vfx-0.2.0.0-cp312-abi3-win_amd64.whl"
URL = "https://pypi.nvidia.com/nvidia-vfx/" + WHEEL
SHA256 = "cebaa12411f7e22b39293205c5c4d07b8073d14ca1e0e7727ee4375207a36445"
RECORD_SHA256 = "4e05af7a8e0e8e2744736119deeffa8e7b4f5fa510dd1f89f163e28eb0acb577"


def sdk_cache():
    cache = Path(os.environ.get("LOCALAPPDATA", tempfile.gettempdir())) / "CinemaUpscale"
    return cache / "nvidia-vfx-0.2.0.0"


def check_cancel():
    cancel = os.environ.get("CINEMA_RTX_CANCEL")
    if cancel and Path(cancel).exists():
        raise InterruptedError("已取消 RTX 处理。")


def _valid_sdk(target):
    marker = target / "READY"
    record = target / "nvidia_vfx-0.2.0.0.dist-info" / "RECORD"
    try:
        if marker.read_bytes() != SHA256.encode("ascii"):
            return False
        contents = record.read_bytes()
        if hashlib.sha256(contents).hexdigest() != RECORD_SHA256:
            return False
        # The wheel's pinned RECORD lists all shipped DLLs, models and Python modules.
        for relative, checksum, size in csv.reader(contents.decode("utf-8").splitlines()):
            check_cancel()
            if not checksum:  # RECORD cannot contain its own hash.
                continue
            file = target / relative
            if file.stat().st_size != int(size):
                return False
            with file.open("rb") as stream:
                digest = hashlib.file_digest(stream, "sha256").digest()
            if base64.urlsafe_b64encode(digest).rstrip(b"=") != checksum[7:].encode("ascii"):
                return False
        return True
    except (FileNotFoundError, NotADirectoryError, IsADirectoryError):
        return False


@contextlib.contextmanager
def _install_lock(target):
    # Windows releases the byte-range lock even if a canceled worker is killed.
    import msvcrt

    target.parent.mkdir(parents=True, exist_ok=True)
    with target.with_name(target.name + ".install.lock").open("a+b") as lock:
        lock.seek(0)
        while True:
            check_cancel()
            try:
                msvcrt.locking(lock.fileno(), msvcrt.LK_NBLCK, 1)
                break
            except OSError as exc:
                if exc.errno != errno.EACCES:
                    raise
                time.sleep(0.15)
        try:
            yield
        finally:
            lock.seek(0)
            msvcrt.locking(lock.fileno(), msvcrt.LK_UNLCK, 1)


def _install_sdk(target, scratch):
    print("status=download_vfx", flush=True)
    # Stage the install on the cache volume. The verified wheel may be downloaded
    # to the movie's scratch volume, which also handles cross-drive installations.
    with tempfile.TemporaryDirectory(prefix="nvvfx-", dir=scratch or target.parent) as tmp:
        wheel = Path(tmp) / WHEEL
        digest = hashlib.sha256()
        try:
            with urllib.request.urlopen(URL, timeout=25) as response, wheel.open("wb") as out:
                while block := response.read(1 << 20):
                    check_cancel()
                    digest.update(block)
                    out.write(block)
        except OSError as exc:
            if isinstance(exc, InterruptedError) or exc.errno == errno.ENOSPC:
                raise
            raise RuntimeError(f"NVIDIA VFX 下载失败，请检查网络或代理后重试。{exc}") from exc
        if digest.hexdigest() != SHA256:
            raise RuntimeError("NVIDIA VFX 文件校验失败，请重新运行。")
        with tempfile.TemporaryDirectory(prefix=target.name + "-stage-", dir=target.parent) as folder:
            staged = Path(folder) / "new"
            with zipfile.ZipFile(wheel) as archive:
                for member in archive.infolist():
                    check_cancel()
                    path = staged / member.filename
                    if member.is_dir():
                        path.mkdir(parents=True, exist_ok=True)
                        continue
                    path.parent.mkdir(parents=True, exist_ok=True)
                    with archive.open(member) as data, path.open("xb") as unpacked:
                        while chunk := data.read(1 << 20):
                            check_cancel()
                            unpacked.write(chunk)
            (staged / "READY").write_text(SHA256, encoding="ascii")
            check_cancel()
            # Rename the old cache only once the replacement is complete.
            old = Path(folder) / "previous"
            if target.exists():
                target.rename(old)
            try:
                staged.rename(target)
            except OSError:
                if old.exists():
                    old.rename(target)
                raise


def load_sdk(scratch=None):
    target = sdk_cache()
    if not _valid_sdk(target):
        with _install_lock(target):
            if not _valid_sdk(target):
                _install_sdk(target, scratch)
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

    def host_buffer(self, size, stack):
        """Pinned memory avoids the CUDA driver's extra pageable-memory copy."""
        pointer = ct.c_void_p()
        status = self.cuda.cudaHostAlloc(ct.byref(pointer), ct.c_size_t(size), 0)
        if status:
            # Lockable memory is a machine-wide resource. Fall back to the same
            # pageable storage used by the quality path when it is unavailable.
            if status not in (2, 801):  # cudaErrorMemoryAllocation / cudaErrorNotSupported
                self.check(status)
            self.cuda.cudaGetLastError()
            return bytearray(size)
        stack.callback(self.cuda.cudaFreeHost, pointer)
        return memoryview((ct.c_ubyte * size).from_address(pointer.value)).cast("B")


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


def _large_pipe():
    import _winapi
    import msvcrt

    try:
        read_handle, write_handle = _winapi.CreatePipe(None, 4 << 20)
    except OSError:
        # Windows may deny the larger nonpaged-pool allocation.
        return os.pipe()
    try:
        read_fd = msvcrt.open_osfhandle(read_handle, os.O_BINARY | os.O_RDONLY)
    except BaseException:
        _winapi.CloseHandle(read_handle)
        _winapi.CloseHandle(write_handle)
        raise
    try:
        write_fd = msvcrt.open_osfhandle(write_handle, os.O_BINARY | os.O_WRONLY)
    except BaseException:
        os.close(read_fd)
        _winapi.CloseHandle(write_handle)
        raise
    return read_fd, write_fd


def _frame_pipe(stack):
    r, w = _large_pipe()
    reader = stack.enter_context(os.fdopen(r, "rb", 0))
    writer = stack.enter_context(os.fdopen(w, "wb", 0))
    return reader, writer


def _write_frame(stream, frame):
    view = memoryview(frame)
    while view:
        n = stream.write(view)
        if not n:
            raise BrokenPipeError("编码管道在一帧写入完成前已关闭。")
        view = view[n:]


def _wait_write(future):
    while True:
        try:
            return future.result(timeout=0.15)
        except TimeoutError:
            if future.done():
                return future.result()
            check_cancel()


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
            "stream=width,height,avg_frame_rate,r_frame_rate,color_range,pix_fmt,color_space,color_transfer,color_primaries:"
            "stream_side_data=dv_profile,dv_bl_signal_compatibility_id",
            "-of", "json", src,
        ],
        creationflags=hidden,
    )
    return json.loads(probe)["streams"][0], ["-i", src]


def pq_to_rgb10(source_range):
    # zimg performs the matrix/range conversion before a lossless planar-to-
    # packed rearrangement. The direct swscale packed path raises PQ black.
    return (
        f"zscale=filter=spline36:matrixin=2020_ncl:rangein={source_range}:matrix=gbr:range=full,"
        "format=gbrp10le,format=x2bgr10le"
    )


def rgb10_to_pq():
    return (
        "format=gbrp10le,zscale=filter=spline36:matrixin=gbr:rangein=full:"
        "matrix=2020_ncl:range=limited:chromal=left,format=yuv420p10le"
    )


def run_worker(params):
    # Windows closes this handle on process exit. Explicitly closing it would
    # kill the worker itself before it can report the final encode status.
    job = ChildJob()
    job.contain_current()
    hidden = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    env = os.environ.copy()
    w, h = params["width"], params["height"]
    speed = params.get("speed", "quality")
    if speed not in ("quality", "fast"):
        raise ValueError("未知的 RTX 处理速度。")
    fast = speed == "fast"
    stream, input_args = decoder_input(params, hidden)
    rate = stream.get("avg_frame_rate")
    if not rate or rate == "0/0":
        rate = stream["r_frame_rate"]
    iw, ih = stream["width"], stream["height"]
    if iw >= w and ih >= h:
        raise ValueError("RTX 超分需要至少一个方向的画面尺寸放大。")
    compressed = params.get("compressed_source", False)
    if compressed:
        config = [s for s in stream.get("side_data_list", []) if "dv_profile" in s]
        if (any(s.get("dv_profile") != 8 or s.get("dv_bl_signal_compatibility_id") != 1 for s in config)
                or (stream.get("color_space"), stream.get("color_transfer"), stream.get("color_primaries"),
                    stream.get("color_range")) != ("bt2020nc", "smpte2084", "bt2020", "tv")):
            raise ValueError("压缩片源修复需要有限范围 HDR10 或 HDR10 兼容的 Dolby Vision Profile 8.1。")
    nvvfx, libs = load_sdk(Path(params["output"]).resolve().parent)
    ffmpeg = [bundled_tool("ffmpeg"), "-hide_banner", "-nostdin", "-loglevel", "error"]
    # RGB10A2 expects red in bits 0..9. FFmpeg calls exactly that layout x2bgr10le.
    source_range = "full" if stream.get("color_range") == "pc" else "limited"
    init_args, rgb16_filter = [], None
    if params.get("sdr_to_hdr"):
        from hdr import sdr_filter

        init_args, expansion = sdr_filter(
            iw, ih, iw, ih, "reference", mapping=params.get("sdr_mapping", "expand"), output_rgb=True,
            scene_shader=params.get("scene_shader"),
        )
        full = source_range == "full" or stream["pix_fmt"].startswith("yuvj")
        tagging = "setparams=range=" + ("full" if full else "limited")
        rgb16_filter = tagging + ":colorspace=bt709:color_primaries=bt709:color_trc=bt709," + expansion
    elif any(side.get("dv_profile") == 5 for side in stream.get("side_data_list", [])):
        from profile5 import decode_profile5_filter

        init_args, rgb16_filter = decode_profile5_filter(iw, ih, iw, ih, output_rgb=True)
    if rgb16_filter is None:
        source_filter = pq_to_rgb10(source_range)
        if compressed:
            # Filter in the original 10-bit PQ luma/chroma planes before the model.
            source_filter = "hqdn3d=1.5:1.0:2.0:1.5," + source_filter
    else:
        # SDR and P5 are reconstructed as RGB16 on Vulkan. Keep chroma at
        # full resolution all the way to the model input.
        source_filter = rgb16_filter + ",zscale=rangein=full:range=full:dither=ordered,format=gbrp10le,format=x2bgr10le"
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
        source_filter,
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
        rgb10_to_pq(),
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
    quality = nvvfx.QualityLevel.ULTRA if compressed else nvvfx.QualityLevel.HIGHBITRATE_ULTRA
    strength = 0.6 if compressed else 1.0
    print(
        f"NVIDIA RTX Video Super Resolution | VFX {nvvfx.get_sdk_version()} | {quality.name} {strength:.2f} "
        f"| RGB10A2 {iw}x{ih} -> {w}x{h}" + (" | 异步帧传输" if fast else ""),
        flush=True,
    )
    created = []
    with contextlib.ExitStack() as stack:
        gpu = GPUFrame(libs, iw, ih)
        stack.callback(gpu.close)
        effect = stack.enter_context(
            nvvfx.VideoSuperRes(
                quality=quality,
                strength=strength,
                device=0,
                image_encoding=nvvfx.ImageEncoding.RGB10A2,
            )
        )
        effect.output_width, effect.output_height = w, h
        effect.load()
        try:
            if fast:
                decoded, decoder_out = _frame_pipe(stack)
                converter_in, sink = _frame_pipe(stack)
                encoder_in, converter_out = _frame_pipe(stack)
            else:
                decoder_out = converter_in = converter_out = subprocess.PIPE
            dec = subprocess.Popen(decoder, stdout=decoder_out, creationflags=hidden, env=env)
            created.append(dec)
            if fast:
                decoder_out.close()
            conv = subprocess.Popen(
                converter, stdin=converter_in, stdout=converter_out, creationflags=hidden, env=env
            )
            created.append(conv)
            if fast:
                converter_in.close()
                converter_out.close()
            else:
                decoded, sink, encoder_in = dec.stdout, conv.stdin, conv.stdout
            enc = subprocess.Popen(encoder, stdin=encoder_in, creationflags=hidden, env=env)
            created.append(enc)
            encoder_in.close()
            if fast:
                buf_in = gpu.host_buffer(iw * ih * 4, stack)
                buffers = [gpu.host_buffer(w * h * 4, stack) for _ in range(2)]
                writer = stack.enter_context(ThreadPoolExecutor(max_workers=1))
            else:
                buf_in = bytearray(iw * ih * 4)
                buffers = [bytearray(w * h * 4)]
            pending_write = None
            count = 0
            last_progress = time.monotonic()
            while read_frame(decoded, buf_in):
                check_cancel()
                gpu.upload(buf_in)
                buf_out = buffers[count % len(buffers)]
                gpu.download(effect.run(gpu).image, buf_out, w, h)
                if fast:
                    # The writer owns the preceding buffer while VFX fills the
                    # current one. A single worker preserves frame order and
                    # keeping at most one write pending bounds memory use.
                    if pending_write is not None:
                        _wait_write(pending_write)
                    pending_write = writer.submit(_write_frame, sink, buf_out)
                else:
                    _write_frame(sink, buf_out)
                count += 1
                now = time.monotonic()
                if now - last_progress >= 0.8:
                    print(f"progress_frame={count}", flush=True)
                    last_progress = now
            print(f"progress_frame={count}", flush=True)
            if pending_write is not None:
                _wait_write(pending_write)
            sink.close()
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
