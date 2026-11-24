param([string]$Python = "python", [string]$InnoCompiler = "", [string]$Output = "")
$ErrorActionPreference = "Stop"
$sourceRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
if (-not $Output) { $Output = Join-Path $sourceRoot "build-output" }
$Output = [IO.Path]::GetFullPath($Output)
$dist = Join-Path $Output "dist"
$stage = Join-Path $dist "LumaFrameStudio"
Push-Location -LiteralPath $sourceRoot
try {
    foreach ($item in @("bin/ffmpeg.exe","bin/ffprobe.exe","bin/dovi_tool.exe","bin/mkvtoolnix/mkvmerge.exe","bin/nvenc/NVEncC64.exe")) {
        if (-not (Test-Path -LiteralPath $item)) { throw "Missing $item. Run python tools/fetch_tools.py first." }
    }
    $felDir = Join-Path $sourceRoot "bin/fel"
    $felManifest = Get-Content -LiteralPath (Join-Path $felDir "runtime-sha256.json") -Raw | ConvertFrom-Json
    foreach ($name in @("python.exe","python312.dll","python312.zip","vapoursynth.dll","vapoursynth.pyd","ffms2.dll","felbaker.dll")) {
        if (-not $felManifest.PSObject.Properties[$name]) { throw "Incomplete FEL runtime manifest: $name. Run tools/fetch_fel.py." }
    }
    foreach ($entry in $felManifest.PSObject.Properties) {
        if ([IO.Path]::GetFileName($entry.Name) -ne $entry.Name) { throw "Unexpected FEL runtime filename." }
        $file = Join-Path $felDir $entry.Name
        if ((Get-FileHash -LiteralPath $file -Algorithm SHA256).Hash -ne $entry.Value) { throw "FEL runtime checksum mismatch: $file" }
    }
    $params = @("-m","PyInstaller","--noconfirm","--onedir","--windowed","--noupx",
        "--contents-directory",".","--name","LumaFrameStudio","--icon",(Join-Path $sourceRoot "assets/icon.ico"),
        "--distpath",$dist,"--workpath",(Join-Path $Output "cache"),"--specpath",$Output,
        "--hidden-import","ctypes.util","--hidden-import","importlib.resources",
        "--hidden-import","inspect","--hidden-import","numbers","--hidden-import","dataclasses",
        "--hidden-import","typing","--hidden-import","platform","--hidden-import","glob","main.py")
    & $Python @params
    if ($LASTEXITCODE) { throw "PyInstaller failed ($LASTEXITCODE)" }
    foreach ($dir in @("assets","shaders","bin","licenses","docs")) {
        Copy-Item -LiteralPath (Join-Path $sourceRoot $dir) -Destination $stage -Recurse -Force
    }
    foreach ($name in @("README.md","THIRD_PARTY.md","LICENSE")) {
        if (Test-Path -LiteralPath $name) { Copy-Item -LiteralPath $name -Destination $stage -Force }
    }
    if ($InnoCompiler) {
        & $InnoCompiler ("/DStageDir="+$stage) ("/O"+$Output) (Join-Path $PSScriptRoot "installer.iss")
        if ($LASTEXITCODE) { throw "Inno Setup failed ($LASTEXITCODE)" }
    }
} finally { Pop-Location }
