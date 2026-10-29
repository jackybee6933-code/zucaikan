#define AppName "足彩胜负彩分析系统"
#ifndef AppVersion
  #define AppVersion "1.3.21"
#endif

[Setup]
AppId={{F2C5E0A9-7A1B-4A9D-8F62-6D0B5F15B6C1}
AppName={#AppName}
AppVersion={#AppVersion}
AppPublisher=足彩分析工具
DefaultDirName={localappdata}\FootballAnalysis
DefaultGroupName={#AppName}
UninstallDisplayIcon={app}\football_analysis.exe
OutputDir=dist
OutputBaseFilename=football_analysis_setup
Compression=lzma
SolidCompression=yes
PrivilegesRequired=lowest
ArchitecturesInstallIn64BitMode=x64
CloseApplications=yes
RestartApplications=yes
DisableProgramGroupPage=yes
DisableDirPage=no
Uninstallable=yes
DefaultDialogFontName=Microsoft YaHei

[Languages]
Name: "chinesesimp"; MessagesFile: "installer\ChineseSimplified.isl"

[Files]
Source: "dist\football_analysis.exe"; DestDir: "{app}"; Flags: ignoreversion
Source: "dist\updater.exe"; DestDir: "{app}"; DestName: "updater.new.exe"; Flags: ignoreversion; Check: FileExists(ExpandConstant('{app}\updater.exe'))
Source: "dist\updater.exe"; DestDir: "{app}"; Flags: ignoreversion; Check: not FileExists(ExpandConstant('{app}\updater.exe'))
Source: "tools\scrape_odds_pages.py"; DestDir: "{app}\tools"; Flags: ignoreversion

[Dirs]
Name: "{app}\cache"; Flags: uninsneveruninstall
Name: "{app}\data"; Flags: uninsneveruninstall
Name: "{app}\reports"; Flags: uninsneveruninstall

[Icons]
Name: "{group}\足彩胜负彩分析系统"; Filename: "{app}\football_analysis.exe"
Name: "{autodesktop}\足彩胜负彩分析系统"; Filename: "{app}\football_analysis.exe"; Tasks: desktopicon

[Tasks]
Name: "desktopicon"; Description: "创建桌面快捷方式"; GroupDescription: "快捷方式："; Flags: unchecked

[Run]
Filename: "{app}\football_analysis.exe"; Description: "启动足彩胜负彩分析系统"; Flags: nowait postinstall skipifsilent
