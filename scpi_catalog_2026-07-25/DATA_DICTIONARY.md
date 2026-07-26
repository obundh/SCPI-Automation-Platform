# 데이터 사전

## Profile (배포 프로필)

현재 스키마의 `profile`은 완성된 대표 모델이나 지원 보증서가 아니다.
permissive 오픈소스 드라이버·프로필에서 확인하고 장비에서 다시 검증할
명령·parameter·출처를 모은 배포 프로필이다. 제조사 매뉴얼에서 추출한
명령 색인이나 페이지 매핑은 포함하지 않는다.

| 필드 | 의미 |
|---|---|
| `profile_id` | 프로그램 내부 고유 ID |
| `manufacturer` | 제조사 |
| `model_family` | 제품군 |
| `models` | 프로필 선택을 검토할 모델 후보 |
| `instrument_class` | 장비 종류 |
| `identification.idn_patterns` | `*IDN?` 응답과 매칭할 정규식 |
| `interfaces` | 확인된 통신 인터페이스 |
| `manual_ids` | 문서명·버전·공식 URL 등 공식 문서의 서지정보 참조 |
| `source_ids` | 명령 근거가 된 permissive 코드·프로파일 출처 |
| `verification` | permissive 소스 근거 단계. 실장비 지원 판정과 다름 |
| `model_limits` | 모델·채널·옵션별 한계 |
| `safe_shutdown` | 오류·중단 시 순서대로 실행할 기능 |
| `capabilities` | 공통 기능과 실제 SCPI 바인딩 |

## Capability

| 필드 | 의미 |
|---|---|
| `capability_id` | 제조사에 종속되지 않는 공통 기능 ID |
| `label_ko` | GUI에 표시할 한국어 이름 |
| `category` | 기능 묶음 |
| `operations.set/query/execute` | 실제 SCPI 템플릿 |
| `parameters` | 치환값 타입·단위·범위·enum |
| `response_type` | query 응답 파서 힌트 |
| `risk_level` | low / medium / high |
| `scope` | channel / marker / trace / port 등 반복 대상 |
| `preconditions` | 실행 전 확인 조건 |
| `alternatives` | AUTO 등 특수 입력을 위한 대체 명령 |
| `source_ids` | 근거가 된 원자료 |
| `verification` | 해당 바인딩의 출처 근거 단계. operation 실장비 결과와 다름 |

## User-local manual command candidate

이 스키마는 사용자가 적법하게 사용할 수 있는 매뉴얼로 직접 만든 비공개
후보를 로컬에서 검증하기 위한 것이다. 해당 JSON, 제조사 원문, OCR, 명령
색인과 페이지 매핑은 이 데이터팩에 포함하지 않으며 저장소 밖 사용자 로컬
폴더에서만 읽는다.

| 필드 | 사용자 로컬 파일에서의 의미 |
|---|---|
| `command_pattern` | 사용자가 로컬에서 검토할 명령 후보 |
| `manual_page` | 사용자가 적법하게 보유한 로컬 문서에서 확인할 위치 |
| `query_scpi_candidate` | 검토용 query 후보. 자동 실행 승인 아님 |
| `query_support` | 로컬 문서에 명시된 query인지, 미검증 추정인지 |
| `write_support` | write 지원 확인 상태 |
| `probe_policy` | query 우선, 제한적 query 또는 수동 확인 등 검증 정책 |
| `verification` | 로컬 후보 상태. 기능 완성·실장비 통과를 뜻하지 않음 |

## Final device binding

최종 장비 분류는 카탈로그 원본과 별도로 저장한다.

| 필드 | 의미 |
|---|---|
| `resource`, `idn`, `serial` | 검증한 물리 장비 식별값 |
| `firmware`, `options` | 명령 지원에 영향을 주는 장비 상태 |
| `profile_id`, `catalog_fingerprint` | 검증 기준이 된 배포 프로필 |
| `compatible_operation_ids` | 실장비 기준을 통과해 사용 가능한 operation |
| `incompatible_operation_ids` | 시험 결과 실패한 operation |
| `unresolved_operation_ids` | 미시험·건너뜀·위험·수동 확인 operation |
| operation result | 응답, 오류, readback, 복원, timeout과 검증 시각 |

`low / medium / high` 위험도는 검증 상태와 별개다. 위험도가 낮은 미검증
operation은 실행할 수 없고, 위험도가 높은 통과 operation은 실행 때 추가
안전 조건과 승인이 필요하다.

## SQLite 핵심 테이블

- `profiles`
- `capabilities`
- `operations`
- `manuals`
- `sources`
- `profile_manuals`
- `profile_sources`

## SQLite View

- `v_command_map`: 프로그램이 가장 쉽게 조회할 평탄화 View
- `v_profile_manuals`: 프로필과 공식 문서 서지정보 연결
- `v_high_risk_commands`: 위험 명령만 추출
