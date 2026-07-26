from __future__ import annotations

import re
import time
import uuid
from contextlib import ExitStack
from dataclasses import dataclass, replace
from datetime import datetime, timezone
from threading import Event
from typing import Callable, ContextManager, Iterable, Mapping

from scpi_automation.identity import (
    DeviceCategory,
    InstrumentIdentity,
    InstrumentProfile,
    parse_idn_response,
    profile_by_id,
)
from scpi_automation.planning import (
    CompiledStepMetadata,
    MeasurementPlanItem,
    compile_routine_with_plan,
)
from scpi_automation.routine import (
    DelayStep,
    PlanBoundDelayStep,
    RoutineFeature,
    RoutineStep,
    SelectedFeature,
    SelectedInstrument,
    WaitForCompletionStep,
    feature_by_id,
    select_feature,
)
from scpi_automation.transport import open_resource_session
from scpi_automation.validation import (
    load_local_extension_registry,
    merge_profile_extensions,
    profile_fingerprint,
)

from .codec import (
    CommandCompileError,
    parse_query_response,
    render_feature_command,
)
from .models import (
    EXECUTION_SCHEMA_VERSION,
    ExecutionEvent,
    ExecutionEventCallback,
    ExecutionPolicy,
    ExecutionResult,
    ExecutionStatus,
    MeasurementRecord,
    SafetyRecord,
    StepRecord,
)


class ExecutionPreflightError(ValueError):
    """Raised before the first routine command is sent."""


class _ExecutionAbort(RuntimeError):
    def __init__(self, status: ExecutionStatus, message: str) -> None:
        super().__init__(message)
        self.status = status


@dataclass(frozen=True, slots=True)
class _ResolvedFeatureStep:
    index: int
    selected: SelectedFeature
    feature: RoutineFeature
    command: str


@dataclass(frozen=True, slots=True)
class _ResolvedDelayStep:
    index: int
    seconds: float


@dataclass(frozen=True, slots=True)
class _ResolvedWaitStep:
    index: int
    instrument: SelectedInstrument
    timeout_seconds: float
    operation_id: str
    command: str
    response_type: str


_ResolvedStep = _ResolvedFeatureStep | _ResolvedDelayStep | _ResolvedWaitStep


@dataclass(frozen=True, slots=True)
class _SafetyAction:
    instrument: SelectedInstrument
    operation_id: str
    command: str
    readback_operation_id: str = ""
    readback_command: str = ""


_SAFETY_CAPABILITY_BY_PROFILE = {
    "kikusui_pmx35_3a": "output.state",
    "rs_smb100a": "rf.output.state",
    "keysight_e36312a": "channel.output.state",
    "keysight_33500_series": "output.state",
    "keysight_e4980a": "bias.dc.state",
    "keysight_n52xx_pna": "rf.output.state",
    "rs_hmp2000_hmp4000": "output.master_state",
}
_ENERGY_SOURCE_CATEGORIES = frozenset(
    {
        DeviceCategory.SIGNAL_GENERATOR,
        DeviceCategory.FUNCTION_GENERATOR,
        DeviceCategory.POWER_SUPPLY,
        DeviceCategory.LCR_METER,
        DeviceCategory.NETWORK_ANALYZER,
    }
)
_VALIDATED_STATUSES = frozenset(
    {"hardware_validated", "hardware_validated_partial"}
)


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds")


def _identity_values(identity: InstrumentIdentity) -> tuple[str, ...]:
    return tuple(
        value.strip().casefold()
        for value in (
            identity.raw,
            identity.manufacturer,
            identity.model,
            identity.serial,
            identity.firmware,
        )
    )


def _selected_identity(instrument: SelectedInstrument) -> InstrumentIdentity:
    return InstrumentIdentity(
        raw=instrument.raw_idn,
        manufacturer=instrument.manufacturer,
        model=instrument.model,
        serial=instrument.serial,
        firmware=instrument.firmware,
    )


def _effective_profile(instrument: SelectedInstrument) -> InstrumentProfile:
    profile = profile_by_id(instrument.profile_id)
    if profile is None:
        raise ExecutionPreflightError(
            f"{instrument.display_name}: 현재 후보 명령팩을 찾지 못했습니다."
        )
    try:
        registry = load_local_extension_registry()
    except (OSError, ValueError):
        registry = None
    if registry is None:
        return profile
    option_response = (
        instrument.option_response
        if instrument.option_state == "queried"
        else ""
    )
    records = registry.for_identity(
        profile.profile_id,
        _selected_identity(instrument),
        option_response,
        instrument.option_state,
    )
    return merge_profile_extensions(profile, records)


def _authoritative_feature(
    selected: SelectedFeature,
    profile: InstrumentProfile,
) -> RoutineFeature:
    feature = feature_by_id(
        selected.feature_id,
        selected.instrument.profile_id,
    )
    capability = next(
        (
            item
            for item in profile.capabilities
            if item.capability_id == feature.capability_id
        ),
        None,
    )
    if capability is None:
        raise ExecutionPreflightError(
            f"{feature.display_name}: 현재 후보 명령팩에서 기능을 찾지 못했습니다."
        )
    operation = next(
        (item for item in capability.operations if item.name == feature.operation),
        None,
    )
    if operation is None:
        raise ExecutionPreflightError(
            f"{feature.display_name}: 현재 후보 명령팩에서 operation을 찾지 못했습니다."
        )
    if operation.binary:
        raise ExecutionPreflightError(
            f"{feature.display_name}: Binary 전송은 V1 실행기에서 지원하지 않습니다."
        )
    return replace(
        feature,
        scpi_preview=operation.scpi,
        response_type=operation.response_type,
    )


def _normalized_scpi(command: str) -> str:
    return "".join(command.split()).upper()


def _find_query(
    profile: InstrumentProfile,
    instrument: SelectedInstrument,
    normalized_command: str,
    *,
    require_pass: bool,
) -> tuple[str, str, str] | None:
    for capability in profile.capabilities:
        for operation in capability.operations:
            if (
                operation.name == "query"
                and _normalized_scpi(operation.scpi) == normalized_command
            ):
                operation_id = f"{capability.capability_id}::query"
                if (
                    require_pass
                    and operation_id
                    not in instrument.compatible_operation_ids
                ):
                    continue
                return operation_id, operation.scpi.strip(), operation.response_type
    return None


def _capability_unit(
    profile: InstrumentProfile,
    capability_id: str,
) -> str:
    return next(
        (
            capability.unit.strip()
            for capability in profile.capabilities
            if capability.capability_id == capability_id
        ),
        "",
    )


def _check_profile_binding(
    instrument: SelectedInstrument,
    profile: InstrumentProfile,
    *,
    dry_run: bool,
) -> None:
    if instrument.compatibility_status == "demo_catalog_preview":
        if not dry_run:
            raise ExecutionPreflightError(
                f"{instrument.display_name}: 데모 장비에는 실제 명령을 보낼 수 없습니다."
            )
        return
    if instrument.compatibility_status not in _VALIDATED_STATUSES:
        raise ExecutionPreflightError(
            f"{instrument.display_name}: 실장비에서 operation별 검증을 먼저 완료해 주세요."
        )
    current_fingerprint = profile_fingerprint(profile)
    if (
        not instrument.validation_catalog_fingerprint
        or instrument.validation_catalog_fingerprint != current_fingerprint
    ):
        raise ExecutionPreflightError(
            f"{instrument.display_name}: 검증 이후 후보 명령팩이 바뀌었습니다. "
            "현재 매뉴얼 명령으로 다시 검증해 주세요."
        )
    if not instrument.compatible_operation_ids:
        raise ExecutionPreflightError(
            f"{instrument.display_name}: 실행 가능한 검증 통과 operation이 없습니다."
        )


def _collect_instruments(
    instruments: Iterable[SelectedInstrument],
    routine_steps: Iterable[RoutineStep],
    plan_items: Iterable[MeasurementPlanItem],
) -> tuple[SelectedInstrument, ...]:
    by_resource: dict[str, SelectedInstrument] = {}

    def add(instrument: SelectedInstrument) -> None:
        existing = by_resource.get(instrument.resource)
        if existing is not None and existing != instrument:
            raise ExecutionPreflightError(
                f"{instrument.resource}: 같은 VISA 주소에 서로 다른 장비 정보가 있습니다."
            )
        by_resource[instrument.resource] = instrument

    for instrument in instruments:
        add(instrument)
    for step in routine_steps:
        if isinstance(
            step,
            (SelectedFeature, PlanBoundDelayStep, WaitForCompletionStep),
        ):
            selected = by_resource.get(step.instrument.resource)
            if selected is None:
                raise ExecutionPreflightError(
                    f"{step.instrument.resource}: 루틴 장비가 현재 실행 대상으로 "
                    "선택되어 있지 않습니다."
                )
            if selected != step.instrument:
                raise ExecutionPreflightError(
                    f"{step.instrument.resource}: 루틴의 장비 식별 정보가 현재 "
                    "선택 장비와 다릅니다."
                )
    # Plans may provide values only. They never grant permission to control an
    # additional resource; compile_routine_with_plan verifies them against the
    # explicitly selected instrument snapshot.
    return tuple(by_resource.values())


def _resolve_steps(
    routine_steps: tuple[RoutineStep, ...],
    profiles: Mapping[str, InstrumentProfile],
    *,
    dry_run: bool,
) -> tuple[_ResolvedStep, ...]:
    resolved: list[_ResolvedStep] = []
    for index, step in enumerate(routine_steps, start=1):
        if isinstance(step, DelayStep):
            resolved.append(_ResolvedDelayStep(index=index, seconds=step.seconds))
            continue
        if isinstance(step, PlanBoundDelayStep):
            raise ExecutionPreflightError(
                "계획 연동 대기가 실행 전에 실제 시간으로 변환되지 않았습니다."
            )
        if isinstance(step, WaitForCompletionStep):
            profile = profiles[step.instrument.resource]
            found = _find_query(
                profile,
                step.instrument,
                "*OPC?",
                require_pass=not (
                    dry_run
                    and step.instrument.compatibility_status
                    == "demo_catalog_preview"
                ),
            )
            if found is None:
                raise ExecutionPreflightError(
                    f"{step.instrument.display_name}: 앞 명령 완료 확인은 "
                    "검증된 *OPC? operation이 있을 때만 실행할 수 있습니다."
                )
            operation_id, command, response_type = found
            resolved.append(
                _ResolvedWaitStep(
                    index=index,
                    instrument=step.instrument,
                    timeout_seconds=step.timeout_seconds,
                    operation_id=operation_id,
                    command=command,
                    response_type=response_type or "integer",
                )
            )
            continue
        if not isinstance(step, SelectedFeature):
            raise ExecutionPreflightError(
                f"{index}번 단계 형식을 실행기가 이해하지 못했습니다."
            )
        selected = select_feature(
            step.instrument,
            step.feature_id,
            arguments=step.arguments,
            result_name=step.result_name,
        )
        profile = profiles[step.instrument.resource]
        feature = _authoritative_feature(selected, profile)
        operation_id = f"{feature.capability_id}::{feature.operation}"
        if (
            step.instrument.compatibility_status != "demo_catalog_preview"
            and operation_id not in step.instrument.compatible_operation_ids
        ):
            raise ExecutionPreflightError(
                f"{feature.display_name}: 실장비에서 PASS가 아닌 operation입니다."
            )
        try:
            command = render_feature_command(feature, selected.arguments)
        except CommandCompileError as exc:
            raise ExecutionPreflightError(
                f"{feature.display_name}: {exc}"
            ) from exc
        resolved.append(
            _ResolvedFeatureStep(
                index=index,
                selected=selected,
                feature=feature,
                command=command,
            )
        )
    return tuple(resolved)


def _selector_values(
    instrument: SelectedInstrument,
    resolved_steps: tuple[_ResolvedStep, ...],
    safety_feature: RoutineFeature,
) -> tuple[dict[str, str], ...]:
    selector_parameters = tuple(
        parameter
        for parameter in safety_feature.parameters
        if parameter.name != "state"
    )
    if not selector_parameters:
        return ({},)
    if len(selector_parameters) != 1:
        raise ExecutionPreflightError(
            f"{instrument.display_name}: 안전 종료 selector가 복잡해 자동 종료할 수 없습니다."
        )
    parameter = selector_parameters[0]
    observed = {
        dict(step.selected.arguments).get(parameter.name, "")
        for step in resolved_steps
        if (
            isinstance(step, _ResolvedFeatureStep)
            and step.selected.instrument.resource == instrument.resource
        )
    }
    observed.discard("")
    if observed:
        return tuple(
            {parameter.name: value} for value in sorted(observed)
        )
    if (
        isinstance(parameter.minimum, int)
        and isinstance(parameter.maximum, int)
        and 1 <= parameter.minimum <= parameter.maximum <= 16
    ):
        return tuple(
            {parameter.name: str(value)}
            for value in range(parameter.minimum, parameter.maximum + 1)
        )
    raise ExecutionPreflightError(
        f"{instrument.display_name}: 안전 종료에 필요한 "
        f"{parameter.name} 값을 확정할 수 없습니다."
    )


def _build_safety_actions(
    instruments: tuple[SelectedInstrument, ...],
    resolved_steps: tuple[_ResolvedStep, ...],
) -> tuple[_SafetyAction, ...]:
    actions: list[_SafetyAction] = []
    for instrument in instruments:
        writes = tuple(
            step
            for step in resolved_steps
            if (
                isinstance(step, _ResolvedFeatureStep)
                and step.selected.instrument.resource == instrument.resource
                and step.feature.operation in {"set", "execute"}
            )
        )
        if not writes or instrument.category not in _ENERGY_SOURCE_CATEGORIES:
            continue
        capability_id = _SAFETY_CAPABILITY_BY_PROFILE.get(
            instrument.profile_id
        )
        if capability_id is None:
            raise ExecutionPreflightError(
                f"{instrument.display_name}: 이 출력 장비의 검증된 안전 종료 "
                "operation이 아직 등록되지 않아 쓰기 명령을 실행할 수 없습니다."
            )
        set_feature_id = (
            f"{instrument.category.value}.cap.{capability_id}.set"
        )
        base_set_feature = feature_by_id(
            set_feature_id,
            instrument.profile_id,
        )
        for selectors in _selector_values(
            instrument,
            resolved_steps,
            base_set_feature,
        ):
            off_arguments = {**selectors, "state": "false"}
            try:
                selected_off = select_feature(
                    instrument,
                    set_feature_id,
                    arguments=off_arguments,
                )
            except (KeyError, ValueError) as exc:
                raise ExecutionPreflightError(
                    f"{instrument.display_name}: 출력 OFF operation을 먼저 "
                    "실장비에서 검증해 주세요."
                ) from exc
            profile = _effective_profile(instrument)
            off_feature = _authoritative_feature(selected_off, profile)
            off_command = render_feature_command(
                off_feature,
                selected_off.arguments,
            )
            query_operation_id = f"{capability_id}::query"
            query_command = ""
            if query_operation_id in instrument.compatible_operation_ids:
                query_feature_id = (
                    f"{instrument.category.value}.cap.{capability_id}.query"
                )
                selected_query = select_feature(
                    instrument,
                    query_feature_id,
                    arguments=selectors,
                )
                query_feature = _authoritative_feature(
                    selected_query,
                    profile,
                )
                query_command = render_feature_command(
                    query_feature,
                    selected_query.arguments,
                )
            actions.append(
                _SafetyAction(
                    instrument=instrument,
                    operation_id=f"{capability_id}::set",
                    command=off_command,
                    readback_operation_id=(
                        query_operation_id if query_command else ""
                    ),
                    readback_command=query_command,
                )
            )
    return tuple(actions)


def _has_declared_option_query(profile: InstrumentProfile) -> bool:
    return any(
        operation.name == "query"
        and _normalized_scpi(operation.scpi) == "*OPT?"
        for capability in profile.capabilities
        for operation in capability.operations
    )


def _preflight_live_identity(
    session,
    instrument: SelectedInstrument,
    profile: InstrumentProfile,
    policy: ExecutionPolicy,
) -> None:
    raw_idn = str(session.query("*IDN?")).strip()
    if len(raw_idn) > policy.max_response_characters:
        raise ExecutionPreflightError(
            f"{instrument.display_name}: *IDN? 응답이 허용 크기를 넘었습니다."
        )
    live_identity = parse_idn_response(raw_idn)
    if _identity_values(live_identity) != _identity_values(
        _selected_identity(instrument)
    ):
        raise ExecutionPreflightError(
            f"{instrument.display_name}: 현재 연결된 장비의 IDN·시리얼·"
            "펌웨어가 검증 당시와 다릅니다."
        )
    if instrument.option_state == "unqueried":
        raise ExecutionPreflightError(
            f"{instrument.display_name}: 옵션 상태를 확인하지 않아 실제 실행할 수 없습니다."
        )
    if instrument.option_state == "queried":
        found = _find_query(
            profile,
            instrument,
            "*OPT?",
            require_pass=True,
        )
        if found is None:
            raise ExecutionPreflightError(
                f"{instrument.display_name}: 검증 통과한 *OPT? operation이 없습니다."
            )
        option_response = str(session.query(found[1])).strip()
        if (
            option_response.casefold()
            != instrument.option_response.strip().casefold()
        ):
            raise ExecutionPreflightError(
                f"{instrument.display_name}: 설치 옵션이 검증 당시와 다릅니다."
            )
    elif (
        instrument.option_state == "unsupported"
        and _has_declared_option_query(profile)
    ):
        raise ExecutionPreflightError(
            f"{instrument.display_name}: 후보 명령팩에는 *OPT?가 있는데 "
            "옵션 미지원으로 저장되어 있습니다. 옵션을 다시 검증해 주세요."
        )


def _stop_status(stop_event: Event, emergency_event: Event) -> ExecutionStatus | None:
    if emergency_event.is_set():
        return ExecutionStatus.EMERGENCY_STOPPED
    if stop_event.is_set():
        return ExecutionStatus.STOPPED
    return None


def _wait_interruptibly(
    seconds: float,
    stop_event: Event,
    emergency_event: Event,
) -> ExecutionStatus | None:
    deadline = time.monotonic() + seconds
    while True:
        status = _stop_status(stop_event, emergency_event)
        if status is not None:
            return status
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            return None
        stop_event.wait(min(0.1, remaining))


def _parse_error_code(response: str) -> int | None:
    match = re.match(r"\s*([+-]?\d+)(?:\s*,|\s*$)", response)
    if match is None:
        return None
    try:
        return int(match.group(1))
    except ValueError:
        return None


def run_execution(
    *,
    instruments: Iterable[SelectedInstrument],
    routine_steps: Iterable[RoutineStep],
    plan_items: Iterable[MeasurementPlanItem] = (),
    dry_run: bool = True,
    backend: str = "",
    timeout_ms: int = 2_000,
    stop_event: Event | None = None,
    emergency_event: Event | None = None,
    event_callback: ExecutionEventCallback | None = None,
    session_factory: Callable[..., ContextManager] = open_resource_session,
    policy: ExecutionPolicy | None = None,
    operator_confirmed: bool = False,
) -> ExecutionResult:
    """Compile and run one immutable routine snapshot.

    Explicitly bound plan values are expanded by test case before VISA opens.
    Every concrete value then passes the existing operation-level validation.
    """

    selected_policy = policy or ExecutionPolicy(io_timeout_ms=timeout_ms)
    stop_flag = stop_event or Event()
    emergency_flag = emergency_event or Event()
    instrument_snapshot = tuple(instruments)
    routine_snapshot = tuple(routine_steps)
    plan_snapshot = tuple(plan_items)
    execution_steps = routine_snapshot
    compiled_metadata: tuple[CompiledStepMetadata, ...] = tuple(
        CompiledStepMetadata(template_step_index=index)
        for index, _step in enumerate(routine_snapshot, start=1)
    )
    compiled_digest = ""
    uses_plan_values = False
    test_case_count = 0
    run_id = uuid.uuid4().hex
    started_at = _utc_now()
    started_monotonic = time.monotonic()
    events: list[ExecutionEvent] = []
    records: list[StepRecord] = []
    measurements: list[MeasurementRecord] = []
    safety_records: list[SafetyRecord] = []
    status = ExecutionStatus.FAILED
    stop_reason = ""
    resolved_instruments: tuple[SelectedInstrument, ...] = ()
    resolved_steps: tuple[_ResolvedStep, ...] = ()
    safety_actions: tuple[_SafetyAction, ...] = ()
    sessions: dict[str, object] = {}
    commands_started = False
    callback_value_unset = object()

    def metadata_for(step_index: int | None) -> CompiledStepMetadata | None:
        if (
            step_index is None
            or step_index < 1
            or step_index > len(compiled_metadata)
        ):
            return None
        return compiled_metadata[step_index - 1]

    def record_context(step_index: int) -> dict[str, object]:
        metadata = metadata_for(step_index)
        if metadata is None:
            return {}
        return {
            "case_id": metadata.case_id,
            "case_name": metadata.case_name,
            "case_index": metadata.case_index,
            "repeat_index": metadata.repeat_index,
            "repeat_count": metadata.repeat_count,
            "template_step_index": metadata.template_step_index,
            "applied_plan_bindings": tuple(
                (
                    binding.parameter_name,
                    binding.field_id,
                    binding.value,
                )
                for binding in metadata.applied_bindings
            ),
        }

    def measurement_context(step_index: int) -> dict[str, object]:
        values = record_context(step_index)
        values.pop("applied_plan_bindings", None)
        return values

    def emit(
        level: str,
        kind: str,
        message: str,
        *,
        step_index: int | None = None,
        resource: str = "",
        command: str = "",
        response: str = "",
        feature_id: str = "",
        capability_id: str = "",
        response_type: str = "",
        parsed_value: object | None = None,
        unit: str = "",
        measurement_id: str = "",
        callback_response: object = callback_value_unset,
        callback_parsed_value: object = callback_value_unset,
    ) -> ExecutionEvent:
        metadata = metadata_for(step_index)
        event = ExecutionEvent(
            sequence=len(events) + 1,
            timestamp_utc=_utc_now(),
            level=level,
            kind=kind,
            message=message,
            step_index=step_index,
            total_steps=len(execution_steps),
            resource=resource,
            command=command,
            response=response,
            feature_id=feature_id,
            capability_id=capability_id,
            response_type=response_type,
            parsed_value=parsed_value,
            unit=unit,
            measurement_id=measurement_id,
            case_id=metadata.case_id if metadata is not None else "",
            case_name=metadata.case_name if metadata is not None else "",
            case_index=metadata.case_index if metadata is not None else 0,
            repeat_index=(
                metadata.repeat_index if metadata is not None else 0
            ),
            repeat_count=(
                metadata.repeat_count if metadata is not None else 0
            ),
            template_step_index=(
                metadata.template_step_index if metadata is not None else 0
            ),
        )
        events.append(event)
        if event_callback is not None:
            try:
                callback_event = event
                callback_updates: dict[str, object] = {}
                if callback_response is not callback_value_unset:
                    callback_updates["response"] = str(callback_response)
                if callback_parsed_value is not callback_value_unset:
                    callback_updates["parsed_value"] = callback_parsed_value
                if callback_updates:
                    callback_event = replace(event, **callback_updates)
                event_callback(callback_event)
            except Exception:
                pass
        return event

    def check_run_limit() -> None:
        if (
            time.monotonic() - started_monotonic
            > selected_policy.max_run_seconds
        ):
            raise _ExecutionAbort(
                ExecutionStatus.FAILED,
                "전체 실행 제한 시간을 넘었습니다.",
            )

    def check_stop() -> None:
        requested = _stop_status(stop_flag, emergency_flag)
        if requested is not None:
            label = (
                "긴급 안전정지가 요청되었습니다."
                if requested is ExecutionStatus.EMERGENCY_STOPPED
                else "사용자가 실행 중지를 요청했습니다."
            )
            raise _ExecutionAbort(requested, label)

    def error_query_for(
        instrument: SelectedInstrument,
        profile: InstrumentProfile,
    ) -> tuple[str, str] | None:
        found = _find_query(
            profile,
            instrument,
            "SYST:ERR?",
            require_pass=True,
        )
        return None if found is None else (found[0], found[1])

    def check_error_queue(
        session,
        instrument: SelectedInstrument,
        profile: InstrumentProfile,
        *,
        step_index: int,
        current_operation_id: str,
    ) -> None:
        query = error_query_for(instrument, profile)
        if query is None or query[0] == current_operation_id:
            return
        nonzero: list[str] = []
        for _index in range(selected_policy.max_error_entries):
            response = str(session.query(query[1])).strip()
            emit(
                "info",
                "error_queue",
                "장비 오류 큐를 확인했어요.",
                step_index=step_index,
                resource=instrument.resource,
                command=query[1],
                response=response,
            )
            code = _parse_error_code(response)
            if code is None:
                raise RuntimeError(
                    f"장비 오류 응답을 해석할 수 없습니다: {response!r}"
                )
            if code == 0:
                if nonzero:
                    raise RuntimeError(
                        "장비 오류 큐: " + " / ".join(nonzero)
                    )
                return
            nonzero.append(response)
        raise RuntimeError(
            "장비 오류 큐가 제한 횟수 안에 비워지지 않았습니다: "
            + " / ".join(nonzero)
        )

    def run_safety_finalizer(reason: str) -> None:
        for action in safety_actions:
            session = sessions.get(action.instrument.resource)
            if session is None:
                safety_records.append(
                    SafetyRecord(
                        sequence=len(safety_records) + 1,
                        timestamp_utc=_utc_now(),
                        resource=action.instrument.resource,
                        operation_id=action.operation_id,
                        command=action.command,
                        status="not_attempted",
                        message="VISA 세션이 없어 출력 OFF 상태를 확인하지 못했습니다.",
                    )
                )
                continue
            emit(
                "warning",
                "safety_shutdown_intent",
                f"{reason} 출력 OFF 안전 종료를 시도합니다.",
                resource=action.instrument.resource,
                command=action.command,
            )
            try:
                session.write(action.command)
                response = ""
                safety_status = "attempted_unconfirmed"
                message = "출력 OFF 명령을 보냈지만 검증된 readback이 없습니다."
                if action.readback_command:
                    response = str(session.query(action.readback_command)).strip()
                    confirmed = parse_query_response(
                        response,
                        "boolean",
                        max_characters=selected_policy.max_response_characters,
                    )
                    if confirmed is False:
                        safety_status = "confirmed_off"
                        message = "출력 OFF readback을 확인했습니다."
                    else:
                        safety_status = "unconfirmed"
                        message = "출력이 아직 ON으로 읽혀 OFF를 확인하지 못했습니다."
                safety_records.append(
                    SafetyRecord(
                        sequence=len(safety_records) + 1,
                        timestamp_utc=_utc_now(),
                        resource=action.instrument.resource,
                        operation_id=action.operation_id,
                        command=action.command,
                        status=safety_status,
                        response=response,
                        message=message,
                    )
                )
                emit(
                    (
                        "info"
                        if safety_status == "confirmed_off"
                        else "warning"
                    ),
                    "safety_shutdown_result",
                    message,
                    resource=action.instrument.resource,
                    command=action.command,
                    response=response,
                )
            except Exception as exc:
                message = f"출력 OFF를 확인하지 못했습니다: {exc}"
                safety_records.append(
                    SafetyRecord(
                        sequence=len(safety_records) + 1,
                        timestamp_utc=_utc_now(),
                        resource=action.instrument.resource,
                        operation_id=action.operation_id,
                        command=action.command,
                        status="failed",
                        message=message,
                    )
                )
                emit(
                    "critical",
                    "safety_shutdown_failed",
                    message,
                    resource=action.instrument.resource,
                    command=action.command,
                )

    emit(
        "info",
        "run_started",
        (
            "Dry Run을 시작합니다. 장비에는 명령을 보내지 않습니다."
            if dry_run
            else "실제 장비 실행 준비를 시작합니다."
        ),
    )
    try:
        if not routine_snapshot:
            raise ExecutionPreflightError("실행할 루틴 단계가 없습니다.")
        compiled = compile_routine_with_plan(
            routine_snapshot,
            plan_snapshot,
            selected_instruments=instrument_snapshot,
            max_expanded_steps=selected_policy.max_expanded_steps,
        )
        execution_steps = compiled.steps
        compiled_metadata = compiled.metadata
        compiled_digest = compiled.digest
        uses_plan_values = compiled.uses_plan_values
        test_case_count = len(compiled.cases)
        known_delay_seconds = sum(
            step.seconds
            for step in execution_steps
            if isinstance(step, DelayStep)
        )
        if known_delay_seconds > selected_policy.max_run_seconds:
            raise ExecutionPreflightError(
                f"계획된 PC 대기 시간만 {known_delay_seconds:g}초라서 전체 "
                f"실행 제한 {selected_policy.max_run_seconds:g}초를 넘습니다."
            )
        resolved_instruments = _collect_instruments(
            instrument_snapshot,
            execution_steps,
            plan_snapshot,
        )
        if not dry_run and operator_confirmed is not True:
            raise ExecutionPreflightError(
                "실제 실행은 출력·배선·DUT 허용 범위를 확인한 운영자의 "
                "명시적 승인이 필요합니다."
            )
        profiles = {
            instrument.resource: _effective_profile(instrument)
            for instrument in resolved_instruments
        }
        for instrument in resolved_instruments:
            if any(
                isinstance(step, (SelectedFeature, WaitForCompletionStep))
                and step.instrument.resource == instrument.resource
                for step in execution_steps
            ):
                _check_profile_binding(
                    instrument,
                    profiles[instrument.resource],
                    dry_run=dry_run,
                )
        resolved_steps = _resolve_steps(
            execution_steps,
            profiles,
            dry_run=dry_run,
        )
        safety_actions = (
            ()
            if dry_run
            else _build_safety_actions(
                resolved_instruments,
                resolved_steps,
            )
        )
        emit(
            "info",
            "compile_completed",
            (
                f"루틴 {len(resolved_steps)}단계와 계획 "
                f"{len(plan_snapshot)}개를 고정했어요. "
                + (
                    f"시험 케이스 {test_case_count}개의 계획값을 검증된 "
                    "장비 명령 인수로 연결했습니다."
                    if uses_plan_values
                    else "계획값 연결이 없어 고정 루틴을 한 번 실행합니다."
                )
            ),
        )

        if dry_run:
            for resolved in resolved_steps:
                timestamp = _utc_now()
                if isinstance(resolved, _ResolvedDelayStep):
                    command = ""
                    response = f"{resolved.seconds:g}초 PC 대기"
                    resource = ""
                    feature_id = ""
                    capability_id = ""
                    operation = "delay"
                    result_name = ""
                    response_type = ""
                    step_kind = "delay"
                elif isinstance(resolved, _ResolvedWaitStep):
                    command = resolved.command
                    response = "검증된 완료 응답 1을 기다림"
                    resource = resolved.instrument.resource
                    feature_id = ""
                    capability_id = resolved.operation_id.rsplit("::", 1)[0]
                    operation = "query"
                    result_name = ""
                    response_type = resolved.response_type
                    step_kind = "wait_for_completion"
                else:
                    command = resolved.command
                    response = "Query 응답은 실제 실행에서 기록"
                    resource = resolved.selected.instrument.resource
                    feature_id = resolved.feature.feature_id
                    capability_id = resolved.feature.capability_id
                    operation = resolved.feature.operation
                    result_name = resolved.selected.result_name
                    response_type = resolved.feature.response_type
                    step_kind = "feature"
                records.append(
                    StepRecord(
                        step_index=resolved.index,
                        step_kind=step_kind,
                        status="dry_run",
                        started_at_utc=timestamp,
                        finished_at_utc=timestamp,
                        duration_ms=0.0,
                        resource=resource,
                        feature_id=feature_id,
                        capability_id=capability_id,
                        operation=operation,
                        command=command,
                        response=response,
                        result_name=result_name,
                        response_type=response_type,
                        **record_context(resolved.index),
                    )
                )
                emit(
                    "info",
                    "dry_run_step",
                    f"{resolved.index}번 단계를 확인했어요.",
                    step_index=resolved.index,
                    resource=resource,
                    command=command,
                )
            status = ExecutionStatus.COMPLETED
            stop_reason = "Dry Run 완료"
        else:
            check_stop()
            with ExitStack() as stack:
                for instrument in resolved_instruments:
                    if not any(
                        isinstance(step, (SelectedFeature, WaitForCompletionStep))
                        and step.instrument.resource == instrument.resource
                        for step in execution_steps
                    ):
                        continue
                    if instrument.resource.startswith("DEMO::"):
                        raise ExecutionPreflightError(
                            "데모 VISA 주소에는 실제 명령을 보낼 수 없습니다."
                        )
                    session = stack.enter_context(
                        session_factory(
                            resource=instrument.resource,
                            backend=backend,
                            timeout_ms=selected_policy.io_timeout_ms,
                        )
                    )
                    sessions[instrument.resource] = session
                    _preflight_live_identity(
                        session,
                        instrument,
                        profiles[instrument.resource],
                        selected_policy,
                    )
                    emit(
                        "info",
                        "identity_verified",
                        (
                            f"{instrument.display_name}의 IDN·시리얼·펌웨어·"
                            "옵션 연결을 확인했어요."
                        ),
                        resource=instrument.resource,
                    )

                try:
                    for resolved in resolved_steps:
                        check_run_limit()
                        check_stop()
                        step_started = _utc_now()
                        step_monotonic = time.monotonic()
                        try:
                            if isinstance(resolved, _ResolvedDelayStep):
                                emit(
                                    "info",
                                    "delay_started",
                                    f"{resolved.seconds:g}초 동안 기다립니다.",
                                    step_index=resolved.index,
                                )
                                requested = _wait_interruptibly(
                                    resolved.seconds,
                                    stop_flag,
                                    emergency_flag,
                                )
                                if requested is not None:
                                    raise _ExecutionAbort(
                                        requested,
                                        "대기 중 실행 중지가 요청되었습니다.",
                                    )
                                response = f"{resolved.seconds:g}초 대기 완료"
                                records.append(
                                    StepRecord(
                                        step_index=resolved.index,
                                        step_kind="delay",
                                        status="completed",
                                        started_at_utc=step_started,
                                        finished_at_utc=_utc_now(),
                                        duration_ms=(
                                            time.monotonic() - step_monotonic
                                        )
                                        * 1000,
                                        operation="delay",
                                        response=response,
                                        **record_context(resolved.index),
                                    )
                                )
                                emit(
                                    "info",
                                    "step_completed",
                                    f"{resolved.index}번 대기가 끝났어요.",
                                    step_index=resolved.index,
                                )
                                continue

                            if isinstance(resolved, _ResolvedWaitStep):
                                instrument = resolved.instrument
                                session = sessions[instrument.resource]
                                original_timeout = session.timeout
                                session.timeout = max(
                                    1,
                                    int(resolved.timeout_seconds * 1000),
                                )
                                commands_started = True
                                emit(
                                    "info",
                                    "command_intent",
                                    "앞 명령 완료 응답을 기다립니다.",
                                    step_index=resolved.index,
                                    resource=instrument.resource,
                                    command=resolved.command,
                                )
                                try:
                                    response = str(
                                        session.query(resolved.command)
                                    ).strip()
                                finally:
                                    session.timeout = original_timeout
                                parsed = parse_query_response(
                                    response,
                                    resolved.response_type,
                                    max_characters=(
                                        selected_policy.max_response_characters
                                    ),
                                )
                                if parsed != 1:
                                    raise RuntimeError(
                                        f"*OPC? 응답이 1이 아닙니다: {response!r}"
                                    )
                                check_error_queue(
                                    session,
                                    instrument,
                                    profiles[instrument.resource],
                                    step_index=resolved.index,
                                    current_operation_id=resolved.operation_id,
                                )
                                records.append(
                                    StepRecord(
                                        step_index=resolved.index,
                                        step_kind="wait_for_completion",
                                        status="completed",
                                        started_at_utc=step_started,
                                        finished_at_utc=_utc_now(),
                                        duration_ms=(
                                            time.monotonic() - step_monotonic
                                        )
                                        * 1000,
                                        resource=instrument.resource,
                                        capability_id=(
                                            resolved.operation_id.rsplit(
                                                "::", 1
                                            )[0]
                                        ),
                                        operation="query",
                                        command=resolved.command,
                                        response=response,
                                        response_type=resolved.response_type,
                                        **record_context(resolved.index),
                                    )
                                )
                                emit(
                                    "info",
                                    "step_completed",
                                    f"{resolved.index}번 완료 확인이 끝났어요.",
                                    step_index=resolved.index,
                                    resource=instrument.resource,
                                    command=resolved.command,
                                    response=response,
                                )
                                continue

                            selected = resolved.selected
                            feature = resolved.feature
                            instrument = selected.instrument
                            session = sessions[instrument.resource]
                            operation_id = (
                                f"{feature.capability_id}::{feature.operation}"
                            )
                            commands_started = True
                            emit(
                                "warning" if feature.is_dangerous else "info",
                                "command_intent",
                                f"{feature.display_name} 명령을 전송합니다.",
                                step_index=resolved.index,
                                resource=instrument.resource,
                                command=resolved.command,
                            )
                            response = ""
                            step_response = ""
                            measurement_id = ""
                            if feature.operation == "query":
                                response = str(
                                    session.query(resolved.command)
                                ).strip()
                                parsed = parse_query_response(
                                    response,
                                    feature.response_type,
                                    max_characters=(
                                        selected_policy.max_response_characters
                                    ),
                                )
                                unit = _capability_unit(
                                    profiles[instrument.resource],
                                    feature.capability_id,
                                )
                                measurement = MeasurementRecord(
                                    measurement_id=uuid.uuid4().hex,
                                    sequence=len(measurements) + 1,
                                    timestamp_utc=_utc_now(),
                                    step_index=resolved.index,
                                    resource=instrument.resource,
                                    manufacturer=instrument.manufacturer,
                                    model=instrument.model,
                                    feature_id=feature.feature_id,
                                    capability_id=feature.capability_id,
                                    operation=feature.operation,
                                    result_name=(
                                        selected.result_name
                                        or feature.display_name
                                    ),
                                    response_type=feature.response_type,
                                    raw_response=response,
                                    parsed_value=parsed,
                                    unit=unit,
                                    **measurement_context(resolved.index),
                                )
                                measurements.append(measurement)
                                measurement_id = measurement.measurement_id
                                is_large_value = isinstance(parsed, tuple)
                                if is_large_value:
                                    step_response = (
                                        f"[측정값 {measurement_id} 참조 · "
                                        f"{len(parsed)}개]"
                                    )
                                else:
                                    step_response = response
                                emit(
                                    "info",
                                    "measurement_recorded",
                                    (
                                        f"측정 결과 '{measurement.result_name}'을 "
                                        "기록했어요."
                                    ),
                                    step_index=resolved.index,
                                    resource=instrument.resource,
                                    command=resolved.command,
                                    response=step_response,
                                    feature_id=feature.feature_id,
                                    capability_id=feature.capability_id,
                                    response_type=feature.response_type,
                                    parsed_value=(
                                        None if is_large_value else parsed
                                    ),
                                    unit=unit,
                                    measurement_id=measurement_id,
                                    callback_response=(
                                        response
                                        if is_large_value
                                        else callback_value_unset
                                    ),
                                    callback_parsed_value=(
                                        parsed
                                        if is_large_value
                                        else callback_value_unset
                                    ),
                                )
                            elif feature.operation in {"set", "execute"}:
                                session.write(resolved.command)
                            else:
                                raise RuntimeError(
                                    f"지원하지 않는 operation입니다: {feature.operation}"
                                )
                            check_error_queue(
                                session,
                                instrument,
                                profiles[instrument.resource],
                                step_index=resolved.index,
                                current_operation_id=operation_id,
                            )
                            records.append(
                                StepRecord(
                                    step_index=resolved.index,
                                    step_kind="feature",
                                    status="completed",
                                    started_at_utc=step_started,
                                    finished_at_utc=_utc_now(),
                                    duration_ms=(
                                        time.monotonic() - step_monotonic
                                    )
                                    * 1000,
                                    resource=instrument.resource,
                                    feature_id=feature.feature_id,
                                    capability_id=feature.capability_id,
                                    operation=feature.operation,
                                    command=resolved.command,
                                    response=step_response,
                                    result_name=selected.result_name,
                                    response_type=feature.response_type,
                                    measurement_id=measurement_id,
                                    **record_context(resolved.index),
                                )
                            )
                            emit(
                                "info",
                                "step_completed",
                                f"{resolved.index}번 단계를 완료했어요.",
                                step_index=resolved.index,
                                resource=instrument.resource,
                                command=resolved.command,
                                response=step_response,
                            )
                        except _ExecutionAbort:
                            raise
                        except Exception as exc:
                            resource = ""
                            feature_id = ""
                            capability_id = ""
                            operation = ""
                            command = ""
                            if isinstance(resolved, _ResolvedFeatureStep):
                                resource = resolved.selected.instrument.resource
                                feature_id = resolved.feature.feature_id
                                capability_id = resolved.feature.capability_id
                                operation = resolved.feature.operation
                                command = resolved.command
                            elif isinstance(resolved, _ResolvedWaitStep):
                                resource = resolved.instrument.resource
                                capability_id = resolved.operation_id.rsplit(
                                    "::", 1
                                )[0]
                                operation = "query"
                                command = resolved.command
                            records.append(
                                StepRecord(
                                    step_index=resolved.index,
                                    step_kind=(
                                        "feature"
                                        if isinstance(
                                            resolved, _ResolvedFeatureStep
                                        )
                                        else "wait_for_completion"
                                    ),
                                    status="failed",
                                    started_at_utc=step_started,
                                    finished_at_utc=_utc_now(),
                                    duration_ms=(
                                        time.monotonic() - step_monotonic
                                    )
                                    * 1000,
                                    resource=resource,
                                    feature_id=feature_id,
                                    capability_id=capability_id,
                                    operation=operation,
                                    command=command,
                                    error=str(exc),
                                    **record_context(resolved.index),
                                )
                            )
                            raise
                    if commands_started and safety_actions:
                        run_safety_finalizer("루틴 정상 완료 후")
                    if any(
                        record.status
                        in {"failed", "unconfirmed", "not_attempted"}
                        for record in safety_records
                    ):
                        status = ExecutionStatus.FAILED
                        stop_reason = (
                            "루틴은 끝났지만 출력 OFF 안전 종료를 "
                            "확인하지 못했습니다."
                        )
                    else:
                        status = ExecutionStatus.COMPLETED
                        stop_reason = "루틴 실행 및 안전 종료 완료"
                except _ExecutionAbort as exc:
                    status = exc.status
                    stop_reason = str(exc)
                    emit(
                        "warning",
                        "run_stopped",
                        stop_reason,
                    )
                    if commands_started:
                        run_safety_finalizer(stop_reason)
                except Exception as exc:
                    status = ExecutionStatus.FAILED
                    stop_reason = str(exc)
                    emit(
                        "error",
                        "run_failed",
                        f"실행을 중단했습니다: {exc}",
                    )
                    if commands_started or sessions:
                        run_safety_finalizer("실행 오류로")
    except _ExecutionAbort as exc:
        status = exc.status
        stop_reason = str(exc)
        emit("warning", "run_stopped", stop_reason)
    except Exception as exc:
        status = ExecutionStatus.FAILED
        stop_reason = str(exc)
        emit(
            "error",
            "preflight_failed",
            f"실행 전 확인을 통과하지 못했습니다: {exc}",
        )

    finished_at = _utc_now()
    duration_ms = (time.monotonic() - started_monotonic) * 1000
    emit(
        "info" if status is ExecutionStatus.COMPLETED else "warning",
        "run_finished",
        (
            f"실행 결과: {status.label_ko}. "
            f"측정값 {len(measurements)}개를 기록했습니다."
        ),
    )
    return ExecutionResult(
        schema_version=EXECUTION_SCHEMA_VERSION,
        run_id=run_id,
        started_at_utc=started_at,
        finished_at_utc=finished_at,
        duration_ms=duration_ms,
        status=status,
        dry_run=bool(dry_run),
        stop_reason=stop_reason,
        instruments=resolved_instruments,
        routine_steps=routine_snapshot,
        plan_items=plan_snapshot,
        step_records=tuple(records),
        measurements=tuple(measurements),
        events=tuple(events),
        executed_steps=execution_steps,
        compiled_digest=compiled_digest,
        uses_plan_values=uses_plan_values,
        test_case_count=test_case_count,
        safety_records=tuple(safety_records),
    )
