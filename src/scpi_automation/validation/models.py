from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field, replace
from enum import Enum
from typing import Mapping

from scpi_automation.identity import InstrumentIdentity, InstrumentProfile


VALIDATION_SCHEMA_VERSION = 1


class OperationStatus(str, Enum):
    """Current result of validating one candidate-pack operation."""

    PENDING = "pending"
    PASS = "pass"
    FAIL = "fail"
    SKIPPED = "skipped"
    UNSAFE = "unsafe"
    MANUAL = "manual"

    @property
    def is_terminal(self) -> bool:
        return self is not OperationStatus.PENDING


class OperationKind(str, Enum):
    QUERY = "query"
    SET = "set"
    EXECUTE = "execute"

    @property
    def is_write(self) -> bool:
        return self in {OperationKind.SET, OperationKind.EXECUTE}


class FailureKind(str, Enum):
    NONE = ""
    TIMEOUT = "timeout"
    SESSION_ERROR = "session_error"
    INSTRUMENT_ERROR = "instrument_error"
    INVALID_RESPONSE = "invalid_response"
    READBACK_MISMATCH = "readback_mismatch"
    RESTORE_FAILED = "restore_failed"


@dataclass(frozen=True, slots=True)
class ErrorQueueEntry:
    phase: str
    response: str
    code: int | None

    @property
    def is_error(self) -> bool:
        return self.code is None or self.code != 0


@dataclass(frozen=True, slots=True)
class OperationValidation:
    """Persistable evidence for one operation.

    ``operation_id`` is stable inside a profile and uses
    ``<capability_id>::<operation name>``.  A PASS result is the only state
    that is exposed as a compatible operation.
    """

    operation_id: str
    capability_id: str
    operation_name: str
    kind: OperationKind
    command_template: str
    response_type: str
    binary: bool
    risk_level: str
    status: OperationStatus = OperationStatus.PENDING
    validation_mode: str = ""
    attempts: int = 0
    sent_commands: tuple[str, ...] = ()
    response: str = ""
    original_response: str = ""
    verification_response: str = ""
    restore_attempted: bool = False
    restored: bool = False
    error_queue: tuple[ErrorQueueEntry, ...] = ()
    failure_kind: FailureKind = FailureKind.NONE
    message: str = ""

    def __post_init__(self) -> None:
        if not self.capability_id.strip() or not self.operation_name.strip():
            raise ValueError("Operation capability/name must not be empty")
        expected_id = f"{self.capability_id}::{self.operation_name}"
        if self.operation_id != expected_id:
            raise ValueError(
                f"Operation ID must be {expected_id!r}, got {self.operation_id!r}"
            )
        if self.attempts < 0:
            raise ValueError("Operation attempts must not be negative")
        if self.restored and not self.restore_attempted:
            raise ValueError("restored requires restore_attempted")


@dataclass(frozen=True, slots=True)
class ManualProbeEvidence:
    """Audit evidence for a raw query or failed structured extension attempt."""

    candidate_key: str
    manual_id: str
    command_id: str
    command_pattern: str
    query_command: str
    manual_page: int
    status: str
    response: str = ""
    message: str = ""
    attempts: int = 1

    def __post_init__(self) -> None:
        if not self.candidate_key.strip():
            raise ValueError("Manual probe candidate_key must not be empty")
        if not self.manual_id.strip() or not self.command_id.strip():
            raise ValueError("Manual probe source IDs must not be empty")
        if self.status not in {"response", "fail"}:
            raise ValueError("Manual probe status must be response or fail")
        if self.manual_page < 1 or self.attempts < 1:
            raise ValueError("Manual probe page/attempts must be positive")
        if self.status == "response" and not self.response.strip():
            raise ValueError("Manual probe response evidence must not be empty")


@dataclass(frozen=True, slots=True)
class ValidationProgress:
    schema_version: int
    source_profile_id: str
    resource: str
    catalog_fingerprint: str
    operations: tuple[OperationValidation, ...]
    identity_raw: str = ""
    identity_manufacturer: str = ""
    identity_model: str = ""
    identity_serial: str = ""
    identity_firmware: str = ""
    manual_probes: tuple[ManualProbeEvidence, ...] = ()
    run_count: int = 0
    stopped: bool = False
    stop_reason: str = ""

    def __post_init__(self) -> None:
        if self.schema_version != VALIDATION_SCHEMA_VERSION:
            raise ValueError(
                f"Unsupported validation schema: {self.schema_version}"
            )
        if not self.source_profile_id.strip():
            raise ValueError("source_profile_id must not be empty")
        operation_ids = [item.operation_id for item in self.operations]
        if len(operation_ids) != len(set(operation_ids)):
            raise ValueError("Validation progress contains duplicate operation IDs")
        candidate_keys = [item.candidate_key for item in self.manual_probes]
        if len(candidate_keys) != len(set(candidate_keys)):
            raise ValueError("Validation progress contains duplicate manual probes")

    @property
    def is_scan_complete(self) -> bool:
        return all(item.status.is_terminal for item in self.operations)

    @property
    def is_fully_resolved(self) -> bool:
        """Whether every operation has an explicit pass/fail decision."""

        return all(
            item.status in {OperationStatus.PASS, OperationStatus.FAIL}
            for item in self.operations
        )

    def operation(self, operation_id: str) -> OperationValidation:
        for item in self.operations:
            if item.operation_id == operation_id:
                return item
        raise KeyError(f"Unknown validation operation: {operation_id}")

    def replace_operation(
        self,
        updated: OperationValidation,
    ) -> ValidationProgress:
        found = False
        operations: list[OperationValidation] = []
        for item in self.operations:
            if item.operation_id == updated.operation_id:
                operations.append(updated)
                found = True
            else:
                operations.append(item)
        if not found:
            raise KeyError(
                f"Unknown validation operation: {updated.operation_id}"
            )
        return replace(self, operations=tuple(operations))


@dataclass(frozen=True, slots=True)
class ValidationResult:
    """Final compatibility projection built only from PASS operations."""

    schema_version: int
    source_profile_id: str
    resource: str
    catalog_fingerprint: str
    compatible_capability_ids: tuple[str, ...]
    fully_compatible_capability_ids: tuple[str, ...]
    compatible_operation_ids: tuple[str, ...]
    incompatible_operation_ids: tuple[str, ...]
    unresolved_operation_ids: tuple[str, ...]
    status_counts: tuple[tuple[str, int], ...]
    scan_complete: bool
    fully_resolved: bool
    stopped: bool
    operations: tuple[OperationValidation, ...]
    identity_raw: str = ""
    identity_manufacturer: str = ""
    identity_model: str = ""
    identity_serial: str = ""
    identity_firmware: str = ""
    manual_probes: tuple[ManualProbeEvidence, ...] = ()


@dataclass(frozen=True, slots=True)
class ValidationPolicy:
    """Inputs and safety policy for one deterministic validation run."""

    timeout_ms: int = 2000
    error_query: str | None = "SYST:ERR?"
    max_error_entries: int = 8
    operation_arguments: Mapping[str, Mapping[str, object]] = field(
        default_factory=dict
    )
    approved_hazardous_operation_ids: frozenset[str] = frozenset()
    skipped_operation_ids: frozenset[str] = frozenset()
    numeric_relative_tolerance: float = 1e-6
    numeric_absolute_tolerance: float = 1e-12

    def __post_init__(self) -> None:
        if not 1 <= self.timeout_ms <= 600_000:
            raise ValueError("timeout_ms must be between 1 and 600000")
        if not 1 <= self.max_error_entries <= 100:
            raise ValueError("max_error_entries must be between 1 and 100")
        if self.error_query is not None and not self.error_query.strip():
            raise ValueError("error_query must be None or a non-empty command")
        if self.error_query is not None and any(
            token in self.error_query for token in ("\x00", "\r", "\n", ";")
        ):
            raise ValueError("error_query contains an unsafe SCPI separator")
        if self.numeric_relative_tolerance < 0:
            raise ValueError("numeric_relative_tolerance must not be negative")
        if self.numeric_absolute_tolerance < 0:
            raise ValueError("numeric_absolute_tolerance must not be negative")


def operation_id(capability_id: str, operation_name: str) -> str:
    if not capability_id.strip() or not operation_name.strip():
        raise ValueError("Capability and operation names must not be empty")
    return f"{capability_id}::{operation_name}"


def _operation_kind(name: str) -> OperationKind:
    try:
        return OperationKind(name)
    except ValueError:
        # Unknown catalog verbs are writes until reviewed.  They are therefore
        # represented as EXECUTE and will never be auto-run by the engine.
        return OperationKind.EXECUTE


def profile_fingerprint(profile: InstrumentProfile) -> str:
    """Hash the validation-relevant catalog surface for safe resume checks."""

    payload = {
        "profile_id": profile.profile_id,
        "capabilities": [
            {
                "capability_id": capability.capability_id,
                "risk_level": capability.risk_level,
                "parameters": list(capability.parameters),
                "operations": [
                    {
                        "name": operation.name,
                        "scpi": operation.scpi,
                        "response_type": operation.response_type,
                        "binary": operation.binary,
                    }
                    for operation in capability.operations
                ],
            }
            for capability in profile.capabilities
        ],
    }
    encoded = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def create_validation_progress(
    profile: InstrumentProfile,
    resource: str,
    identity: InstrumentIdentity | None = None,
) -> ValidationProgress:
    operations = tuple(
        OperationValidation(
            operation_id=operation_id(
                capability.capability_id,
                operation.name,
            ),
            capability_id=capability.capability_id,
            operation_name=operation.name,
            kind=_operation_kind(operation.name),
            command_template=operation.scpi,
            response_type=operation.response_type,
            binary=operation.binary,
            risk_level=capability.risk_level.lower(),
        )
        for capability in profile.capabilities
        for operation in capability.operations
    )
    return ValidationProgress(
        schema_version=VALIDATION_SCHEMA_VERSION,
        source_profile_id=profile.profile_id,
        resource=resource.strip(),
        catalog_fingerprint=profile_fingerprint(profile),
        operations=operations,
        identity_raw=identity.raw if identity is not None else "",
        identity_manufacturer=(
            identity.manufacturer if identity is not None else ""
        ),
        identity_model=identity.model if identity is not None else "",
        identity_serial=identity.serial if identity is not None else "",
        identity_firmware=identity.firmware if identity is not None else "",
    )


def ensure_progress_matches_profile(
    progress: ValidationProgress,
    profile: InstrumentProfile,
) -> None:
    if progress.source_profile_id != profile.profile_id:
        raise ValueError(
            "Validation progress belongs to a different candidate command pack"
        )
    if progress.catalog_fingerprint != profile_fingerprint(profile):
        raise ValueError(
            "Candidate command pack changed after validation started; "
            "start a new validation run"
        )
    expected_ids = tuple(
        operation_id(capability.capability_id, operation.name)
        for capability in profile.capabilities
        for operation in capability.operations
    )
    actual_ids = tuple(item.operation_id for item in progress.operations)
    if actual_ids != expected_ids:
        raise ValueError(
            "Validation progress operation list does not match the profile"
        )


def reset_operations(
    progress: ValidationProgress,
    operation_ids: tuple[str, ...] | list[str] | set[str],
) -> ValidationProgress:
    """Reset selected terminal operations so a changed policy can retry them."""

    requested = set(operation_ids)
    known = {item.operation_id for item in progress.operations}
    unknown = requested - known
    if unknown:
        raise KeyError(
            "Unknown validation operation(s): " + ", ".join(sorted(unknown))
        )
    operations = tuple(
        replace(
            item,
            status=OperationStatus.PENDING,
            validation_mode="",
            sent_commands=(),
            response="",
            original_response="",
            verification_response="",
            restore_attempted=False,
            restored=False,
            error_queue=(),
            failure_kind=FailureKind.NONE,
            message="",
        )
        if item.operation_id in requested
        else item
        for item in progress.operations
    )
    return replace(
        progress,
        operations=operations,
        stopped=False,
        stop_reason="",
    )


def apply_manual_result(
    progress: ValidationProgress,
    operation_id_value: str,
    *,
    passed: bool,
    note: str,
    validation_mode: str = "manual_operator",
) -> ValidationProgress:
    """Record an operator-observed result for a MANUAL operation.

    An UNSAFE operation cannot be bypassed here.  Reversible hazardous writes
    must be reset and rerun with their exact operation ID in the approval set.
    """

    note = note.strip()
    if not note:
        raise ValueError("A manual validation result requires an evidence note")
    if validation_mode not in {
        "manual_operator",
        "manual_operator_hazardous",
    }:
        raise ValueError("Unsupported manual validation mode")
    current = progress.operation(operation_id_value)
    if current.status is not OperationStatus.MANUAL:
        raise ValueError("Only MANUAL operations accept a manual result")
    updated = replace(
        current,
        status=OperationStatus.PASS if passed else OperationStatus.FAIL,
        validation_mode=validation_mode,
        attempts=current.attempts + 1,
        failure_kind=FailureKind.NONE if passed else FailureKind.SESSION_ERROR,
        message=note,
    )
    return progress.replace_operation(updated)


def build_validation_result(
    progress: ValidationProgress,
) -> ValidationResult:
    passed = tuple(
        item.operation_id
        for item in progress.operations
        if item.status is OperationStatus.PASS
    )
    failed = tuple(
        item.operation_id
        for item in progress.operations
        if item.status is OperationStatus.FAIL
    )
    unresolved = tuple(
        item.operation_id
        for item in progress.operations
        if item.status
        in {
            OperationStatus.PENDING,
            OperationStatus.SKIPPED,
            OperationStatus.UNSAFE,
            OperationStatus.MANUAL,
        }
    )

    capability_statuses: dict[str, list[OperationStatus]] = {}
    for item in progress.operations:
        capability_statuses.setdefault(item.capability_id, []).append(
            item.status
        )
    compatible_capabilities = tuple(
        capability_id
        for capability_id, statuses in capability_statuses.items()
        if OperationStatus.PASS in statuses
    )
    fully_compatible_capabilities = tuple(
        capability_id
        for capability_id, statuses in capability_statuses.items()
        if statuses and all(status is OperationStatus.PASS for status in statuses)
    )
    counts = tuple(
        (status.value, sum(item.status is status for item in progress.operations))
        for status in OperationStatus
    )
    return ValidationResult(
        schema_version=progress.schema_version,
        source_profile_id=progress.source_profile_id,
        resource=progress.resource,
        catalog_fingerprint=progress.catalog_fingerprint,
        compatible_capability_ids=compatible_capabilities,
        fully_compatible_capability_ids=fully_compatible_capabilities,
        compatible_operation_ids=passed,
        incompatible_operation_ids=failed,
        unresolved_operation_ids=unresolved,
        status_counts=counts,
        scan_complete=progress.is_scan_complete,
        fully_resolved=progress.is_fully_resolved,
        stopped=progress.stopped,
        operations=progress.operations,
        identity_raw=progress.identity_raw,
        identity_manufacturer=progress.identity_manufacturer,
        identity_model=progress.identity_model,
        identity_serial=progress.identity_serial,
        identity_firmware=progress.identity_firmware,
        manual_probes=progress.manual_probes,
    )
