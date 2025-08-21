#define MyAppVersion "1.3.2"
#define MyAppPublisher "Jonas Albrecht"
#define MyFilename="heat_sheet_pdf_highlighter_installer"
#define MyAppURL "https://github.com/jonalbr/heat-sheet-pdf-highlighter"
#define MyAppSupportURL "https://github.com/jonalbr/heat-sheet-pdf-highlighter/issues"
#define MyAppUpdateURL "https://api.github.com/repos/jonalbr/heat-sheet-pdf-highlighter/releases/latest"
#define MyAppExeName "heat_sheet_pdf_highlighter.exe"
#define MyAppId GetEnv('AppId')

[Setup]
AppName={cm:MyAppName}
AppId={#MyAppId}
AppVersion={#MyAppVersion}
VersionInfoVersion={#MyAppVersion}
AppVerName={cm:MyAppVerName, {#MyAppVersion}}
WizardStyle=modern
DefaultDirName={autopf}\{cm:MyAppName}
DefaultGroupName={cm:MyAppName}
AppPublisher={#MyAppPublisher}
AppPublisherURL={#MyAppURL}
AppSupportURL={#MyAppSupportURL}
AppUpdatesURL={#MyAppUpdateURL}
VersionInfoDescription=Highlight PDF heat sheets
VersionInfoProductName=Heat Sheet PDF Highlighter
DisableProgramGroupPage=yes
LicenseFile="LICENSE"
OutputDir="."
OutputBaseFilename={#MyFilename}
SetupIconFile=assets\icon_no_background.ico
UninstallDisplayIcon={app}\{#MyFilename}.exe
PrivilegesRequired=lowest
Compression=lzma2/max
SolidCompression=yes
MissingMessagesWarning=yes
NotRecognizedMessagesWarning=yes

[Languages]
Name: "en"; MessagesFile: "compiler:Default.isl"
Name: "de"; MessagesFile: "compiler:Languages\German.isl"

[Messages]
en.BeveledLabel=English
de.BeveledLabel=Deutsch

[CustomMessages]
en.MyAppName=Heat Sheet PDF Highlighter
en.MyAppVerName=Heat Sheet PDF Highlighter %1
de.MyAppName=Meldeergebnis PDF Highlighter
de.MyAppVerName=Meldeergebnis PDF Highlighter %1

[Registry]
Root: HKCU; Subkey: "Software\Microsoft\Windows\CurrentVersion\Uninstall\{#MyAppId}_is1"; ValueType: string; ValueName: "DisplayName"; ValueData: "{cm:MyAppName}"; Flags: uninsdeletevalue
Root: HKCU; Subkey: "Software\Microsoft\Windows\CurrentVersion\Uninstall\{#MyAppId}_is1"; ValueType: string; ValueName: "DisplayVersion"; ValueData: "{#MyAppVersion}"; Flags: uninsdeletevalue
Root: HKCU; Subkey: "Software\Microsoft\Windows\CurrentVersion\Uninstall\{#MyAppId}_is1"; ValueType: string; ValueName: "Publisher"; ValueData: "{#MyAppPublisher}"; Flags: uninsdeletevalue
Root: HKCU; Subkey: "Software\Microsoft\Windows\CurrentVersion\Uninstall\{#MyAppId}_is1"; ValueType: string; ValueName: "URLInfoAbout"; ValueData: "{#MyAppURL}"; Flags: uninsdeletevalue
Root: HKCU; Subkey: "Software\Microsoft\Windows\CurrentVersion\Uninstall\{#MyAppId}_is1"; ValueType: string; ValueName: "URLUpdateInfo"; ValueData: "{#MyAppUpdateURL}"; Flags: uninsdeletevalue

[Tasks]
Name: "desktopicon"; Description: "{cm:CreateDesktopIcon}"; GroupDescription: "{cm:AdditionalIcons}"; Flags: unchecked

[Files]
Source: "cx_build\*";            DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{autoprograms}\{cm:MyAppName}"; Filename: "{app}\{#MyAppExeName}"
Name: "{autodesktop}\{cm:MyAppName}"; Filename: "{app}\{#MyAppExeName}"; Tasks: desktopicon

[UninstallDelete]
Type: filesandordirs; Name: "{userappdata}\Heat Sheet PDF Highlighter"

[Code]
function InitializeSetup: Boolean;
var
    IsUpdate: Boolean;
begin
    // Check if the application is already installed
    IsUpdate := RegKeyExists(HKEY_CURRENT_USER, 'Software\Microsoft\Windows\CurrentVersion\Uninstall\{#MyAppId}_is1');

    if WizardSilent() then
    begin
        // Silent mode, determine if it is an update
        if IsUpdate then
        begin
            // Silent update with progress bar
            WizardForm.Show;
        end;
    end;

    Result := True;
end;

procedure CurStepChanged(CurStep: TSetupStep);
begin
    if CurStep = ssInstall then
    begin
        // Show progress bar in silent mode during installation
        if WizardSilent() then
        begin
            WizardForm.Show;
        end;
    end;
end;

function StringChange(S: string): string;
var
    Search, Replace: string;
    i: Integer;
begin
    Search := '&';
    Replace := '&&';
    Result := S;
    i := Pos(Search, Result);
    while i > 0 do
    begin
        Delete(Result, i, Length(Search));
        Insert(Replace, Result, i);
        i := Pos(Search, Result);
    end;
end;

[Run]
Filename: "{app}\{#MyAppExeName}"; Description: "{cm:LaunchProgram,{code:StringChange|{cm:MyAppName}}}"; Flags: nowait postinstall
