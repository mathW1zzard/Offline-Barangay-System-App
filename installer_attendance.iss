; Inno Setup script for the ATTENDANCE EDITION (Barangay Queuing and Attendance Management System)
; Put this file in the project root (next to build_attendance.bat), run build_attendance.bat first,
; then open this file in Inno Setup and press Compile (Ctrl+F9).
; Output: installer_output\Barangay_Attendance_Setup.exe
; It is a separate app from the Barangay System: its own AppId, program folder and data folder, so
; installing or removing one never touches the other.

#define MyAppName "Barangay Queuing and Attendance Management System"
; Keep MyAppVersion the same as APP_VERSION in main.py
#define MyAppVersion "2.0.0"
#define MyAppPublisher "Soft-Tech Builders"
#define MyAppExeName "Barangay_Attendance.exe"
#define MyBuildDir "dist_attendance\Barangay_Attendance"

[Setup]
; AppId identifies this app for upgrades/uninstall. Never change it after releasing.
AppId={{EADA9E2A-0E78-498A-8D5F-8F8BB552CE42}
AppName={#MyAppName}
AppVersion={#MyAppVersion}
AppPublisher={#MyAppPublisher}
AppContact=softtech.builders26@gmail.com
DefaultDirName={autopf}\Barangay_Attendance_Edition
DefaultGroupName={#MyAppName}
DisableProgramGroupPage=yes
; Terms and conditions page: user must choose "I accept" to continue
LicenseFile=terms_and_conditions_attendance.txt
SetupIconFile=app_icon.ico
UninstallDisplayIcon={app}\{#MyAppExeName}
OutputDir=installer_output
OutputBaseFilename=Barangay_Attendance_Setup
Compression=lzma2
SolidCompression=yes
WizardStyle=modern
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
; Program Files needs administrator rights to install
PrivilegesRequired=admin

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon"; Description: "Create a &desktop shortcut"; GroupDescription: "Shortcuts:"

[Files]
Source: "{#MyBuildDir}\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs
; Full license texts of the open-source parts (made by build_attendance.bat), next to the program
Source: "THIRD_PARTY_NOTICES_Attendance.txt"; DestDir: "{app}"; DestName: "THIRD_PARTY_NOTICES.txt"; Flags: ignoreversion

[Icons]
Name: "{group}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"
Name: "{group}\Uninstall {#MyAppName}"; Filename: "{uninstallexe}"
Name: "{autodesktop}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"; Tasks: desktopicon

[Run]
Filename: "{app}\{#MyAppExeName}"; Description: "Launch {#MyAppName}"; Flags: nowait postinstall skipifsilent

; NOTE: the database lives in %LOCALAPPDATA%\Barangay_Attendance_Edition (outside {app}),
; so uninstalling or upgrading the program never deletes the barangay's records.
; Do not add an [UninstallDelete] entry for it.
