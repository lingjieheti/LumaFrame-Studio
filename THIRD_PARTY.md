# LumaFrame Studio 1.1.2 的第三方组件

从源码构建 Windows x64 版时，在项目根目录运行 `py -3.12 tools/fetch_tools.py`。脚本只使用 Python 标准库；解压 7z 需要已安装的 7-Zip。每次安装均验证完整下载的 SHA-256，再按文件清单提取到 `bin`、`shaders` 和 `licenses`。镜像缓存可用 `--cache` 指定，缓存文件同样经过校验。Python 包版本见 `requirements-build.txt`。[下载清单与完整校验值](tools/fetch_tools.py) · [7-Zip](https://www.7-zip.org/)

## 媒体程序和着色器

双层 FEL 合成组件在构建时通过 `tools/fetch_fel.py` 单独下载和校验。发布包随附独立解释器、VapourSynth R73、FFMS2 5.0、FelBaker 1.0.0 和运行库清单。Windows 用户无需安装系统 Python、AviSynth 或 VapourSynth。[源代码、构建和许可清单](licenses/fel-components.md)

| 组件 | 项目使用的文件与许可 | 版本、源码与版权说明 |
|---|---|---|
| FFmpeg / FFprobe 8.1 | Gyan Windows x64 full 静态构建，GPLv3。Windows 安装程序从 Gyan 获取独立的 ffmpeg.exe 与 ffprobe.exe，许可在 `licenses/FFmpeg.txt` | [8.1 原始二进制](https://github.com/GyanD/codexffmpeg/releases/tag/8.1) · [Gyan 指定的 FFmpeg 源码 9047fa1b08](https://github.com/FFmpeg/FFmpeg/tree/9047fa1b08) · [构建类型与第三方库清单](https://www.gyan.dev/ffmpeg/builds/#libraries) |
| dovi_tool / dolby_vision 2.3.4 | RPU 解析、编辑、生成和回读。两者均为 MIT，版权属 quietvoid，见 `licenses/dovi_tool-LICENSE.txt` 和 `licenses/libdovi-LICENSE.txt` | [发行包](https://github.com/quietvoid/dovi_tool/releases/tag/2.3.4) · [含 Rust 依赖锁文件的源码](https://github.com/quietvoid/dovi_tool/tree/2.3.4) |
| MKVToolNix 104.0 | 使用 mkvmerge.exe 封装 Matroska。上游附 GNU GPLv2 及组件声明，位于 `bin/mkvtoolnix/doc` | [版本对应源码](https://mkvtoolnix.download/sources/mkvtoolnix-104.0.tar.xz) · [Windows 发行目录](https://mkvtoolnix.download/windows/releases/104.0/) |
| NVEncC 9.41 x64 | NVEncC64.exe，rigaya 的 NVEnc 代码为 MIT，其他组件依各自条款。完整上游声明见 `licenses/NVEnc-license.txt`，原文采用日文 Windows 编码 | [上游 9.41 源码](https://github.com/rigaya/NVEnc/tree/9.41) · [发布包](https://github.com/rigaya/NVEnc/releases/tag/9.41) · [构建说明](https://github.com/rigaya/NVEnc/blob/9.41/Build.en.md) |
| NVEncC 使用的 FFmpeg 9.0.1 DLL | 发布包内六个独立共享库，分别为 avcodec、avdevice、avfilter、avformat、avutil、swresample。各自的运行时许可函数返回 LGPL version 3 or later。见 `licenses/NVEnc-components.txt` 及 `licenses/NVEnc-FFmpeg-LGPL-3.0.txt` | [rigaya 的对应源码、依赖与补丁包](https://github.com/rigaya/ffmpeg_dlls_for_hwenc/releases/download/20260812/ffmpeg_lgpl_src_20260812.7z) · [构建脚本](https://github.com/rigaya/build_scripts) |
| NVIDIA Image Scaling | NVEncC 内的 NIS 缩放路径。NVIDIA Corporation，MIT，见 `licenses/NVIDIA-Image-Scaling-LICENSE.txt` | [NVIDIA 源码与许可](https://github.com/NVIDIAGameWorks/NVIDIAImageScaling/tree/35e13ba316c98eeecf16f37eae70ce88019911f6) |
| FSRCNNX 1.1 | 使用 igv 的可直接编辑 GLSL 源文件 `shaders/FSRCNNX_x2_16-0-4-1.glsl`。该文件头指定 LGPL-3.0-or-later，版权 2017–2021 igv。训练项目 1.1 根目录另有 MIT 文本。两份文本分别在 `licenses/FSRCNNX-LGPLv3.txt` 和 `licenses/FSRCNNX-training-license.txt` | [原始 GLSL 发布文件](https://github.com/igv/FSRCNN-TensorFlow/releases/tag/1.1) · [1.1 训练源码](https://github.com/igv/FSRCNN-TensorFlow/tree/1.1) |

安装时可复用与原包逐字节一致的本机工具，或将 Gyan 的原版 7z 放在安装程序旁离线使用。校验值如下。

| 文件 | SHA-256 |
|---|---|
| ffmpeg-8.1-full_build.7z | `4dcbe10fa72bffa3e976d09cf3335f53a2716a8041af7a047609898fe6019069` |
| ffmpeg.exe | `d1e2a156261ecc675081943197a85f08f2868784a0af499171ede89353edad31` |
| ffprobe.exe | `70872c3ffbc43d0b2c570f9837f54d6e9a832f4ca25463e9735b6a3ec0621478` |

可以运行 `bin/ffmpeg.exe -buildconf` 查看配置。项目用到其中的 libplacebo 图像处理、zimg 像素转换和 x265 编码。上表中的 Gyan 链接指明 FFmpeg 本体版本。[FFmpeg 许可说明](https://ffmpeg.org/legal.html) · [libplacebo](https://code.videolan.org/videolan/libplacebo) · [zimg](https://github.com/sekrit-twc/zimg) · [x265](https://github.com/Multicorewareinc/x265)

## 桌面运行环境和构建工具

| 组件 | 用途与许可 | 对应源码 |
|---|---|---|
| Qt / PySide6-Essentials / shiboken6 6.9.3 | 开源 Qt for Python 桌面窗口、绑定和动态库。Essentials 与 shiboken6 的包声明提供 LGPL-3.0-only、GPL-2.0-only 或 GPL-3.0-only 选项；本项目使用开放源代码的 LGPLv3 方式。见 `licenses/Qt-LGPL-3.0.txt`、`licenses/Qt-GPL-3.0.txt` 及各模块的第三方说明。兼容的 Qt/PySide 动态库可以替换 | [Qt 6.9.3 源码包](https://download.qt.io/archive/qt/6.9/6.9.3/single/qt-everywhere-src-6.9.3.tar.xz) · [PySide / shiboken 6.9.3 源码](https://github.com/qtproject/pyside-pyside-setup/tree/v6.9.3) · [Qt for Python 许可与致谢](https://doc.qt.io/qtforpython-6/licenses.html) |
| CPython 3.12.10 | Python 解释器及标准库。PSF 与历史组件许可见 `licenses/Python-3.12.10.txt` | [CPython v3.12.10](https://github.com/python/cpython/tree/v3.12.10) |
| PyInstaller 6.16.0 | Windows 独立目录打包。GPL 及 bootloader 特例的原文见 `licenses/PyInstaller-6.16.0.txt` | [PyInstaller v6.16.0](https://github.com/pyinstaller/pyinstaller/tree/v6.16.0) |
| Inno Setup 6、7-Zip | 分别生成 Windows 安装程序、解开上游压缩包；源码构建时由构建者安装 | [Inno Setup 许可](https://jrsoftware.org/files/is/license.txt) · [7-Zip 许可](https://www.7-zip.org/license.txt) |

## 可选 NVIDIA RTX Video Effects

选择 RTX AI 模式后，软件从 NVIDIA 的 Python 包索引获取 `nvidia_vfx-0.2.0.0-cp312-abi3-win_amd64.whl`，约 416 MiB。校验值为 `cebaa12411f7e22b39293205c5c4d07b8073d14ca1e0e7727ee4375207a36445`。该包和模型按 NVIDIA 上游专有条款使用，运行库在用户机器上缓存并保留其声明。GPU 驱动及 Windows 系统运行库由各自供应商提供。[NVIDIA 包索引](https://pypi.nvidia.com/nvidia-vfx/) · [NVIDIA VFX 文档](https://docs.nvidia.com/maxine/vfx/latest/Filters/VideoSuperResolution.html)

Dolby Vision、NVIDIA、GeForce、RTX 和 Qt 等名称与标识属于各自权利人。杜比测试素材的出处与复现方法见[验证记录](docs/dolby-validation.md)，发行物仅包含本项目代码和所列组件。
