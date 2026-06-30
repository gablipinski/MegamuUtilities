; ============================================================================
; Guardtower - Inno Setup 6 Installer Script
; ============================================================================
; Build command (from project root):
;   "C:\Program Files (x86)\Inno Setup 6\ISCC.exe" installer\setup.iss
;
; Prerequisites:
;   - dist\Guardtower.exe must exist (run scripts\build_exe.ps1 first)
;   - Inno Setup 6 installed: https://jrsoftware.org/isdl.php
; ============================================================================

#ifndef MyAppName
#define MyAppName      "Guardtower"
#endif
#ifndef MyAppVersion
#define MyAppVersion   "1.1.1"
#endif
#ifndef MyAppPublisher
#define MyAppPublisher "Megamu Utilities"
#endif
#define MyAppExeName    "Guardtower.exe"
#define SourcePath      ".."

[Setup]
AppId={{2E5C7C5D-8C75-4A0A-8F5E-8B4F0B6E1C10}}
AppName={#MyAppName}
AppVersion={#MyAppVersion}
AppVerName={#MyAppName} {#MyAppVersion}
AppPublisher={#MyAppPublisher}
DefaultDirName={autopf}\{#MyAppName}
DefaultGroupName={#MyAppName}
DisableProgramGroupPage=yes
OutputDir={#SourcePath}\installer_output
OutputBaseFilename=Guardtower_Setup_{#MyAppVersion}
Compression=lzma2/ultra64
SolidCompression=yes
WizardStyle=modern
PrivilegesRequired=admin
ArchitecturesInstallIn64BitMode=x64compatible
UninstallDisplayIcon={app}\{#MyAppExeName}
SetupIconFile={#SourcePath}\icons\guardtower.ico

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon"; Description: "{cm:CreateDesktopIcon}"; \
    GroupDescription: "{cm:AdditionalIcons}"; Flags: unchecked

[Files]
Source: "{#SourcePath}\dist\{#MyAppExeName}"; \
    DestDir: "{app}"; Flags: ignoreversion

Source: "{#SourcePath}\dist\configs\*"; \
    DestDir: "{app}\configs"; Flags: ignoreversion recursesubdirs createallsubdirs

Source: "{#SourcePath}\dist\icons\*"; \
    DestDir: "{app}\icons"; Flags: ignoreversion recursesubdirs createallsubdirs

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
procedure CurStepChanged(CurStep: TSetupStep);
begin
  if CurStep = ssPostInstall then
  begin
    MsgBox(
      '{#MyAppName} has been installed successfully.' + #13#10 + #13#10 +
      'IMPORTANT - License required:' + #13#10 +
      'This software requires a personal license.dat file to run.' + #13#10 + #13#10 +
      'When you first launch the application, it will display your' + #13#10 +
      'unique Machine ID. Send that ID to your software distributor' + #13#10 +
      'to receive your license.dat file.' + #13#10 + #13#10 +
      'Place license.dat here:' + #13#10 +
      '%APPDATA%\Guardtower\license.dat',
      mbInformation, MB_OK
    );
  end;
end;











