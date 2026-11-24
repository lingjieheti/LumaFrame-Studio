# 自动制作杜比视界时的低对比度剪辑

2026 年 11 月 25 日在 Windows、RTX 5080、随软件提供的 FFmpeg 8.1 上复核。1.4.0 已能识别强硬切；本次补测了两处在十位 SDR 转 PQ 后变弱的颜色硬切。通过 1.4.0 正式安装版和新版引擎分别导出 4K MKV，再用 dovi_tool 从最终文件抽回 RPU。16 帧解码后全部十位 YUV 像素哈希相同，亮度元数据的分镜发生了变化。[上一版的漏检记录](scene-analysis-1.4.md) · [Dolby 逐镜分析说明](https://professionalsupport.dolby.com/s/article/Dolby-Vision-Content-Creation-Best-Practices-Guide?language=en_US)

| 样片 | 1.4.0 检出的起始帧 | 本次检出的起始帧 |
|---|---|---|
| 16 帧 BT.709，灰、彩色、灰、亮灰，各 4 帧 | 0、12 | 0、4、8、12 |
| 两段暗灰 PQ，检测用 RGB 帧的平均绝对差为 5.47/100 | 0 | 0、8 |
| 只有 1.95/100 差异的暗灰硬切 | 0 | 0 |
| 突然平移纹理，RGB 差 6.78/100，平均 MaxRGB PQ 变化 0.18% 满量程 | 0 | 0 |
| 单帧闪光、两帧闪光、三帧闪光，各一段 | 每段均为 0 | 每段均为 0 |
| 匀速平移、加速平移、灰阶渐变、彩色渐变，各一段 | 每段均为 0 | 每段均为 0 |
| Netflix《Sparks》《Meridian》各 60 帧拼接，真实交界在第 60 帧 | 0、60 | 0、60 |
| 《Meridian》另一段 60 帧连续镜头（原始帧 8660–8719） | 0 | 0 |

第一行另外复核了最终 RPU 中每个镜头的平均 PQ。原来的四组读数为 2047、2047、2047、2809，新版为 1959、2222、1959、2809，单位为十二位 PQ 码值。第二行和第三行的十位有限范围中性 Y′ 起点均为 150，分别跳至 197 和 166。真实电影样本只覆盖三个短镜头。很短的插镜、轻微的构图变化和曝光变化仍需要结合原片辨认。[自然镜头与来源](compressed-1.4.md)

检测沿用 1.4.0 的 320 × 180 以内 RGB 缩略图、三帧中值和 FFmpeg 强硬切信号。在此基础上，软件读取连续帧的平均绝对 RGB 差异（MAFD）。对于额外的弱候选，差异要达到 4/100，且达到前后三个相邻转换各自平均差异中较大值的四倍。转换前两帧和转换后两帧的平均 MaxRGB PQ 还需相差至少 2% 满量程。这样可在镜头内运动较平稳时识别突变，同时减少纹理平移和短时闪光的误切。此处采用本项目实测的阈值。参考的 PySceneDetect AdaptiveDetector 采用其自己的帧差表示与阈值。[FFmpeg scdet 实现](https://github.com/FFmpeg/FFmpeg/blob/n8.1/libavfilter/vf_scdet.c) · [PySceneDetect 自适应检测说明](https://www.scenedetect.com/api/#adaptive-detector)

## 复验

从源码根目录运行下列命令可复验弱候选、相机运动、短闪光以及写入后从 HEVC 回读 RPU 的检查。Vulkan 测试使用与应用相同的 FFmpeg 和 dovi_tool。[回归测试](../tests/test_hdr_scenes.py)

```powershell
$env:LUMAFRAME_TEST_VULKAN = '1'
python -m unittest discover -s tests -p 'test_hdr_scenes.py'
```

要复做表格第一行，可制作 640 × 360、25 fps、10 位有限范围 BT.709 的无损 4:2:0 视频。四段各四帧，填充的十位 Y′/Cb/Cr 三元组依次为 (640,512,512)、(640,360,812)、(640,512,512)、(905,512,512)。在软件中以参考模式和默认高光扩展生成杜比视界，再抽取刷新标志。下列命令在相应的 Windows 安装目录下执行，输入与输出路径按实际情况填写。

```powershell
.\LumaFrameStudio.exe --export source.mkv --target newdv --mode reference --output output.mkv --result report.json
.\bin\dovi_tool.exe extract-rpu -i output.mkv -o actual.rpu
.\bin\dovi_tool.exe export -i actual.rpu -d scenes=cuts.txt --levels-format json --levels level1=level1.json
```
