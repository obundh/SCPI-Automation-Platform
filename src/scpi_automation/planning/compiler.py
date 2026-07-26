"""Deterministically bind structured test plans to routine templates."""

from __future__ import annotations

import hashlib
import math
from dataclasses import dataclass
from typing import Iterable

from scpi_automation.routine import (
    DelayStep,
    PlanBoundDelayStep,
    RoutineStep,
    SelectedFeature,
    SelectedInstrument,
    select_feature,
)

from .models import (
    GenericPlanItem,
    MeasurementPlanItem,
    MeasurementTestCase,
    SignalGeneratorPlanItem,
    SpectrumPlanItem,
)


MAX_EXPANDED_STEPS = 100_000


class PlanCompilationError(ValueError):
    """Raised before VISA access when a routine cannot use its test plan."""


@dataclass(frozen=True, slots=True)
class AppliedPlanBinding:
    parameter_name: str
    field_id: str
    value: str
    resource: str


@dataclass(frozen=True, slots=True)
class CompiledStepMetadata:
    template_step_index: int
    case_id: str = ""
    case_name: str = ""
    case_index: int = 0
    repeat_index: int = 0
    repeat_count: int = 0
    applied_bindings: tuple[AppliedPlanBinding, ...] = ()


@dataclass(frozen=True, slots=True)
class CompiledRoutine:
    steps: tuple[RoutineStep, ...]
    metadata: tuple[CompiledStepMetadata, ...]
    cases: tuple[MeasurementTestCase, ...]
    digest: str
    uses_plan_values: bool

    def __post_init__(self) -> None:
        if len(self.steps) != len(self.metadata):
            raise ValueError("컴파일된 단계와 단계 메타데이터 수가 다릅니다.")


def _case_groups(
    plan_items: tuple[MeasurementPlanItem, ...],
) -> tuple[MeasurementTestCase, ...]:
    groups: dict[str, list[MeasurementPlanItem]] = {}
    order: list[str] = []
    for index, item in enumerate(plan_items, start=1):
        case_id = item.case_id or f"legacy-single-{index:04d}"
        if case_id not in groups:
            groups[case_id] = []
            order.append(case_id)
        groups[case_id].append(item)

    result: list[MeasurementTestCase] = []
    for index, case_id in enumerate(order, start=1):
        items = tuple(groups[case_id])
        names = {item.case_name for item in items if item.case_name}
        repeats = {item.repeat_count for item in items}
        if len(names) > 1:
            raise PlanCompilationError(
                f"{case_id}: 같은 시험 케이스에 서로 다른 이름이 있습니다."
            )
        if len(repeats) != 1:
            raise PlanCompilationError(
                f"{case_id}: 같은 시험 케이스의 반복 횟수가 서로 다릅니다."
            )
        try:
            result.append(
                MeasurementTestCase(
                    case_id=case_id,
                    case_name=next(iter(names), f"시험 {index:02d}"),
                    repeat_count=next(iter(repeats)),
                    items=items,
                )
            )
        except (TypeError, ValueError) as exc:
            raise PlanCompilationError(str(exc)) from exc
    return tuple(result)


def group_plan_items(
    plan_items: Iterable[MeasurementPlanItem],
) -> tuple[MeasurementTestCase, ...]:
    """Return explicit cases without ever pairing devices by list position."""

    return _case_groups(tuple(plan_items))


def _executable_item_for(
    case: MeasurementTestCase,
    instrument: SelectedInstrument,
) -> SpectrumPlanItem | SignalGeneratorPlanItem:
    matches = tuple(
        item
        for item in case.items
        if (
            isinstance(item, (SpectrumPlanItem, SignalGeneratorPlanItem))
            and item.instrument.resource == instrument.resource
        )
    )
    if not matches:
        raise PlanCompilationError(
            f"{case.case_name}: {instrument.display_name}의 실행 설정이 없습니다."
        )
    if len(matches) > 1:
        raise PlanCompilationError(
            f"{case.case_name}: {instrument.resource} 설정이 중복됐습니다."
        )
    item = matches[0]
    if item.instrument != instrument:
        raise PlanCompilationError(
            f"{case.case_name}: {instrument.resource}의 장비 식별 정보가 "
            "현재 선택 장비와 다릅니다."
        )
    return item


def _plan_value(
    item: SpectrumPlanItem | SignalGeneratorPlanItem,
    field_id: str,
) -> float:
    spectrum_fields = {
        "center_frequency_hz",
        "span_hz",
        "start_frequency_hz",
        "stop_frequency_hz",
        "rbw_hz",
        "vbw_hz",
        "reference_level_dbm",
    }
    generator_fields = {
        "frequency_hz",
        "power_dbm",
        "dwell_seconds",
    }
    allowed = (
        spectrum_fields
        if isinstance(item, SpectrumPlanItem)
        else generator_fields
    )
    if field_id not in allowed:
        raise PlanCompilationError(
            f"{item.instrument.display_name} 계획에는 {field_id} 실행값이 없습니다."
        )
    value = getattr(item, field_id)
    if value is None:
        label = "RBW" if field_id == "rbw_hz" else "VBW"
        raise PlanCompilationError(
            f"{item.case_name or item.case_id or '시험 계획'}: {label}가 자동으로 "
            "설정되어 있어 수동 Set 루틴에 값을 넣을 수 없습니다. 계획에서 "
            f"{label} 값을 입력하거나 검증된 {label} Auto 기능을 루틴에 넣어 주세요."
        )
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise PlanCompilationError(f"{field_id} 계획값은 숫자여야 합니다.")
    normalized = float(value)
    if not math.isfinite(normalized):
        raise PlanCompilationError(f"{field_id} 계획값은 유한한 숫자여야 합니다.")
    return normalized


def _number_text(value: float) -> str:
    return format(value, ".15g")


def _has_plan_bindings(steps: tuple[RoutineStep, ...]) -> bool:
    return any(
        (
            isinstance(step, SelectedFeature)
            and bool(step.plan_bindings)
        )
        or isinstance(step, PlanBoundDelayStep)
        for step in steps
    )


def _digest(
    steps: tuple[RoutineStep, ...],
    metadata: tuple[CompiledStepMetadata, ...],
) -> str:
    return hashlib.sha256(repr((steps, metadata)).encode("utf-8")).hexdigest()


def compile_routine_with_plan(
    routine_steps: Iterable[RoutineStep],
    plan_items: Iterable[MeasurementPlanItem],
    *,
    selected_instruments: Iterable[SelectedInstrument] = (),
    max_expanded_steps: int = MAX_EXPANDED_STEPS,
) -> CompiledRoutine:
    """Resolve every plan value before any caller is allowed to open VISA."""

    template = tuple(routine_steps)
    plans = tuple(plan_items)
    if not template:
        raise PlanCompilationError("실행할 루틴 단계가 없습니다.")
    if max_expanded_steps < 1:
        raise ValueError("최대 확장 단계 수는 1 이상이어야 합니다.")

    trusted = {
        instrument.resource: instrument
        for instrument in selected_instruments
    }
    if trusted:
        for item in plans:
            current = trusted.get(item.instrument.resource)
            if current is None or current != item.instrument:
                raise PlanCompilationError(
                    f"{item.instrument.resource}: 시험 계획의 장비가 현재 선택 "
                    "장비와 일치하지 않습니다."
                )

    if not _has_plan_bindings(template):
        metadata = tuple(
            CompiledStepMetadata(template_step_index=index)
            for index, _step in enumerate(template, start=1)
        )
        return CompiledRoutine(
            steps=template,
            metadata=metadata,
            cases=(),
            digest=_digest(template, metadata),
            uses_plan_values=False,
        )

    if not plans:
        raise PlanCompilationError(
            "루틴에 ‘시험 계획에서 가져오기’가 있지만 만든 시험 케이스가 없습니다."
        )
    bound_resources = {
        step.instrument.resource
        for step in template
        if (
            isinstance(step, SelectedFeature)
            and step.plan_bindings
        )
        or isinstance(step, PlanBoundDelayStep)
    }
    if len(bound_resources) > 1 and any(not item.case_id for item in plans):
        raise PlanCompilationError(
            "여러 장비의 계획값을 함께 쓰는 루틴입니다. 장비별 평면 목록을 "
            "순서로 짝짓지 않으므로, 계획서에서 같은 시험 케이스로 묶어 주세요."
        )

    cases = _case_groups(plans)
    expanded_count = sum(case.repeat_count * len(template) for case in cases)
    if expanded_count > max_expanded_steps:
        raise PlanCompilationError(
            f"시험 케이스와 반복을 펼치면 {expanded_count}단계가 됩니다. "
            f"안전 상한 {max_expanded_steps}단계 이하로 줄여 주세요."
        )

    concrete_steps: list[RoutineStep] = []
    metadata: list[CompiledStepMetadata] = []
    for case_index, case in enumerate(cases, start=1):
        for repeat_index in range(1, case.repeat_count + 1):
            for template_index, step in enumerate(template, start=1):
                applied: list[AppliedPlanBinding] = []
                if isinstance(step, SelectedFeature) and step.plan_bindings:
                    item = _executable_item_for(case, step.instrument)
                    arguments = dict(step.arguments)
                    for binding in step.plan_bindings:
                        value_text = _number_text(
                            _plan_value(item, binding.field_id)
                        )
                        arguments[binding.parameter_name] = value_text
                        applied.append(
                            AppliedPlanBinding(
                                parameter_name=binding.parameter_name,
                                field_id=binding.field_id,
                                value=value_text,
                                resource=step.instrument.resource,
                            )
                        )
                    try:
                        concrete: RoutineStep = select_feature(
                            step.instrument,
                            step.feature_id,
                            arguments=arguments,
                            result_name=step.result_name,
                        )
                    except (KeyError, TypeError, ValueError) as exc:
                        raise PlanCompilationError(
                            f"{case.case_name} · {step.instrument.display_name}: {exc}"
                        ) from exc
                elif isinstance(step, PlanBoundDelayStep):
                    item = _executable_item_for(case, step.instrument)
                    seconds = _plan_value(item, step.field_id)
                    try:
                        concrete = DelayStep(seconds=seconds)
                    except (TypeError, ValueError) as exc:
                        raise PlanCompilationError(
                            f"{case.case_name} · Dwell 대기 시간: {exc}"
                        ) from exc
                    applied.append(
                        AppliedPlanBinding(
                            parameter_name="seconds",
                            field_id=step.field_id,
                            value=_number_text(seconds),
                            resource=step.instrument.resource,
                        )
                    )
                else:
                    concrete = step
                concrete_steps.append(concrete)
                metadata.append(
                    CompiledStepMetadata(
                        template_step_index=template_index,
                        case_id=case.case_id,
                        case_name=case.case_name,
                        case_index=case_index,
                        repeat_index=repeat_index,
                        repeat_count=case.repeat_count,
                        applied_bindings=tuple(applied),
                    )
                )

    normalized_steps = tuple(concrete_steps)
    normalized_metadata = tuple(metadata)
    return CompiledRoutine(
        steps=normalized_steps,
        metadata=normalized_metadata,
        cases=cases,
        digest=_digest(normalized_steps, normalized_metadata),
        uses_plan_values=True,
    )
