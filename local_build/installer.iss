; ── 코코비즈 로컬 설치판 Inno Setup 스크립트 ──────────────────────
; build.bat 실행 후 생성된 _build\ 폴더를 패키징해 설치 프로그램(.exe) 제작.
; Inno Setup(https://jrsoftware.org/isdl.php) 설치 후 이 파일을 컴파일.

#define AppName "코코비즈 (코스트코 자동화)"
#define AppVer  "1.0.0"
#define AppPub  "코코비즈"
#define BuildDir "_build"

[Setup]
AppName={#AppName}
AppVersion={#AppVer}
AppPublisher={#AppPub}
DefaultDirName={autopf}\CocoBiz
DefaultGroupName=코코비즈
DisableProgramGroupPage=yes
OutputBaseFilename=CocoBiz_Setup_{#AppVer}
Compression=lzma2
SolidCompression=yes
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
PrivilegesRequired=lowest
SetupIconFile=

[Languages]
Name: "korean"; MessagesFile: "compiler:Languages\Korean.isl"

[Files]
; 빌드 결과물 전체(python\, app\, run_costco.bat)를 설치 폴더로 복사
Source: "{#BuildDir}\*"; DestDir: "{app}"; Flags: recursesubdirs createallsubdirs ignoreversion

[Icons]
Name: "{group}\코코비즈 실행";     Filename: "{app}\run_costco.bat"; WorkingDir: "{app}"; IconFilename: "{app}\app\favicon.ico"
Name: "{autodesktop}\코코비즈";    Filename: "{app}\run_costco.bat"; WorkingDir: "{app}"; IconFilename: "{app}\app\favicon.ico"
Name: "{group}\코코비즈 제거";     Filename: "{uninstallexe}"

[Tasks]
Name: "desktopicon"; Description: "바탕화면 아이콘 만들기"; GroupDescription: "추가 아이콘:"

[Run]
Filename: "{app}\run_costco.bat"; Description: "지금 코코비즈 실행"; Flags: postinstall nowait skipifsilent shellexec

; 참고:
;  - 첫 실행 시 라이선스키 입력(활성화) 화면이 뜹니다 (1키=1PC, 서버 검증).
;  - 각 사용자는 본인 네이버/쿠팡 API 키를 설정 탭에서 입력해 사용합니다.
