# SCPI 기기별 명령 매핑 데이터팩

현황 기준: 2026-07-26 라이선스 정리 후 공개 프로필
카탈로그 버전: `0.1.0`

이 자료는 **LLM이나 MCP 없이** 구형 Windows 계측 노트북에서 사용할 범용 SCPI 자동화 프로그램의
초기 로컬 데이터베이스로 쓰기 위해 정리한 1차본이다. 여기의 12개 프로필은
permissive 오픈소스 드라이버·프로필을 근거로 정리했으며, 완성된 대표 모델이나
실장비 지원 보증서가 아니다.

제조사 매뉴얼 원문, 명령 색인, 명령과 페이지의 대응표는 이 데이터팩에 넣지
않는다. 공식 문서 링크는 서지정보 확인용이며 문서 재배포 허가를 뜻하지 않는다.

## 현재 규모

- permissive OSS 근거 프로필: **12개**
- 공통 기능(capability) 바인딩: **237개**
- 구조화된 SCPI operation: **390개**
- 내장 제조사 매뉴얼 원문·명령 색인·페이지 매핑: **0개**
- 공식 매뉴얼·지원 페이지: **12개**
- 코드·프로파일·아키텍처 출처: **15개**
- high-risk operation: **60개**
- SQLite 무결성 검사: **ok**
- SCPI placeholder 검증: **통과**

## 포함 프로파일

| profile_id | 제조사 | 모델군 | 장비 분류 | 기능 수 | operation 수 | 검증 단계 |
|---|---|---|---|---:|---:|---|
| `kikusui_pmx35_3a` | Kikusui | PMX-A | dc_power_supply | 20 | 27 | profile_source_confirmed |
| `rs_smb100a` | Rohde & Schwarz | SMB100A | rf_signal_generator | 15 | 27 | source_code_confirmed |
| `rs_fsl` | Rohde & Schwarz | FSL | spectrum_analyzer | 20 | 32 | source_code_confirmed |
| `rs_fsw` | Rohde & Schwarz | FSW | signal_and_spectrum_analyzer | 27 | 39 | source_code_confirmed |
| `rs_fsv_fsva` | Rohde & Schwarz | FSV/FSVA legacy (FW 3.60) | signal_and_spectrum_analyzer | 14 | 25 | source_code_confirmed |
| `keysight_e36312a` | Keysight | E36300 Series | dc_power_supply | 5 | 8 | source_code_confirmed |
| `keysight_33500_series` | Keysight / Agilent | 33500 Series | function_arbitrary_waveform_generator | 23 | 46 | source_code_confirmed |
| `keysight_344xxa_truevolt` | Keysight | 344xxA / Truevolt | digital_multimeter | 20 | 33 | project_driver_confirmed |
| `rigol_ds1000z` | RIGOL | DS1000Z | digital_oscilloscope | 32 | 55 | live_hardware_source_confirmed |
| `keysight_e4980a` | Keysight | E4980A | lcr_meter | 18 | 29 | project_driver_confirmed |
| `keysight_n52xx_pna` | Keysight | N52xx PNA/PNA-X | vector_network_analyzer | 22 | 39 | project_driver_confirmed |
| `rs_hmp2000_hmp4000` | Rohde & Schwarz | HMP2000/HMP4000 | dc_power_supply | 21 | 30 | source_code_confirmed |

## 파일 설명

- `scpi_catalog.json` — 전체 통합 원본
- `scpi_catalog.sqlite` — 실행파일에서 바로 조회할 정규화 DB
- `command_bindings.csv` — Excel·텍스트 검토용 평탄화 표
- `profiles/*.json` — 기기별 분리 프로파일
- `manual_catalog.json` — 공식 문서의 제목·번호·버전·공식 URL 등 서지정보
- `source_catalog.json` — permissive OSS 원자료·revision·라이선스
- `capability_taxonomy.json` — 장비 분류·공통 기능 ID·위험도·검증 단계
- `scpi_catalog.schema.json` — 기본 JSON Schema
- `DATA_DICTIONARY.md` — 필드 정의
- `reports/coverage_report.json` — 범위·수량·한계·검증 결과
- `THIRD_PARTY_NOTICES.md` — 제3자 출처와 라이선스 주의
- `LICENSE_AUDIT.md` — 제조사 문서와 프로필별 공개 배포 판단
- `EXPANSION_BACKLOG.md` — 다음 수집 대상

## 매핑 방식

GUI는 SCPI 문자열이 아니라 공통 기능을 사용한다.

```text
rf.output.state
source.frequency
source.power
analyzer.frequency.center
marker.peak_search
measurement.voltage
```

프로그램은 `*IDN?` 응답으로 배포 프로필을 선택한다. 정확한 IDN 일치는
프로필 선택일 뿐, 그 장비가 모든 명령을 지원한다는 판정이 아니다.

```text
IDN·firmware·option
  + 배포 프로필 ID·버전·fingerprint
  + operation별 실장비 검증 결과
  = 최종 장비 분류와 실행 allowlist
```

allowlist에 `pass`로 기록된 operation만 실제 명령으로 렌더링한다.

```text
profile_id = rs_smb100a
capability = rf.output.state
arguments = {state: true}
SCPI = :OUTP:STAT 1
```

```text
profile_id = keysight_e36312a
capability = channel.output.state
arguments = {channel: 2, state: true}
SCPI = OUTPut 1, (@2)
```

## SQLite 사용 예

```sql
SELECT profile_id, capability_id, operation_name, scpi, risk_level
FROM v_command_map
WHERE profile_id='rs_smb100a'
ORDER BY capability_id, operation_name;
```

```sql
SELECT *
FROM v_command_map
WHERE capability_id='rf.output.state';
```

```sql
SELECT *
FROM v_high_risk_commands
ORDER BY profile_id, capability_id;
```

## 명령 근거와 실장비 검증

- `profile_source_confirmed`: 구조화된 장비 프로파일에서 확인
- `source_code_confirmed`: 오픈소스 드라이버 코드에서 확인
- `project_driver_confirmed`: 주요 프로젝트의 공식 저장소 드라이버에서 확인
- `live_hardware_source_confirmed`: 출처 프로젝트가 실장비 사용·시험을 명시
- `hardware_verified_by_catalog_owner`: 우리 실장비에서 직접 검증한 과거 표기

위 값은 후보 명령의 **출처 근거**다. `source_code_confirmed`나
`live_hardware_source_confirmed`도 현재 연결한 물리 장비에서 동작한다는 보증이
아니다. 실장비 결과는 operation마다 다음 상태로 별도 저장한다.

- `pass`: query·readback·복원 등 정해진 기준을 통과
- `fail`: 응답, 오류 큐, readback 또는 복원 기준을 통과하지 못함
- `pending`: 아직 시험하지 않음
- `skipped`: 운영자가 이번 검증에서 건너뜀
- `unsafe`: 현재 연결·시험 조건에서 자동 검증 금지
- `manual`: 파일·메모리·calibration·reset 등 수동 절차 필요

사용자가 적법하게 보유한 매뉴얼에서 직접 만든 Query 후보는 위 operation
상태와 별도로 `응답 수신·미승격` 증거를 저장할 수 있다. 제조사 원문, OCR,
명령 색인과 페이지 매핑은 저장소 밖 사용자 로컬 폴더에서만 관리하며 이
데이터팩·Git·설치본·실행파일에 포함하지 않는다.

GUI에서 사용자 로컬 후보를 기능으로 승격하려면 `조회 / 설정 / 실행` 유형,
실제 SCPI template, parameter 타입·단위·범위·선택값, 시험값, 응답 parser,
위험도와 paired readback Query를 먼저 구조화해야 한다. 이후 exact
manufacturer·model·serial·firmware·`*OPT?`를 다시 확인하고 Query 또는
쓰기 → Readback → 원복 → 원복 확인이 모두 PASS인 operation만 로컬
레지스트리에 저장한다. Execute는 자동 전송하지 않고 수동 시험 증거를 요구한다.
로컬 SET/EXECUTE는 항상 고위험 개별 승인을 요구하고 `manual_only` 후보를
자동 Query로 바꾸지 않는다. 옵션 상태는 조회됨·미지원·미조회로 구분하며,
최초 승격은 실제 시험한 값·채널·Trace·선택지 조합에만 잠근다. 저장
레지스트리는 HMAC-SHA256과 Windows 현재 사용자 DPAPI 키로 인증한다.
JSON·anti-rollback state·DPAPI key anchor의 세대와 digest가 모두 일치해야
한다. 세 파일 전체를 동일한 과거 백업으로 복원한 경우는 외부 단조 카운터가
없는 오프라인 구조에서 탐지할 수 없으므로 로컬 기능을 다시 검증해야 한다.
여러 프로그램 창의 저장은 프로세스 잠금과 generation/digest
compare-and-swap으로 직렬화하며, 오래된 스냅샷 저장은 거부한다.
배포 `profiles/*.json`은 이 과정에서 수정하지 않는다.
장비 분류별 공통 기능 카드는 설명·데모용이며 실행 권한이 아니다. 최종
분류와 루틴에는 현재 장비에서 operation별 PASS를 받은 모델 기능만 노출한다.
저장된 루틴의 상태·allowlist는 모든 스키마 버전에서 현재 검증 결과와 다시
결합해야 한다.

### FSV/FSVA 공개 범위

`rs_fsv_fsva`는 QCoDeS contrib의 MIT 소스에서 확인한 **14개 capability와
25개 operation**만 배포한다. 현재 범위는 Center, Span, Reference Level,
RBW, VBW, Sweep Time·Continuous, Trigger Source·Level, Correction State,
Input Impedance, Measurement Initiate, ACP Power Fetch와 Reset이다.

Trace 표시·모드, Marker, Detector, Averaging 같은 개념은 다른 분석기에서도
사용하는 범용 기능이므로 `capability_taxonomy.json`과 UI 설계에 남는다.
다만 FSV/FSVA 모델의 실제 SCPI 문자열은 공개 매뉴얼 색인에서 옮겨오지 않는다.
사용자가 보유한 실장비에서 명령·응답·오류 큐·readback·원복을 독립 검증한
경우에만 해당 물리 장비에 묶인 로컬 기능으로 확장한다.

## 중요한 안전 원칙

1. 명시된 read-only query와 오류 큐 확인부터 시작한다.
2. 복원 가능한 write는 현재값 조회 → 시험값 → readback → 원래 값 복원 →
   복원 readback 순서로 검증한다.
3. execute, binary, 파일·메모리 변경, reset, calibration은 자동 일괄
   검증하지 않고 수동 단계로 격리한다.
4. `risk_level=high`는 실행 전 확인 및 사용자 시험 한계를 별도로 적용한다.
5. 전압·전류·RF 전력·Bias는 장비 최대치보다 더 낮은 **시험별 허용치**를 둔다.
6. 통신 오류·사용자 중지 시 배포 프로필의 `safe_shutdown`을 실행하고 성공
   여부를 별도로 기록한다.
7. 배포 프로필 또는 검증된 사용자 로컬 기능에 없는 명령이나 parameter를
   자동 추측해 보내지 않는다.
8. 펌웨어·옵션이 다르면 같은 모델명이어도 검증 결과를 별도 장비 바인딩으로 관리한다.
9. `*RST`와 보호 해제 명령은 안전한 명령으로 간주하지 않는다.
10. 무한 반복·출력 FORCE 정책은 기본 금지한다.
11. 루틴 JSON에 저장된 통과 allowlist만으로 제어 권한을 복원하지 않고 현재
    연결 장비의 raw IDN·펌웨어·옵션·검증 레지스트리와 다시 결합한다.

## 범위의 한계

이건 “전 세계 모든 장비가 완성된 DB”도, 12개 모델이 완전히 지원된다는
목록도 아니다. **기능별 실장비 검증을 시작할 수 있는 1차 seed catalog**다.
12개 프로필은 permissive OSS에서 확인한 기능만 담는다. 390개 구조화
operation도 실장비 검증 `pass` 전에는 지원 기능으로 확정되지 않는다.
제조사 매뉴얼 PDF 원문·OCR·명령 색인·페이지 매핑은 배포 데이터팩에 포함하지
않으며, 공식 링크와 서지정보만 담는다. 사용자 로컬 매뉴얼 추출 결과는
저장소 밖에서만 사용할 수 있다.
