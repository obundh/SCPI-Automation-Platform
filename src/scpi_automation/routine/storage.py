"""Safe, deterministic persistence for conceptual measurement routines.

Routine files intentionally contain stable feature IDs rather than executable
SCPI strings.  A separately verified device profile is responsible for
translating those feature IDs when a routine is eventually executed.
"""

from __future__ import annotations

import json
import math
import os
import tempfile
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any, Iterable

from scpi_automation.identity import DeviceCategory

from .catalog import feature_by_id, select_feature
from .models import (
    DelayStep,
    PlanArgumentBinding,
    PlanBoundDelayStep,
    RoutineStep,
    SelectedFeature,
    SelectedInstrument,
    WaitForCompletionStep,
)


SCHEMA_VERSION = 6
SUPPORTED_SCHEMA_VERSIONS = frozenset({1, 2, 3, 4, 5, SCHEMA_VERSION})

_ROOT_KEYS = frozenset({"schema_version", "instruments", "steps"})
_INSTRUMENT_KEYS_V1 = frozenset(
    {
        "resource",
        "category",
        "manufacturer",
        "model",
        "serial",
        "profile_id",
    }
)
_INSTRUMENT_KEYS_V2 = frozenset(
    {
        *_INSTRUMENT_KEYS_V1,
        "compatibility_status",
        "compatible_capability_ids",
    }
)
_INSTRUMENT_KEYS_V3 = frozenset(
    {
        *_INSTRUMENT_KEYS_V2,
        "compatible_operation_ids",
        "incompatible_operation_ids",
        "unresolved_operation_ids",
    }
)
_INSTRUMENT_KEYS_V4 = frozenset(
    {
        *_INSTRUMENT_KEYS_V3,
        "firmware",
        "validation_catalog_fingerprint",
        "option_response",
    }
)
_INSTRUMENT_KEYS_V5 = frozenset(
    {
        *_INSTRUMENT_KEYS_V4,
        "raw_idn",
        "option_state",
    }
)
_FEATURE_STEP_KEYS_V1 = frozenset(
    {"type", "instrument_resource", "feature_id"}
)
_FEATURE_STEP_KEYS_V2 = frozenset(
    {
        *_FEATURE_STEP_KEYS_V1,
        "arguments",
        "result_name",
    }
)
_FEATURE_STEP_KEYS_V6 = frozenset(
    {
        *_FEATURE_STEP_KEYS_V2,
        "plan_bindings",
    }
)
_DELAY_STEP_KEYS = frozenset({"type", "seconds"})
_PLAN_DELAY_STEP_KEYS = frozenset(
    {"type", "instrument_resource", "field_id"}
)
_WAIT_STEP_KEYS = frozenset(
    {"type", "instrument_resource", "timeout_seconds"}
)


class RoutineStorageError(ValueError):
    """Raised when a routine cannot be represented or loaded safely."""


@dataclass(frozen=True, slots=True)
class RoutineFile:
    """An immutable routine reconstructed from a versioned JSON file."""

    schema_version: int
    instruments: tuple[SelectedInstrument, ...]
    steps: tuple[RoutineStep, ...]

    @property
    def required_instruments(self) -> tuple[SelectedInstrument, ...]:
        """Return the equipment that must be connected before this routine runs."""

        return self.instruments


def save_routine(
    path: str | os.PathLike[str],
    instruments: Iterable[SelectedInstrument],
    steps: Iterable[RoutineStep],
) -> None:
    """Atomically save one conceptual routine as human-readable UTF-8 JSON."""

    instrument_tuple = _validated_instruments(tuple(instruments))
    step_tuple = _validated_steps(tuple(steps), instrument_tuple)
    payload = {
        "schema_version": SCHEMA_VERSION,
        "instruments": [
            {
                "resource": instrument.resource,
                "category": instrument.category.value,
                "manufacturer": instrument.manufacturer,
                "model": instrument.model,
                "serial": instrument.serial,
                "firmware": instrument.firmware,
                "raw_idn": instrument.raw_idn,
                "profile_id": instrument.profile_id,
                "compatibility_status": instrument.compatibility_status,
                "compatible_capability_ids": list(
                    instrument.compatible_capability_ids
                ),
                "compatible_operation_ids": list(
                    instrument.compatible_operation_ids
                ),
                "incompatible_operation_ids": list(
                    instrument.incompatible_operation_ids
                ),
                "unresolved_operation_ids": list(
                    instrument.unresolved_operation_ids
                ),
                "validation_catalog_fingerprint": (
                    instrument.validation_catalog_fingerprint
                ),
                "option_response": instrument.option_response,
                "option_state": instrument.option_state,
            }
            for instrument in instrument_tuple
        ],
        "steps": [
            _serialize_step(step)
            for step in step_tuple
        ],
    }

    destination = Path(path)
    temporary_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            newline="\n",
            dir=destination.parent,
            prefix=f".{destination.name}.",
            suffix=".tmp",
            delete=False,
        ) as temporary_file:
            temporary_path = Path(temporary_file.name)
            json.dump(
                payload,
                temporary_file,
                ensure_ascii=False,
                indent=2,
                allow_nan=False,
            )
            temporary_file.write("\n")
            temporary_file.flush()
            os.fsync(temporary_file.fileno())

        os.replace(temporary_path, destination)
        temporary_path = None
    finally:
        if temporary_path is not None:
            try:
                temporary_path.unlink()
            except FileNotFoundError:
                pass


def _read_routine_root(
    path: str | os.PathLike[str],
) -> tuple[dict[str, Any], int]:
    """Read and strictly validate the common routine JSON envelope."""

    source = Path(path)
    try:
        text = source.read_text(encoding="utf-8")
    except UnicodeError as exc:
        raise RoutineStorageError(
            "루틴 파일이 올바른 UTF-8 텍스트가 아닙니다."
        ) from exc

    try:
        payload = json.loads(
            text,
            object_pairs_hook=_object_without_duplicate_keys,
            parse_constant=_reject_non_finite_json_number,
        )
    except json.JSONDecodeError as exc:
        raise RoutineStorageError(
            f"루틴 JSON 형식이 올바르지 않습니다: {exc.msg}"
        ) from exc

    root = _expect_object(payload, "루틴 파일")
    _expect_exact_keys(root, _ROOT_KEYS, "루틴 파일")

    schema_version = root["schema_version"]
    if type(schema_version) is not int:
        raise RoutineStorageError("schema_version은 정수여야 합니다.")
    if schema_version not in SUPPORTED_SCHEMA_VERSIONS:
        raise RoutineStorageError(
            f"지원하지 않는 루틴 schema_version입니다: {schema_version}"
        )
    return root, schema_version


def load_routine_requirements(
    path: str | os.PathLike[str],
) -> RoutineFile:
    """Read saved equipment identity without trusting its command allowlist.

    The GUI uses this first to explain which currently connected equipment is
    missing.  No feature step is reconstructed by this metadata-only pass.
    """

    root, schema_version = _read_routine_root(path)
    raw_instruments = _expect_list(root["instruments"], "instruments")
    instruments = _load_instruments(
        raw_instruments,
        schema_version,
        preserve_saved_validation=True,
    )
    _expect_list(root["steps"], "steps")
    return RoutineFile(
        schema_version=schema_version,
        instruments=instruments,
        steps=(),
    )


def load_routine(
    path: str | os.PathLike[str],
    *,
    trusted_instruments: Iterable[SelectedInstrument] | None = None,
) -> RoutineFile:
    """Load a strictly validated v1 through v5 routine JSON file.

    Older files are migrated in memory. New saves use version 5. A saved
    operation allowlist is audit data, not authority.  For every supported
    schema version, command-bearing feature steps are reconstructed only from
    a matching ``trusted_instruments`` record supplied by the current
    discovery/validation session.
    """

    root, schema_version = _read_routine_root(path)
    raw_instruments = _expect_list(root["instruments"], "instruments")
    instruments = _load_instruments(
        raw_instruments,
        schema_version,
        trusted_instruments=trusted_instruments,
    )
    raw_steps = _expect_list(root["steps"], "steps")
    steps = _load_steps(raw_steps, instruments, schema_version)

    return RoutineFile(
        schema_version=schema_version,
        instruments=instruments,
        steps=steps,
    )


def _serialize_step(step: RoutineStep) -> dict[str, object]:
    if type(step) is SelectedFeature:
        return {
            "type": "feature",
            "instrument_resource": step.instrument.resource,
            "feature_id": step.feature_id,
            "arguments": {
                name: value for name, value in step.arguments
            },
            "plan_bindings": {
                binding.parameter_name: binding.field_id
                for binding in step.plan_bindings
            },
            "result_name": step.result_name,
        }
    if type(step) is DelayStep:
        return {
            "type": "delay",
            "seconds": step.seconds,
        }
    if type(step) is PlanBoundDelayStep:
        return {
            "type": "plan_bound_delay",
            "instrument_resource": step.instrument.resource,
            "field_id": step.field_id,
        }
    if type(step) is WaitForCompletionStep:
        return {
            "type": "wait_for_completion",
            "instrument_resource": step.instrument.resource,
            "timeout_seconds": step.timeout_seconds,
        }
    raise RoutineStorageError(
        f"저장할 수 없는 루틴 단계 형식입니다: {type(step).__name__}"
    )


def _load_instruments(
    raw_instruments: list[Any],
    schema_version: int,
    *,
    trusted_instruments: Iterable[SelectedInstrument] | None = None,
    preserve_saved_validation: bool = False,
) -> tuple[SelectedInstrument, ...]:
    instruments: list[SelectedInstrument] = []
    seen_resources: set[str] = set()
    trusted = (
        None
        if trusted_instruments is None
        else tuple(trusted_instruments)
    )

    for index, raw_instrument in enumerate(raw_instruments):
        location = f"instruments[{index}]"
        item = _expect_object(raw_instrument, location)
        _expect_exact_keys(
            item,
            (
                _INSTRUMENT_KEYS_V1
                if schema_version == 1
                else (
                    _INSTRUMENT_KEYS_V2
                    if schema_version == 2
                    else (
                        _INSTRUMENT_KEYS_V3
                        if schema_version == 3
                        else (
                            _INSTRUMENT_KEYS_V4
                            if schema_version == 4
                            else _INSTRUMENT_KEYS_V5
                        )
                    )
                )
            ),
            location,
        )

        resource = _expect_string(item["resource"], f"{location}.resource")
        category_value = _expect_string(
            item["category"],
            f"{location}.category",
        )
        try:
            category = DeviceCategory(category_value)
        except ValueError as exc:
            raise RoutineStorageError(
                f"{location}.category에 알 수 없는 장비 분류가 있습니다: "
                f"{category_value}"
            ) from exc

        if resource in seen_resources:
            raise RoutineStorageError(
                f"같은 장비 resource가 두 번 등록되어 있습니다: {resource}"
            )
        seen_resources.add(resource)

        compatibility_status = (
            ""
            if schema_version == 1
            else _expect_string(
                item["compatibility_status"],
                f"{location}.compatibility_status",
            )
        )
        compatible_capability_ids = (
            ()
            if schema_version == 1
            else _expect_string_tuple(
                item["compatible_capability_ids"],
                f"{location}.compatible_capability_ids",
            )
        )
        compatible_operation_ids = (
            ()
            if schema_version < 3
            else _expect_string_tuple(
                item["compatible_operation_ids"],
                f"{location}.compatible_operation_ids",
            )
        )
        incompatible_operation_ids = (
            ()
            if schema_version < 3
            else _expect_string_tuple(
                item["incompatible_operation_ids"],
                f"{location}.incompatible_operation_ids",
            )
        )
        unresolved_operation_ids = (
            ()
            if schema_version < 3
            else _expect_string_tuple(
                item["unresolved_operation_ids"],
                f"{location}.unresolved_operation_ids",
            )
        )
        if schema_version == 3:
            # v3 stored PASS operation IDs without the catalog fingerprint,
            # firmware, or option response that prove which command surface
            # was validated. Preserve the audit trail as unresolved, but do
            # not let it unlock commands in a newer catalog.
            unresolved_operation_ids = tuple(
                dict.fromkeys(
                    compatible_operation_ids + unresolved_operation_ids
                )
            )
            compatibility_status = "candidate_pack_unvalidated"
            compatible_capability_ids = ()
            compatible_operation_ids = ()

        saved_option_response = (
            ""
            if schema_version < 4
            else _expect_string(
                item["option_response"],
                f"{location}.option_response",
            )
        )
        saved_option_state = (
            (
                "queried"
                if saved_option_response.strip()
                else "unqueried"
            )
            if schema_version < 5
            else _expect_string(
                item["option_state"],
                f"{location}.option_state",
            )
        )
        try:
            instrument = SelectedInstrument(
                resource=resource,
                category=category,
                manufacturer=_expect_string(
                    item["manufacturer"],
                    f"{location}.manufacturer",
                ),
                model=_expect_string(item["model"], f"{location}.model"),
                serial=_expect_string(item["serial"], f"{location}.serial"),
                firmware=(
                    ""
                    if schema_version < 4
                    else _expect_string(
                        item["firmware"],
                        f"{location}.firmware",
                    )
                ),
                raw_idn=(
                    ""
                    if schema_version < 5
                    else _expect_string(
                        item["raw_idn"],
                        f"{location}.raw_idn",
                    )
                ),
                profile_id=_expect_string(
                    item["profile_id"],
                    f"{location}.profile_id",
                ),
                compatibility_status=compatibility_status,
                compatible_capability_ids=compatible_capability_ids,
                compatible_operation_ids=compatible_operation_ids,
                incompatible_operation_ids=incompatible_operation_ids,
                unresolved_operation_ids=unresolved_operation_ids,
                validation_catalog_fingerprint=(
                    ""
                    if schema_version < 4
                    else _expect_string(
                        item["validation_catalog_fingerprint"],
                        f"{location}.validation_catalog_fingerprint",
                    )
                ),
                option_response=saved_option_response,
                option_state=saved_option_state,
            )
        except (TypeError, ValueError) as exc:
            raise RoutineStorageError(
                f"{location}의 장비 정보가 올바르지 않습니다: {exc}"
            ) from exc
        if not preserve_saved_validation:
            # A saved routine is never an authorization source.  Rebind every
            # instrument, including legacy v1-v3 records and records whose
            # saved operation allowlist is empty, to the current validation
            # result.  Otherwise a file can clear its allowlist/status or
            # downgrade its schema to bypass the operation-level gate.
            if trusted is None:
                instrument = replace(
                    instrument,
                    compatibility_status="candidate_pack_unvalidated",
                    compatible_capability_ids=(),
                    compatible_operation_ids=(),
                    unresolved_operation_ids=tuple(
                        dict.fromkeys(
                            instrument.compatible_operation_ids
                            + instrument.unresolved_operation_ids
                        )
                    ),
                    validation_catalog_fingerprint="",
                )
            else:
                trusted_match = _trusted_instrument_match(
                    instrument,
                    trusted,
                    schema_version=schema_version,
                )
                if trusted_match is None:
                    instrument = replace(
                        instrument,
                        compatibility_status="candidate_pack_unvalidated",
                        compatible_capability_ids=(),
                        compatible_operation_ids=(),
                        unresolved_operation_ids=tuple(
                            dict.fromkeys(
                                instrument.compatible_operation_ids
                                + instrument.unresolved_operation_ids
                            )
                        ),
                        validation_catalog_fingerprint="",
                    )
                else:
                    # Keep the saved address so step references remain
                    # resolvable, but replace every authorization field with
                    # the currently selected, trusted validation result.
                    instrument = replace(
                        trusted_match,
                        resource=instrument.resource,
                    )
        instruments.append(instrument)

    return tuple(instruments)


def _trusted_instrument_match(
    saved: SelectedInstrument,
    trusted: tuple[SelectedInstrument, ...],
    *,
    schema_version: int,
) -> SelectedInstrument | None:
    """Resolve one saved identity to exactly one current trusted device."""

    def normalized(value: str) -> str:
        return value.strip().casefold()

    def same_identity(current: SelectedInstrument) -> bool:
        core_matches = (
            current.category is saved.category
            and normalized(current.manufacturer)
            == normalized(saved.manufacturer)
            and normalized(current.model) == normalized(saved.model)
            and normalized(current.serial) == normalized(saved.serial)
            and bool(normalized(saved.serial))
            and normalized(current.profile_id)
            == normalized(saved.profile_id)
        )
        if not core_matches:
            return False
        if schema_version >= 4 and (
            normalized(current.firmware) != normalized(saved.firmware)
            or normalized(current.option_response)
            != normalized(saved.option_response)
        ):
            return False
        if schema_version >= 5 and (
            normalized(current.raw_idn) != normalized(saved.raw_idn)
            or not normalized(saved.raw_idn)
            or current.option_state != saved.option_state
        ):
            return False
        return (
            schema_version < 4
            or (
                normalized(current.firmware)
                == normalized(saved.firmware)
                and normalized(current.option_response)
                == normalized(saved.option_response)
            )
        )

    exact = tuple(
        current
        for current in trusted
        if (
            current.resource == saved.resource
            and same_identity(current)
        )
    )
    if len(exact) == 1:
        return exact[0]
    serial_matches = tuple(
        current for current in trusted if same_identity(current)
    )
    if len(serial_matches) == 1:
        return serial_matches[0]
    return None


def _load_steps(
    raw_steps: list[Any],
    instruments: tuple[SelectedInstrument, ...],
    schema_version: int,
) -> tuple[RoutineStep, ...]:
    by_resource = {
        instrument.resource: instrument
        for instrument in instruments
    }
    steps: list[RoutineStep] = []

    for index, raw_step in enumerate(raw_steps):
        location = f"steps[{index}]"
        item = _expect_object(raw_step, location)
        step_type = _expect_string(item.get("type"), f"{location}.type")

        if step_type == "feature":
            _expect_exact_keys(
                item,
                (
                    _FEATURE_STEP_KEYS_V1
                    if schema_version == 1
                    else (
                        _FEATURE_STEP_KEYS_V6
                        if schema_version >= 6
                        else _FEATURE_STEP_KEYS_V2
                    )
                ),
                location,
            )
            instrument = _referenced_instrument(item, location, by_resource)
            feature_id = _expect_string(
                item["feature_id"],
                f"{location}.feature_id",
            )
            arguments: tuple[tuple[str, str], ...] = ()
            plan_bindings: tuple[PlanArgumentBinding, ...] = ()
            result_name = ""
            if schema_version >= 2:
                argument_values = _expect_object(
                    item["arguments"],
                    f"{location}.arguments",
                )
                arguments = tuple(
                    (
                        _expect_string(name, f"{location}.arguments key"),
                        _expect_string(
                            value,
                            f"{location}.arguments.{name}",
                        ),
                    )
                    for name, value in argument_values.items()
                )
                result_name = _expect_string(
                    item["result_name"],
                    f"{location}.result_name",
                )
            if schema_version >= 6:
                binding_values = _expect_object(
                    item["plan_bindings"],
                    f"{location}.plan_bindings",
                )
                try:
                    plan_bindings = tuple(
                        PlanArgumentBinding(
                            parameter_name=_expect_string(
                                name,
                                f"{location}.plan_bindings key",
                            ),
                            field_id=_expect_string(
                                value,
                                f"{location}.plan_bindings.{name}",
                            ),
                        )
                        for name, value in binding_values.items()
                    )
                except (TypeError, ValueError) as exc:
                    raise RoutineStorageError(
                        f"{location}.plan_bindings가 올바르지 않습니다: {exc}"
                    ) from exc
            try:
                steps.append(
                    select_feature(
                        instrument,
                        feature_id,
                        arguments=arguments,
                        plan_bindings=plan_bindings,
                        result_name=result_name,
                    )
                )
            except (KeyError, TypeError, ValueError) as exc:
                raise RoutineStorageError(
                    f"{location}의 기능 설정이 올바르지 않습니다: {exc}"
                ) from exc
        elif step_type == "delay":
            _expect_exact_keys(item, _DELAY_STEP_KEYS, location)
            seconds = _expect_number(item["seconds"], f"{location}.seconds")
            try:
                steps.append(DelayStep(seconds=seconds))
            except (TypeError, ValueError) as exc:
                raise RoutineStorageError(
                    f"{location}.seconds가 올바르지 않습니다: {exc}"
                ) from exc
        elif step_type == "plan_bound_delay":
            if schema_version < 6:
                raise RoutineStorageError(
                    f"{location}: plan_bound_delay는 루틴 스키마 6부터 지원합니다."
                )
            _expect_exact_keys(item, _PLAN_DELAY_STEP_KEYS, location)
            instrument = _referenced_instrument(item, location, by_resource)
            field_id = _expect_string(
                item["field_id"],
                f"{location}.field_id",
            )
            try:
                steps.append(
                    PlanBoundDelayStep(
                        instrument=instrument,
                        field_id=field_id,
                    )
                )
            except (TypeError, ValueError) as exc:
                raise RoutineStorageError(
                    f"{location}의 계획 연동 대기가 올바르지 않습니다: {exc}"
                ) from exc
        elif step_type == "wait_for_completion":
            _expect_exact_keys(item, _WAIT_STEP_KEYS, location)
            instrument = _referenced_instrument(item, location, by_resource)
            timeout = _expect_number(
                item["timeout_seconds"],
                f"{location}.timeout_seconds",
            )
            try:
                steps.append(
                    WaitForCompletionStep(
                        instrument=instrument,
                        timeout_seconds=timeout,
                    )
                )
            except (TypeError, ValueError) as exc:
                raise RoutineStorageError(
                    f"{location}.timeout_seconds가 올바르지 않습니다: {exc}"
                ) from exc
        else:
            raise RoutineStorageError(
                f"{location}에 알 수 없는 단계 형식이 있습니다: {step_type}"
            )

    return tuple(steps)


def _validated_instruments(
    instruments: tuple[SelectedInstrument, ...],
) -> tuple[SelectedInstrument, ...]:
    seen_resources: set[str] = set()
    for index, instrument in enumerate(instruments):
        if type(instrument) is not SelectedInstrument:
            raise RoutineStorageError(
                f"instruments[{index}]는 SelectedInstrument여야 합니다."
            )
        if not isinstance(instrument.category, DeviceCategory):
            raise RoutineStorageError(
                f"instruments[{index}]의 장비 분류가 올바르지 않습니다."
            )
        for field_name in (
            "resource",
            "manufacturer",
            "model",
            "serial",
            "firmware",
            "raw_idn",
            "profile_id",
            "compatibility_status",
            "validation_catalog_fingerprint",
            "option_response",
            "option_state",
        ):
            if type(getattr(instrument, field_name)) is not str:
                raise RoutineStorageError(
                    f"instruments[{index}].{field_name}은 문자열이어야 합니다."
                )
        for tuple_field in (
            "compatible_capability_ids",
            "compatible_operation_ids",
            "incompatible_operation_ids",
            "unresolved_operation_ids",
        ):
            values = getattr(instrument, tuple_field)
            if type(values) is not tuple:
                raise RoutineStorageError(
                    f"instruments[{index}].{tuple_field}는 "
                    "문자열 튜플이어야 합니다."
                )
            if any(
                type(item) is not str or not item.strip()
                for item in values
            ):
                raise RoutineStorageError(
                    f"instruments[{index}].{tuple_field}에는 "
                    "비어 있지 않은 문자열만 넣을 수 있습니다."
                )
            if len(set(values)) != len(values):
                raise RoutineStorageError(
                    f"instruments[{index}].{tuple_field}에 "
                    "중복 항목이 있습니다."
                )
        if not instrument.resource.strip():
            raise RoutineStorageError(
                f"instruments[{index}].resource는 비워둘 수 없습니다."
            )
        if instrument.resource in seen_resources:
            raise RoutineStorageError(
                "같은 장비 resource를 두 번 저장할 수 없습니다: "
                f"{instrument.resource}"
            )
        seen_resources.add(instrument.resource)
    return instruments


def _validated_steps(
    steps: tuple[RoutineStep, ...],
    instruments: tuple[SelectedInstrument, ...],
) -> tuple[RoutineStep, ...]:
    by_resource = {
        instrument.resource: instrument
        for instrument in instruments
    }
    for index, step in enumerate(steps):
        location = f"steps[{index}]"
        if type(step) is SelectedFeature:
            instrument = _matching_instrument(
                step.instrument,
                location,
                by_resource,
            )
            if type(step.feature_id) is not str:
                raise RoutineStorageError(
                    f"{location}.feature_id는 문자열이어야 합니다."
                )
            try:
                normalized = select_feature(
                    instrument,
                    step.feature_id,
                    arguments=step.arguments,
                    plan_bindings=step.plan_bindings,
                    result_name=step.result_name,
                )
            except (KeyError, TypeError, ValueError) as exc:
                raise RoutineStorageError(
                    f"{location}의 기능 설정이 올바르지 않습니다: {exc}"
                ) from exc
            if normalized.arguments != step.arguments:
                raise RoutineStorageError(
                    f"{location}.arguments는 정규화된 문자열 값이어야 합니다."
                )
            if normalized.plan_bindings != step.plan_bindings:
                raise RoutineStorageError(
                    f"{location}.plan_bindings가 정규화되어 있지 않습니다."
                )
            if normalized.result_name != step.result_name:
                raise RoutineStorageError(
                    f"{location}.result_name 앞뒤에 공백을 둘 수 없습니다."
                )
        elif type(step) is DelayStep:
            _validate_finite_number(step.seconds, f"{location}.seconds")
        elif type(step) is PlanBoundDelayStep:
            _matching_instrument(step.instrument, location, by_resource)
            if step.field_id != "dwell_seconds":
                raise RoutineStorageError(
                    f"{location}.field_id는 dwell_seconds여야 합니다."
                )
        elif type(step) is WaitForCompletionStep:
            _matching_instrument(step.instrument, location, by_resource)
            _validate_finite_number(
                step.timeout_seconds,
                f"{location}.timeout_seconds",
            )
        else:
            raise RoutineStorageError(
                f"{location}에 저장할 수 없는 단계 형식이 있습니다: "
                f"{type(step).__name__}"
            )
    return steps


def _validate_feature_for_instrument(
    feature_id: str,
    instrument: SelectedInstrument,
    location: str,
) -> None:
    try:
        feature = feature_by_id(feature_id)
    except KeyError as exc:
        raise RoutineStorageError(
            f"{location}에 알 수 없는 기능 ID가 있습니다: {feature_id}"
        ) from exc
    if feature.category is not instrument.category:
        raise RoutineStorageError(
            f"{location}의 기능 '{feature_id}'은 "
            f"{instrument.category.value} 장비에서 사용할 수 없습니다."
        )


def _referenced_instrument(
    item: dict[str, Any],
    location: str,
    by_resource: dict[str, SelectedInstrument],
) -> SelectedInstrument:
    resource = _expect_string(
        item["instrument_resource"],
        f"{location}.instrument_resource",
    )
    try:
        return by_resource[resource]
    except KeyError as exc:
        raise RoutineStorageError(
            f"{location}에서 필요한 장비를 찾을 수 없습니다: {resource}"
        ) from exc


def _matching_instrument(
    step_instrument: SelectedInstrument,
    location: str,
    by_resource: dict[str, SelectedInstrument],
) -> SelectedInstrument:
    if type(step_instrument) is not SelectedInstrument:
        raise RoutineStorageError(
            f"{location}의 대상 장비 정보가 올바르지 않습니다."
        )
    try:
        stored_instrument = by_resource[step_instrument.resource]
    except KeyError as exc:
        raise RoutineStorageError(
            f"{location}의 대상 장비가 필요 장비 목록에 없습니다: "
            f"{step_instrument.resource}"
        ) from exc
    if stored_instrument != step_instrument:
        raise RoutineStorageError(
            f"{location}의 장비 정보가 필요 장비 목록과 일치하지 않습니다: "
            f"{step_instrument.resource}"
        )
    return stored_instrument


def _object_without_duplicate_keys(
    pairs: list[tuple[str, Any]],
) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise RoutineStorageError(
                f"JSON 속성이 중복되어 있습니다: {key}"
            )
        result[key] = value
    return result


def _reject_non_finite_json_number(value: str) -> None:
    raise RoutineStorageError(
        f"JSON에 사용할 수 없는 숫자가 있습니다: {value}"
    )


def _expect_object(value: Any, location: str) -> dict[str, Any]:
    if type(value) is not dict:
        raise RoutineStorageError(f"{location}은 JSON 객체여야 합니다.")
    return value


def _expect_list(value: Any, location: str) -> list[Any]:
    if type(value) is not list:
        raise RoutineStorageError(f"{location}은 JSON 배열이어야 합니다.")
    return value


def _expect_string_tuple(value: Any, location: str) -> tuple[str, ...]:
    items = _expect_list(value, location)
    result = tuple(
        _expect_string(item, f"{location}[{index}]")
        for index, item in enumerate(items)
    )
    if any(not item.strip() for item in result):
        raise RoutineStorageError(f"{location}에는 빈 문자열을 넣을 수 없습니다.")
    if len(set(result)) != len(result):
        raise RoutineStorageError(f"{location}에 중복 항목이 있습니다.")
    return result


def _expect_string(value: Any, location: str) -> str:
    if type(value) is not str:
        raise RoutineStorageError(f"{location}은 문자열이어야 합니다.")
    return value


def _expect_number(value: Any, location: str) -> float:
    if type(value) not in (int, float):
        raise RoutineStorageError(f"{location}은 숫자여야 합니다.")
    return _validate_finite_number(value, location)


def _validate_finite_number(value: int | float, location: str) -> float:
    if type(value) not in (int, float):
        raise RoutineStorageError(f"{location}은 유한한 숫자여야 합니다.")
    try:
        normalized = float(value)
    except OverflowError as exc:
        raise RoutineStorageError(
            f"{location}은 유한한 숫자여야 합니다."
        ) from exc
    if not math.isfinite(normalized):
        raise RoutineStorageError(f"{location}은 유한한 숫자여야 합니다.")
    return normalized


def _expect_exact_keys(
    value: dict[str, Any],
    expected: frozenset[str],
    location: str,
) -> None:
    actual = set(value)
    missing = expected - actual
    unknown = actual - expected
    if missing:
        raise RoutineStorageError(
            f"{location}에 필수 속성이 없습니다: {', '.join(sorted(missing))}"
        )
    if unknown:
        raise RoutineStorageError(
            f"{location}에 알 수 없는 속성이 있습니다: "
            f"{', '.join(sorted(unknown))}"
        )
