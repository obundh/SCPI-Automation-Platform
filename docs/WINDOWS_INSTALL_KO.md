# Windows에서 처음 설치하기

## 어떤 파일을 받아야 하나요?

[GitHub Releases](https://github.com/obundh/SCPI-Automation-Platform/releases)에서
가장 위 버전을 엽니다.

- **대부분의 사용자:** `SCPI-Automation-Platform-Setup-...-win64.exe`
- **설치 권한이 없는 시험 PC:** `SCPI-Automation-Platform-Portable-...-win64.zip`

소스 코드 ZIP은 일반 사용자가 받는 실행파일이 아닙니다. Python도 설치할
필요가 없습니다.

## 설치 파일로 시작하기

1. `Setup-...-win64.exe`를 더블클릭합니다.
2. 설치 위치를 특별히 바꿀 필요 없이 `Next`와 `Install`을 누릅니다.
3. 원하면 바탕화면 바로가기를 선택합니다.
4. 마지막 화면에서 `계측기 연결 도우미 실행`을 선택합니다.
5. 첫 화면에서 `데모 장비 4대로 둘러보기`를 눌러 전체 순서를 익힙니다.

프로그램은 현재 Windows 사용자 영역에 설치되므로 일반적으로 관리자
권한이 필요하지 않습니다. Windows 설정의 `설치된 앱`에서 제거할 수 있으며,
제거해도 사용자가 저장한 측정 결과와 장비 검증 기록은 자동으로 삭제하지
않습니다.

## Portable ZIP으로 시작하기

1. ZIP 파일을 새 폴더에 압축 해제합니다.
2. 압축을 풀지 않은 상태로 ZIP 안의 EXE를 직접 실행하지 마세요.
3. 폴더 안의 `SCPI-Automation-Platform.exe`를 더블클릭합니다.
4. `LICENSES`, `README-KO.txt` 등의 동봉 파일은 EXE와 같은 폴더에
   그대로 둡니다.

USB처럼 폴더째 옮겨 사용하는 경우에도 사용자 검증 기록과 측정 결과는
Windows 사용자 폴더에 별도로 저장됩니다.

## Windows에서 경고가 나타나는 경우

현재 무료 개발 프리뷰에는 상용 Authenticode 코드 서명이 적용되지 않았습니다.
따라서 SmartScreen이 `Windows의 PC 보호` 또는 `알 수 없는 게시자`라고
표시할 수 있습니다.

다음 세 가지가 모두 맞을 때만 `추가 정보 → 실행`을 선택하세요.

1. 주소가 `github.com/obundh/SCPI-Automation-Platform`인지 확인했습니다.
2. Releases에 첨부된 파일을 직접 받았습니다.
3. 필요하면 같은 Release의 `SHA256SUMS-Windows.txt`와 파일 해시를
   비교했습니다.

공식 Release가 아닌 메일·메신저·다른 사이트에서 받은 실행파일은 실행하지
마세요.

PowerShell에서 해시를 확인하려면 다음처럼 입력합니다.

```powershell
Get-FileHash -Algorithm SHA256 .\SCPI-Automation-Platform-Setup-이름.exe
```

화면에 나온 해시가 `SHA256SUMS-Windows.txt`의 같은 파일 값과 일치해야 합니다.

## 실제 장비가 검색되지 않는 경우

프로그램과 데모는 설치만으로 실행됩니다. 실제 계측기 검색에는 PC와 장비가
대화할 수 있게 해주는 **VISA 통신 드라이버**가 별도로 필요할 수 있습니다.
VISA는 쉽게 말해 PC와 계측기 사이의 통역 통로입니다.

시험실에서 이미 사용 중인 VISA가 있다면 그대로 사용하는 것이 우선입니다.
여러 종류를 무작정 모두 설치하면 기본 backend가 달라져 혼란이 생길 수
있습니다.

- [NI-VISA 공식 다운로드](https://www.ni.com/en/support/downloads/drivers/download.ni-visa.html)
- [Keysight IO Libraries Suite 공식 다운로드](https://www.keysight.com/us/content/lib/software-detail/computer-software/io-libraries-suite-downloads-2175637.html)
- [R&S VISA 공식 다운로드](https://www.rohde-schwarz.com/uk/driver-pages/remote-control/3-visa-and-tools_231388.html)

이 프로젝트는 제조사 설치 프로그램이나 DLL을 재배포하지 않습니다. 인터넷이
막힌 시험 PC라면 기관의 소프트웨어 관리 절차에 따라 공식 오프라인 설치
파일을 별도로 준비해야 합니다.

VISA 설치 후에는 다음 순서로 확인합니다.

1. 계측기와 PC를 연결하고 계측기 전원을 켭니다.
2. 제조사의 연결 도구에서 장비 주소가 보이는지 확인합니다.
3. 계측기 연결 도우미를 다시 실행합니다.
4. `1. 장비 찾기`에서 `장비 찾아보기`를 누릅니다.
5. 여전히 안 보이면 화면의 `이렇게 해보세요` 안내를 확인합니다.

## 안전하게 처음 시험하기

1. 데모 장비로 루틴과 계획서 작성 방법을 먼저 확인합니다.
2. 실제 장비에서는 조회 명령부터 검증합니다.
3. Dry Run에서 대상 장비, 설정값, 실행 순서와 안전 종료를 확인합니다.
4. RF 출력·전압·전류·전력 명령은 장비와 DUT의 정격을 확인한 뒤
   승인합니다.
5. 실제 비상정지와 장비 전면 출력 OFF 수단을 확보합니다.

자동 테스트 통과는 특정 장비·펌웨어·옵션·DUT 조합의 안전을 보증하지
않습니다.
