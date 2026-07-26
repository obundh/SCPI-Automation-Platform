"""Identity-bound local operations promoted from manual command candidates.

Raw command headers extracted from a programming manual are intentionally not
executable.  This module provides the missing, explicit bridge:

``manual candidate -> typed draft -> live validation -> promoted extension``.

An extension is bound to one exact instrument identity and keeps the source
manual location plus the complete validation result.  Only promoted records
whose operation evidence is PASS may be exposed to the routine builder.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import math
import os
import re
import secrets
import stat
import string
import tempfile
import time
from contextlib import contextmanager
from dataclasses import dataclass, field, replace
from pathlib import Path
from types import MappingProxyType
from typing import Iterable, Mapping

from scpi_automation.identity import (
    CatalogCapability,
    CatalogOperation,
    DeviceCategory,
    InstrumentIdentity,
    InstrumentProfile,
    parse_idn_response,
)

from .engine import (
    ValidationSession,
    _build_reversible_write,
    _render_command,
    validate_profile,
)
from .manual_catalog import ManualCommandCandidate
from .models import (
    FailureKind,
    OperationKind,
    OperationStatus,
    OperationValidation,
    ValidationPolicy,
    ValidationProgress,
    ValidationResult,
    apply_manual_result,
    create_validation_progress,
    build_validation_result,
    profile_fingerprint,
)
from .persistence import (
    validation_result_from_dict,
    validation_result_to_dict,
)


LOCAL_EXTENSION_SCHEMA_VERSION = 3
_DOCUMENT_TYPE = "scpi-local-operation-extensions"
_AUTHENTICATION_FIELD = "authentication"
_AUTHENTICATION_ALGORITHM = "HMAC-SHA256"
_REGISTRY_GENERATION_FIELD = "registry_generation"
_STATE_DOCUMENT_TYPE = "scpi-local-operation-extension-state"
_STATE_SCHEMA_VERSION = 1
_KEY_DOCUMENT_TYPE = "scpi-local-operation-extension-key"
_KEY_SCHEMA_VERSION = 2
_KEY_SIZE = 32
_DPAPI_KEY_PREFIX = b"SCPI-EXTENSION-DPAPI-V2\x00"
_RAW_KEY_PREFIX = b"SCPI-EXTENSION-RAW-V2\x00"
_STATE_DPAPI_PREFIX = b"SCPI-EXTENSION-STATE-DPAPI-V1\x00"
_STATE_RAW_PREFIX = b"SCPI-EXTENSION-STATE-RAW-V1\x00"
OPTION_STATE_QUERIED = "queried"
OPTION_STATE_UNSUPPORTED = "unsupported"
OPTION_STATE_UNQUERIED = "unqueried"
_OPTION_STATES = frozenset(
    {
        OPTION_STATE_QUERIED,
        OPTION_STATE_UNSUPPORTED,
        OPTION_STATE_UNQUERIED,
    }
)
_ALLOWED_RISKS = frozenset(
    {"low", "medium", "high", "hazardous", "critical"}
)
_ALLOWED_RESPONSE_TYPES = frozenset(
    {
        "",
        "string",
        "string_array",
        "float_or_string",
        "array",
        "boolean",
        "float",
        "number",
        "integer",
        "float_array",
        "float_pair",
        "float_triplet",
    }
)
_ALLOWED_PARAMETER_TYPES = frozenset(
    {
        "string",
        "float",
        "integer",
        "number",
        "boolean",
        "enum",
        "number_or_auto",
        "float_or_enum",
        "integer_or_mnemonic",
        "float_or_mnemonic",
        "float_or_string",
    }
)
_SAFE_TOKEN = re.compile(r"[A-Za-z_][A-Za-z0-9_.-]*")
_CAPABILITY_ID = re.compile(r"[a-z][a-z0-9_.-]*")


def _normalized_identity_value(value: str) -> str:
    return value.strip().casefold()


def _identity_tuple(identity: InstrumentIdentity) -> tuple[str, ...]:
    return tuple(
        _normalized_identity_value(value)
        for value in (
            identity.raw,
            identity.manufacturer,
            identity.model,
            identity.serial,
            identity.firmware,
        )
    )


def _safe_command_template(value: str, field_name: str) -> str:
    command = value.strip()
    if not command:
        raise ValueError(f"{field_name} must not be empty")
    if len(command) > 10_000:
        raise ValueError(f"{field_name} is too long")
    if any(token in command for token in ("\x00", "\r", "\n", ";")):
        raise ValueError(
            f"{field_name} contains a prohibited separator or control character"
        )
    return command


def _placeholder_names(template: str) -> tuple[str, ...]:
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
                or _SAFE_TOKEN.fullmatch(name) is None
            ):
                raise ValueError(
                    "SCPI placeholders must be simple names such as {value}"
                )
            names.append(name)
    except ValueError as exc:
        raise ValueError(f"Invalid SCPI template: {exc}") from exc
    return tuple(dict.fromkeys(names))


@dataclass(frozen=True, slots=True)
class LocalExtensionParameter:
    """Typed user input used by a promoted local operation."""

    name: str
    value_type: str
    unit: str = ""
    minimum: float | int | None = None
    maximum: float | int | None = None
    choices: tuple[str, ...] = ()
    mapping: tuple[tuple[str, str], ...] = ()
    note_ko: str = ""

    def __post_init__(self) -> None:
        if _SAFE_TOKEN.fullmatch(self.name.strip()) is None:
            raise ValueError("Parameter name must be a simple SCPI token")
        if self.value_type not in _ALLOWED_PARAMETER_TYPES:
            raise ValueError(
                f"Unsupported local parameter type: {self.value_type!r}"
            )
        if (
            self.minimum is not None
            and self.maximum is not None
            and self.minimum > self.maximum
        ):
            raise ValueError("Parameter minimum must not exceed maximum")
        for label, bound in (
            ("minimum", self.minimum),
            ("maximum", self.maximum),
        ):
            if bound is not None and not math.isfinite(float(bound)):
                raise ValueError(f"Parameter {label} must be finite")
        if self.value_type == "enum" and not self.choices:
            raise ValueError("Enum parameters require at least one choice")
        if any(
            any(token in choice for token in ("\x00", "\r", "\n", ";"))
            for choice in self.choices
        ):
            raise ValueError("Parameter choices contain an unsafe separator")

    def to_catalog_dict(self) -> dict[str, object]:
        value: dict[str, object] = {
            "name": self.name,
            "type": self.value_type,
        }
        if self.unit:
            value["unit"] = self.unit
        if self.minimum is not None:
            value["minimum"] = self.minimum
        if self.maximum is not None:
            value["maximum"] = self.maximum
        if self.choices:
            value["choices"] = list(self.choices)
        if self.mapping:
            value["mapping"] = dict(self.mapping)
        if self.note_ko:
            value["note_ko"] = self.note_ko
        return value


@dataclass(frozen=True, slots=True)
class LocalExtensionOperation:
    """One structured query, set, or execute operation."""

    name: str
    scpi: str
    response_type: str = ""
    binary: bool = False

    def __post_init__(self) -> None:
        try:
            OperationKind(self.name)
        except ValueError as exc:
            raise ValueError(
                "Local operation name must be query, set, or execute"
            ) from exc
        object.__setattr__(
            self,
            "scpi",
            _safe_command_template(self.scpi, "SCPI command"),
        )
        if self.name == OperationKind.QUERY.value and "?" not in self.scpi:
            raise ValueError("A query extension command must contain '?'")
        if self.name != OperationKind.QUERY.value and "?" in self.scpi:
            raise ValueError(
                "Set/execute extension commands must not contain '?'"
            )
        if self.response_type not in _ALLOWED_RESPONSE_TYPES:
            raise ValueError(
                f"Unsupported response type: {self.response_type!r}"
            )
        if self.name != OperationKind.QUERY.value and self.response_type:
            raise ValueError("Only query operations declare a response type")


@dataclass(frozen=True, slots=True)
class LocalExtensionDefinition:
    """A typed draft which is still unusable until live validation passes."""

    extension_id: str
    source_profile_id: str
    category: DeviceCategory
    identity_raw: str
    identity_manufacturer: str
    identity_model: str
    identity_serial: str
    identity_firmware: str
    identity_options: str
    manual_id: str
    manual_title: str
    manual_url: str
    manual_page: int
    source_command_id: str
    source_command_pattern: str
    capability_id: str
    label_ko: str
    group: str
    risk_level: str
    operations: tuple[LocalExtensionOperation, ...]
    parameters: tuple[LocalExtensionParameter, ...] = ()
    probe_arguments: tuple[
        tuple[str, tuple[tuple[str, str], ...]],
        ...,
    ] = ()
    note_ko: str = ""
    identity_options_state: str = OPTION_STATE_UNQUERIED

    def __post_init__(self) -> None:
        if _SAFE_TOKEN.fullmatch(self.extension_id) is None:
            raise ValueError("extension_id must be a simple stable token")
        if not self.source_profile_id.strip():
            raise ValueError("source_profile_id must not be empty")
        identity_fields = {
            "raw IDN": self.identity_raw,
            "manufacturer": self.identity_manufacturer,
            "model": self.identity_model,
            "serial": self.identity_serial,
            "firmware": self.identity_firmware,
        }
        missing_identity = [
            name for name, value in identity_fields.items() if not value.strip()
        ]
        if missing_identity:
            raise ValueError(
                "Local extension promotion requires an exact "
                + ", ".join(missing_identity)
            )
        if not self.manual_id.strip() or not self.source_command_id.strip():
            raise ValueError("Manual and source command IDs are required")
        if self.manual_page < 1:
            raise ValueError("manual_page must be positive")
        if _CAPABILITY_ID.fullmatch(self.capability_id) is None:
            raise ValueError(
                "capability_id must use lowercase letters, numbers, dots, "
                "hyphens, or underscores"
            )
        if not self.capability_id.startswith("local."):
            raise ValueError("Local capability IDs must start with 'local.'")
        if not self.label_ko.strip() or not self.group.strip():
            raise ValueError("Local feature label and group are required")
        if self.risk_level not in _ALLOWED_RISKS:
            raise ValueError(f"Unsupported risk level: {self.risk_level!r}")
        if self.identity_options_state not in _OPTION_STATES:
            raise ValueError(
                "identity_options_state must be queried, unsupported, or "
                "unqueried"
            )
        if (
            self.identity_options_state == OPTION_STATE_QUERIED
            and not self.identity_options.strip()
        ):
            raise ValueError(
                "A queried option binding requires the exact *OPT? response"
            )
        if (
            self.identity_options_state
            in {OPTION_STATE_UNSUPPORTED, OPTION_STATE_UNQUERIED}
            and self.identity_options.strip()
        ):
            raise ValueError(
                "Only a queried option binding may contain an *OPT? response"
            )
        if not self.operations:
            raise ValueError("At least one structured operation is required")
        names = [operation.name for operation in self.operations]
        if len(names) != len(set(names)):
            raise ValueError("A local capability cannot repeat an operation kind")
        if (
            any(name != OperationKind.QUERY.value for name in names)
            and self.risk_level not in {"high", "hazardous", "critical"}
        ):
            raise ValueError(
                "Every manual SET/EXECUTE candidate is treated as high risk "
                "until a curated profile proves otherwise"
            )

        parameter_names = {parameter.name for parameter in self.parameters}
        placeholders = {
            name
            for operation in self.operations
            for name in _placeholder_names(operation.scpi)
        }
        if placeholders != parameter_names:
            missing = placeholders - parameter_names
            unused = parameter_names - placeholders
            details = []
            if missing:
                details.append("missing definitions: " + ", ".join(sorted(missing)))
            if unused:
                details.append("unused definitions: " + ", ".join(sorted(unused)))
            raise ValueError("Parameter metadata mismatch (" + "; ".join(details) + ")")

        arguments_by_operation = dict(self.probe_arguments)
        if len(arguments_by_operation) != len(self.probe_arguments):
            raise ValueError("Probe arguments repeat an operation name")
        unknown_operations = set(arguments_by_operation) - set(names)
        if unknown_operations:
            raise ValueError(
                "Probe arguments reference unknown operations: "
                + ", ".join(sorted(unknown_operations))
            )
        for operation in self.operations:
            operation_arguments = dict(
                arguments_by_operation.get(operation.name, ())
            )
            expected = set(_placeholder_names(operation.scpi))
            if set(operation_arguments) != expected:
                raise ValueError(
                    f"{operation.name} probe arguments must exactly match "
                    f"{sorted(expected)}"
                )
            for value in operation_arguments.values():
                _safe_command_template(str(value), "Probe argument")

        operation_names = set(names)
        if OperationKind.SET.value in operation_names:
            if OperationKind.QUERY.value not in operation_names:
                raise ValueError(
                    "A set extension requires a paired readback query"
                )
            set_placeholders = set(
                _placeholder_names(
                    next(
                        item.scpi
                        for item in self.operations
                        if item.name == OperationKind.SET.value
                    )
                )
            )
            query_placeholders = set(
                _placeholder_names(
                    next(
                        item.scpi
                        for item in self.operations
                        if item.name == OperationKind.QUERY.value
                    )
                )
            )
            restorable = set_placeholders - query_placeholders
            set_command = next(
                item.scpi
                for item in self.operations
                if item.name == OperationKind.SET.value
            )
            fixed_final_token = re.fullmatch(
                r".+?\s[A-Za-z][A-Za-z0-9_.+-]*",
                set_command,
            )
            if len(restorable) > 1 or (
                not restorable and fixed_final_token is None
            ):
                raise ValueError(
                    "A reversible set extension requires one value placeholder "
                    "not used by its readback query, or a conservative fixed "
                    "final mnemonic such as 'MODE MAXH'"
                )

    @property
    def identity(self) -> InstrumentIdentity:
        return InstrumentIdentity(
            raw=self.identity_raw,
            manufacturer=self.identity_manufacturer,
            model=self.identity_model,
            serial=self.identity_serial,
            firmware=self.identity_firmware,
        )

    @property
    def operation_ids(self) -> tuple[str, ...]:
        return tuple(
            f"{self.capability_id}::{operation.name}"
            for operation in self.operations
        )

    @property
    def operation_arguments(self) -> Mapping[str, Mapping[str, object]]:
        values = {
            f"{self.capability_id}::{operation_name}": MappingProxyType(
                dict(arguments)
            )
            for operation_name, arguments in self.probe_arguments
        }
        return MappingProxyType(values)

    @property
    def validation_profile_id(self) -> str:
        """Bind every source, identity, option, command, and range field."""

        payload = {
            "extension_id": self.extension_id,
            "source_profile_id": self.source_profile_id,
            "category": self.category.value,
            "identity": {
                "raw": self.identity_raw,
                "manufacturer": self.identity_manufacturer,
                "model": self.identity_model,
                "serial": self.identity_serial,
                "firmware": self.identity_firmware,
                "options": self.identity_options,
                "options_state": self.identity_options_state,
            },
            "source": {
                "manual_id": self.manual_id,
                "manual_title": self.manual_title,
                "manual_url": self.manual_url,
                "manual_page": self.manual_page,
                "command_id": self.source_command_id,
                "command_pattern": self.source_command_pattern,
            },
            "capability": {
                "capability_id": self.capability_id,
                "label_ko": self.label_ko,
                "group": self.group,
                "risk_level": self.risk_level,
                "operations": [
                    {
                        "name": operation.name,
                        "scpi": operation.scpi,
                        "response_type": operation.response_type,
                        "binary": operation.binary,
                    }
                    for operation in self.operations
                ],
                "parameters": [
                    parameter.to_catalog_dict()
                    for parameter in self.parameters
                ],
                "probe_arguments": [
                    [operation_name, list(arguments)]
                    for operation_name, arguments in self.probe_arguments
                ],
                "note_ko": self.note_ko,
            },
        }
        digest = hashlib.sha256(
            json.dumps(
                payload,
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            ).encode("utf-8")
        ).hexdigest()[:16]
        return (
            f"local_extension_{self.source_profile_id}_"
            f"{self.extension_id}_{digest}"
        )

    def matches_identity(
        self,
        identity: InstrumentIdentity,
        option_response: str | None = None,
        option_state: str | None = None,
    ) -> bool:
        if _identity_tuple(self.identity) != _identity_tuple(identity):
            return False
        if self.identity_options_state == OPTION_STATE_UNQUERIED:
            return False
        if option_state != self.identity_options_state:
            return False
        if self.identity_options_state == OPTION_STATE_QUERIED:
            if option_response is None:
                return False
            return (
                self.identity_options.strip().casefold()
                == option_response.strip().casefold()
            )
        return not (option_response or "").strip()

    def as_capability(self) -> CatalogCapability:
        return CatalogCapability(
            capability_id=self.capability_id,
            label_ko=self.label_ko,
            group=self.group,
            risk_level=self.risk_level,
            verification="local_extension_candidate",
            operations=tuple(
                CatalogOperation(
                    name=operation.name,
                    scpi=operation.scpi,
                    response_type=operation.response_type,
                    binary=operation.binary,
                )
                for operation in self.operations
            ),
            note_ko=(
                self.note_ko
                or (
                    f"Local extension from {self.manual_title}, "
                    f"p.{self.manual_page}."
                )
            ),
            parameters=tuple(
                parameter.to_catalog_dict()
                for parameter in self.parameters
            ),
        )

    def validation_profile(
        self,
        base_profile: InstrumentProfile,
    ) -> InstrumentProfile:
        if base_profile.profile_id != self.source_profile_id:
            raise ValueError("The extension belongs to another candidate pack")
        if base_profile.category is not self.category:
            raise ValueError("The extension category does not match the profile")
        return InstrumentProfile(
            profile_id=self.validation_profile_id,
            manufacturer=self.identity_manufacturer,
            model_family=self.identity_model,
            models=(self.identity_model,),
            instrument_class=base_profile.instrument_class,
            category=self.category,
            idn_patterns=(),
            verification_status="local_extension_candidate",
            hardware_verified=False,
            capabilities=(self.as_capability(),),
)


def _validate_promoted_pass_evidence(
    evidence: OperationValidation,
    *,
    paired_query: OperationValidation | None,
    capability: CatalogCapability,
    policy: ValidationPolicy,
) -> None:
    """Validate the engine evidence that authorizes one local operation."""

    prefix = f"{evidence.operation_id}: "
    if evidence.status is not OperationStatus.PASS:
        raise ValueError(prefix + "promoted operation evidence must be PASS")
    if evidence.attempts < 1:
        raise ValueError(prefix + "PASS evidence requires an actual attempt")
    if evidence.failure_kind is not FailureKind.NONE:
        raise ValueError(prefix + "PASS evidence cannot contain a failure")

    if evidence.kind is OperationKind.QUERY:
        if evidence.validation_mode != "automatic_query":
            raise ValueError(
                prefix + "QUERY PASS requires automatic_query evidence"
            )
        try:
            expected_query, _ = _render_command(
                evidence,
                capability,
                policy.operation_arguments.get(evidence.operation_id, {}),
            )
        except ValueError as exc:
            raise ValueError(
                prefix + f"QUERY PASS command could not be rendered: {exc}"
            ) from exc
        if (
            evidence.sent_commands != (expected_query,)
            or "?" not in expected_query
            or not evidence.response.strip()
            or evidence.original_response != evidence.response
            or evidence.verification_response
            or evidence.restore_attempted
            or evidence.restored
        ):
            raise ValueError(
                prefix
                + "QUERY PASS transcript does not match the rendered command "
                "and response evidence"
            )
        return

    if evidence.kind is OperationKind.SET:
        if evidence.validation_mode != "automatic_reversible_write":
            raise ValueError(
                prefix
                + "SET PASS requires automatic_reversible_write evidence"
            )
        commands = evidence.sent_commands
        try:
            if paired_query is None:
                raise ValueError("paired readback query is missing")
            (
                expected_write,
                expected_restore,
                _test_token,
                _original_token,
            ) = _build_reversible_write(
                evidence,
                paired_query,
                capability,
                policy,
            )
            expected_readback, _ = _render_command(
                paired_query,
                capability,
                policy.operation_arguments.get(
                    paired_query.operation_id,
                    {},
                ),
            )
        except ValueError as exc:
            raise ValueError(
                prefix
                + f"SET PASS transcript could not be reconstructed: {exc}"
            ) from exc
        expected_commands = (
            expected_write,
            expected_readback,
            expected_restore,
            expected_readback,
        )
        if (
            commands != expected_commands
            or paired_query.sent_commands != (expected_readback,)
            or not evidence.original_response.strip()
            or evidence.original_response != paired_query.response
            or not evidence.verification_response.strip()
            or evidence.response != evidence.verification_response
            or not evidence.restore_attempted
            or not evidence.restored
        ):
            raise ValueError(
                prefix
                + "SET PASS requires write/readback/restore evidence and "
                "verified restoration; the transcript does not match the "
                "rendered commands"
            )
        return

    if evidence.kind is OperationKind.EXECUTE:
        if evidence.validation_mode not in {
            "manual_operator",
            "manual_operator_hazardous",
        }:
            raise ValueError(
                prefix + "EXECUTE PASS requires manual operator evidence"
            )
        if not evidence.message.strip():
            raise ValueError(
                prefix + "EXECUTE PASS requires a non-empty manual evidence note"
            )
        if (
            evidence.sent_commands
            or evidence.response
            or evidence.original_response
            or evidence.verification_response
            or evidence.restore_attempted
            or evidence.restored
        ):
            raise ValueError(
                prefix
                + "EXECUTE PASS must not claim an automatically transmitted "
                "command or readback"
            )
        return

    raise ValueError(prefix + f"unsupported operation kind {evidence.kind!r}")


@dataclass(frozen=True, slots=True)
class PromotedLocalExtension:
    """A local definition plus immutable live-validation evidence."""

    definition: LocalExtensionDefinition
    validation_result: ValidationResult

    def __post_init__(self) -> None:
        if (
            self.definition.identity_options_state
            == OPTION_STATE_UNQUERIED
        ):
            raise ValueError(
                "A local extension cannot be promoted before *OPT? support "
                "and the current option state are checked"
            )
        expected_profile_id = self.definition.validation_profile_id
        result = self.validation_result
        if result.source_profile_id != expected_profile_id:
            raise ValueError("Validation result belongs to another extension")
        expected_fingerprint = profile_fingerprint(
            InstrumentProfile(
                profile_id=expected_profile_id,
                manufacturer=self.definition.identity_manufacturer,
                model_family=self.definition.identity_model,
                models=(self.definition.identity_model,),
                instrument_class="local_extension",
                category=self.definition.category,
                idn_patterns=(),
                verification_status="local_extension_candidate",
                hardware_verified=False,
                capabilities=(self.definition.as_capability(),),
            )
        )
        if result.catalog_fingerprint != expected_fingerprint:
            raise ValueError(
                "Local extension definition changed after live validation"
            )
        if _identity_tuple(self.definition.identity) != _identity_tuple(
            InstrumentIdentity(
                raw=result.identity_raw,
                manufacturer=result.identity_manufacturer,
                model=result.identity_model,
                serial=result.identity_serial,
                firmware=result.identity_firmware,
            )
        ):
            raise ValueError("Validation result identity does not match extension")
        expected_ids = set(self.definition.operation_ids)
        actual_ids = {item.operation_id for item in result.operations}
        if expected_ids != actual_ids:
            raise ValueError("Validation evidence does not match extension operations")
        if set(result.compatible_operation_ids) != expected_ids:
            raise ValueError(
                "Every operation in a promoted extension must have PASS evidence"
            )
        if result.incompatible_operation_ids or result.unresolved_operation_ids:
            raise ValueError("A promoted extension cannot contain unresolved operations")
        operations_by_name = {
            operation.name: operation
            for operation in self.definition.operations
        }
        evidence_by_kind = {
            evidence.kind: evidence for evidence in result.operations
        }
        capability = self.definition.as_capability()
        evidence_policy = ValidationPolicy(
            operation_arguments=self.definition.operation_arguments,
        )
        for evidence in result.operations:
            definition_operation = operations_by_name[evidence.operation_name]
            if (
                evidence.command_template != definition_operation.scpi
                or evidence.kind.value != definition_operation.name
                or evidence.response_type
                != definition_operation.response_type
                or evidence.binary != definition_operation.binary
                or evidence.risk_level != self.definition.risk_level
            ):
                raise ValueError(
                    "Local extension operation metadata changed after validation"
                )
            _validate_promoted_pass_evidence(
                evidence,
                paired_query=evidence_by_kind.get(OperationKind.QUERY),
                capability=capability,
                policy=evidence_policy,
            )

    @property
    def compatible_operation_ids(self) -> tuple[str, ...]:
        return self.validation_result.compatible_operation_ids


@dataclass(frozen=True, slots=True)
class LocalExtensionRegistry:
    schema_version: int = LOCAL_EXTENSION_SCHEMA_VERSION
    records: tuple[PromotedLocalExtension, ...] = ()
    base_generation: int = field(default=0, compare=False, repr=False)
    base_digest: str = field(default="", compare=False, repr=False)

    def __post_init__(self) -> None:
        if self.schema_version != LOCAL_EXTENSION_SCHEMA_VERSION:
            raise ValueError(
                f"Unsupported local extension schema: {self.schema_version}"
            )
        if type(self.base_generation) is not int or self.base_generation < 0:
            raise ValueError("Local extension base generation is invalid")
        if self.base_generation == 0:
            if self.base_digest:
                raise ValueError(
                    "An unsaved local extension registry cannot have a digest"
                )
        elif not _valid_sha256_hex(self.base_digest):
            raise ValueError("Local extension base digest is invalid")
        extension_ids = [
            record.definition.extension_id for record in self.records
        ]
        if len(extension_ids) != len(set(extension_ids)):
            raise ValueError("Local extension registry contains duplicate IDs")
        operation_ids = [
            operation_id
            for record in self.records
            for operation_id in record.compatible_operation_ids
        ]
        if len(operation_ids) != len(set(operation_ids)):
            raise ValueError(
                "Local extension registry contains duplicate operation IDs"
            )

    def for_profile(
        self,
        profile_id: str,
    ) -> tuple[PromotedLocalExtension, ...]:
        return tuple(
            record
            for record in self.records
            if record.definition.source_profile_id == profile_id
        )

    def for_identity(
        self,
        profile_id: str,
        identity: InstrumentIdentity,
        option_response: str | None = None,
        option_state: str | None = None,
    ) -> tuple[PromotedLocalExtension, ...]:
        return tuple(
            record
            for record in self.for_profile(profile_id)
            if record.definition.matches_identity(
                identity,
                option_response,
                option_state,
            )
        )

    def by_operation_id(
        self,
        operation_id: str,
    ) -> PromotedLocalExtension | None:
        return next(
            (
                record
                for record in self.records
                if operation_id in record.compatible_operation_ids
            ),
            None,
        )

    def replace(self, promoted: PromotedLocalExtension) -> LocalExtensionRegistry:
        records = [
            record
            for record in self.records
            if record.definition.extension_id
            != promoted.definition.extension_id
        ]
        records.append(promoted)
        records.sort(key=lambda item: item.definition.extension_id)
        return LocalExtensionRegistry(
            records=tuple(records),
            base_generation=self.base_generation,
            base_digest=self.base_digest,
        )

    def remove(self, extension_id: str) -> LocalExtensionRegistry:
        """Return a registry without one revoked extension."""

        return LocalExtensionRegistry(
            records=tuple(
                record
                for record in self.records
                if record.definition.extension_id != extension_id
            ),
            base_generation=self.base_generation,
            base_digest=self.base_digest,
        )


def _extension_hash(
    candidate: ManualCommandCandidate,
    identity: InstrumentIdentity,
    operation_kind: str,
    discriminator: str = "",
) -> str:
    payload = "\x1f".join(
        (
            candidate.profile_id,
            candidate.manual_id,
            candidate.command_id,
            identity.raw,
            identity.manufacturer,
            identity.model,
            identity.serial,
            identity.firmware,
            operation_kind,
            discriminator,
        )
    ).encode("utf-8")
    return "ext_" + hashlib.sha256(payload).hexdigest()[:16]


def _capability_slug(value: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", ".", value.casefold()).strip(".")
    return slug or "manual"


def _structured_extension_discriminator(
    *,
    label_ko: str,
    group: str,
    risk_level: str,
    option_response: str,
    option_state: str,
    operations: Iterable[LocalExtensionOperation],
    parameters: Iterable[LocalExtensionParameter],
    probe_arguments: Iterable[
        tuple[str, tuple[tuple[str, str], ...]]
    ],
) -> str:
    """Return a stable discriminator for one exact tested argument variant."""

    payload = {
        "label_ko": label_ko.strip(),
        "group": group.strip(),
        "risk_level": risk_level,
        "option_response": option_response.strip(),
        "option_state": option_state,
        "operations": [
            {
                "name": operation.name,
                "scpi": operation.scpi,
                "response_type": operation.response_type,
                "binary": operation.binary,
            }
            for operation in operations
        ],
        "parameters": [
            parameter.to_catalog_dict() for parameter in parameters
        ],
        "probe_arguments": [
            [operation_name, list(arguments)]
            for operation_name, arguments in probe_arguments
        ],
    }
    return json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )


def _ordered_probe_arguments(
    placeholders: Iterable[str],
    supplied: Mapping[str, object] | None,
    operation_name: str,
) -> tuple[tuple[str, str], ...]:
    names = tuple(placeholders)
    values = dict(supplied or {})
    missing = set(names) - set(values)
    unknown = set(values) - set(names)
    if missing or unknown:
        details = []
        if missing:
            details.append("missing " + ", ".join(sorted(missing)))
        if unknown:
            details.append("unknown " + ", ".join(sorted(unknown)))
        raise ValueError(
            f"{operation_name} probe arguments do not match the template: "
            + "; ".join(details)
        )
    return tuple((name, str(values[name])) for name in names)


def query_extension_draft(
    candidate: ManualCommandCandidate,
    identity: InstrumentIdentity,
    category: DeviceCategory,
    *,
    label_ko: str,
    response_type: str = "string",
    group: str = "manual",
    capability_slug: str = "",
    risk_level: str = "low",
    query_arguments: Mapping[str, object] | None = None,
    parameters: Iterable[LocalExtensionParameter] | None = None,
    query_command: str = "",
    option_response: str = "",
    option_state: str | None = None,
    note_ko: str = "",
) -> LocalExtensionDefinition:
    """Structure an explicit manual query as a non-runnable draft."""

    if candidate.probe_policy == "manual_only":
        raise ValueError(
            "manual_only candidates cannot be converted into an automatic "
            "query; use the manual execute-evidence path"
        )
    if not (query_command.strip() or candidate.query_probe.strip()):
        raise ValueError("The manual candidate has no query command")
    effective_query = query_command.strip() or candidate.query_probe
    resolved_option_state = (
        option_state
        if option_state is not None
        else (
            OPTION_STATE_QUERIED
            if option_response.strip()
            else OPTION_STATE_UNQUERIED
        )
    )
    slug = _capability_slug(
        capability_slug
        or candidate.command_pattern.rstrip("?")
        or candidate.command_group
    )
    command = LocalExtensionOperation(
        name=OperationKind.QUERY.value,
        scpi=effective_query,
        response_type=response_type,
    )
    placeholders = _placeholder_names(command.scpi)
    arguments = _ordered_probe_arguments(
        placeholders,
        query_arguments,
        OperationKind.QUERY.value,
    )
    parameter_definitions = (
        tuple(parameters)
        if parameters is not None
        else tuple(
            LocalExtensionParameter(name=name, value_type="integer")
            for name in placeholders
        )
    )
    ordered_arguments = (
        (
            OperationKind.QUERY.value,
            arguments,
        ),
    )
    extension_id = _extension_hash(
        candidate,
        identity,
        "query",
        _structured_extension_discriminator(
            label_ko=label_ko,
            group=group,
            risk_level=risk_level,
            option_response=option_response,
            option_state=resolved_option_state,
            operations=(command,),
            parameters=parameter_definitions,
            probe_arguments=ordered_arguments,
        ),
    )
    return LocalExtensionDefinition(
        extension_id=extension_id,
        source_profile_id=candidate.profile_id,
        category=category,
        identity_raw=identity.raw,
        identity_manufacturer=identity.manufacturer,
        identity_model=identity.model,
        identity_serial=identity.serial,
        identity_firmware=identity.firmware,
        identity_options=option_response.strip(),
        manual_id=candidate.manual_id,
        manual_title=candidate.source.title,
        manual_url=candidate.source_url,
        manual_page=candidate.manual_page,
        source_command_id=candidate.command_id,
        source_command_pattern=candidate.command_pattern,
        capability_id=f"local.{slug}.{extension_id}",
        label_ko=label_ko.strip(),
        group=group.strip() or "manual",
        risk_level=risk_level,
        operations=(command,),
        parameters=parameter_definitions,
        probe_arguments=ordered_arguments,
        note_ko=note_ko,
        identity_options_state=resolved_option_state,
    )


def typed_extension_draft(
    candidate: ManualCommandCandidate,
    identity: InstrumentIdentity,
    category: DeviceCategory,
    *,
    operation_kind: OperationKind | str,
    label_ko: str,
    command_template: str,
    group: str = "manual",
    capability_slug: str = "",
    risk_level: str = "hazardous",
    parameters: Iterable[LocalExtensionParameter] = (),
    probe_arguments: Mapping[str, object] | None = None,
    readback_query: str = "",
    readback_response_type: str = "string",
    readback_arguments: Mapping[str, object] | None = None,
    option_response: str = "",
    option_state: str | None = None,
    note_ko: str = "",
) -> LocalExtensionDefinition:
    """Create an explicitly typed SET or EXECUTE draft.

    SET drafts must declare a paired readback query and exactly one restorable
    value placeholder.  EXECUTE drafts remain manual-evidence operations.
    """

    kind = (
        operation_kind
        if isinstance(operation_kind, OperationKind)
        else OperationKind(operation_kind)
    )
    if kind is OperationKind.QUERY:
        raise ValueError("Use query_extension_draft for query operations")
    resolved_option_state = (
        option_state
        if option_state is not None
        else (
            OPTION_STATE_QUERIED
            if option_response.strip()
            else OPTION_STATE_UNQUERIED
        )
    )
    slug = _capability_slug(
        capability_slug
        or candidate.command_pattern.rstrip("?")
        or candidate.command_group
    )
    operations: list[LocalExtensionOperation] = []
    arguments: list[tuple[str, tuple[tuple[str, str], ...]]] = []
    if kind is OperationKind.SET:
        if not readback_query.strip():
            raise ValueError("A set extension requires a readback query")
        operations.append(
            LocalExtensionOperation(
                name=OperationKind.QUERY.value,
                scpi=readback_query,
                response_type=readback_response_type,
            )
        )
        arguments.append(
            (
                OperationKind.QUERY.value,
                _ordered_probe_arguments(
                    _placeholder_names(readback_query),
                    readback_arguments,
                    OperationKind.QUERY.value,
                ),
            )
        )
    operations.append(
        LocalExtensionOperation(
            name=kind.value,
            scpi=command_template,
        )
    )
    arguments.append(
        (
            kind.value,
            _ordered_probe_arguments(
                _placeholder_names(command_template),
                probe_arguments,
                kind.value,
            ),
        )
    )
    parameter_definitions = tuple(parameters)
    ordered_operations = tuple(operations)
    ordered_arguments = tuple(arguments)
    extension_id = _extension_hash(
        candidate,
        identity,
        kind.value,
        _structured_extension_discriminator(
            label_ko=label_ko,
            group=group,
            risk_level=risk_level,
            option_response=option_response,
            option_state=resolved_option_state,
            operations=ordered_operations,
            parameters=parameter_definitions,
            probe_arguments=ordered_arguments,
        ),
    )
    return LocalExtensionDefinition(
        extension_id=extension_id,
        source_profile_id=candidate.profile_id,
        category=category,
        identity_raw=identity.raw,
        identity_manufacturer=identity.manufacturer,
        identity_model=identity.model,
        identity_serial=identity.serial,
        identity_firmware=identity.firmware,
        identity_options=option_response.strip(),
        manual_id=candidate.manual_id,
        manual_title=candidate.source.title,
        manual_url=candidate.source_url,
        manual_page=candidate.manual_page,
        source_command_id=candidate.command_id,
        source_command_pattern=candidate.command_pattern,
        capability_id=f"local.{slug}.{extension_id}",
        label_ko=label_ko.strip(),
        group=group.strip() or "manual",
        risk_level=risk_level,
        operations=ordered_operations,
        parameters=parameter_definitions,
        probe_arguments=ordered_arguments,
        note_ko=note_ko,
        identity_options_state=resolved_option_state,
    )


def validate_local_extension(
    definition: LocalExtensionDefinition,
    base_profile: InstrumentProfile,
    session: ValidationSession,
    *,
    timeout_ms: int = 2000,
    error_query: str | None = "SYST:ERR?",
    approved_hazardous: bool = False,
    progress: ValidationProgress | None = None,
) -> ValidationResult:
    """Validate one typed draft with the same deterministic engine as profiles."""

    if definition.identity_options_state == OPTION_STATE_UNQUERIED:
        raise ValueError(
            "Check *OPT? support and bind the current option state before "
            "validating a local extension"
        )
    verify_local_extension_identity(definition, session)
    profile = definition.validation_profile(base_profile)
    approved = (
        frozenset(definition.operation_ids)
        if approved_hazardous
        else frozenset()
    )
    policy = ValidationPolicy(
        timeout_ms=timeout_ms,
        error_query=error_query,
        operation_arguments=definition.operation_arguments,
        approved_hazardous_operation_ids=approved,
    )
    if progress is None:
        progress = create_validation_progress(
            profile,
            "",
            definition.identity,
        )
    validated = validate_profile(
        profile,
        session,
        resource="",
        policy=policy,
        progress=progress,
    )
    return build_validation_result(validated)


def bind_local_extension_options(
    definition: LocalExtensionDefinition,
    session: ValidationSession,
) -> LocalExtensionDefinition:
    """Bind an unpromoted draft to the live *OPT? support state.

    This helper only performs the safe common option query.  It never sends a
    candidate operation.  A newly discovered option response re-keys the local
    extension so two option variants cannot overwrite each other.
    """

    try:
        live_options = str(session.query("*OPT?")).strip()
    except Exception as exc:
        if (
            definition.identity_options_state == OPTION_STATE_QUERIED
        ):
            raise ValueError(
                "The connected instrument no longer provides the *OPT? "
                "response required by this draft"
            ) from exc
        option_state = OPTION_STATE_UNSUPPORTED
        option_response = ""
    else:
        if not live_options:
            raise ValueError("*OPT? returned an empty option response")
        if (
            definition.identity_options_state == OPTION_STATE_QUERIED
            and definition.identity_options.strip().casefold()
            != live_options.casefold()
        ):
            raise ValueError(
                "Connected instrument option response does not match the "
                "draft"
            )
        option_state = OPTION_STATE_QUERIED
        option_response = live_options

    if (
        definition.identity_options_state == option_state
        and definition.identity_options.strip() == option_response
    ):
        return definition

    discriminator = _structured_extension_discriminator(
        label_ko=definition.label_ko,
        group=definition.group,
        risk_level=definition.risk_level,
        option_response=option_response,
        option_state=option_state,
        operations=definition.operations,
        parameters=definition.parameters,
        probe_arguments=definition.probe_arguments,
    )
    payload = "\x1f".join(
        (
            definition.source_profile_id,
            definition.manual_id,
            definition.source_command_id,
            definition.identity_raw,
            definition.identity_manufacturer,
            definition.identity_model,
            definition.identity_serial,
            definition.identity_firmware,
            ",".join(
                operation.name for operation in definition.operations
            ),
            discriminator,
        )
    ).encode("utf-8")
    extension_id = "ext_" + hashlib.sha256(payload).hexdigest()[:16]
    capability_prefix = definition.capability_id.removesuffix(
        definition.extension_id
    )
    return replace(
        definition,
        extension_id=extension_id,
        capability_id=f"{capability_prefix}{extension_id}",
        identity_options=option_response,
        identity_options_state=option_state,
    )


def verify_local_extension_identity(
    definition: LocalExtensionDefinition,
    session: ValidationSession,
) -> None:
    """Fail before any candidate command when IDN/options do not match."""

    live_identity = parse_idn_response(str(session.query("*IDN?")))
    if _identity_tuple(live_identity) != _identity_tuple(definition.identity):
        raise ValueError(
            "Connected instrument manufacturer/model/serial/firmware does not "
            "match the local extension identity"
        )
    if definition.identity_options_state == OPTION_STATE_UNQUERIED:
        raise ValueError(
            "The local extension has no verified *OPT? support state"
        )
    try:
        live_options = str(session.query("*OPT?")).strip()
    except Exception as exc:
        if definition.identity_options_state == OPTION_STATE_UNSUPPORTED:
            return
        raise ValueError(
            "The connected instrument did not provide the required *OPT? "
            "response"
        ) from exc
    if definition.identity_options_state == OPTION_STATE_UNSUPPORTED:
        raise ValueError(
            "This instrument answered *OPT?; register the exact option "
            "response instead of marking options unsupported"
        )
    if (
        live_options.casefold()
        != definition.identity_options.strip().casefold()
    ):
        raise ValueError(
            "Connected instrument option response does not match the "
            "local extension evidence"
        )


def attest_local_extension(
    definition: LocalExtensionDefinition,
    base_profile: InstrumentProfile,
    session: ValidationSession,
    *,
    passed: bool,
    note: str,
    hazardous_approved: bool = False,
) -> ValidationResult:
    """Record operator evidence for an execute-only extension.

    The program deliberately does not transmit an unstructured execute
    command.  The operator must test it under an appropriate bench procedure
    and record what was observed.  High-risk operations require a separate,
    exact approval flag in addition to the evidence note.  The current IDN and
    option response are re-queried before the evidence can be accepted.
    """

    verify_local_extension_identity(definition, session)
    if tuple(operation.name for operation in definition.operations) != (
        OperationKind.EXECUTE.value,
    ):
        raise ValueError(
            "Manual attestation is only available for execute-only extensions"
        )
    hazardous = definition.risk_level in {
        "high",
        "hazardous",
        "critical",
    }
    if hazardous and not hazardous_approved:
        raise ValueError(
            "High-risk execute extension requires a separate exact approval"
        )
    profile = definition.validation_profile(base_profile)
    progress = create_validation_progress(
        profile,
        "",
        definition.identity,
    )
    operation = progress.operations[0]
    progress = progress.replace_operation(
        replace(
            operation,
            status=OperationStatus.MANUAL,
            validation_mode="manual_required",
            message=(
                "Execute operations require operator-observed evidence and "
                "are never transmitted automatically."
            ),
        )
    )
    progress = apply_manual_result(
        progress,
        operation.operation_id,
        passed=passed,
        note=note,
        validation_mode=(
            "manual_operator_hazardous"
            if hazardous
            else "manual_operator"
        ),
    )
    return build_validation_result(progress)


def promote_local_extension(
    definition: LocalExtensionDefinition,
    validation_result: ValidationResult,
    registry: LocalExtensionRegistry | None = None,
) -> LocalExtensionRegistry:
    """Return a registry containing the extension only when every op passed."""

    promoted = PromotedLocalExtension(
        definition=definition,
        validation_result=validation_result,
    )
    return (registry or LocalExtensionRegistry()).replace(promoted)


def merge_profile_extensions(
    base_profile: InstrumentProfile,
    records: Iterable[PromotedLocalExtension],
) -> InstrumentProfile:
    """Build a validation profile that includes identity-approved extensions."""

    additions = tuple(
        record.definition.as_capability()
        for record in records
        if record.definition.source_profile_id == base_profile.profile_id
    )
    if not additions:
        return base_profile
    known = {
        capability.capability_id for capability in base_profile.capabilities
    }
    if any(item.capability_id in known for item in additions):
        raise ValueError("Local extension conflicts with a catalog capability")
    return InstrumentProfile(
        profile_id=base_profile.profile_id,
        manufacturer=base_profile.manufacturer,
        model_family=base_profile.model_family,
        models=base_profile.models,
        instrument_class=base_profile.instrument_class,
        category=base_profile.category,
        idn_patterns=base_profile.idn_patterns,
        verification_status=base_profile.verification_status,
        hardware_verified=base_profile.hardware_verified,
        capabilities=base_profile.capabilities + additions,
    )


def default_local_extension_path() -> Path:
    """Return the writable per-user registry location."""

    local_app_data = os.environ.get("LOCALAPPDATA", "").strip()
    if local_app_data:
        root = Path(local_app_data)
    else:
        root = Path.home() / "AppData" / "Local"
    return root / "SCPI-Automation-Platform" / "local_extensions.json"


def _local_extension_key_path(registry_path: Path) -> Path:
    """Keep a custom registry's stable authentication key beside the JSON."""

    return registry_path.with_name(registry_path.name + ".key")


def _local_extension_state_path(registry_path: Path) -> Path:
    """Keep the latest authenticated registry generation outside the JSON."""

    return registry_path.with_name(registry_path.name + ".state")


def _local_extension_lock_path(registry_path: Path) -> Path:
    """Return the process-shared lock file for one local registry."""

    return registry_path.with_name(registry_path.name + ".lock")


def _dpapi_protect(secret: bytes) -> bytes:
    """Protect a secret with the current Windows user's DPAPI credentials."""

    import ctypes
    from ctypes import wintypes

    class DataBlob(ctypes.Structure):
        _fields_ = [
            ("cbData", wintypes.DWORD),
            ("pbData", ctypes.POINTER(ctypes.c_ubyte)),
        ]

    crypt32 = ctypes.WinDLL("crypt32", use_last_error=True)
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    crypt32.CryptProtectData.argtypes = [
        ctypes.POINTER(DataBlob),
        wintypes.LPCWSTR,
        ctypes.POINTER(DataBlob),
        wintypes.LPVOID,
        wintypes.LPVOID,
        wintypes.DWORD,
        ctypes.POINTER(DataBlob),
    ]
    crypt32.CryptProtectData.restype = wintypes.BOOL
    kernel32.LocalFree.argtypes = [wintypes.HLOCAL]
    kernel32.LocalFree.restype = wintypes.HLOCAL

    input_buffer = ctypes.create_string_buffer(secret)
    input_blob = DataBlob(
        len(secret),
        ctypes.cast(input_buffer, ctypes.POINTER(ctypes.c_ubyte)),
    )
    output_blob = DataBlob()
    if not crypt32.CryptProtectData(
        ctypes.byref(input_blob),
        "SCPI Automation Platform local extension key",
        None,
        None,
        None,
        0x1,  # CRYPTPROTECT_UI_FORBIDDEN
        ctypes.byref(output_blob),
    ):
        error = ctypes.get_last_error()
        raise OSError(error, "Windows DPAPI could not protect the registry key")
    try:
        return ctypes.string_at(output_blob.pbData, output_blob.cbData)
    finally:
        kernel32.LocalFree(
            ctypes.cast(output_blob.pbData, wintypes.HLOCAL)
        )


def _dpapi_unprotect(protected: bytes) -> bytes:
    """Unprotect a key that was bound to the current Windows user."""

    import ctypes
    from ctypes import wintypes

    class DataBlob(ctypes.Structure):
        _fields_ = [
            ("cbData", wintypes.DWORD),
            ("pbData", ctypes.POINTER(ctypes.c_ubyte)),
        ]

    crypt32 = ctypes.WinDLL("crypt32", use_last_error=True)
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    crypt32.CryptUnprotectData.argtypes = [
        ctypes.POINTER(DataBlob),
        ctypes.POINTER(wintypes.LPWSTR),
        ctypes.POINTER(DataBlob),
        wintypes.LPVOID,
        wintypes.LPVOID,
        wintypes.DWORD,
        ctypes.POINTER(DataBlob),
    ]
    crypt32.CryptUnprotectData.restype = wintypes.BOOL
    kernel32.LocalFree.argtypes = [wintypes.HLOCAL]
    kernel32.LocalFree.restype = wintypes.HLOCAL

    input_buffer = ctypes.create_string_buffer(protected)
    input_blob = DataBlob(
        len(protected),
        ctypes.cast(input_buffer, ctypes.POINTER(ctypes.c_ubyte)),
    )
    output_blob = DataBlob()
    if not crypt32.CryptUnprotectData(
        ctypes.byref(input_blob),
        None,
        None,
        None,
        None,
        0x1,  # CRYPTPROTECT_UI_FORBIDDEN
        ctypes.byref(output_blob),
    ):
        error = ctypes.get_last_error()
        raise OSError(error, "Windows DPAPI could not unprotect the registry key")
    try:
        return ctypes.string_at(output_blob.pbData, output_blob.cbData)
    finally:
        kernel32.LocalFree(
            ctypes.cast(output_blob.pbData, wintypes.HLOCAL)
        )


def _atomic_write_bytes(path: Path, content: bytes) -> None:
    """Durably replace one file without exposing a partially written payload."""

    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{path.name}.",
        suffix=".tmp",
        dir=path.parent,
    )
    temporary = Path(temporary_name)
    try:
        if os.name != "nt":
            os.chmod(temporary, 0o600)
        with os.fdopen(descriptor, "wb") as stream:
            descriptor = -1
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    except BaseException:
        if descriptor >= 0:
            os.close(descriptor)
        try:
            temporary.unlink()
        except FileNotFoundError:
            pass
        raise


@contextmanager
def _registry_file_lock(
    registry_path: Path,
    *,
    timeout_seconds: float = 5.0,
):
    """Serialize registry reads and compare-and-swap writes across processes."""

    lock_path = _local_extension_lock_path(registry_path)
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    with lock_path.open("a+b") as stream:
        stream.seek(0, os.SEEK_END)
        if stream.tell() == 0:
            stream.write(b"\0")
            stream.flush()
            os.fsync(stream.fileno())
        deadline = time.monotonic() + timeout_seconds
        acquired = False
        try:
            if os.name == "nt":
                import msvcrt

                while not acquired:
                    try:
                        stream.seek(0)
                        msvcrt.locking(
                            stream.fileno(),
                            msvcrt.LK_NBLCK,
                            1,
                        )
                        acquired = True
                    except OSError:
                        if time.monotonic() >= deadline:
                            raise ValueError(
                                "Another program window is updating the local "
                                "extension registry"
                            )
                        time.sleep(0.05)
            else:
                import fcntl

                while not acquired:
                    try:
                        fcntl.flock(
                            stream.fileno(),
                            fcntl.LOCK_EX | fcntl.LOCK_NB,
                        )
                        acquired = True
                    except BlockingIOError:
                        if time.monotonic() >= deadline:
                            raise ValueError(
                                "Another program window is updating the local "
                                "extension registry"
                            )
                        time.sleep(0.05)
            yield
        finally:
            if acquired:
                if os.name == "nt":
                    import msvcrt

                    stream.seek(0)
                    msvcrt.locking(
                        stream.fileno(),
                        msvcrt.LK_UNLCK,
                        1,
                    )
                else:
                    import fcntl

                    fcntl.flock(stream.fileno(), fcntl.LOCK_UN)


@dataclass(frozen=True, slots=True)
class _RegistryKeyRecord:
    secret: bytes
    generation: int
    registry_digest: str


def _encode_registry_key(
    record: _RegistryKeyRecord,
    registry_path: Path,
) -> bytes:
    cleartext = (
        json.dumps(
            {
                "document_type": _KEY_DOCUMENT_TYPE,
                "schema_version": _KEY_SCHEMA_VERSION,
                "registry_path_hash": _registry_path_hash(registry_path),
                "secret": record.secret.hex(),
                "generation": record.generation,
                "registry_digest": record.registry_digest,
            },
            ensure_ascii=False,
            indent=2,
            allow_nan=False,
        )
        + "\n"
    ).encode("utf-8")
    if os.name == "nt":
        return _DPAPI_KEY_PREFIX + _dpapi_protect(cleartext)
    return _RAW_KEY_PREFIX + cleartext


def _load_registry_key_record(
    key_path: Path,
    registry_path: Path,
) -> _RegistryKeyRecord:
    try:
        encoded = key_path.read_bytes()
    except FileNotFoundError as exc:
        raise ValueError(
            "Local extension authentication key is missing"
        ) from exc
    except OSError as exc:
        raise ValueError(
            f"Could not read local extension authentication key: {exc}"
        ) from exc

    try:
        if os.name == "nt":
            if not encoded.startswith(_DPAPI_KEY_PREFIX):
                raise ValueError(
                    "Local extension key is not protected with Windows DPAPI"
                )
            cleartext = _dpapi_unprotect(
                encoded[len(_DPAPI_KEY_PREFIX) :]
            )
        else:
            if stat.S_IMODE(key_path.stat().st_mode) & 0o077:
                raise ValueError(
                    "Local extension key permissions must be 0600"
                )
            if not encoded.startswith(_RAW_KEY_PREFIX):
                raise ValueError("Local extension key has an invalid format")
            cleartext = encoded[len(_RAW_KEY_PREFIX) :]
    except OSError as exc:
        raise ValueError(
            f"Could not unlock local extension authentication key: {exc}"
        ) from exc

    try:
        payload = json.loads(cleartext.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError(
            f"Local extension authentication key is invalid: {exc}"
        ) from exc
    expected_keys = {
        "document_type",
        "schema_version",
        "registry_path_hash",
        "secret",
        "generation",
        "registry_digest",
    }
    if not isinstance(payload, dict) or set(payload) != expected_keys:
        raise ValueError("Local extension authentication key is invalid")
    if (
        payload.get("document_type") != _KEY_DOCUMENT_TYPE
        or payload.get("schema_version") != _KEY_SCHEMA_VERSION
        or payload.get("registry_path_hash")
        != _registry_path_hash(registry_path)
        or not _valid_sha256_hex(payload.get("secret"))
    ):
        raise ValueError(
            "Local extension authentication key does not belong to this registry"
        )
    generation = payload.get("generation")
    registry_digest = payload.get("registry_digest")
    if type(generation) is not int or generation < 0:
        raise ValueError("Local extension authentication key is invalid")
    if generation == 0:
        if registry_digest != "":
            raise ValueError("Local extension authentication key is invalid")
    elif not _valid_sha256_hex(registry_digest):
        raise ValueError("Local extension authentication key is invalid")
    secret = bytes.fromhex(payload["secret"])
    if len(secret) != _KEY_SIZE:
        raise ValueError("Local extension authentication key is invalid")
    return _RegistryKeyRecord(
        secret=secret,
        generation=generation,
        registry_digest=registry_digest.casefold(),
    )


def _write_registry_key_record(
    key_path: Path,
    registry_path: Path,
    record: _RegistryKeyRecord,
) -> None:
    _atomic_write_bytes(
        key_path,
        _encode_registry_key(record, registry_path),
    )


def _load_or_create_registry_key(
    key_path: Path,
    registry_path: Path,
) -> _RegistryKeyRecord:
    if key_path.exists():
        return _load_registry_key_record(key_path, registry_path)
    record = _RegistryKeyRecord(
        secret=secrets.token_bytes(_KEY_SIZE),
        generation=0,
        registry_digest="",
    )
    _write_registry_key_record(key_path, registry_path, record)
    return _load_registry_key_record(key_path, registry_path)


def _canonical_registry_payload(payload: Mapping[str, object]) -> bytes:
    return json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")


def _registry_path_hash(registry_path: Path) -> str:
    """Bind an anti-rollback state file to one absolute registry path."""

    try:
        resolved = registry_path.resolve(strict=False)
    except OSError:
        resolved = registry_path.absolute()
    normalized = os.path.normcase(str(resolved))
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()


def _valid_sha256_hex(value: object) -> bool:
    return (
        isinstance(value, str)
        and len(value) == hashlib.sha256().digest_size * 2
        and all(character in string.hexdigits for character in value)
    )


def _encode_registry_state(payload: Mapping[str, object]) -> bytes:
    cleartext = (
        json.dumps(
            payload,
            ensure_ascii=False,
            indent=2,
            allow_nan=False,
        )
        + "\n"
    ).encode("utf-8")
    if os.name == "nt":
        return _STATE_DPAPI_PREFIX + _dpapi_protect(cleartext)
    return _STATE_RAW_PREFIX + cleartext


def _load_registry_state(
    state_path: Path,
    key: bytes,
    registry_path: Path,
) -> tuple[int, str]:
    """Load and authenticate the latest accepted registry generation."""

    try:
        encoded = state_path.read_bytes()
    except FileNotFoundError as exc:
        raise ValueError(
            "Local extension anti-rollback state is missing"
        ) from exc
    except OSError as exc:
        raise ValueError(
            f"Could not read local extension anti-rollback state: {exc}"
        ) from exc

    try:
        if os.name == "nt":
            if not encoded.startswith(_STATE_DPAPI_PREFIX):
                raise ValueError(
                    "Local extension anti-rollback state is not protected "
                    "with Windows DPAPI"
                )
            cleartext = _dpapi_unprotect(
                encoded[len(_STATE_DPAPI_PREFIX) :]
            )
        else:
            if stat.S_IMODE(state_path.stat().st_mode) & 0o077:
                raise ValueError(
                    "Local extension anti-rollback state permissions "
                    "must be 0600"
                )
            if not encoded.startswith(_STATE_RAW_PREFIX):
                raise ValueError(
                    "Local extension anti-rollback state has an invalid format"
                )
            cleartext = encoded[len(_STATE_RAW_PREFIX) :]
    except OSError as exc:
        raise ValueError(
            f"Could not unlock local extension anti-rollback state: {exc}"
        ) from exc

    try:
        payload = json.loads(cleartext.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError(
            f"Could not parse local extension anti-rollback state: {exc}"
        ) from exc
    if not isinstance(payload, dict):
        raise ValueError("Local extension anti-rollback state is invalid")
    expected_keys = {
        "document_type",
        "schema_version",
        "registry_path_hash",
        "generation",
        "registry_digest",
        _AUTHENTICATION_FIELD,
    }
    if set(payload) != expected_keys:
        raise ValueError("Local extension anti-rollback state is invalid")
    authentication = payload.get(_AUTHENTICATION_FIELD)
    if (
        not isinstance(authentication, dict)
        or set(authentication) != {"algorithm", "tag"}
        or authentication.get("algorithm") != _AUTHENTICATION_ALGORITHM
        or not _valid_sha256_hex(authentication.get("tag"))
    ):
        raise ValueError(
            "Local extension anti-rollback state authentication is invalid"
        )

    unsigned_payload = dict(payload)
    del unsigned_payload[_AUTHENTICATION_FIELD]
    supplied_tag = authentication["tag"]
    expected_tag = hmac.new(
        key,
        _canonical_registry_payload(unsigned_payload),
        hashlib.sha256,
    ).hexdigest()
    if not hmac.compare_digest(supplied_tag.casefold(), expected_tag):
        raise ValueError(
            "Local extension anti-rollback state authentication failed"
        )
    if (
        unsigned_payload.get("document_type") != _STATE_DOCUMENT_TYPE
        or unsigned_payload.get("schema_version") != _STATE_SCHEMA_VERSION
        or unsigned_payload.get("registry_path_hash")
        != _registry_path_hash(registry_path)
    ):
        raise ValueError(
            "Local extension anti-rollback state does not belong to this registry"
        )
    generation = unsigned_payload.get("generation")
    registry_digest = unsigned_payload.get("registry_digest")
    if (
        type(generation) is not int
        or generation < 1
        or not _valid_sha256_hex(registry_digest)
    ):
        raise ValueError("Local extension anti-rollback state is invalid")
    return generation, registry_digest.casefold()


def _save_registry_state(
    state_path: Path,
    key: bytes,
    registry_path: Path,
    *,
    generation: int,
    registry_digest: str,
) -> None:
    unsigned_payload: dict[str, object] = {
        "document_type": _STATE_DOCUMENT_TYPE,
        "schema_version": _STATE_SCHEMA_VERSION,
        "registry_path_hash": _registry_path_hash(registry_path),
        "generation": generation,
        "registry_digest": registry_digest,
    }
    tag = hmac.new(
        key,
        _canonical_registry_payload(unsigned_payload),
        hashlib.sha256,
    ).hexdigest()
    payload = dict(unsigned_payload)
    payload[_AUTHENTICATION_FIELD] = {
        "algorithm": _AUTHENTICATION_ALGORITHM,
        "tag": tag,
    }
    _atomic_write_bytes(state_path, _encode_registry_state(payload))


def _parameter_to_dict(
    parameter: LocalExtensionParameter,
) -> dict[str, object]:
    return {
        "name": parameter.name,
        "value_type": parameter.value_type,
        "unit": parameter.unit,
        "minimum": parameter.minimum,
        "maximum": parameter.maximum,
        "choices": list(parameter.choices),
        "mapping": [list(item) for item in parameter.mapping],
        "note_ko": parameter.note_ko,
    }


def _definition_to_dict(
    definition: LocalExtensionDefinition,
) -> dict[str, object]:
    return {
        "extension_id": definition.extension_id,
        "source_profile_id": definition.source_profile_id,
        "category": definition.category.value,
        "identity": {
            "raw": definition.identity_raw,
            "manufacturer": definition.identity_manufacturer,
            "model": definition.identity_model,
            "serial": definition.identity_serial,
            "firmware": definition.identity_firmware,
            "options": definition.identity_options,
            "options_state": definition.identity_options_state,
        },
        "source": {
            "manual_id": definition.manual_id,
            "manual_title": definition.manual_title,
            "manual_url": definition.manual_url,
            "manual_page": definition.manual_page,
            "command_id": definition.source_command_id,
            "command_pattern": definition.source_command_pattern,
        },
        "capability": {
            "capability_id": definition.capability_id,
            "label_ko": definition.label_ko,
            "group": definition.group,
            "risk_level": definition.risk_level,
            "operations": [
                {
                    "name": operation.name,
                    "scpi": operation.scpi,
                    "response_type": operation.response_type,
                    "binary": operation.binary,
                }
                for operation in definition.operations
            ],
            "parameters": [
                _parameter_to_dict(parameter)
                for parameter in definition.parameters
            ],
            "probe_arguments": {
                operation_name: dict(arguments)
                for operation_name, arguments in definition.probe_arguments
            },
            "note_ko": definition.note_ko,
        },
    }


def _required_string(value: object, name: str) -> str:
    if not isinstance(value, str):
        raise ValueError(f"{name} must be a string")
    return value


def _required_object(value: object, name: str) -> dict[str, object]:
    if not isinstance(value, dict):
        raise ValueError(f"{name} must be an object")
    return value


def _number_or_none(value: object, name: str) -> float | int | None:
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{name} must be numeric or null")
    return value


def _definition_from_dict(value: object) -> LocalExtensionDefinition:
    item = _required_object(value, "definition")
    identity = _required_object(item.get("identity"), "definition.identity")
    source = _required_object(item.get("source"), "definition.source")
    capability = _required_object(
        item.get("capability"),
        "definition.capability",
    )
    raw_operations = capability.get("operations")
    raw_parameters = capability.get("parameters")
    raw_arguments = capability.get("probe_arguments")
    if not isinstance(raw_operations, list):
        raise ValueError("definition.capability.operations must be a list")
    if not isinstance(raw_parameters, list):
        raise ValueError("definition.capability.parameters must be a list")
    if not isinstance(raw_arguments, dict):
        raise ValueError(
            "definition.capability.probe_arguments must be an object"
        )
    operations: list[LocalExtensionOperation] = []
    for index, raw_operation in enumerate(raw_operations):
        operation = _required_object(
            raw_operation,
            f"definition.capability.operations[{index}]",
        )
        binary = operation.get("binary")
        if not isinstance(binary, bool):
            raise ValueError("Local extension binary flag must be boolean")
        operations.append(
            LocalExtensionOperation(
                name=_required_string(operation.get("name"), "operation.name"),
                scpi=_required_string(operation.get("scpi"), "operation.scpi"),
                response_type=_required_string(
                    operation.get("response_type"),
                    "operation.response_type",
                ),
                binary=binary,
            )
        )
    parameters: list[LocalExtensionParameter] = []
    for index, raw_parameter in enumerate(raw_parameters):
        parameter = _required_object(
            raw_parameter,
            f"definition.capability.parameters[{index}]",
        )
        choices = parameter.get("choices")
        mapping = parameter.get("mapping")
        if not isinstance(choices, list) or not all(
            isinstance(choice, str) for choice in choices
        ):
            raise ValueError("Local extension choices must be strings")
        if not isinstance(mapping, list) or not all(
            isinstance(pair, list)
            and len(pair) == 2
            and all(isinstance(part, str) for part in pair)
            for pair in mapping
        ):
            raise ValueError("Local extension mapping must contain string pairs")
        parameters.append(
            LocalExtensionParameter(
                name=_required_string(parameter.get("name"), "parameter.name"),
                value_type=_required_string(
                    parameter.get("value_type"),
                    "parameter.value_type",
                ),
                unit=_required_string(parameter.get("unit"), "parameter.unit"),
                minimum=_number_or_none(
                    parameter.get("minimum"),
                    "parameter.minimum",
                ),
                maximum=_number_or_none(
                    parameter.get("maximum"),
                    "parameter.maximum",
                ),
                choices=tuple(choices),
                mapping=tuple((pair[0], pair[1]) for pair in mapping),
                note_ko=_required_string(
                    parameter.get("note_ko"),
                    "parameter.note_ko",
                ),
            )
        )
    probe_arguments: list[
        tuple[str, tuple[tuple[str, str], ...]]
    ] = []
    for operation_name, raw_values in raw_arguments.items():
        if not isinstance(operation_name, str) or not isinstance(
            raw_values,
            dict,
        ):
            raise ValueError("Probe arguments must be nested string objects")
        probe_arguments.append(
            (
                operation_name,
                tuple(
                    (
                        _required_string(name, "probe argument name"),
                        _required_string(argument, "probe argument value"),
                    )
                    for name, argument in raw_values.items()
                ),
            )
        )
    manual_page = source.get("manual_page")
    if isinstance(manual_page, bool) or not isinstance(manual_page, int):
        raise ValueError("definition.source.manual_page must be an integer")
    return LocalExtensionDefinition(
        extension_id=_required_string(
            item.get("extension_id"),
            "definition.extension_id",
        ),
        source_profile_id=_required_string(
            item.get("source_profile_id"),
            "definition.source_profile_id",
        ),
        category=DeviceCategory(
            _required_string(item.get("category"), "definition.category")
        ),
        identity_raw=_required_string(identity.get("raw"), "identity.raw"),
        identity_manufacturer=_required_string(
            identity.get("manufacturer"),
            "identity.manufacturer",
        ),
        identity_model=_required_string(
            identity.get("model"),
            "identity.model",
        ),
        identity_serial=_required_string(
            identity.get("serial"),
            "identity.serial",
        ),
        identity_firmware=_required_string(
            identity.get("firmware"),
            "identity.firmware",
        ),
        identity_options=_required_string(
            identity.get("options", ""),
            "identity.options",
        ),
        manual_id=_required_string(
            source.get("manual_id"),
            "source.manual_id",
        ),
        manual_title=_required_string(
            source.get("manual_title"),
            "source.manual_title",
        ),
        manual_url=_required_string(
            source.get("manual_url"),
            "source.manual_url",
        ),
        manual_page=manual_page,
        source_command_id=_required_string(
            source.get("command_id"),
            "source.command_id",
        ),
        source_command_pattern=_required_string(
            source.get("command_pattern"),
            "source.command_pattern",
        ),
        capability_id=_required_string(
            capability.get("capability_id"),
            "capability.capability_id",
        ),
        label_ko=_required_string(
            capability.get("label_ko"),
            "capability.label_ko",
        ),
        group=_required_string(
            capability.get("group"),
            "capability.group",
        ),
        risk_level=_required_string(
            capability.get("risk_level"),
            "capability.risk_level",
        ),
        operations=tuple(operations),
        parameters=tuple(parameters),
        probe_arguments=tuple(probe_arguments),
        note_ko=_required_string(
            capability.get("note_ko"),
            "capability.note_ko",
        ),
        identity_options_state=_required_string(
            identity.get("options_state"),
            "identity.options_state",
        ),
    )


def local_extension_registry_to_dict(
    registry: LocalExtensionRegistry,
) -> dict[str, object]:
    return {
        "document_type": _DOCUMENT_TYPE,
        "schema_version": registry.schema_version,
        "records": [
            {
                "definition": _definition_to_dict(record.definition),
                "validation_result": validation_result_to_dict(
                    record.validation_result
                ),
            }
            for record in registry.records
        ],
    }


def local_extension_registry_from_dict(
    value: object,
) -> LocalExtensionRegistry:
    item = _required_object(value, "local extension registry")
    if item.get("document_type") != _DOCUMENT_TYPE:
        raise ValueError("Not a local extension registry")
    schema_version = item.get("schema_version")
    if schema_version != LOCAL_EXTENSION_SCHEMA_VERSION:
        raise ValueError(
            f"Unsupported local extension schema version: {schema_version}"
        )
    raw_records = item.get("records")
    if not isinstance(raw_records, list):
        raise ValueError("Local extension records must be a list")
    records: list[PromotedLocalExtension] = []
    for index, raw_record in enumerate(raw_records):
        record = _required_object(raw_record, f"records[{index}]")
        records.append(
            PromotedLocalExtension(
                definition=_definition_from_dict(record.get("definition")),
                validation_result=validation_result_from_dict(
                    record.get("validation_result")
                ),
            )
        )
    return LocalExtensionRegistry(records=tuple(records))


def save_local_extension_registry(
    registry: LocalExtensionRegistry,
    path: str | Path | None = None,
) -> Path:
    destination = Path(path) if path is not None else default_local_extension_path()
    with _registry_file_lock(destination):
        return _save_local_extension_registry_unlocked(
            registry,
            destination,
        )


def _save_local_extension_registry_unlocked(
    registry: LocalExtensionRegistry,
    destination: Path,
) -> Path:
    state_path = _local_extension_state_path(destination)
    key_path = _local_extension_key_path(destination)
    registry_exists = destination.exists()
    state_exists = state_path.exists()
    if registry_exists != state_exists:
        raise ValueError(
            "Local extension registry and anti-rollback state must both "
            "exist or both be absent"
        )
    if registry_exists:
        # Refuse to advance from a replayed registry or state file.  The
        # existing pair must be the latest authenticated generation first.
        current_registry = _load_local_extension_registry_unlocked(
            destination,
            missing_ok=False,
        )
        if (
            registry.base_generation
            != current_registry.base_generation
            or not hmac.compare_digest(
                registry.base_digest,
                current_registry.base_digest,
            )
        ):
            raise ValueError(
                "The local extension registry changed in another program "
                "window. Reload it before saving so a revoked function "
                "cannot be restored from a stale snapshot."
            )
    elif registry.base_generation != 0 or registry.base_digest:
        raise ValueError(
            "The local extension registry changed or was removed after it "
            "was loaded. Reload it before saving."
        )
    key_record = _load_or_create_registry_key(
        key_path,
        destination,
    )
    if not registry_exists and key_record.generation != 0:
        raise ValueError(
            "Local extension registry is missing while its latest key "
            "generation remains"
        )
    key = key_record.secret
    if state_exists:
        current_generation, current_digest = _load_registry_state(
            state_path,
            key,
            destination,
        )
        if (
            key_record.generation != current_generation
            or not hmac.compare_digest(
                key_record.registry_digest,
                current_digest,
            )
        ):
            raise ValueError(
                "Local extension registry rollback or replay detected"
            )
    else:
        current_generation = 0
    generation = current_generation + 1
    payload = local_extension_registry_to_dict(registry)
    payload[_REGISTRY_GENERATION_FIELD] = generation
    registry_digest = hashlib.sha256(
        _canonical_registry_payload(payload)
    ).hexdigest()
    tag = hmac.new(
        key,
        _canonical_registry_payload(payload),
        hashlib.sha256,
    ).hexdigest()
    authenticated_payload = dict(payload)
    authenticated_payload[_AUTHENTICATION_FIELD] = {
        "algorithm": _AUTHENTICATION_ALGORITHM,
        "tag": tag,
    }
    content = (
        json.dumps(
            authenticated_payload,
            ensure_ascii=False,
            indent=2,
            allow_nan=False,
        )
        + "\n"
    ).encode("utf-8")
    _atomic_write_bytes(destination, content)
    # The two files cannot be replaced transactionally on every supported
    # filesystem.  Writing the registry first makes an interrupted update fail
    # closed because the preceding state digest will no longer match.
    _save_registry_state(
        state_path,
        key,
        destination,
        generation=generation,
        registry_digest=registry_digest,
    )
    # The DPAPI-protected key record is the final trust anchor.  Updating it
    # last makes replay of an older registry + state pair detectable.
    _write_registry_key_record(
        key_path,
        destination,
        _RegistryKeyRecord(
            secret=key,
            generation=generation,
            registry_digest=registry_digest,
        ),
    )
    return destination


def load_local_extension_registry(
    path: str | Path | None = None,
    *,
    missing_ok: bool = True,
) -> LocalExtensionRegistry:
    source = Path(path) if path is not None else default_local_extension_path()
    with _registry_file_lock(source):
        return _load_local_extension_registry_unlocked(
            source,
            missing_ok=missing_ok,
        )


def _load_local_extension_registry_unlocked(
    source: Path,
    *,
    missing_ok: bool,
) -> LocalExtensionRegistry:
    state_path = _local_extension_state_path(source)
    key_path = _local_extension_key_path(source)
    source_exists = source.exists()
    state_exists = state_path.exists()
    if not source_exists:
        if state_exists:
            raise ValueError(
                "Local extension registry is missing while anti-rollback "
                "state remains"
            )
        if key_path.exists():
            key_record = _load_registry_key_record(key_path, source)
            if key_record.generation != 0:
                raise ValueError(
                    "Local extension registry is missing while its latest "
                    "key generation remains"
                )
        if missing_ok:
            return LocalExtensionRegistry()
    elif not state_exists:
        raise ValueError(
            "Local extension anti-rollback state is missing"
        )
    try:
        payload = json.loads(source.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"Could not load local extensions: {exc}") from exc
    if not isinstance(payload, dict):
        raise ValueError("Local extension registry authentication is missing")
    authentication = payload.get(_AUTHENTICATION_FIELD)
    if (
        not isinstance(authentication, dict)
        or set(authentication) != {"algorithm", "tag"}
        or authentication.get("algorithm") != _AUTHENTICATION_ALGORITHM
        or not _valid_sha256_hex(authentication.get("tag"))
    ):
        raise ValueError("Local extension registry authentication is missing")
    supplied_tag = authentication["tag"]

    unsigned_payload = dict(payload)
    del unsigned_payload[_AUTHENTICATION_FIELD]
    key_record = _load_registry_key_record(key_path, source)
    key = key_record.secret
    expected_tag = hmac.new(
        key,
        _canonical_registry_payload(unsigned_payload),
        hashlib.sha256,
    ).hexdigest()
    if not hmac.compare_digest(supplied_tag.casefold(), expected_tag):
        raise ValueError("Local extension registry authentication failed")
    generation = unsigned_payload.get(_REGISTRY_GENERATION_FIELD)
    if type(generation) is not int or generation < 1:
        raise ValueError(
            "Local extension registry generation is missing or invalid"
        )
    state_generation, state_digest = _load_registry_state(
        state_path,
        key,
        source,
    )
    registry_digest = hashlib.sha256(
        _canonical_registry_payload(unsigned_payload)
    ).hexdigest()
    if (
        generation != state_generation
        or generation != key_record.generation
        or not hmac.compare_digest(registry_digest, state_digest)
        or not hmac.compare_digest(
            registry_digest,
            key_record.registry_digest,
        )
    ):
        raise ValueError(
            "Local extension registry rollback or replay detected"
        )
    registry_payload = dict(unsigned_payload)
    del registry_payload[_REGISTRY_GENERATION_FIELD]
    return replace(
        local_extension_registry_from_dict(registry_payload),
        base_generation=generation,
        base_digest=registry_digest,
    )
