from __future__ import annotations

import json
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch

from scpi_automation.identity import (
    DeviceCategory,
    InstrumentIdentity,
    profile_by_id,
)
from scpi_automation.validation import (
    LocalExtensionParameter,
    LocalExtensionRegistry,
    ManualCommandCandidate,
    ManualSource,
    OperationKind,
    OperationStatus,
    OPTION_STATE_QUERIED,
    attest_local_extension,
    bind_local_extension_options,
    load_local_extension_registry,
    local_extension_registry_from_dict,
    local_extension_registry_to_dict,
    promote_local_extension,
    query_extension_draft,
    save_local_extension_registry,
    typed_extension_draft,
    validate_local_extension,
)
from scpi_automation.routine import (
    SelectedInstrument,
    feature_by_id,
    features_for,
    local_extension_features_for,
    select_feature,
)


class _ExtensionSession:
    def __init__(self) -> None:
        self.timeout = 1000
        self.value = "10"
        self.mode = "WRIT"
        self.commands: list[str] = []

    def query(self, command: str) -> str:
        self.commands.append(command)
        if command == "*IDN?":
            return "Rohde&Schwarz,FSV30,SERIAL-1,3.60"
        if command == "*OPT?":
            return "K54"
        if command == "SYST:ERR?":
            return '0,"No error"'
        if command == "MEAS?":
            return "1.25"
        if command == "CONF?":
            return self.value
        if command == "MODE?":
            return self.mode
        raise RuntimeError(f"unexpected query: {command}")

    def write(self, command: str) -> object:
        self.commands.append(command)
        if command.startswith("CONF "):
            self.value = command.split(" ", 1)[1]
            return None
        if command.startswith("MODE "):
            self.mode = command.split(" ", 1)[1]
            return None
        raise RuntimeError(f"unexpected write: {command}")


def _candidate(
    *,
    pattern: str = "MEASure?",
    probe: str = "MEAS?",
    policy: str = "query_explicit",
) -> ManualCommandCandidate:
    source = ManualSource(
        manual_id="example_manual_v1",
        title="Example Programming Manual",
        document_reference="EX-100",
        version="1",
        firmware="1.0",
        source_url="https://example.invalid/manual.pdf",
        index_pdf_pages=(10,),
    )
    return ManualCommandCandidate(
        profile_id="rs_fsv_fsva",
        command_id="example.manual.measure",
        command_pattern=pattern,
        command_group="MEAS",
        manual_page=100,
        query_scpi_candidate=probe,
        query_support="manual_explicit",
        write_support="unknown",
        probe_policy=policy,
        verification="manual_index_candidate",
        source=source,
    )


class LocalExtensionTests(unittest.TestCase):
    def setUp(self) -> None:
        self.profile = profile_by_id("rs_fsv_fsva")
        self.assertIsNotNone(self.profile)
        self.identity = InstrumentIdentity(
            raw="Rohde&Schwarz,FSV30,SERIAL-1,3.60",
            manufacturer="Rohde&Schwarz",
            model="FSV30",
            serial="SERIAL-1",
            firmware="3.60",
        )

    def test_query_candidate_requires_typed_validation_before_promotion(
        self,
    ) -> None:
        draft = query_extension_draft(
            _candidate(),
            self.identity,
            DeviceCategory.SPECTRUM_ANALYZER,
            label_ko="매뉴얼 측정값 읽기",
            response_type="float",
            option_response="K54",
        )
        session = _ExtensionSession()
        result = validate_local_extension(
            draft,
            self.profile,
            session,
            timeout_ms=1500,
        )

        self.assertEqual(
            result.compatible_operation_ids,
            (f"{draft.capability_id}::query",),
        )
        operation = result.operations[0]
        self.assertEqual(operation.status, OperationStatus.PASS)
        self.assertEqual(operation.response, "1.25")
        self.assertEqual(operation.validation_mode, "automatic_query")

        registry = promote_local_extension(draft, result)
        self.assertIsNotNone(
            registry.by_operation_id(result.compatible_operation_ids[0])
        )
        self.assertEqual(
            registry.remove(draft.extension_id).records,
            (),
        )

    def test_identity_mismatch_stops_before_candidate_command(self) -> None:
        draft = query_extension_draft(
            _candidate(),
            self.identity,
            DeviceCategory.SPECTRUM_ANALYZER,
            label_ko="매뉴얼 측정값 읽기",
            response_type="float",
            option_response="K54",
        )

        class WrongSession(_ExtensionSession):
            def query(self, command: str) -> str:
                self.commands.append(command)
                if command == "*IDN?":
                    return "Rohde&Schwarz,FSV30,OTHER-SERIAL,3.60"
                raise AssertionError(
                    "candidate command must not run after identity mismatch"
                )

        session = WrongSession()
        with self.assertRaisesRegex(ValueError, "does not match"):
            validate_local_extension(
                draft,
                self.profile,
                session,
            )
        self.assertEqual(session.commands, ["*IDN?"])

    def test_option_mismatch_stops_before_candidate_command(self) -> None:
        draft = query_extension_draft(
            _candidate(),
            self.identity,
            DeviceCategory.SPECTRUM_ANALYZER,
            label_ko="Option-bound read",
            response_type="float",
            option_response="K54",
        )

        class WrongOptionSession(_ExtensionSession):
            def query(self, command: str) -> str:
                if command == "*OPT?":
                    self.commands.append(command)
                    return "K55"
                return super().query(command)

        session = WrongOptionSession()
        with self.assertRaisesRegex(ValueError, "option response"):
            validate_local_extension(
                draft,
                self.profile,
                session,
            )
        self.assertEqual(session.commands, ["*IDN?", "*OPT?"])

    def test_reversible_set_must_pass_query_write_readback_and_restore(
        self,
    ) -> None:
        candidate = _candidate(pattern="CONFigure", probe="CONF?")
        draft = typed_extension_draft(
            candidate,
            self.identity,
            DeviceCategory.SPECTRUM_ANALYZER,
            operation_kind=OperationKind.SET,
            label_ko="로컬 설정값",
            command_template="CONF {value}",
            readback_query="CONF?",
            readback_response_type="float",
            parameters=(
                LocalExtensionParameter(
                    name="value",
                    value_type="float",
                    minimum=0,
                    maximum=100,
                ),
            ),
            probe_arguments={"value": "20"},
            risk_level="hazardous",
            option_response="K54",
        )
        session = _ExtensionSession()
        result = validate_local_extension(
            draft,
            self.profile,
            session,
            approved_hazardous=True,
        )

        self.assertEqual(len(result.compatible_operation_ids), 2)
        set_result = next(
            operation
            for operation in result.operations
            if operation.kind is OperationKind.SET
        )
        self.assertTrue(set_result.restore_attempted)
        self.assertTrue(set_result.restored)
        self.assertEqual(session.value, "10")
        self.assertGreaterEqual(len(set_result.sent_commands), 4)

    def test_fixed_mnemonic_set_can_be_read_back_and_restored(self) -> None:
        draft = typed_extension_draft(
            _candidate(pattern="MODE", probe="MODE?"),
            self.identity,
            DeviceCategory.SPECTRUM_ANALYZER,
            operation_kind="set",
            label_ko="Max Hold",
            command_template="MODE MAXH",
            readback_query="MODE?",
            readback_response_type="string",
            risk_level="hazardous",
            option_response="K54",
        )
        session = _ExtensionSession()

        result = validate_local_extension(
            draft,
            self.profile,
            session,
            approved_hazardous=True,
        )

        self.assertEqual(len(result.compatible_operation_ids), 2)
        self.assertEqual(session.mode, "WRIT")

        clear_write = typed_extension_draft(
            _candidate(pattern="MODE", probe="MODE?"),
            self.identity,
            DeviceCategory.SPECTRUM_ANALYZER,
            operation_kind="set",
            label_ko="Clear Write",
            command_template="MODE WRIT",
            readback_query="MODE?",
            readback_response_type="string",
            risk_level="hazardous",
            option_response="K54",
        )
        self.assertNotEqual(
            draft.extension_id,
            clear_write.extension_id,
        )

    def test_raw_or_partial_evidence_cannot_be_promoted(self) -> None:
        draft = typed_extension_draft(
            _candidate(pattern="CONFigure", probe="CONF?"),
            self.identity,
            DeviceCategory.SPECTRUM_ANALYZER,
            operation_kind="set",
            label_ko="로컬 설정값",
            command_template="CONF {value}",
            readback_query="CONF?",
            readback_response_type="float",
            parameters=(
                LocalExtensionParameter(
                    name="value",
                    value_type="float",
                ),
            ),
            probe_arguments={"value": "20"},
            option_response="K54",
        )
        session = _ExtensionSession()
        result = validate_local_extension(
            draft,
            self.profile,
            session,
            approved_hazardous=True,
        )
        query_only = result.compatible_operation_ids[:1]
        tampered = type(result)(
            **{
                field: getattr(result, field)
                for field in result.__dataclass_fields__
                if field != "compatible_operation_ids"
            },
            compatible_operation_ids=query_only,
        )
        with self.assertRaisesRegex(
            ValueError,
            "Every operation",
        ):
            promote_local_extension(draft, tampered)

    def test_tampered_set_evidence_fails_promotion_and_deserialization(
        self,
    ) -> None:
        draft = typed_extension_draft(
            _candidate(pattern="CONFigure", probe="CONF?"),
            self.identity,
            DeviceCategory.SPECTRUM_ANALYZER,
            operation_kind="set",
            label_ko="Tamper-resistant setting",
            command_template="CONF {value}",
            readback_query="CONF?",
            readback_response_type="float",
            parameters=(
                LocalExtensionParameter(
                    name="value",
                    value_type="float",
                ),
            ),
            probe_arguments={"value": "20"},
            option_response="K54",
        )
        result = validate_local_extension(
            draft,
            self.profile,
            _ExtensionSession(),
            approved_hazardous=True,
        )
        tampered_operations = tuple(
            replace(
                operation,
                sent_commands=(
                    "CONF 20",
                    "CONF 20",
                    "CONF 10",
                    "CONF 10",
                ),
            )
            if operation.kind is OperationKind.SET
            else operation
            for operation in result.operations
        )
        tampered_result = replace(
            result,
            operations=tampered_operations,
        )

        with self.assertRaisesRegex(
            ValueError,
            "SET PASS requires write/readback/restore evidence",
        ):
            promote_local_extension(draft, tampered_result)

        shape_preserving_forgery = replace(
            result,
            operations=tuple(
                replace(
                    operation,
                    sent_commands=(
                        "OUTP ON",
                        "CONF?",
                        "OUTP OFF",
                        "CONF?",
                    ),
                )
                if operation.kind is OperationKind.SET
                else operation
                for operation in result.operations
            ),
        )
        with self.assertRaisesRegex(
            ValueError,
            "transcript.*rendered",
        ):
            promote_local_extension(draft, shape_preserving_forgery)

        payload = local_extension_registry_to_dict(
            promote_local_extension(draft, result)
        )
        serialized_operations = payload["records"][0]["validation_result"][
            "operations"
        ]
        serialized_set = next(
            operation
            for operation in serialized_operations
            if operation["kind"] == OperationKind.SET.value
        )
        serialized_set["sent_commands"] = [
            "CONF 20",
            "CONF 20",
            "CONF 10",
            "CONF 10",
        ]

        with self.assertRaisesRegex(
            ValueError,
            "SET PASS requires write/readback/restore evidence",
        ):
            local_extension_registry_from_dict(payload)

    def test_query_evidence_must_match_the_exact_rendered_command(self) -> None:
        draft = query_extension_draft(
            _candidate(),
            self.identity,
            DeviceCategory.SPECTRUM_ANALYZER,
            label_ko="Exact query transcript",
            response_type="float",
            option_response="K54",
        )
        result = validate_local_extension(
            draft,
            self.profile,
            _ExtensionSession(),
        )
        forged = replace(
            result,
            operations=tuple(
                replace(operation, sent_commands=("FAKE?",))
                for operation in result.operations
            ),
        )

        with self.assertRaisesRegex(
            ValueError,
            "QUERY PASS transcript.*rendered command",
        ):
            promote_local_extension(draft, forged)

    def test_registry_round_trip_keeps_identity_source_and_evidence(self) -> None:
        draft = query_extension_draft(
            _candidate(),
            self.identity,
            DeviceCategory.SPECTRUM_ANALYZER,
            label_ko="매뉴얼 측정값 읽기",
            response_type="float",
            option_response="K54",
        )
        result = validate_local_extension(
            draft,
            self.profile,
            _ExtensionSession(),
        )
        registry = promote_local_extension(draft, result)

        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "extensions.json"
            save_local_extension_registry(registry, path)
            loaded = load_local_extension_registry(path)
            payload = json.loads(path.read_text(encoding="utf-8"))

        self.assertEqual(loaded, registry)
        self.assertEqual(
            payload["records"][0]["definition"]["source"]["manual_page"],
            100,
        )
        self.assertEqual(
            payload["records"][0]["validation_result"]["identity"]["serial"],
            "SERIAL-1",
        )
        wrong_identity = InstrumentIdentity(
            raw="Rohde&Schwarz,FSV30,OTHER,3.60",
            manufacturer="Rohde&Schwarz",
            model="FSV30",
            serial="OTHER",
            firmware="3.60",
        )
        self.assertEqual(
            loaded.for_identity("rs_fsv_fsva", wrong_identity),
            (),
        )

    def test_registry_file_authentication_fails_closed_on_tampering(
        self,
    ) -> None:
        draft = query_extension_draft(
            _candidate(),
            self.identity,
            DeviceCategory.SPECTRUM_ANALYZER,
            label_ko="Authenticated query",
            response_type="float",
            option_response="K54",
        )
        registry = promote_local_extension(
            draft,
            validate_local_extension(
                draft,
                self.profile,
                _ExtensionSession(),
            ),
        )

        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "custom-extensions.json"
            key_path = path.with_name(path.name + ".key")
            save_local_extension_registry(registry, path)
            original_text = path.read_text(encoding="utf-8")
            original_key = key_path.read_bytes()

            payload_tampering = original_text.replace(
                '"response": "1.25"',
                '"response": "9.25"',
                1,
            )
            self.assertNotEqual(payload_tampering, original_text)
            path.write_text(payload_tampering, encoding="utf-8")
            with self.assertRaisesRegex(
                ValueError,
                "authentication failed",
            ):
                load_local_extension_registry(path)

            path.write_text(original_text, encoding="utf-8")
            unsigned_payload = json.loads(original_text)
            del unsigned_payload["authentication"]
            path.write_text(
                json.dumps(unsigned_payload, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
            with self.assertRaisesRegex(
                ValueError,
                "authentication is missing",
            ):
                load_local_extension_registry(path)

            path.write_text(original_text, encoding="utf-8")
            authentication = json.loads(original_text)["authentication"]
            original_tag = authentication["tag"]
            changed_character = "0" if original_tag[0] != "0" else "1"
            tampered_tag = changed_character + original_tag[1:]
            path.write_text(
                original_text.replace(original_tag, tampered_tag, 1),
                encoding="utf-8",
            )
            with self.assertRaisesRegex(
                ValueError,
                "authentication failed",
            ):
                load_local_extension_registry(path)

            path.write_text(original_text, encoding="utf-8")
            save_local_extension_registry(
                load_local_extension_registry(path),
                path,
            )
            self.assertNotEqual(key_path.read_bytes(), original_key)
            self.assertEqual(load_local_extension_registry(path), registry)
            key_path.unlink()
            with self.assertRaisesRegex(
                ValueError,
                "authentication key is missing",
            ):
                load_local_extension_registry(path)

    def test_signed_registry_replay_is_rejected_by_generation_state(
        self,
    ) -> None:
        draft = query_extension_draft(
            _candidate(),
            self.identity,
            DeviceCategory.SPECTRUM_ANALYZER,
            label_ko="Replay-resistant query",
            response_type="float",
            option_response="K54",
        )
        registry = promote_local_extension(
            draft,
            validate_local_extension(
                draft,
                self.profile,
                _ExtensionSession(),
            ),
        )

        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "extensions.json"
            state_path = path.with_name(path.name + ".state")
            save_local_extension_registry(registry, path)
            first_registry = path.read_bytes()
            first_state = state_path.read_bytes()
            first_generation = json.loads(
                first_registry.decode("utf-8")
            )["registry_generation"]

            empty_registry = load_local_extension_registry(path)
            for record in empty_registry.records:
                empty_registry = empty_registry.remove(
                    record.definition.extension_id
                )
            save_local_extension_registry(empty_registry, path)
            second_generation = json.loads(
                path.read_text(encoding="utf-8")
            )["registry_generation"]
            self.assertGreater(second_generation, first_generation)
            self.assertTrue(state_path.is_file())
            self.assertEqual(
                load_local_extension_registry(path).records,
                (),
            )

            path.write_bytes(first_registry)
            with self.assertRaisesRegex(
                ValueError,
                "rollback or replay",
            ):
                load_local_extension_registry(path)
            with self.assertRaisesRegex(
                ValueError,
                "rollback or replay",
            ):
                save_local_extension_registry(registry, path)

            state_path.write_bytes(first_state)
            with self.assertRaisesRegex(
                ValueError,
                "rollback or replay",
            ):
                load_local_extension_registry(path)

    def test_stale_registry_snapshot_cannot_restore_a_revoked_extension(
        self,
    ) -> None:
        first_draft = query_extension_draft(
            _candidate(),
            self.identity,
            DeviceCategory.SPECTRUM_ANALYZER,
            label_ko="First query",
            response_type="float",
            option_response="K54",
        )
        first_registry = promote_local_extension(
            first_draft,
            validate_local_extension(
                first_draft,
                self.profile,
                _ExtensionSession(),
            ),
        )
        second_draft = query_extension_draft(
            replace(
                _candidate(),
                command_id="example.manual.measure.second",
                manual_page=101,
            ),
            self.identity,
            DeviceCategory.SPECTRUM_ANALYZER,
            label_ko="Second query",
            response_type="float",
            option_response="K54",
        )

        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "extensions.json"
            save_local_extension_registry(first_registry, path)
            stale = load_local_extension_registry(path)

            revoked = load_local_extension_registry(path).remove(
                first_draft.extension_id
            )
            save_local_extension_registry(revoked, path)
            self.assertEqual(
                load_local_extension_registry(path).records,
                (),
            )

            stale_with_second = promote_local_extension(
                second_draft,
                validate_local_extension(
                    second_draft,
                    self.profile,
                    _ExtensionSession(),
                ),
                stale,
            )
            with self.assertRaisesRegex(
                ValueError,
                "another program window",
            ):
                save_local_extension_registry(stale_with_second, path)
            self.assertEqual(
                load_local_extension_registry(path).records,
                (),
            )

    def test_registry_rejects_definition_tampering_after_validation(self) -> None:
        draft = typed_extension_draft(
            _candidate(pattern="CONFigure", probe="CONF?"),
            self.identity,
            DeviceCategory.SPECTRUM_ANALYZER,
            operation_kind="set",
            label_ko="로컬 설정값",
            command_template="CONF {value}",
            readback_query="CONF?",
            readback_response_type="float",
            parameters=(
                LocalExtensionParameter(
                    name="value",
                    value_type="float",
                    minimum=0,
                    maximum=100,
                ),
            ),
            probe_arguments={"value": "20"},
            option_response="K54",
        )
        registry = promote_local_extension(
            draft,
            validate_local_extension(
                draft,
                self.profile,
                _ExtensionSession(),
                approved_hazardous=True,
            ),
        )
        payload = local_extension_registry_to_dict(registry)
        payload["records"][0]["definition"]["capability"]["parameters"][0][
            "maximum"
        ] = 1_000_000

        with self.assertRaisesRegex(
            ValueError,
            "another extension|changed after live validation",
        ):
            local_extension_registry_from_dict(payload)

    def test_set_without_readback_or_parameter_metadata_is_rejected(self) -> None:
        with self.assertRaisesRegex(ValueError, "readback"):
            typed_extension_draft(
                _candidate(pattern="CONFigure", probe="CONF?"),
                self.identity,
                DeviceCategory.SPECTRUM_ANALYZER,
                operation_kind="set",
                label_ko="잘못된 설정",
                command_template="CONF {value}",
                parameters=(
                    LocalExtensionParameter(
                        name="value",
                        value_type="float",
                    ),
                ),
                probe_arguments={"value": "20"},
            )

    def test_only_promoted_allowlisted_extension_appears_in_routine(self) -> None:
        draft = query_extension_draft(
            _candidate(),
            self.identity,
            DeviceCategory.SPECTRUM_ANALYZER,
            label_ko="매뉴얼 측정값 읽기",
            response_type="float",
            option_response="K54",
        )
        result = validate_local_extension(
            draft,
            self.profile,
            _ExtensionSession(),
        )
        registry = promote_local_extension(draft, result)
        operation_id = result.compatible_operation_ids[0]

        direct = local_extension_features_for(
            self.profile.profile_id,
            (operation_id,),
            registry=registry,
        )
        self.assertEqual(len(direct), 1)
        self.assertEqual(direct[0].capability_id, draft.capability_id)

        with patch(
            "scpi_automation.routine.catalog."
            "_load_extensions_fail_closed",
            return_value=registry,
        ):
            visible = features_for(
                self.profile.category,
                self.profile.profile_id,
                compatibility_status="hardware_validated_partial",
                compatible_operation_ids=(operation_id,),
            )
            self.assertEqual(visible, direct)
            self.assertEqual(
                feature_by_id(
                    direct[0].feature_id,
                    self.profile.profile_id,
                ),
                direct[0],
            )

        self.assertEqual(
            local_extension_features_for(
                self.profile.profile_id,
                (),
                registry=registry,
            ),
            (),
        )

        exact_instrument = SelectedInstrument(
            resource="TCPIP0::192.0.2.40::inst0::INSTR",
            category=self.profile.category,
            manufacturer=self.identity.manufacturer,
            model=self.identity.model,
            serial=self.identity.serial,
            firmware=self.identity.firmware,
            raw_idn=self.identity.raw,
            profile_id=self.profile.profile_id,
            compatibility_status="hardware_validated_partial",
            compatible_operation_ids=(operation_id,),
            option_response="K54",
            option_state="queried",
        )
        wrong_instrument = SelectedInstrument(
            resource="TCPIP0::192.0.2.41::inst0::INSTR",
            category=self.profile.category,
            manufacturer=self.identity.manufacturer,
            model=self.identity.model,
            serial="OTHER-SERIAL",
            firmware=self.identity.firmware,
            raw_idn=(
                "Rohde&Schwarz,FSV30,OTHER-SERIAL,3.60"
            ),
            profile_id=self.profile.profile_id,
            compatibility_status="hardware_validated_partial",
            compatible_operation_ids=(operation_id,),
            option_response="K54",
            option_state="queried",
        )
        wrong_option_instrument = SelectedInstrument(
            resource="TCPIP0::192.0.2.42::inst0::INSTR",
            category=self.profile.category,
            manufacturer=self.identity.manufacturer,
            model=self.identity.model,
            serial=self.identity.serial,
            firmware=self.identity.firmware,
            raw_idn=self.identity.raw,
            profile_id=self.profile.profile_id,
            compatibility_status="hardware_validated_partial",
            compatible_operation_ids=(operation_id,),
            option_response="K55",
            option_state="queried",
        )
        wrong_raw_idn_instrument = replace(
            exact_instrument,
            resource="TCPIP0::192.0.2.43::inst0::INSTR",
            raw_idn=(
                "Rohde&Schwarz,FSV30,SERIAL-1,3.60,EXTRA"
            ),
        )
        with patch(
            "scpi_automation.routine.catalog."
            "_load_extensions_fail_closed",
            return_value=registry,
        ):
            selected = select_feature(
                exact_instrument,
                direct[0].feature_id,
            )
            self.assertEqual(selected.feature_id, direct[0].feature_id)
            with self.assertRaisesRegex(ValueError, "다른 제조사"):
                select_feature(
                    wrong_instrument,
                    direct[0].feature_id,
                )
            with self.assertRaisesRegex(ValueError, "옵션"):
                select_feature(
                    wrong_option_instrument,
                    direct[0].feature_id,
                )
            with self.assertRaisesRegex(ValueError, "다른 제조사"):
                select_feature(
                    wrong_raw_idn_instrument,
                    direct[0].feature_id,
                )

    def test_high_risk_execute_requires_exact_approval_and_evidence(self) -> None:
        draft = typed_extension_draft(
            _candidate(pattern="INITiate", probe=""),
            self.identity,
            DeviceCategory.SPECTRUM_ANALYZER,
            operation_kind="execute",
            label_ko="수동 실행 기능",
            command_template="INIT",
            risk_level="high",
            option_response="K54",
        )
        with self.assertRaisesRegex(ValueError, "separate exact approval"):
            attest_local_extension(
                draft,
                self.profile,
                _ExtensionSession(),
                passed=True,
                note="Isolated DUT and observed one sweep.",
            )

        result = attest_local_extension(
            draft,
            self.profile,
            _ExtensionSession(),
            passed=True,
            note="Isolated DUT and observed one sweep.",
            hazardous_approved=True,
        )

        self.assertEqual(len(result.compatible_operation_ids), 1)
        self.assertEqual(
            result.operations[0].validation_mode,
            "manual_operator_hazardous",
        )
        promote_local_extension(draft, result)
        false_transmission_claim = replace(
            result,
            operations=(
                replace(
                    result.operations[0],
                    sent_commands=("INIT",),
                ),
            ),
        )
        with self.assertRaisesRegex(
            ValueError,
            "must not claim an automatically transmitted command",
        ):
            promote_local_extension(draft, false_transmission_claim)

    def test_critical_set_is_blocked_until_exact_operation_is_approved(
        self,
    ) -> None:
        draft = typed_extension_draft(
            _candidate(pattern="CONFigure", probe="CONF?"),
            self.identity,
            DeviceCategory.SPECTRUM_ANALYZER,
            operation_kind="set",
            label_ko="중요 설정값",
            command_template="CONF {value}",
            readback_query="CONF?",
            readback_response_type="float",
            parameters=(
                LocalExtensionParameter(
                    name="value",
                    value_type="float",
                ),
            ),
            probe_arguments={"value": "20"},
            risk_level="critical",
            option_response="K54",
        )
        blocked = validate_local_extension(
            draft,
            self.profile,
            _ExtensionSession(),
        )
        set_result = next(
            item
            for item in blocked.operations
            if item.kind is OperationKind.SET
        )
        self.assertEqual(set_result.status, OperationStatus.UNSAFE)

        approved = validate_local_extension(
            draft,
            self.profile,
            _ExtensionSession(),
            approved_hazardous=True,
        )
        self.assertEqual(len(approved.compatible_operation_ids), 2)

    def test_manual_only_candidate_cannot_become_automatic_query(
        self,
    ) -> None:
        with self.assertRaisesRegex(ValueError, "manual_only"):
            query_extension_draft(
                _candidate(
                    pattern="*TST?",
                    probe="*TST?",
                    policy="manual_only",
                ),
                self.identity,
                DeviceCategory.SPECTRUM_ANALYZER,
                label_ko="Self Test",
                option_response="K54",
            )

    def test_manual_writes_cannot_self_declare_low_risk(self) -> None:
        for risk in ("low", "medium"):
            with self.subTest(risk=risk):
                with self.assertRaisesRegex(
                    ValueError,
                    "treated as high risk",
                ):
                    typed_extension_draft(
                        _candidate(
                            pattern="OUTPut",
                            probe="OUTP?",
                        ),
                        self.identity,
                        DeviceCategory.SPECTRUM_ANALYZER,
                        operation_kind="set",
                        label_ko="RF Output",
                        command_template="OUTP {state}",
                        readback_query="OUTP?",
                        readback_response_type="boolean",
                        parameters=(
                            LocalExtensionParameter(
                                name="state",
                                value_type="boolean",
                            ),
                        ),
                        probe_arguments={"state": "false"},
                        risk_level=risk,
                        option_response="K54",
                    )

    def test_option_bound_registry_never_matches_missing_option_state(
        self,
    ) -> None:
        draft = query_extension_draft(
            _candidate(),
            self.identity,
            DeviceCategory.SPECTRUM_ANALYZER,
            label_ko="Option-bound read",
            response_type="float",
            option_response="K54",
        )
        registry = promote_local_extension(
            draft,
            validate_local_extension(
                draft,
                self.profile,
                _ExtensionSession(),
            ),
        )
        self.assertEqual(
            registry.for_identity(
                self.profile.profile_id,
                self.identity,
            ),
            (),
        )
        self.assertEqual(
            len(
                registry.for_identity(
                    self.profile.profile_id,
                    self.identity,
                    "K54",
                    OPTION_STATE_QUERIED,
                )
            ),
            1,
        )

    def test_live_option_probe_rekeys_an_unbound_draft(self) -> None:
        draft = query_extension_draft(
            _candidate(),
            self.identity,
            DeviceCategory.SPECTRUM_ANALYZER,
            label_ko="Unbound read",
            response_type="float",
        )
        bound = bind_local_extension_options(
            draft,
            _ExtensionSession(),
        )
        self.assertEqual(
            bound.identity_options_state,
            OPTION_STATE_QUERIED,
        )
        self.assertEqual(bound.identity_options, "K54")
        self.assertNotEqual(bound.extension_id, draft.extension_id)
        result = validate_local_extension(
            bound,
            self.profile,
            _ExtensionSession(),
        )
        promote_local_extension(bound, result)

    def test_local_routine_arguments_are_locked_to_exact_probe(
        self,
    ) -> None:
        candidate = _candidate(pattern="CONFigure", probe="CONF?")
        parameters = (
            LocalExtensionParameter(
                name="value",
                value_type="float",
                minimum=0,
                maximum=1e308,
            ),
        )
        draft = typed_extension_draft(
            candidate,
            self.identity,
            DeviceCategory.SPECTRUM_ANALYZER,
            operation_kind="set",
            label_ko="Probe-locked setting",
            command_template="CONF {value}",
            readback_query="CONF?",
            readback_response_type="float",
            parameters=parameters,
            probe_arguments={"value": "20"},
            option_response="K54",
        )
        other_probe = typed_extension_draft(
            candidate,
            self.identity,
            DeviceCategory.SPECTRUM_ANALYZER,
            operation_kind="set",
            label_ko="Probe-locked setting",
            command_template="CONF {value}",
            readback_query="CONF?",
            readback_response_type="float",
            parameters=parameters,
            probe_arguments={"value": "21"},
            option_response="K54",
        )
        self.assertNotEqual(draft.extension_id, other_probe.extension_id)

        result = validate_local_extension(
            draft,
            self.profile,
            _ExtensionSession(),
            approved_hazardous=True,
        )
        registry = promote_local_extension(draft, result)
        operation_id = f"{draft.capability_id}::set"
        feature = next(
            item
            for item in local_extension_features_for(
                self.profile.profile_id,
                (operation_id,),
                registry=registry,
            )
            if item.operation == "set"
        )
        instrument = SelectedInstrument(
            resource="TCPIP0::192.0.2.80::inst0::INSTR",
            category=self.profile.category,
            manufacturer=self.identity.manufacturer,
            model=self.identity.model,
            serial=self.identity.serial,
            firmware=self.identity.firmware,
            raw_idn=self.identity.raw,
            profile_id=self.profile.profile_id,
            compatibility_status="hardware_validated_partial",
            compatible_operation_ids=result.compatible_operation_ids,
            option_response="K54",
            option_state="queried",
        )
        with patch(
            "scpi_automation.routine.catalog."
            "_load_extensions_fail_closed",
            return_value=registry,
        ):
            selected = select_feature(
                instrument,
                feature.feature_id,
                arguments={"value": "20"},
            )
            self.assertEqual(dict(selected.arguments), {"value": "20"})
            for untested in ("21", "9.9e307"):
                with self.subTest(untested=untested):
                    with self.assertRaisesRegex(
                        ValueError,
                        "한 가지 시험 인수 조합",
                    ):
                        select_feature(
                            instrument,
                            feature.feature_id,
                            arguments={"value": untested},
                        )


if __name__ == "__main__":
    unittest.main()
