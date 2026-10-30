; Установщик для Windows (Inno Setup 6). Запуск — scripts/build_windows.ps1.
; Для текущего пользователя, без прав администратора: тогда обновление из программы
; ставится тихо, без запроса UAC.
#ifndef AppVersion
  #define AppVersion "0.0.0"
#endif

[Setup]
AppId={{0468716B-179C-46F0-A55E-40B56021F5DA}
AppName=Калькулятор ВЭД
AppVersion={#AppVersion}
AppVerName=Калькулятор ВЭД {#AppVersion}
DefaultDirName={userpf}\vedcalc
DefaultGroupName=Калькулятор ВЭД
DisableProgramGroupPage=yes
DisableDirPage=yes
PrivilegesRequired=lowest
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
MinVersion=10.0
OutputDir=..\dist
OutputBaseFilename=vedcalc-{#AppVersion}-windows-x64-setup
Compression=lzma2
SolidCompression=yes
WizardStyle=modern
UninstallDisplayIcon={app}\vedcalc.exe
CloseApplications=yes
RestartApplications=no

[Languages]
Name: "russian"; MessagesFile: "compiler:Languages\Russian.isl"

[Tasks]
Name: "desktopicon"; Description: "Значок на рабочем столе"

[Files]
Source: "..\dist\vedcalc\*"; DestDir: "{app}"; Flags: recursesubdirs ignoreversion
; Старые файлы новой версии не мешают: {app} чистится перед установкой
[InstallDelete]
Type: filesandordirs; Name: "{app}\_internal"

[Icons]
Name: "{group}\Калькулятор ВЭД"; Filename: "{app}\vedcalc.exe"
Name: "{userdesktop}\Калькулятор ВЭД"; Filename: "{app}\vedcalc.exe"; Tasks: desktopicon

[Run]
Filename: "{app}\vedcalc.exe"; Description: "Запустить программу"; Flags: nowait postinstall skipifsilent
; Тихая установка из программы (обновление) — сразу запускаем новую версию
Filename: "{app}\vedcalc.exe"; Flags: nowait skipifnotsilent; Check: not NoLaunch

[Code]
function NoLaunch: Boolean;
begin
  Result := ExpandConstant('{param:NOLAUNCH|0}') = '1';
end;
