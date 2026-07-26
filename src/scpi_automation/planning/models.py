from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Mapping, TypeAlias

from scpi_automation.identity import DeviceCategory
from scpi_automation.planning.templates import (
    COMMON_PLAN_FIELDS,
    PlanFieldDefinition,
    PlanScalar,
    template_for_instrument,
)
from scpi_automation.routine import SelectedInstrument


def _positive_finite(value: float, field_name: str) -> float:
    if isinstance(value, bool):
        raise ValueError(f"{field_name}은(는) 0보다 큰 숫자여야 합니다.")
    try:
        normalized = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{field_name}은(는) 0보다 큰 숫자여야 합니다.") from exc
    if not math.isfinite(normalized) or normalized <= 0:
        raise ValueError(f"{field_name}은(는) 0보다 큰 숫자여야 합니다.")
    return normalized


def _optional_positive_finite(
    value: float | None,
    field_name: str,
) -> float | None:
    if value is None:
        return None
    return _positive_finite(value, field_name)


def _validate_case_metadata(
    case_id: str,
    case_name: str,
    repeat_count: int,
) -> tuple[str, str, int]:
    if not isinstance(case_id, str) or not isinstance(case_name, str):
        raise TypeError("시험 케이스 ID와 이름은 문자열이어야 합니다.")
    normalized_id = case_id.strip()
    normalized_name = case_name.strip()
    if normalized_name and not normalized_id:
        raise ValueError("시험 케이스 이름을 쓰려면 case_id도 필요합니다.")
    if isinstance(repeat_count, bool) or not isinstance(repeat_count, int):
        raise ValueError("시험 반복 횟수는 정수여야 합니다.")
    if not 1 <= repeat_count <= 1_000:
        raise ValueError("시험 반복 횟수는 1~1000회 범위여야 합니다.")
    return normalized_id, normalized_name, repeat_count


@dataclass(frozen=True, slots=True)
class SpectrumPlanItem:
    """One model-independent spectrum-analyzer measurement setup.

    Frequencies and bandwidths are stored in Hz. ``None`` for RBW or VBW
    means that the instrument profile should use its verified automatic mode.
    This is a plan only; constructing an item never opens a VISA session.
    """

    instrument: SelectedInstrument
    center_frequency_hz: float
    span_hz: float
    rbw_hz: float | None
    vbw_hz: float | None
    reference_level_dbm: float
    case_id: str = ""
    case_name: str = ""
    repeat_count: int = 1

    def __post_init__(self) -> None:
        if not isinstance(self.instrument, SelectedInstrument):
            raise TypeError("instrument는 SelectedInstrument여야 합니다.")
        if self.instrument.category is not DeviceCategory.SPECTRUM_ANALYZER:
            raise ValueError("스펙트럼 분석기만 이 계획 항목에 사용할 수 있습니다.")

        center = _positive_finite(self.center_frequency_hz, "중심 주파수")
        span = _positive_finite(self.span_hz, "Span")
        if center - (span / 2) < 0:
            raise ValueError(
                "Span이 너무 커서 시작 주파수가 0 Hz보다 작아집니다."
            )
        rbw = _optional_positive_finite(self.rbw_hz, "RBW")
        vbw = _optional_positive_finite(self.vbw_hz, "VBW")

        if isinstance(self.reference_level_dbm, bool):
            raise ValueError("Ref. Level은 유한한 숫자여야 합니다.")
        try:
            reference_level = float(self.reference_level_dbm)
        except (TypeError, ValueError) as exc:
            raise ValueError("Ref. Level은 유한한 숫자여야 합니다.") from exc
        if not math.isfinite(reference_level):
            raise ValueError("Ref. Level은 유한한 숫자여야 합니다.")

        object.__setattr__(self, "center_frequency_hz", center)
        object.__setattr__(self, "span_hz", span)
        object.__setattr__(self, "rbw_hz", rbw)
        object.__setattr__(self, "vbw_hz", vbw)
        object.__setattr__(self, "reference_level_dbm", reference_level)
        case_id, case_name, repeat_count = _validate_case_metadata(
            self.case_id,
            self.case_name,
            self.repeat_count,
        )
        object.__setattr__(self, "case_id", case_id)
        object.__setattr__(self, "case_name", case_name)
        object.__setattr__(self, "repeat_count", repeat_count)

    @property
    def start_frequency_hz(self) -> float:
        return self.center_frequency_hz - (self.span_hz / 2)

    @property
    def stop_frequency_hz(self) -> float:
        return self.center_frequency_hz + (self.span_hz / 2)


@dataclass(frozen=True, slots=True)
class SignalGeneratorPlanItem:
    """One model-independent CW signal-generator output setup.

    The values describe a requested plan only. ``power_dbm`` is a source
    setpoint and must not be treated as measured power at the DUT.
    """

    instrument: SelectedInstrument
    frequency_hz: float
    power_dbm: float
    dwell_seconds: float
    case_id: str = ""
    case_name: str = ""
    repeat_count: int = 1

    def __post_init__(self) -> None:
        if not isinstance(self.instrument, SelectedInstrument):
            raise TypeError("instrument는 SelectedInstrument여야 합니다.")
        if self.instrument.category is not DeviceCategory.SIGNAL_GENERATOR:
            raise ValueError("신호발생기만 이 계획 항목에 사용할 수 있습니다.")

        frequency = _positive_finite(self.frequency_hz, "출력 주파수")
        dwell = _positive_finite(self.dwell_seconds, "유지 시간")
        if dwell > 3600:
            raise ValueError("유지 시간은 한 단계당 3600초 이하여야 합니다.")

        if isinstance(self.power_dbm, bool):
            raise ValueError("출력 레벨은 유한한 숫자여야 합니다.")
        try:
            power = float(self.power_dbm)
        except (TypeError, ValueError) as exc:
            raise ValueError("출력 레벨은 유한한 숫자여야 합니다.") from exc
        if not math.isfinite(power):
            raise ValueError("출력 레벨은 유한한 숫자여야 합니다.")

        object.__setattr__(self, "frequency_hz", frequency)
        object.__setattr__(self, "power_dbm", power)
        object.__setattr__(self, "dwell_seconds", dwell)
        case_id, case_name, repeat_count = _validate_case_metadata(
            self.case_id,
            self.case_name,
            self.repeat_count,
        )
        object.__setattr__(self, "case_id", case_id)
        object.__setattr__(self, "case_name", case_name)
        object.__setattr__(self, "repeat_count", repeat_count)


@dataclass(frozen=True, slots=True)
class PlanFieldValue:
    """One normalized value stored without an executable SCPI command."""

    field_id: str
    value: PlanScalar
    unit: str = ""

    def __post_init__(self) -> None:
        if not isinstance(self.field_id, str) or not self.field_id.strip():
            raise ValueError("계획 필드 값에는 field_id가 필요합니다.")
        if isinstance(self.value, float) and not math.isfinite(self.value):
            raise ValueError(f"{self.field_id} 값은 유한해야 합니다.")
        if not isinstance(self.value, (str, float, int, bool)):
            raise TypeError(f"{self.field_id} 값 형식이 올바르지 않습니다.")
        if not isinstance(self.unit, str):
            raise TypeError("계획 필드 단위는 문자열이어야 합니다.")


def _normalize_plan_values(
    values: tuple[PlanFieldValue, ...],
    definitions: tuple[PlanFieldDefinition, ...],
    *,
    section_name: str,
) -> tuple[PlanFieldValue, ...]:
    if not isinstance(values, tuple):
        raise TypeError(f"{section_name} 값은 PlanFieldValue 튜플이어야 합니다.")
    raw_by_id: dict[str, PlanScalar] = {}
    for value in values:
        if not isinstance(value, PlanFieldValue):
            raise TypeError(f"{section_name} 값은 PlanFieldValue여야 합니다.")
        if value.field_id in raw_by_id:
            raise ValueError(f"{section_name}에 같은 필드가 두 번 있습니다: {value.field_id}")
        raw_by_id[value.field_id] = value.value

    definitions_by_id = {definition.field_id: definition for definition in definitions}
    unknown = set(raw_by_id) - set(definitions_by_id)
    if unknown:
        raise ValueError(
            f"{section_name}에 등록되지 않은 필드가 있습니다: "
            f"{', '.join(sorted(unknown))}"
        )

    normalized: list[PlanFieldValue] = []
    for definition in definitions:
        raw = raw_by_id.get(definition.field_id)
        value = definition.normalize(raw)
        if value is None:
            continue
        normalized.append(
            PlanFieldValue(
                field_id=definition.field_id,
                value=value,
                unit=definition.unit,
            )
        )
    return tuple(normalized)


def _validate_related_plan_values(
    category: DeviceCategory,
    method_id: str,
    values: Mapping[str, PlanScalar],
) -> None:
    """Validate relationships that cannot be checked one field at a time."""

    def require(field_id: str, label: str) -> PlanScalar:
        value = values.get(field_id)
        if value is None or (isinstance(value, str) and not value.strip()):
            raise ValueError(f"{label} 값을 입력해 주세요.")
        return value

    def number(field_id: str) -> float | None:
        value = values.get(field_id)
        if value is None or isinstance(value, bool):
            return None
        return float(value)

    if category is DeviceCategory.SPECTRUM_ANALYZER:
        if values.get("rbw_mode") == "수동 지정":
            require("rbw_hz", "수동 RBW")
        if values.get("vbw_mode") == "수동 지정":
            require("vbw_hz", "수동 VBW")
        if (
            method_id == "channel_power_obw"
            and values.get("detector") != "RMS"
        ):
            raise ValueError(
                "Channel Power·OBW 계획은 RMS Detector를 선택해 주세요."
            )

    elif category is DeviceCategory.SIGNAL_GENERATOR:
        frequency_mode = values.get("frequency_mode")
        if frequency_mode == "균일 Sweep":
            start = number("sweep_start_hz")
            stop = number("sweep_stop_hz")
            if start is None or stop is None:
                raise ValueError("균일 Sweep의 시작·종료 주파수를 입력해 주세요.")
            if stop <= start:
                raise ValueError("Sweep 종료 주파수는 시작 주파수보다 커야 합니다.")
            if number("sweep_step_hz") is None and number("sweep_points") is None:
                raise ValueError("Sweep 간격 또는 포인트 수 중 하나를 입력해 주세요.")
        if method_id == "rf_pulse_output" or values.get(
            "pulse_modulation"
        ) not in {None, "OFF", "지원 여부 확인"}:
            period = number("pulse_period_seconds")
            width = number("pulse_width_seconds")
            if period is None or width is None:
                raise ValueError("Pulse Period와 Pulse Width를 입력해 주세요.")
            if width > period:
                raise ValueError("Pulse Width는 Pulse Period보다 클 수 없습니다.")
        if values.get("reference_clock") in {"External 10 MHz", "공통 기준기"}:
            if values.get("reference_lock_confirmed") is not True:
                raise ValueError("외부 기준 Clock의 Lock 상태를 확인해 주세요.")

    elif category is DeviceCategory.FUNCTION_GENERATOR:
        pulse_selected = (
            method_id == "pulse_timing"
            or values.get("waveform_shape") == "Pulse"
        )
        if pulse_selected:
            period = number("pulse_period_seconds")
            width = number("pulse_width_seconds")
            if period is None or width is None:
                raise ValueError("Pulse Period와 Pulse Width를 입력해 주세요.")
            if width > period:
                raise ValueError("Pulse Width는 Pulse Period보다 클 수 없습니다.")
        high = number("high_level_v")
        low = number("low_level_v")
        if (high is None) != (low is None):
            raise ValueError("High 전압과 Low 전압은 함께 입력해 주세요.")
        if high is not None and low is not None:
            if high <= low:
                raise ValueError("High 전압은 Low 전압보다 커야 합니다.")
            if values.get("amplitude_unit") == "Vpp":
                amplitude = number("amplitude_value")
                if amplitude is not None and not math.isclose(
                    amplitude,
                    high - low,
                    rel_tol=1e-6,
                    abs_tol=1e-12,
                ):
                    raise ValueError(
                        "Vpp 진폭은 High 전압과 Low 전압의 차이와 같아야 합니다."
                    )

    elif category is DeviceCategory.OSCILLOSCOPE:
        if method_id == "timing_edges":
            require("edge_threshold_definition", "Edge·Pulse 기준 레벨")
            require("sample_rate", "Sample Rate")

    elif category is DeviceCategory.DIGITAL_MULTIMETER:
        function = str(values.get("measurement_function", ""))
        if values.get("range_mode") == "고정 Range":
            require("range_value", "고정 Range")
        expected_unit = (
            "V"
            if "Voltage" in function
            else "A"
            if "Current" in function
            else "Ω"
            if "Resistance" in function
            else None
        )
        if expected_unit and values.get("range_unit") != expected_unit:
            raise ValueError(
                f"{function}의 Range 단위는 {expected_unit}를 선택해 주세요."
            )
        if "Current" in function and values.get("current_fuse_checked") is not True:
            raise ValueError("전류 측정 전 Jack과 Fuse를 확인해 주세요.")
        if function == "4W Resistance":
            connection = str(values.get("connection_method", "")).casefold()
            if "4" not in connection and "four" not in connection:
                raise ValueError("4W Resistance는 4-wire 배선을 명시해 주세요.")
        if (
            values.get("sample_source") == "Timer"
            and number("sample_interval_seconds") is None
        ):
            raise ValueError("Timer Sample의 Sample 간격을 입력해 주세요.")

    elif category is DeviceCategory.POWER_SUPPLY:
        setpoint = number("voltage_setpoint_v")
        ovp = number("ovp_v")
        if ovp is not None and setpoint is not None and ovp <= setpoint:
            raise ValueError("OVP는 정상 전압 설정값보다 높아야 합니다.")
        current_limit = number("current_limit_a")
        ocp = number("ocp_a")
        if (
            ocp is not None
            and current_limit is not None
            and ocp < current_limit
        ):
            raise ValueError("OCP는 정상 전류 한계보다 낮게 설정할 수 없습니다.")
        if method_id == "dc_regulation_transient":
            require("ac_input_conditions", "AC 입력·Line 조건")
            if (
                number("load_slew_a_per_s") is None
                and number("load_edge_seconds") is None
            ):
                raise ValueError("부하 Slew 또는 부하 Edge 시간을 입력해 주세요.")
            require("transient_capture_setup", "과도 응답 측정 경로")

    elif category is DeviceCategory.LCR_METER:
        signal_mode = values.get("signal_level_mode")
        signal_voltage = number("signal_voltage_v")
        signal_current = number("signal_current_a")
        if signal_mode == "Voltage Level":
            if signal_voltage is None:
                raise ValueError("Voltage Level의 측정 신호 전압을 입력해 주세요.")
            if signal_current is not None:
                raise ValueError("Voltage Level에서는 측정 신호 전류를 비워 주세요.")
        elif signal_mode == "Current Level":
            if signal_current is None:
                raise ValueError("Current Level의 측정 신호 전류를 입력해 주세요.")
            if signal_voltage is not None:
                raise ValueError("Current Level에서는 측정 신호 전압을 비워 주세요.")
        bias_state = values.get("dc_bias_state")
        if bias_state in {"내부 Bias", "외부 Bias"}:
            require("dc_bias_voltage_v", "DC Bias 전압")
        if bias_state == "외부 Bias":
            require("external_bias_current_limit_a", "외부 Bias 전류 제한")
            require("external_bias_polarity", "외부 Bias 극성·연결")
            require("stored_energy_notes", "저장 에너지·잔류전압")
        if values.get("range_mode") == "고정 Range":
            require("impedance_range_ohm", "고정 Impedance Range")

    elif category is DeviceCategory.NETWORK_ANALYZER:
        sweep_type = values.get("sweep_type")
        if sweep_type == "Segment":
            require("segment_table", "Segment Sweep 표")
        if sweep_type == "Power":
            require("power_sweep_range", "Power Sweep 범위")
        if method_id == "vna_phase_delay":
            require("group_delay_aperture_hz", "Group Delay Aperture")


@dataclass(frozen=True, slots=True)
class GenericPlanItem:
    """A validated category-specific measurement planning aid.

    The item stores the operator's method, common test context and detailed
    considerations. It is intentionally not an executable command sequence and
    does not assert that a named standard has been satisfied.
    """

    instrument: SelectedInstrument
    method_id: str
    common_values: tuple[PlanFieldValue, ...]
    detail_values: tuple[PlanFieldValue, ...]
    assistance_notice_acknowledged: bool
    case_id: str = ""
    case_name: str = ""
    repeat_count: int = 1

    def __post_init__(self) -> None:
        if not isinstance(self.instrument, SelectedInstrument):
            raise TypeError("instrument는 SelectedInstrument여야 합니다.")
        if not isinstance(self.method_id, str) or not self.method_id.strip():
            raise ValueError("시험 방법을 선택해 주세요.")
        if self.instrument.category is DeviceCategory.UNKNOWN:
            raise ValueError("미분류 장비에는 상세 측정 계획을 만들 수 없습니다.")
        if self.assistance_notice_acknowledged is not True:
            raise ValueError(
                "이 계획이 표준 준수를 보증하지 않는 계획 보조임을 확인해 주세요."
            )

        template = template_for_instrument(self.instrument)
        try:
            detail_definitions = template.fields_for_method(self.method_id)
        except KeyError as exc:
            raise ValueError(
                f"{self.instrument.category.label_ko}에 등록되지 않은 시험 방법입니다: "
                f"{self.method_id}"
            ) from exc

        normalized_common = _normalize_plan_values(
            self.common_values,
            COMMON_PLAN_FIELDS,
            section_name="공통 계획",
        )
        normalized_detail = _normalize_plan_values(
            self.detail_values,
            detail_definitions,
            section_name="장비별 상세 계획",
        )
        normalized_values = {
            value.field_id: value.value
            for value in (*normalized_common, *normalized_detail)
        }
        _validate_related_plan_values(
            self.instrument.category,
            self.method_id,
            normalized_values,
        )
        object.__setattr__(self, "common_values", normalized_common)
        object.__setattr__(self, "detail_values", normalized_detail)
        case_id, case_name, repeat_count = _validate_case_metadata(
            self.case_id,
            self.case_name,
            self.repeat_count,
        )
        object.__setattr__(self, "case_id", case_id)
        object.__setattr__(self, "case_name", case_name)
        object.__setattr__(self, "repeat_count", repeat_count)

    @classmethod
    def from_raw(
        cls,
        *,
        instrument: SelectedInstrument,
        method_id: str,
        common_values: Mapping[str, PlanScalar | None],
        detail_values: Mapping[str, PlanScalar | None],
        assistance_notice_acknowledged: bool,
        case_id: str = "",
        case_name: str = "",
        repeat_count: int = 1,
    ) -> GenericPlanItem:
        if not isinstance(common_values, Mapping) or not isinstance(
            detail_values,
            Mapping,
        ):
            raise TypeError("계획 입력값은 field_id와 값의 mapping이어야 합니다.")

        def as_field_values(
            raw_values: Mapping[str, PlanScalar | None],
        ) -> tuple[PlanFieldValue, ...]:
            return tuple(
                PlanFieldValue(
                    field_id=field_id,
                    value="" if raw_value is None else raw_value,
                )
                for field_id, raw_value in raw_values.items()
            )

        return cls(
            instrument=instrument,
            method_id=method_id,
            common_values=as_field_values(common_values),
            detail_values=as_field_values(detail_values),
            assistance_notice_acknowledged=assistance_notice_acknowledged,
            case_id=case_id,
            case_name=case_name,
            repeat_count=repeat_count,
        )

    @property
    def category(self) -> DeviceCategory:
        return self.instrument.category

    @property
    def method_label_ko(self) -> str:
        return template_for_instrument(self.instrument).method_by_id(
            self.method_id
        ).label_ko

    def value_for(self, field_id: str) -> PlanScalar:
        for field_value in (*self.common_values, *self.detail_values):
            if field_value.field_id == field_id:
                return field_value.value
        raise KeyError(f"계획에 없는 필드입니다: {field_id}")

    def values_dict(self) -> dict[str, PlanScalar]:
        return {
            field_value.field_id: field_value.value
            for field_value in (*self.common_values, *self.detail_values)
        }


MeasurementPlanItem: TypeAlias = (
    SpectrumPlanItem | SignalGeneratorPlanItem | GenericPlanItem
)


@dataclass(frozen=True, slots=True)
class MeasurementTestCase:
    """An explicit group of device settings that run as one test condition."""

    case_id: str
    case_name: str
    repeat_count: int
    items: tuple[MeasurementPlanItem, ...]

    def __post_init__(self) -> None:
        case_id, case_name, repeat_count = _validate_case_metadata(
            self.case_id,
            self.case_name,
            self.repeat_count,
        )
        if not case_id:
            raise ValueError("실행 가능한 시험 케이스에는 case_id가 필요합니다.")
        if not self.items:
            raise ValueError("시험 케이스에는 장비 설정이 하나 이상 필요합니다.")
        executable_resources: set[str] = set()
        for item in self.items:
            if not isinstance(
                item,
                (SpectrumPlanItem, SignalGeneratorPlanItem, GenericPlanItem),
            ):
                raise TypeError("지원하지 않는 시험 계획 항목입니다.")
            if item.case_id and item.case_id != case_id:
                raise ValueError("서로 다른 case_id의 항목을 한 케이스에 넣을 수 없습니다.")
            if item.repeat_count != repeat_count:
                raise ValueError("한 시험 케이스의 반복 횟수는 모두 같아야 합니다.")
            if isinstance(item, (SpectrumPlanItem, SignalGeneratorPlanItem)):
                resource = item.instrument.resource
                if resource in executable_resources:
                    raise ValueError(
                        f"한 시험 케이스에 같은 장비 설정이 중복됐습니다: {resource}"
                    )
                executable_resources.add(resource)
        object.__setattr__(self, "case_id", case_id)
        object.__setattr__(self, "case_name", case_name)
        object.__setattr__(self, "repeat_count", repeat_count)
