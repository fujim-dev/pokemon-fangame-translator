#define MyAppName "Pokémon Fangame Translator"
#define MyAppVersion "1.0.2"
#define MyAppPublisher "Pokémon Fangame Translator Community"
#define MyAppExeName "PokemonFangameTranslator.exe"

[Setup]
AppId={{E8E52A0F-14B1-4B54-9A4B-6C8F784C0B71}
AppName={#MyAppName}
AppVersion={#MyAppVersion}
AppPublisher={#MyAppPublisher}
DefaultDirName={localappdata}\Programs\Pokemon Fangame Translator
DefaultGroupName=Pokémon Fangame Translator
DisableProgramGroupPage=yes
PrivilegesRequired=lowest
OutputDir=..\..\release
OutputBaseFilename=Pokemon_Fangame_Translator_Setup_v1.0.2
SetupIconFile=PokemonFangameTranslator.ico
UninstallDisplayIcon={app}\{#MyAppExeName}
Compression=lzma2/ultra64
SolidCompression=yes
WizardStyle=modern
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
CloseApplications=yes
RestartApplications=no
VersionInfoVersion={#MyAppVersion}
VersionInfoProductName={#MyAppName}
VersionInfoDescription=Pokémon Fangame Translator v1.0.2 — Bêta publique
VersionInfoCompany={#MyAppPublisher}
LicenseFile=..\..\LICENSE
InfoBeforeFile=..\..\INSTALLATION_AVERTISSEMENT.txt
UsePreviousAppDir=yes
Uninstallable=yes

[Languages]
Name: "french"; MessagesFile: "compiler:Languages\French.isl"

[Tasks]
Name: "desktopicon"; Description: "Créer une icône sur le Bureau"; GroupDescription: "Raccourcis :"; Flags: unchecked

[Files]
Source: "..\..\dist\PokemonFangameTranslator\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{group}\Pokémon Fangame Translator"; Filename: "{app}\{#MyAppExeName}"; WorkingDir: "{app}"
Name: "{autodesktop}\Pokémon Fangame Translator"; Filename: "{app}\{#MyAppExeName}"; WorkingDir: "{app}"; Tasks: desktopicon

[Run]
Filename: "{app}\{#MyAppExeName}"; Description: "Lancer Pokémon Fangame Translator"; Flags: nowait postinstall skipifsilent

[UninstallDelete]
Type: filesandordirs; Name: "{app}"
