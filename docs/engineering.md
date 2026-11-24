# 实现与取舍

本项目将超分、色彩处理、杜比元数据及成品验证分别实现。GPU 滤镜输入与输出的实际精度、模型所见的图像以及编码后成片的表现都影响结果。选择模式时值得先比较同一镜头的 4K 试片。

| 调研对象 | 对实现的影响 |
|---|---|
| NVIDIA Video Effects SDK | 官方当前 VSR 接受 RGB10A2 输入和输出，HighBitrate 模式用于压缩较轻的影像。RTX 模式采用这一接口。常规播放器的 VSR 开关无法制作离线文件。[NVIDIA 开发文档](https://docs.nvidia.com/maxine/vfx/latest/Filters/VideoSuperResolution.html) |
| StaxRip / NVEncC | 能管理 Dolby RPU。所查 NGX 滤镜源码以每通道 8 位 RGB 作为中间格式，因此本应用的十位 RTX AI 路径直接调用新版 NVIDIA SDK。[StaxRip 工作流](https://github.com/staxrip/staxrip/wiki/Encoding-Dolby-Vision-with-StaxRip) · [NGX 滤镜实现](https://github.com/rigaya/NVEnc/blob/fe7ffdd2bc400e9ff98bfdf528d3bd4d7e1e3301/NVEncCore/NVEncFilterNGX.cpp#L535-L544) |
| FSRCNNX | 在亮度通道运行公开的卷积超分模型，以高精度处理色彩，再进行十位编码。上游以通用图像恢复为目标，HDR 片源适配需通过实测评价。[模型与说明](https://github.com/igv/FSRCNN-TensorFlow/releases/tag/1.1) |
| UniFab RTX Rapid Upscaler | 厂商列出杜比输入和 HDR 保留，未给出所有输入 Profile 下逐帧保留 RPU 的保证。本应用将输出条件写入验证流程。[产品文档](https://unifab.ai/rtx-rapid-upscaler.htm) · [输入格式](https://unifab.ai/technology/new-features-unifab-rtx-rapid-upscaler-ai-features) |
| Dolby / DaVinci Resolve | Dolby 的制作流程独立分析画面并允许调色师制作 trims。SDR 生成模式提供自动估算，原有 Dolby 电影则尽量保留可复用的创作参数。[Dolby 制作指南](https://professionalsupport.dolby.com/s/article/Quick-Start-Guide-Dolby-Vision-DaVinci-Resolve-Studio) · [元数据层级](https://professionalsupport.dolby.com/s/article/Dolby-Vision-Metadata-Levels) |
| 时序超分模型研究 | BasicVSR++ 等模型在指定数据集、退化方式和倍率上有研究结果。直接部署此类预训练权重到 PQ 电影，仍需验证色阶与时序。首版保留可对照的 Spline36 并实际测量发行内的模型。[BasicVSR++ 论文](https://openaccess.thecvf.com/content/CVPR2022/html/Chan_BasicVSR_Improving_Video_Super-Resolution_With_Enhanced_Propagation_and_Alignment_CVPR_2022_paper.html) |

## 元数据与质量

Profile 7 的 BL 和 EL 分别携带基础画面和增强信息。dovi_tool 能抽取、拆分、转换和回填 RPU；FEL 的像素重建还需把两层解码后交给合成器。我们比较了 AviSynth+ DoViBaker、VapourSynth FelBaker、FFmpeg 的 dovi_split 和 mpv/libplacebo 的 FEL 播放路径。1.1 版在当前 Windows 编码链使用 FelBaker 及其固定版本参考向量。保留原生双层时直接复制并验证压缩数据，可避免重新量化。[DoViBaker](https://github.com/erazortt/DoViBaker) · [FelBaker](https://github.com/bbeny123/felbaker/tree/1.0.0) · [dovi_split 的职责](https://ffmpeg.org/ffmpeg-bitstream-filters.html#dovi_005fsplit) · [mpv FEL 实现](https://github.com/mpv-player/mpv/pull/17932) · [项目实测](profile7.md)

原版 Dolby Vision 的 RPU 携带和帧关联的重塑或显示管理参数。P5 的基础图像需要按其色彩表示重建。已是 HDR10 兼容的 P8.1 则避免重复应用杜比映射。分镜黑边按新的像素尺寸调整，从封装后的 MKV 再次提取动态元数据并校验。自动制作的新杜比视界通过软件估算新画面的镜头亮度，再生成 Profile 8.1 元数据；估算精度、映射假设和实际测试记录见验证报告。[FFmpeg libplacebo](https://ffmpeg.org/ffmpeg-filters.html#libplacebo) · [dovi_tool](https://github.com/quietvoid/dovi_tool) · [验证记录](dolby-validation.md)

## 界面

影片作为内容层。浮动导航取色自当前封面或缩略图，导入按钮与预览共用居中裁切坐标，对按钮中心和边缘使用不同的背景采样。选中、悬停、键盘焦点各有独立状态。指针静止后，悬停过渡会结束。浓度滑块旁的色带用于对照扩散和着色。界面使用 Qt 在 Windows 本地绘制。设计取舍参考 Apple 对功能控件层、边缘反射、背景扩散和透明度偏好的说明。[Apple 材质指南](https://developer.apple.com/design/human-interface-guidelines/materials) · [WWDC25 材质设计](https://developer.apple.com/videos/play/wwdc2025/219/) · [WWDC26 材质更新](https://developer.apple.com/videos/play/wwdc2026/102/?time=190)
