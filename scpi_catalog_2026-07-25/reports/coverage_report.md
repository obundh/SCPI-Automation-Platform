# Coverage Report

- Database integrity: `ok`
- Profiles: **12**
- Capabilities: **237**
- Curated operations: **390**
- Bundled manufacturer-manual command candidates: **0**
- High-risk operations: **60**
- Manuals: **12**
- Sources: **15**
- SCPI template placeholder validation: **ok**

## Profiles

| Profile | Capabilities | Operations | Manual candidates | Verification |
|---|---:|---:|---:|---|
| `kikusui_pmx35_3a` | 20 | 27 | 0 | profile_source_confirmed |
| `rs_smb100a` | 15 | 27 | 0 | source_code_confirmed |
| `rs_fsl` | 20 | 32 | 0 | source_code_confirmed |
| `rs_fsw` | 27 | 39 | 0 | source_code_confirmed |
| `rs_fsv_fsva` | 14 | 25 | 0 | source_code_confirmed |
| `keysight_e36312a` | 5 | 8 | 0 | source_code_confirmed |
| `keysight_33500_series` | 23 | 46 | 0 | source_code_confirmed |
| `keysight_344xxa_truevolt` | 20 | 33 | 0 | project_driver_confirmed |
| `rigol_ds1000z` | 32 | 55 | 0 | live_hardware_source_confirmed |
| `keysight_e4980a` | 18 | 29 | 0 | project_driver_confirmed |
| `keysight_n52xx_pna` | 22 | 39 | 0 | project_driver_confirmed |
| `rs_hmp2000_hmp4000` | 21 | 30 | 0 | source_code_confirmed |

## Limitations

- 제조사 매뉴얼 원문, 명령 색인, 페이지 매핑은 배포하지 않는다.
- 모델별 SCPI 바인딩은 라이선스가 확인된 오픈소스 출처만 포함한다.
- 사용자 로컬 매뉴얼 후보는 저장소 밖에서만 관리하며 자동 실행하지 않는다.
- 위험 명령, 파일·메모리 변경, Reset·Calibration은 일괄 자동 검증하지 않는다.
- 오픈소스 드라이버 근거는 해당 물리 장비에서의 동작 보증이 아니다.
