; Inno Setup script for TenderPilot. Built by packaging\build.ps1:
;   ISCC tenderpilot.iss            -> installer\TenderPilot-Setup.exe       (full program + demo shortcut)
;   ISCC /DDEMO tenderpilot.iss     -> installer\TenderPilot-Demo-Setup.exe  (demo only)
; Installs per user (no administrator rights needed). User data lives in %LOCALAPPDATA%\TenderPilot[-Demo]
; and is never removed by uninstall.

#define AppVersion "1.2.0"
#ifdef DEMO
  #define AppName "TenderPilot Demo"
  #define AppIdGuid "{{6C1B7E0A-3F7D-4C38-9B67-2D0E5A9F4D21}"
  #define OutName "TenderPilot-Demo-Setup"
  #define InfoFile "after_install_demo.txt"
#else
  #define AppName "TenderPilot"
  #define AppIdGuid "{{1F4A2C9E-8B53-4E7A-A0D6-7C3E9B12F5A8}"
  #define OutName "TenderPilot-Setup"
  #define InfoFile "after_install.txt"
#endif

[Setup]
AppId={#AppIdGuid}
AppName={#AppName}
AppVersion={#AppVersion}
AppVerName={#AppName} {#AppVersion}
AppPublisher=TenderPilot
DefaultDirName={localappdata}\Programs\{#AppName}
DefaultGroupName={#AppName}
DisableProgramGroupPage=yes
PrivilegesRequired=lowest
OutputDir=..\installer
OutputBaseFilename={#OutName}
SetupIconFile=tenderpilot.ico
UninstallDisplayIcon={app}\TenderPilot.exe
Compression=lzma2/max
SolidCompression=yes
WizardStyle=modern
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
InfoAfterFile={#InfoFile}
CloseApplications=yes

[Tasks]
Name: "desktopicon"; Description: "Create a desktop shortcut"; GroupDescription: "Shortcuts:"

[Files]
Source: "dist\TenderPilot\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs
Source: "{#InfoFile}"; DestDir: "{app}"; DestName: "READ ME.txt"; Flags: ignoreversion

[Icons]
#ifdef DEMO
Name: "{group}\TenderPilot Demo"; Filename: "{app}\TenderPilot.exe"; Parameters: "--demo"; Comment: "Practice copy with SYNTHETIC data"
Name: "{group}\Read me"; Filename: "{app}\READ ME.txt"
Name: "{group}\Uninstall TenderPilot Demo"; Filename: "{uninstallexe}"
Name: "{userdesktop}\TenderPilot Demo"; Filename: "{app}\TenderPilot.exe"; Parameters: "--demo"; Tasks: desktopicon
#else
Name: "{group}\TenderPilot"; Filename: "{app}\TenderPilot.exe"; Comment: "Tender and bid preparation"
Name: "{group}\TenderPilot Demo"; Filename: "{app}\TenderPilot.exe"; Parameters: "--demo"; Comment: "Practice copy with SYNTHETIC data"
Name: "{group}\Back up TenderPilot data"; Filename: "{app}\TenderPilot.exe"; Parameters: "--backup"
Name: "{group}\Open data folder"; Filename: "{localappdata}\TenderPilot"
Name: "{group}\Read me"; Filename: "{app}\READ ME.txt"
Name: "{group}\Uninstall TenderPilot"; Filename: "{uninstallexe}"
Name: "{userdesktop}\TenderPilot"; Filename: "{app}\TenderPilot.exe"; Tasks: desktopicon
#endif

[Dirs]
#ifndef DEMO
Name: "{localappdata}\TenderPilot"; Flags: uninsneveruninstall
#endif

[Run]
#ifdef DEMO
Filename: "{app}\TenderPilot.exe"; Parameters: "--demo"; Description: "Start the demo now"; Flags: postinstall nowait skipifsilent
; Update started from the app (silent): start TenderPilot again; the browser tab that started the update reloads itself.
Filename: "{app}\TenderPilot.exe"; Parameters: "--demo --no-browser"; Flags: nowait; Check: WizardSilent
#else
Filename: "{app}\TenderPilot.exe"; Description: "Start TenderPilot now"; Flags: postinstall nowait skipifsilent
Filename: "{app}\TenderPilot.exe"; Parameters: "--no-browser {param:relaunchargs|}"; Flags: nowait; Check: WizardSilent
#endif
