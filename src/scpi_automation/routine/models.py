from __future__ import annotations

import math
from dataclasses import dataclass
from enum import Enum
from typing import TypeAlias

from scpi_automation.identity import DeviceCategory


MIN_STEP_SECONDS = 0.1
MAX_STEP_SECONDS = 3600.0


def _validated_step_seconds(value: float, field_name: str) -> float:
    """Return a normalized duration after applying routine safety bounds."""

    if isinstance(value, bool):
        raise ValueError(f"{field_name}은(는) 초 단위 숫자여야 합니다.")
    try:
        normalized = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{field_name}은(는) 초 단위 숫자여야 합니다.") from exc
    if not math.isfinite(normalized):
        raise ValueError(f"{field_name}은(는) 유한한 값이어야 합니다.")
    if not MIN_STEP_SECONDS <= normalized <= MAX_STEP_SECONDS:
        raise ValueError(
            f"{field_name}은(는) {MIN_STEP_SECONDS}초 이상 "
            f"{MAX_STEP_SECONDS:g}초 이하여야 합니다."
        )
    return normalized


class FeatureRisk(str, Enum):
    """Operator-facing risk level for a conceptual routine feature."""

    SAFE = "safe"
    CAUTION = "caution"
    HAZARDOUS = "hazardous"

    @property
    def label_ko(self) -> str:
        return {
            FeatureRisk.SAFE: "안전",
            FeatureRisk.CAUTION: "설정 변경",
            FeatureRisk.HAZARDOUS: "출력 주의",
        }[self]

    @property
    def is_dangerous(self) -> bool:
        return self is FeatureRisk.HAZARDOUS


class FeatureVerification(str, Enum):
    """How far a conceptual feature has been verified for a real instrument."""

    PROFILE_REQUIRED = "profile_required"
    BENCH_OBSERVED = "bench_observed"
    VERIFIED = "verified"

    @property
    def label_ko(self) -> str:
        return {
            FeatureVerification.PROFILE_REQUIRED: "장비별 확인 필요",
            FeatureVerification.BENCH_OBSERVED: "시험 사용 이력 있음",
            FeatureVerification.VERIFIED: "검증 완료",
        }[self]


@dataclass(frozen=True, slots=True)
class RoutineParameter:
    """One user-editable argument declared by a candidate command pack."""

    name: str
    value_type: str
    unit: str = ""
    minimum: float | int | None = None
    maximum: float | int | None = None
    choices: tuple[str, ...] = ()
    mapping: tuple[tuple[str, str], ...] = ()
    note_ko: str = ""

    def __post_init__(self) -> None:
        if not self.name.strip():
            raise ValueError("파라미터 이름은 비워둘 수 없습니다.")


@dataclass(frozen=True, slots=True)
class RoutineFeature:
    """A device capability shown to the user, not an executable command."""

    feature_id: str
    category: DeviceCategory
    display_name: str
    description: str
    risk: FeatureRisk
    verification: FeatureVerification = FeatureVerification.PROFILE_REQUIRED
    capability_id: str = ""
    operation: str = ""
    group: str = ""
    scpi_preview: str = ""
    response_type: str = ""
    parameters: tuple[RoutineParameter, ...] = ()
    profile_ids: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        expected_prefix = f"{self.category.value}."
        if not self.feature_id.startswith(expected_prefix):
            raise ValueError(
                f"기능 ID는 장비 분류 접두사 '{expected_prefix}'로 시작해야 합니다."
            )
        if not self.display_name.strip():
            raise ValueError("기능 표시 이름은 비워둘 수 없습니다.")
        if not self.description.strip():
            raise ValueError("기능 설명은 비워둘 수 없습니다.")

    @property
    def is_dangerous(self) -> bool:
        return self.risk.is_dangerous


@dataclass(frozen=True, slots=True)
class SelectedInstrument:
    """An instrument selected for routine composition."""

    resource: str
    category: DeviceCategory
    manufacturer: str = ""
    model: str = ""
    serial: str = ""
    firmware: str = ""
    raw_idn: str = ""
    profile_id: str = ""
    compatibility_status: str = ""
    compatible_capability_ids: tuple[str, ...] = ()
    compatible_operation_ids: tuple[str, ...] = ()
    incompatible_operation_ids: tuple[str, ...] = ()
    unresolved_operation_ids: tuple[str, ...] = ()
    validation_catalog_fingerprint: str = ""
    option_response: str = ""
    option_state: str = "unqueried"

    def __post_init__(self) -> None:
        if not self.resource.strip():
            raise ValueError("선택 장비의 resource 주소는 비워둘 수 없습니다.")
        if self.option_state not in {
            "queried",
            "unsupported",
            "unqueried",
        }:
            raise ValueError(
                "option_state must be queried, unsupported, or unqueried"
            )
        if self.option_state == "queried" and not self.option_response.strip():
            raise ValueError(
                "queried option_state requires an *OPT? response"
            )
        if (
            self.option_state != "queried"
            and self.option_response.strip()
        ):
            raise ValueError(
                "Only queried option_state may contain an *OPT? response"
            )

    @property
    def display_name(self) -> str:
        identity_name = " ".join(
            part.strip() for part in (self.manufacturer, self.model) if part.strip()
        )
        return identity_name or self.resource


@dataclass(frozen=True, slots=True)
class PlanArgumentBinding:
    """One explicit link from a feature parameter to a plan field."""

    parameter_name: str
    field_id: str

    def __post_init__(self) -> None:
        if not self.parameter_name.strip():
            raise ValueError("계획값을 받을 기능 인수 이름은 비워둘 수 없습니다.")
        if not self.field_id.strip():
            raise ValueError("연결할 계획 필드 ID는 비워둘 수 없습니다.")


@dataclass(frozen=True, slots=True)
class SelectedFeature:
    """A feature chosen for one specific instrument."""

    instrument: SelectedInstrument
    feature_id: str
    arguments: tuple[tuple[str, str], ...] = ()
    plan_bindings: tuple[PlanArgumentBinding, ...] = ()
    result_name: str = ""

    def __post_init__(self) -> None:
        if not self.feature_id.strip():
            raise ValueError("선택 기능 ID는 비워둘 수 없습니다.")
        argument_names: set[str] = set()
        for name, _value in self.arguments:
            if not name.strip():
                raise ValueError("기능 인수 이름은 비워둘 수 없습니다.")
            if name in argument_names:
                raise ValueError(f"같은 기능 인수가 두 번 들어 있습니다: {name}")
            argument_names.add(name)
        binding_names: set[str] = set()
        for binding in self.plan_bindings:
            if not isinstance(binding, PlanArgumentBinding):
                raise TypeError("plan_bindings에는 PlanArgumentBinding만 넣을 수 있습니다.")
            if binding.parameter_name in binding_names:
                raise ValueError(
                    "같은 기능 인수에 계획값이 두 번 연결되어 있습니다: "
                    f"{binding.parameter_name}"
                )
            if binding.parameter_name in argument_names:
                raise ValueError(
                    "같은 기능 인수에 고정값과 계획값을 함께 지정할 수 없습니다: "
                    f"{binding.parameter_name}"
                )
            binding_names.add(binding.parameter_name)

    @property
    def device_resource(self) -> str:
        return self.instrument.resource

    @property
    def category(self) -> DeviceCategory:
        return self.instrument.category


@dataclass(frozen=True, slots=True)
class DelayStep:
    """A device-independent delay performed by the future PC executor."""

    seconds: float

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "seconds",
            _validated_step_seconds(self.seconds, "대기 시간"),
        )


@dataclass(frozen=True, slots=True)
class PlanBoundDelayStep:
    """A PC delay whose duration comes from one plan item's dwell field."""

    instrument: SelectedInstrument
    field_id: str = "dwell_seconds"

    def __post_init__(self) -> None:
        if not isinstance(self.instrument, SelectedInstrument):
            raise TypeError("계획 대기 시간의 장비는 SelectedInstrument여야 합니다.")
        if self.field_id != "dwell_seconds":
            raise ValueError(
                "현재 계획 연동 대기는 신호발생기의 dwell_seconds만 지원합니다."
            )
        if self.instrument.category is not DeviceCategory.SIGNAL_GENERATOR:
            raise ValueError("계획 연동 대기는 신호발생기 계획에서만 가져올 수 있습니다.")

    @property
    def device_resource(self) -> str:
        return self.instrument.resource


@dataclass(frozen=True, slots=True)
class WaitForCompletionStep:
    """Wait for pending work on one explicitly selected instrument."""

    instrument: SelectedInstrument
    timeout_seconds: float

    def __post_init__(self) -> None:
        if not isinstance(self.instrument, SelectedInstrument):
            raise TypeError("완료 확인 대상은 SelectedInstrument여야 합니다.")
        object.__setattr__(
            self,
            "timeout_seconds",
            _validated_step_seconds(self.timeout_seconds, "완료 확인 제한 시간"),
        )

    @property
    def device_resource(self) -> str:
        return self.instrument.resource


RoutineStep: TypeAlias = (
    SelectedFeature
    | DelayStep
    | PlanBoundDelayStep
    | WaitForCompletionStep
)


def create_delay(seconds: float) -> DelayStep:
    """Create a validated, device-independent delay step."""

    return DelayStep(seconds=seconds)


def create_plan_bound_delay(
    instrument: SelectedInstrument,
) -> PlanBoundDelayStep:
    """Create a delay linked to this generator's Dwell plan value."""

    return PlanBoundDelayStep(instrument=instrument)


def wait_for_completion(
    instrument: SelectedInstrument,
    timeout_seconds: float,
) -> WaitForCompletionStep:
    """Create a validated completion wait for one explicit instrument."""

    return WaitForCompletionStep(
        instrument=instrument,
        timeout_seconds=timeout_seconds,
    )
