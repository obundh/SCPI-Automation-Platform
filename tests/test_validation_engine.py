from __future__ import annotations

import json
import tempfile
import threading
import unittest
from dataclasses import replace
from pathlib import Path

from scpi_automation.identity import (
    CatalogCapability,
    CatalogOperation,
    DeviceCategory,
    InstrumentIdentity,
    InstrumentProfile,
    catalog_profiles,
    profile_by_id,
)
from scpi_automation.validation import (
    FailureKind,
    ManualProbeEvidence,
    OperationStatus,
    ValidationPolicy,
    apply_manual_result,
    build_validation_result,
    create_validation_progress,
    load_validation_progress,
    load_validation_result,
    operation_id,
    progress_file_checkpoint,
    reset_operations,
    save_validation_progress,
    save_validation_result,
    validate_profile,
)


def _capability(
    capability_id: str,
    operations: tuple[CatalogOperation, ...],
    *,
    risk: str = "low",
    parameters: tuple[dict[str, object], ...] = (),
) -> CatalogCapability:
    return CatalogCapability(
        capability_id=capability_id,
        label_ko=capability_id,
        group="test",
        risk_level=risk,
        verification="profile_required",
        operations=operations,
        parameters=parameters,
    )


FREQUENCY = _capability(
    "source.frequency",
    (
        CatalogOperation("set", ":FREQ {value}"),
        CatalogOperation("query", ":FREQ?", "float"),
    ),
    parameters=(
        {
            "name": "value",
            "type": "float",
            "minimum": 1,
            "maximum": 1000,
        },
    ),
)
MODE = _capability(
    "source.mode",
    (
        CatalogOperation("set", ":MODE {mode}"),
        CatalogOperation("query", ":MODE?", "string"),
    ),
    parameters=(
        {
            "name": "mode",
            "type": "enum",
            "choices": ["CW", "LIST"],
        },
    ),
)
MAX_HOLD = _capability(
    "trace.mode.max_hold",
    (
        CatalogOperation("set", ":MODE MAXH"),
        CatalogOperation("query", ":MODE?", "string"),
    ),
)
POWER = _capability(
    "source.power",
    (
        CatalogOperation("set", ":POW {value}"),
        CatalogOperation("query", ":POW?", "float"),
    ),
    risk="high",
    parameters=(
        {
            "name": "value",
            "type": "float",
            "minimum": -100,
            "maximum": 10,
        },
    ),
)
MEASUREMENT = _capability(
    "measurement.value",
    (CatalogOperation("query", ":MEAS?", "float"),),
)
BAD_QUERY = _capability(
    "measurement.bad",
    (CatalogOperation("query", ":BAD?", "float"),),
)
WRITE_ONLY = _capability(
    "source.write_only",
    (CatalogOperation("set", ":WRITEONLY {value}"),),
    parameters=({"name": "value", "type": "float"},),
)
ACTION = _capability(
    "system.action",
    (CatalogOperation("execute", ":DO"),),
)
BINARY = _capability(
    "trace.binary",
    (CatalogOperation("query", ":TRACE?", "float_array", binary=True),),
)
TRACE = _capability(
    "trace.read",
    (
        CatalogOperation(
            "query",
            ":TRACE? TRACE{trace}",
            "float_array",
        ),
    ),
    parameters=(
        {
            "name": "trace",
            "type": "integer",
            "minimum": 1,
            "maximum": 6,
        },
    ),
)


def _profile(
    *capabilities: CatalogCapability,
    profile_id: str = "test_profile",
) -> InstrumentProfile:
    return InstrumentProfile(
        profile_id=profile_id,
        manufacturer="Example",
        model_family="VALIDATOR",
        models=("VALIDATOR-1",),
        instrument_class="rf_signal_generator",
        category=DeviceCategory.SIGNAL_GENERATOR,
        idn_patterns=(),
        verification_status="test",
        hardware_verified=False,
        capabilities=capabilities,
    )


class FakeSession:
    """State-aware fake implementing only the validation session protocol."""

    def __init__(self) -> None:
        self.timeout = 777
        self.values: dict[str, str] = {
            "FREQ": "100",
            "MODE": "CW",
            "POW": "-30",
        }
        self.initial_values = dict(self.values)
        self.events: list[tuple[str, str]] = []
        self.error_queue: list[str] = []
        self.fail_queries: dict[str, BaseException] = {}
        self.query_overrides: dict[str, str] = {}
        self.error_after_query: dict[str, str] = {}
        self.ignore_probe_for: set[str] = set()
        self.fail_restore_for: set[str] = set()
        self.timeout_after_applying_for: set[str] = set()

    def query(self, command: str) -> str:
        self.events.append(("query", command))
        if command == "SYST:ERR?":
            if self.error_queue:
                return self.error_queue.pop(0)
            return '0,"No error"'
        if command in self.fail_queries:
            raise self.fail_queries[command]
        if command in self.query_overrides:
            return self.query_overrides[command]
        if command in self.error_after_query:
            self.error_queue.append(self.error_after_query[command])
        if command == ":FREQ?":
            return self.values["FREQ"]
        if command == ":MODE?":
            return self.values["MODE"]
        if command == ":POW?":
            return self.values["POW"]
        if command == ":MEAS?":
            return "42.5"
        if command == ":BAD?":
            return "0"
        if command.startswith(":TRACE?"):
            return "1,2,3"
        raise RuntimeError(f"Unexpected query: {command}")

    def write(self, command: str) -> object:
        self.events.append(("write", command))
        pieces = command.split(maxsplit=1)
        if len(pieces) != 2:
            raise RuntimeError(f"Unexpected write: {command}")
        header, value = pieces
        key = {
            ":FREQ": "FREQ",
            ":MODE": "MODE",
            ":POW": "POW",
            ":WRITEONLY": "WRITEONLY",
        }.get(header)
        if key is None:
            raise RuntimeError(f"Unexpected write: {command}")
        original = self.initial_values.get(key)
        if key in self.fail_restore_for and original == value:
            raise RuntimeError("restore rejected")
        if not (key in self.ignore_probe_for and original != value):
            self.values[key] = value
        if key in self.timeout_after_applying_for and original != value:
            self.timeout_after_applying_for.remove(key)
            raise TimeoutError("write timed out after transmission")
        return len(command)

    @property
    def operation_events(self) -> list[tuple[str, str]]:
        return [
            event
            for event in self.events
            if event[1] != "SYST:ERR?"
        ]


class ValidationModelTests(unittest.TestCase):
    def test_progress_contains_every_profile_operation_with_stable_ids(self) -> None:
        profile = _profile(FREQUENCY, MEASUREMENT, ACTION)

        progress = create_validation_progress(profile, "TCPIP::1::INSTR")

        self.assertEqual(
            tuple(item.operation_id for item in progress.operations),
            (
                "source.frequency::set",
                "source.frequency::query",
                "measurement.value::query",
                "system.action::execute",
            ),
        )
        self.assertTrue(
            all(
                item.status is OperationStatus.PENDING
                for item in progress.operations
            )
        )
        self.assertEqual(len(progress.catalog_fingerprint), 64)

    def test_every_catalog_operation_is_projected_without_loss(self) -> None:
        for profile in catalog_profiles():
            with self.subTest(profile=profile.profile_id):
                progress = create_validation_progress(profile, "DEMO::INSTR")
                expected = sum(
                    len(capability.operations)
                    for capability in profile.capabilities
                )
                self.assertEqual(len(progress.operations), expected)
                self.assertEqual(
                    len({item.operation_id for item in progress.operations}),
                    expected,
                )

        fsv = profile_by_id("rs_fsv_fsva")
        self.assertIsNotNone(fsv)
        assert fsv is not None
        progress = create_validation_progress(fsv, "TCPIP::FSV30::INSTR")
        self.assertEqual(
            len(progress.operations),
            sum(len(item.operations) for item in fsv.capabilities),
        )

    def test_manual_result_requires_manual_state_and_evidence(self) -> None:
        profile = _profile(WRITE_ONLY)
        session = FakeSession()
        progress = validate_profile(
            profile,
            session,
            policy=ValidationPolicy(
                operation_arguments={
                    "source.write_only::set": {"value": 3},
                }
            ),
        )
        self.assertEqual(
            progress.operation("source.write_only::set").status,
            OperationStatus.MANUAL,
        )

        with self.assertRaises(ValueError):
            apply_manual_result(
                progress,
                "source.write_only::set",
                passed=True,
                note="",
            )
        progress = apply_manual_result(
            progress,
            "source.write_only::set",
            passed=True,
            note="Bench operator confirmed write and recovery procedure TEST-1.",
        )
        result = build_validation_result(progress)

        self.assertEqual(
            result.compatible_operation_ids,
            ("source.write_only::set",),
        )
        self.assertEqual(
            result.fully_compatible_capability_ids,
            ("source.write_only",),
        )


class ValidationEngineTests(unittest.TestCase):
    def test_fixed_enum_function_is_validated_and_restored_independently(
        self,
    ) -> None:
        profile = _profile(MAX_HOLD)
        session = FakeSession()

        progress = validate_profile(profile, session)

        record = progress.operation("trace.mode.max_hold::set")
        self.assertEqual(record.status, OperationStatus.PASS)
        self.assertEqual(record.verification_response, "MAXH")
        self.assertTrue(record.restore_attempted)
        self.assertTrue(record.restored)
        self.assertEqual(session.values["MODE"], "CW")
        self.assertIn(("write", ":MODE MAXH"), session.events)
        self.assertIn(("write", ":MODE CW"), session.events)

    def test_queries_run_globally_before_reversible_writes_and_values_restore(self) -> None:
        profile = _profile(FREQUENCY, MODE)
        session = FakeSession()
        progress = validate_profile(
            profile,
            session,
            resource="USB0::1::INSTR",
            policy=ValidationPolicy(
                operation_arguments={
                    "source.frequency::set": {"value": 200},
                    "source.mode::set": {"mode": "LIST"},
                }
            ),
        )

        self.assertEqual(
            session.operation_events,
            [
                ("query", ":FREQ?"),
                ("query", ":MODE?"),
                ("write", ":FREQ 200"),
                ("query", ":FREQ?"),
                ("write", ":FREQ 100"),
                ("query", ":FREQ?"),
                ("write", ":MODE LIST"),
                ("query", ":MODE?"),
                ("write", ":MODE CW"),
                ("query", ":MODE?"),
            ],
        )
        self.assertEqual(session.values, session.initial_values)
        self.assertEqual(session.timeout, 777)
        for item in progress.operations:
            self.assertEqual(item.status, OperationStatus.PASS)
        frequency_set = progress.operation("source.frequency::set")
        self.assertTrue(frequency_set.restore_attempted)
        self.assertTrue(frequency_set.restored)

    def test_hazardous_write_is_blocked_until_exact_operation_is_approved(self) -> None:
        profile = _profile(POWER)
        session = FakeSession()
        arguments = {"source.power::set": {"value": -20}}

        progress = validate_profile(
            profile,
            session,
            policy=ValidationPolicy(operation_arguments=arguments),
        )

        self.assertEqual(
            progress.operation("source.power::query").status,
            OperationStatus.PASS,
        )
        self.assertEqual(
            progress.operation("source.power::set").status,
            OperationStatus.UNSAFE,
        )
        self.assertFalse(any(kind == "write" for kind, _ in session.events))
        result = build_validation_result(progress)
        self.assertEqual(
            result.compatible_operation_ids,
            ("source.power::query",),
        )
        self.assertEqual(
            result.compatible_capability_ids,
            ("source.power",),
        )
        self.assertEqual(result.fully_compatible_capability_ids, ())

        progress = reset_operations(progress, {"source.power::set"})
        progress = validate_profile(
            profile,
            session,
            progress=progress,
            policy=ValidationPolicy(
                operation_arguments=arguments,
                approved_hazardous_operation_ids=frozenset(
                    {"source.power::set"}
                ),
            ),
        )

        self.assertEqual(
            progress.operation("source.power::set").status,
            OperationStatus.PASS,
        )
        self.assertEqual(session.values["POW"], "-30")

    def test_execute_and_write_without_readback_are_manual_and_never_sent(self) -> None:
        profile = _profile(WRITE_ONLY, ACTION)
        session = FakeSession()

        progress = validate_profile(
            profile,
            session,
            policy=ValidationPolicy(
                operation_arguments={
                    "source.write_only::set": {"value": 2},
                }
            ),
        )

        self.assertEqual(
            progress.operation("source.write_only::set").status,
            OperationStatus.MANUAL,
        )
        self.assertEqual(
            progress.operation("system.action::execute").status,
            OperationStatus.MANUAL,
        )
        self.assertEqual(session.operation_events, [])

    def test_query_timeout_is_recorded_and_blocks_paired_set(self) -> None:
        profile = _profile(FREQUENCY)
        session = FakeSession()
        session.fail_queries[":FREQ?"] = TimeoutError("query timeout")

        progress = validate_profile(
            profile,
            session,
            policy=ValidationPolicy(
                operation_arguments={
                    "source.frequency::set": {"value": 200},
                }
            ),
        )

        query = progress.operation("source.frequency::query")
        setting = progress.operation("source.frequency::set")
        self.assertEqual(query.status, OperationStatus.FAIL)
        self.assertEqual(query.failure_kind, FailureKind.TIMEOUT)
        self.assertEqual(setting.status, OperationStatus.MANUAL)
        self.assertNotIn(("write", ":FREQ 200"), session.events)

    def test_instrument_error_queue_is_recorded_as_query_failure(self) -> None:
        profile = _profile(BAD_QUERY)
        session = FakeSession()
        session.error_after_query[":BAD?"] = '-113,"Undefined header"'

        progress = validate_profile(profile, session)
        record = progress.operation("measurement.bad::query")

        self.assertEqual(record.status, OperationStatus.FAIL)
        self.assertEqual(record.failure_kind, FailureKind.INSTRUMENT_ERROR)
        self.assertTrue(
            any(entry.code == -113 for entry in record.error_queue)
        )

    def test_declared_numeric_query_rejects_non_numeric_response(self) -> None:
        profile = _profile(MEASUREMENT)
        session = FakeSession()
        session.query_overrides[":MEAS?"] = "NOT_A_NUMBER"

        progress = validate_profile(profile, session)
        record = progress.operation("measurement.value::query")

        self.assertEqual(record.status, OperationStatus.FAIL)
        self.assertEqual(record.failure_kind, FailureKind.INVALID_RESPONSE)
        self.assertIn("finite number", record.message)

    def test_readback_mismatch_fails_but_still_restores_original(self) -> None:
        profile = _profile(FREQUENCY)
        session = FakeSession()
        session.ignore_probe_for.add("FREQ")

        progress = validate_profile(
            profile,
            session,
            policy=ValidationPolicy(
                operation_arguments={
                    "source.frequency::set": {"value": 200},
                }
            ),
        )
        record = progress.operation("source.frequency::set")

        self.assertEqual(record.status, OperationStatus.FAIL)
        self.assertEqual(record.failure_kind, FailureKind.READBACK_MISMATCH)
        self.assertTrue(record.restore_attempted)
        self.assertTrue(record.restored)
        self.assertEqual(session.values["FREQ"], "100")

    def test_restore_failure_is_a_distinct_critical_failure(self) -> None:
        profile = _profile(FREQUENCY)
        session = FakeSession()
        session.fail_restore_for.add("FREQ")

        progress = validate_profile(
            profile,
            session,
            policy=ValidationPolicy(
                operation_arguments={
                    "source.frequency::set": {"value": 200},
                }
            ),
        )
        record = progress.operation("source.frequency::set")

        self.assertEqual(record.status, OperationStatus.FAIL)
        self.assertEqual(record.failure_kind, FailureKind.RESTORE_FAILED)
        self.assertTrue(record.restore_attempted)
        self.assertFalse(record.restored)
        self.assertEqual(session.values["FREQ"], "200")

    def test_write_timeout_attempts_restoration_even_if_transmission_may_have_worked(
        self,
    ) -> None:
        profile = _profile(FREQUENCY)
        session = FakeSession()
        session.timeout_after_applying_for.add("FREQ")

        progress = validate_profile(
            profile,
            session,
            policy=ValidationPolicy(
                operation_arguments={
                    "source.frequency::set": {"value": 200},
                }
            ),
        )
        record = progress.operation("source.frequency::set")

        self.assertEqual(record.status, OperationStatus.FAIL)
        self.assertEqual(record.failure_kind, FailureKind.TIMEOUT)
        self.assertTrue(record.restored)
        self.assertEqual(session.values["FREQ"], "100")

    def test_stop_leaves_remaining_operations_pending_and_run_can_resume(self) -> None:
        profile = _profile(FREQUENCY, MODE)
        session = FakeSession()
        stop = threading.Event()

        def stop_after_first_result(progress) -> None:
            terminal = sum(
                item.status is not OperationStatus.PENDING
                for item in progress.operations
            )
            if terminal == 1:
                stop.set()

        policy = ValidationPolicy(
            operation_arguments={
                "source.frequency::set": {"value": 200},
                "source.mode::set": {"mode": "LIST"},
            }
        )
        progress = validate_profile(
            profile,
            session,
            policy=policy,
            stop_flag=stop,
            on_progress=stop_after_first_result,
        )

        self.assertTrue(progress.stopped)
        self.assertEqual(
            progress.operation("source.frequency::query").status,
            OperationStatus.PASS,
        )
        self.assertEqual(
            progress.operation("source.mode::query").status,
            OperationStatus.PENDING,
        )
        stop.clear()
        progress = validate_profile(
            profile,
            session,
            policy=policy,
            progress=progress,
            stop_flag=stop,
        )

        self.assertFalse(progress.stopped)
        self.assertTrue(progress.is_scan_complete)
        self.assertTrue(progress.is_fully_resolved)
        self.assertEqual(progress.run_count, 2)
        self.assertTrue(
            all(item.status is OperationStatus.PASS for item in progress.operations)
        )

    def test_binary_missing_probe_and_explicit_skip_have_non_pass_statuses(self) -> None:
        profile = _profile(BINARY, TRACE, MEASUREMENT)
        session = FakeSession()

        progress = validate_profile(
            profile,
            session,
            policy=ValidationPolicy(
                skipped_operation_ids=frozenset(
                    {"measurement.value::query"}
                )
            ),
        )

        self.assertEqual(
            progress.operation("trace.binary::query").status,
            OperationStatus.MANUAL,
        )
        self.assertEqual(
            progress.operation("trace.read::query").status,
            OperationStatus.MANUAL,
        )
        self.assertEqual(
            progress.operation("measurement.value::query").status,
            OperationStatus.SKIPPED,
        )
        self.assertEqual(session.operation_events, [])

    def test_out_of_range_or_injected_probe_is_manual_and_not_sent(self) -> None:
        profile = _profile(FREQUENCY, MODE)
        session = FakeSession()

        progress = validate_profile(
            profile,
            session,
            policy=ValidationPolicy(
                operation_arguments={
                    "source.frequency::set": {"value": 5000},
                    "source.mode::set": {"mode": "LIST;:OUTP ON"},
                }
            ),
        )

        self.assertEqual(
            progress.operation("source.frequency::set").status,
            OperationStatus.MANUAL,
        )
        self.assertEqual(
            progress.operation("source.mode::set").status,
            OperationStatus.MANUAL,
        )
        self.assertFalse(any(kind == "write" for kind, _ in session.events))

    def test_unknown_policy_operation_is_rejected_before_session_use(self) -> None:
        profile = _profile(MEASUREMENT)
        session = FakeSession()

        with self.assertRaises(ValueError):
            validate_profile(
                profile,
                session,
                policy=ValidationPolicy(
                    skipped_operation_ids=frozenset({"unknown::query"})
                ),
            )

        self.assertEqual(session.events, [])
        self.assertEqual(session.timeout, 777)


class ValidationPersistenceTests(unittest.TestCase):
    def test_progress_and_result_preserve_physical_identity_snapshot(
        self,
    ) -> None:
        profile = _profile(MEASUREMENT)
        identity = InstrumentIdentity(
            raw="Example,VALIDATOR-1,SERIAL-7,3.60",
            manufacturer="Example",
            model="VALIDATOR-1",
            serial="SERIAL-7",
            firmware="3.60",
        )
        progress = create_validation_progress(
            profile,
            "TCPIP::7::INSTR",
            identity,
        )
        progress = replace(
            progress,
            manual_probes=(
                ManualProbeEvidence(
                    candidate_key="manual-1::command-1",
                    manual_id="manual-1",
                    command_id="command-1",
                    command_pattern="MEASure?",
                    query_command="MEAS?",
                    manual_page=42,
                    status="response",
                    response="1.23",
                    message="응답만 확인, 기능 미승격",
                ),
            ),
        )
        with tempfile.TemporaryDirectory() as directory:
            progress_path = Path(directory) / "identity-progress.json"
            result_path = Path(directory) / "identity-result.json"
            save_validation_progress(progress_path, progress)
            loaded_progress = load_validation_progress(progress_path)
            self.assertEqual(loaded_progress.identity_model, "VALIDATOR-1")
            self.assertEqual(loaded_progress.identity_serial, "SERIAL-7")
            self.assertEqual(
                loaded_progress.manual_probes,
                progress.manual_probes,
            )

            result = build_validation_result(progress)
            save_validation_result(result_path, result)
            loaded_result = load_validation_result(result_path)
            self.assertEqual(loaded_result.identity_firmware, "3.60")
            self.assertEqual(loaded_result.identity_raw, identity.raw)
            self.assertEqual(
                loaded_result.manual_probes,
                progress.manual_probes,
            )

    def test_progress_and_result_json_round_trip_with_operation_evidence(self) -> None:
        profile = _profile(FREQUENCY, WRITE_ONLY)
        session = FakeSession()
        with tempfile.TemporaryDirectory() as directory:
            progress_path = Path(directory) / "progress.json"
            result_path = Path(directory) / "result.json"
            progress = validate_profile(
                profile,
                session,
                policy=ValidationPolicy(
                    operation_arguments={
                        "source.frequency::set": {"value": 200},
                        "source.write_only::set": {"value": 2},
                    }
                ),
                on_progress=progress_file_checkpoint(progress_path),
            )

            checkpointed = load_validation_progress(progress_path)
            self.assertEqual(checkpointed, progress)
            save_validation_progress(progress_path, progress)
            self.assertEqual(load_validation_progress(progress_path), progress)

            result = build_validation_result(progress)
            save_validation_result(result_path, result)
            loaded_result = load_validation_result(result_path)
            self.assertEqual(loaded_result, result)
            payload = json.loads(result_path.read_text(encoding="utf-8"))
            self.assertEqual(
                payload["compatible_operation_ids"],
                [
                    "source.frequency::set",
                    "source.frequency::query",
                ],
            )
            self.assertIn("operations", payload)
            self.assertTrue(
                any(
                    operation["original_response"] == "100"
                    for operation in payload["operations"]
                )
            )

    def test_tampered_result_summary_is_rejected(self) -> None:
        profile = _profile(MEASUREMENT)
        session = FakeSession()
        result = build_validation_result(validate_profile(profile, session))
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "result.json"
            save_validation_result(path, result)
            payload = json.loads(path.read_text(encoding="utf-8"))
            payload["compatible_operation_ids"] = ["invented::query"]
            path.write_text(
                json.dumps(payload, ensure_ascii=False),
                encoding="utf-8",
            )

            with self.assertRaises(ValueError):
                load_validation_result(path)

    def test_tampered_pass_without_operation_evidence_is_rejected(self) -> None:
        profile = _profile(WRITE_ONLY)
        result = build_validation_result(
            create_validation_progress(profile, "TCPIP::1::INSTR")
        )
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "result.json"
            save_validation_result(path, result)
            payload = json.loads(path.read_text(encoding="utf-8"))
            operation = payload["operations"][0]
            operation["status"] = "pass"
            payload["compatible_capability_ids"] = ["source.write_only"]
            payload["fully_compatible_capability_ids"] = ["source.write_only"]
            payload["compatible_operation_ids"] = [
                "source.write_only::set"
            ]
            payload["unresolved_operation_ids"] = []
            payload["status_counts"]["pending"] = 0
            payload["status_counts"]["pass"] = 1
            payload["scan_complete"] = True
            payload["fully_resolved"] = True
            path.write_text(
                json.dumps(payload, ensure_ascii=False),
                encoding="utf-8",
            )

            with self.assertRaisesRegex(ValueError, "requires at least one attempt"):
                load_validation_result(path)


if __name__ == "__main__":
    unittest.main()
