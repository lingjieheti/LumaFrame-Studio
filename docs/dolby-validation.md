# 杜比视界与 SDR 扩展验证

LumaFrame Studio 1.0.0 在一段来自 Dolby 的实拍样片上，完成了 1920 × 1080 单层 Profile 5 到 3840 × 2160 单层 Profile 8.1 的转换。FSRCNNX 和 RTX 高码率 Ultra 两种正式预设均编码了全部 120 帧，两份成片各自回读得到的 120 条 RPU 与预期数据逐字节一致。下面的结果来自 Windows / RTX 5080 上的文件测量。电视播放兼容性与专业杜比母版认证需要另行验证。

## 如何复核真实样片

使用 Chromium 测试集的两份 Glass Blowing 2 片段。两份片段均截取自 Dolby 发布的同一段实拍画面，一份原始 Profile 5，另一份由 Dolby 提供 Profile 8.1 编码，可作独立色彩参照。测试使用的截取版封装时间基为 60 fps。有关原视频和生成截取版的步骤，由 Chromium 在素材说明中列出；样片仅由测试者依其来源取得。[样片出处和制备步骤](https://chromium.googlesource.com/chromium/src/media/+/e1fb8ebd06a111a90a9e07a60a24d2dafaf3058e/test/data/README.md#mp4-file-with-dolby-vision)

| 测试输入 | SHA-256 |
|---|---|
| [glass-blowing2-dolby-vision-profile-5-frag.mp4](https://chromium.googlesource.com/chromium/src/media/+/e1fb8ebd06a111a90a9e07a60a24d2dafaf3058e/test/data/glass-blowing2-dolby-vision-profile-5-frag.mp4) | `d21fc3938a813b6ce25efe890074b23d966a5828c9a3d33de7e8591a25dd2036` |
| [glass-blowing2-dolby-vision-profile-8-1-frag.mp4](https://chromium.googlesource.com/chromium/src/media/+/e1fb8ebd06a111a90a9e07a60a24d2dafaf3058e/test/data/glass-blowing2-dolby-vision-profile-8-1-frag.mp4) | `2cbedc36cf1ec2fdaaa54d26942ff93faeda4d4b2baf9fd25ee74bfebc15287e` |

在独立工作目录准备输入文件，命名为 `glass-p5.mp4`、`glass-p81.mp4`。以下 PowerShell 示例中，`$app` 指向安装目录，输出名均应选择尚未使用的名称。命令通过正式导出入口处理整个样片。

```powershell
$app = (Resolve-Path (Join-Path $env:LOCALAPPDATA 'Programs\LumaFrame Studio')).Path
$ff = Join-Path $app 'bin\ffmpeg.exe'
$probe = Join-Path $app 'bin\ffprobe.exe'
$dv = Join-Path $app 'bin\dovi_tool.exe'
Get-FileHash glass-p5.mp4,glass-p81.mp4
& "$app\LumaFrameStudio.exe" --export glass-p5.mp4 --target preserve --mode rtx --output glass-4k.mkv --result glass-result.json | Out-Host
& $probe -v error -select_streams v:0 -count_frames -show_streams -of json glass-4k.mkv
& $ff -v error -i glass-p5.mp4 -map 0:v:0 -c:v copy -bsf:v hevc_mp4toannexb -f hevc source.hevc
& $ff -v error -i glass-4k.mkv -map 0:v:0 -c:v copy -bsf:v hevc_mp4toannexb -f hevc out.hevc
& $dv -m 3 extract-rpu -i source.hevc -o expected.rpu
& $dv extract-rpu -i out.hevc -o readback.rpu
& $dv info -i readback.rpu -s
Get-FileHash expected.rpu,readback.rpu
```

此样片的有效画面偏移均为零，以上 mode 3 结果可直接对比。含黑边的片源需在预期 RPU 中按输出尺寸换算 L5。成片应报告 HEVC Main 10、BT.2020 非恒定亮度矩阵、ST 2084、有限范围、Dolby Profile 8、BL 信号兼容 ID 1，含 BL 及 RPU，EL 标志为 0。3840 × 2160 / 60 的实测 DV level 为 9。两份 RPU 的实测摘要均为 `253b1d816364d4689b30908fd5038838a5f9f1e9882edd44285f70d35e4fbd45`。逐帧导出 JSON 对照后，源与成片的 CMv2.9 显示元数据及镜头起始标志均一致。样片含 L1、L2、L4、L5、L6，一个镜头；它能验证颜色重建与逐帧关联，切镜变化另外使用合成片覆盖。[dovi_tool 2.3.4](https://github.com/quietvoid/dovi_tool/tree/2.3.4) · [Dolby Vision levels](https://ott.dolby.com/OnDelKits/Dolby_Vision_Online_Delivery_Kit/v1/Documentation/Specs/Visio_Profiles/help_files/topics/c_dovi_levels.html)

### 画面误差

Profile 5 路径先把逐帧 RPU 应用于实际解码像素，通过 libplacebo 重建 BT.2020/PQ，继而超分、编码并写入已转换的 RPU。采用同一滤镜将源 P5、独立参照片和输出均缩至 320 × 180，仅检验大范围亮度、色偏和时序。Y/Cb/Cr 的 MAE 以 10-bit 码值计，PSNR 使用峰值 1023。每项聚合全部 120 帧，基准为原 P5 重建画面。[FFmpeg libplacebo 滤镜](https://ffmpeg.org/ffmpeg-filters.html#libplacebo)

| 与 P5 重建画面对比的版本 | MAE，Y / Cb / Cr | PSNR，Y / Cb / Cr，dB |
|---|---|---|
| Dolby 独立编码的 P8.1 参照 | 1.99 / 2.55 / 0.81 | 52.30 / 46.60 / 57.71 |
| RTX 高码率 Ultra + NVENC，正式导出、质量参数 15 | 1.71 / 0.52 / 0.49 | 55.03 / 61.98 / 63.01 |
| FSRCNNX + x265，正式导出、slow / CRF 14 | 0.49 / 0.79 / 1.05 | 61.28 / 60.34 / 58.55 |

复核 FSRCNNX 行时，将上面命令中的 `--mode rtx` 换为 `--mode neural`，并使用新文件名。低分辨率测量不足以评价 4K 纹理、细节恢复或观看偏好。

可用相同参数生成原始对照帧。每帧由 320 × 180 的 Y 平面及两个 160 × 90 色度平面组成，均为小端无符号 16 位存储的 10-bit 样本。分别取两文件对应平面的差值，计算全部绝对差的均值及 `10 * log10(1023² / 均方误差)`。

```powershell
$vf = 'hwupload,libplacebo=w=320:h=180:apply_dolbyvision=1:format=yuv420p10le:colorspace=bt2020nc:color_primaries=bt2020:color_trc=smpte2084:range=tv:tonemapping=clip:inverse_tonemapping=0:peak_detect=0:deband=0:upscaler=spline36:antiringing=0.75:dither_temporal=0,hwdownload,format=yuv420p10le'
foreach ($clip in 'glass-p5.mp4','glass-p81.mp4','glass-4k.mkv') {
    & $ff -v error -init_hw_device vulkan=lf:,vendor_id=0x10de -filter_hw_device lf -i $clip -map 0:v:0 -an -vf $vf -fps_mode passthrough -pix_fmt yuv420p10le -c:v rawvideo -f rawvideo "$clip.yuv"
}
```

## P8.1 的动态有效画面区域

用 x265 编码含 B 帧的 10 帧、160 × 90 合成片，注入人工构造的 P8.1 RPU。前 5 帧 L5 上下偏移均为 4，后 5 帧均为 0。调用 `dovi.prepare_rpu` 并指定 320 × 180，注入后重新抽取，结果分别为 8 / 8 和 0 / 0。10 条 RPU 全量字节校验通过；将缩放前的 RPU 故意作为期望值时，验证器检出了差异。测试覆盖显示顺序及有效区域切换。[dovi_tool 编辑器](https://github.com/quietvoid/dovi_tool/blob/2.3.4/docs/editor.md)

## 从普通 SDR 生成 HDR10 和 Profile 8.1

用九个均匀色块横排生成 BT.709 的 10-bit 测试图，依次为黑、50% 灰、75% 灰、90% 灰、白、肤色及红绿蓝三原色。肤色的归一化 RGB 为 0.68 / 0.46 / 0.35。输入 288 × 96，输出 576 × 192，每个色块取中心采样点。将输出的 BT.2020/PQ 转为线性 RGB 后，以 0.2627 / 0.6780 / 0.0593 加权得到下表的亮度。

| 样本 | 实测亮度，cd/m² |
|---|---|
| 50%、75%、90% 灰 | 37.95、99.92、227.16 |
| 肤色色块 | 40.12 |
| 白，FSRCNNX / Spline36 | 973.19 / 993.79 |
| 白，仅作 SDR → PQ 常规转换、跳过高光扩展 | 约 199 |

曲线以 203 cd/m² 作为线性化后的参考白，从 120 cd/m² 起扩展高光，在映射后限制最亮 RGB 通道至 1000 cd/m²。接近白的区域可能被显著提亮，已经剪切的纹理无法恢复。转换后以实测色块为准，单个码值会受缩放、量化和压缩影响。[映射着色器源码](../shaders/SDR_highlights_1000.glsl)

另用连续 3 帧色板、3 帧黑、3 帧白编码 HEVC，再调用 `hdr.measure_output` 和 `dovi.generate_rpu_from_measurements`。回读确认 9 条 RPU，镜头从第 0、3、6 帧开始。三个镜头测得的 12-bit PQ 最小 / 最大 / 平均分别为 0 / 3076 / 1814、5 / 5 / 5、3062 / 3062 / 3062；经 dovi_tool 的 CMv2.9 L1 取值约束，实际写入值为 0 / 3076 / 1814、5 / 2081 / 819、12 / 3062 / 3061。分析在宽 480 像素的副本上逐帧进行，可能漏掉很小的高亮点。新 RPU 是对映射画面的自动估计，原 SDR 片源没有可继承的杜比创作 trims。[1.1.1 测量着色器](https://github.com/lingjieheti/LumaFrame-Studio/blob/v1.1.1/shaders/Measure_CIE_Y.glsl) · [dovi_tool 生成器](https://github.com/quietvoid/dovi_tool/blob/2.3.4/docs/generator.md)

Profile 7（含增强层）、Profile 8.4（HLG）以及加密输入不在此版本的验证与支持范围。真实 P5 样片只有一个镜头，缺少足以重建完整静态母版色度坐标的 RPU L9，本片仅写入可以追溯到原输入的静态信息。保留逐帧动态元数据不等于在所有电视、功放和播放软件上都能触发杜比视界。


## 1.1.2 的自动 L1 分析

从 BT.709 制作新杜比视界时，软件按最终 HEVC 画面的 PQ 编码 R、G、B 三个通道中最大值估算 L1。ETSI TS 103 572 对 min_PQ、max_PQ、avg_PQ 的定义均引用 PQ MaxRGB。旧版本使用线性加权的 CIE Y，饱和色的测量值因通道权重而偏低。分镜及 480 像素宽采样仍为软件近似。已有杜比片源使用原始元数据。[ETSI TS 103 572，第 11–12 页](https://www.etsi.org/deliver/etsi_ts/103500_103599/103572/01.03.01_60/ts_103572v010301p.pdf) · [新版着色器](../shaders/Measure_MaxRGB.glsl)

使用 BT.2020/PQ、10 位有限范围 HEVC 无损色块，各取 3 帧。红、绿、蓝、灰的最大 PQ 通道值均设为 0.75，12 位理论值为 3071。实测新版读数依次为 3071、3067、3071、3071，旧版为 2478、2889、1861、3067。此处比较分析器读数，生成器写入 RPU 时还会执行 CMv2.9 的取值约束。
