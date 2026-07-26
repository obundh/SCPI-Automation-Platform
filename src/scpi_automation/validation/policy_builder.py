"""Conservative defaults for deterministic profile validation.

This module only builds a :class:`ValidationPolicy`; it never communicates
with an instrument.  Defaults are intentionally incomplete when a safe,
reversible probe cannot be inferred from catalog metadata.
"""

from __future__ import annotations

import math
import re
import string
from dataclasses import dataclass
from types import MappingProxyType
from typing import Any, Mapping

from scpi_automation.identity import (
    CatalogCapability,
    CatalogOperation,
    InstrumentProfile,
)

from .models import ValidationPolicy, operation_id


_NUMERIC_TYPES = frozenset(
    {
        "float",
        "integer",
        "number",
        "float_or_enum",
        "float_or_mnemonic",
        "integer_or_mnemonic",
    }
)
_SELECTOR_NAMES = frozenset(
    {
        "channel",
        "trace",
        "marker",
        "port",
        "window",
        "input",
    }
)
_ARBITRARY_TYPES = frozenset(
    {
        "string",
        "float_or_string",
        "voltage_current_time_triplets",
    }
)
_FILE_DATA_PARAMETER_TOKENS = (
    "data",
    "file",
    "filename",
    "path",
    "directory",
    "expression",
    "triplet",
    "waveform",
)


@dataclass(frozen=True, slots=True)
class SafeValidationPolicyBuild:
    """Policy plus explicit evidence about operations left for the operator."""

    policy: ValidationPolicy
    manual_reasons: Mapping[str, str]
    automatic_operation_ids: tuple[str, ...]

    @property
    def operation_arguments(self) -> Mapping[str, Mapping[str, object]]:
        return self.policy.operation_arguments

    def reason_for(self, operation_id_value: str) -> str:
        return self.manual_reasons.get(operation_id_value, "")


class _NeedsOperator(ValueError):
    pass


def _placeholders(template: str) -> tuple[str, ...]:
    names: list[str] = []
    try:
        parsed = string.Formatter().parse(template)
        for _literal, name, format_spec, conversion in parsed:
            if name is None:
                continue
            if (
                not name
                or format_spec
                or conversion
                or not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", name)
            ):
                raise _NeedsOperator(
                    "Complex or unsafe template placeholders require "
                    "operator input."
                )
            names.append(name)
    except ValueError as exc:
        raise _NeedsOperator(
            f"Invalid SCPI template requires manual review: {exc}"
        ) from exc
    return tuple(dict.fromkeys(names))


def _parameters(
    capability: CatalogCapability,
) -> dict[str, Mapping[str, Any]]:
    return {
        str(item.get("name", "")).strip(): item
        for item in capability.parameters
        if str(item.get("name", "")).strip()
    }


def _finite_number(
    value: object,
    *,
    parameter_name: str,
) -> float | int | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    if not math.isfinite(float(value)):
        raise _NeedsOperator(
            f"{parameter_name} has a non-finite catalog bound."
        )
    return value


def _numeric_default(
    definition: Mapping[str, Any],
    *,
    selector: bool,
) -> object:
    name = str(definition.get("name", "value"))
    value_type = str(definition.get("type", "")).lower()
    minimum = _finite_number(
        definition.get("minimum"),
        parameter_name=name,
    )
    maximum = _finite_number(
        definition.get("maximum"),
        parameter_name=name,
    )
    if (
        minimum is not None
        and maximum is not None
        and float(minimum) > float(maximum)
    ):
        raise _NeedsOperator(f"{name} has inverted catalog bounds.")

    if selector:
        value: object = minimum if minimum is not None else 1
    elif minimum is not None:
        # A catalog minimum is a more defensible generic probe than a midpoint
        # for frequency, time, count, range, attenuation, and similar controls.
        value = minimum
    else:
        value = 0

    if maximum is not None and float(value) > float(maximum):
        raise _NeedsOperator(
            f"{name} has no conservative numeric value inside its bounds."
        )
    if minimum is not None and float(value) < float(minimum):
        raise _NeedsOperator(
            f"{name} has no conservative numeric value inside its bounds."
        )
    if value_type in {"integer", "integer_or_mnemonic"}:
        if not float(value).is_integer():
            raise _NeedsOperator(
                f"{name} has no integral conservative catalog bound."
            )
        return int(value)
    return value


def _argument_default(
    name: str,
    definition: Mapping[str, Any] | None,
    *,
    selector_only: bool,
) -> object:
    if definition is None:
        raise _NeedsOperator(
            f"No catalog definition exists for placeholder {name!r}."
        )
    value_type = str(definition.get("type", "")).lower()
    is_selector = (
        name.casefold() in _SELECTOR_NAMES and value_type in _NUMERIC_TYPES
    )
    if selector_only and not is_selector:
        raise _NeedsOperator(
            f"Query placeholder {name!r} is not a numeric selector; "
            "the operator must choose it."
        )
    if value_type in _ARBITRARY_TYPES:
        raise _NeedsOperator(
            f"Arbitrary {value_type or 'string'} parameter {name!r} "
            "requires operator input."
        )
    if value_type == "boolean":
        return False
    if value_type == "enum":
        raw_choices = definition.get("choices", ())
        if not isinstance(raw_choices, (list, tuple)) or not raw_choices:
            raise _NeedsOperator(
                f"Enum parameter {name!r} has no catalog choices."
            )
        choices = tuple(raw_choices)
        # Some catalogs model a state as an enum and list ON first.  OFF is
        # still the conservative probe when it is available.
        for safe_state in ("OFF", "0", "FALSE", "DISABLE", "DISABLED"):
            for choice in choices:
                if str(choice).strip().upper() == safe_state:
                    return choice
        return choices[0]
    if value_type == "number_or_auto":
        minimum = _finite_number(
            definition.get("minimum"),
            parameter_name=name,
        )
        if minimum is not None:
            return minimum
        return "AUTO"
    if value_type in _NUMERIC_TYPES:
        choices = definition.get("choices", ())
        if isinstance(choices, (list, tuple)) and choices:
            # MIN/DEF are safer than guessing a model-dependent numeric range.
            for mnemonic in ("MIN", "DEF"):
                for choice in choices:
                    if str(choice).strip().upper() == mnemonic:
                        return choice
        return _numeric_default(definition, selector=is_selector)
    raise _NeedsOperator(
        f"Unsupported parameter type {value_type!r} for {name!r} "
        "requires operator input."
    )


def _is_reset(template: str) -> bool:
    normalized = re.sub(r"\s+", "", template).upper()
    return (
        normalized.startswith("*RST")
        or ":RST" in normalized
        or "RESET" in normalized
    )


def _is_file_or_data_write(
    capability: CatalogCapability,
    operation: CatalogOperation,
) -> bool:
    text = (
        f"{capability.capability_id} {operation.scpi} "
        + " ".join(
            str(item.get("name", ""))
            for item in capability.parameters
        )
    ).casefold()
    return any(token in text for token in _FILE_DATA_PARAMETER_TOKENS)


def _is_output_enable_write(
    capability: CatalogCapability,
    operation: CatalogOperation,
) -> bool:
    capability_id = capability.capability_id.casefold()
    if (
        ("output" in capability_id and "state" in capability_id)
        or capability_id in {"channel.active", "bias.dc.state"}
    ):
        return True
    template = operation.scpi.upper()
    return bool(
        re.search(
            r"(?:^|:)OUTP(?:UT)?(?:\{[^}]+\}|\d+)?"
            r"(?=\s+\{(?:STATE|VALUE)\}|:(?:STAT|GEN|SEL))",
            template,
        )
    )


def _is_voltage_current_power_write(operation: CatalogOperation) -> bool:
    template = operation.scpi.upper()
    return bool(
        re.search(
            r"(?:^|:)(?:VOLT(?:AGE)?|CURR(?:ENT)?|POW(?:ER)?)"
            r"(?=:|\s|\{)",
            template,
        )
    )


def _write_blockers(
    capability: CatalogCapability,
    operation: CatalogOperation,
) -> tuple[str, ...]:
    blockers: list[str] = []
    if capability.risk_level.strip().casefold() in {
        "high",
        "hazardous",
        "critical",
    }:
        blockers.append(
            "High-risk writes are never approved by the default policy."
        )
    if _is_reset(operation.scpi):
        blockers.append("Reset commands require explicit manual validation.")
    if _is_file_or_data_write(capability, operation):
        blockers.append(
            "File, path, expression, waveform, or data writes require "
            "operator input."
        )
    if capability.capability_id.casefold().startswith("trace.mode"):
        blockers.append(
            "Trace-mode changes can clear accumulated trace data or alter "
            "detector/acquisition state; restoring only the mode token is not "
            "a complete rollback."
        )
    if _is_output_enable_write(capability, operation):
        blockers.append(
            "Output-enable/state writes are blocked because restoration "
            "could energize an output."
        )
    if _is_voltage_current_power_write(operation):
        blockers.append(
            "Voltage, current, or power writes require explicit operator "
            "limits."
        )
    return tuple(dict.fromkeys(blockers))


def _query_arguments(
    capability: CatalogCapability,
    operation: CatalogOperation,
) -> dict[str, object]:
    definitions = _parameters(capability)
    return {
        name: _argument_default(
            name,
            definitions.get(name),
            selector_only=True,
        )
        for name in _placeholders(operation.scpi)
    }


def _set_arguments(
    capability: CatalogCapability,
    operation: CatalogOperation,
    query: CatalogOperation | None,
    query_arguments: Mapping[str, object] | None,
) -> dict[str, object]:
    if query is None:
        raise _NeedsOperator(
            "No paired query exists, so the original value cannot be restored."
        )
    set_names = _placeholders(operation.scpi)
    query_names = _placeholders(query.scpi)
    restorable_names = tuple(
        name for name in set_names if name not in query_names
    )
    if len(restorable_names) > 1:
        raise _NeedsOperator(
            "Automatic reversible validation supports at most one value "
            "placeholder in addition to shared selectors."
        )
    if not restorable_names and re.fullmatch(
        r".+?\s[A-Za-z][A-Za-z0-9_.+-]*",
        operation.scpi,
    ) is None:
        raise _NeedsOperator(
            "A write without a value placeholder must end in one conservative "
            "mnemonic token to support automatic restoration."
        )
    if query_arguments is None:
        raise _NeedsOperator(
            "The paired query requires operator input, so restoration is "
            "not safe."
        )
    definitions = _parameters(capability)
    result: dict[str, object] = {}
    for name in set_names:
        if name in query_names:
            if name not in query_arguments:
                raise _NeedsOperator(
                    f"Shared selector {name!r} has no safe query value."
                )
            result[name] = query_arguments[name]
        elif restorable_names:
            result[name] = _argument_default(
                name,
                definitions.get(name),
                selector_only=False,
            )
    return result


def build_safe_validation_policy(
    profile: InstrumentProfile,
    *,
    timeout_ms: int = 2000,
    error_query: str | None = "SYST:ERR?",
    max_error_entries: int = 8,
) -> SafeValidationPolicyBuild:
    """Build safe defaults without granting hazardous-write approvals.

    Query selectors and reversible low/medium-risk set probes are populated
    when catalog metadata is sufficient.  Every operation not suitable for
    automatic validation receives an operator-facing reason.
    """

    arguments: dict[str, Mapping[str, object]] = {}
    manual_reasons: dict[str, str] = {}
    automatic: list[str] = []

    for capability in profile.capabilities:
        operations = {
            operation.name: operation for operation in capability.operations
        }
        query = operations.get("query")
        query_args: dict[str, object] | None = None
        if query is not None:
            query_id = operation_id(capability.capability_id, "query")
            if query.binary:
                manual_reasons[query_id] = (
                    "Binary queries require a model-specific transfer adapter "
                    "and manual validation."
                )
            else:
                try:
                    query_args = _query_arguments(capability, query)
                except _NeedsOperator as exc:
                    manual_reasons[query_id] = str(exc)
                else:
                    if query_args:
                        arguments[query_id] = MappingProxyType(
                            dict(query_args)
                        )
                    automatic.append(query_id)

        for operation in capability.operations:
            current_id = operation_id(
                capability.capability_id,
                operation.name,
            )
            if operation.name == "query":
                continue
            if operation.name != "set":
                reasons = [
                    "Execute and unknown write operations are never run "
                    "automatically because no generic readback/restore path "
                    "exists."
                ]
                reasons.extend(_write_blockers(capability, operation))
                if operation.binary:
                    reasons.append(
                        "Binary transfers require a model-specific adapter."
                    )
                manual_reasons[current_id] = " ".join(
                    dict.fromkeys(reasons)
                )
                continue

            blockers = list(_write_blockers(capability, operation))
            if operation.binary:
                blockers.append(
                    "Binary transfers require a model-specific adapter."
                )
            if blockers:
                manual_reasons[current_id] = " ".join(
                    dict.fromkeys(blockers)
                )
                continue
            try:
                set_args = _set_arguments(
                    capability,
                    operation,
                    query,
                    query_args,
                )
            except _NeedsOperator as exc:
                manual_reasons[current_id] = str(exc)
            else:
                if set_args:
                    arguments[current_id] = MappingProxyType(dict(set_args))
                automatic.append(current_id)

    policy = ValidationPolicy(
        timeout_ms=timeout_ms,
        error_query=error_query,
        max_error_entries=max_error_entries,
        operation_arguments=MappingProxyType(dict(arguments)),
        approved_hazardous_operation_ids=frozenset(),
        skipped_operation_ids=frozenset(),
    )
    return SafeValidationPolicyBuild(
        policy=policy,
        manual_reasons=MappingProxyType(dict(manual_reasons)),
        automatic_operation_ids=tuple(automatic),
    )
