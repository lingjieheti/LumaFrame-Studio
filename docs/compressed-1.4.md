# 1.4 压缩片源修复实测

对需要放大的 Dolby Vision Profile 8.1 片源，可以手选 **RTX 压缩片源修复**。在三个 Netflix 原生 4K HDR 镜头、五组有损输入上，这项设置比默认 RTX 更接近母版。两组干净输入则由默认 RTX 得到更小的色差和帧间误差。影片包含精心保留的颗粒、干净纹理或多种质量的镜头时，可先试做一段。程序保留原有默认模式。[Netflix Open Content](https://opencontent.netflix.com/) · [1.3.0 默认模式实测](super-resolution-validation.md)

## 五组有损成片及两个反例

每行按“默认 RTX → 压缩片源修复”排列。RTX 5080 上两路使用相同的 10 位色彩转换、分辨率、NVENC P6、VBR 质量 15 和最高 120 Mbps。默认使用 HighBitrate Ultra（模式 19，强度 1.0）。压缩片源修复先在 10 位 PQ 的亮度、色度平面进行轻度空间和时间降噪，再通过标准 Ultra（模式 4，强度 0.60）放大。FFmpeg `hqdn3d` 的空间亮度、空间色度、时间亮度、时间色度强度依次为 1.5、1.0、2.0、1.5。NVIDIA 的官方指南将标准 Ultra 定位于典型压缩素材，将 HighBitrate 系列用于干净输入。设置选择另经下列同片实验验证。[NVIDIA VSR 指南，2026-09-22](https://docs.nvidia.com/maxine/vfx/latest/Filters/VideoSuperResolution.html#choosing-a-vsr-mode) · [FFmpeg hqdn3d](https://ffmpeg.org/ffmpeg-filters.html#hqdn3d-1)

| 1080p 输入 | 平均 ΔE<sub>ITP</sub> ↓ | P95 ↓ | Y′ PSNR（dB）↑ | 帧间误差变化 ↓ | MKV 字节变化 |
|---|---:|---:|---:|---:|---:|
| Sparks，CRF 31 | 14.657 → 14.305 | 38.02 → 37.19 | 34.637 → 34.759 | 14.9175 → 14.6796 | +3.7% |
| Sparks，CRF 37 | 18.562 → 18.249 | 49.29 → 48.69 | 31.786 → 31.849 | 18.6247 → 18.4170 | +5.2% |
| Meridian，CRF 31 | 9.618 → 9.493 | 20.27 → 20.11 | 39.057 → 39.082 | 9.5041 → 9.4889 | +95.5% |
| Meridian，CRF 37 | 12.106 → 11.930 | 24.27 → 24.12 | 37.373 → 37.395 | 9.6195 → 9.5903 | +113.8% |
| Meridian 另一镜头，CRF 34，留出验证 | 11.961 → 11.573 | 23.64 → 23.03 | 38.039 → 38.155 | 9.7054 → 9.6991 | +103.8% |
| Sparks，无损降采样 | 6.018 → 6.514 | 13.73 → 14.62 | 44.235 → 43.622 | 6.1310 → 6.5645 | −6.2% |
| Meridian，无损降采样 | 6.701 → 6.812 | 14.89 → 14.99 | 41.582 → 41.474 | 9.0022 → 9.0824 | −22.5% |

每个镜头 60 帧，帧率 60000/1001。HDR 色差采样 12 帧，每种设置比较 6,220,800 个像素。Y′、Cb、Cr 的 PSNR 和帧间误差由完整 60 帧计算。帧间误差统计相邻两帧“成片减母版”的亮度残差之差的平均绝对值，没有做运动补偿。三段素材合计约三秒；同一镜头的多档压缩共享画面内容。七个指定区域另测 ColorVideoVDP，其中有两个区域感知分数略降。真实观感依赖内容和观看条件。[HDR 色差测量方法](quality-1.3.md) · [分区域时空感知测量](perceptual-1.4.md)

## 边缘与色度

每五帧取一帧，在原始 4K 亮度平面每隔两个像素取样。按母版的横、纵差分绝对值之和选出前 10% 的边缘（阈值并列时全取，剔除零梯度），比较两方向的梯度均方根误差。单位为 10 位码值。在以下五组有损输入中，强边缘均更接近原始母版。

| 镜头及 CRF | 强边缘梯度误差 ↓ | Cb PSNR（dB）↑ | Cr PSNR（dB）↑ |
|---|---:|---:|---:|
| Sparks 31 | 26.079 → 25.062 | 44.220 → 44.381 | 45.212 → 45.598 |
| Sparks 37 | 30.581 → 29.650 | 43.252 → 43.408 | 44.331 → 44.603 |
| Meridian 31 | 23.479 → 23.356 | 47.266 → 47.486 | 51.872 → 52.137 |
| Meridian 37 | 24.130 → 23.982 | 46.171 → 46.429 | 49.048 → 49.318 |
| 独立镜头 34 | 24.234 → 23.861 | 45.537 → 45.889 | 48.301 → 48.838 |

## 相近文件大小时的对照

同一 VBR 质量参数可以生成不同大小的文件。另用留出镜头单独测试，将压缩片源模式的 NVENC VBR 质量从 15 改为 17.5，调参只看文件大小。此处两份文件相差 0.97%。该片段的平均与 P95 色差、亮度 PSNR 及帧间误差仍有改善。其余片段与更长影片的相近码率表现仍需更多实验。

| 留出镜头 | MKV 字节 | 平均 ΔE<sub>ITP</sub> ↓ | P95 ↓ | Y′ PSNR（dB）↑ | 帧间误差 ↓ |
|---|---:|---:|---:|---:|---:|
| 默认 RTX，质量 15 | 825,912 | 11.961 | 23.64 | 38.039 | 9.7054 |
| 压缩片源修复，质量 15 | 1,683,269 | 11.573 | 23.03 | 38.155 | 9.6991 |
| 压缩片源修复，质量 17.5 | 833,954 | 11.575 | 23.02 | 38.162 | 9.6965 |

## 筛选过程

先在已有两个镜头上比较以下六个候选，表中为平均 ΔE<sub>ITP</sub>。只减弱高码率模型、只加轻降噪、只做去块，均未取得交付组合在几类场景间的综合表现。Meridian CRF 31 的标准 Ultra 单用时色差略小，帧间误差却由默认 9.5041 升到 9.5163，加入轻度降噪后降至 9.4889。在确定模式、滤镜和强度后，另取 Meridian 第 8660–8719 帧作留出验证。

| 设置 | Sparks 31 | Sparks 37 | Meridian 31 | Meridian 37 |
|---|---:|---:|---:|---:|
| 默认 HighBitrate Ultra 1.0 | 14.657 | 18.562 | 9.618 | 12.106 |
| 默认 + FFmpeg 弱去块 | 14.652 | 18.536 | 9.632 | 12.104 |
| 默认 + 轻度三维降噪 | 14.626 | 18.469 | 9.621 | 12.057 |
| HighBitrate Ultra 0.50 | 14.633 | 18.550 | 9.610 | 12.110 |
| 标准 Ultra 0.60 | 14.377 | 18.347 | 9.490 | 11.967 |
| HighBitrate Ultra 0.50 + 主要处理色度的降噪 | 14.586 | 18.483 | 9.598 | 12.046 |
| 标准 Ultra 0.60 + 轻度三维降噪 | 14.305 | 18.249 | 9.493 | 11.930 |

去块使用 FFmpeg `deblock=filter=weak:block=8:alpha=.05:beta=.025:gamma=.025:delta=.025`。主要处理色度的候选使用 `hqdn3d=.01:1.0:.01:1.5`。[FFmpeg deblock](https://ffmpeg.org/ffmpeg-filters.html#deblock)

## 复测

留出镜头取自 Netflix《Meridian》P3 D65/PQ IMF，原图中可以看到两名人物、百叶窗和透光扬尘。脚本从原 MXF 的第 27,000,141,597 字节开始读取，依次核对原片 XML 帧号 8660–8719；第一个 JPEG 2000 码流的 SHA-256 为 `e6f4c1d77a0ee6f2387fe5078d4f97c2137786c05a4de01858b55927969f8f8a`。独立重取的 60 个码流均与首轮逐字节匹配。实验先从原素材制备 4K BT.2020/PQ 母版，再降采样、施加 x265 压缩，给测试输入写入供导出流程使用的 P8.1 元数据。各模式都与原始 4K 基础层对照。[取样和测量代码](../tools/bench/sparks.py)

```powershell
foreach ($scene in 'sparks', 'meridian') {
    foreach ($crf in 31, 37) {
        python tools/bench/sparks.py "work/$scene" --scene $scene --input-crf $crf --modes rtx rtx_compressed
        foreach ($mode in 'rtx', 'rtx_compressed') {
            python tools/bench/hdr_difference.py "work/$scene/truth.mkv" "work/$scene/crf$crf-$mode.mkv"
        }
    }
}
python tools/bench/sparks.py work/holdout --scene meridian-holdout --input-crf 34 --modes rtx rtx_compressed
python tools/bench/sparks.py work/holdout --scene meridian-holdout --input-crf 34 --modes rtx_compressed --rtx-quality 17.5 --run-name equal
python tools/bench/hdr_difference.py work/holdout/truth.mkv work/holdout/equal-crf34-rtx_compressed.mkv
```
