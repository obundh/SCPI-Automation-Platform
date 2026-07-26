# 라이선스 및 제조사 문서 사용 감사

검토일: 2026-07-26

이 문서는 SCPI Automation Platform에 포함되는 장비 프로필과 제조사 문서 근거를
공개 저장소 및 실행파일에 배포해도 되는 데이터와 로컬에서만 사용해야 하는 데이터로
구분하기 위한 기록이다.

## 결론

- 제조사 매뉴얼 PDF, 매뉴얼 본문, 표, 그림, 화면 캡처는 저장소와 실행파일에 포함하지 않는다.
- 제조사 매뉴얼에서 추출한 전체 명령 색인, 명령과 매뉴얼 페이지의 대응표 및 이에 준하는
  대량 데이터도 공개 배포하지 않는다.
- 장비 프로필의 실제 SCPI 명령은 허용 범위가 확인된 permissive 오픈소스, 사용자가 보유한
  실장비를 통한 독립 검증 또는 제조사의 명시적 서면 허가를 근거로 작성한다.
- 제조사 문서는 제목, 문서번호, 버전, 적용 모델과 공식 웹 링크 같은 서지정보만 남긴다.
  공식 링크를 수록했다는 사실은 해당 문서의 재배포 허가를 의미하지 않는다.
- permissive 오픈소스 근거를 이용한 프로필은 해당 프로젝트의 저작권 표시와 라이선스 조건을
  `THIRD_PARTY_NOTICES.md` 및 배포물에 함께 포함해야 한다.
- permissive 오픈소스 라이선스는 해당 오픈소스 작성자가 보유한 권리만
  허가하며 제조사 매뉴얼에 관한 권리까지 보증하지 않는다. 공개 프로필에는
  짧은 기능 구문과 상호운용에 필요한 사실만 독립적으로 정규화한다.

## 프로필별 배포 판단

아래의 “유지”는 제조사 매뉴얼에서 추출한 데이터가 아니라 표에 적힌 permissive 오픈소스
근거만을 사용하고, 해당 저작권 및 라이선스 고지를 배포물에 포함하는 경우를 뜻한다.

| 프로필 | 대표 모델·제품군 | 장비 분류 | 유지 가능한 permissive OSS 근거 | 공개 배포에서 제거한 매뉴얼 근거 | 배포 상태 |
|---|---|---|---|---|---|
| `keysight_33500_series` | Keysight/Agilent 33500 Series | 함수·임의파형 발생기 | [PyMeasure `agilent33500.py`](https://github.com/pymeasure/pymeasure/blob/master/pymeasure/instruments/agilent/agilent33500.py), MIT | 33500 Series User's Guide에서 추출한 명령 색인 113개와 페이지 대응정보 | **유지** — PyMeasure 근거 프로필만 배포 |
| `keysight_344xxa_truevolt` | 34410A, 34411A, 34460A, 34461A, 34465A, 34470A | 디지털 멀티미터 | [QCoDeS `Keysight_344xxA_submodules.py`](https://github.com/microsoft/Qcodes/blob/main/src/qcodes/instrument_drivers/Keysight/private/Keysight_344xxA_submodules.py), MIT | Truevolt Operating and Service Guide에서 추출한 명령 색인 431개와 페이지 대응정보 | **유지** — QCoDeS 근거 프로필만 배포 |
| `keysight_e36312a` | E36312A / E36300 Series | DC 전원공급기 | [PyMeasure `keysightE36312A.py`](https://github.com/pymeasure/pymeasure/blob/master/pymeasure/instruments/keysight/keysightE36312A.py), MIT | E36300 Series Programming Guide에서 추출한 명령 색인 120개와 페이지 대응정보 | **유지** — PyMeasure 근거 프로필만 배포 |
| `keysight_e4980a` | E4980A | LCR 미터 | [QCoDeS `keysight_e4980a.py`](https://github.com/microsoft/Qcodes/blob/main/src/qcodes/instrument_drivers/Keysight/keysight_e4980a.py), MIT | E4980A User's Guide에서 추출한 명령 색인 148개와 페이지 대응정보 | **유지** — QCoDeS 근거 프로필만 배포 |
| `keysight_n52xx_pna` | N5245A, N52xxA PNA/PNA-X | 벡터 네트워크 분석기 | [QCoDeS `N52xx.py`](https://github.com/microsoft/Qcodes/blob/main/src/qcodes/instrument_drivers/Keysight/N52xx.py) 및 [`Keysight_N5245A.py`](https://github.com/microsoft/Qcodes/blob/main/src/qcodes/instrument_drivers/Keysight/Keysight_N5245A.py), MIT | 매뉴얼 명령 데이터는 배포하지 않으며 공식 Help 링크와 서지정보만 유지 | **유지** — QCoDeS 근거 프로필만 배포 |
| `kikusui_pmx35_3a` | PMX35-3A / PMX-A Series | DC 전원공급기 | [TECTOS-JP `kikusui_pmx35_3a.yaml`](https://github.com/TECTOS-JP/lab-visa-mcp/blob/main/examples/instruments/kikusui_pmx35_3a.yaml), MIT | PMX Communication Interface Manual 본문과 명령 색인은 배포하지 않고 공식 링크만 유지 | **유지** — MIT 프로필 근거만 배포 |
| `rigol_ds1000z` | DS1054Z, DS1074Z, DS1104Z, MSO/DS1000Z Series | 디지털 오실로스코프 | [armchairdeity/rigol-ds1000z](https://github.com/armchairdeity/rigol-ds1000z), MIT | DS1000Z Programming Guide 본문·표·명령 색인은 배포하지 않고 공식 링크만 유지 | **유지** — MIT 드라이버 근거만 배포 |
| `rs_fsl` | FSL3, FSL6, FSL18 / FSL Series | 스펙트럼 분석기 | [PyMeasure `fsseries.py`](https://github.com/pymeasure/pymeasure/blob/master/pymeasure/instruments/rohdeschwarz/fsseries.py), MIT | FSL Operating Manual에서 추출한 명령 색인 1,241개와 페이지 대응정보 | **유지** — PyMeasure 근거 프로필만 배포 |
| `rs_fsv_fsva` | FSV3/7/13/30/40, FSVA4/7/13/30/40 | 신호·스펙트럼 분석기 | [QCoDeS contrib `FSV_3013.py`](https://github.com/QCoDeS/Qcodes_contrib_drivers/blob/main/src/qcodes_contrib_drivers/drivers/RohdeSchwarz/FSV_3013.py), MIT | FSV/FSVA User Manual 명령 색인 798개와 페이지 대응정보, 매뉴얼 페이지만 근거였던 기능 70개 | **부분 유지** — OSS 근거 14개 기능만 배포하고 나머지는 재검증 전 제외 |
| `rs_fsw` | FSW Series | 신호·스펙트럼 분석기 | [PyMeasure `fsseries.py`](https://github.com/pymeasure/pymeasure/blob/master/pymeasure/instruments/rohdeschwarz/fsseries.py), MIT | FSW User Manual의 본문·표·명령 색인은 배포하지 않고 공식 링크만 유지 | **유지** — PyMeasure 근거 프로필만 배포 |
| `rs_hmp2000_hmp4000` | HMP2020, HMP2030, HMP4040, HMP2000/4000 Series | DC 전원공급기 | [PyMeasure `hmp.py`](https://github.com/pymeasure/pymeasure/blob/master/pymeasure/instruments/rohdeschwarz/hmp.py), MIT | HMP Series User Manual에서 추출한 명령 색인 55개와 페이지 대응정보 | **유지** — PyMeasure 근거 프로필만 배포 |
| `rs_smb100a` | SMB100A | RF 신호발생기 | [QCoDeS contrib `SMB100A.py`](https://github.com/QCoDeS/Qcodes_contrib_drivers/blob/main/src/qcodes_contrib_drivers/drivers/RohdeSchwarz/SMB100A.py), MIT | SMB100A Operating Manual에서 추출한 명령 색인 429개와 페이지 대응정보 | **유지** — QCoDeS contrib 근거 프로필만 배포 |

FSV/FSVA에서 OSS 근거로 유지하는 14개 기능은 중심주파수, Span, 기준 레벨, RBW, VBW,
Sweep Time, Continuous Sweep, Trigger Source, Trigger Level, Correction State, Input Impedance,
Measurement Initiate, ACP Power Fetch 및 Reset이다. 매뉴얼에서만 확인했던 Marker, Trace,
Detector, Averaging, 추가 Sweep 및 System 기능은 permissive 소스나 실장비 독립 검증 근거가
생기기 전까지 공개 프로필에 포함하지 않는다.

## 제조사 공식 문서 정책

아래 링크는 검토 당시 확인한 공식 정책 및 문서 고지다. 제조사 정책은 변경될 수 있으므로
릴리스 전 다시 확인한다.

- **Rohde & Schwarz:** [Digital Channels Terms of Use](https://www.rohde-schwarz.com/au/general-information/terms-of-website-use_101516.html)
  는 개인용 인쇄·일부 다운로드 범위를 안내하며, 자료 수정과 허가 없는 상업적 이용을 제한한다.
- **Keysight:** [Website Terms of Use](https://www.keysight.com/kr/ko/contact/terms-of-use.html)
  는 조직 내부의 비상업적 제품 취득·사용·지원 목적에 한정하여 자료 열람과 다운로드를 허용하고,
  공개 배포 및 다른 웹·네트워크 환경에서의 사용을 제한한다.
  [Keysight 공식 Legal Notices](https://helpfiles.keysight.com/Standalone_BenchVueSoftware_PW_HelpFiles/PWPowerAppSuite/Content/General/Legal%20Notices.htm)
  에도 사전 서면동의 없는 매뉴얼 복제 금지가 명시되어 있다.
- **RIGOL:** DS1000Z Programming Guide의 문서 고지는 사전 서면승인 없는
  문서 일부의 복사·복제·재배열을 금지한다. 저장소에는
  [공식 제품·문서 페이지](https://www.rigolna.com/products/digital-oscilloscopes/1000z/)와
  [공식 다운로드 페이지](https://www.rigolna.com/support/downloads/) 링크만 둔다.
- **Kikusui:** [PMX 공식 매뉴얼 다운로드 페이지](https://global.kikusui.co.jp/download/pmx/)
  는 허가 없는 매뉴얼 일부 복제를 금지하고, 비상업적 개인 목적의 한 부 복사만 예외로 안내한다.
  제품의 통신 방식 같은 사실 확인에는
  [PMX-A Interface 공식 페이지](https://global.kikusui.co.jp/spec/pmx-a_interface/)를 링크한다.

## 공통 기능을 유지하는 원칙

1. `중심주파수 설정`, `RBW 설정`, `Peak Search`, `전압 측정`처럼 장비 분류에서 공통으로
   사용하는 기능 개념, 자체 작성한 capability ID, 한국어 기능명, 단위 및 입력 UI 구조는
   제조사 매뉴얼 문장을 복사하지 않고 독립적으로 작성한다.
2. `*IDN?`, `*RST`, `*CLS`, `*OPC?` 같은 짧은 상호운용 명령과 기능적 SCPI 구문은
   필요한 최소 범위만 사용한다. 표준이나 제조사 매뉴얼의 설명문, 예제, 표, 전체 색인 및
   배열을 함께 복제하지 않는다.
3. 제조사·모델별 실제 명령 템플릿과 값 범위는 다음 중 하나 이상의 근거가 있을 때만 배포한다.
   - permissive 라이선스가 확인된 오픈소스 드라이버 또는 프로필
   - 사용자가 적법하게 보유한 실장비에서 수행한 독립적인 송신·조회·오류 확인
   - 제조사의 명시적인 서면 재사용 허가
4. 공식 매뉴얼을 보고 기능 존재 여부를 확인한 사실과, 해당 명령을 공개 프로필에 재배포할
   권리는 구분한다. 매뉴얼 확인만으로 `redistributable` 상태를 부여하지 않는다.
5. 오픈소스 코드 확인, 매뉴얼 확인, Dry Run 통과 및 실장비 검증 상태를 각각 별도 필드로
   관리한다. 문서 근거만 있는 기능을 `hardware_verified`로 표시하지 않는다.

## 사용자 로컬 매뉴얼과 추출 캐시

- 사용자가 직접 불러온 매뉴얼은 사용자 PC의 로컬 작업 영역에서만 처리한다.
- `tmp/pdfs/`, `tmp/manuals/`, `tmp/manual_extraction/` 및 이와 동등한 임시 경로는
  `.gitignore`로 제외하고 Git, 릴리스 압축파일, 설치 프로그램 및 실행파일에 포함하지 않는다.
- 매뉴얼에서 생성한 명령 후보, 페이지 대응표, OCR 텍스트 및 임베딩도 로컬 캐시로 취급하며
  공개 저장소에 커밋하지 않는다.
- 프로필로 승격할 때는 매뉴얼 추출 결과를 그대로 복사하지 않고, 위의 공통 기능 유지 원칙에
  따라 출처와 재배포 권한을 다시 확인한다.
- 릴리스 전 `git ls-files`와 Git 이력을 검사하여 제조사 PDF나 추출물이 현재 트리뿐 아니라
  과거 커밋에도 포함되지 않았는지 확인한다.

## 상표 및 비제휴 고지

Rohde & Schwarz, R&S, Keysight, Agilent, RIGOL, Kikusui 및 각 제품명·모델명은 각 권리자의
상표 또는 등록상표일 수 있다. 이 프로젝트는 호환 장비를 식별하고 상호운용성을 설명하기
위한 지칭 목적으로만 제조사명과 모델명을 사용한다. 각 제조사가 이 프로젝트를 보증, 승인,
후원하거나 공식적으로 제휴했다는 의미가 아니다. 제조사 로고, 제품 UI의 복제 이미지,
마케팅 사진 및 고유한 트레이드 드레스는 별도 허가 없이 사용하지 않는다.
`SCPI`와 `VISA` 명칭 역시 통신 방식을 설명하기 위한 것이며 관련 표준기관의
인증, 공식 적합성 또는 보증을 주장하지 않는다.

## 면책

이 문서는 공개 배포 위험을 줄이기 위한 기술적 라이선스 감사 기록이며 법률 자문이 아니다.
저작권, 계약, 상표 및 데이터베이스 권리의 적용은 국가와 배포 방식에 따라 달라질 수 있다.
상용 배포, 유료 서비스, 대량 프로필 제공 또는 제조사 문서에서 파생한 데이터 제공을 계획할
경우에는 배포 전에 관련 자격을 갖춘 법률 전문가의 검토를 받아야 한다.
