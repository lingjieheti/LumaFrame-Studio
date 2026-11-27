"""Translations for LumaFrame's media engine. Paths and tool output remain opaque."""

import re
from string import Formatter


# Source messages are shared with the engine's Chinese CLI and compatibility callbacks.
ENGINE_MESSAGES = {
    # Inspection, setup and export errors
    "需要恰好一条主视频轨。含多角度、辅助视频轨或无视频轨的文件目前无法处理。": (
        "Exactly one main video track is required. Multi-angle files and files with additional video tracks or no video track are unsupported.",
        "主映像トラックが1本必要です。マルチアングル、追加の映像トラック、または映像トラックのないファイルには対応していません。",
    ),
    "无法读取视频帧率。": (
        "Could not read the video frame rate.",
        "映像のフレームレートを読み取れません。",
    ),
    "片源已超出 4K 的工作画布。": (
        "The source exceeds the 4K processing dimensions.",
        "入力映像のサイズが4Kの処理範囲を超えています。",
    ),
    "此文件带旋转矩阵，暂不支持旋转后导出。": (
        "Exporting video with rotation metadata is unsupported.",
        "回転情報のある映像の書き出しには対応していません。",
    ),
    "容器带额外的轨道裁切信息，无法用现有几何参数安全放大。": (
        "The container specifies additional track cropping. The resulting dimensions cannot be upscaled reliably.",
        "コンテナに追加のトラッククロップ情報があり、正しい寸法で拡大できません。",
    ),
    "隔行视频需要先去隔行。": (
        "Deinterlace the source video before exporting.",
        "インターレース映像は先にデインターレース処理を行ってください。",
    ),
    "杜比视界片源需使用方形像素，以保持有效画面与元数据一致。": (
        "Dolby Vision sources require square pixels so the active area matches the metadata.",
        "Dolby Visionの有効画面領域とメタデータを一致させるには、ピクセルアスペクト比が1:1の素材が必要です。",
    ),
    "HDR10 需要 HEVC Main 10、方形像素、有限范围 BT.2020/PQ，画宽上限为 3840。": (
        "HDR10 requires HEVC Main 10, square pixels, limited-range BT.2020/PQ and a width of up to 3840 pixels.",
        "HDR10にはHEVC Main 10、正方画素、リミテッドレンジのBT.2020/PQが必要です。幅の上限は3840ピクセルです。",
    ),
    "HDR10 · 静态 PQ": (
        "HDR10 · static PQ",
        "HDR10 · 静的PQ",
    ),
    "Dolby Vision Profile 7 双层": (
        "Dolby Vision Profile 7 (dual layer)",
        "Dolby Vision Profile 7（デュアルレイヤー）",
    ),
    "暂不支持此像素格式（{pixel_format}）。": (
        "Unsupported pixel format ({pixel_format}).",
        "このピクセル形式（{pixel_format}）には対応していません。",
    ),
    "SDR 转换仅支持 BT.709；已有 HDR10、HLG 或其它色彩空间不能按 SDR 处理。": (
        "SDR conversion requires BT.709. HDR10, HLG and other color spaces cannot be processed as SDR.",
        "SDR変換はBT.709のみ対応しています。HDR10、HLG、その他の色空間はSDRとして処理できません。",
    ),
    "低分辨率片源缺少完整 BT.709 标记，无法确认色彩空间。": (
        "The low-resolution source has incomplete BT.709 tags. Its color space could not be confirmed.",
        "低解像度素材のBT.709タグが不完全なため、色空間を確認できません。",
    ),
    "HD 片源缺少部分色彩标记，按 SDR BT.709 解释。": (
        "Some color tags are missing. The HD source will be interpreted as SDR BT.709.",
        "HD素材の一部の色タグが欠けているため、SDR BT.709として扱います。",
    ),
    "片源已有动态 HDR 元数据，无法按静态 HDR10 制作。": (
        "The source contains dynamic HDR metadata and cannot be processed as static HDR10.",
        "入力に動的HDRメタデータが含まれるため、静的HDR10として処理できません。",
    ),
    "帧内 HDR 元数据与 SDR 色彩标记或推定的 BT.709 冲突，请核对片源。": (
        "HDR metadata in the frames conflicts with the SDR tags or inferred BT.709 space. Check the source.",
        "フレーム内のHDRメタデータとSDRタグまたは推定されたBT.709色空間が一致しません。素材をご確認ください。",
    ),
    "HDR10 原片缺少可读取的 ST 2086 母版显示信息。": (
        "The HDR10 source has no readable ST 2086 mastering display metadata.",
        "HDR10素材からST 2086のマスタリングディスプレイ情報を読み取れません。",
    ),
    "视频缺少可还原的显示时间戳。": (
        "The video has no recoverable presentation timestamps.",
        "映像の表示タイムスタンプを復元できません。",
    ),
    "视频显示时间戳为空或重复，无法保证逐帧同步。": (
        "Missing or duplicate presentation timestamps prevent frame-accurate synchronization.",
        "表示タイムスタンプの欠落または重複により、フレーム単位で同期できません。",
    ),
    "无法校验 HEVC 头中的档位。": (
        "Could not verify the tier in the HEVC header.",
        "HEVCヘッダーのティアを確認できません。",
    ),
    "无法核验杜比视界等级 {level}。": (
        "Could not verify Dolby Vision level {level}.",
        "Dolby Visionレベル{level}を確認できません。",
    ),
    "输出信令的级别或档位与实际画宽、编码码率约束不符。": (
        "The signaled level or tier is incompatible with the actual width or encoder bitrate limit.",
        "出力のレベルまたはティアが、実際の画面幅やエンコーダーのビットレート制限と一致しません。",
    ),
    "缺少 {name}，请重新解压完整安装包。": (
        "{name} is missing. Reinstall the complete application package.",
        "{name}が見つかりません。完全なパッケージを再インストールしてください。",
    ),
    "任务已取消。": (
        "Export canceled.",
        "書き出しをキャンセルしました。",
    ),
    "MKV 封装发出警告，未发布文件。": (
        "The MKV muxer reported a warning. The output file was not published.",
        "MKVの多重化で警告が発生したため、出力ファイルを保存しませんでした。",
    ),
    "{tool} 返回 {code}。": (
        "{tool} exited with code {code}.",
        "{tool}が終了コード{code}を返しました。",
    ),
    "目标文件已存在，请选择新的输出文件名。": (
        "The destination file already exists. Choose a different filename.",
        "出力先に同じ名前のファイルがあります。別のファイル名を指定してください。",
    ),
    "请选择现有文件夹中的 .mkv 输出文件。": (
        "Choose an .mkv output file in an existing folder.",
        "既存のフォルダーに出力する.mkvファイルを指定してください。",
    ),
    "未知的 RTX 速度模式。": (
        "Unknown RTX speed mode.",
        "不明なRTX速度モードです。",
    ),
    "并行加速仅用于 RTX 超分导出。": (
        "Parallel acceleration is available only for RTX upscaling exports.",
        "並列高速化はRTX超解像での書き出しにのみ使用できます。",
    ),
    "不支持的处理模式或输出格式。": (
        "Unsupported processing mode or output format.",
        "この処理モードまたは出力形式には対応していません。",
    ),
    "分析边距仅用于新制作的 HDR10 或杜比视界。": (
        "Analysis margins apply only when creating new HDR10 or Dolby Vision output.",
        "解析マージンを指定できるのは、HDR10またはDolby Visionを新たに作成する場合のみです。",
    ),
    "压缩片源修复仅适用于需要放大的静态 HDR10 或 Dolby Vision Profile 8.1。": (
        "Compressed-source restoration requires a static HDR10 or Dolby Vision Profile 8.1 source that needs upscaling.",
        "圧縮映像の補正は、拡大が必要な静的HDR10またはDolby Vision Profile 8.1の素材にのみ使用できます。",
    ),
    "亮度映射仅适用于 SDR 片源，请选择有效的映射方式。": (
        "Luminance mapping requires an SDR source and a supported mapping method.",
        "輝度マッピングはSDR素材専用です。対応する方式を選択してください。",
    ),
    "原生 HDR10 请选择生成杜比视界并保留原始编码画面。": (
        "For native HDR10, choose Dolby Vision creation with the original encoded video.",
        "ネイティブHDR10では、元の映像ビットストリームを保持してDolby Visionを作成してください。",
    ),
    "原码流无损封装需要尺寸已匹配的 Profile 8.1 片源。": (
        "Lossless remuxing requires a Profile 8.1 source at the target dimensions.",
        "無劣化の再多重化には、出力寸法と一致するProfile 8.1素材が必要です。",
    ),
    "双层原样保留仅适用于原生 4K Profile 7 片源。": (
        "Preserving both original layers requires a native 4K Profile 7 source.",
        "元の2レイヤーをそのまま保持できるのは、ネイティブ4KのProfile 7素材のみです。",
    ),
    "保留杜比需要 Profile 5、7 或 8.1。生成 HDR10 或新杜比视界请选择 BT.709 SDR 或静态 HDR10 片源。": (
        "To preserve Dolby Vision, use a Profile 5, 7 or 8.1 source. To create HDR10 or new Dolby Vision, use BT.709 SDR or static HDR10.",
        "Dolby Visionの保持にはProfile 5、7、8.1のいずれかが必要です。HDR10または新しいDolby Visionの作成には、BT.709 SDRまたは静的HDR10素材を選択してください。",
    ),
    "此杜比视界配置需要先处理色彩或增强层，请选择 RTX AI、FSRCNNX 或参考模式。": (
        "This Dolby Vision configuration needs color or enhancement-layer processing. Choose RTX AI, FSRCNNX or reference mode.",
        "このDolby Vision構成には色または拡張レイヤーの処理が必要です。RTX AI、FSRCNNX、リファレンスのいずれかを選択してください。",
    ),
    "NIS 模式仅处理现有 Profile 8.1 片源；SDR 片源可选择 RTX AI、FSRCNNX 或参考模式。": (
        "NIS mode requires an existing Profile 8.1 source. For SDR, choose RTX AI, FSRCNNX or reference mode.",
        "NISモードは既存のProfile 8.1素材専用です。SDR素材にはRTX AI、FSRCNNX、リファレンスのいずれかを選択してください。",
    ),
    "FSRCNNX 需要超过 1.30 倍的放大；请改用参考模式或可用的 RTX AI。": (
        "FSRCNNX requires upscaling by more than 1.30×. Choose reference mode or RTX AI if available.",
        "FSRCNNXは1.30倍を超える拡大に対応します。リファレンスモードまたは利用可能なRTX AIを選択してください。",
    ),
    "此片源已达到输出尺寸，请使用参考模式在原分辨率处理。": (
        "The source already matches the output dimensions. Use reference mode to process it at its original resolution.",
        "入力がすでに出力寸法と一致しています。元の解像度で処理するにはリファレンスモードを選んでください。",
    ),
    "封装工具无法完整读取此文件的单一主视频轨。": (
        "The muxer could not read the complete main video track.",
        "多重化ツールがメイン映像トラック全体を読み取れませんでした。",
    ),
    "此容器封面无法按原附件保留。": (
        "The container cover cannot be preserved as its original attachment.",
        "コンテナのカバー画像を元の添付ファイルとして保持できません。",
    ),
    # Processing stages
    "正在从 NVIDIA 下载并验证 VFX 运行库（416 MiB）…": (
        "Downloading and verifying the NVIDIA VFX runtime (416 MiB)…",
        "NVIDIA VFXランタイム（416 MiB）をダウンロードして検証中…",
    ),
    "正在准备导出": (
        "Preparing export",
        "書き出しを準備中",
    ),
    "检查片源与轨道": (
        "Inspecting source and tracks",
        "素材とトラックを確認中",
    ),
    "复制并计算原始 MKV 校验值": (
        "Copying the original MKV and computing its checksum",
        "元のMKVをコピーし、チェックサムを計算中",
    ),
    "逐字节回读并核对 MKV 校验值": (
        "Reading back the MKV and verifying its checksum",
        "MKVを全バイト読み直し、チェックサムを照合中",
    ),
    "读取原片逐帧时间戳": (
        "Reading source frame timestamps",
        "元映像の各フレームのタイムスタンプを読み取り中",
    ),
    "核对增强层逐帧时间戳": (
        "Checking enhancement-layer frame timestamps",
        "拡張レイヤーの各フレームのタイムスタンプを照合中",
    ),
    "提取原始视频轨": (
        "Extracting the source video track",
        "元の映像トラックを抽出中",
    ),
    "提取增强层视频轨": (
        "Extracting the enhancement-layer video track",
        "拡張レイヤーの映像トラックを抽出中",
    ),
    "提取基础层视频轨": (
        "Extracting the base-layer video track",
        "ベースレイヤーの映像トラックを抽出中",
    ),
    "核对原始 HDR10 编码画面与母版信息": (
        "Verifying the original HDR10 video bitstream and mastering metadata",
        "元のHDR10映像ビットストリームとマスタリング情報を検証中",
    ),
    "核对原始基础层": (
        "Checking the original base layer",
        "元のベースレイヤーを照合中",
    ),
    "核对原始增强层": (
        "Checking the original enhancement layer",
        "元の拡張レイヤーを照合中",
    ),
    "逐帧合成杜比增强层并编码": (
        "Compositing the Dolby Vision enhancement layer and encoding each frame",
        "Dolby Visionの拡張レイヤーを各フレームに合成してエンコード中",
    ),
    "核对 MEL 原始基础层": (
        "Checking the original MEL base layer",
        "MEL素材の元のベースレイヤーを照合中",
    ),
    "已保留 MEL 原始基础层，正在转换逐帧动态元数据": (
        "Converting per-frame dynamic metadata with the original MEL base layer preserved",
        "MEL素材の元のベースレイヤーを保持し、各フレームの動的メタデータを変換中",
    ),
    "提取杜比视界元数据并换算有效画面尺寸": (
        "Extracting Dolby Vision metadata and rescaling active-area dimensions",
        "Dolby Visionメタデータを抽出し、有効画面領域の寸法を換算中",
    ),
    "核对原始视频码流": (
        "Checking the source video bitstream",
        "元の映像ビットストリームを照合中",
    ),
    "已保留原始单层视频与逐帧动态元数据": (
        "Original single-layer video and per-frame dynamic metadata preserved",
        "元のシングルレイヤー映像とフレーム単位の動的メタデータを保持しました",
    ),
    "已保留原始双层视频": (
        "Original dual-layer video preserved",
        "元のデュアルレイヤー映像を保持しました",
    ),
    "RTX 超分与 HEVC 编码": (
        "RTX upscaling and HEVC encoding",
        "RTX超解像とHEVCエンコード",
    ),
    "NVIDIA 高位深缩放与 HEVC 编码": (
        "NVIDIA high-bit-depth scaling and HEVC encoding",
        "NVIDIA高ビット深度スケーリングとHEVCエンコード",
    ),
    "神经网络超分与 x265 编码": (
        "Neural upscaling and x265 encoding",
        "ニューラル超解像とx265エンコード",
    ),
    "高位深缩放与 x265 编码": (
        "High-bit-depth scaling and x265 encoding",
        "高ビット深度スケーリングとx265エンコード",
    ),
    "逐帧测量 MaxCLL 与 MaxFALL": (
        "Measuring MaxCLL and MaxFALL frame by frame",
        "各フレームのMaxCLLとMaxFALLを測定中",
    ),
    "精测临界亮度值": (
        "Refining near-threshold luminance measurements",
        "しきい値付近の輝度を精密測定中",
    ),
    "写入实测 HDR10 亮度": (
        "Writing measured HDR10 light levels",
        "実測したHDR10の輝度情報を書き込み中",
    ),
    "测量杜比视界逐镜亮度": (
        "Measuring luminance by scene for Dolby Vision",
        "Dolby Vision用にシーンごとの輝度を測定中",
    ),
    "生成逐帧杜比视界元数据": (
        "Generating Dolby Vision metadata for each frame",
        "各フレームのDolby Visionメタデータを生成中",
    ),
    "写入逐帧杜比视界元数据": (
        "Writing per-frame Dolby Vision metadata",
        "フレーム単位のDolby Visionメタデータを書き込み中",
    ),
    "读取封装所需的音轨与章节": (
        "Reading audio tracks and chapters for muxing",
        "多重化する音声トラックとチャプターを読み取り中",
    ),
    "按原时间戳封装视频及原片轨道": (
        "Muxing video and source tracks with their original timestamps",
        "元のタイムスタンプで映像と各トラックを多重化中",
    ),
    "复读输出并核验尺寸与色彩信令": (
        "Reopening the output to verify dimensions and color signaling",
        "出力を読み直し、寸法と色信号を検証中",
    ),
    "复核成片逐帧显示时间": (
        "Verifying output frame presentation timestamps",
        "出力の各フレームの表示タイムスタンプを検証中",
    ),
    "核验 HDR 母版、音字幕轨与章节": (
        "Verifying HDR mastering metadata, audio, subtitles and chapters",
        "HDRマスタリング情報、音声、字幕、チャプターを検証中",
    ),
    "拆分成片并核验双层视频": (
        "Separating and verifying the two output video layers",
        "出力映像を2つのレイヤーに分離して検証中",
    ),
    "回读并核对成片基础层": (
        "Reading back and checking the output base layer",
        "出力のベースレイヤーを読み直して照合中",
    ),
    "回读并核对成片增强层": (
        "Reading back and checking the output enhancement layer",
        "出力の拡張レイヤーを読み直して照合中",
    ),
    "提取成片视频并复核编码画面": (
        "Extracting the output video and checking coded pictures",
        "出力映像を抽出して符号化画像を照合中",
    ),
    "核对成片编码画面与参数集": (
        "Checking output coded pictures and parameter sets",
        "出力の符号化画像とパラメータセットを照合中",
    ),
    "回读成片杜比视界元数据": (
        "Reading back the output Dolby Vision metadata",
        "出力のDolby Visionメタデータを読み直し中",
    ),
    "核对杜比视界帧数及 SHA-256": (
        "Verifying the Dolby Vision frame count and SHA-256 digest",
        "Dolby Visionのフレーム数とSHA-256を照合中",
    ),
    "导出与校验均已完成": (
        "Export and verification complete",
        "書き出しと検証が完了しました",
    ),
    "正在选择影片中段的关键帧": (
        "Selecting a keyframe near the middle of the film",
        "映像の中ほどからキーフレームを選択中",
    ),
    "合成杜比增强层并编码": (
        "Compositing the Dolby Vision enhancement layer and encoding",
        "Dolby Visionの拡張レイヤーを合成してエンコード中",
    ),
    "合成杜比增强层": (
        "Compositing the Dolby Vision enhancement layer",
        "Dolby Visionの拡張レイヤーを合成中",
    ),
    # Track, video and file validation
    "封装后副轨数量变化。": (
        "The number of auxiliary tracks changed during muxing.",
        "多重化後に副トラック数が変わりました。",
    ),
    "音轨或字幕轨顺序发生变化。": (
        "The audio or subtitle track order changed.",
        "音声または字幕トラックの順序が変わりました。",
    ),
    "副轨属性未保留：{key}。": (
        "An auxiliary track property was not preserved: {key}.",
        "副トラックの属性が保持されませんでした：{key}。",
    ),
    "封装后音频、字幕或数据轨数量变化。": (
        "The count of audio, subtitle or data tracks changed after muxing.",
        "多重化後に音声、字幕、またはデータトラックの数が変わりました。",
    ),
    "副轨编码变化：{old} → {new}。": (
        "An auxiliary track codec changed: {old} → {new}.",
        "副トラックのコーデックが変わりました：{old} → {new}。",
    ),
    "音轨声道数或采样率发生变化。": (
        "The audio channel count or sample rate changed.",
        "音声のチャンネル数またはサンプリング周波数が変わりました。",
    ),
    "字幕轨的起始时间发生了变化。": (
        "The start time of a subtitle track changed.",
        "字幕トラックの開始時刻が変わりました。",
    ),
    "转换后字幕的显示时间、时长或条数发生了变化。": (
        "Subtitle presentation times, durations or cue counts changed after conversion.",
        "変換後に字幕の表示時刻、表示時間、または件数が変わりました。",
    ),
    "附件名称或字节数校验失败。": (
        "Attachment names or sizes do not match.",
        "添付ファイルの名前またはサイズが一致しません。",
    ),
    "章节数量校验失败。": (
        "The chapter count does not match.",
        "チャプター数が一致しません。",
    ),
    "章节时间校验失败。": (
        "Chapter timestamps do not match.",
        "チャプターの時刻が一致しません。",
    ),
    "章节结束时间校验失败。": (
        "Chapter end times do not match.",
        "チャプターの終了時刻が一致しません。",
    ),
    "章节名称校验失败。": (
        "Chapter names do not match.",
        "チャプター名が一致しません。",
    ),
    "双轨 BL/EL 的逐帧顺序或时间戳不匹配。": (
        "Frame order or timestamps differ between the BL and EL tracks.",
        "BLとELトラックのフレーム順序またはタイムスタンプが一致しません。",
    ),
    "HDR10 基础层图像数与显示时间戳数不符。": (
        "The HDR10 base-layer picture count differs from the presentation timestamp count.",
        "HDR10ベースレイヤーの画像数と表示タイムスタンプ数が一致しません。",
    ),
    "双层编码图像数与原片显示帧数不符。": (
        "The dual-layer coded picture count differs from the source presentation frame count.",
        "デュアルレイヤーの符号化画像数と元映像の表示フレーム数が一致しません。",
    ),
    "混合 FEL/MEL 请使用原生 4K 双层原样保留模式。": (
        "For mixed FEL/MEL, use native 4K dual-layer preservation mode.",
        "FELとMELが混在する素材には、ネイティブ4Kのデュアルレイヤー保持モードを使用してください。",
    ),
    "MEL 基础层的编码图像数与原片显示帧数不符。": (
        "The MEL base-layer coded picture count differs from the source presentation frame count.",
        "MELベースレイヤーの符号化画像数と元映像の表示フレーム数が一致しません。",
    ),
    "原片编码图像数和显示帧数不一致。": (
        "The source coded picture count differs from its presentation frame count.",
        "元映像の符号化画像数と表示フレーム数が一致しません。",
    ),
    "原片 RPU 数量与视频显示帧数不符。": (
        "The source RPU count differs from the video presentation frame count.",
        "元のRPU数と映像の表示フレーム数が一致しません。",
    ),
    "新生成的 RPU 帧数与原视频不一致。": (
        "The generated RPU frame count differs from the source video frame count.",
        "生成したRPUのフレーム数が元映像と一致しません。",
    ),
    "导出尺寸校验失败。": (
        "The output dimensions do not match the requested dimensions.",
        "出力寸法が指定された寸法と一致しません。",
    ),
    "输出的 10 位 HDR10/PQ 色彩信令未通过校验。": (
        "Output 10-bit HDR10/PQ color signaling failed verification.",
        "出力の10ビットHDR10/PQ色信号を確認できませんでした。",
    ),
    "无损封装后杜比视界等级发生变化。": (
        "The Dolby Vision level changed during lossless remuxing.",
        "無劣化の再多重化後にDolby Visionレベルが変わりました。",
    ),
    "HDR10 输出意外携带杜比视界配置。": (
        "The HDR10 output unexpectedly contains Dolby Vision configuration data.",
        "HDR10出力に想定外のDolby Vision構成情報が含まれています。",
    ),
    "输出帧数或逐帧显示时间与原片不一致。": (
        "The output frame count or frame presentation timestamps differ from the source.",
        "出力のフレーム数または各フレームの表示時刻が元映像と一致しません。",
    ),
    "输出视频的末帧显示时长与原片不一致。": (
        "The display duration of the final output frame differs from the source.",
        "出力の最終フレームの表示時間が元映像と一致しません。",
    ),
    "成片中的静态 HDR 母版或内容亮度与预期不符。": (
        "The output static HDR mastering metadata or content light levels differ from the expected values.",
        "出力の静的HDRマスタリング情報またはコンテンツ輝度情報が期待値と一致しません。",
    ),
    "封装后音轨相对视频发生时移。": (
        "Audio shifted relative to the video during muxing.",
        "多重化後に映像に対して音声の時刻がずれました。",
    ),
    "原样保留的 MKV 整文件 SHA-256 回读不一致。": (
        "The SHA-256 digest of the preserved MKV did not match on readback.",
        "元のまま保持したMKV全体を読み直したところ、SHA-256が一致しませんでした。",
    ),
    "成片 BL、EL 或原始 RPU 字节校验失败。": (
        "Output BL, EL or original RPU bytes failed verification.",
        "出力のBL、EL、または元のRPUのバイト列を検証できませんでした。",
    ),
    "无损封装后的 HEVC 编码画面、参数集或附加数据发生变化。": (
        "HEVC coded pictures, parameter sets or supplementary data changed during lossless remuxing.",
        "無劣化の再多重化後にHEVCの符号化画像、パラメータセット、または補助データが変わりました。",
    ),
    "片源在导出期间发生了变化，请重新导出。": (
        "The source file changed during processing. Export it again.",
        "書き出し中に素材が変更されました。もう一度書き出してください。",
    ),
    # Dolby Vision metadata and active area
    "缺少配套的 bin/dovi_tool.exe，请完整解压软件。": (
        "The bundled bin/dovi_tool.exe is missing. Reinstall the complete application.",
        "同梱のbin/dovi_tool.exeがありません。完全なパッケージを再インストールしてください。",
    ),
    "杜比视界保留模式仅支持 HEVC Profile 8.1 片源。": (
        "This Dolby Vision preservation mode requires HEVC Profile 8.1 input.",
        "このDolby Vision保持モードにはHEVC Profile 8.1素材が必要です。",
    ),
    "未读到唯一的 Dolby Vision 配置记录，无法确认片源是 Profile 8.1。": (
        "Could not identify a single Dolby Vision configuration record to confirm Profile 8.1.",
        "Dolby Vision構成レコードを1つに特定できず、Profile 8.1であることを確認できません。",
    ),
    "片源不是单层 HEVC Dolby Vision Profile 8.1（HDR10 兼容）。": (
        "The source is not single-layer HEVC Dolby Vision Profile 8.1 (HDR10 compatible).",
        "入力がHDR10互換のシングルレイヤーHEVC Dolby Vision Profile 8.1に該当しません。",
    ),
    "杜比视界保留模式要求 10-bit 4:2:0 基础层。": (
        "Dolby Vision preservation requires a 10-bit 4:2:0 base layer.",
        "Dolby Visionの保持には10ビット4:2:0のベースレイヤーが必要です。",
    ),
    "Profile 8.1 需要 HDR10 基础层，{field} 应为 {value}，实际为 {actual}。": (
        "Profile 8.1 requires an HDR10 base layer. Expected {field} to be {value}, found {actual}.",
        "Profile 8.1にはHDR10ベースレイヤーが必要です。{field}の期待値は{value}ですが、実際の値は{actual}です。",
    ),
    "dovi_tool 的运行器需要返回 stdout 文本。": (
        "The dovi_tool runner must return standard output as text.",
        "dovi_toolの実行処理は標準出力をテキストで返す必要があります。",
    ),
    "RPU 实际内容不是统一的 Profile 8，或解析失败。": (
        "The RPU data could not be parsed or is not consistently Profile 8.",
        "RPUデータの解析に失敗したか、すべてのRPUがProfile 8に統一されていません。",
    ),
    "没有可验证的杜比视界逐帧元数据。": (
        "No per-frame Dolby Vision metadata was available to verify.",
        "検証できるフレーム単位のDolby Visionメタデータがありません。",
    ),
    "dovi_tool 的 L5 导出路径与临时目录均含逗号或等号，请指定另一工作目录。": (
        "Both the dovi_tool L5 export path and temporary folder contain commas or equals signs. Choose another working folder.",
        "dovi_toolのL5出力先と一時フォルダーの両方にカンマまたは等号が含まれています。別の作業フォルダーを指定してください。",
    ),
    "有效画面需要已知的正整数画布尺寸。": (
        "The active area requires known, positive integer frame dimensions.",
        "有効画面領域には、正の整数で指定したフレーム寸法が必要です。",
    ),
    "有效画面需要左、右、上、下四个 0 至 8191 的整数像素偏移。": (
        "Specify four integer active-area offsets from 0 to 8191 pixels: left, right, top and bottom.",
        "有効画面領域の左・右・上・下のオフセットを、0～8191ピクセルの整数で指定してください。",
    ),
    "L5 有效画面偏移超出实际画布。": (
        "L5 active-area offsets exceed the actual frame dimensions.",
        "L5の有効画面オフセットが実際のフレーム寸法を超えています。",
    ),
    "无法读取 L5 帧范围。": (
        "Could not read the L5 frame range.",
        "L5のフレーム範囲を読み取れません。",
    ),
    "杜比视界 L5 帧范围不完整或重叠。": (
        "Dolby Vision L5 frame ranges are incomplete or overlap.",
        "Dolby Vision L5のフレーム範囲に欠落または重複があります。",
    ),
    "L5 帧数和 RPU 帧数不一致。": (
        "L5 and RPU frame counts differ.",
        "L5とRPUのフレーム数が一致しません。",
    ),
    "画布尺寸必须为正数。": (
        "Frame dimensions must be positive.",
        "フレーム寸法は正の値で指定してください。",
    ),
    "未知的 Profile 7 增强层。": (
        "Unknown Profile 7 enhancement-layer type.",
        "Profile 7の拡張レイヤーの種類を識別できません。",
    ),
    "复用原始 RPU 仅适用于已校验的 Profile 7 双层片源。": (
        "Reusing the original RPU requires a verified dual-layer Profile 7 source.",
        "元のRPUを再利用できるのは、検証済みのデュアルレイヤーProfile 7素材のみです。",
    ),
    "L5 分镜有效画面偏移写回后复检未通过。": (
        "L5 scene active-area offsets failed verification after writing.",
        "書き込み後のL5シーン別有効画面オフセットが検証に失敗しました。",
    ),
    "写入的杜比 L5 有效画面与指定像素范围不一致。": (
        "The written Dolby Vision L5 active area differs from the specified pixel bounds.",
        "書き込んだDolby Vision L5の有効画面領域が、指定したピクセル範囲と一致しません。",
    ),
    "成品中逐帧提取的 Dolby Vision RPU 与计划写入的数据不一致。": (
        "The Dolby Vision RPU extracted from the output frames differs from the intended data.",
        "出力の各フレームから抽出したDolby Vision RPUが、予定されていたデータと一致しません。",
    ),
    "核验指定的有效画面时需要给出输出画布尺寸。": (
        "Specify the output frame dimensions to verify the requested active area.",
        "指定された有効画面領域を検証するには、出力フレーム寸法が必要です。",
    ),
    "场景帧范围必须从 0 开始连续覆盖所有帧。": (
        "Scene ranges must cover every frame consecutively, starting at frame 0.",
        "シーン範囲はフレーム0から始まり、すべてのフレームを連続して含む必要があります。",
    ),
    "场景亮度必须是有序的 12-bit PQ 整数。": (
        "Scene luminance values must be ordered 12-bit PQ integers.",
        "シーンの輝度値は順序どおりの12ビットPQ整数で指定してください。",
    ),
    "画面测量没有得到任何场景。": (
        "No scenes were found during picture analysis.",
        "映像解析でシーンを取得できませんでした。",
    ),
    "L6 亮度字段超出 16-bit 合法范围。": (
        "L6 luminance fields exceed the valid 16-bit range.",
        "L6の輝度フィールドが16ビットの有効範囲を超えています。",
    ),
    "写入指定的有效画面时需要给出输出画布尺寸。": (
        "Specify the output frame dimensions to write the requested active area.",
        "指定された有効画面領域を書き込むには、出力フレーム寸法が必要です。",
    ),
    "生成的杜比视界帧数与测量帧数不一致。": (
        "The generated Dolby Vision frame count differs from the measured frame count.",
        "生成されたDolby Visionのフレーム数が測定したフレーム数と一致しません。",
    ),
    # FEL processing, neural scaling and RTX
    "未知的 RTX 处理速度。": (
        "Unknown RTX processing speed.",
        "不明なRTX処理速度です。",
    ),
    "4:2:0 输出宽高必须为正偶数。": (
        "4:2:0 output width and height must be positive even numbers.",
        "4:2:0の出力幅と高さには正の偶数が必要です。",
    ),
    "编码质量或最大码率超出范围。": (
        "Encoding quality or maximum bitrate is out of range.",
        "エンコード品質または最大ビットレートが範囲外です。",
    ),
    "压缩片源修复仅适用于需要超分的杜比视界 Profile 8.1。": (
        "Compressed-source restoration requires a Dolby Vision Profile 8.1 source that needs upscaling.",
        "圧縮映像の補正には、拡大が必要なDolby Vision Profile 8.1素材が必要です。",
    ),
    "找不到 NVEncC 编码器：{path}": (
        "NVEncC encoder not found: {path}",
        "NVEncCエンコーダーが見つかりません：{path}",
    ),
    "FEL 流式输入需要偶数尺寸的 PQ12 画面。": (
        "Streamed FEL input requires PQ12 pictures with even dimensions.",
        "FELのストリーム入力には幅と高さが偶数のPQ12映像が必要です。",
    ),
    "FEL 流式输入需要正帧率。": (
        "Streamed FEL input requires a positive frame rate.",
        "FELのストリーム入力のフレームレートには正の値が必要です。",
    ),
    "未知的放大算法：{mode}": (
        "Unknown upscaling algorithm: {mode}",
        "不明な拡大アルゴリズム：{mode}",
    ),
    "FEL 像素合成需要支持 AVX2 的处理器。原生 4K 双层可选择原样保留。": (
        "FEL pixel reconstruction requires a CPU with AVX2. For native 4K dual-layer sources, you can preserve both layers.",
        "FELの画素再構成にはAVX2対応CPUが必要です。ネイティブ4Kのデュアルレイヤー素材は2層のまま保持できます。",
    ),
    "双层杜比组件缺少校验清单，请重新安装。": (
        "The dual-layer Dolby Vision component is missing its verification manifest. Reinstall the application.",
        "デュアルレイヤーDolby Visionコンポーネントの検証リストがありません。再インストールしてください。",
    ),
    "双层杜比组件校验清单不完整，请重新安装。": (
        "The dual-layer Dolby Vision verification manifest is incomplete. Reinstall the application.",
        "デュアルレイヤーDolby Visionコンポーネントの検証リストが不完全です。再インストールしてください。",
    ),
    "双层杜比运行库缺少 {name}，请重新安装。": (
        "The dual-layer Dolby Vision runtime is missing {name}. Reinstall the application.",
        "デュアルレイヤーDolby Visionランタイムに{name}がありません。再インストールしてください。",
    ),
    "双层杜比运行库 {name} 校验失败，请重新安装。": (
        "Dual-layer Dolby Vision runtime verification failed for {name}. Reinstall the application.",
        "デュアルレイヤーDolby Visionランタイムの{name}を検証できませんでした。再インストールしてください。",
    ),
    "缺少双层杜比合成组件，请重新安装完整的软件包。": (
        "The dual-layer Dolby Vision reconstruction component is missing. Reinstall the complete package.",
        "デュアルレイヤーDolby Visionの再構成コンポーネントがありません。完全なパッケージを再インストールしてください。",
    ),
    "RTX 编码视频的实际解码帧数与合成帧数不匹配。": (
        "The decoded frame count of the RTX-encoded video differs from the reconstructed frame count.",
        "RTXでエンコードした映像のデコードフレーム数が、再構成したフレーム数と一致しません。",
    ),
    "双层杜比片源缺少有效帧率或帧数。": (
        "The dual-layer Dolby Vision source lacks a valid frame rate or frame count.",
        "デュアルレイヤーDolby Vision素材のフレームレートまたはフレーム数が無効です。",
    ),
    "RTX 管道的合成画幅、帧率或杜比元数据参数不完整。": (
        "The RTX pipeline is missing reconstruction dimensions, frame rate or Dolby Vision metadata parameters.",
        "RTX処理に必要な再構成寸法、フレームレート、またはDolby Visionメタデータの設定が不完全です。",
    ),
    "FEL 编码器必须从一个标准输入管道读取画面。": (
        "The FEL encoder must read pictures from a single standard input pipe.",
        "FELエンコーダーは1本の標準入力パイプから映像を読み込む必要があります。",
    ),
    "FEL 编码器必须在任务目录内生成 HEVC 裸流。": (
        "The FEL encoder must create a raw HEVC stream in the job folder.",
        "FELエンコーダーは処理用フォルダー内にHEVCの生ストリームを作成する必要があります。",
    ),
    "双层杜比编码目标已存在。": (
        "The dual-layer Dolby Vision encoding destination already exists.",
        "デュアルレイヤーDolby Visionのエンコード先がすでに存在します。",
    ),
    "目标磁盘空间不足，无法继续编码双层杜比影片。请更换输出磁盘或释放空间。": (
        "The destination disk is full. Free up space or choose another disk to continue dual-layer Dolby Vision encoding.",
        "出力先の容量不足によりデュアルレイヤーDolby Visionのエンコードを続行できません。空き容量を確保するか、別のディスクを選択してください。",
    ),
    "双层 Dolby Vision 合成失败。": (
        "Dual-layer Dolby Vision reconstruction failed.",
        "デュアルレイヤーDolby Visionの再構成に失敗しました。",
    ),
    "双层合成输出的帧数、位深、色彩或轨道配置校验失败。": (
        "The reconstructed output failed verification of frame count, bit depth, color or track configuration.",
        "再構成した映像のフレーム数、ビット深度、色、またはトラック構成を検証できませんでした。",
    ),
    "缺少 FSRCNNX 模型文件。请重新解压完整软件包。": (
        "The FSRCNNX model file is missing. Reinstall the complete application package.",
        "FSRCNNXモデルファイルがありません。完全なパッケージを再インストールしてください。",
    ),
    "已取消 RTX 处理。": (
        "RTX processing canceled.",
        "RTX処理をキャンセルしました。",
    ),
    "NVIDIA VFX 下载失败，请检查网络或代理后重试。{detail}": (
        "NVIDIA VFX download failed. Check your network or proxy and retry. {detail}",
        "NVIDIA VFXのダウンロードに失敗しました。ネットワークまたはプロキシを確認して再試行してください。{detail}",
    ),
    "NVIDIA VFX 文件校验失败，请重新运行。": (
        "NVIDIA VFX file verification failed. Run the export again.",
        "NVIDIA VFXファイルの検証に失敗しました。もう一度実行してください。",
    ),
    "NVIDIA VFX 返回了意外的颜色位深或尺寸。": (
        "NVIDIA VFX returned an unexpected color bit depth or frame size.",
        "NVIDIA VFXから想定外の色深度またはフレーム寸法が返されました。",
    ),
    "NVIDIA VFX 输出像素排列不受支持。": (
        "The pixel layout returned by NVIDIA VFX is unsupported.",
        "NVIDIA VFXが返したピクセル配列には対応していません。",
    ),
    "视频解码器输出了不完整的一帧。": (
        "The video decoder returned an incomplete frame.",
        "映像デコーダーが不完全なフレームを出力しました。",
    ),
    "编码管道在一帧写入完成前已关闭。": (
        "The encoder pipe closed before a complete frame was written.",
        "1フレームを書き終える前にエンコードパイプが閉じました。",
    ),
    "FEL 流式合成只支持从管道读入 PQ12。": (
        "Streamed FEL reconstruction requires PQ12 pipe input.",
        "FELのストリーム再構成はパイプ経由のPQ12入力にのみ対応しています。",
    ),
    "RTX 超分需要至少一个方向的画面尺寸放大。": (
        "RTX upscaling requires an increase in width or height.",
        "RTX超解像には、幅または高さの少なくとも一方を拡大する必要があります。",
    ),
    "压缩片源修复需要有限范围 HDR10 或 HDR10 兼容的 Dolby Vision Profile 8.1。": (
        "Compressed-source restoration requires limited-range HDR10 or HDR10-compatible Dolby Vision Profile 8.1.",
        "圧縮映像の補正にはリミテッドレンジのHDR10、またはHDR10互換のDolby Vision Profile 8.1が必要です。",
    ),
    "异步帧传输": (
        "Asynchronous frame transfer",
        "非同期フレーム転送",
    ),
    "RTX 解码、色彩转换或 HEVC 编码未成功，请查看上方日志。": (
        "RTX decoding, color conversion or HEVC encoding failed. See the log above.",
        "RTXデコード、色変換、またはHEVCエンコードに失敗しました。上のログをご確認ください。",
    ),
    # HDR luminance measurement and HDR10 bitstreams
    "未知的 SDR 到 HDR 亮度映射。": (
        "Unknown SDR-to-HDR luminance mapping.",
        "不明なSDRからHDRへの輝度マッピングです。",
    ),
    "分镜检测的帧序号不连续。": (
        "Frame numbers are not consecutive during shot detection.",
        "ショット検出時のフレーム番号が連続していません。",
    ),
    "分镜检测和亮度测量的帧数不同。": (
        "Shot detection and luminance measurement returned different frame counts.",
        "ショット検出と輝度測定のフレーム数が一致しません。",
    ),
    "逐镜分析需要有效的预期帧数。": (
        "Scene analysis requires a valid expected frame count.",
        "シーン解析には有効な予定フレーム数が必要です。",
    ),
    "找不到 FFmpeg，无法测量输出亮度。": (
        "FFmpeg was not found. Output luminance cannot be measured.",
        "FFmpegが見つからないため、出力輝度を測定できません。",
    ),
    "亮度分析的帧序号不连续。": (
        "Frame numbers are not consecutive in the luminance analysis.",
        "輝度解析のフレーム番号が連続していません。",
    ),
    "逐镜亮度分析的实际帧数与输出视频不一致。": (
        "The scene luminance analysis frame count differs from the output video frame count.",
        "シーンごとの輝度解析で得たフレーム数が、出力映像と一致しません。",
    ),
    "没有可分析的输出画面。": (
        "There are no output frames to analyze.",
        "解析できる出力フレームがありません。",
    ),
    "分镜帧序号与亮度分析不一致。": (
        "Shot boundary frame numbers do not match the luminance analysis.",
        "ショット境界のフレーム番号が輝度解析と一致しません。",
    ),
    "静态 HDR10 的 SEI 报文不完整。": (
        "The static HDR10 SEI message is incomplete.",
        "静的HDR10のSEIメッセージが不完全です。",
    ),
    "静态 HDR10 的 SEI 长度超出数据范围。": (
        "The static HDR10 SEI length exceeds the available data.",
        "静的HDR10のSEI長がデータ範囲を超えています。",
    ),
    "HEVC SEI 结束位无效。": (
        "Invalid HEVC SEI trailing bits.",
        "HEVC SEIの終端ビットが無効です。",
    ),
    "无效的 Annex B 起始码。": (
        "Invalid Annex B start code.",
        "Annex Bの開始コードが無効です。",
    ),
    "HEVC 数据缺少有效的 NAL 边界。": (
        "The HEVC data lacks valid NAL unit boundaries.",
        "HEVCデータに有効なNALユニット境界がありません。",
    ),
    "实测 HDR10 内容亮度超出可表达范围。": (
        "Measured HDR10 content light levels exceed the representable range.",
        "実測したHDR10コンテンツの輝度が表現可能な範囲を超えています。",
    ),
    "HEVC NAL 头无效。": (
        "Invalid HEVC NAL header.",
        "HEVC NALヘッダーが無効です。",
    ),
    "缺少可写入内容亮度的视频画面。": (
        "There is no coded picture to carry the content light metadata.",
        "コンテンツ輝度情報を書き込める符号化画像がありません。",
    ),
    "原片含杜比元数据或额外编码层，不能作为单层 HDR10 制作。": (
        "The source contains Dolby Vision metadata or additional coded layers and cannot be processed as single-layer HDR10.",
        "素材にDolby Visionメタデータまたは追加の符号化レイヤーが含まれるため、シングルレイヤーHDR10として処理できません。",
    ),
    "原片含 HDR10+ 或 HDR Vivid 动态元数据，不能作为静态 HDR10 制作。": (
        "The source contains HDR10+ or HDR Vivid dynamic metadata and cannot be processed as static HDR10.",
        "素材にHDR10+またはHDR Vividの動的メタデータが含まれるため、静的HDR10として処理できません。",
    ),
    "原片的 HDR10 母版或内容亮度信息不完整，或在片内发生变化。": (
        "The source HDR10 mastering metadata or content light metadata is incomplete or changes within the film.",
        "素材のHDR10マスタリング情報またはコンテンツ輝度情報が不完全か、映像の途中で変化しています。",
    ),
    "原始 HEVC 缺少 ST 2086 母版显示亮度，无法沿用母版信息。": (
        "The original HEVC lacks ST 2086 mastering display luminance, so its mastering metadata cannot be reused.",
        "元のHEVCにST 2086のマスタリングディスプレイ輝度がなく、マスタリング情報を引き継げません。",
    ),
    "母版显示亮度超出可表达范围。": (
        "Mastering display luminance exceeds the representable range.",
        "マスタリングディスプレイの輝度が表現可能な範囲を超えています。",
    ),
    "母版峰值无法表示为整数尼特。": (
        "The mastering peak luminance cannot be expressed in whole nits.",
        "マスタリングのピーク輝度を整数のnitで表現できません。",
    ),
    "内容亮度分析的画面顺序或统计字段不完整。": (
        "The frame sequence or statistics fields are incomplete in the content light analysis.",
        "コンテンツ輝度解析のフレーム順序または統計項目が不完全です。",
    ),
    "内容亮度分析返回了无效的像素数量或亮度。": (
        "Content light analysis returned an invalid pixel count or luminance value.",
        "コンテンツ輝度解析で無効な画素数または輝度値が返されました。",
    ),
    "内容亮度分析的解码帧数与输出视频不一致。": (
        "The decoded frame count during content light analysis differs from the output video.",
        "コンテンツ輝度解析のデコードフレーム数が出力映像と一致しません。",
    ),
    "内容亮度测量需要有效的 4:2:0 画布和解码帧数。": (
        "Content light measurement requires valid 4:2:0 frame dimensions and a decoded frame count.",
        "コンテンツ輝度測定には有効な4:2:0フレーム寸法とデコードフレーム数が必要です。",
    ),
    "内容亮度测量所需的 HEVC 10 位 BT.2020/PQ 编码、范围或实际画布不符。": (
        "The encoding, signal range or dimensions do not meet the 10-bit HEVC BT.2020/PQ requirements for content light measurement.",
        "コーデック、信号レンジ、または実際の寸法が、コンテンツ輝度測定に必要な10ビットHEVC BT.2020/PQの要件と一致しません。",
    ),
    "未知的 HEVC 色度采样位置。": (
        "Unknown HEVC chroma sample location.",
        "HEVCのクロマサンプル位置を識別できません。",
    ),
    "HDR10 内容亮度测量失败。": (
        "HDR10 content light measurement failed.",
        "HDR10コンテンツの輝度測定に失敗しました。",
    ),
    # Profile 5 and Profile 7 sources, previews and subtitle packets
    "试片目标文件已存在。": (
        "The preview output file already exists.",
        "プレビューの出力先に同名のファイルがあります。",
    ),
    "试片基础层与增强层逐帧时间不匹配。": (
        "Preview frame timestamps differ between the base and enhancement layers.",
        "プレビューのベースレイヤーと拡張レイヤーで、各フレームの時刻が一致しません。",
    ),
    "无法读取双轨试片的帧率。": (
        "Could not read the frame rate of the dual-track preview.",
        "デュアルトラックのプレビューのフレームレートを読み取れません。",
    ),
    "Profile 5 转换要求具备单一 Dolby 配置记录的 HEVC 10-bit 4:2:0 片源。": (
        "Profile 5 conversion requires 10-bit 4:2:0 HEVC input with a single Dolby Vision configuration record.",
        "Profile 5変換には、Dolby Vision構成レコードが1つの10ビット4:2:0 HEVC素材が必要です。",
    ),
    "片源不符合单层 Dolby Vision Profile 5。": (
        "The source does not meet the single-layer Dolby Vision Profile 5 requirements.",
        "入力がシングルレイヤーDolby Vision Profile 5の要件を満たしていません。",
    ),
    "P5 画面尺寸无效。": (
        "Invalid Profile 5 picture dimensions.",
        "Profile 5の画面寸法が無効です。",
    ),
    "缺少 FSRCNNX 神经网络资源。": (
        "FSRCNNX neural network resources are missing.",
        "FSRCNNXのニューラルネットワーク用リソースがありません。",
    ),
    "Profile 7 基础层必须为有限范围的 BT.2020/PQ HEVC Main 10。": (
        "A Profile 7 base layer must use limited-range BT.2020/PQ HEVC Main 10.",
        "Profile 7のベースレイヤーにはリミテッドレンジのBT.2020/PQ HEVC Main 10が必要です。",
    ),
    "Profile 7 的基础层、增强层或 RPU 配置记录不完整。": (
        "The Profile 7 base layer, enhancement layer or RPU configuration record is incomplete.",
        "Profile 7のベースレイヤー、拡張レイヤー、またはRPU構成レコードが不完全です。",
    ),
    "不支持此 Profile 7 基础层兼容类型。": (
        "Unsupported Profile 7 base-layer compatibility type.",
        "このProfile 7のベースレイヤー互換タイプには対応していません。",
    ),
    "MP4 box 头不完整。": (
        "The MP4 box header is incomplete.",
        "MP4ボックスのヘッダーが不完全です。",
    ),
    "MP4 box 长度不合法。": (
        "Invalid MP4 box length.",
        "MP4ボックスの長さが無効です。",
    ),
    "Profile 7 全部 RPU 的类型或帧数与视频不一致。": (
        "The Profile 7 RPU types or frame count do not match the video.",
        "Profile 7のRPUの種類またはフレーム数が映像と一致しません。",
    ),
    "无法验证独立的 10 位 HEVC 基础层或增强层。": (
        "Could not verify a separate 10-bit HEVC base or enhancement layer.",
        "独立した10ビットHEVCのベースレイヤーまたは拡張レイヤーを検証できません。",
    ),
    "基础层与增强层尺寸或逐帧数量不匹配。": (
        "The dimensions or frame counts differ between the base and enhancement layers.",
        "ベースレイヤーと拡張レイヤーの寸法またはフレーム数が一致しません。",
    ),
    "双层 HEVC 流式拆分失败。": (
        "Streaming separation of the two HEVC layers failed.",
        "デュアルレイヤーHEVCのストリーム分離に失敗しました。",
    ),
    "双层 HEVC 数据缺少有效的 NAL 边界。": (
        "The dual-layer HEVC data lacks valid NAL unit boundaries.",
        "デュアルレイヤーHEVCデータに有効なNALユニット境界がありません。",
    ),
    "增强层或基础层缺少可验证的视频图像。": (
        "The enhancement layer or base layer contains no verifiable coded pictures.",
        "拡張レイヤーまたはベースレイヤーに検証できる符号化画像がありません。",
    ),
    "PGS 字幕数据包缺少有效的时间戳或内容校验值。": (
        "A PGS subtitle packet is missing a valid timestamp or payload checksum.",
        "PGS字幕パケットに有効なタイムスタンプまたは内容のチェックサムがありません。",
    ),
    "PGS 字幕的内容、显示时间、时长或顺序发生了变化。": (
        "PGS subtitle content, presentation times, durations or order changed.",
        "PGS字幕の内容、表示時刻、表示時間、または順序が変わりました。",
    ),
    # Progress units
    "估算中…": (
        "Estimating…",
        "推定中…",
    ),
    "约 {time}": (
        "≈ {time}",
        "約 {time}",
    ),
    "{done:,.0f} / {total:,.0f} 帧": (
        "{done:,.0f} / {total:,.0f} frames",
        "{done:,.0f} / {total:,.0f} フレーム",
    ),
    "{rate}/秒": (
        "{rate}/s",
        "{rate}/秒",
    ),
    "{rate:.1f} 帧/秒": (
        "{rate:.1f} fps",
        "{rate:.1f} フレーム/秒",
    ),
    "{rate:.2f}%/秒": (
        "{rate:.2f}%/s",
        "{rate:.2f}%/秒",
    ),
}


def _message_patterns():
    """Match engine-generated values embedded in a single known diagnostic line."""
    fields = {"code": r"-?\d+", "level": r"\d+", "time": r"\d+:\d{2}:\d{2}",
              "tool": r"[^\\/\r\n]+?\.exe", "rate": r"[\d.,]+(?: [KMGT]?i?B)?"}
    patterns = []
    for source, translations in ENGINE_MESSAGES.items():
        if "{" not in source:
            continue
        expression = []
        for literal, name, spec, _ in Formatter().parse(source):
            expression.append(re.escape(literal))
            if name is not None:
                value = r"[\d,.]+" if spec else fields.get(name, r".+?")
                expression.append(f"(?P<{name}>{value})")
        # Captured values already have the requested precision and grouping.
        rendered = tuple("".join(lit + ("{" + field + "}" if field else "")
                                 for lit, field, _, _ in Formatter().parse(template))
                         for template in translations)
        patterns.append((re.compile("".join(expression)), rendered))
    return tuple(patterns)


_PATTERNS = _message_patterns()
_COUNT = re.compile(r"(?P<done>[\d,]+)(?P<divider>\s*/\s*)(?P<total>[\d,]+) 帧(?P<fps> · [\d.]+ fps)?")
_OTHER_UNITS = re.compile(r"[\d,.]+(?: / [\d,.]+ MiB|%)")


def _translate_line(line, index):
    options = ENGINE_MESSAGES.get(line)
    if options:
        return options[index]
    for pattern, templates in _PATTERNS:
        match = pattern.fullmatch(line)
        if match:
            return templates[index].format_map(match.groupdict())

    # Legacy two-argument progress callbacks receive the counters in the label.
    stage, sep, counts = line.partition(" · ")
    if sep and stage in ENGINE_MESSAGES:
        count = _COUNT.fullmatch(counts)
        if count:
            unit = ("frames", "フレーム")[index]
            return (ENGINE_MESSAGES[stage][index] + sep + count["done"] + count["divider"]
                    + count["total"] + " " + unit + (count["fps"] or ""))
        if _OTHER_UNITS.fullmatch(counts):
            return ENGINE_MESSAGES[stage][index] + sep + counts

    suffix = " | 异步帧传输"
    if line.startswith("NVIDIA RTX Video ") and line.endswith(suffix):
        return line[:-len(suffix)] + " | " + ENGINE_MESSAGES["异步帧传输"][index]

    # Python adds an errno prefix to application-level disk errors.
    system_error = re.fullmatch(r"(\[(?:Errno|WinError) -?\d+\] )(.+)", line)
    if system_error and system_error[2] in ENGINE_MESSAGES:
        return system_error[1] + ENGINE_MESSAGES[system_error[2]][index]
    return line


def engine_localize(text, lang):
    """Translate a known diagnostic or stage, preserving appended native tool output."""
    if lang not in ("en", "ja") or not text:
        return text
    first, newline, detail = text.partition("\n")
    line = first.removesuffix("\r")
    localized = _translate_line(line, 0 if lang == "en" else 1)
    return localized + first[len(line):] + newline + detail
