; Inno Setup script for ED Colony Scout.
;
; Builds a self-extracting, per-user installer (no admin rights required)
; from the PyInstaller onedir output. Build order (from the repo root):
;   pip install -r requirements.txt
;   pip install -r requirements-build.txt
;   pyinstaller packaging/ed_colony_scout.spec --clean
;   iscc packaging/installer.iss
; (or just `packaging/build.ps1`, which does both steps in order.)
; The resulting installer is written to dist-installer\ and is meant to be
; attached to a GitHub Release, not committed to the repo.

; Bump this together with packaging/version_info.txt's filevers/prodvers/
; FileVersion/ProductVersion and gui/config.py's APP_VERSION when cutting a
; new release.
#define MyAppName "ED Colony Scout"
#define MyAppVersion "1.0.0"
#define MyAppExeName "ED Colony Scout.exe"

[Setup]
; Fresh GUID for this app -- never reuse another app's AppId.
AppId={{16BC3601-E6B7-445E-95D9-7ACABB6D24BB}
AppName={#MyAppName}
AppVersion={#MyAppVersion}
DefaultDirName={localappdata}\Programs\EDColonyScout
DefaultGroupName={#MyAppName}
PrivilegesRequired=lowest
DisableProgramGroupPage=yes
CloseApplications=yes
RestartApplications=no
OutputDir=..\dist-installer
OutputBaseFilename=ED-Colony-Scout-Setup-{#MyAppVersion}
UninstallDisplayIcon={app}\{#MyAppExeName}
SetupIconFile=icon.ico
Compression=lzma2
SolidCompression=yes
ArchitecturesInstallIn64BitMode=x64compatible

; Wipe the PyInstaller bundle directory before installing the new one, so an
; upgrade can't accumulate files a prior release shipped but this one no
; longer does (e.g. after a Python/PyInstaller version bump). User data
; lives entirely outside {app} (%LOCALAPPDATA%\EDColonyScout\out and
; \sector_library, %APPDATA%\EDColonyScout\config.json -- see
; gui/config.py), so this never touches it.
[InstallDelete]
Type: filesandordirs; Name: "{app}\_internal"

[Files]
Source: "..\dist\ED Colony Scout\*"; DestDir: "{app}"; Flags: recursesubdirs ignoreversion

[Icons]
Name: "{group}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"
Name: "{userdesktop}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"

[Run]
Filename: "{app}\{#MyAppExeName}"; Description: "Launch {#MyAppName}"; Flags: nowait postinstall skipifsilent
