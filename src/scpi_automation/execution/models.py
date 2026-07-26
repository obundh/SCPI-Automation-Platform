from __future__ import annotations

import math
from dataclasses import dataclass
from enum import Enum
from typing import Callable, TypeAlias

from scpi_automation.planning import MeasurementPlanItem
from scpi_automation.routine import RoutineStep, SelectedInstrument


EXECUTION_SCHEMA_VERSION = 2


class ExecutionStatus(str, Enum):
    """Terminal state of one execution attempt."""

    COMPLETED = "completed"
    STOPPED = "stopped"
    EMERGENCY_STOPPED = "emergency_stopped"
    FAILED = "failed"

    @property
    def label_ko(self) -> str:
        return {
            ExecutionStatus.COMPLETED: "완료",
            ExecutionStatus.STOPPED: "사용자 중지",
            ExecutionStatus.EMERGENCY_STOPPED: "긴급 안전정지",
            ExecutionStatus.FAILED: "실패",
        }[self]


@dataclass(frozen=True, slots=True)
class ExecutionPolicy:
    """Finite limits applied by the deterministic execution worker."""

    io_timeout_ms: int = 2_000
    max_run_seconds: float = 86_400.0
    max_response_characters: int = 1_000_000
    max_error_entries: int = 8
    max_expanded_steps: int = 100_000

    def __post_init__(self) -> None:
        if not 1 <= self.io_timeout_ms <= 600_000:
            raise ValueError("VISA Timeout은 1~600000 ms 범위여야 합니다.")
        if (
            isinstance(self.max_run_seconds, bool)
            or not math.isfinite(float(self.max_run_seconds))
            or not 0.1 <= float(self.max_run_seconds) <= 604_800
        ):
            raise ValueError("전체 실행 제한 시간은 0.1초~7일 범위여야 합니다.")
        if not 1 <= self.max_response_characters <= 10_000_000:
            raise ValueError("응답 글자 수 제한은 1~10000000 범위여야 합니다.")
        if not 1 <= self.max_error_entries <= 100:
            raise ValueError("오류 큐 확인 횟수는 1~100 범위여야 합니다.")
        if not 1 <= self.max_expanded_steps <= 1_000_000:
            raise ValueError("전체 확장 단계 상한은 1~1000000 범위여야 합니다.")


@dataclass(frozen=True, slots=True)
class ExecutionEvent:
    sequence: int
    timestamp_utc: str
    level: str
    kind: str
    message: str
    step_index: int | None = None
    total_steps: int = 0
    resource: str = ""
    command: str = ""
    response: str = ""
    feature_id: str = ""
    capability_id: str = ""
    response_type: str = ""
    parsed_value: object | None = None
    unit: str = ""
    measurement_id: str = ""
    case_id: str = ""
    case_name: str = ""
    case_index: int = 0
    repeat_index: int = 0
    repeat_count: int = 0
    template_step_index: int = 0

    def __post_init__(self) -> None:
        if self.sequence < 1 or self.total_steps < 0:
            raise ValueError("실행 로그 순번과 전체 단계 수가 올바르지 않습니다.")
        if self.step_index is not None and self.step_index < 1:
            raise ValueError("실행 로그 단계 번호는 1 이상이어야 합니다.")
        if min(
            self.case_index,
            self.repeat_index,
            self.repeat_count,
            self.template_step_index,
        ) < 0:
            raise ValueError("시험 케이스와 루틴 단계 번호는 음수일 수 없습니다.")


@dataclass(frozen=True, slots=True)
class StepRecord:
    step_index: int
    step_kind: str
    status: str
    started_at_utc: str
    finished_at_utc: str
    duration_ms: float
    resource: str = ""
    feature_id: str = ""
    capability_id: str = ""
    operation: str = ""
    command: str = ""
    response: str = ""
    result_name: str = ""
    response_type: str = ""
    measurement_id: str = ""
    error: str = ""
    case_id: str = ""
    case_name: str = ""
    case_index: int = 0
    repeat_index: int = 0
    repeat_count: int = 0
    template_step_index: int = 0
    applied_plan_bindings: tuple[tuple[str, str, str], ...] = ()

    def __post_init__(self) -> None:
        if self.step_index < 1:
            raise ValueError("실행 단계 번호는 1 이상이어야 합니다.")
        if (
            isinstance(self.duration_ms, bool)
            or not math.isfinite(float(self.duration_ms))
            or self.duration_ms < 0
        ):
            raise ValueError("실행 단계 소요 시간은 0 이상의 유한한 값이어야 합니다.")


ScalarValue: TypeAlias = str | int | float | bool
ParsedValue: TypeAlias = ScalarValue | tuple[ScalarValue, ...]


@dataclass(frozen=True, slots=True)
class MeasurementRecord:
    measurement_id: str
    sequence: int
    timestamp_utc: str
    step_index: int
    resource: str
    manufacturer: str
    model: str
    feature_id: str
    capability_id: str
    operation: str
    result_name: str
    response_type: str
    raw_response: str
    parsed_value: ParsedValue
    unit: str = ""
    status: str = "ok"
    case_id: str = ""
    case_name: str = ""
    case_index: int = 0
    repeat_index: int = 0
    repeat_count: int = 0
    template_step_index: int = 0

    def __post_init__(self) -> None:
        if self.sequence < 1 or self.step_index < 1:
            raise ValueError("측정 순번과 단계 번호는 1 이상이어야 합니다.")

        def validate(value: ParsedValue) -> None:
            if isinstance(value, tuple):
                for item in value:
                    validate(item)
            elif isinstance(value, float) and not math.isfinite(value):
                raise ValueError("측정값에는 NaN 또는 무한대를 저장할 수 없습니다.")

        validate(self.parsed_value)


@dataclass(frozen=True, slots=True)
class SafetyRecord:
    sequence: int
    timestamp_utc: str
    resource: str
    operation_id: str
    command: str
    status: str
    response: str = ""
    message: str = ""

    def __post_init__(self) -> None:
        if self.sequence < 1:
            raise ValueError("안전 종료 기록 순번은 1 이상이어야 합니다.")


@dataclass(frozen=True, slots=True)
class ExecutionResult:
    schema_version: int
    run_id: str
    started_at_utc: str
    finished_at_utc: str
    duration_ms: float
    status: ExecutionStatus
    dry_run: bool
    stop_reason: str
    instruments: tuple[SelectedInstrument, ...]
    routine_steps: tuple[RoutineStep, ...]
    plan_items: tuple[MeasurementPlanItem, ...]
    step_records: tuple[StepRecord, ...]
    measurements: tuple[MeasurementRecord, ...]
    events: tuple[ExecutionEvent, ...]
    executed_steps: tuple[RoutineStep, ...] = ()
    compiled_digest: str = ""
    uses_plan_values: bool = False
    test_case_count: int = 0
    safety_records: tuple[SafetyRecord, ...] = ()

    def __post_init__(self) -> None:
        if self.schema_version != EXECUTION_SCHEMA_VERSION:
            raise ValueError(
                f"지원하지 않는 실행 결과 스키마입니다: {self.schema_version}"
            )
        if not self.run_id.strip():
            raise ValueError("실행 ID는 비워둘 수 없습니다.")
        if (
            isinstance(self.duration_ms, bool)
            or not math.isfinite(float(self.duration_ms))
            or self.duration_ms < 0
        ):
            raise ValueError("전체 실행 소요 시간은 0 이상의 유한한 값이어야 합니다.")

    @property
    def error_count(self) -> int:
        return sum(
            event.level in {"error", "critical"} for event in self.events
        )


ExecutionEventCallback: TypeAlias = Callable[[ExecutionEvent], None]
