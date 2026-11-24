#ifndef StageDir
  #error Build using tools/build.ps1
#endif
[Setup]
AppId={{E839665F-01D2-43AC-BA81-5C5B30A03C17}
AppName=LumaFrame Studio
AppVersion=1.3.0
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
OutputBaseFilename=LumaFrame-Studio-1.3.0-Windows-x64
Compression=lzma2/fast
SolidCompression=yes
WizardStyle=modern
DisableProgramGroupPage=yes
Uninstallable=not IsPortable
CreateUninstallRegKey=not IsPortable
[Languages]
Name: "zhCN"; MessagesFile: "compiler:Default.isl,ChineseSimplified.isl"
Name: "en"; MessagesFile: "compiler:Default.isl"
[CustomMessages]
zhCN.OpenApp=打开 LumaFrame Studio
en.OpenApp=Open LumaFrame Studio
zhCN.MediaTitle=视频处理组件
en.MediaTitle=Media tools
zhCN.MediaText=从上游获取并校验 FFmpeg 8.1
en.MediaText=Getting and verifying the official FFmpeg 8.1 package.
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
    if not Matches(Archive, ArchiveHash) then RaiseException('The FFmpeg archive checksum does not match.');
    ForceDirectories(Stage);
    Args := '-xf "' + Archive + '" -C "' + Stage + '" ffmpeg-8.1-full_build/bin/ffmpeg.exe ffmpeg-8.1-full_build/bin/ffprobe.exe';
    if not Exec(ExpandConstant('{sys}\tar.exe'), Args, '', SW_HIDE, ewWaitUntilTerminated, ExitCode) or (ExitCode <> 0) then
      RaiseException('Unable to extract FFmpeg. Windows tar.exe with 7-Zip support is required.');
    MediaFolder := Stage + '\ffmpeg-8.1-full_build\bin';
    if not MediaReady then RaiseException('Media executable checksum verification failed.');
  except
    Result := GetExceptionMessage + #13#10 + 'For offline installation, put the official ' + ArchiveName + ' next to Setup and retry.';
  end;
end;
