; ============================================================================
; Watchtower — Inno Setup 6 Installer Script
; ============================================================================
; Build command (from project root):
;   "C:\Program Files (x86)\Inno Setup 6\ISCC.exe" installer\setup.iss
;
; Prerequisites:
;   - dist\Watchtower.exe must exist (run scripts\build_exe.ps1 first)
;   - Inno Setup 6 installed: https://jrsoftware.org/isdl.php
; ============================================================================

#ifndef MyAppName
#define MyAppName      "Watchtower"
#endif
#ifndef MyAppVersion
#define MyAppVersion   "1.1.1"
#endif
#ifndef MyAppPublisher
#define MyAppPublisher "Megamu Utilities"
#endif
#define MyAppExeName   "Watchtower.exe"
#define SourcePath     ".."

[Setup]
AppId={{7F3A2B1C-E6D5-4F8A-9B0C-1D2E3F4A5B6C}
AppName={#MyAppName}
AppVersion={#MyAppVersion}
AppVerName={#MyAppName} {#MyAppVersion}
AppPublisher={#MyAppPublisher}
DefaultDirName={autopf}\{#MyAppName}
DefaultGroupName={#MyAppName}
DisableProgramGroupPage=yes
OutputDir={#SourcePath}\installer_output
OutputBaseFilename=Watchtower_Setup_{#MyAppVersion}
Compression=lzma2/ultra64
SolidCompression=yes
WizardStyle=modern
PrivilegesRequired=admin
ArchitecturesInstallIn64BitMode=x64compatible
UninstallDisplayIcon={app}\{#MyAppExeName}
SetupIconFile={#SourcePath}\icons\watchtower.ico

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon"; Description: "{cm:CreateDesktopIcon}"; \
    GroupDescription: "{cm:AdditionalIcons}"; Flags: unchecked

[Files]
; Main executable — compiled by scripts\build_exe.ps1
Source: "{#SourcePath}\dist\{#MyAppExeName}"; \
    DestDir: "{app}"; Flags: ignoreversion

; NOTE: license.dat is NOT bundled. The user must obtain it from the
; distributor and place it in %APPDATA%\Watchtower\ after installation.

[Icons]
Name: "{group}\{#MyAppName}";          Filename: "{app}\{#MyAppExeName}"
Name: "{group}\Uninstall {#MyAppName}"; Filename: "{uninstallexe}"
Name: "{autodesktop}\{#MyAppName}";     Filename: "{app}\{#MyAppExeName}"; \
    Tasks: desktopicon

[Run]
Filename: "{app}\{#MyAppExeName}"; \
    Description: "Launch {#MyAppName}"; \
    Flags: nowait postinstall skipifsilent

[Code]
// Show a reminder about the license requirement after installation.
procedure CurStepChanged(CurStep: TSetupStep);
begin
  if CurStep = ssPostInstall then
  begin
    MsgBox(
      '{#MyAppName} has been installed successfully.' + #13#10 + #13#10 +
      'IMPORTANT — License required:' + #13#10 +
      'This software requires a personal license.dat file to run.' + #13#10 + #13#10 +
      'When you first launch the application, it will display your' + #13#10 +
      'unique Machine ID. Send that ID to your software distributor' + #13#10 +
      'to receive your license.dat file.' + #13#10 + #13#10 +
      'Place license.dat here:' + #13#10 +
      '%APPDATA%\Watchtower\license.dat',
      mbInformation, MB_OK
    );
  end;
end;


