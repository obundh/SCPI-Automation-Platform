# 카탈로그 확장 백로그

## RF·EMC 우선

- Keysight N517x/N518x/N519x RF·Vector Signal Generator
- R&S SMB100B, SMBV100B, SMW200A, SMA100B
- Keysight N9000/N9010/N9020/N9030 X-Series
- R&S FSV3000/FSVA3000/FSWP
- Anritsu MG369x/MG37xxx Signal Generator
- EMI Test Receiver: R&S ESR/ESW/ESRP, Keysight N9038A
- RF Power Meter: Keysight N1911A/N1912A, R&S NRP
- VNA: R&S ZNB/ZNA/ZVL, Copper Mountain

## 일반 계측

- Tektronix MSO/DPO 계열
- Keysight InfiniiVision X-Series
- RIGOL DP800, DG1000Z, DSA800
- Keithley 2400/2450/2460/7510
- Keysight DAQ970A/34970A
- Yokogawa WT3000/WT5000
- Keysight N6700/N6705
- 전자부하·SMU·파워 분석기·DAQ

## 검증 승격 절차

1. 재사용할 수 있는 permissive 오픈소스 드라이버·프로필을 찾고 프로젝트,
   파일, revision, 저작권 표시와 라이선스를 확인
2. 공식 문서는 문서번호·버전·적용 모델·공식 URL 같은 서지정보만 등록하고,
   원문·명령 색인·페이지 매핑은 공개 데이터에 추가하지 않음
3. permissive 소스의 기능을 capability·operation으로 정리하고 parameter·응답·
   위험도를 독립적으로 검토
4. 실제 `*IDN?`, serial, firmware, option과 interface를 수집
5. 정확히 일치하는 모델이어도 배포 프로필만 선택하고 자동 승인하지 않음
6. 명시된 read-only query, 응답 형식, 오류 큐와 timeout을 operation별로 검증
7. 복원 가능한 write는 현재값 저장·readback·원래 값 복원까지 검증
8. 출력·파일·메모리·reset·calibration은 물리 조건을 확인하는 수동 승인으로 분리
9. `pass / fail / pending / skipped / unsafe / manual` 결과를 장비 바인딩에 저장
10. 통과 operation allowlist와 안전 한계·종료 결과를 묶어 최종 장비 바인딩 생성

배포 프로필 전체를 한 번에 `hardware_verified`로 승격하지 않는다. 같은 모델도
firmware·option 또는 물리 장비별 결과가 다르면 operation allowlist를 따로
관리한다.

Trace, Marker, Detector처럼 장비 분류에서 공통으로 사용하는 기능 개념은
taxonomy에 추가할 수 있다. 그러나 새 모델의 실제 SCPI 템플릿은 permissive
소스 또는 사용자 보유 실장비의 독립 검증 근거가 생긴 뒤에만 연결한다.

사용자가 적법하게 보유한 매뉴얼을 로컬에서 분석하는 기능은 허용하지만,
원문·OCR·명령 후보·페이지 매핑·추출 중간 파일은 저장소 밖 사용자 전용 폴더에
두고 Git, 릴리스 압축파일, 설치 프로그램과 실행파일에 포함하지 않는다.
