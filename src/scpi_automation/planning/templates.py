from __future__ import annotations

import math
import re
from dataclasses import dataclass
from enum import Enum
from types import MappingProxyType
from typing import Mapping, TypeAlias

from scpi_automation.identity import DeviceCategory
from scpi_automation.routine import SelectedInstrument


PLAN_ASSISTANCE_NOTICE_KO = (
    "이 화면은 표준·사내 절차에 맞춘 측정 계획 작성을 돕는 도구입니다. "
    "표준 준수, 인증 통과 또는 측정 유효성을 보증하지 않습니다. "
    "적용 문서의 최신 판, 장비 교정 상태, 배선, 안전 한계와 합격 기준은 "
    "시험 책임자가 확인해야 합니다."
)

PlanScalar: TypeAlias = str | float | int | bool


class PlanFieldType(str, Enum):
    TEXT = "text"
    MULTILINE = "multiline"
    NUMBER = "number"
    INTEGER = "integer"
    CHOICE = "choice"
    BOOLEAN = "boolean"


@dataclass(frozen=True, slots=True)
class PlanFieldDefinition:
    field_id: str
    label_ko: str
    field_type: PlanFieldType
    help_ko: str
    required: bool = False
    unit: str = ""
    choices: tuple[str, ...] = ()
    default: PlanScalar | None = None
    minimum: float | None = None
    maximum: float | None = None
    must_be_true: bool = False

    def __post_init__(self) -> None:
        if not self.field_id.strip():
            raise ValueError("계획 필드 ID는 비워둘 수 없습니다.")
        if not self.label_ko.strip() or not self.help_ko.strip():
            raise ValueError(f"{self.field_id}의 표시 이름과 도움말이 필요합니다.")
        if self.field_type is PlanFieldType.CHOICE and not self.choices:
            raise ValueError(f"{self.field_id} 선택 필드에는 선택지가 필요합니다.")
        if self.field_type is not PlanFieldType.CHOICE and self.choices:
            raise ValueError(f"{self.field_id}에는 선택지를 지정할 수 없습니다.")
        if self.must_be_true and self.field_type is not PlanFieldType.BOOLEAN:
            raise ValueError("must_be_true는 boolean 필드에만 사용할 수 있습니다.")
        if (
            self.minimum is not None
            and self.maximum is not None
            and self.minimum > self.maximum
        ):
            raise ValueError(f"{self.field_id}의 최소값이 최대값보다 큽니다.")

    def normalize(self, raw_value: object) -> PlanScalar | None:
        if self.field_type in {PlanFieldType.TEXT, PlanFieldType.MULTILINE}:
            if raw_value is None:
                value = ""
            elif isinstance(raw_value, str):
                value = raw_value.strip()
            else:
                raise ValueError(f"{self.label_ko}은(는) 글자로 입력해 주세요.")
            if self.required and not value:
                raise ValueError(f"{self.label_ko}을(를) 입력해 주세요.")
            return value or None

        if self.field_type is PlanFieldType.CHOICE:
            if raw_value is None:
                value = ""
            elif isinstance(raw_value, str):
                value = raw_value.strip()
            else:
                raise ValueError(f"{self.label_ko} 선택값이 올바르지 않습니다.")
            if not value and not self.required:
                return None
            if value not in self.choices:
                raise ValueError(
                    f"{self.label_ko}은(는) 제공된 선택지에서 골라 주세요."
                )
            return value

        if self.field_type is PlanFieldType.BOOLEAN:
            value = self._normalize_boolean(raw_value)
            if self.must_be_true and not value:
                raise ValueError(f"{self.label_ko}을(를) 확인해야 계획을 만들 수 있어요.")
            return value

        if raw_value is None or (
            isinstance(raw_value, str) and not raw_value.strip()
        ):
            if self.required:
                raise ValueError(f"{self.label_ko} 값을 입력해 주세요.")
            return None
        if isinstance(raw_value, bool):
            raise ValueError(f"{self.label_ko}은(는) 숫자로 입력해 주세요.")

        if self.field_type is PlanFieldType.INTEGER:
            normalized_text: str | None = None
            if isinstance(raw_value, str):
                normalized_text = self._normalized_numeric_text(raw_value)
            try:
                if normalized_text is not None:
                    if any(character in normalized_text for character in ".eE"):
                        raise ValueError
                    value: int | float = int(normalized_text)
                elif isinstance(raw_value, int):
                    value = raw_value
                elif isinstance(raw_value, float) and raw_value.is_integer():
                    value = int(raw_value)
                else:
                    raise ValueError
            except (TypeError, ValueError, OverflowError) as exc:
                raise ValueError(f"{self.label_ko}은(는) 정수로 입력해 주세요.") from exc
        else:
            normalized_text = (
                self._normalized_numeric_text(raw_value)
                if isinstance(raw_value, str)
                else None
            )
            try:
                if normalized_text is not None:
                    value = float(normalized_text)
                else:
                    value = float(raw_value)
            except (TypeError, ValueError, OverflowError) as exc:
                raise ValueError(f"{self.label_ko}은(는) 숫자로 입력해 주세요.") from exc
            if not math.isfinite(value):
                raise ValueError(f"{self.label_ko}은(는) 유한한 숫자여야 합니다.")

        if self.minimum is not None and value < self.minimum:
            raise ValueError(
                f"{self.label_ko}은(는) {self.minimum:g}{self._unit_suffix()} 이상이어야 합니다."
            )
        if self.maximum is not None and value > self.maximum:
            raise ValueError(
                f"{self.label_ko}은(는) {self.maximum:g}{self._unit_suffix()} 이하여야 합니다."
            )
        return value

    def _normalized_numeric_text(self, raw_value: str) -> str:
        """Accept valid thousands separators, but never reinterpret ``1,5``."""

        normalized = raw_value.strip()
        if "," not in normalized:
            return normalized
        if not re.fullmatch(
            r"[+-]?\d{1,3}(?:,\d{3})+(?:\.\d+)?(?:[eE][+-]?\d+)?",
            normalized,
        ):
            raise ValueError(
                f"{self.label_ko}의 쉼표는 천 단위(예: 1,000)로만 입력해 주세요."
            )
        return normalized.replace(",", "")

    @staticmethod
    def _normalize_boolean(raw_value: object) -> bool:
        if isinstance(raw_value, bool):
            return raw_value
        if isinstance(raw_value, str):
            normalized = raw_value.strip().casefold()
            if normalized in {"1", "true", "yes", "on", "예", "확인"}:
                return True
            if normalized in {"0", "false", "no", "off", "아니요", ""}:
                return False
        raise ValueError("확인 항목은 체크 여부로 입력해 주세요.")

    def _unit_suffix(self) -> str:
        return f" {self.unit}" if self.unit else ""


@dataclass(frozen=True, slots=True)
class PlanMethodTemplate:
    method_id: str
    label_ko: str
    purpose_ko: str
    procedure_steps: tuple[str, ...]
    consideration_ids: tuple[str, ...]
    expected_results: tuple[str, ...]
    recommended_values: tuple[tuple[str, PlanScalar], ...] = ()

    def __post_init__(self) -> None:
        if not self.method_id.strip() or not self.label_ko.strip():
            raise ValueError("시험 방법에는 ID와 표시 이름이 필요합니다.")
        if not self.purpose_ko.strip():
            raise ValueError(f"{self.method_id} 시험 방법의 목적이 필요합니다.")
        if not self.procedure_steps or not all(
            step.strip() for step in self.procedure_steps
        ):
            raise ValueError(f"{self.method_id} 시험 방법의 절차 단계가 필요합니다.")
        if not self.consideration_ids:
            raise ValueError(f"{self.method_id} 시험 방법의 고려 필드가 필요합니다.")
        if not self.expected_results:
            raise ValueError(f"{self.method_id} 시험 방법의 예정 결과가 필요합니다.")
        recommended_ids = tuple(
            field_id for field_id, _value in self.recommended_values
        )
        if len(recommended_ids) != len(set(recommended_ids)):
            raise ValueError(
                f"{self.method_id} 시험 방법의 권장값 필드가 중복되었습니다."
            )


@dataclass(frozen=True, slots=True)
class CategoryPlanTemplate:
    category: DeviceCategory
    summary_ko: str
    standard_examples: tuple[str, ...]
    methods: tuple[PlanMethodTemplate, ...]
    detail_fields: tuple[PlanFieldDefinition, ...]

    def __post_init__(self) -> None:
        if self.category is DeviceCategory.UNKNOWN:
            raise ValueError("미분류 장비에는 계획 템플릿을 만들 수 없습니다.")
        if (
            not self.summary_ko.strip()
            or not self.standard_examples
            or not all(example.strip() for example in self.standard_examples)
            or not self.methods
            or not self.detail_fields
        ):
            raise ValueError(f"{self.category.value} 계획 템플릿이 비어 있습니다.")
        field_ids = tuple(field.field_id for field in self.detail_fields)
        if len(field_ids) != len(set(field_ids)):
            raise ValueError(f"{self.category.value} 상세 필드 ID가 중복되었습니다.")
        method_ids = tuple(method.method_id for method in self.methods)
        if len(method_ids) != len(set(method_ids)):
            raise ValueError(f"{self.category.value} 시험 방법 ID가 중복되었습니다.")
        known_fields = set(field_ids)
        fields_by_id = {
            field.field_id: field for field in self.detail_fields
        }
        for method in self.methods:
            unknown = set(method.consideration_ids) - known_fields
            if unknown:
                raise ValueError(
                    f"{method.method_id}에 알 수 없는 상세 필드가 있습니다: "
                    f"{', '.join(sorted(unknown))}"
                )
            unknown_recommended = {
                field_id
                for field_id, _value in method.recommended_values
            } - set(method.consideration_ids)
            if unknown_recommended:
                raise ValueError(
                    f"{method.method_id}의 권장값이 고려 필드에 없습니다: "
                    f"{', '.join(sorted(unknown_recommended))}"
                )
            for field_id, recommended_value in method.recommended_values:
                fields_by_id[field_id].normalize(recommended_value)

    def method_by_id(self, method_id: str) -> PlanMethodTemplate:
        for method in self.methods:
            if method.method_id == method_id:
                return method
        raise KeyError(f"등록되지 않은 시험 방법입니다: {method_id}")

    def fields_for_method(self, method_id: str) -> tuple[PlanFieldDefinition, ...]:
        method = self.method_by_id(method_id)
        fields_by_id = {field.field_id: field for field in self.detail_fields}
        return tuple(fields_by_id[field_id] for field_id in method.consideration_ids)


def _field(
    field_id: str,
    label_ko: str,
    field_type: PlanFieldType,
    help_ko: str,
    *,
    required: bool = False,
    unit: str = "",
    choices: tuple[str, ...] = (),
    default: PlanScalar | None = None,
    minimum: float | None = None,
    maximum: float | None = None,
    must_be_true: bool = False,
) -> PlanFieldDefinition:
    return PlanFieldDefinition(
        field_id=field_id,
        label_ko=label_ko,
        field_type=field_type,
        help_ko=help_ko,
        required=required,
        unit=unit,
        choices=choices,
        default=default,
        minimum=minimum,
        maximum=maximum,
        must_be_true=must_be_true,
    )


COMMON_PLAN_FIELDS = (
    _field(
        "standard_procedure",
        "적용 표준·절차서",
        PlanFieldType.TEXT,
        "표준 번호·판, 고객 규격 또는 사내 SOP 문서와 개정 번호를 적어요.",
        required=True,
    ),
    _field(
        "sample_description",
        "시료·DUT",
        PlanFieldType.MULTILINE,
        "모델, 시리얼, HW/SW 버전, 포트와 동작 상태를 식별할 수 있게 적어요.",
        required=True,
    ),
    _field(
        "environment_conditions",
        "환경·전원 조건",
        PlanFieldType.MULTILINE,
        "온도, 습도, 전원, 차폐·접지와 같이 결과에 영향을 줄 조건을 적어요.",
        required=True,
        default="온도·습도·전원·접지 조건을 시험 시작 전 기록",
    ),
    _field(
        "stabilization_seconds",
        "안정화 시간",
        PlanFieldType.NUMBER,
        "장비와 시료가 지정 상태에 도달한 뒤 측정을 시작할 때까지의 시간이에요.",
        required=True,
        unit="s",
        default=60,
        minimum=0,
        maximum=86_400,
    ),
    _field(
        "repeat_count",
        "반복 횟수",
        PlanFieldType.INTEGER,
        "재현성 확인과 통계 처리를 위해 같은 조건을 반복할 횟수예요.",
        required=True,
        unit="회",
        default=1,
        minimum=1,
        maximum=10_000,
    ),
    _field(
        "acceptance_criteria",
        "합격 기준",
        PlanFieldType.MULTILINE,
        "상·하한, 오차, 판정식과 반올림 규칙을 절차서 표현 그대로 적어요.",
        required=True,
    ),
    _field(
        "calibration_status",
        "교정·검증 상태",
        PlanFieldType.CHOICE,
        "교정 유효기간과 필요한 자가점검·기준기 확인 상태를 고르세요.",
        required=True,
        choices=(
            "교정 유효 확인",
            "교정 예정·확인 필요",
            "내부 기준기·자가점검",
            "해당 없음",
        ),
        default="교정 예정·확인 필요",
    ),
    _field(
        "safety_confirmed",
        "안전 조건 확인",
        PlanFieldType.BOOLEAN,
        "정격, 배선, 감쇠, 접지, 출력 OFF와 비상 중지 방법을 확인한 뒤 체크하세요.",
        required=True,
        default=False,
        must_be_true=True,
    ),
)


SPECTRUM_FIELDS = (
    _field(
        "frequency_plan",
        "주파수 구간·목록",
        PlanFieldType.TEXT,
        "Center/Span 또는 Start/Stop과 측정 포인트를 적어요.",
        required=True,
        default="Center 1 GHz, Span 100 MHz",
    ),
    _field(
        "rbw_mode",
        "RBW 방식",
        PlanFieldType.CHOICE,
        "표준의 RBW 지정값과 장비 자동 설정 중 하나를 고르세요.",
        required=True,
        choices=("수동 지정", "장비 자동", "표준별 단계 변경"),
        default="수동 지정",
    ),
    _field(
        "rbw_hz",
        "RBW",
        PlanFieldType.NUMBER,
        "분해능과 Noise Floor, Sweep Time의 균형을 고려해 정해요.",
        unit="Hz",
        default=100_000,
        minimum=0.001,
    ),
    _field(
        "vbw_mode",
        "VBW 방식",
        PlanFieldType.CHOICE,
        "VBW 자동 또는 수동값과 RBW 대비 비율을 절차에 맞춰 정해요.",
        required=True,
        choices=("수동 지정", "장비 자동", "RBW 비율 적용"),
        default="장비 자동",
    ),
    _field(
        "vbw_hz",
        "VBW",
        PlanFieldType.NUMBER,
        "표시 노이즈 평활 정도와 측정 시간을 함께 고려해요.",
        unit="Hz",
        minimum=0.001,
    ),
    _field(
        "detector",
        "Detector",
        PlanFieldType.CHOICE,
        "Peak, RMS, Sample 등 표준이 요구하는 검파기를 고르세요.",
        required=True,
        choices=(
            "Positive Peak",
            "Negative Peak",
            "RMS",
            "Sample",
            "Average",
            "Quasi-Peak",
            "Auto",
        ),
        default="Positive Peak",
    ),
    _field(
        "trace_mode",
        "Trace 모드",
        PlanFieldType.CHOICE,
        "Clear/Write, Max Hold, Average 등 결과 집계 방식을 정해요.",
        required=True,
        choices=("Clear/Write", "Max Hold", "Min Hold", "Average", "View"),
        default="Clear/Write",
    ),
    _field(
        "sweep_time_seconds",
        "Sweep Time",
        PlanFieldType.NUMBER,
        "자동값 사용 여부와 신호 변화 속도를 고려한 시간을 적어요.",
        unit="s",
        minimum=0.000001,
        maximum=10_000,
    ),
    _field(
        "sweep_count",
        "Sweep·평균 횟수",
        PlanFieldType.INTEGER,
        "Max Hold나 Average가 충분히 수렴하도록 횟수를 정해요.",
        required=True,
        unit="회",
        default=1,
        minimum=1,
        maximum=100_000,
    ),
    _field(
        "scan_mode",
        "Scan 방식",
        PlanFieldType.CHOICE,
        "Swept, Stepped, FFT 기반 Scan을 적용 절차와 장비 기능에 맞춰 고르세요.",
        choices=("Swept", "Stepped", "FFT/Time Domain", "수동 주파수 목록"),
        default="Swept",
    ),
    _field(
        "dwell_seconds",
        "주파수별 Dwell",
        PlanFieldType.NUMBER,
        "간헐 신호 탐색과 Detector 응답에 필요한 포인트별 관찰 시간이에요.",
        unit="s",
        minimum=0,
        maximum=3600,
    ),
    _field(
        "reference_level_dbm",
        "Reference Level",
        PlanFieldType.NUMBER,
        "포화·오버로드를 피하면서 충분한 동적 범위를 확보해요.",
        required=True,
        unit="dBm",
        default=0,
        minimum=-160,
        maximum=50,
    ),
    _field(
        "input_attenuation_db",
        "입력 감쇠",
        PlanFieldType.NUMBER,
        "예상 최대 입력과 Mixer Level을 고려해 정해요.",
        unit="dB",
        default=10,
        minimum=0,
        maximum=100,
    ),
    _field(
        "preamp_state",
        "Preamp",
        PlanFieldType.CHOICE,
        "저레벨 측정에서만 사용하고 최대 허용 입력을 다시 확인해요.",
        required=True,
        choices=("OFF", "ON", "장비 자동", "지원 여부 확인"),
        default="OFF",
    ),
    _field(
        "overload_check",
        "입력 과부하 점검",
        PlanFieldType.BOOLEAN,
        "Attenuation 또는 Preamp 상태를 바꿔 결과가 유의하게 달라지는지 확인하세요.",
        required=True,
        default=False,
        must_be_true=True,
    ),
    _field(
        "trigger_source",
        "Trigger",
        PlanFieldType.CHOICE,
        "연속파, Burst, Pulse 신호 특성에 맞는 트리거를 고르세요.",
        required=True,
        choices=("Immediate", "External", "Video", "RF Power", "지원 여부 확인"),
        default="Immediate",
    ),
    _field(
        "marker_strategy",
        "Marker·Peak 판정",
        PlanFieldType.CHOICE,
        "최대 Peak, Next Peak, 고정 Marker 또는 Delta 방식 중 선택해요.",
        required=True,
        choices=("최대 Peak", "Next Peak 목록", "고정 Marker", "Delta Marker"),
        default="최대 Peak",
    ),
    _field(
        "peak_threshold_db",
        "Peak Threshold",
        PlanFieldType.NUMBER,
        "Noise Floor 아래의 불필요한 Peak가 목록에 들어오지 않도록 기준을 정해요.",
        unit="dB",
        minimum=-300,
        maximum=300,
    ),
    _field(
        "peak_excursion_db",
        "Peak Excursion",
        PlanFieldType.NUMBER,
        "인접 Peak를 별도로 인식할 최소 레벨 차이를 정해요.",
        unit="dB",
        minimum=0,
        maximum=300,
    ),
    _field(
        "emi_receiver_mode",
        "EMI Receiver·검파 조건",
        PlanFieldType.TEXT,
        "CISPR 계열 적용 시 Frequency Band별 RBW, Detector, Dwell과 Scan Table을 적어요.",
    ),
    _field(
        "trace_capture",
        "Trace 데이터 저장",
        PlanFieldType.BOOLEAN,
        "화면값뿐 아니라 추적 가능한 Trace 배열과 설정값을 저장할지 정해요.",
        default=True,
    ),
    _field(
        "measurement_bandwidth_hz",
        "측정·채널 대역폭",
        PlanFieldType.NUMBER,
        "Channel Power, OBW, ACP 계산에 사용할 대역폭이에요.",
        unit="Hz",
        minimum=0.001,
    ),
    _field(
        "channel_spacing_hz",
        "채널 간격",
        PlanFieldType.NUMBER,
        "ACP 등 인접 채널 측정의 Offset 또는 채널 간격이에요.",
        unit="Hz",
        minimum=0,
    ),
    _field(
        "path_loss_db",
        "케이블·경로 보정",
        PlanFieldType.NUMBER,
        "케이블, Attenuator, Coupler의 주파수별 손실 부호와 기준면을 확인해요.",
        unit="dB",
        default=0,
        minimum=-200,
        maximum=200,
    ),
    _field(
        "path_loss_table",
        "주파수별 경로 보정표",
        PlanFieldType.MULTILINE,
        "Cable·Attenuator·Antenna·LISN·Transducer의 주파수별 보정값, 부호와 기준면을 적어요.",
    ),
    _field(
        "obw_percent",
        "OBW 전력 백분율",
        PlanFieldType.NUMBER,
        "점유대역폭 계산에 사용할 포함 전력 비율을 적용 절차서와 맞춰요.",
        unit="%",
        default=99,
        minimum=0.001,
        maximum=100,
    ),
    _field(
        "acp_offset_definition",
        "ACP Offset·대역폭",
        PlanFieldType.MULTILINE,
        "인접·차인접 채널별 Offset, 적분 대역폭, 개수와 방향을 적어요.",
    ),
    _field(
        "emi_unit_transducer",
        "EMI 단위·Transducer 보정",
        PlanFieldType.MULTILINE,
        "dBµV, dBµV/m 등 결과 단위와 Antenna/LISN/Probe factor, Cable loss 적용 순서를 적어요.",
    ),
    _field(
        "limit_definition",
        "Limit·검색 구간",
        PlanFieldType.MULTILINE,
        "주파수별 제한값, 제외 구간, Harmonic 번호와 판정 여유를 적어요.",
    ),
)

SPECTRUM_METHODS = (
    PlanMethodTemplate(
        "spectrum_level",
        "주파수·레벨 및 Trace 확인",
        "관심 대역의 신호 위치, 최대 레벨과 전체 Trace를 확인합니다.",
        (
            "입력 경로 손실과 예상 최대 레벨을 확인하고 입력 감쇠를 정합니다.",
            "주파수 범위, RBW/VBW, Detector와 Trace 모드를 적용합니다.",
            "Single Sweep 완료 후 Marker와 Trace를 읽고 설정 Readback을 기록합니다.",
        ),
        (
            "frequency_plan",
            "rbw_mode",
            "rbw_hz",
            "vbw_mode",
            "vbw_hz",
            "detector",
            "trace_mode",
            "sweep_time_seconds",
            "sweep_count",
            "scan_mode",
            "dwell_seconds",
            "reference_level_dbm",
            "input_attenuation_db",
            "preamp_state",
            "overload_check",
            "trigger_source",
            "marker_strategy",
            "peak_threshold_db",
            "peak_excursion_db",
            "trace_capture",
            "path_loss_db",
            "path_loss_table",
        ),
        ("Marker 주파수·레벨", "Trace 데이터", "적용 설정 Readback"),
    ),
    PlanMethodTemplate(
        "channel_power_obw",
        "Channel Power·점유 대역폭",
        "지정 채널 내 전력과 점유 대역폭을 절차서의 적분·백분율 기준으로 평가합니다.",
        (
            "채널 중심, 측정 대역폭과 필요한 인접 채널 Offset을 확인합니다.",
            "RMS Detector와 평균 조건 등 적용 문서의 측정 조건을 설정합니다.",
            "Channel Power·OBW 결과와 Trace, 장비 설정을 함께 기록합니다.",
        ),
        (
            "frequency_plan",
            "measurement_bandwidth_hz",
            "channel_spacing_hz",
            "obw_percent",
            "acp_offset_definition",
            "rbw_mode",
            "rbw_hz",
            "vbw_mode",
            "vbw_hz",
            "detector",
            "trace_mode",
            "sweep_count",
            "scan_mode",
            "dwell_seconds",
            "reference_level_dbm",
            "input_attenuation_db",
            "overload_check",
            "path_loss_db",
            "path_loss_table",
            "limit_definition",
            "trace_capture",
        ),
        ("Channel Power", "점유 대역폭과 경계 주파수", "Trace·설정 Readback"),
        recommended_values=(("detector", "RMS"),),
    ),
    PlanMethodTemplate(
        "spurious_harmonic",
        "불요파·고조파 탐색",
        "넓은 주파수 범위에서 Harmonic과 비의도 방사를 제한값과 비교합니다.",
        (
            "기본파 포화 방지와 대역별 경로 손실·외부 Filter 조건을 확인합니다.",
            "표준별 주파수 구간에 맞춰 RBW/VBW, Detector와 Sweep 조건을 바꿉니다.",
            "Max Hold 또는 반복 Sweep 후 Peak 목록과 Trace를 Limit과 비교합니다.",
        ),
        (
            "frequency_plan",
            "rbw_mode",
            "rbw_hz",
            "vbw_mode",
            "vbw_hz",
            "detector",
            "trace_mode",
            "sweep_time_seconds",
            "sweep_count",
            "scan_mode",
            "dwell_seconds",
            "reference_level_dbm",
            "input_attenuation_db",
            "preamp_state",
            "overload_check",
            "trigger_source",
            "marker_strategy",
            "peak_threshold_db",
            "peak_excursion_db",
            "trace_capture",
            "path_loss_db",
            "path_loss_table",
            "limit_definition",
        ),
        ("주파수별 Peak 목록", "기본파 대비 dBc 또는 절대 레벨", "Limit 판정"),
    ),
    PlanMethodTemplate(
        "emi_cispr_assist",
        "CISPR 계열 EMI Pre-scan·후보 주파수 확인",
        "CISPR 계열 절차의 Band별 Scan과 Peak 후보 선별을 보조하되, "
        "인증용 Receiver·LISN·Antenna·Site 적합성은 별도로 확인합니다.",
        (
            "적용 CISPR 문서의 최신 판, 주파수 Band, RBW와 Detector 순서를 확인합니다.",
            "LISN·Antenna·Cable·Site 보정과 입력 과부하·Preamp·Attenuation을 점검합니다.",
            "Pre-scan Trace에서 후보 Peak를 선별하고 요구 Detector·Dwell로 재측정합니다.",
        ),
        (
            "frequency_plan",
            "emi_receiver_mode",
            "rbw_mode",
            "rbw_hz",
            "vbw_mode",
            "vbw_hz",
            "detector",
            "trace_mode",
            "scan_mode",
            "dwell_seconds",
            "sweep_time_seconds",
            "sweep_count",
            "reference_level_dbm",
            "input_attenuation_db",
            "preamp_state",
            "overload_check",
            "marker_strategy",
            "peak_threshold_db",
            "peak_excursion_db",
            "trace_capture",
            "path_loss_db",
            "path_loss_table",
            "emi_unit_transducer",
            "limit_definition",
        ),
        ("Band별 Pre-scan Trace", "후보 주파수·Detector 결과", "보정·과부하 점검 기록"),
    ),
)


RF_GENERATOR_FIELDS = (
    _field(
        "frequency_plan",
        "출력 주파수·목록",
        PlanFieldType.TEXT,
        "CW 값 또는 Start/Stop/Step/List 순서를 적어요.",
        required=True,
        default="1 GHz CW",
    ),
    _field(
        "power_dbm",
        "출력 레벨",
        PlanFieldType.NUMBER,
        "DUT 기준면에서 필요한 값과 경로 손실을 구분해 적어요.",
        required=True,
        unit="dBm",
        default=-30,
        minimum=-200,
        maximum=50,
    ),
    _field(
        "phase_degrees",
        "위상",
        PlanFieldType.NUMBER,
        "상대 위상 기준이 있을 때 사용해요.",
        unit="deg",
        default=0,
        minimum=-360,
        maximum=360,
    ),
    _field(
        "dwell_seconds",
        "Dwell",
        PlanFieldType.NUMBER,
        "각 주파수에서 DUT와 측정기가 안정화될 시간을 정해요.",
        required=True,
        unit="s",
        default=1,
        minimum=0.001,
        maximum=3600,
    ),
    _field(
        "frequency_mode",
        "주파수 모드",
        PlanFieldType.CHOICE,
        "CW, 장비 Sweep 또는 PC 반복 설정 방식을 고르세요.",
        required=True,
        choices=("CW", "균일 Sweep", "주파수 List", "PC 단계 반복"),
        default="CW",
    ),
    _field(
        "sweep_start_hz",
        "Sweep 시작",
        PlanFieldType.NUMBER,
        "Sweep를 사용할 때의 시작 주파수예요.",
        unit="Hz",
        minimum=0.001,
    ),
    _field(
        "sweep_stop_hz",
        "Sweep 종료",
        PlanFieldType.NUMBER,
        "Sweep를 사용할 때의 종료 주파수예요.",
        unit="Hz",
        minimum=0.001,
    ),
    _field(
        "sweep_step_hz",
        "Sweep 간격",
        PlanFieldType.NUMBER,
        "포인트 수와 함께 실제 생성값을 검산해요.",
        unit="Hz",
        minimum=0.001,
    ),
    _field(
        "sweep_points",
        "Sweep 포인트",
        PlanFieldType.INTEGER,
        "장비 Start/Stop/Step 정의와 모순되지 않도록 정해요.",
        unit="개",
        minimum=2,
        maximum=1_000_000,
    ),
    _field(
        "pulse_modulation",
        "Pulse Modulation",
        PlanFieldType.CHOICE,
        "Pulse ON/OFF와 내부·외부 Source 조건을 확인해요.",
        required=True,
        choices=("OFF", "내부 Pulse", "외부 Pulse", "지원 여부 확인"),
        default="OFF",
    ),
    _field(
        "trigger_source",
        "Sweep·Pulse Trigger",
        PlanFieldType.CHOICE,
        "자동, 단발, 외부 동기 중 시험 구성에 맞는 Source를 고르세요.",
        required=True,
        choices=("Auto", "Single/Bus", "External", "지원 여부 확인"),
        default="Auto",
    ),
    _field(
        "reference_clock",
        "기준 Clock",
        PlanFieldType.CHOICE,
        "주파수 정확도가 중요하면 내부·외부 10 MHz Lock 상태를 기록해요.",
        required=True,
        choices=("Internal", "External 10 MHz", "공통 기준기", "확인 필요"),
        default="Internal",
    ),
    _field(
        "load_impedance_ohm",
        "부하 임피던스",
        PlanFieldType.CHOICE,
        "발생기 표시 레벨과 DUT 종단 조건이 일치하는지 확인해요.",
        required=True,
        choices=("50 Ω", "75 Ω", "High-Z", "별도 Matching"),
        default="50 Ω",
    ),
    _field(
        "inline_attenuation_db",
        "외부 감쇠·경로 손실",
        PlanFieldType.NUMBER,
        "Cable·Attenuator·Switch 손실과 보상 여부를 구분해요.",
        unit="dB",
        default=0,
        minimum=-200,
        maximum=200,
    ),
    _field(
        "dut_level_correction_db",
        "DUT 입력단 레벨 보정",
        PlanFieldType.NUMBER,
        "발생기 설정값에서 Cable·Switch·Attenuator·Coupler를 거쳐 DUT에 도달하는 보정값과 부호를 적어요.",
        unit="dB",
        default=0,
        minimum=-300,
        maximum=300,
    ),
    _field(
        "frequency_level_correction_table",
        "주파수별 실제 레벨 보정표",
        PlanFieldType.MULTILINE,
        "각 주파수의 발생기 설정값, 기준면 측정값, 경로 손실과 적용 보정값을 적어요.",
    ),
    _field(
        "pulse_period_seconds",
        "RF Pulse Period",
        PlanFieldType.NUMBER,
        "내부 또는 외부 Pulse의 반복 주기를 적어요.",
        unit="s",
        default=0.001,
        minimum=0.000000001,
    ),
    _field(
        "pulse_width_seconds",
        "RF Pulse Width",
        PlanFieldType.NUMBER,
        "DUT가 실제로 받는 RF ON 구간을 적어요.",
        unit="s",
        default=0.0005,
        minimum=0.000000001,
    ),
    _field(
        "pulse_delay_seconds",
        "RF Pulse Delay",
        PlanFieldType.NUMBER,
        "Trigger 기준부터 RF Pulse 시작까지의 지연을 적어요.",
        unit="s",
        minimum=0,
    ),
    _field(
        "pulse_polarity",
        "RF Pulse Polarity",
        PlanFieldType.CHOICE,
        "외부 Gate·Pulse 입력의 활성 극성을 확인해요.",
        choices=("Positive", "Negative", "지원 여부 확인"),
        default="Positive",
    ),
    _field(
        "pulse_transition_seconds",
        "RF Pulse Rise·Fall",
        PlanFieldType.TEXT,
        "상승·하강 시간 요구, 측정 대역폭과 확인 계측기를 적어요.",
    ),
    _field(
        "reference_lock_confirmed",
        "외부 기준 Lock 확인",
        PlanFieldType.BOOLEAN,
        "외부 10 MHz 또는 공통 기준기를 쓸 때 Lock 상태와 기준기 교정 상태를 확인하세요.",
        default=False,
    ),
    _field(
        "reference_plane",
        "출력 기준면",
        PlanFieldType.TEXT,
        "발생기 단자, 케이블 끝, Fixture 또는 DUT 입력 중 어디인지 적어요.",
        required=True,
        default="DUT 입력 커넥터",
    ),
    _field(
        "monitor_instrument",
        "출력 확인 계측기",
        PlanFieldType.TEXT,
        "파워미터·분석기와 Sensor/Attenuator 구성, 교정 상태를 적어요.",
    ),
    _field(
        "output_initially_off",
        "RF 출력 OFF 확인",
        PlanFieldType.BOOLEAN,
        "주파수·레벨·배선을 검토하기 전 RF가 OFF인지 확인하세요.",
        required=True,
        default=False,
        must_be_true=True,
    ),
    _field(
        "limit_definition",
        "허용 오차·DUT 한계",
        PlanFieldType.MULTILINE,
        "주파수·레벨 오차와 DUT 최대 입력을 적어요.",
    ),
)

RF_GENERATOR_METHODS = (
    PlanMethodTemplate(
        "rf_cw_output",
        "CW 주파수·출력 레벨 확인",
        "지정 CW 조건의 설정 Readback과 기준면 실제 레벨을 외부 측정기로 비교합니다.",
        (
            "RF OFF에서 배선, 부하, 감쇠와 DUT 최대 입력을 확인합니다.",
            "주파수·최소 안전 레벨을 먼저 적용하고 Readback을 확인합니다.",
            "RF ON 후 외부 계측값을 기록하고 종료 시 OFF 상태를 재확인합니다.",
        ),
        (
            "frequency_plan",
            "power_dbm",
            "phase_degrees",
            "dwell_seconds",
            "frequency_mode",
            "reference_clock",
            "load_impedance_ohm",
            "inline_attenuation_db",
            "dut_level_correction_db",
            "frequency_level_correction_table",
            "reference_lock_confirmed",
            "reference_plane",
            "monitor_instrument",
            "output_initially_off",
            "limit_definition",
        ),
        ("주파수·레벨 설정 Readback", "기준면 측정 레벨", "RF OFF 종료 결과"),
    ),
    PlanMethodTemplate(
        "rf_frequency_sweep",
        "주파수 Sweep 응답",
        "주파수 범위의 DUT 응답 또는 발생기 평탄도를 일정 간격으로 확인합니다.",
        (
            "Start/Stop/Step/Point의 일관성과 각 포인트 Dwell을 확인합니다.",
            "출력 레벨과 경로 손실을 안전 한계 이내로 고정합니다.",
            "각 포인트의 Readback·외부 측정 결과와 누락·Timeout을 기록합니다.",
        ),
        (
            "frequency_plan",
            "power_dbm",
            "dwell_seconds",
            "frequency_mode",
            "sweep_start_hz",
            "sweep_stop_hz",
            "sweep_step_hz",
            "sweep_points",
            "trigger_source",
            "reference_clock",
            "load_impedance_ohm",
            "inline_attenuation_db",
            "dut_level_correction_db",
            "frequency_level_correction_table",
            "reference_lock_confirmed",
            "reference_plane",
            "monitor_instrument",
            "output_initially_off",
            "limit_definition",
        ),
        ("포인트별 주파수·레벨", "DUT 또는 평탄도 응답", "누락·오류 기록"),
    ),
    PlanMethodTemplate(
        "rf_pulse_output",
        "Pulse RF 동작 확인",
        "Pulse Modulation의 동기, ON/OFF 상태와 반복 측정 조건을 확인합니다.",
        (
            "Pulse Source와 Trigger 배선·Level·Polarity를 확인합니다.",
            "RF OFF에서 Carrier 조건을 적용한 뒤 Pulse를 활성화합니다.",
            "오실로스코프·분석기로 시간·주파수 영역 결과를 함께 기록합니다.",
        ),
        (
            "frequency_plan",
            "power_dbm",
            "dwell_seconds",
            "pulse_modulation",
            "pulse_period_seconds",
            "pulse_width_seconds",
            "pulse_delay_seconds",
            "pulse_polarity",
            "pulse_transition_seconds",
            "trigger_source",
            "reference_clock",
            "load_impedance_ohm",
            "inline_attenuation_db",
            "dut_level_correction_db",
            "frequency_level_correction_table",
            "reference_lock_confirmed",
            "reference_plane",
            "monitor_instrument",
            "output_initially_off",
            "limit_definition",
        ),
        ("Pulse 상태·Trigger 조건", "Carrier 설정 Readback", "외부 시간영역 결과"),
    ),
)


FUNCTION_GENERATOR_FIELDS = (
    _field(
        "channel",
        "출력 채널",
        PlanFieldType.INTEGER,
        "실제 모델의 채널 수와 배선 채널을 확인해요.",
        required=True,
        default=1,
        minimum=1,
        maximum=8,
    ),
    _field(
        "waveform_shape",
        "파형 종류",
        PlanFieldType.CHOICE,
        "Sine, Square, Pulse, ARB 등 시험 목적에 맞는 파형을 고르세요.",
        required=True,
        choices=("Sine", "Square", "Triangle", "Ramp", "Pulse", "Noise", "ARB", "DC"),
        default="Sine",
    ),
    _field(
        "frequency_hz",
        "주파수",
        PlanFieldType.NUMBER,
        "파형 종류별 최대 주파수와 샘플링 한계를 확인해요.",
        required=True,
        unit="Hz",
        default=1000,
        minimum=0.000001,
    ),
    _field(
        "amplitude_value",
        "진폭",
        PlanFieldType.NUMBER,
        "부하 조건에서의 Vpp, Vrms 또는 dBm 값을 적어요.",
        required=True,
        default=1,
        minimum=-200,
        maximum=1000,
    ),
    _field(
        "amplitude_unit",
        "진폭 단위",
        PlanFieldType.CHOICE,
        "표시 단위가 부하 임피던스와 일치하는지 확인해요.",
        required=True,
        choices=("Vpp", "Vrms", "dBm"),
        default="Vpp",
    ),
    _field(
        "offset_v",
        "DC Offset",
        PlanFieldType.NUMBER,
        "진폭과 합산된 High/Low 전압이 DUT 범위를 넘지 않게 해요.",
        unit="V",
        default=0,
    ),
    _field(
        "high_level_v",
        "High 전압",
        PlanFieldType.NUMBER,
        "High/Low 방식으로 지정할 때의 상한 전압이에요.",
        unit="V",
    ),
    _field(
        "low_level_v",
        "Low 전압",
        PlanFieldType.NUMBER,
        "High/Low 방식으로 지정할 때의 하한 전압이에요.",
        unit="V",
    ),
    _field(
        "phase_degrees",
        "위상",
        PlanFieldType.NUMBER,
        "다채널 동기 또는 기준 위상이 필요할 때 적어요.",
        unit="deg",
        default=0,
        minimum=-360,
        maximum=360,
    ),
    _field(
        "load_impedance",
        "예상 부하",
        PlanFieldType.CHOICE,
        "High-Z와 50 Ω 설정 차이가 실제 진폭을 바꾸므로 반드시 확인해요.",
        required=True,
        choices=("High-Z", "50 Ω", "75 Ω", "직접 입력·확인"),
        default="High-Z",
    ),
    _field(
        "duty_cycle_percent",
        "Duty Cycle",
        PlanFieldType.NUMBER,
        "Square 또는 Pulse의 High 시간 비율이에요.",
        unit="%",
        minimum=0,
        maximum=100,
    ),
    _field(
        "pulse_period_seconds",
        "Pulse Period",
        PlanFieldType.NUMBER,
        "주파수와 Period를 동시에 지정할 때 모순되지 않게 해요.",
        unit="s",
        default=0.001,
        minimum=0.000000001,
    ),
    _field(
        "pulse_width_seconds",
        "Pulse Width",
        PlanFieldType.NUMBER,
        "Period, Duty, Transition Time과 가능한 조합인지 확인해요.",
        unit="s",
        default=0.0005,
        minimum=0.000000001,
    ),
    _field(
        "transition_seconds",
        "상승·하강 시간",
        PlanFieldType.NUMBER,
        "부하와 케이블 대역폭까지 포함해 측정 가능한 값을 정해요.",
        unit="s",
        minimum=0.000000001,
    ),
    _field(
        "burst_state",
        "Burst",
        PlanFieldType.CHOICE,
        "Burst 사용 여부와 Trigger/Gated 방식을 정해요.",
        required=True,
        choices=("OFF", "Triggered", "Gated"),
        default="OFF",
    ),
    _field(
        "burst_cycles",
        "Burst Cycle",
        PlanFieldType.INTEGER,
        "한 Trigger에 생성할 파형 Cycle 수예요.",
        unit="cycle",
        minimum=1,
        maximum=1_000_000,
    ),
    _field(
        "burst_period_seconds",
        "Burst Period",
        PlanFieldType.NUMBER,
        "Burst 길이보다 긴 반복 주기를 정해요.",
        unit="s",
        minimum=0.000001,
    ),
    _field(
        "arb_waveform_name",
        "ARB 파형",
        PlanFieldType.TEXT,
        "장비 메모리에 존재하는 파형 이름과 생성 버전을 적어요.",
    ),
    _field(
        "arb_sample_rate",
        "ARB Sample Rate",
        PlanFieldType.NUMBER,
        "파형 포인트 수, 반복 주기와 장비 최대값을 확인해요.",
        unit="Sa/s",
        minimum=0.000001,
    ),
    _field(
        "arb_filter",
        "ARB Filter",
        PlanFieldType.CHOICE,
        "Normal, Step, Off 등 재구성 Filter가 파형에 미치는 영향을 확인해요.",
        choices=("Normal", "Step", "Off", "지원 여부 확인"),
        default="Normal",
    ),
    _field(
        "arb_advance",
        "ARB 진행 방식",
        PlanFieldType.CHOICE,
        "Trigger 또는 Sample Rate 진행 방식을 고르세요.",
        choices=("Trigger", "Sample Rate", "지원 여부 확인"),
        default="Sample Rate",
    ),
    _field(
        "trigger_source",
        "Trigger Source",
        PlanFieldType.CHOICE,
        "Burst·ARB 시작을 내부, Bus 또는 외부 Trigger와 동기할지 정해요.",
        choices=("Immediate", "Bus/Manual", "External", "Gated", "지원 여부 확인"),
        default="Immediate",
    ),
    _field(
        "dut_level_correction",
        "DUT 입력단 실제 레벨 확인",
        PlanFieldType.MULTILINE,
        "50 Ω/High-Z 차이, Cable 손실과 Probe Loading을 반영한 DUT 입력단 전압 확인 방법을 적어요.",
        required=True,
        default="DUT 입력단에서 오실로스코프로 실제 진폭 확인",
    ),
    _field(
        "output_initially_off",
        "출력 OFF 확인",
        PlanFieldType.BOOLEAN,
        "진폭·Offset·부하·배선을 확인하기 전 출력이 OFF인지 확인하세요.",
        required=True,
        default=False,
        must_be_true=True,
    ),
    _field(
        "verification_measurement",
        "외부 확인 방법",
        PlanFieldType.MULTILINE,
        "오실로스코프·DMM·Counter의 입력 임피던스와 측정 항목을 적어요.",
    ),
)

FUNCTION_GENERATOR_METHODS = (
    PlanMethodTemplate(
        "waveform_output",
        "기본 파형·진폭·주파수 확인",
        "Sine, Square 등 기본 파형의 주파수, 진폭, Offset과 위상을 확인합니다.",
        (
            "출력 OFF에서 채널, 부하 임피던스와 DUT 허용 전압을 확인합니다.",
            "파형, 주파수, 진폭·단위, Offset과 위상을 적용합니다.",
            "외부 계측기로 기준면 파형을 확인하고 종료 시 출력을 끕니다.",
        ),
        (
            "channel",
            "waveform_shape",
            "frequency_hz",
            "amplitude_value",
            "amplitude_unit",
            "offset_v",
            "high_level_v",
            "low_level_v",
            "phase_degrees",
            "load_impedance",
            "dut_level_correction",
            "output_initially_off",
            "verification_measurement",
        ),
        ("설정 Readback", "기준면 주파수·진폭·Offset", "파형 캡처"),
    ),
    PlanMethodTemplate(
        "pulse_timing",
        "Pulse 시간 특성 확인",
        "Pulse 폭, Duty, 상승·하강 시간과 Trigger 반복성을 확인합니다.",
        (
            "Scope Probe, 종단, 대역폭과 시간축을 먼저 정합니다.",
            "Period, Width/Duty와 Transition Time의 가능한 조합을 적용합니다.",
            "여러 Pulse를 캡처해 평균·최대·최소와 Jitter를 기록합니다.",
        ),
        (
            "channel",
            "waveform_shape",
            "frequency_hz",
            "amplitude_value",
            "amplitude_unit",
            "offset_v",
            "load_impedance",
            "dut_level_correction",
            "duty_cycle_percent",
            "pulse_period_seconds",
            "pulse_width_seconds",
            "transition_seconds",
            "output_initially_off",
            "verification_measurement",
        ),
        ("Pulse Width·Period·Duty", "상승·하강 시간", "반복성·Jitter"),
    ),
    PlanMethodTemplate(
        "burst_arb",
        "Burst·ARB 재생 확인",
        "Burst Trigger 또는 임의파형의 메모리 선택, Sample Rate와 반복 조건을 확인합니다.",
        (
            "파형 파일·메모리 이름, 포인트 수와 생성 버전을 확인합니다.",
            "Sample Rate, Filter, Advance, Burst Cycle·Period를 적용합니다.",
            "Trigger별 파형 시작·종료와 반복 누락 여부를 캡처합니다.",
        ),
        (
            "channel",
            "waveform_shape",
            "frequency_hz",
            "amplitude_value",
            "amplitude_unit",
            "offset_v",
            "load_impedance",
            "burst_state",
            "burst_cycles",
            "burst_period_seconds",
            "arb_waveform_name",
            "arb_sample_rate",
            "arb_filter",
            "arb_advance",
            "trigger_source",
            "dut_level_correction",
            "output_initially_off",
            "verification_measurement",
        ),
        ("ARB 이름·Sample Rate Readback", "Trigger별 파형 캡처", "누락·왜곡 기록"),
    ),
)


OSCILLOSCOPE_FIELDS = (
    _field(
        "channel_selection",
        "측정 채널",
        PlanFieldType.TEXT,
        "채널별 신호명, 기준점과 Differential 여부를 적어요.",
        required=True,
        default="CH1",
    ),
    _field(
        "probe_ratio",
        "Probe 배율",
        PlanFieldType.CHOICE,
        "Probe와 장비 채널 설정이 일치하는지 확인해요.",
        required=True,
        choices=("1:1", "10:1", "100:1", "1000:1", "전류 Probe", "직접 입력"),
        default="10:1",
    ),
    _field(
        "coupling",
        "Coupling",
        PlanFieldType.CHOICE,
        "DC, AC, GND Coupling과 입력 임피던스를 구분해요.",
        required=True,
        choices=("DC 1 MΩ", "DC 50 Ω", "AC 1 MΩ", "GND", "Differential"),
        default="DC 1 MΩ",
    ),
    _field(
        "bandwidth_limit",
        "대역 제한",
        PlanFieldType.CHOICE,
        "노이즈 제거와 실제 Edge 왜곡의 Trade-off를 확인해요.",
        required=True,
        choices=("OFF", "20 MHz", "100 MHz", "장비별 선택", "표준 지정"),
        default="OFF",
    ),
    _field(
        "volts_per_div",
        "수직 Scale",
        PlanFieldType.NUMBER,
        "Clipping 없이 화면의 충분한 칸을 사용하도록 정해요.",
        required=True,
        unit="V/div",
        default=1,
        minimum=0.000001,
    ),
    _field(
        "vertical_offset_v",
        "수직 Offset",
        PlanFieldType.NUMBER,
        "Probe Offset과 DUT DC Level을 고려해 정해요.",
        unit="V",
        default=0,
    ),
    _field(
        "time_per_div",
        "시간 Scale",
        PlanFieldType.NUMBER,
        "관심 이벤트 전후 구간과 Sample Rate를 함께 고려해요.",
        required=True,
        unit="s/div",
        default=0.001,
        minimum=0.000000000001,
    ),
    _field(
        "time_offset_seconds",
        "시간 Offset",
        PlanFieldType.NUMBER,
        "Pretrigger와 이벤트 이후 관찰 구간을 정해요.",
        unit="s",
        default=0,
    ),
    _field(
        "trigger_mode",
        "Trigger 종류",
        PlanFieldType.CHOICE,
        "Edge, Pulse Width, Slope 등 이벤트 특성에 맞게 고르세요.",
        required=True,
        choices=("Edge", "Pulse Width", "Slope", "Video", "Pattern", "지원 여부 확인"),
        default="Edge",
    ),
    _field(
        "trigger_source",
        "Trigger Source",
        PlanFieldType.TEXT,
        "채널 또는 외부 Trigger와 Coupling 조건을 적어요.",
        required=True,
        default="CH1",
    ),
    _field(
        "trigger_slope",
        "Trigger 기울기",
        PlanFieldType.CHOICE,
        "Rising, Falling 또는 Either를 고르세요.",
        required=True,
        choices=("Rising", "Falling", "Either"),
        default="Rising",
    ),
    _field(
        "trigger_level_v",
        "Trigger Level",
        PlanFieldType.NUMBER,
        "Noise Margin과 Hysteresis를 고려한 기준 전압이에요.",
        required=True,
        unit="V",
        default=0,
    ),
    _field(
        "trigger_sweep",
        "Trigger Sweep",
        PlanFieldType.CHOICE,
        "희소 이벤트는 Normal/Single을 우선 검토해요.",
        required=True,
        choices=("Auto", "Normal", "Single"),
        default="Single",
    ),
    _field(
        "acquisition_mode",
        "획득 모드",
        PlanFieldType.CHOICE,
        "Normal, Average, Peak Detect, High Resolution을 목적에 맞게 골라요.",
        required=True,
        choices=("Normal", "Average", "Peak Detect", "High Resolution", "Sequence"),
        default="Normal",
    ),
    _field(
        "sample_rate",
        "Sample Rate",
        PlanFieldType.NUMBER,
        "신호 대역폭과 시간 해상도에 충분한 값을 정해요.",
        unit="Sa/s",
        default=1_000_000_000,
        minimum=1,
    ),
    _field(
        "record_length",
        "Record Length",
        PlanFieldType.INTEGER,
        "관찰 시간과 Sample Rate에서 필요한 포인트 수를 계산해요.",
        unit="point",
        minimum=1,
        maximum=1_000_000_000,
    ),
    _field(
        "pretrigger_percent",
        "Pretrigger",
        PlanFieldType.NUMBER,
        "Trigger 전 기준 파형을 확보할 Record 비율이에요.",
        unit="%",
        default=20,
        minimum=0,
        maximum=100,
    ),
    _field(
        "waveform_format",
        "파형 저장 형식",
        PlanFieldType.CHOICE,
        "Binary 원시값에는 Preamble·Byte Order 정보도 함께 저장해요.",
        required=True,
        choices=("ASCII", "BYTE", "WORD", "화면 이미지+원시값"),
        default="WORD",
    ),
    _field(
        "waveform_range",
        "파형 읽기 구간",
        PlanFieldType.TEXT,
        "Start/Stop point 또는 전체 메모리 중 저장 구간을 적어요.",
        default="전체 표시 구간",
    ),
    _field(
        "measurement_items",
        "자동 측정 항목",
        PlanFieldType.MULTILINE,
        "Vpp, RMS, Frequency, Rise/Fall, Pulse Width 등 필요한 결과를 적어요.",
        required=True,
        default="Vpp, Frequency",
    ),
    _field(
        "deskew_seconds",
        "채널 Deskew",
        PlanFieldType.NUMBER,
        "다채널 시간차 측정 전에 Probe·Cable 지연을 보정해요.",
        unit="s",
        default=0,
    ),
    _field(
        "event_count",
        "필요 이벤트 수",
        PlanFieldType.INTEGER,
        "Single 획득 반복 또는 통계에 필요한 유효 이벤트 수예요.",
        unit="개",
        default=1,
        minimum=1,
        maximum=1_000_000,
    ),
    _field(
        "signal_integrity_notes",
        "Probe·접지·부하 고려",
        PlanFieldType.MULTILINE,
        "Probe 대역폭, Ground Lead, Loading, Isolation과 최대 입력을 적어요.",
    ),
    _field(
        "probe_input_rating",
        "Probe·입력 안전 정격",
        PlanFieldType.MULTILINE,
        "Probe CAT/최대 전압, Differential Common-mode, 50 Ω 입력 허용전력과 감쇠기 구성을 적어요.",
        required=True,
    ),
    _field(
        "ground_connection_safety",
        "접지·절연 연결 안전 확인",
        PlanFieldType.BOOLEAN,
        "접지 클립이 보호접지에 연결된 장비인지 확인하고 Floating 측정은 정격 Differential Probe로 계획하세요.",
        required=True,
        default=False,
        must_be_true=True,
    ),
    _field(
        "edge_threshold_definition",
        "Edge·Pulse 기준 레벨",
        PlanFieldType.TEXT,
        "Rise/Fall 10–90%, 20–80%, Pulse Width 50% 등 시간 측정 기준을 적어요.",
        default="Rise/Fall 10–90%, Pulse Width 50%",
    ),
    _field(
        "timebase_accuracy_check",
        "Timebase 정확도·기준 Clock",
        PlanFieldType.TEXT,
        "시간 정확도 요구, 외부 기준 사용 여부와 장비 Timebase 사양을 적어요.",
    ),
    _field(
        "jitter_statistics",
        "Jitter 통계 조건",
        PlanFieldType.TEXT,
        "표본 수, RMS/Peak-to-Peak, Histogram·Outlier 처리와 측정 대역을 적어요.",
    ),
)

OSCILLOSCOPE_METHODS = (
    PlanMethodTemplate(
        "waveform_capture",
        "기본 파형·전압·주파수 측정",
        "안정된 반복 파형을 캡처하고 전압·주파수 결과와 원시 파형을 저장합니다.",
        (
            "Probe 배율, Coupling, 최대 입력과 기준 접지를 확인합니다.",
            "수직·시간 Scale과 Edge Trigger를 적용해 파형을 안정화합니다.",
            "Single 획득 후 Preamble·파형과 자동 측정 결과를 저장합니다.",
        ),
        (
            "channel_selection",
            "probe_ratio",
            "coupling",
            "bandwidth_limit",
            "volts_per_div",
            "vertical_offset_v",
            "time_per_div",
            "time_offset_seconds",
            "trigger_mode",
            "trigger_source",
            "trigger_slope",
            "trigger_level_v",
            "trigger_sweep",
            "acquisition_mode",
            "sample_rate",
            "record_length",
            "pretrigger_percent",
            "waveform_format",
            "waveform_range",
            "measurement_items",
            "signal_integrity_notes",
            "probe_input_rating",
            "ground_connection_safety",
        ),
        ("Preamble·원시 파형", "전압·주파수 측정값", "설정 Readback"),
    ),
    PlanMethodTemplate(
        "timing_edges",
        "Rise/Fall·Pulse 시간 측정",
        "Edge, Pulse Width, 채널 간 Delay와 Jitter를 충분한 대역폭으로 평가합니다.",
        (
            "Probe·Scope 전체 대역폭과 Sample Rate가 측정 Edge에 충분한지 확인합니다.",
            "Trigger와 수직 기준 레벨, 채널 Deskew를 적용합니다.",
            "여러 이벤트를 획득해 평균·최대·최소와 원시 파형을 기록합니다.",
        ),
        (
            "channel_selection",
            "probe_ratio",
            "coupling",
            "bandwidth_limit",
            "volts_per_div",
            "time_per_div",
            "trigger_mode",
            "trigger_source",
            "trigger_slope",
            "trigger_level_v",
            "trigger_sweep",
            "acquisition_mode",
            "sample_rate",
            "record_length",
            "pretrigger_percent",
            "waveform_format",
            "measurement_items",
            "deskew_seconds",
            "event_count",
            "signal_integrity_notes",
            "probe_input_rating",
            "ground_connection_safety",
            "edge_threshold_definition",
            "timebase_accuracy_check",
            "jitter_statistics",
        ),
        ("Rise/Fall·Pulse Width", "채널 Delay·Jitter", "이벤트별 원시 파형"),
    ),
    PlanMethodTemplate(
        "rare_event",
        "희소 이벤트·이상 파형 포착",
        "Normal/Single Trigger로 희소 이벤트를 포착하고 이벤트 전후 파형을 보존합니다.",
        (
            "Auto Sweep 대신 이벤트에 맞는 Trigger와 유한한 대기 시간을 정합니다.",
            "Pretrigger, Record Length와 저장할 이벤트 수를 설정합니다.",
            "각 획득의 Timeout, Trigger 상태와 원시 파형을 함께 기록합니다.",
        ),
        (
            "channel_selection",
            "probe_ratio",
            "coupling",
            "bandwidth_limit",
            "volts_per_div",
            "vertical_offset_v",
            "time_per_div",
            "time_offset_seconds",
            "trigger_mode",
            "trigger_source",
            "trigger_slope",
            "trigger_level_v",
            "trigger_sweep",
            "acquisition_mode",
            "sample_rate",
            "record_length",
            "pretrigger_percent",
            "waveform_format",
            "waveform_range",
            "event_count",
            "signal_integrity_notes",
            "probe_input_rating",
            "ground_connection_safety",
        ),
        ("이벤트 전후 원시 파형", "Trigger·Timeout 상태", "이벤트 시각·횟수"),
    ),
)


DMM_FIELDS = (
    _field(
        "measurement_function",
        "측정 Function",
        PlanFieldType.CHOICE,
        "DCV, ACV, DCI, ACI, 2W/4W 저항 중 배선과 맞는 기능을 고르세요.",
        required=True,
        choices=("DC Voltage", "AC Voltage", "DC Current", "AC Current", "2W Resistance", "4W Resistance"),
        default="DC Voltage",
    ),
    _field(
        "connection_method",
        "배선·단자",
        PlanFieldType.TEXT,
        "Front/Rear, 2W/4W, Shunt, Guard와 극성을 적어요.",
        required=True,
        default="Front 단자, 2-wire",
    ),
    _field(
        "range_mode",
        "Range 방식",
        PlanFieldType.CHOICE,
        "Autorange 또는 예상값보다 여유 있는 고정 Range를 정해요.",
        required=True,
        choices=("Auto", "고정 Range", "MIN", "MAX", "DEF"),
        default="Auto",
    ),
    _field(
        "range_value",
        "고정 Range",
        PlanFieldType.NUMBER,
        "고정 Range 사용 시 단위와 예상 최대값을 함께 확인해요.",
        minimum=0,
    ),
    _field(
        "range_unit",
        "Range 단위",
        PlanFieldType.CHOICE,
        "Function에 맞는 단위를 고르세요.",
        required=True,
        choices=("V", "A", "Ω"),
        default="V",
    ),
    _field(
        "resolution_digits",
        "분해능·Digits",
        PlanFieldType.TEXT,
        "요구 불확도와 측정 시간에 맞는 분해능을 적어요.",
        default="장비 기본",
    ),
    _field(
        "nplc",
        "NPLC",
        PlanFieldType.NUMBER,
        "전원 주기 적분과 측정 속도의 균형을 정해요.",
        unit="PLC",
        default=1,
        minimum=0.0001,
        maximum=1000,
    ),
    _field(
        "auto_zero",
        "Auto Zero",
        PlanFieldType.CHOICE,
        "Offset 보정 필요성과 측정 속도를 함께 고려해요.",
        required=True,
        choices=("ON", "OFF", "Once", "지원 여부 확인"),
        default="ON",
    ),
    _field(
        "trigger_source",
        "Trigger Source",
        PlanFieldType.CHOICE,
        "Immediate, External, Bus 또는 내부 Level Trigger를 고르세요.",
        required=True,
        choices=("Immediate", "External", "Bus", "Internal", "Timer"),
        default="Immediate",
    ),
    _field(
        "trigger_count",
        "Trigger 횟수",
        PlanFieldType.INTEGER,
        "전체 측정 묶음 수와 Sample Count의 곱을 확인해요.",
        unit="회",
        default=1,
        minimum=1,
        maximum=1_000_000,
    ),
    _field(
        "trigger_delay_seconds",
        "Trigger Delay",
        PlanFieldType.NUMBER,
        "Switching·시료 안정화 후 실제 Sample까지의 지연이에요.",
        unit="s",
        default=0,
        minimum=0,
        maximum=3600,
    ),
    _field(
        "trigger_slope",
        "Trigger Slope",
        PlanFieldType.CHOICE,
        "외부 Trigger의 Edge 방향을 고르세요.",
        choices=("Positive", "Negative", "해당 없음"),
        default="해당 없음",
    ),
    _field(
        "sample_count",
        "Trigger당 Sample",
        PlanFieldType.INTEGER,
        "Buffer 크기와 전체 측정 시간을 확인해요.",
        unit="개",
        default=1,
        minimum=1,
        maximum=10_000_000,
    ),
    _field(
        "sample_source",
        "Sample Timing",
        PlanFieldType.CHOICE,
        "즉시 또는 Timer 간격으로 Sample할지 정해요.",
        choices=("Immediate", "Timer"),
        default="Immediate",
    ),
    _field(
        "sample_interval_seconds",
        "Sample 간격",
        PlanFieldType.NUMBER,
        "Timer Sample 사용 시 NPLC 측정 시간보다 충분히 길게 정해요.",
        unit="s",
        minimum=0,
        maximum=3600,
    ),
    _field(
        "pretrigger_samples",
        "Pretrigger Sample",
        PlanFieldType.INTEGER,
        "내부 Trigger 전 기준값이 필요할 때 Sample 수를 정해요.",
        unit="개",
        default=0,
        minimum=0,
        maximum=10_000_000,
    ),
    _field(
        "line_frequency_hz",
        "전원 주파수",
        PlanFieldType.CHOICE,
        "NPLC 환산과 전원 노이즈 제거 기준을 확인해요.",
        required=True,
        choices=("50 Hz", "60 Hz", "장비 자동 확인"),
        default="장비 자동 확인",
    ),
    _field(
        "settling_notes",
        "안정화·열기전력 고려",
        PlanFieldType.MULTILINE,
        "Relay, Cable, DUT 자가발열, Thermal EMF와 Guard 조건을 적어요.",
    ),
    _field(
        "lead_compensation",
        "Lead·Offset 보정",
        PlanFieldType.CHOICE,
        "Null, Open/Short, 4W 또는 별도 Lead 보정 상태를 기록해요.",
        choices=("없음", "Null/Relative", "4-wire", "Open/Short", "별도 보정"),
        default="없음",
    ),
    _field(
        "scan_channels",
        "Scan 채널·순서",
        PlanFieldType.TEXT,
        "Multiplexer 사용 시 채널, Function, Range와 순서를 적어요.",
    ),
    _field(
        "input_terminal_rating",
        "입력 단자·최대 정격·CAT",
        PlanFieldType.MULTILINE,
        "Front/Rear 단자, V/Ω 또는 전류 Jack, 최대 입력, CAT 환경과 Fuse 정격을 적어요.",
        required=True,
    ),
    _field(
        "max_input_safety_confirmed",
        "입력 안전 한계 확인",
        PlanFieldType.BOOLEAN,
        "예상 전압·전류와 과도값이 선택 단자·Range·CAT 정격 이내인지 확인하세요.",
        required=True,
        default=False,
        must_be_true=True,
    ),
    _field(
        "current_fuse_checked",
        "전류 Jack·Fuse 확인",
        PlanFieldType.BOOLEAN,
        "전류 측정 시 올바른 Jack, Fuse 정격·상태와 측정 후 리드 복귀를 확인하세요.",
        default=False,
    ),
    _field(
        "input_characteristics",
        "입력 임피던스·대역폭·Crest Factor",
        PlanFieldType.MULTILINE,
        "DCV 입력 임피던스, AC 대역폭·Crest Factor, 전류 Burden Voltage와 불확도 영향을 적어요.",
    ),
    _field(
        "uncertainty_budget_notes",
        "불확도·사양 적용",
        PlanFieldType.MULTILINE,
        "장비 정확도, Range, 온도, 교정 주기, Lead·Shunt·Thermal EMF 항목을 적어요.",
    ),
)

DMM_METHODS = (
    PlanMethodTemplate(
        "dmm_single_read",
        "단일 전압·전류·저항 측정",
        "지정 Function, Range, NPLC 조건에서 안정된 값을 읽고 연결 불확도를 기록합니다.",
        (
            "배선·단자와 예상값에 맞는 Function·Range를 확인합니다.",
            "NPLC, Auto Zero와 충분한 안정화 시간을 적용합니다.",
            "Read 결과, 설정 Readback과 Overrange·오류 상태를 기록합니다.",
        ),
        (
            "measurement_function",
            "connection_method",
            "range_mode",
            "range_value",
            "range_unit",
            "resolution_digits",
            "nplc",
            "auto_zero",
            "trigger_source",
            "trigger_count",
            "trigger_delay_seconds",
            "sample_count",
            "line_frequency_hz",
            "settling_notes",
            "lead_compensation",
            "input_terminal_rating",
            "max_input_safety_confirmed",
            "current_fuse_checked",
            "input_characteristics",
            "uncertainty_budget_notes",
        ),
        ("측정값·단위", "Range·NPLC Readback", "Overrange·오류 상태"),
    ),
    PlanMethodTemplate(
        "dmm_stability_log",
        "시간 경과·안정도 기록",
        "일정 간격으로 값을 기록해 Drift, 평균과 Peak-to-Peak를 평가합니다.",
        (
            "예상 Drift와 전체 관찰 시간에 맞는 Sample 간격·수를 계산합니다.",
            "Timer Sample과 고정 Range를 사용해 Range 전환 영향을 줄입니다.",
            "Timestamp, 원시값, 평균·표준편차와 환경 변화를 함께 기록합니다.",
        ),
        (
            "measurement_function",
            "connection_method",
            "range_mode",
            "range_value",
            "range_unit",
            "resolution_digits",
            "nplc",
            "auto_zero",
            "trigger_source",
            "trigger_count",
            "trigger_delay_seconds",
            "sample_count",
            "sample_source",
            "sample_interval_seconds",
            "line_frequency_hz",
            "settling_notes",
            "lead_compensation",
            "input_terminal_rating",
            "max_input_safety_confirmed",
            "current_fuse_checked",
            "input_characteristics",
            "uncertainty_budget_notes",
        ),
        ("Timestamp별 원시값", "평균·표준편차·Drift", "환경 변화 기록"),
    ),
    PlanMethodTemplate(
        "dmm_triggered_capture",
        "외부 Trigger·Pretrigger 측정",
        "외부 또는 내부 Trigger 전후의 Sample Buffer를 읽어 과도 상태를 확인합니다.",
        (
            "Trigger Level·Slope·배선과 DMM 입력 보호를 확인합니다.",
            "Trigger Count, Sample Count, Pretrigger와 Timer를 설정합니다.",
            "Initiate 후 유한 Timeout으로 완료를 기다리고 Buffer를 Fetch합니다.",
        ),
        (
            "measurement_function",
            "connection_method",
            "range_mode",
            "range_value",
            "range_unit",
            "resolution_digits",
            "nplc",
            "auto_zero",
            "trigger_source",
            "trigger_count",
            "trigger_delay_seconds",
            "trigger_slope",
            "sample_count",
            "sample_source",
            "sample_interval_seconds",
            "pretrigger_samples",
            "line_frequency_hz",
            "settling_notes",
            "input_terminal_rating",
            "max_input_safety_confirmed",
            "current_fuse_checked",
            "input_characteristics",
            "uncertainty_budget_notes",
        ),
        ("Trigger 전후 Sample Buffer", "Trigger·완료 상태", "Timeout·오류 기록"),
    ),
)


POWER_SUPPLY_FIELDS = (
    _field(
        "channel_selection",
        "출력 채널",
        PlanFieldType.TEXT,
        "채널 번호, Rail 이름, 공통·독립 출력 관계를 적어요.",
        required=True,
        default="CH1",
    ),
    _field(
        "voltage_setpoint_v",
        "전압 설정값",
        PlanFieldType.NUMBER,
        "DUT 절대 최대 정격보다 낮은 시험 한계 안에서 정해요.",
        required=True,
        unit="V",
        default=0,
        minimum=0,
    ),
    _field(
        "current_limit_a",
        "전류 한계",
        PlanFieldType.NUMBER,
        "정상 소비전류와 돌입전류를 고려하되 DUT 보호 한계를 넘지 않게 해요.",
        required=True,
        unit="A",
        default=0.1,
        minimum=0,
    ),
    _field(
        "ovp_v",
        "OVP",
        PlanFieldType.NUMBER,
        "DUT 최대 전압보다 낮고 정상 설정값보다 높은 보호값을 정해요.",
        unit="V",
        minimum=0,
    ),
    _field(
        "ocp_a",
        "OCP",
        PlanFieldType.NUMBER,
        "전류 한계와 OCP 동작의 차이, 복구 정책을 확인해요.",
        unit="A",
        minimum=0,
    ),
    _field(
        "output_sequence",
        "출력 ON/OFF 순서",
        PlanFieldType.MULTILINE,
        "다중 Rail의 전압 설정, 활성화, Master ON/OFF와 종료 순서를 적어요.",
        required=True,
        default="설정 적용 → Readback 확인 → Output ON → 측정 → Output OFF",
    ),
    _field(
        "dwell_seconds",
        "단계 유지 시간",
        PlanFieldType.NUMBER,
        "각 전압·부하 단계에서 DUT와 측정값이 안정화될 시간이에요.",
        unit="s",
        default=1,
        minimum=0,
        maximum=3600,
    ),
    _field(
        "sense_mode",
        "Sense 방식",
        PlanFieldType.CHOICE,
        "Local/Remote Sense 배선과 Open Sense 보호를 확인해요.",
        required=True,
        choices=("Local Sense", "Remote Sense", "지원 여부 확인"),
        default="Local Sense",
    ),
    _field(
        "load_condition",
        "부하 조건",
        PlanFieldType.MULTILINE,
        "전자부하 Mode, 전류·저항·전력값과 변화 Timing을 적어요.",
        required=True,
        default="무부하 또는 DUT 정상 부하",
    ),
    _field(
        "load_wiring",
        "부하 배선·전압 강하",
        PlanFieldType.MULTILINE,
        "Cable 굵기·길이, 접촉 저항, Ground, Remote Sense와 Backfeed 경로를 적어요.",
        required=True,
    ),
    _field(
        "ramp_rate_v_per_s",
        "전압 Ramp",
        PlanFieldType.NUMBER,
        "DUT Inrush와 Sequence 요구에 맞는 상승·하강 속도를 정해요.",
        unit="V/s",
        minimum=0,
    ),
    _field(
        "voltage_steps",
        "전압 단계",
        PlanFieldType.TEXT,
        "Start/Stop/Step 또는 직접 목록과 허용 Ramp를 적어요.",
    ),
    _field(
        "current_steps",
        "전류·부하 단계",
        PlanFieldType.TEXT,
        "Load Current 목록, Slew와 각 단계 시간을 적어요.",
    ),
    _field(
        "ac_input_conditions",
        "AC 입력·Line 조건",
        PlanFieldType.MULTILINE,
        "Line Regulation 측정의 AC 입력 전압·주파수·허용 변동과 안정화 시간을 적어요.",
        default="정격 AC 입력, Low/Nominal/High 조건과 단계별 안정화 시간",
    ),
    _field(
        "load_slew_a_per_s",
        "부하 Slew",
        PlanFieldType.NUMBER,
        "전자부하 상승·하강 Slew 또는 Edge 속도를 적어요.",
        unit="A/s",
        default=1,
        minimum=0,
    ),
    _field(
        "load_edge_seconds",
        "부하 Edge 시간",
        PlanFieldType.NUMBER,
        "과도 응답을 만드는 부하 전환의 상승·하강 시간을 적어요.",
        unit="s",
        minimum=0,
    ),
    _field(
        "transient_capture_setup",
        "과도 응답 측정 경로",
        PlanFieldType.MULTILINE,
        "Scope/DMM 대역폭, Probe·Shunt, Sample Rate, Trigger와 Overshoot·Settling 판정 기준을 적어요.",
        default="오실로스코프 전압 Probe, 전자부하 Trigger, Overshoot·Settling 측정",
    ),
    _field(
        "transient_sample_seconds",
        "과도 응답 Sample 간격",
        PlanFieldType.NUMBER,
        "부하 변화보다 충분히 빠른 측정 경로와 간격을 정해요.",
        unit="s",
        minimum=0.000000001,
    ),
    _field(
        "sequence_triplets",
        "전압·전류·시간 Sequence",
        PlanFieldType.MULTILINE,
        "각 행을 Voltage, Current, Time으로 적고 장비 한계와 총 시간을 검산해요.",
    ),
    _field(
        "sequence_repetitions",
        "Sequence 반복",
        PlanFieldType.INTEGER,
        "0이 무한 반복을 뜻하는 장비가 있으므로 유한 횟수를 명시해요.",
        unit="회",
        default=1,
        minimum=1,
        maximum=100_000,
    ),
    _field(
        "measurement_items",
        "기록 항목",
        PlanFieldType.MULTILINE,
        "설정값, 실제 V/I, CC/CV 상태, 보호 상태와 외부 DMM 값을 적어요.",
        required=True,
        default="설정 V/I, 실제 V/I, CC/CV, 보호 상태",
    ),
    _field(
        "protection_recovery",
        "보호 동작 후 처리",
        PlanFieldType.MULTILINE,
        "출력 OFF, 원인 확인, 보호 해제 승인과 재시험 조건을 적어요.",
    ),
    _field(
        "output_initially_off",
        "출력 OFF 확인",
        PlanFieldType.BOOLEAN,
        "전압·전류 한계·극성·배선을 확인하기 전 모든 출력이 OFF인지 확인하세요.",
        required=True,
        default=False,
        must_be_true=True,
    ),
    _field(
        "dut_absolute_limits",
        "DUT 절대 최대 정격",
        PlanFieldType.MULTILINE,
        "전압, 전류, 역극성, 접지와 Backfeed 금지 조건을 적어요.",
        required=True,
    ),
)

POWER_SUPPLY_METHODS = (
    PlanMethodTemplate(
        "dc_static_output",
        "정전압·정전류 출력 확인",
        "설정값과 실제 출력 V/I, CC/CV 상태를 무부하와 지정 부하에서 확인합니다.",
        (
            "출력 OFF에서 극성, Channel, DUT 한계와 전류 제한을 확인합니다.",
            "전압·전류·보호값을 적용하고 Readback 후 출력을 켭니다.",
            "실제 V/I와 CC/CV 상태를 기록하고 종료 시 모든 출력을 끕니다.",
        ),
        (
            "channel_selection",
            "voltage_setpoint_v",
            "current_limit_a",
            "ovp_v",
            "ocp_a",
            "output_sequence",
            "dwell_seconds",
            "sense_mode",
            "load_condition",
            "load_wiring",
            "ramp_rate_v_per_s",
            "measurement_items",
            "protection_recovery",
            "output_initially_off",
            "dut_absolute_limits",
        ),
        ("설정·실제 V/I", "CC/CV·보호 상태", "Output OFF 종료 확인"),
    ),
    PlanMethodTemplate(
        "dc_regulation_transient",
        "Line·Load Regulation 및 과도 응답",
        "입력·부하 변화에 따른 정상상태 변동과 Overshoot·Settling을 평가합니다.",
        (
            "전자부하, Scope/DMM와 Remote Sense 기준면을 확인합니다.",
            "전압·부하 단계를 유한 순서로 적용하고 각 단계 안정값을 측정합니다.",
            "부하 Edge 전후 파형과 최대 편차·회복 시간을 기록합니다.",
        ),
        (
            "channel_selection",
            "voltage_setpoint_v",
            "current_limit_a",
            "ovp_v",
            "ocp_a",
            "output_sequence",
            "dwell_seconds",
            "sense_mode",
            "load_condition",
            "load_wiring",
            "ramp_rate_v_per_s",
            "voltage_steps",
            "current_steps",
            "ac_input_conditions",
            "load_slew_a_per_s",
            "load_edge_seconds",
            "transient_capture_setup",
            "transient_sample_seconds",
            "measurement_items",
            "output_initially_off",
            "dut_absolute_limits",
        ),
        ("단계별 실제 V/I", "Regulation 오차", "Overshoot·Undershoot·Settling"),
    ),
    PlanMethodTemplate(
        "dc_sequence_protection",
        "Sequence·보호 동작 확인",
        "유한 전압·전류·시간 Sequence와 OVP/OCP 발생 후 안전 종료를 확인합니다.",
        (
            "Sequence 총 시간, 반복 수와 각 Triplet의 장비·DUT 한계를 검산합니다.",
            "보호 동작 시험은 별도 승인과 안전 부하에서 최소 에너지로 수행합니다.",
            "정상·오류·취소 모두 Master/Channel OFF와 실제 상태 확인을 기록합니다.",
        ),
        (
            "channel_selection",
            "voltage_setpoint_v",
            "current_limit_a",
            "ovp_v",
            "ocp_a",
            "output_sequence",
            "sense_mode",
            "load_condition",
            "load_wiring",
            "ramp_rate_v_per_s",
            "sequence_triplets",
            "sequence_repetitions",
            "measurement_items",
            "protection_recovery",
            "output_initially_off",
            "dut_absolute_limits",
        ),
        ("Sequence 단계별 V/I", "OVP/OCP·오류 상태", "안전 종료·복구 기록"),
    ),
)


LCR_FIELDS = (
    _field(
        "measurement_function",
        "측정 조합",
        PlanFieldType.CHOICE,
        "Cp-D, Cs-Rs, Lp-Q, Ls-Q, R-X, Z-θ 등 등가회로에 맞는 조합을 고르세요.",
        required=True,
        choices=("Cp-D", "Cp-Q", "Cs-D", "Cs-Rs", "Lp-Q", "Ls-Q", "R-X", "Z-θ", "G-B", "Y-θ"),
        default="R-X",
    ),
    _field(
        "frequency_plan",
        "측정 주파수",
        PlanFieldType.TEXT,
        "단일값 또는 Log/Linear 목록과 각 포인트 안정화 시간을 적어요.",
        required=True,
        default="1 kHz",
    ),
    _field(
        "signal_level_mode",
        "측정 신호 방식",
        PlanFieldType.CHOICE,
        "Voltage Level 또는 Current Level 중 장비와 시료에 맞는 한 가지만 선택해요.",
        required=True,
        choices=("Voltage Level", "Current Level"),
        default="Voltage Level",
    ),
    _field(
        "signal_voltage_v",
        "측정 신호 전압",
        PlanFieldType.NUMBER,
        "소자의 비선형성과 정격을 고려한 AC Level이에요.",
        unit="V",
        default=1,
        minimum=0,
    ),
    _field(
        "signal_current_a",
        "측정 신호 전류",
        PlanFieldType.NUMBER,
        "Current Level Mode를 사용할 때의 AC Level이에요.",
        unit="A",
        minimum=0,
    ),
    _field(
        "dc_bias_state",
        "DC Bias",
        PlanFieldType.CHOICE,
        "Bias 사용 여부, 외부 Bias Unit과 극성을 확인해요.",
        required=True,
        choices=("OFF", "내부 Bias", "외부 Bias", "지원 여부 확인"),
        default="OFF",
    ),
    _field(
        "dc_bias_voltage_v",
        "DC Bias 전압",
        PlanFieldType.NUMBER,
        "소자 정격, 극성과 방전 절차를 확인한 뒤 정해요.",
        unit="V",
    ),
    _field(
        "external_bias_current_limit_a",
        "외부 Bias 전류 제한",
        PlanFieldType.NUMBER,
        "외부 Bias Unit 사용 시 시료·Fixture를 보호할 전류 제한을 적어요.",
        unit="A",
        minimum=0,
    ),
    _field(
        "external_bias_polarity",
        "외부 Bias 극성·연결",
        PlanFieldType.TEXT,
        "Bias 극성, Return 경로, 차단·방전 소자와 순서를 적어요.",
    ),
    _field(
        "stored_energy_notes",
        "저장 에너지·잔류전압",
        PlanFieldType.MULTILINE,
        "시료 C/L과 Fixture의 저장 에너지, 방전 시간, 잔류전압 확인 방법을 적어요.",
    ),
    _field(
        "range_mode",
        "Impedance Range",
        PlanFieldType.CHOICE,
        "Autorange 또는 고정 Range로 반복성을 확보할지 정해요.",
        required=True,
        choices=("Auto", "고정 Range", "표준 지정"),
        default="Auto",
    ),
    _field(
        "impedance_range_ohm",
        "고정 Impedance Range",
        PlanFieldType.NUMBER,
        "예상 임피던스에 맞는 장비 Range를 정해요.",
        unit="Ω",
        minimum=0,
    ),
    _field(
        "aperture_mode",
        "Aperture",
        PlanFieldType.CHOICE,
        "Short/Medium/Long 측정 시간과 Noise의 균형을 정해요.",
        required=True,
        choices=("Short", "Medium", "Long"),
        default="Medium",
    ),
    _field(
        "averaging_count",
        "평균 횟수",
        PlanFieldType.INTEGER,
        "Aperture와 전체 Sweep 시간을 함께 검산해요.",
        unit="회",
        default=1,
        minimum=1,
        maximum=10_000,
    ),
    _field(
        "open_correction",
        "OPEN 보정",
        PlanFieldType.CHOICE,
        "Fixture·Cable을 포함한 Open 보정 실행·적용 상태를 기록해요.",
        required=True,
        choices=("새로 실행", "저장값 적용", "적용 안 함"),
        default="새로 실행",
    ),
    _field(
        "short_correction",
        "SHORT 보정",
        PlanFieldType.CHOICE,
        "Fixture·Cable을 포함한 Short 보정 실행·적용 상태를 기록해요.",
        required=True,
        choices=("새로 실행", "저장값 적용", "적용 안 함"),
        default="새로 실행",
    ),
    _field(
        "load_correction",
        "LOAD 보정",
        PlanFieldType.CHOICE,
        "정확도 요구와 Fixture 특성에 따라 Load 보정 또는 기준 소자 확인을 계획해요.",
        required=True,
        choices=("새로 실행", "저장값 적용", "기준 소자 확인", "적용 안 함"),
        default="기준 소자 확인",
    ),
    _field(
        "fixture_description",
        "Fixture·Cable",
        PlanFieldType.MULTILINE,
        "Fixture 모델, 접촉 방식, Cable 길이, 4TP/2TP와 Shield 상태를 적어요.",
        required=True,
    ),
    _field(
        "compensation_timestamp",
        "보정 확인 시각",
        PlanFieldType.TEXT,
        "보정 수행 시각, 온도와 재보정 조건을 적어요.",
    ),
    _field(
        "compensation_frequency_range",
        "보정 유효 주파수·Fixture 범위",
        PlanFieldType.MULTILINE,
        "OPEN/SHORT/LOAD 보정 세트가 유효한 주파수, Signal Level, Bias와 Fixture 구성을 적어요.",
    ),
    _field(
        "equivalent_circuit",
        "등가회로·Series/Parallel",
        PlanFieldType.TEXT,
        "부품 특성과 적용 기준이 요구하는 등가회로를 적어요.",
        required=True,
        default="시험 절차서 지정 등가회로",
    ),
    _field(
        "result_items",
        "기록 결과",
        PlanFieldType.MULTILINE,
        "Primary/Secondary 값, R/X, Z/θ, Q/D와 Correction 상태를 적어요.",
        required=True,
        default="Primary, Secondary, R, X, Correction 상태",
    ),
    _field(
        "bias_discharge_confirmed",
        "Bias 방전 절차 확인",
        PlanFieldType.BOOLEAN,
        "Bias 사용 시 시료 분리 전 OFF·방전·잔류 전압 확인 절차를 확인하세요.",
        required=True,
        default=False,
        must_be_true=True,
    ),
)

LCR_METHODS = (
    PlanMethodTemplate(
        "lcr_single_frequency",
        "단일 주파수 임피던스 측정",
        "지정 주파수·Level에서 등가회로의 Primary/Secondary 값을 측정합니다.",
        (
            "Fixture, Open/Short 보정과 시료 접촉 상태를 확인합니다.",
            "Function, 주파수, Level, Range와 Aperture를 적용합니다.",
            "Formatted·Corrected 결과와 Correction 상태를 함께 기록합니다.",
        ),
        (
            "measurement_function",
            "frequency_plan",
            "signal_level_mode",
            "signal_voltage_v",
            "signal_current_a",
            "dc_bias_state",
            "dc_bias_voltage_v",
            "external_bias_current_limit_a",
            "external_bias_polarity",
            "stored_energy_notes",
            "range_mode",
            "impedance_range_ohm",
            "aperture_mode",
            "averaging_count",
            "open_correction",
            "short_correction",
            "load_correction",
            "fixture_description",
            "compensation_timestamp",
            "compensation_frequency_range",
            "equivalent_circuit",
            "result_items",
            "bias_discharge_confirmed",
        ),
        ("Primary·Secondary 값", "R/X 또는 Z/θ", "Correction·Range 상태"),
    ),
    PlanMethodTemplate(
        "lcr_frequency_sweep",
        "주파수별 임피던스 특성",
        "주파수 목록에서 임피던스, 위상과 Q/D 변화를 측정합니다.",
        (
            "주파수 범위 전체에서 Fixture 보정 유효성과 Level 한계를 확인합니다.",
            "포인트별 같은 Aperture·평균 조건을 적용하거나 변경 규칙을 명시합니다.",
            "주파수별 원시 결과, Range 전환과 오류를 표로 기록합니다.",
        ),
        (
            "measurement_function",
            "frequency_plan",
            "signal_level_mode",
            "signal_voltage_v",
            "signal_current_a",
            "dc_bias_state",
            "dc_bias_voltage_v",
            "external_bias_current_limit_a",
            "external_bias_polarity",
            "stored_energy_notes",
            "range_mode",
            "impedance_range_ohm",
            "aperture_mode",
            "averaging_count",
            "open_correction",
            "short_correction",
            "load_correction",
            "fixture_description",
            "compensation_timestamp",
            "compensation_frequency_range",
            "equivalent_circuit",
            "result_items",
            "bias_discharge_confirmed",
        ),
        ("주파수별 Primary·Secondary", "임피던스·위상 곡선", "Range·오류 기록"),
    ),
    PlanMethodTemplate(
        "lcr_bias_characteristic",
        "DC Bias 의존 특성",
        "DC Bias 단계에 따른 C/L/Z 변화를 정격 이내에서 평가합니다.",
        (
            "시료 Bias 정격, 극성, 외부 Bias Unit와 방전 경로를 확인합니다.",
            "최소 Bias부터 유한 단계로 적용하고 각 단계 안정 후 측정합니다.",
            "오류·취소·종료 시 Bias OFF와 잔류 전압 확인을 기록합니다.",
        ),
        (
            "measurement_function",
            "frequency_plan",
            "signal_level_mode",
            "signal_voltage_v",
            "signal_current_a",
            "dc_bias_state",
            "dc_bias_voltage_v",
            "external_bias_current_limit_a",
            "external_bias_polarity",
            "stored_energy_notes",
            "range_mode",
            "impedance_range_ohm",
            "aperture_mode",
            "averaging_count",
            "open_correction",
            "short_correction",
            "load_correction",
            "fixture_description",
            "compensation_frequency_range",
            "equivalent_circuit",
            "result_items",
            "bias_discharge_confirmed",
        ),
        ("Bias별 임피던스 결과", "정격·극성 상태", "Bias OFF·방전 결과"),
    ),
)


VNA_FIELDS = (
    _field(
        "port_selection",
        "사용 Port",
        PlanFieldType.TEXT,
        "Port 번호, 방향, Cable·Adapter와 DUT 연결을 적어요.",
        required=True,
        default="Port 1 → Port 2",
    ),
    _field(
        "s_parameters",
        "S-Parameter",
        PlanFieldType.TEXT,
        "S11, S21, S12, S22 등 필요한 측정 파라미터를 적어요.",
        required=True,
        default="S11, S21",
    ),
    _field(
        "frequency_plan",
        "주파수 Sweep",
        PlanFieldType.TEXT,
        "Start/Stop/Center/Span/CW와 Linear/Log/Segment 조건을 적어요.",
        required=True,
        default="Start 100 MHz, Stop 1 GHz, Linear",
    ),
    _field(
        "sweep_points",
        "Sweep Point",
        PlanFieldType.INTEGER,
        "주파수 해상도, DUT 지연과 전체 시간을 고려해요.",
        required=True,
        unit="개",
        default=201,
        minimum=1,
        maximum=1_000_000,
    ),
    _field(
        "sweep_type",
        "Sweep 종류",
        PlanFieldType.CHOICE,
        "Linear, Log, CW, Power, Segment 등 목적에 맞게 고르세요.",
        required=True,
        choices=("Linear", "Log", "CW", "Power", "Segment", "Phase"),
        default="Linear",
    ),
    _field(
        "if_bandwidth_hz",
        "IF Bandwidth",
        PlanFieldType.NUMBER,
        "Noise, Dynamic Range와 Sweep Time의 균형을 정해요.",
        required=True,
        unit="Hz",
        default=1000,
        minimum=0.001,
    ),
    _field(
        "source_power_dbm",
        "Source Power",
        PlanFieldType.NUMBER,
        "DUT Compression·손상과 Receiver Dynamic Range를 고려해요.",
        required=True,
        unit="dBm",
        default=-20,
        minimum=-200,
        maximum=50,
    ),
    _field(
        "port_power_notes",
        "Port별 Power",
        PlanFieldType.TEXT,
        "Port별 Level Offset, Attenuator와 Option 의존 한계를 적어요.",
    ),
    _field(
        "averaging_state",
        "Averaging",
        PlanFieldType.CHOICE,
        "Average ON/OFF와 평균 누적 초기화 시점을 정해요.",
        required=True,
        choices=("OFF", "ON", "Sweep별 초기화"),
        default="OFF",
    ),
    _field(
        "averaging_count",
        "평균 횟수",
        PlanFieldType.INTEGER,
        "전체 측정 시간과 필요한 Noise 감소량을 고려해요.",
        unit="회",
        default=1,
        minimum=1,
        maximum=100_000,
    ),
    _field(
        "trigger_source",
        "Trigger Source",
        PlanFieldType.CHOICE,
        "Immediate, Manual, External 동기와 완료 확인 방식을 정해요.",
        required=True,
        choices=("Immediate", "Manual", "External"),
        default="Immediate",
    ),
    _field(
        "trace_format",
        "Trace Format",
        PlanFieldType.CHOICE,
        "Log Mag, Phase, Smith, Real/Imag 등 판정 목적에 맞게 고르세요.",
        required=True,
        choices=("Log Magnitude", "Linear Magnitude", "Phase", "Unwrapped Phase", "Smith/Polar", "Real/Imag"),
        default="Log Magnitude",
    ),
    _field(
        "calibration_type",
        "Calibration 방식",
        PlanFieldType.CHOICE,
        "SOLT, TRL, Response, ECal 등 Port·Fixture에 맞는 방식을 고르세요.",
        required=True,
        choices=("1-port SOL", "2-port SOLT", "TRL/LRM", "Response", "ECal", "저장 Cal 적용"),
        default="2-port SOLT",
    ),
    _field(
        "calibration_kit",
        "Calibration Kit",
        PlanFieldType.TEXT,
        "Kit 모델·시리얼·Connector·Definition과 교정 상태를 적어요.",
        required=True,
    ),
    _field(
        "calibration_verification",
        "Calibration 검증",
        PlanFieldType.MULTILINE,
        "Verification Standard, Through/Load 확인 결과와 재교정 조건을 적어요.",
        required=True,
    ),
    _field(
        "reference_plane",
        "측정 기준면",
        PlanFieldType.TEXT,
        "Cable 끝, Fixture, Probe Tip 등 Calibration이 이동된 기준면을 적어요.",
        required=True,
        default="DUT Connector",
    ),
    _field(
        "fixture_deembedding",
        "Fixture·De-embedding",
        PlanFieldType.MULTILINE,
        "Port Extension, Electrical Delay, Fixture S2P와 적용 순서를 적어요.",
    ),
    _field(
        "segment_table",
        "Segment Sweep 표",
        PlanFieldType.MULTILINE,
        "Segment별 Start/Stop/Points/IFBW/Power/Dwell을 적고 장비 지원 여부를 확인해요.",
    ),
    _field(
        "power_sweep_range",
        "Power Sweep 범위",
        PlanFieldType.MULTILINE,
        "Start/Stop/Step 또는 Point, CW 주파수, DUT 압축·손상 한계와 감쇠 구성을 적어요.",
    ),
    _field(
        "group_delay_aperture_hz",
        "Group Delay Aperture",
        PlanFieldType.NUMBER,
        "Group Delay 계산에 사용할 주파수 간격·Aperture를 적어요.",
        unit="Hz",
        default=1_000_000,
        minimum=0.001,
    ),
    _field(
        "power_receiver_calibration",
        "Power·Receiver Calibration",
        PlanFieldType.MULTILINE,
        "Source Power calibration, Receiver calibration, Sensor 기준면과 적용 Port·주파수 범위를 적어요.",
    ),
    _field(
        "connector_care",
        "Connector·Torque",
        PlanFieldType.MULTILINE,
        "Connector 검사·청소, Torque Wrench 값과 Mate 횟수를 기록해요.",
    ),
    _field(
        "rf_output_initially_off",
        "RF 출력 OFF 확인",
        PlanFieldType.BOOLEAN,
        "Calibration·배선·Power 검토 전에 RF Output이 OFF인지 확인하세요.",
        required=True,
        default=False,
        must_be_true=True,
    ),
    _field(
        "limit_definition",
        "판정 Limit",
        PlanFieldType.MULTILINE,
        "주파수별 Return Loss, Gain, Ripple, Phase/Delay 제한과 보간 규칙을 적어요.",
        required=True,
    ),
)

VNA_METHODS = (
    PlanMethodTemplate(
        "vna_sparameter_sweep",
        "S-Parameter 주파수 Sweep",
        "교정 기준면에서 Reflection과 Transmission S-Parameter를 측정합니다.",
        (
            "Port, Cable, Connector와 Calibration Kit 상태를 확인합니다.",
            "Calibration·검증 후 Sweep, IFBW, Power, Average와 Trace를 설정합니다.",
            "Single Sweep 완료 후 Trace Data와 Cal·설정 상태를 저장합니다.",
        ),
        (
            "port_selection",
            "s_parameters",
            "frequency_plan",
            "sweep_points",
            "sweep_type",
            "if_bandwidth_hz",
            "source_power_dbm",
            "port_power_notes",
            "averaging_state",
            "averaging_count",
            "trigger_source",
            "trace_format",
            "calibration_type",
            "calibration_kit",
            "calibration_verification",
            "reference_plane",
            "fixture_deembedding",
            "segment_table",
            "power_sweep_range",
            "power_receiver_calibration",
            "connector_care",
            "rf_output_initially_off",
            "limit_definition",
        ),
        ("복소 S-Parameter 또는 Formatted Trace", "Calibration 상태", "Limit 판정"),
    ),
    PlanMethodTemplate(
        "vna_gain_return_loss",
        "Gain·Insertion Loss·Return Loss",
        "S21 Gain/Loss와 S11/S22 Return Loss, Ripple을 주파수별로 평가합니다.",
        (
            "DUT 동작점, Bias Tee·Attenuator와 최대 입력·출력을 확인합니다.",
            "Receiver Compression을 피하는 Source Power와 IFBW를 설정합니다.",
            "Gain/Return Loss, Ripple, Compression 의심 구간과 Trace를 기록합니다.",
        ),
        (
            "port_selection",
            "s_parameters",
            "frequency_plan",
            "sweep_points",
            "sweep_type",
            "if_bandwidth_hz",
            "source_power_dbm",
            "port_power_notes",
            "averaging_state",
            "averaging_count",
            "trigger_source",
            "trace_format",
            "calibration_type",
            "calibration_kit",
            "calibration_verification",
            "reference_plane",
            "fixture_deembedding",
            "segment_table",
            "power_sweep_range",
            "power_receiver_calibration",
            "connector_care",
            "rf_output_initially_off",
            "limit_definition",
        ),
        ("Gain·Insertion Loss", "Input·Output Return Loss", "Ripple·Limit 판정"),
    ),
    PlanMethodTemplate(
        "vna_phase_delay",
        "Phase·Group Delay",
        "Unwrapped Phase와 Group Delay를 충분한 주파수 간격과 기준면 보정으로 평가합니다.",
        (
            "필요한 Delay 분해능에 맞춰 Point 간격과 Aperture 조건을 정합니다.",
            "Port Extension·Fixture De-embedding과 Phase 기준을 확인합니다.",
            "Phase unwrap 오류와 불연속 구간을 원시 복소 데이터와 함께 검토합니다.",
        ),
        (
            "port_selection",
            "s_parameters",
            "frequency_plan",
            "sweep_points",
            "sweep_type",
            "if_bandwidth_hz",
            "source_power_dbm",
            "averaging_state",
            "averaging_count",
            "trigger_source",
            "trace_format",
            "calibration_type",
            "calibration_kit",
            "calibration_verification",
            "reference_plane",
            "fixture_deembedding",
            "segment_table",
            "power_sweep_range",
            "group_delay_aperture_hz",
            "power_receiver_calibration",
            "connector_care",
            "rf_output_initially_off",
            "limit_definition",
        ),
        ("Unwrapped Phase", "Group Delay·Ripple", "복소 Trace·기준면 정보"),
    ),
)


CATEGORY_PLAN_TEMPLATES: Mapping[DeviceCategory, CategoryPlanTemplate] = (
    MappingProxyType(
        {
            DeviceCategory.SPECTRUM_ANALYZER: CategoryPlanTemplate(
                DeviceCategory.SPECTRUM_ANALYZER,
                "주파수·대역폭·Detector·Trace·Marker와 입력 경로를 함께 계획합니다.",
                (
                    "CISPR 16-2-1·16-2-3 계열 EMI 측정 절차 — 적용 문서·장비 적합성 별도 확인",
                    "ETSI·FCC·3GPP 제품별 방사·점유대역폭 절차 — 해당 규격 최신 판 확인",
                    "사내 Spectrum·Channel Power·Spurious 측정 SOP",
                ),
                SPECTRUM_METHODS,
                SPECTRUM_FIELDS,
            ),
            DeviceCategory.SIGNAL_GENERATOR: CategoryPlanTemplate(
                DeviceCategory.SIGNAL_GENERATOR,
                "출력 기준면, 주파수·레벨·Sweep와 RF OFF 안전 조건을 함께 계획합니다.",
                (
                    "IEC 61000-4-3·-6 계열 레벨링 구성 참고 — 전용 증폭기·Coupler 요건 별도 확인",
                    "ETSI·3GPP·고객 규격의 감도·Blocking·Interference 절차",
                    "사내 RF Source 주파수·레벨 검증 SOP",
                ),
                RF_GENERATOR_METHODS,
                RF_GENERATOR_FIELDS,
            ),
            DeviceCategory.FUNCTION_GENERATOR: CategoryPlanTemplate(
                DeviceCategory.FUNCTION_GENERATOR,
                "채널·부하·파형·Pulse·Burst·ARB와 출력 OFF 조건을 함께 계획합니다.",
                (
                    "IEEE 181 Pulse 파라미터 용어·측정 정의 참고",
                    "제품·고객 규격의 Clock·Pulse·Waveform 자극 절차",
                    "사내 함수·임의파형 발생기 출력 검증 SOP",
                ),
                FUNCTION_GENERATOR_METHODS,
                FUNCTION_GENERATOR_FIELDS,
            ),
            DeviceCategory.OSCILLOSCOPE: CategoryPlanTemplate(
                DeviceCategory.OSCILLOSCOPE,
                "Probe·수직·시간축·Trigger·획득·원시 파형 저장 조건을 함께 계획합니다.",
                (
                    "IEEE 181 Pulse·Rise/Fall 파라미터 정의 참고",
                    "IEC 61083 계열 Digitizer 요구가 적용되는 경우 해당 절차 확인",
                    "사내 파형·Timing·희소 이벤트 캡처 SOP",
                ),
                OSCILLOSCOPE_METHODS,
                OSCILLOSCOPE_FIELDS,
            ),
            DeviceCategory.DIGITAL_MULTIMETER: CategoryPlanTemplate(
                DeviceCategory.DIGITAL_MULTIMETER,
                "Function·Range·NPLC·Trigger·Sample과 배선·안정화 조건을 계획합니다.",
                (
                    "JCGM 100(GUM) 불확도 평가 원칙 참고",
                    "ISO/IEC 17025 품질 체계 아래 승인된 시험·교정 절차",
                    "제조사 Performance Verification 및 사내 DMM 측정 SOP",
                ),
                DMM_METHODS,
                DMM_FIELDS,
            ),
            DeviceCategory.POWER_SUPPLY: CategoryPlanTemplate(
                DeviceCategory.POWER_SUPPLY,
                "채널·전압·전류·보호·부하·Sequence와 안전 종료를 함께 계획합니다.",
                (
                    "제품 규격의 Line·Load Regulation·Transient 시험 절차",
                    "IEC 61010·62368-1 계열 안전 한계가 적용되는 경우 해당 요구 확인",
                    "사내 DC Source·OVP/OCP·Sequence 검증 SOP",
                ),
                POWER_SUPPLY_METHODS,
                POWER_SUPPLY_FIELDS,
            ),
            DeviceCategory.LCR_METER: CategoryPlanTemplate(
                DeviceCategory.LCR_METER,
                "측정 함수·주파수·Level·Bias·Fixture 보정과 등가회로를 계획합니다.",
                (
                    "IEC 60384·60115 등 부품군별 전기 특성 절차 — 해당 부품 규격 확인",
                    "제조사 Fixture Compensation·Impedance Accuracy 절차",
                    "사내 LCR·Bias·주파수 Sweep 측정 SOP",
                ),
                LCR_METHODS,
                LCR_FIELDS,
            ),
            DeviceCategory.NETWORK_ANALYZER: CategoryPlanTemplate(
                DeviceCategory.NETWORK_ANALYZER,
                "Port·S-Parameter·Sweep·IFBW·Power·Calibration과 기준면을 계획합니다.",
                (
                    "IEEE 287 정밀 동축 측정 기법 참고",
                    "IEC 61169 Connector 계열 요구가 적용되는 경우 해당 문서 확인",
                    "Calibration Kit 제조사 정의·VNA Performance Verification 절차",
                ),
                VNA_METHODS,
                VNA_FIELDS,
            ),
        }
    )
)


def template_for_category(category: DeviceCategory) -> CategoryPlanTemplate:
    try:
        return CATEGORY_PLAN_TEMPLATES[category]
    except KeyError as exc:
        raise KeyError(f"계획서를 지원하지 않는 장비 분류입니다: {category.value}") from exc


def template_for_instrument(instrument: SelectedInstrument) -> CategoryPlanTemplate:
    if not isinstance(instrument, SelectedInstrument):
        raise TypeError("계획 템플릿 대상은 SelectedInstrument여야 합니다.")
    return template_for_category(instrument.category)


def plan_supported_categories() -> tuple[DeviceCategory, ...]:
    return tuple(CATEGORY_PLAN_TEMPLATES)
