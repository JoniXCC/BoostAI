; Inno Setup script for BoostAI. Built by scripts\build.ps1 after PyInstaller.
#define AppName "BoostAI"
#define AppVersion "1.0.0"

[Setup]
AppId={{6B7E4C1A-2F4D-4B7B-9C3E-B00571A10001}
AppName={#AppName}
AppVersion={#AppVersion}
AppPublisher=BoostAI contributors
DefaultDirName={localappdata}\Programs\{#AppName}
DefaultGroupName={#AppName}
; Per-user install: no administrator rights needed to install or run.
PrivilegesRequired=lowest
OutputDir=..\installer_output
OutputBaseFilename=BoostAI-Setup-{#AppVersion}
SetupIconFile=..\assets\boostai.ico
UninstallDisplayIcon={app}\BoostAI.exe
Compression=lzma2
SolidCompression=yes
WizardStyle=modern
LicenseFile=..\LICENSE

[Files]
Source: "..\dist\BoostAI\*"; DestDir: "{app}"; Flags: recursesubdirs ignoreversion

[Icons]
Name: "{group}\{#AppName}"; Filename: "{app}\BoostAI.exe"
Name: "{userdesktop}\{#AppName}"; Filename: "{app}\BoostAI.exe"; Tasks: desktopicon

[Tasks]
Name: "desktopicon"; Description: "Create a desktop shortcut"; Flags: unchecked

[Run]
Filename: "{app}\BoostAI.exe"; Description: "Launch BoostAI"; Flags: nowait postinstall skipifsilent

[UninstallRun]
; Remove BoostAI's own "start with Windows" entry if the user enabled it. User data in %LOCALAPPDATA%\BoostAI is kept.
Filename: "{sys}\reg.exe"; Parameters: "delete HKCU\Software\Microsoft\Windows\CurrentVersion\Run /v BoostAI /f"; Flags: runhidden; RunOnceId: "RemoveAutostart"
