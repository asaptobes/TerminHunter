; Termin Hunter — Windows installer script
; ------------------------------------------------------------------
; Build tool: Inno Setup Compiler (free) — https://jrsoftware.org/isdl.php
;
; This does NOT build the app itself — it packages the .exe that
; PyInstaller already produced into a proper Setup.exe with a wizard,
; Start Menu shortcut, optional desktop shortcut, and an uninstaller
; entry in "Apps & Features". Run the PyInstaller build first (see
; README.md), THEN compile this script.
;
; To compile:
;   1. Install Inno Setup (jrsoftware.org/isdl.php).
;   2. Open this file in the Inno Setup Compiler (or run:
;      "C:\Program Files (x86)\Inno Setup 6\ISCC.exe" installer\windows_installer.iss )
;   3. The result lands in installer\output\TerminHunter-Setup.exe —
;      THAT is the single file you hand to people.
; ------------------------------------------------------------------

#define MyAppName "Termin Hunter"
#define MyAppVersion "1.0.0"
#define MyAppPublisher "Toby Lee"
#define MyAppExeName "TerminHunter.exe"

[Setup]
AppId={{7E2B7C2A-4E39-4B1D-9E2F-TERMINHUNTER1}}
AppName={#MyAppName}
AppVersion={#MyAppVersion}
AppPublisher={#MyAppPublisher}
; Installs to the current user's own AppData — no admin rights / UAC
; prompt required. Change to {autopf} + PrivilegesRequired=admin below
; if you'd rather install for all users under Program Files.
DefaultDirName={localappdata}\{#MyAppName}
DefaultGroupName={#MyAppName}
DisableProgramGroupPage=yes
PrivilegesRequired=lowest
OutputDir=output
OutputBaseFilename=TerminHunter-Setup
Compression=lzma2
SolidCompression=yes
WizardStyle=modern
UninstallDisplayIcon={app}\{#MyAppExeName}
SetupIconFile=..\assets\icon.ico

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon"; Description: "Create a &desktop shortcut"; GroupDescription: "Additional shortcuts:"

[Files]
; Expects the PyInstaller build to already exist at dist\TerminHunter.exe
; (one level up from this installer\ folder — run ISCC from the
; termin-hunter project root, or adjust the relative path below).
Source: "..\dist\{#MyAppExeName}"; DestDir: "{app}"; Flags: ignoreversion

[Icons]
Name: "{group}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"
Name: "{group}\Uninstall {#MyAppName}"; Filename: "{uninstallexe}"
Name: "{autodesktop}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"; Tasks: desktopicon

[Run]
Filename: "{app}\{#MyAppExeName}"; Description: "Launch {#MyAppName}"; Flags: nowait postinstall skipifsilent

[Messages]
WelcomeLabel2=This will install {#MyAppName} on your computer.%n%nTermin Hunter drives your existing Google Chrome browser rather than bundling its own — make sure Chrome is installed before running it.%n%nClick Next to continue.
