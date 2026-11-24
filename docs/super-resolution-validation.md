# RTX 超分的灰阶、色度和压缩片源实测

在 GeForce RTX 5080 上，用 Netflix 两个原生 4K HDR 镜头比较 1.2.0 和新的 RTX 颜色转换。六组测试的亮度 PSNR 均提高。改善最大的是干净降采样的《Sparks》，Y′、Cb、Cr 分别提高 0.274、0.763、0.630 dB。输入受重压缩时，收益较小。这批短片缺少主观盲评和跨产品对照，无法据此建立行业画质排名。

## 从黑位到白位

NVIDIA VFX 的十位接口使用 RGB10A2。1.2.0 经过两个直接连接打包 RGB 的 swscale 转换时，合法中性灰 64–940 中的 877 个输入值最后只剩 733 个不同数值，黑位从 64 升至 66。新流程让 zimg 先在平面视频格式中计算 BT.2020 非恒定亮度矩阵与范围，再重排为 NVIDIA 接收的十位打包格式。独立往返测试得到 877 个不同灰阶，全部与输入逐像素一致。彩条测试另外核对红、绿、蓝的存储顺序及 Cb、Cr 的方向。上述数值测量模型前后的格式转换，成片还会经历超分与 HEVC 编码。[NVIDIA VFX 数据格式](https://docs.nvidia.com/maxine/vfx/latest/Filters/VideoSuperResolution.html) · [zimg 支持的颜色转换](https://github.com/sekrit-twc/zimg#capabilities) · [灰阶与彩条回归](../tests/test_rtx_color_precision.py)

## 六组母版对照

《Sparks》取连续 60 帧工地人物和网格，《Meridian》取连续 60 帧室内人物和条纹衬衫。将原始 JPEG 2000 转为 4K BT.2020/PQ 10-bit 4:2:0 对照，再降采样至 1080p。每个镜头各准备无损降采样和两档额外 HEVC 压缩。旧、新流程分别导出 60 帧 4K P8.1 MKV，固定 HIGHBITRATE_ULTRA 模型、P6 编码预设、目标质量 15、峰值 120 Mbps。给测试输入写入配套的 P8.1 元数据以运行完整导出流程。表内画质指标均读取最终 MKV 解码出的像素。[镜头、帧号及原始素材校验值](quality.md) · [Netflix Open Content](https://opencontent.netflix.com/)

| 镜头 | 1080p 输入 | Y′ PSNR，dB，旧 → 新 | Cb PSNR，dB，旧 → 新 | Cr PSNR，dB，旧 → 新 | 相邻帧残差变化，旧 → 新 |
|---|---|---:|---:|---:|---:|
| Sparks | 无损 | 43.961 → 44.235 | 50.170 → 50.933 | 50.413 → 51.043 | 6.1290 → 6.1310 |
| Meridian | 无损 | 41.426 → 41.582 | 49.919 → 49.958 | 56.616 → 56.613 | 8.9976 → 9.0022 |
| Sparks | HEVC CRF 31 | 34.611 → 34.637 | 44.183 → 44.220 | 45.175 → 45.212 | 14.9162 → 14.9175 |
| Meridian | HEVC CRF 31 | 38.999 → 39.057 | 47.252 → 47.266 | 51.861 → 51.872 | 9.5041 → 9.5041 |
| Sparks | HEVC CRF 37 | 31.772 → 31.786 | 43.234 → 43.252 | 44.313 → 44.331 | 18.6247 → 18.6247 |
| Meridian | HEVC CRF 37 | 37.362 → 37.373 | 46.164 → 46.171 | 49.032 → 49.048 | 9.6192 → 9.6195 |

各颜色分量分别汇总全部像素和全部帧，以 1023 为 PSNR 峰值。最后一列越低越好，单位为 10-bit 亮度码值，计算方法为相邻两帧的「输出减母版」残差之差的平均绝对值。这一项整体持平或略升，未做运动补偿。《Meridian》无损条件的 Cr 指标下降 0.003 dB。真实录像里的颗粒、交错扫描、失焦和多次转码需要另外测试。

## 为什么保留现有 RTX 模型与编码预设

- 在原颜色转换路径上比较 NVIDIA 标准 Ultra 和 HighBitrate Ultra。四组有损输入的标准 Ultra 均产生更大的输出文件，亮度 PSNR 下降 0.030–0.222 dB，相邻帧残差变化也增大。部分色度指标提高。NVIDIA 将标准模型用于典型压缩视频，将 HighBitrate 族用于较干净的素材，具体表现需用实际片源评估。交付版本继续采用 HighBitrate Ultra。[NVIDIA VSR 模式选择](https://docs.nvidia.com/maxine/vfx/latest/Filters/VideoSuperResolution.html#choosing-a-vsr-mode)
- 在新的颜色转换路径上额外测试 NVENC P7、16 帧前瞻、全分辨率双遍和 B 参考帧，作为一组设置与交付的 P6 比较。两个干净输入上的亮度 PSNR 均略低。《Meridian》无损条件由 41.5823 变为 41.5813 dB，文件从 13,780,105 减至 13,543,714 字节。未采用这一组合。NVENC 的编码预设 P7 与杜比视界双层 Profile 7 是两个独立概念。[NVIDIA NVENC 编码指南](https://docs.nvidia.com/video-technologies/video-codec-sdk/13.1/nvenc-video-encoder-api-prog-guide/index.html)

## FSRCNNX 与杜比实拍

FSRCNNX 在 Vulkan 上完成增强后输出全分辨率 16 位 RGB，再以 zimg 转换成 10 位 Y′CbCr，显式使用左侧色度定位和有序抖动。旧路径直接在 GPU 上量化。以逐级中性灰放大两倍，输出 Cb 平均值从 511.252 接近至 511.995，理想值为 512。重新运行两个无损降采样镜头，得到以下完整 4K 文件读数。

| FSRCNNX 镜头 | Y′ PSNR，dB，旧 → 新 | Cb PSNR，dB，旧 → 新 | Cr PSNR，dB，旧 → 新 | 相邻帧残差变化，旧 → 新 |
|---|---:|---:|---:|---:|
| Sparks | 43.692 → 43.727 | 49.796 → 50.153 | 49.787 → 50.265 | 6.4258 → 6.4234 |
| Meridian | 41.519 → 41.528 | 49.575 → 49.810 | 54.566 → 56.438 | 9.0138 → 9.0147 |

Dolby 发布的 Glass Blowing 2 实拍经过 Profile 5 重建后，新的 RTX 路径直接接收 16 位 RGB 转成的 RGB10A2，省去了中途一次 4:2:0 色度抽样。两版均完整导出 120 帧，转换后 RPU 摘要相同。将成片与同帧 P5 重建分别缩至 320 × 180，在全部像素上比较，Y′ 平均绝对误差从 1.708 降至 0.553，Cb 从 0.524 增至 0.632，Cr 从 0.490 增至 0.560，单位均为 10-bit 码值。这项检验针对大范围色彩与亮度关系，4K 细节由上面的原生母版试验评估。[原片与独立 P8.1 参照](dolby-validation.md)

## 复测

下面的 PowerShell 命令依次测试两个镜头，各自复用同一份 4K 对照文件。结果 JSON 包含三个分量的 PSNR、Y′ SSIM、相邻帧残差变化和文件大小。可将生成的 `bench-p81.mkv`、`bench-p81-crf31.mkv`、`bench-p81-crf37.mkv` 交给旧版本的 `--export`，在相同条件下重测旧版。素材由脚本从 Netflix 公开源按帧取用，安装包仅提供软件。

```powershell
foreach ($scene in 'sparks', 'meridian') {
    python tools/bench/sparks.py "work/$scene" --scene $scene --run-name clean --modes rtx
    python tools/bench/sparks.py "work/$scene" --scene $scene --input-crf 31 --modes rtx
    python tools/bench/sparks.py "work/$scene" --scene $scene --input-crf 37 --modes rtx
}
```
