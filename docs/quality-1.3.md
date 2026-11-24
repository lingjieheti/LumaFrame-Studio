# LumaFrame Studio 1.3 的 HDR 母版对照

在 RTX 5080 上，分别用 1.2.0 与 1.3.0 的 RTX 高码率 Ultra 路径，把相同的 1080p 输入导出为 3840 × 2160 的 10 位 HEVC。对照物为 Netflix 开放影片《Sparks》《Meridian》的同一段原生 4K HDR 画面。前者包含金属网格和工地，后者包含窗边逆光、人脸和条纹衣物。每段 60 帧，先按相同流程降为 1080p，再分别保留无损输入，或用 x265 CRF 31、37 压缩后输入。三档使用同一段画面，合计六个条件。[母版出处与帧号](quality.md) · [Netflix Open Content](https://opencontent.netflix.com/)

## RTX 成片相对 4K 母版的 HDR 色差

下表为 PQ 基础层的 ΔE<sub>ITP</sub>，越小越接近母版。按 BT.2124 把两幅画面转换到 ICtCp，结合亮度与两个色度分量计算。每段取第 0、5、…、55 帧，在原分辨率上每四行四列取一点，每段每种设置比较 6,220,800 个像素。P95 指 95% 的受测像素色差不超过该值。比较保留相同的 NVIDIA 模型和交付编码配置。新版修正了向模型传入和取回 RGB10 画面时的量化与色度重采样。[ITU-R BT.2124](https://www.itu.int/rec/R-REC-BT.2124-0-201901-I/en)

| 母版 | 1080p 输入 | 1.2.0 平均 | 1.3.0 平均 | 1.2.0 P95 | 1.3.0 P95 |
|---|---|---:|---:|---:|---:|
| Sparks | 无损 | 6.429 | 6.018 | 14.84 | 13.73 |
| Sparks | CRF 31 | 14.749 | 14.657 | 38.16 | 38.02 |
| Sparks | CRF 37 | 18.624 | 18.562 | 49.40 | 49.29 |
| Meridian | 无损 | 6.853 | 6.701 | 15.11 | 14.89 |
| Meridian | CRF 31 | 9.690 | 9.618 | 20.41 | 20.27 |
| Meridian | CRF 37 | 12.135 | 12.106 | 24.34 | 24.27 |

两段素材的平均值与 P95 都有所降低。重压缩素材的变化很小，仍能看到输入压缩带来的损失。独立灰阶测试覆盖视频范围内全部 877 个 10 位灰码。绕过模型本体，仅测模型两侧的格式转换，旧路径产生 733 个不同的灰码，新路径完整保留 877 个。该项测试单独定位格式转换产生的误差。真实导出还会受到模型及有损编码影响。

## 原生 4K Dolby Vision 的代际损失

用包含 Dolby 实拍内容的 120 帧 4K Profile 8.1 MKV 当作输入，再从两个版本的默认工作流导出。1.2.0 在原尺寸重新编码，原动态元数据完整保留，成片相对输入的平均 ΔE<sub>ITP</sub> 为 3.286，P95 为 8.16。1.3.0 的两项均为 0，整份 MKV 的 SHA-256 与输入一致。此实验每十帧取一帧，对照 6,220,800 个像素。另以带音轨、字幕和变帧率时间戳的 MP4 验证转封装，检查编码图像和逐帧 RPU 摘要。原生双层输入的保留及 MEL/FEL 转换范围见专项报告。[双层转换与验证](profile7.md)

## 调研与复验

NVIDIA 为典型压缩视频和高码率自然影像提供不同的模型。逐帧图像的误差、纹理观感和跨帧稳定性也可能朝不同方向变化。RealBasicVSR、SeedVR、FlashVSR 以及 STCDiT 提供了真实退化处理、生成式细节恢复与时域一致性的不同方案。这些工作发表的跨数据集指标不能直接代替本软件在 10 位 PQ 母版上的同片对照。本次未建立跨厂商盲测排名，也未做杜比参考监视器上的人工逐镜调色验收。[NVIDIA VSR](https://docs.nvidia.com/maxine/vfx/latest/Filters/VideoSuperResolution.html) · [RealBasicVSR](https://openaccess.thecvf.com/content/CVPR2022/html/Chan_Investigating_Tradeoffs_in_Real-World_Video_Super-Resolution_CVPR_2022_paper.html) · [SeedVR](https://openaccess.thecvf.com/content/CVPR2025/html/Wang_SeedVR_Seeding_Infinity_in_Diffusion_Transformer_Towards_Generic_Video_Restoration_CVPR_2025_paper.html) · [FlashVSR](https://openaccess.thecvf.com/content/CVPR2026/html/Zhuang_FlashVSR_Towards_Real-time_Diffusion-Based_Streaming_Video_Super_Resolution_CVPR_2026_paper.html) · [STCDiT](https://openaccess.thecvf.com/content/CVPR2026/papers/Chen_STCDiT_Spatio-Temporally_Consistent_Diffusion_Transformer_for_High-Quality_Video_Super-Resolution_CVPR_2026_paper.pdf)

用已有母版和导出文件可以独立复算表中指标。比较程序检查色彩标记、尺寸和帧时间，对画面转换后的 16 位全范围 ICtCp 计算色差。相同文件的结果为 0，标准 PQ 黑与满量程中性白的对照读数为 720。

```powershell
python tools/bench/hdr_difference.py truth.mkv output.mkv --every 5 --stride 4
```

[母版取样命令、各算法与时域指标](super-resolution-validation.md) · [SDR 映射、杜比高光和实写 RPU](dolby-validation.md)
