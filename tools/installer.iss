#ifndef StageDir
  #error Build using tools/build.ps1
#endif
#define StudioVersion "1.8.1"
[Setup]
AppId={{E839665F-01D2-43AC-BA81-5C5B30A03C17}
AppName=LumaFrame Studio
AppVersion={#StudioVersion}
VersionInfoVersion={#StudioVersion}.0
AppPublisher=lingjieheti
AppPublisherURL=https://github.com/lingjieheti/LumaFrame-Studio
AppSupportURL=https://github.com/lingjieheti/LumaFrame-Studio/issues
DefaultDirName={localappdata}\Programs\LumaFrame Studio
DefaultGroupName=LumaFrame Studio
PrivilegesRequired=lowest
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
UninstallDisplayIcon={app}\LumaFrameStudio.exe
SetupIconFile={#StageDir}\assets\icon.ico
OutputBaseFilename=LumaFrame-Studio-{#StudioVersion}-Windows-x64
Compression=lzma2/fast
SolidCompression=yes
WizardStyle=modern
DisableProgramGroupPage=yes
Uninstallable=not IsPortable
CreateUninstallRegKey=not IsPortable
[Languages]
Name: "zhCN"; MessagesFile: "compiler:Default.isl,ChineseSimplified.isl"
Name: "en"; MessagesFile: "compiler:Default.isl"
Name: "ja"; MessagesFile: "compiler:Languages\Japanese.isl"
[CustomMessages]
zhCN.OpenApp=打开 LumaFrame Studio
en.OpenApp=Open LumaFrame Studio
ja.OpenApp=LumaFrame Studio を開く
zhCN.MediaTitle=视频处理组件
en.MediaTitle=Media tools
ja.MediaTitle=動画処理ツール
zhCN.MediaText=从上游获取并校验 FFmpeg 8.1
en.MediaText=Getting and verifying the official FFmpeg 8.1 package.
ja.MediaText=公式の FFmpeg 8.1 パッケージを取得し、検証します。
zhCN.BadArchive=FFmpeg 压缩包校验失败。
en.BadArchive=The FFmpeg archive checksum does not match.
ja.BadArchive=FFmpeg アーカイブのチェックサムが一致しません。
zhCN.ExtractFailed=无法解压 FFmpeg。请检查 Windows tar.exe 是否支持 7-Zip。
en.ExtractFailed=Unable to extract FFmpeg. Windows tar.exe with 7-Zip support is required.
ja.ExtractFailed=FFmpeg を展開できません。7-Zip 対応の Windows tar.exe が必要です。
zhCN.BadTools=FFmpeg 程序文件校验失败。
en.BadTools=Media executable checksum verification failed.
ja.BadTools=動画処理ツールのチェックサム検証に失敗しました。
zhCN.OfflineHint=离线安装时，请将官方 %1 放到安装程序旁边，然后重试。
en.OfflineHint=For offline installation, put the official %1 next to Setup and retry.
ja.OfflineHint=オフラインでインストールするには、公式の %1 をインストーラーと同じフォルダーに置いて、もう一度お試しください。
[Files]
Source: "{#StageDir}\*"; Excludes: "ffmpeg.exe,ffprobe.exe"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs
Source: "{code:MediaFile|ffmpeg.exe}"; DestDir: "{app}\bin"; DestName: "ffmpeg.exe"; ExternalSize: 223360000; Flags: external ignoreversion
Source: "{code:MediaFile|ffprobe.exe}"; DestDir: "{app}\bin"; DestName: "ffprobe.exe"; ExternalSize: 223153664; Flags: external ignoreversion
[Icons]
Name: "{group}\LumaFrame Studio"; Filename: "{app}\LumaFrameStudio.exe"; Check: not IsPortable
[Run]
Filename: "{app}\LumaFrameStudio.exe"; Description: "{cm:OpenApp}"; Flags: nowait postinstall skipifsilent
[Code]
const
  ArchiveName = 'ffmpeg-8.1-full_build.7z';
  ArchiveURL = 'https://github.com/GyanD/codexffmpeg/releases/download/8.1/ffmpeg-8.1-full_build.7z';
  ArchiveHash = '4dcbe10fa72bffa3e976d09cf3335f53a2716a8041af7a047609898fe6019069';
  EncoderHash = 'd1e2a156261ecc675081943197a85f08f2868784a0af499171ede89353edad31';
  ProbeHash = '70872c3ffbc43d0b2c570f9837f54d6e9a832f4ca25463e9735b6a3ec0621478';
var
  MediaFolder: String;
  DownloadPage: TDownloadWizardPage;

function IsPortable: Boolean;
begin
  Result := ExpandConstant('{param:portable|0}') = '1';
end;

procedure InitializeWizard();
begin
  DownloadPage := CreateDownloadPage(CustomMessage('MediaTitle'), CustomMessage('MediaText'), nil);
  DownloadPage.ShowBaseNameInsteadOfUrl := True;
end;

function MediaFile(Param: String): String;
begin
  Result := AddBackslash(MediaFolder) + Param;
end;

function Matches(const Name, Hash: String): Boolean;
begin
  if FileExists(Name) then Result := CompareText(GetSHA256OfFile(Name), Hash) = 0
  else Result := False;
end;

function MediaReady: Boolean;
begin
  Result := Matches(MediaFile('ffmpeg.exe'), EncoderHash) and Matches(MediaFile('ffprobe.exe'), ProbeHash);
end;

function PrepareToInstall(var NeedsRestart: Boolean): String;
var
  Archive, Stage, Args, InstalledBin: String;
  ExitCode: Integer;
begin
  Result := '';
  try
    Stage := ExpandConstant('{tmp}\ffmpeg-runtime');
    InstalledBin := ExpandConstant('{app}\bin');
    MediaFolder := InstalledBin;
    if MediaReady then begin
      { Stage the exact installed files so Setup never copies a file over itself. }
      if ForceDirectories(Stage) and
         CopyFile(InstalledBin + '\ffmpeg.exe', Stage + '\ffmpeg.exe', False) and
         CopyFile(InstalledBin + '\ffprobe.exe', Stage + '\ffprobe.exe', False) then begin
        MediaFolder := Stage;
        if MediaReady then begin
          Log('Reusing verified FFmpeg from the existing installation.');
          exit;
        end;
      end;
    end;
    Archive := ExpandConstant('{src}\') + ArchiveName;
    if not FileExists(Archive) then begin
      MediaFolder := ExtractFileDir(FileSearch('ffmpeg.exe', GetEnv('PATH')));
      if (MediaFolder <> '') and MediaReady then exit;
      DownloadPage.Clear;
      DownloadPage.Add(ArchiveURL, ArchiveName, ArchiveHash);
      DownloadPage.Show;
      try
        DownloadPage.Download;
      finally
        DownloadPage.Hide;
      end;
      Archive := ExpandConstant('{tmp}\') + ArchiveName;
    end;
    if not Matches(Archive, ArchiveHash) then RaiseException(CustomMessage('BadArchive'));
    ForceDirectories(Stage);
    Args := '-xf "' + Archive + '" -C "' + Stage + '" ffmpeg-8.1-full_build/bin/ffmpeg.exe ffmpeg-8.1-full_build/bin/ffprobe.exe';
    if not Exec(ExpandConstant('{sys}\tar.exe'), Args, '', SW_HIDE, ewWaitUntilTerminated, ExitCode) or (ExitCode <> 0) then
      RaiseException(CustomMessage('ExtractFailed'));
    MediaFolder := Stage + '\ffmpeg-8.1-full_build\bin';
    if not MediaReady then RaiseException(CustomMessage('BadTools'));
  except
    Result := GetExceptionMessage + #13#10 + FmtMessage(CustomMessage('OfflineHint'), [ArchiveName]);
  end;
end;
