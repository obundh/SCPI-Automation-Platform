from __future__ import annotations

import json
from pathlib import Path
from typing import Callable, Mapping

from .models import (
    ErrorQueueEntry,
    FailureKind,
    ManualProbeEvidence,
    OperationKind,
    OperationStatus,
    OperationValidation,
    VALIDATION_SCHEMA_VERSION,
    ValidationProgress,
    ValidationResult,
    build_validation_result,
)


_PROGRESS_DOCUMENT_TYPE = "scpi_operation_validation_progress"
_RESULT_DOCUMENT_TYPE = "scpi_operation_validation_result"


def _operation_to_dict(operation: OperationValidation) -> dict[str, object]:
    return {
        "operation_id": operation.operation_id,
        "capability_id": operation.capability_id,
        "operation_name": operation.operation_name,
        "kind": operation.kind.value,
        "command_template": operation.command_template,
        "response_type": operation.response_type,
        "binary": operation.binary,
        "risk_level": operation.risk_level,
        "status": operation.status.value,
        "validation_mode": operation.validation_mode,
        "attempts": operation.attempts,
        "sent_commands": list(operation.sent_commands),
        "response": operation.response,
        "original_response": operation.original_response,
        "verification_response": operation.verification_response,
        "restore_attempted": operation.restore_attempted,
        "restored": operation.restored,
        "error_queue": [
            {
                "phase": entry.phase,
                "response": entry.response,
                "code": entry.code,
            }
            for entry in operation.error_queue
        ],
        "failure_kind": operation.failure_kind.value,
        "message": operation.message,
    }


def _manual_probe_to_dict(
    evidence: ManualProbeEvidence,
) -> dict[str, object]:
    return {
        "candidate_key": evidence.candidate_key,
        "manual_id": evidence.manual_id,
        "command_id": evidence.command_id,
        "command_pattern": evidence.command_pattern,
        "query_command": evidence.query_command,
        "manual_page": evidence.manual_page,
        "status": evidence.status,
        "response": evidence.response,
        "message": evidence.message,
        "attempts": evidence.attempts,
    }


def _required_string(value: object, field_name: str) -> str:
    if not isinstance(value, str):
        raise ValueError(f"{field_name} must be a string")
    return value


def _string_tuple(value: object, field_name: str) -> tuple[str, ...]:
    if not isinstance(value, list) or not all(
        isinstance(item, str) for item in value
    ):
        raise ValueError(f"{field_name} must be a list of strings")
    return tuple(value)


def _identity_to_dict(
    *,
    raw: str,
    manufacturer: str,
    model: str,
    serial: str,
    firmware: str,
) -> dict[str, str]:
    return {
        "raw": raw,
        "manufacturer": manufacturer,
        "model": model,
        "serial": serial,
        "firmware": firmware,
    }


def _identity_from_dict(value: object) -> dict[str, str]:
    if value is None:
        value = {}
    if not isinstance(value, dict):
        raise ValueError("identity must be an object")
    return {
        field_name: _required_string(
            value.get(field_name, ""),
            f"identity.{field_name}",
        )
        for field_name in (
            "raw",
            "manufacturer",
            "model",
            "serial",
            "firmware",
        )
    }


def _operation_from_dict(value: object) -> OperationValidation:
    if not isinstance(value, dict):
        raise ValueError("Operation result must be an object")
    raw_queue = value.get("error_queue", [])
    if not isinstance(raw_queue, list):
        raise ValueError("error_queue must be a list")
    entries: list[ErrorQueueEntry] = []
    for raw_entry in raw_queue:
        if not isinstance(raw_entry, dict):
            raise ValueError("Error queue entry must be an object")
        code = raw_entry.get("code")
        if code is not None and (
            isinstance(code, bool) or not isinstance(code, int)
        ):
            raise ValueError("Error queue code must be an integer or null")
        entries.append(
            ErrorQueueEntry(
                phase=_required_string(
                    raw_entry.get("phase"),
                    "error_queue.phase",
                ),
                response=_required_string(
                    raw_entry.get("response"),
                    "error_queue.response",
                ),
                code=code,
            )
        )
    attempts = value.get("attempts")
    if isinstance(attempts, bool) or not isinstance(attempts, int) or attempts < 0:
        raise ValueError("attempts must be a non-negative integer")
    binary = value.get("binary")
    restore_attempted = value.get("restore_attempted")
    restored = value.get("restored")
    if not isinstance(binary, bool):
        raise ValueError("binary must be a boolean")
    if not isinstance(restore_attempted, bool) or not isinstance(restored, bool):
        raise ValueError("Restore flags must be booleans")
    try:
        kind = OperationKind(_required_string(value.get("kind"), "kind"))
        status = OperationStatus(
            _required_string(value.get("status"), "status")
        )
        failure_kind = FailureKind(
            _required_string(value.get("failure_kind"), "failure_kind")
        )
    except ValueError as exc:
        raise ValueError(f"Invalid validation enum value: {exc}") from exc
    operation = OperationValidation(
        operation_id=_required_string(
            value.get("operation_id"),
            "operation_id",
        ),
        capability_id=_required_string(
            value.get("capability_id"),
            "capability_id",
        ),
        operation_name=_required_string(
            value.get("operation_name"),
            "operation_name",
        ),
        kind=kind,
        command_template=_required_string(
            value.get("command_template"),
            "command_template",
        ),
        response_type=_required_string(
            value.get("response_type"),
            "response_type",
        ),
        binary=binary,
        risk_level=_required_string(
            value.get("risk_level"),
            "risk_level",
        ),
        status=status,
        validation_mode=_required_string(
            value.get("validation_mode"),
            "validation_mode",
        ),
        attempts=attempts,
        sent_commands=_string_tuple(
            value.get("sent_commands"),
            "sent_commands",
        ),
        response=_required_string(value.get("response"), "response"),
        original_response=_required_string(
            value.get("original_response"),
            "original_response",
        ),
        verification_response=_required_string(
            value.get("verification_response"),
            "verification_response",
        ),
        restore_attempted=restore_attempted,
        restored=restored,
        error_queue=tuple(entries),
        failure_kind=failure_kind,
        message=_required_string(value.get("message"), "message"),
    )
    _validate_loaded_operation_evidence(operation)
    return operation


def _manual_probe_from_dict(value: object) -> ManualProbeEvidence:
    if not isinstance(value, dict):
        raise ValueError("Manual probe evidence must be an object")
    manual_page = value.get("manual_page")
    attempts = value.get("attempts")
    if (
        isinstance(manual_page, bool)
        or not isinstance(manual_page, int)
        or isinstance(attempts, bool)
        or not isinstance(attempts, int)
    ):
        raise ValueError("Manual probe page/attempts must be integers")
    return ManualProbeEvidence(
        candidate_key=_required_string(
            value.get("candidate_key"),
            "manual_probe.candidate_key",
        ),
        manual_id=_required_string(
            value.get("manual_id"),
            "manual_probe.manual_id",
        ),
        command_id=_required_string(
            value.get("command_id"),
            "manual_probe.command_id",
        ),
        command_pattern=_required_string(
            value.get("command_pattern"),
            "manual_probe.command_pattern",
        ),
        query_command=_required_string(
            value.get("query_command"),
            "manual_probe.query_command",
        ),
        manual_page=manual_page,
        status=_required_string(
            value.get("status"),
            "manual_probe.status",
        ),
        response=_required_string(
            value.get("response"),
            "manual_probe.response",
        ),
        message=_required_string(
            value.get("message"),
            "manual_probe.message",
        ),
        attempts=attempts,
    )


def _validate_loaded_operation_evidence(
    operation: OperationValidation,
) -> None:
    """Reject persisted PASS flags that lack the evidence needed to unlock UI."""

    if operation.status is not OperationStatus.PASS:
        return
    if operation.attempts < 1:
        raise ValueError(
            f"{operation.operation_id}: PASS requires at least one attempt"
        )
    if operation.failure_kind is not FailureKind.NONE:
        raise ValueError(
            f"{operation.operation_id}: PASS cannot contain a failure kind"
        )
    if operation.validation_mode == "automatic_query":
        if (
            operation.kind is not OperationKind.QUERY
            or not operation.sent_commands
            or not operation.response.strip()
        ):
            raise ValueError(
                f"{operation.operation_id}: automatic query PASS lacks "
                "command/response evidence"
            )
        return
    if operation.validation_mode == "automatic_reversible_write":
        if (
            operation.kind is not OperationKind.SET
            or not operation.restore_attempted
            or not operation.restored
            or not operation.original_response.strip()
            or not operation.verification_response.strip()
            or len(operation.sent_commands) < 4
        ):
            raise ValueError(
                f"{operation.operation_id}: reversible write PASS lacks "
                "readback/restoration evidence"
            )
        return
    if operation.validation_mode in {
        "manual_operator",
        "manual_operator_hazardous",
    }:
        if not operation.message.strip():
            raise ValueError(
                f"{operation.operation_id}: manual PASS requires an evidence note"
            )
        return
    raise ValueError(
        f"{operation.operation_id}: unsupported PASS validation mode "
        f"{operation.validation_mode!r}"
    )


def validation_progress_to_dict(
    progress: ValidationProgress,
) -> dict[str, object]:
    return {
        "document_type": _PROGRESS_DOCUMENT_TYPE,
        "schema_version": progress.schema_version,
        "source_profile_id": progress.source_profile_id,
        "resource": progress.resource,
        "catalog_fingerprint": progress.catalog_fingerprint,
        "identity": _identity_to_dict(
            raw=progress.identity_raw,
            manufacturer=progress.identity_manufacturer,
            model=progress.identity_model,
            serial=progress.identity_serial,
            firmware=progress.identity_firmware,
        ),
        "run_count": progress.run_count,
        "stopped": progress.stopped,
        "stop_reason": progress.stop_reason,
        "operations": [
            _operation_to_dict(operation)
            for operation in progress.operations
        ],
        "manual_probes": [
            _manual_probe_to_dict(evidence)
            for evidence in progress.manual_probes
        ],
    }


def validation_progress_from_dict(value: object) -> ValidationProgress:
    if not isinstance(value, dict):
        raise ValueError("Validation progress document must be an object")
    if value.get("document_type") != _PROGRESS_DOCUMENT_TYPE:
        raise ValueError("Not a SCPI validation progress document")
    schema_version = value.get("schema_version")
    if schema_version != VALIDATION_SCHEMA_VERSION:
        raise ValueError(
            f"Unsupported validation schema version: {schema_version}"
        )
    run_count = value.get("run_count")
    stopped = value.get("stopped")
    raw_operations = value.get("operations")
    raw_manual_probes = value.get("manual_probes", [])
    if isinstance(run_count, bool) or not isinstance(run_count, int) or run_count < 0:
        raise ValueError("run_count must be a non-negative integer")
    if not isinstance(stopped, bool):
        raise ValueError("stopped must be a boolean")
    if not isinstance(raw_operations, list):
        raise ValueError("operations must be a list")
    if not isinstance(raw_manual_probes, list):
        raise ValueError("manual_probes must be a list")
    identity = _identity_from_dict(value.get("identity"))
    return ValidationProgress(
        schema_version=schema_version,
        source_profile_id=_required_string(
            value.get("source_profile_id"),
            "source_profile_id",
        ),
        resource=_required_string(value.get("resource"), "resource"),
        catalog_fingerprint=_required_string(
            value.get("catalog_fingerprint"),
            "catalog_fingerprint",
        ),
        operations=tuple(
            _operation_from_dict(operation)
            for operation in raw_operations
        ),
        manual_probes=tuple(
            _manual_probe_from_dict(evidence)
            for evidence in raw_manual_probes
        ),
        identity_raw=identity["raw"],
        identity_manufacturer=identity["manufacturer"],
        identity_model=identity["model"],
        identity_serial=identity["serial"],
        identity_firmware=identity["firmware"],
        run_count=run_count,
        stopped=stopped,
        stop_reason=_required_string(
            value.get("stop_reason"),
            "stop_reason",
        ),
    )


def validation_result_to_dict(
    result: ValidationResult,
) -> dict[str, object]:
    return {
        "document_type": _RESULT_DOCUMENT_TYPE,
        "schema_version": result.schema_version,
        "source_profile_id": result.source_profile_id,
        "resource": result.resource,
        "catalog_fingerprint": result.catalog_fingerprint,
        "identity": _identity_to_dict(
            raw=result.identity_raw,
            manufacturer=result.identity_manufacturer,
            model=result.identity_model,
            serial=result.identity_serial,
            firmware=result.identity_firmware,
        ),
        "compatible_capability_ids": list(
            result.compatible_capability_ids
        ),
        "fully_compatible_capability_ids": list(
            result.fully_compatible_capability_ids
        ),
        "compatible_operation_ids": list(
            result.compatible_operation_ids
        ),
        "incompatible_operation_ids": list(
            result.incompatible_operation_ids
        ),
        "unresolved_operation_ids": list(
            result.unresolved_operation_ids
        ),
        "status_counts": {
            key: count for key, count in result.status_counts
        },
        "scan_complete": result.scan_complete,
        "fully_resolved": result.fully_resolved,
        "stopped": result.stopped,
        "operations": [
            _operation_to_dict(operation)
            for operation in result.operations
        ],
        "manual_probes": [
            _manual_probe_to_dict(evidence)
            for evidence in result.manual_probes
        ],
    }


def validation_result_from_dict(value: object) -> ValidationResult:
    if not isinstance(value, dict):
        raise ValueError("Validation result document must be an object")
    if value.get("document_type") != _RESULT_DOCUMENT_TYPE:
        raise ValueError("Not a SCPI validation result document")
    schema_version = value.get("schema_version")
    if schema_version != VALIDATION_SCHEMA_VERSION:
        raise ValueError(
            f"Unsupported validation schema version: {schema_version}"
        )
    raw_operations = value.get("operations")
    raw_manual_probes = value.get("manual_probes", [])
    if not isinstance(raw_operations, list):
        raise ValueError("operations must be a list")
    if not isinstance(raw_manual_probes, list):
        raise ValueError("manual_probes must be a list")
    stopped = value.get("stopped")
    if not isinstance(stopped, bool):
        raise ValueError("stopped must be a boolean")
    identity = _identity_from_dict(value.get("identity"))
    progress = ValidationProgress(
        schema_version=schema_version,
        source_profile_id=_required_string(
            value.get("source_profile_id"),
            "source_profile_id",
        ),
        resource=_required_string(value.get("resource"), "resource"),
        catalog_fingerprint=_required_string(
            value.get("catalog_fingerprint"),
            "catalog_fingerprint",
        ),
        operations=tuple(
            _operation_from_dict(operation)
            for operation in raw_operations
        ),
        manual_probes=tuple(
            _manual_probe_from_dict(evidence)
            for evidence in raw_manual_probes
        ),
        identity_raw=identity["raw"],
        identity_manufacturer=identity["manufacturer"],
        identity_model=identity["model"],
        identity_serial=identity["serial"],
        identity_firmware=identity["firmware"],
        stopped=stopped,
    )
    rebuilt = build_validation_result(progress)

    stored_summary = {
        "compatible_capability_ids": _string_tuple(
            value.get("compatible_capability_ids"),
            "compatible_capability_ids",
        ),
        "fully_compatible_capability_ids": _string_tuple(
            value.get("fully_compatible_capability_ids"),
            "fully_compatible_capability_ids",
        ),
        "compatible_operation_ids": _string_tuple(
            value.get("compatible_operation_ids"),
            "compatible_operation_ids",
        ),
        "incompatible_operation_ids": _string_tuple(
            value.get("incompatible_operation_ids"),
            "incompatible_operation_ids",
        ),
        "unresolved_operation_ids": _string_tuple(
            value.get("unresolved_operation_ids"),
            "unresolved_operation_ids",
        ),
    }
    for field_name, stored in stored_summary.items():
        if stored != getattr(rebuilt, field_name):
            raise ValueError(
                f"Stored result summary does not match operation evidence: "
                f"{field_name}"
            )
    raw_counts = value.get("status_counts")
    if not isinstance(raw_counts, dict) or any(
        not isinstance(key, str)
        or isinstance(count, bool)
        or not isinstance(count, int)
        for key, count in raw_counts.items()
    ):
        raise ValueError("status_counts must be an object of integer counts")
    if tuple(raw_counts.items()) != rebuilt.status_counts:
        raise ValueError(
            "Stored result summary does not match operation evidence: status_counts"
        )
    for field_name in ("scan_complete", "fully_resolved"):
        stored = value.get(field_name)
        if not isinstance(stored, bool) or stored != getattr(rebuilt, field_name):
            raise ValueError(
                f"Stored result summary does not match operation evidence: "
                f"{field_name}"
            )
    return rebuilt


def _atomic_json_write(path: Path, payload: Mapping[str, object]) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    temporary.replace(path)


def save_validation_progress(
    path: str | Path,
    progress: ValidationProgress,
) -> None:
    _atomic_json_write(Path(path), validation_progress_to_dict(progress))


def load_validation_progress(path: str | Path) -> ValidationProgress:
    try:
        payload = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"Could not load validation progress: {exc}") from exc
    return validation_progress_from_dict(payload)


def save_validation_result(
    path: str | Path,
    result: ValidationResult,
) -> None:
    _atomic_json_write(Path(path), validation_result_to_dict(result))


def load_validation_result(path: str | Path) -> ValidationResult:
    try:
        payload = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"Could not load validation result: {exc}") from exc
    return validation_result_from_dict(payload)


def progress_file_checkpoint(
    path: str | Path,
) -> Callable[[ValidationProgress], None]:
    """Build an ``on_progress`` callback for crash-safe JSON checkpoints."""

    resolved = Path(path)

    def checkpoint(progress: ValidationProgress) -> None:
        save_validation_progress(resolved, progress)

    return checkpoint
