#ifndef SourceDir
  #define SourceDir "..\..\dist\GISDesktop"
#endif
#ifndef AppVersion
  #define AppVersion "0.1.0"
#endif

[Setup]
AppId={{0F01A558-9B5F-4B2B-A21E-902AB32722FD}
AppName=GIS桌面通用平台
AppVersion={#AppVersion}
AppPublisher=GISPlatform
DefaultDirName={localappdata}\Programs\GISDesktopPlatform
DefaultGroupName=GIS桌面通用平台
DisableProgramGroupPage=yes
PrivilegesRequired=lowest
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
OutputDir=..\..\dist\installer
OutputBaseFilename=GISDesktop-Setup-{#AppVersion}
Compression=lzma2/ultra64
SolidCompression=yes
WizardStyle=modern
UninstallDisplayIcon={app}\GISDesktop.exe
VersionInfoVersion={#AppVersion}.0
VersionInfoCompany=GISPlatform
VersionInfoDescription=GIS桌面通用平台安装程序
VersionInfoProductName=GIS桌面通用平台
VersionInfoProductVersion={#AppVersion}
CloseApplications=yes
RestartApplications=no

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon"; Description: "创建桌面快捷方式"; GroupDescription: "附加快捷方式："; Flags: unchecked

[Files]
Source: "{#SourceDir}\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{autoprograms}\GIS桌面通用平台"; Filename: "{app}\GISDesktop.exe"; WorkingDir: "{app}"
Name: "{autodesktop}\GIS桌面通用平台"; Filename: "{app}\GISDesktop.exe"; WorkingDir: "{app}"; Tasks: desktopicon

[Run]
Filename: "{app}\GISDesktop.exe"; Description: "启动 GIS桌面通用平台"; Flags: nowait postinstall skipifsilent
