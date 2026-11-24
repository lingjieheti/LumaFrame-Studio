# 2026-11-25 超分与 Dolby Vision 方案复核

一段专门设计的 4K 运动测试片上，Spline36 最能保住移动细斜纹，RTX 压缩片源修复最能还原移动硬边。两部影片的干净降采样镜头都更适合现有 RTX HighBitrate Ultra。软件仍按片源提供多个选项。下表给出这些结论的适用范围，以及本次实际试过的两个候选后端。[运动测试脚本](../tools/bench/motion_grain.py) · [真实镜头、码率对照与反例](compressed-1.4.md)

## 同一秒画面，七条成片路径

生成 24 帧、3840 × 2160、24 fps、有限范围 BT.2020/PQ 的 10 位 FFV1 母版。三个等宽分区分别包含平移的斜纹（含 3.2 像素和 9 像素周期）、移动硬边和逐帧独立的随机颗粒。先用 Lanczos 降到 1920 × 1080，再用 x265 medium、CRF 31 压缩，仅向输入附加一组已知的 P8.1 RPU。五个产品模式用已安装的 1.4.0 程序从正常导出入口逐个成片。另两行是开发环境中的候选。七份成片均校验 24 帧，提取后的 RPU SHA-256 均为 `b02a74ef7567597e0205dee6f90190c27c6dd40c25402d9cf393a412f7dbcef2`。在另一个空目录从生成母版开始完整复跑，七组画质指标全部复现。[生成、导出、测量与 RPU 校验](../tools/bench/motion_grain.py)

| 路径 | 平均 ΔE<sub>ITP</sub> ↓ | P95 ↓ | 斜纹 PSNR ↑ | 硬边 PSNR ↑ | 斜纹帧间误差 ↓ | 硬边帧间误差 ↓ | MKV 字节 |
|---|---:|---:|---:|---:|---:|---:|---:|
| Spline36 参考 | 7.446 | 21.37 | 35.390 | 41.420 | 9.148 | 5.527 | 522,182 |
| NIS | 7.735 | 23.83 | 34.699 | 41.349 | 13.204 | 5.748 | 734,903 |
| FSRCNNX | 7.444 | 22.18 | 35.134 | 42.233 | 10.145 | 4.915 | 813,564 |
| RTX HighBitrate Ultra，默认 | 7.816 | 24.65 | 34.296 | 41.955 | 11.540 | 5.069 | 2,462,469 |
| RTX 压缩片源修复 | 7.547 | 21.38 | 35.330 | 42.836 | 9.479 | 4.694 | 2,408,394 |
| AMD AMF VideoSR 1.0，候选 | 7.871 | 24.65 | 34.144 | 41.797 | 14.556 | 5.426 | 1,289,299 |
| NVIDIA Streaming Ultra，候选 | 21.912 | 82.35 | 23.062 | 40.646 | 107.455 | 7.048 | 3,917,571 |

数值取自解码后的 4K 基础层。HDR 色差每四帧取一帧、横纵每八个像素取一个，共 777,600 个样点，本测试只有中性灰阶。各分区的 PSNR 用满量程 1023 计算，单位 dB。帧间误差是相邻两帧亮度残差之差的平均绝对值，单位为 10 位码值。两者都用全部 24 帧，剔除各区四周 32 个像素。各产品路径使用自己的交付编码参数，因此表中反映整条链路的结果；同一路径内比较更换模型的效果更有可比性。MKV 容器元数据可能使独立复测字节数略有变化。[HDR 色差实现](../tools/bench/hdr_difference.py) · [真实彩色镜头的区域感知分数](perceptual-1.4.md)

**颗粒在输入的哪个环节消失？** 4K 母版的颗粒区亮度标准差为 10.004。降到 1080p 后、编码前，同一区域仍有 444–493 的亮度码值，标准差为 4.484。经过 CRF 31 压缩后，全部 24 帧共 15,292,416 个亮度样点均为码值 470。七份输出的标准差介于 0.001 和 0.496，和母版颗粒的相关系数绝对值均低于 0.001。这个例子能验证丢失的随机分量和输出噪声之间缺乏对应关系。要考察原片保留下来的真实胶片颗粒，仍需使用不同压缩强度、同场景真实扫描母版另行比较。[测试图与各区统计](../tools/bench/motion_grain.py)

## 两个候选后端

**NVIDIA Maxine Streaming Ultra。** NVIDIA 将 21/23 定位于干净或轻度劣化素材再次编码前的增强。另用两部 Netflix 4K HDR 母版制作的无损 1080p 输入，以同一 RTX 5080、RGB10A2、强度 1.0 和相同 NVENC 参数，单独将模式从 HighBitrate Ultra 19 换成 Streaming Ultra 23。每个镜头 60 帧，色差读 12 帧、6,220,800 个样点。两部作品的两个干净镜头均由模式 19 获得较小误差。另列一个明显有损的留出镜头，供识别模型输入条件。模式 23 未列入画质菜单。[NVIDIA 对各组模式的适用范围](https://docs.nvidia.com/maxine/vfx/latest/Filters/VideoSuperResolution.html) · [母版、原始设置与留出位置](compressed-1.4.md)

| 输入 | 模式 19 平均 ΔE<sub>ITP</sub> ↓ | 模式 23 平均 ΔE<sub>ITP</sub> ↓ | 模式 23 P95 ↓ |
|---|---:|---:|---:|
| Sparks，无损降采样 | 6.018 | 19.499 | 75.79 |
| Meridian，无损降采样 | 6.701 | 9.009 | 22.69 |
| Meridian 独立镜头，CRF 34 | 11.961 | 13.552 | 31.44 |

**AMD AMF VideoSR 1.0。** 本机显卡为 Radeon Graphics（设备 1002:13c0，驱动 32.0.21042.62）和 RTX 5080。用 FFmpeg 8.1 显式选择 AMD 的 D3D11 设备并从中建立 AMF 上下文，调用 HQ Scaler，P010 输入、P010 输出，再复用 Spline36 路径的 x265 slow、CRF 16 和封装校验。另制作 877 个有限范围中性灰阶（64–940，每阶宽四个输入像素），在 2× 输出色块中心读取。得到 751 个不同亮度值，202 阶与原码值相同，最大偏差 2 码。该测量覆盖缩放器内部处理和格式转换；原生十位的输入、输出接口本身无法证明每个码值精确传递。AMD 将 VideoSR 1.0 描述为空间 FSR，并注明实验版 VideoSR 1.1 对 NV12/P010 的支持限制。本轮只有 1.0 纳入十位测试，保留为候选。[AMD HQ Scaler API](https://github.com/GPUOpen-LibrariesAndSDKs/AMF/blob/master/amf/doc/AMF_HQ_Scaler_API.md)

## 其他可用方案及验证范围

| 方案 | 一手资料所列能力 | 本轮核验 |
|---|---|---|
| Topaz Video | Proteus 可调修复，Iris 针对面部，Nyx 面向降噪，Dione 处理隔行。厂商将 Starlight Precise 2.6 用于跨帧一致性和细节增强，同时提示重度运动模糊可能被生成为纹理。 | 本机已注册软件、常用安装目录和 PATH 未发现可调用的 Topaz Video。人脸、运动模糊及隔行片仍缺少同机对照。[增强模型](https://docs.topazlabs.com/topaz-video/filters/enhancement) · [Starlight Precise 2.5/2.6](https://docs.topazlabs.com/topaz-video/project-starlight-series/starlight-precise-25) |
| Topaz HDR 与开发接口 | Hyperion 将 SDR 映射到 HDR；Web 版列出 10 位 ProRes/H.265 和 16 位 EXR。公开开发接口用于托管任务，企业支持另行安排本地部署。 | 高位深导出说明仅涵盖指定产品工作流。原生 Dolby Vision 动态元数据、FEL 双层或逐帧往返精度尚无该产品的同源成片验证。[Hyperion](https://docs.topazlabs.com/topaz-video/filters/sdr-to-hdr/hyperion) · [Web 输出](https://docs.topazlabs.com/cloud-apps/topaz-for-web-video) · [开发接口](https://developer.topazlabs.com/) · [企业部署](https://www.topazlabs.com/enterprise) |
| DaVinci Resolve Studio | SuperScale、UltraNR、时域降噪、去隔行及 Dolby Vision 调色。Dolby 的 Resolve 工作流说明区分自动分析与需要授权的逐镜艺术修整。 | 本机已注册软件、常用安装目录和 PATH 未发现可调用的 Resolve。现有材料足以说明制作流程，无法提供与专业调色师制作母版的同片比较。[Blackmagic Studio 功能](https://www.blackmagicdesign.com/products/davinciresolve/studio) · [Dolby Resolve FAQ](https://professionalsupport.dolby.com/s/article/Dolby-Vision-in-DaVinci-Resolve-Studio-FAQs?language=en_US) |
| Intel Video Processing Library | VPP 暴露 AI 超分扩展参数，具体设备与驱动须查询支持情况。 | 本机未枚举到 Intel 图形设备，缺少片源到 P010 成片的实测。保留为有条件的硬件路线。[Intel VPP 结构与查询](https://intel.github.io/libvpl/latest/API_ref/VPL_structs_vpp.html) |
| FlexVSR、FlashVSR+、ReCaVSR、RelayVSR | 涵盖连续倍率因果模型、流式扩散、历史潜变量和稀疏参考帧。FlexVSR 预印本于 11 月 19 日公开。FlashVSR+ 提供官方模型与推理程序，其示例导出将浮点结果转为 8 位。 | 作者报告的 A100/H100 或 24 GB 卡表现与本机 16 GB RTX 5080 的十位 PQ 成片条件不同。原版示例和论文指标尚不足以接入该工作流。论文日期、权重和公开导出代码见各出处。[FlexVSR](https://arxiv.org/abs/2611.26178) · [FlashVSR+ 导出代码](https://github.com/OpenImagingLab/FlashVSR_Plus/blob/main/inference/output.py) · [ReCaVSR](https://arxiv.org/abs/2609.37831) · [RelayVSR 与各方案核验记录](perceptual-1.4.md) |
| Dolby 专业母版、P7 双层与 P8.1 交付 | Dolby 建议先设有效画幅，再按镜分析并在目标显示条件下复核映射。已有 P7 的双层保留、FEL 重建、已有 P8.1 的 RPU 迁移，以及从 HDR10 自动生成新 RPU，属于四种不同交付。 | 项目已有 BL/EL/RPU 签名、FEL 参考哈希、基础层逐帧核对及相关回归。本页的 HDR 数值仅衡量基础层；家庭影院设备映射、授权艺术修整及双层 UHD 光盘母带制作仍缺少相应对照。[Dolby 母版指南](https://professionalsupport.dolby.com/s/article/Dolby-Vision-Content-Creation-Best-Practices-Guide?language=en_US) · [P7 与 FEL 参考验证](profile7.md) · [dovi_tool](https://github.com/quietvoid/dovi_tool) |

## 复测

在有本项目配套工具、NumPy、对应 RTX 运行时的 Windows 开发机上，从仓库根目录运行。每次选一个空目录。默认测试五个产品模式。可用 `--modes reference neural` 等参数选择本机能运行的产品路径。有 AMD 硬件时加 `--amd`，有支持 Streaming Ultra 的 NVIDIA 硬件时加 `--streaming`。脚本通过安装包里的程序导出产品模式，候选实验通过当前源码和配套工具单独导出。目标目录保留母版、输入、成片和 JSON 指标。[复测脚本](../tools/bench/motion_grain.py)

```powershell
python tools/bench/motion_grain.py work/motion-grain --app "C:/path/to/LumaFrameStudio.exe" --amd --streaming
```
