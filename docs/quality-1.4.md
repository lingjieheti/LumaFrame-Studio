# 1.4.0 的画质选择与验证

## 有压缩痕迹的 1080p 杜比片源

当 Profile 8.1 片源需要放大时，画质菜单提供「NVIDIA RTX · 压缩片源修复」。这个模式在十位视频上先做轻度时空降噪，再使用 NVIDIA 标准 Ultra 模型，强度 0.60。原有高码率 Ultra 仍为默认选项。对于干净降采样的《Sparks》，改用修复模式反而使平均 HDR 色差从 6.018 升至 6.514。对于未参与参数筛选的《Meridian》压缩镜头，平均 HDR 色差从 11.961 降至 11.573，但同一交付预设下成片字节数达到原来的 2.04 倍。另做近似等体积对照，原模式输出 825,912 字节，修复模式输出 833,954 字节（多 0.97%），平均 HDR 色差仍由 11.961 降至 11.575。裁区感知评估七处中有五处上升、两处下降。可用「试转片段」比较颗粒、纹理和运动，随后再决定使用哪个模式。[输入、对照、码率与逐项指标](compressed-1.4.md) · [HDR 时空感知评估和近期研究](perceptual-1.4.md)

## 杜比镜头切换

1.4.0 将分镜检测与全分辨率亮度分析分开，在同一次解码中按显示帧号汇合。将两段各 60 帧的 Netflix 4K 母版拼接后，旧版只生成一段亮度数据。新版在第 60 帧识别出剪辑点，两镜的 L1 平均 PQ 码值分别为 1499 和 1203，与单独测量各镜头一致。另用等亮异色硬切、渐变和一帧闪光验证了边界情况。[分镜与 RPU 复读实验](scene-analysis-1.4.md)

## 原生 HDR10 增加杜比动态元数据

符合条件的静态 HDR10 片源直接沿用原始 HEVC 视频。软件测量原画面，制作新的开源 CM 2.9 动态元数据，输出 HDR10 兼容的单层 Dolby Vision Profile 8.1 MKV。输入条件为已经达到目标画幅（宽度上限 3840）、方形像素、逐行 HEVC Main 10 4:2:0、有限范围 BT.2020 非恒定亮度矩阵和 PQ。原始视频需携带 ST 2086 母版显示信息。软件扫描完整码流并比对静态 SEI，遇到原有杜比数据、HDR10+、HDR Vivid 或中途变化的静态母版值会提示。MaxCLL、MaxFALL 有原值时沿用，源流未提供时在 L6 中保留为未知值 0。新生成的亮度统计使用整个编码画幅，带有内嵌黑边的片源需注意有效画面与创作调色的差别。[Dolby 关于 HDR10 转换和逐镜分析的说明](https://professionalsupport.dolby.com/s/article/Longplay-analysis) · [Dolby 元数据层级](https://professionalsupport.dolby.com/s/article/Dolby-Vision-Metadata-Levels)

在 RTX 5080 上，以 3840 × 2160 的 150 帧 HDR10 片源创建三种交替帧时长、一个 FLAC 音轨和一条字幕。成片抽取 HEVC 后，编码图像、参数集及 SEI 的摘要与原始基础层相符。另用 FFmpeg 分别解码源和成片，150/150 帧的 10 位 YUV SHA-256 全部一致，逐帧时间、末帧时长和副轨也通过复核。从成片 RPU 读回的 L6 为母版峰值 1000 尼特、最低 0.001 尼特、MaxCLL 1000 尼特和 MaxFALL 588 尼特。试转入口另外生成了 121 帧的同类文件。这里验证的是基础层像素和元数据的写入；电视上的杜比映射观感受显示器与创作修整影响。

拖入符合条件的原生 HDR10 后，桌面会直接选择「保留 HDR10 原画面 · 生成动态元数据」。在已安装目录可用相同引擎复核源和成片。

```powershell
.\LumaFrameStudio.exe --export 原生HDR10.mkv --output 杜比版.mkv --result 核验.json
.\bin\ffmpeg.exe -v error -i 原生HDR10.mkv -map 0:v:0 -fps_mode passthrough -pix_fmt yuv420p10le -f framehash -hash sha256 输入逐帧.txt
.\bin\ffmpeg.exe -v error -i 杜比版.mkv -map 0:v:0 -fps_mode passthrough -pix_fmt yuv420p10le -f framehash -hash sha256 输出逐帧.txt
```
