# RTX 超分的标准与并行加速

打开需要放大的影片，选择 NVIDIA RTX AI 或 RTX 压缩片源修复。在「速度模式」里可选「标准」或「并行加速」。首次使用默认标准，软件记住之后的选择。五秒试片和整片导出使用同一档。命令行对应 `--speed quality` 和 `--speed fast`。

两档使用同一条画面处理链。常规 RTX 模式采用 HighBitrate Ultra、强度 1.0。压缩片源修复采用 Ultra、强度 0.6。两档的 RGB10A2 超分输入输出、PQ 转换、色度抽样、HEVC Main10 编码参数及杜比元数据处理一致。[NVIDIA VFX 视频超分文档](https://docs.nvidia.com/maxine/vfx/latest/Filters/VideoSuperResolution.html)

## 加速发生在哪里

原流程中，应用把一帧放大后的 10 位 RGB 图像写给 FFmpeg，待写入完成才开始处理下一帧。以 3840 × 2160 为例，一帧占 31.6 MiB。并行加速轮流使用两个输出缓冲区，后台写入上一帧时，前台可以处理下一帧。单个写入线程保持帧序。Windows 管道使用较大缓冲，并在 CUDA 支持时使用锁页主机内存。[NVIDIA CUDA 数据传输指南](https://docs.nvidia.com/cuda/cuda-c-best-practices-guide/#data-transfer-between-host-and-device)

额外资源主要是一帧输出缓冲及三条各申请 4 MiB 的管道。系统无法分配大管道或锁页内存时，会分别使用普通管道或普通主机内存。文件读取、杜比元数据处理、封装和成片校验仍有各自的耗时。原码流复制和原样保留 4K 双层杜比的任务不会进入 RTX 超分流程。

## RTX 5080 实测

在 RTX 5080 16 GB、驱动 621.24 的同一台 Windows 电脑上，用 1.4.1 正式版和 1.4.2 并行加速处理相同输入。下表为从启动导出到成片校验完成的总耗时。测试使用 Netflix Open Content 的 4K PQ 母版，取连续画面降至 1920 × 1080，以 HEVC CRF 24 制成输入，再超分至 3840 × 2160。两段交替拼接用于减小极短片段里启动耗时的占比。[Netflix Open Content](https://opencontent.netflix.com/)

| 片段 | 帧数 | 1.4.1 标准 | 1.4.2 并行加速 |
| --- | ---: | ---: | ---: |
| Sparks | 60 | 7.10 秒 | 6.31 秒 |
| Meridian | 60 | 6.27 秒 | 5.47 秒 |
| 两段交替，各重复四次 | 480 | 21.27 秒 | 14.71 秒 |

Sparks 和 Meridian 各 60 帧。两种模式在每段上都产生了相同的完整 HEVC 码流。逐帧解码后的 10 位 YUV、每帧杜比 RPU、显示时间和帧时长也完全一致。480 帧拼接试验的总耗时缩短了 30.8%。可使用[成片比对脚本](https://github.com/lingjieheti/LumaFrame-Studio/blob/v1.4.2/tools/bench/verify_transport.py)在自己的设备上复验两档输出。
