#ifndef AppVersion
  #define AppVersion "0.1.0.dev1"
#endif

#ifndef PayloadDir
  #error PayloadDir must point to the audited Windows release folder
#endif

#ifndef OutputDir
  #error OutputDir must point to a new installer output folder
#endif

#ifndef OutputBaseFilename
  #define OutputBaseFilename "SCPI-Automation-Platform-Setup-win64"
#endif

#define AppName "계측기 연결 도우미"
#define AppExeName "SCPI-Automation-Platform.exe"

[Setup]
AppId={{11643752-7FC1-4D6B-BAE3-8808802D0FB5}
AppName={#AppName}
AppVersion={#AppVersion}
AppVerName={#AppName} {#AppVersion}
AppPublisher=SCPI Automation Platform contributors
AppPublisherURL=https://github.com/obundh/SCPI-Automation-Platform
AppSupportURL=https://github.com/obundh/SCPI-Automation-Platform/issues
AppUpdatesURL=https://github.com/obundh/SCPI-Automation-Platform/releases
DefaultDirName={localappdata}\Programs\SCPI Automation Platform
DefaultGroupName={#AppName}
DisableProgramGroupPage=yes
PrivilegesRequired=lowest
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
MinVersion=10.0
OutputDir={#OutputDir}
OutputBaseFilename={#OutputBaseFilename}
SetupIconFile=..\..\assets\scpi-automation-platform.ico
UninstallDisplayIcon={app}\{#AppExeName}
LicenseFile={#PayloadDir}\LICENSE.txt
InfoAfterFile={#PayloadDir}\README-KO.txt
Compression=lzma2/normal
SolidCompression=yes
WizardStyle=modern
SetupLogging=yes
CloseApplications=yes
RestartApplications=no
AppMutex=SCPIAutomationPlatform-11643752-7FC1-4D6B-BAE3-8808802D0FB5
ChangesAssociations=no
ChangesEnvironment=no

[Tasks]
Name: "desktopicon"; Description: "바탕화면에 바로가기 만들기"; GroupDescription: "추가 바로가기:"; Flags: unchecked

[Files]
Source: "{#PayloadDir}\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs
Source: "..\licenses\Inno-Setup-6.7.3-license.txt"; DestDir: "{app}\LICENSES\Inno-Setup-6.7.3"; Flags: ignoreversion

[Icons]
Name: "{group}\{#AppName}"; Filename: "{app}\{#AppExeName}"; WorkingDir: "{app}"
Name: "{group}\설치 및 장비 연결 안내"; Filename: "{app}\README-KO.txt"
Name: "{autodesktop}\{#AppName}"; Filename: "{app}\{#AppExeName}"; WorkingDir: "{app}"; Tasks: desktopicon

[Run]
Filename: "{app}\{#AppExeName}"; Description: "설치를 마치고 {#AppName} 실행"; WorkingDir: "{app}"; Flags: nowait postinstall skipifsilent
