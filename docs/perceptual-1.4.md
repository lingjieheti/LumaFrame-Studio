# 重压缩片源的 HDR 时空感知测量

对照组为 1.3.0 默认 RTX 高码率 Ultra。候选组使用 NVIDIA 标准 Ultra、强度 0.6，输入先经 FFmpeg `hqdn3d=1.5:1.0:2.0:1.5`。两组使用相同的 1080p 输入和 4K 交付编码配置。用 Cambridge ColorVideoVDP 对比每组成片与原生 4K HDR 母版，分数越高表示在给定观看条件下模型预测的失真越小。七个测量区域中五个上升、两个下降。所有差异均小于 0.16 JOD。实测支持把该组合用于有明显压缩痕迹的备选修复，收益取决于镜头和画面位置。[ColorVideoVDP 论文及代码](https://www.cl.cam.ac.uk/research/rainbow/projects/colorvideovdp/) · [原始 4K 素材和基础测量方法](quality.md)

## 分区域结果

三个片段各测画面中心。在《Sparks》和额外留出的《Meridian》镜头中，又固定测量中心点位于画面横纵各 1/4、3/4 处的两个区域。各区域均为 384 × 256 个原始像素，表中坐标为左上角。三处合计占一帧约 3.56%。留出的《Meridian》帧号为 8660–8719，选定参数后才测。重压缩输入由 1080p 画面以 x265 CRF 37 或 34 制备。

| 母版和压缩条件 | 裁区原点 (x, y) | 1.3.0 默认 JOD | 候选 JOD | 变化 |
|---|---|---:|---:|---:|
| Sparks，CRF 37 | 768, 412 | 6.4636 | 6.4662 | +0.0026 |
| Sparks，CRF 37 | 1728, 952 | 5.2404 | 5.3210 | +0.0805 |
| Sparks，CRF 37 | 2688, 1492 | 4.6443 | 4.6343 | −0.0100 |
| Meridian，CRF 37 | 1728, 952 | 6.5184 | 6.5885 | +0.0701 |
| Meridian 留出镜头，CRF 34 | 768, 412 | 7.2012 | 7.1207 | −0.0804 |
| Meridian 留出镜头，CRF 34 | 1728, 952 | 5.6323 | 5.6941 | +0.0618 |
| Meridian 留出镜头，CRF 34 | 2688, 1492 | 7.4916 | 7.6454 | +0.1538 |

仅增加同一去噪滤镜、沿用默认模型时，《Sparks》中央得分从 5.2404 降到 5.2251。滤镜、模型及片源共同影响结果。ColorVideoVDP 会同时响应空间细节、色彩与时间变化，上表无法单独量化闪烁，也无法证明观众能察觉这些小幅差异。

## 显示条件与复算

使用 MIT 许可的 ColorVideoVDP 0.5.7，在 CPU 上以作者配置 `standard_hdr_pq` 计算。该配置模拟 30 英寸 3840 × 2160 屏幕、峰值 1500 cd/m²、环境照度 10 lux、观看距离 0.7472 米。每段连续读取 60 帧，母版帧率 60000/1001。保留原分辨率的采样间距，FFmpeg/zimg 将有限范围 BT.2020 非恒定亮度 Y′CbCr 转为 16 位全范围 RGB，再将归一化 PQ 信号交给指标的 BT.2020/PQ 显示模型。测量为 HEVC 基础层解码画面，不包含电视机对 Dolby Vision 动态元数据的显示映射。[作者的 HDR 计算文档](https://github.com/gfxdisp/ColorVideoVDP#hdr-content)

在《Sparks》中央使用完全相同的序列作为两侧输入，得到 10.0000。作阳性对照时，隔一帧将 RGB 各通道的 PQ 信号乘以 0.92，得到 8.7915。命令默认测中心，也可指定任一已测裁区的偶数坐标。与应用相同的 FFmpeg 可执行文件须放在仓库 `bin` 中。`cvvdp` 仅用于复验。

```powershell
python -m pip install cvvdp==0.5.7
python tools/bench/perceptual_hdr.py truth.mkv v130.mkv candidate.mkv --origin 1728 952 --controls
python tools/bench/perceptual_hdr.py truth.mkv v130.mkv candidate.mkv --origin 768 412
python tools/bench/perceptual_hdr.py truth.mkv v130.mkv candidate.mkv --origin 2688 1492
```

## 2026 年 11 月 24 日调研

- 11 月 19 日公开的 FlexVSR 通过 27.4 万个可训练参数适配连续倍率，保留因果时间顺序。论文提供 H100 实验。本轮未找到作者发布的 Windows 可复测权重，也未找到原生十位 PQ 成片对照。[FlexVSR 原论文](https://arxiv.org/abs/2611.26178)
- 11 月 17 日公开的 HYPI2VR 利用运动对齐的噪声和跨帧注意力减少生成细节的变化。作者在 REDS30 测得 HYPI2VR 为 23.56 dB，RealViformer 为 24.73 dB，其他感知指标呈现不同排序。作者发布的实验证据涵盖视频恢复与视觉质量，尚不足以推导本软件的 HDR 母版保真度。[HYPI2VR 原论文及表 1](https://arxiv.org/html/2611.22874v1)
- 10 月 13 日公开的 FlashVSR+ 提供 Apache-2.0 模型，允许为大幅度运动降低时域压缩系数。作者报告单张 24 GB RTX 4090 上无空间分块的 4K 推理，官方环境为 Linux。公开命令行导出路径将浮点结果转换为 8 位并写 H.264。对本机 16 GB RTX 5080，仍需独立验证 HDR 输入域、10 位输出、动态元数据和显存占用。[论文](https://arxiv.org/abs/2610.17552) · [官方运行要求](https://github.com/OpenImagingLab/FlashVSR_Plus) · [模型权重](https://huggingface.co/ruchangyao/FlashVSR_plus) · [导出实现](https://github.com/OpenImagingLab/FlashVSR_Plus/blob/main/inference/output.py)
- 9 月 29 日公开的 ReCaVSR 通过历史潜变量传递局部时间信息，作者在 A100 上测量 1080p 输出为 21.20 fps、峰值分配显存 15.16 GB。同期的 RelayVSR 使用大模型生成稀疏参考帧，再由小模型重建中间帧。两者的官方模型卡均提供 Apache-2.0 权重，公开参考视频导出均经过 8 位 RGB。直接接入这些导出器会损失十位接口的精度。[ReCaVSR 原论文](https://arxiv.org/abs/2609.37831) · [ReCaVSR 权重](https://huggingface.co/kopper/ReCaVSR) · [ReCaVSR 视频读写](https://github.com/kopperx/ReCaVSR/blob/main/recavsr_infer/video.py) · [RelayVSR 权重](https://huggingface.co/kopper/RelayVSR) · [RelayVSR 视频读写](https://github.com/kopperx/RelayVSR/blob/main/refvsr_inference/media.py)
