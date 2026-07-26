from __future__ import annotations

import math
import re
import string
from dataclasses import dataclass, replace
from threading import Event
from typing import Callable, Mapping, Protocol

from scpi_automation.identity import CatalogCapability, InstrumentProfile

from .models import (
    ErrorQueueEntry,
    FailureKind,
    OperationKind,
    OperationStatus,
    OperationValidation,
    ValidationPolicy,
    ValidationProgress,
    create_validation_progress,
    ensure_progress_matches_profile,
    operation_id,
)


class ValidationSession(Protocol):
    """Minimal adapter implemented by a PyVISA session or a test double."""

    timeout: int

    def query(self, command: str) -> str: ...

    def write(self, command: str) -> object: ...


class StopFlag(Protocol):
    def is_set(self) -> bool: ...


ProgressCallback = Callable[[ValidationProgress], None]


class _ManualRequired(ValueError):
    pass


@dataclass(frozen=True, slots=True)
class _QueueResult:
    entries: tuple[ErrorQueueEntry, ...]
    failure_kind: FailureKind = FailureKind.NONE
    message: str = ""

    @property
    def has_instrument_error(self) -> bool:
        return any(entry.code not in (0, None) for entry in self.entries)


def _exception_failure(exc: BaseException) -> FailureKind:
    name = type(exc).__name__.lower()
    message = str(exc).lower()
    if isinstance(exc, TimeoutError) or "timeout" in name or "timed out" in message:
        return FailureKind.TIMEOUT
    return FailureKind.SESSION_ERROR


def _parse_error_code(response: str) -> int | None:
    match = re.match(r"\s*([+-]?\d+)(?:\s*,|\s*$)", response)
    if match is None:
        return None
    try:
        return int(match.group(1))
    except ValueError:
        return None


def _drain_error_queue(
    session: ValidationSession,
    policy: ValidationPolicy,
    phase: str,
) -> _QueueResult:
    if policy.error_query is None:
        return _QueueResult(())

    entries: list[ErrorQueueEntry] = []
    try:
        for _index in range(policy.max_error_entries):
            response = str(session.query(policy.error_query)).strip()
            code = _parse_error_code(response)
            entries.append(
                ErrorQueueEntry(
                    phase=phase,
                    response=response,
                    code=code,
                )
            )
            if code is None:
                return _QueueResult(
                    tuple(entries),
                    FailureKind.INVALID_RESPONSE,
                    f"Could not parse the instrument error queue: {response!r}",
                )
            if code == 0:
                return _QueueResult(tuple(entries))
        return _QueueResult(
            tuple(entries),
            FailureKind.INSTRUMENT_ERROR,
            "Instrument error queue did not become empty within the limit",
        )
    except Exception as exc:
        return _QueueResult(
            tuple(entries),
            _exception_failure(exc),
            f"Could not read the instrument error queue: {exc}",
        )


def _placeholders(template: str) -> tuple[str, ...]:
    names: list[str] = []
    try:
        parsed = string.Formatter().parse(template)
        for _literal, name, format_spec, conversion in parsed:
            if name is None:
                continue
            if not name or format_spec or conversion:
                raise _ManualRequired(
                    "Complex SCPI template formatting requires manual validation"
                )
            if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", name):
                raise _ManualRequired(
                    "Unsafe SCPI template placeholder requires manual validation"
                )
            names.append(name)
    except ValueError as exc:
        raise _ManualRequired(f"Invalid SCPI template: {exc}") from exc
    return tuple(dict.fromkeys(names))


def _parameter_by_name(
    capability: CatalogCapability,
) -> dict[str, Mapping[str, object]]:
    return {
        str(parameter.get("name", "")): parameter
        for parameter in capability.parameters
        if str(parameter.get("name", "")).strip()
    }


def _safe_text_token(value: object) -> str:
    text = str(value).strip()
    if not text:
        raise _ManualRequired("An operation argument is empty")
    if len(text) > 10_000:
        raise _ManualRequired("An operation argument is too long")
    if any(character in text for character in ("\x00", "\r", "\n", ";")):
        raise _ManualRequired(
            "An operation argument contains an unsafe SCPI separator"
        )
    if any(ord(character) < 32 and character != "\t" for character in text):
        raise _ManualRequired(
            "An operation argument contains an unsafe control character"
        )
    return text


def _normalize_argument(
    value: object,
    definition: Mapping[str, object] | None,
) -> str:
    text = _safe_text_token(value)
    if definition is None:
        return text

    value_type = str(definition.get("type", "string"))
    mapping = definition.get("mapping", {})
    if isinstance(value, bool):
        text = "true" if value else "false"
    if isinstance(mapping, dict):
        mapped = {
            str(key).lower(): str(mapped_value)
            for key, mapped_value in mapping.items()
        }
        if text.lower() in mapped:
            text = mapped[text.lower()]

    choices = tuple(str(choice) for choice in definition.get("choices", ()))
    mnemonic_types = {
        "enum",
        "float_or_enum",
        "integer_or_mnemonic",
        "float_or_mnemonic",
    }
    if value_type == "enum":
        if choices and text not in choices:
            raise _ManualRequired(
                f"{definition.get('name', 'argument')} is outside catalog choices"
            )
        return _safe_text_token(text)
    if value_type == "boolean":
        if text.upper() not in {"0", "1", "ON", "OFF", "TRUE", "FALSE"}:
            raise _ManualRequired("Boolean argument is not a recognized SCPI token")
        return text
    if value_type in mnemonic_types and text in choices:
        return text
    if value_type == "number_or_auto" and text.upper() == "AUTO":
        return "AUTO"
    if value_type == "float_or_string" and re.fullmatch(
        r"[A-Za-z][A-Za-z0-9_.+-]*",
        text,
    ):
        return text
    if value_type == "voltage_current_time_triplets":
        parts = tuple(part.strip() for part in text.split(","))
        if (
            len(parts) < 3
            or len(parts) % 3
            or len(parts) // 3 > 128
        ):
            raise _ManualRequired(
                "Triplet argument must contain up to 128 voltage/current/time sets"
            )
        try:
            numbers = tuple(float(part) for part in parts)
        except ValueError as exc:
            raise _ManualRequired("Triplet argument must be numeric") from exc
        if any(not math.isfinite(number) for number in numbers):
            raise _ManualRequired("Triplet argument must contain finite numbers")
        return ",".join(parts)

    numeric_types = {
        "float",
        "integer",
        "number",
        "number_or_auto",
        "float_or_enum",
        "integer_or_mnemonic",
        "float_or_mnemonic",
    }
    if value_type in numeric_types:
        try:
            number = float(text)
        except ValueError as exc:
            raise _ManualRequired(
                f"{definition.get('name', 'argument')} must be numeric"
            ) from exc
        if not math.isfinite(number):
            raise _ManualRequired("Numeric argument must be finite")
        if value_type in {"integer", "integer_or_mnemonic"} and not number.is_integer():
            raise _ManualRequired("Integer argument must not contain a fraction")
        minimum = definition.get("minimum")
        maximum = definition.get("maximum")
        if isinstance(minimum, (int, float)) and number < minimum:
            raise _ManualRequired(
                f"{definition.get('name', 'argument')} is below the catalog minimum"
            )
        if isinstance(maximum, (int, float)) and number > maximum:
            raise _ManualRequired(
                f"{definition.get('name', 'argument')} is above the catalog maximum"
            )
    return _safe_text_token(text)


def _render_command(
    record: OperationValidation,
    capability: CatalogCapability,
    supplied_arguments: Mapping[str, object],
) -> tuple[str, dict[str, str]]:
    template = record.command_template.strip()
    if not template:
        raise _ManualRequired("The candidate command pack has an empty command")
    names = _placeholders(template)
    unknown = set(supplied_arguments) - set(names)
    if unknown:
        raise _ManualRequired(
            "Unknown operation argument(s): " + ", ".join(sorted(unknown))
        )
    missing = set(names) - set(supplied_arguments)
    if missing:
        raise _ManualRequired(
            "Probe value required for: " + ", ".join(sorted(missing))
        )
    definitions = _parameter_by_name(capability)
    normalized = {
        name: _normalize_argument(
            supplied_arguments[name],
            definitions.get(name),
        )
        for name in names
    }
    try:
        command = template.format_map(normalized)
    except (KeyError, ValueError) as exc:
        raise _ManualRequired(f"Could not render SCPI command: {exc}") from exc
    _safe_text_token(command)
    return command, normalized


def _strip_quotes(value: str) -> str:
    value = value.strip()
    if (
        len(value) >= 2
        and value[0] == value[-1]
        and value[0] in {"'", '"'}
    ):
        return value[1:-1].strip()
    return value


def _safe_restore_token(response: str) -> str:
    token = _strip_quotes(response)
    if not token:
        raise _ManualRequired("Readback was empty, so the value cannot be restored")
    if not re.fullmatch(
        r"(?:[+-]?(?:\d+(?:\.\d*)?|\.\d+)(?:[Ee][+-]?\d+)?|"
        r"[A-Za-z][A-Za-z0-9_.+-]*)",
        token,
    ):
        raise _ManualRequired(
            "Readback is not a conservative single SCPI token; "
            "automatic restoration is disabled"
        )
    return token


def _as_boolean(value: str) -> bool | None:
    normalized = _strip_quotes(value).upper()
    if normalized in {"1", "ON", "TRUE"}:
        return True
    if normalized in {"0", "OFF", "FALSE"}:
        return False
    return None


def _numeric_tokens(response: str) -> tuple[str, ...]:
    return tuple(
        token.strip()
        for token in response.split(",")
        if token.strip()
    )


def _finite_number(value: str) -> float | None:
    try:
        number = float(_strip_quotes(value))
    except ValueError:
        return None
    return number if math.isfinite(number) else None


def _validate_query_response(
    response: str,
    response_type: str,
) -> str:
    """Return an operator-facing error, or an empty string when valid."""

    kind = response_type.strip().casefold()
    if kind in {"", "string", "string_array", "float_or_string", "array"}:
        return ""
    if kind == "boolean":
        if _as_boolean(response) is None:
            return "Query response is not a supported boolean value"
        return ""
    if kind in {"float", "number"}:
        if _finite_number(response) is None:
            return "Query response is not a finite number"
        return ""
    if kind == "integer":
        number = _finite_number(response)
        if number is None or not number.is_integer():
            return "Query response is not an integer"
        return ""
    expected_count = {
        "float_pair": 2,
        "float_triplet": 3,
    }.get(kind)
    if kind == "float_array" or expected_count is not None:
        tokens = _numeric_tokens(response)
        if not tokens or any(_finite_number(token) is None for token in tokens):
            return "Query response is not a finite numeric list"
        if expected_count is not None and len(tokens) != expected_count:
            return (
                f"Query response must contain exactly {expected_count} "
                "numeric values"
            )
        return ""
    return ""


def _responses_equivalent(
    expected: str,
    actual: str,
    response_type: str,
    policy: ValidationPolicy,
) -> bool:
    if response_type.lower() == "boolean":
        left = _as_boolean(expected)
        right = _as_boolean(actual)
        return left is not None and left == right
    try:
        left_number = float(_strip_quotes(expected))
        right_number = float(_strip_quotes(actual))
    except ValueError:
        return _strip_quotes(expected).casefold() == _strip_quotes(actual).casefold()
    if not math.isfinite(left_number) or not math.isfinite(right_number):
        return False
    return math.isclose(
        left_number,
        right_number,
        rel_tol=policy.numeric_relative_tolerance,
        abs_tol=policy.numeric_absolute_tolerance,
    )


def _query_record(
    record: OperationValidation,
    capability: CatalogCapability,
    session: ValidationSession,
    policy: ValidationPolicy,
) -> OperationValidation:
    try:
        command, _arguments = _render_command(
            record,
            capability,
            policy.operation_arguments.get(record.operation_id, {}),
        )
    except _ManualRequired as exc:
        return replace(
            record,
            status=OperationStatus.MANUAL,
            validation_mode="manual_required",
            message=str(exc),
        )

    pre = _drain_error_queue(session, policy, "before_query")
    if pre.failure_kind is not FailureKind.NONE:
        return replace(
            record,
            status=OperationStatus.FAIL,
            validation_mode="automatic_query",
            attempts=record.attempts + 1,
            error_queue=pre.entries,
            failure_kind=pre.failure_kind,
            message=pre.message,
        )

    try:
        response = str(session.query(command)).strip()
    except Exception as exc:
        return replace(
            record,
            status=OperationStatus.FAIL,
            validation_mode="automatic_query",
            attempts=record.attempts + 1,
            sent_commands=(command,),
            error_queue=pre.entries,
            failure_kind=_exception_failure(exc),
            message=str(exc),
        )
    post = _drain_error_queue(session, policy, "after_query")
    error_entries = pre.entries + post.entries
    if post.failure_kind is not FailureKind.NONE:
        return replace(
            record,
            status=OperationStatus.FAIL,
            validation_mode="automatic_query",
            attempts=record.attempts + 1,
            sent_commands=(command,),
            response=response,
            error_queue=error_entries,
            failure_kind=post.failure_kind,
            message=post.message,
        )
    if post.has_instrument_error:
        return replace(
            record,
            status=OperationStatus.FAIL,
            validation_mode="automatic_query",
            attempts=record.attempts + 1,
            sent_commands=(command,),
            response=response,
            error_queue=error_entries,
            failure_kind=FailureKind.INSTRUMENT_ERROR,
            message="Instrument reported an error after the query",
        )
    if not response:
        return replace(
            record,
            status=OperationStatus.FAIL,
            validation_mode="automatic_query",
            attempts=record.attempts + 1,
            sent_commands=(command,),
            error_queue=error_entries,
            failure_kind=FailureKind.INVALID_RESPONSE,
            message="Query returned an empty response",
        )
    response_error = _validate_query_response(
        response,
        record.response_type,
    )
    if response_error:
        return replace(
            record,
            status=OperationStatus.FAIL,
            validation_mode="automatic_query",
            attempts=record.attempts + 1,
            sent_commands=(command,),
            response=response,
            original_response=response,
            error_queue=error_entries,
            failure_kind=FailureKind.INVALID_RESPONSE,
            message=response_error,
        )
    return replace(
        record,
        status=OperationStatus.PASS,
        validation_mode="automatic_query",
        attempts=record.attempts + 1,
        sent_commands=(command,),
        response=response,
        original_response=response,
        error_queue=error_entries,
        failure_kind=FailureKind.NONE,
        message="Query completed without an instrument error",
    )


def _query_for_capability(
    progress: ValidationProgress,
    capability_id: str,
) -> OperationValidation | None:
    query_id = operation_id(capability_id, OperationKind.QUERY.value)
    try:
        return progress.operation(query_id)
    except KeyError:
        return None


def _build_reversible_write(
    record: OperationValidation,
    query_record: OperationValidation,
    capability: CatalogCapability,
    policy: ValidationPolicy,
) -> tuple[str, str, str, str]:
    if query_record.status is not OperationStatus.PASS:
        raise _ManualRequired(
            "The paired readback query did not pass, so writing is disabled"
        )
    set_arguments = policy.operation_arguments.get(record.operation_id, {})
    test_command, normalized_set = _render_command(
        record,
        capability,
        set_arguments,
    )
    set_names = _placeholders(record.command_template)
    query_names = _placeholders(query_record.command_template)
    candidates = tuple(name for name in set_names if name not in query_names)
    if len(candidates) > 1:
        raise _ManualRequired(
            "Automatic write validation requires exactly one restorable value "
            "in addition to the readback-query selectors"
        )

    normalized_query: dict[str, str] = {}
    if query_names:
        _query_command, normalized_query = _render_command(
            query_record,
            capability,
            policy.operation_arguments.get(query_record.operation_id, {}),
        )
    for shared_name in set(set_names) & set(query_names):
        if normalized_set[shared_name] != normalized_query[shared_name]:
            raise _ManualRequired(
                "Set and query selectors differ; original-value restoration "
                "cannot be guaranteed"
            )

    original_token = _safe_restore_token(query_record.response)
    if candidates:
        restore_parameter = candidates[0]
        test_token = normalized_set[restore_parameter]
        restore_arguments = dict(normalized_set)
        restore_arguments[restore_parameter] = original_token
        try:
            restore_command = record.command_template.format_map(
                restore_arguments
            )
        except (KeyError, ValueError) as exc:
            raise _ManualRequired(
                f"Could not construct the restore command: {exc}"
            ) from exc
    else:
        # Some user-facing functions intentionally encode one enum value as a
        # distinct operation, for example ``...:MODE MAXH`` and
        # ``...:MODE WRIT``.  This lets each value be validated independently.
        # A conservative final single-token set value is still reversible:
        # capture the paired query, replace only that final token, then verify
        # both the probe and restoration through readback.
        fixed_value_match = re.fullmatch(
            r"(.+?\s)([A-Za-z][A-Za-z0-9_.+-]*)",
            test_command,
        )
        if fixed_value_match is None:
            raise _ManualRequired(
                "Automatic write validation requires one restorable value "
                "placeholder or a conservative final mnemonic token"
            )
        command_prefix, test_token = fixed_value_match.groups()
        restore_command = f"{command_prefix}{original_token}"
    if _responses_equivalent(
        original_token,
        test_token,
        query_record.response_type,
        policy,
    ):
        raise _ManualRequired(
            "Probe value matches the original value; choose a different safe value"
        )
    _safe_text_token(restore_command)
    return test_command, restore_command, test_token, original_token


def _write_with_error_check(
    session: ValidationSession,
    command: str,
    policy: ValidationPolicy,
    phase: str,
) -> tuple[_QueueResult, _QueueResult, BaseException | None]:
    pre = _drain_error_queue(session, policy, f"before_{phase}")
    if pre.failure_kind is not FailureKind.NONE:
        return pre, _QueueResult(()), None
    try:
        session.write(command)
    except Exception as exc:
        return pre, _QueueResult(()), exc
    post = _drain_error_queue(session, policy, f"after_{phase}")
    return pre, post, None


def _reversible_set_record(
    record: OperationValidation,
    capability: CatalogCapability,
    progress: ValidationProgress,
    session: ValidationSession,
    policy: ValidationPolicy,
) -> OperationValidation:
    query_record = _query_for_capability(progress, record.capability_id)
    if query_record is None:
        return replace(
            record,
            status=OperationStatus.MANUAL,
            validation_mode="manual_required",
            message="No paired query exists to capture and restore the original value",
        )
    try:
        (
            test_command,
            restore_command,
            test_token,
            original_token,
        ) = _build_reversible_write(
            record,
            query_record,
            capability,
            policy,
        )
    except _ManualRequired as exc:
        return replace(
            record,
            status=OperationStatus.MANUAL,
            validation_mode="manual_required",
            original_response=query_record.response,
            message=str(exc),
        )

    commands: list[str] = []
    queue_entries: list[ErrorQueueEntry] = []
    failure_kind = FailureKind.NONE
    message = ""
    verification_response = ""
    restore_attempted = False
    restored = False
    write_may_have_occurred = False

    pre, post, write_error = _write_with_error_check(
        session,
        test_command,
        policy,
        "probe_write",
    )
    queue_entries.extend(pre.entries)
    queue_entries.extend(post.entries)
    if pre.failure_kind is not FailureKind.NONE:
        failure_kind = pre.failure_kind
        message = pre.message
    else:
        commands.append(test_command)
        write_may_have_occurred = True
        if write_error is not None:
            failure_kind = _exception_failure(write_error)
            message = str(write_error)
        elif post.failure_kind is not FailureKind.NONE:
            failure_kind = post.failure_kind
            message = post.message
        elif post.has_instrument_error:
            failure_kind = FailureKind.INSTRUMENT_ERROR
            message = "Instrument rejected the probe write"

    if failure_kind is FailureKind.NONE:
        try:
            readback_command, _ = _render_command(
                query_record,
                capability,
                policy.operation_arguments.get(query_record.operation_id, {}),
            )
            pre_read = _drain_error_queue(
                session,
                policy,
                "before_probe_readback",
            )
            queue_entries.extend(pre_read.entries)
            if pre_read.failure_kind is not FailureKind.NONE:
                failure_kind = pre_read.failure_kind
                message = pre_read.message
            else:
                verification_response = str(
                    session.query(readback_command)
                ).strip()
                commands.append(readback_command)
                post_read = _drain_error_queue(
                    session,
                    policy,
                    "after_probe_readback",
                )
                queue_entries.extend(post_read.entries)
                if post_read.failure_kind is not FailureKind.NONE:
                    failure_kind = post_read.failure_kind
                    message = post_read.message
                elif post_read.has_instrument_error:
                    failure_kind = FailureKind.INSTRUMENT_ERROR
                    message = "Instrument reported an error after probe readback"
                elif not _responses_equivalent(
                    test_token,
                    verification_response,
                    query_record.response_type,
                    policy,
                ):
                    failure_kind = FailureKind.READBACK_MISMATCH
                    message = (
                        "Probe readback does not match the value that was written"
                    )
        except Exception as exc:
            failure_kind = _exception_failure(exc)
            message = str(exc)

    # Restoration is deliberately attempted even after a write timeout because
    # the adapter cannot know whether the instrument accepted the command.
    if write_may_have_occurred:
        restore_attempted = True
        pre_restore, post_restore, restore_error = _write_with_error_check(
            session,
            restore_command,
            policy,
            "restore_write",
        )
        queue_entries.extend(pre_restore.entries)
        queue_entries.extend(post_restore.entries)
        if pre_restore.failure_kind is FailureKind.NONE:
            commands.append(restore_command)
        if (
            pre_restore.failure_kind is not FailureKind.NONE
            or restore_error is not None
            or post_restore.failure_kind is not FailureKind.NONE
            or post_restore.has_instrument_error
        ):
            failure_kind = FailureKind.RESTORE_FAILED
            if restore_error is not None:
                message = f"Original-value restoration failed: {restore_error}"
            else:
                message = (
                    "Original-value restoration could not be verified from "
                    "the error queue"
                )
        else:
            try:
                readback_command, _ = _render_command(
                    query_record,
                    capability,
                    policy.operation_arguments.get(
                        query_record.operation_id,
                        {},
                    ),
                )
                pre_verify = _drain_error_queue(
                    session,
                    policy,
                    "before_restore_readback",
                )
                queue_entries.extend(pre_verify.entries)
                if pre_verify.failure_kind is not FailureKind.NONE:
                    failure_kind = FailureKind.RESTORE_FAILED
                    message = pre_verify.message
                else:
                    restored_response = str(
                        session.query(readback_command)
                    ).strip()
                    commands.append(readback_command)
                    post_verify = _drain_error_queue(
                        session,
                        policy,
                        "after_restore_readback",
                    )
                    queue_entries.extend(post_verify.entries)
                    if (
                        post_verify.failure_kind is not FailureKind.NONE
                        or post_verify.has_instrument_error
                        or not _responses_equivalent(
                            original_token,
                            restored_response,
                            query_record.response_type,
                            policy,
                        )
                    ):
                        failure_kind = FailureKind.RESTORE_FAILED
                        message = (
                            "Instrument did not read back the original value "
                            "after restoration"
                        )
                    else:
                        restored = True
            except Exception as exc:
                failure_kind = FailureKind.RESTORE_FAILED
                message = f"Could not verify original-value restoration: {exc}"

    if failure_kind is FailureKind.NONE and restored:
        status = OperationStatus.PASS
        message = "Probe write, readback, and original-value restoration passed"
    else:
        status = OperationStatus.FAIL
        if failure_kind is FailureKind.NONE:
            failure_kind = FailureKind.RESTORE_FAILED
            message = "Original value was not restored"
    return replace(
        record,
        status=status,
        validation_mode="automatic_reversible_write",
        attempts=record.attempts + 1,
        sent_commands=tuple(commands),
        response=verification_response,
        original_response=query_record.response,
        verification_response=verification_response,
        restore_attempted=restore_attempted,
        restored=restored,
        error_queue=tuple(queue_entries),
        failure_kind=failure_kind,
        message=message,
    )


def _validate_policy_ids(
    progress: ValidationProgress,
    policy: ValidationPolicy,
) -> None:
    known = {item.operation_id for item in progress.operations}
    supplied = (
        set(policy.operation_arguments)
        | set(policy.approved_hazardous_operation_ids)
        | set(policy.skipped_operation_ids)
    )
    unknown = supplied - known
    if unknown:
        raise ValueError(
            "Validation policy references unknown operation(s): "
            + ", ".join(sorted(unknown))
        )


def _checkpoint(
    progress: ValidationProgress,
    callback: ProgressCallback | None,
) -> None:
    if callback is not None:
        callback(progress)


def validate_profile(
    profile: InstrumentProfile,
    session: ValidationSession,
    *,
    resource: str = "",
    policy: ValidationPolicy | None = None,
    progress: ValidationProgress | None = None,
    stop_flag: StopFlag | None = None,
    on_progress: ProgressCallback | None = None,
) -> ValidationProgress:
    """Validate every profile operation serially with all queries first.

    The function never opens or closes VISA resources.  It only depends on the
    small :class:`ValidationSession` protocol, allowing the caller to adapt a
    real PyVISA session and allowing the core to be tested without hardware.
    """

    policy = policy or ValidationPolicy()
    if progress is None:
        progress = create_validation_progress(profile, resource)
    else:
        ensure_progress_matches_profile(progress, profile)
        if resource and progress.resource and resource != progress.resource:
            raise ValueError("Validation progress belongs to another resource")
        if resource and not progress.resource:
            progress = replace(progress, resource=resource)
    _validate_policy_ids(progress, policy)
    progress = replace(
        progress,
        run_count=progress.run_count + 1,
        stopped=False,
        stop_reason="",
    )
    _checkpoint(progress, on_progress)

    capabilities = {
        capability.capability_id: capability
        for capability in profile.capabilities
    }
    old_timeout = session.timeout
    session.timeout = policy.timeout_ms
    try:
        # Global query-first ordering guarantees all restorable original values
        # are captured before any profile write is attempted.
        phases = (OperationKind.QUERY, OperationKind.SET, OperationKind.EXECUTE)
        for phase in phases:
            operation_ids = tuple(
                item.operation_id
                for item in progress.operations
                if item.kind is phase and item.status is OperationStatus.PENDING
            )
            for current_id in operation_ids:
                if stop_flag is not None and stop_flag.is_set():
                    progress = replace(
                        progress,
                        stopped=True,
                        stop_reason="Validation was stopped by the operator",
                    )
                    _checkpoint(progress, on_progress)
                    return progress
                record = progress.operation(current_id)
                capability = capabilities[record.capability_id]
                if current_id in policy.skipped_operation_ids:
                    updated = replace(
                        record,
                        status=OperationStatus.SKIPPED,
                        validation_mode="policy_skip",
                        message="Operation was skipped by explicit policy",
                    )
                elif record.binary:
                    updated = replace(
                        record,
                        status=OperationStatus.MANUAL,
                        validation_mode="manual_required",
                        message=(
                            "Binary transfer requires a model-specific adapter "
                            "and manual validation"
                        ),
                    )
                elif record.kind is OperationKind.QUERY:
                    updated = _query_record(
                        record,
                        capability,
                        session,
                        policy,
                    )
                elif (
                    record.risk_level in {"high", "hazardous", "critical"}
                    and current_id
                    not in policy.approved_hazardous_operation_ids
                ):
                    updated = replace(
                        record,
                        status=OperationStatus.UNSAFE,
                        validation_mode="approval_required",
                        message=(
                            "Hazardous write is blocked until this exact "
                            "operation ID is explicitly approved"
                        ),
                    )
                elif record.kind is OperationKind.SET:
                    updated = _reversible_set_record(
                        record,
                        capability,
                        progress,
                        session,
                        policy,
                    )
                else:
                    updated = replace(
                        record,
                        status=OperationStatus.MANUAL,
                        validation_mode="manual_required",
                        message=(
                            "Execute commands have no generic original-value "
                            "readback/restore path and are never auto-run"
                        ),
                    )
                progress = progress.replace_operation(updated)
                _checkpoint(progress, on_progress)
        return progress
    finally:
        session.timeout = old_timeout


def new_stop_flag() -> Event:
    """Convenience factory kept separate from the session abstraction."""

    return Event()
