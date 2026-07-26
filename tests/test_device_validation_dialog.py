from __future__ import annotations

import time
import tempfile
import tkinter as tk
import unittest
from contextlib import contextmanager
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch

from scpi_automation.identity import (
    CatalogCapability,
    CatalogOperation,
    DeviceCategory,
    InstrumentIdentity,
    InstrumentProfile,
    profile_by_id,
)
from scpi_automation.transport import DiscoveryRecord, DiscoveryState
from scpi_automation.ui.device_validation_dialog import DeviceValidationDialog
from scpi_automation.validation import (
    ManualCommandCandidate,
    ManualSource,
    OperationStatus,
    load_local_extension_registry,
    query_extension_draft,
)


class _FakeSession:
    def __init__(self) -> None:
        self.timeout = 1000
        self.commands: list[str] = []

    def query(self, command: str) -> str:
        self.commands.append(command)
        if command == "*IDN?":
            return "Example Instruments,READONLY-1,SERIAL-1,1.0"
        if command == "SYST:ERR?":
            return '0,"No error"'
        if command == ":MEAS?":
            return "42.0"
        raise RuntimeError(f"unexpected query: {command}")

    def write(self, command: str) -> object:
        raise AssertionError(f"query phase must not write: {command}")


class _FsvExtensionSession:
    def __init__(self) -> None:
        self.timeout = 1000
        self.commands: list[str] = []

    def query(self, command: str) -> str:
        self.commands.append(command)
        if command == "*IDN?":
            return "Rohde&Schwarz,FSV30,SERIAL-1,3.60"
        if command == "*OPT?":
            return "K54"
        if command == "SYST:ERR?":
            return '0,"No error"'
        raise RuntimeError(f"unexpected query: {command}")

    def write(self, command: str) -> object:
        raise AssertionError(f"query extension must not write: {command}")


class DeviceValidationDialogTests(unittest.TestCase):
    def setUp(self) -> None:
        self.root = tk.Tk()
        self.root.withdraw()
        self.dialogs: list[DeviceValidationDialog] = []

    def tearDown(self) -> None:
        for dialog in self.dialogs:
            if dialog.winfo_exists():
                dialog._destroy_dialog()
        if self.root.winfo_exists():
            self.root.update_idletasks()
            self.root.destroy()

    def test_fsv_dialog_exposes_only_licensed_curated_operations(
        self,
    ) -> None:
        profile = profile_by_id("rs_fsv_fsva")
        self.assertIsNotNone(profile)
        assert profile is not None
        identity = InstrumentIdentity(
            raw="Rohde&Schwarz,FSV30,SERIAL-1,3.60",
            manufacturer="Rohde&Schwarz",
            model="FSV30",
            serial="SERIAL-1",
            firmware="3.60",
        )
        record = DiscoveryRecord(
            resource="TCPIP0::192.0.2.40::inst0::INSTR",
            interface="TCPIP0",
            state=DiscoveryState.IDENTIFIED,
            identity=identity,
        )
        dialog = DeviceValidationDialog(
            self.root,
            record=record,
            profile=profile,
            backend="",
            timeout_ms=1500,
            on_complete=lambda _result: None,
        )
        self.dialogs.append(dialog)
        self.root.update_idletasks()

        self.assertEqual(len(dialog.operation_tree.get_children()), 25)
        self.assertEqual(len(dialog._manual_candidates), 0)
        self.assertEqual(len(dialog.manual_tree.get_children()), 0)
        operation_ids = set(dialog.operation_tree.get_children())
        self.assertIn("analyzer.frequency.center::set", operation_ids)
        self.assertIn("analyzer.rbw::query", operation_ids)
        self.assertIn("measurement.acp_power.fetch::query", operation_ids)
        self.assertEqual(
            dialog.manual_summary_var.get(),
            "내장 후보 없음 · 사용자 로컬 카탈로그만 지원",
        )

    def test_option_query_is_found_by_scpi_not_capability_id(self) -> None:
        profile = profile_by_id("kikusui_pmx35_3a")
        self.assertIsNotNone(profile)
        assert profile is not None
        identity = InstrumentIdentity(
            raw="KIKUSUI,PMX35-3A,SERIAL-35,1.0",
            manufacturer="KIKUSUI",
            model="PMX35-3A",
            serial="SERIAL-35",
            firmware="1.0",
        )
        record = DiscoveryRecord(
            resource="USB0::0x0B3E::0x0001::SERIAL-35::INSTR",
            interface="USB0",
            state=DiscoveryState.IDENTIFIED,
            identity=identity,
        )
        dialog = DeviceValidationDialog(
            self.root,
            record=record,
            profile=profile,
            backend="",
            timeout_ms=1200,
            on_complete=lambda _result: None,
        )
        self.dialogs.append(dialog)

        option_operation = dialog._option_operation()
        self.assertIsNotNone(option_operation)
        assert option_operation is not None
        self.assertEqual(
            option_operation.operation_id,
            "system.options.query::query",
        )
        dialog._progress = dialog._progress.replace_operation(
            replace(
                option_operation,
                status=OperationStatus.PASS,
                validation_mode="automatic_query",
                attempts=1,
                sent_commands=("*OPT?",),
                response="0",
                original_response="0",
                message="Query completed without an instrument error",
            )
        )
        self.assertEqual(
            dialog._current_option_binding(),
            ("queried", "0"),
        )

    def test_query_stage_rechecks_idn_and_never_sends_a_write(self) -> None:
        profile = InstrumentProfile(
            profile_id="readonly_profile",
            manufacturer="Example Instruments",
            model_family="READONLY",
            models=("READONLY-1",),
            instrument_class="digital_multimeter",
            category=DeviceCategory.DIGITAL_MULTIMETER,
            idn_patterns=(),
            verification_status="candidate",
            hardware_verified=False,
            capabilities=(
                CatalogCapability(
                    capability_id="measurement.value",
                    label_ko="측정값 읽기",
                    group="measurement",
                    risk_level="low",
                    verification="candidate",
                    operations=(
                        CatalogOperation(
                            name="query",
                            scpi=":MEAS?",
                            response_type="float",
                        ),
                    ),
                ),
            ),
        )
        identity = InstrumentIdentity(
            raw="Example Instruments,READONLY-1,SERIAL-1,1.0",
            manufacturer="Example Instruments",
            model="READONLY-1",
            serial="SERIAL-1",
            firmware="1.0",
        )
        record = DiscoveryRecord(
            resource="TCPIP0::192.0.2.50::inst0::INSTR",
            interface="TCPIP0",
            state=DiscoveryState.IDENTIFIED,
            identity=identity,
        )
        session = _FakeSession()

        @contextmanager
        def session_factory(
            _resource: str,
            *,
            backend: str,
            timeout_ms: int,
        ):
            self.assertEqual(backend, "")
            session.timeout = timeout_ms
            yield session

        dialog = DeviceValidationDialog(
            self.root,
            record=record,
            profile=profile,
            backend="",
            timeout_ms=1200,
            on_complete=lambda _result: None,
            session_factory=session_factory,
        )
        self.dialogs.append(dialog)
        dialog._run_all_queries()
        deadline = time.monotonic() + 3.0
        while (
            dialog._worker is not None
            and dialog._worker.is_alive()
            and time.monotonic() < deadline
        ):
            self.root.update()
            time.sleep(0.01)
        time.sleep(0.12)
        self.root.update()

        operation = dialog._progress.operation("measurement.value::query")
        self.assertEqual(operation.status, OperationStatus.PASS)
        self.assertEqual(
            session.commands,
            [
                "*IDN?",
                "SYST:ERR?",
                ":MEAS?",
                "SYST:ERR?",
            ],
        )

    def test_manual_candidate_can_be_typed_validated_and_promoted(
        self,
    ) -> None:
        profile = profile_by_id("rs_fsv_fsva")
        self.assertIsNotNone(profile)
        assert profile is not None
        identity = InstrumentIdentity(
            raw="Rohde&Schwarz,FSV30,SERIAL-1,3.60",
            manufacturer="Rohde&Schwarz",
            model="FSV30",
            serial="SERIAL-1",
            firmware="3.60",
        )
        record = DiscoveryRecord(
            resource="TCPIP0::192.0.2.54::inst0::INSTR",
            interface="TCPIP0",
            state=DiscoveryState.IDENTIFIED,
            identity=identity,
        )
        session = _FsvExtensionSession()
        source = ManualSource(
            manual_id="private_example_manual",
            title="User-supplied private example",
            document_reference="LOCAL-ONLY",
            version="1",
            firmware="",
            source_url="https://example.invalid/private-manual",
            index_pdf_pages=(1,),
        )
        private_candidate = ManualCommandCandidate(
            profile_id="rs_fsv_fsva",
            command_id="private_example.idn",
            command_pattern="*IDN?",
            command_group="*IDN",
            manual_page=1,
            query_scpi_candidate="*IDN?",
            query_support="manual_explicit",
            write_support="unknown",
            probe_policy="query_explicit",
            verification="manual_index_candidate",
            source=source,
        )

        @contextmanager
        def session_factory(
            _resource: str,
            *,
            backend: str,
            timeout_ms: int,
        ):
            session.timeout = timeout_ms
            yield session

        with tempfile.TemporaryDirectory() as folder:
            registry_path = Path(folder) / "local_extensions.json"
            dialog = DeviceValidationDialog(
                self.root,
                record=record,
                profile=profile,
                backend="",
                timeout_ms=1200,
                on_complete=lambda _result: None,
                session_factory=session_factory,
                extension_registry_path=registry_path,
            )
            self.dialogs.append(dialog)
            dialog._manual_candidates = (private_candidate,)
            dialog._manual_by_id = {
                dialog._manual_candidate_key(private_candidate):
                private_candidate
            }
            dialog._refresh_manual_tree()
            candidate = private_candidate
            candidate_key = dialog._manual_candidate_key(candidate)
            definition = query_extension_draft(
                candidate,
                identity,
                profile.category,
                label_ko="장비 이름표 다시 읽기",
                response_type="string",
            )
            dialog.manual_tree.selection_set(candidate_key)

            with (
                patch(
                    "scpi_automation.ui.device_validation_dialog."
                    "ask_local_extension_definition",
                    return_value=definition,
                ),
                patch(
                    "scpi_automation.ui.device_validation_dialog."
                    "messagebox.askyesno",
                    return_value=True,
                ),
                patch(
                    "scpi_automation.ui.device_validation_dialog."
                    "messagebox.showinfo",
                ),
            ):
                dialog._start_manual_extension_flow()
                deadline = time.monotonic() + 3.0
                while time.monotonic() < deadline:
                    self.root.update()
                    if (
                        dialog._worker is not None
                        and not dialog._worker.is_alive()
                        and registry_path.exists()
                    ):
                        break
                    time.sleep(0.01)
                self.root.update()

            registry = load_local_extension_registry(registry_path)

        self.assertEqual(len(registry.records), 1)
        operation_id = registry.records[0].compatible_operation_ids[0]
        self.assertIsNotNone(registry.by_operation_id(operation_id))
        self.assertEqual(
            dialog._progress.operation(operation_id).status,
            OperationStatus.PASS,
        )
        self.assertTrue(
            str(
                dialog.manual_tree.item(candidate_key, "values")[0]
            ).startswith("기능 등록")
        )

    def test_execute_operation_accepts_explicit_operator_evidence(self) -> None:
        profile = InstrumentProfile(
            profile_id="manual_execute_profile",
            manufacturer="Example Instruments",
            model_family="READONLY",
            models=("READONLY-1",),
            instrument_class="digital_multimeter",
            category=DeviceCategory.DIGITAL_MULTIMETER,
            idn_patterns=(),
            verification_status="candidate",
            hardware_verified=False,
            capabilities=(
                CatalogCapability(
                    capability_id="marker.peak_search",
                    label_ko="Peak Search",
                    group="marker",
                    risk_level="low",
                    verification="candidate",
                    operations=(
                        CatalogOperation(
                            name="execute",
                            scpi="CALC:MARK1:MAX",
                        ),
                    ),
                ),
            ),
        )
        identity = InstrumentIdentity(
            raw="Example Instruments,READONLY-1,SERIAL-1,1.0",
            manufacturer="Example Instruments",
            model="READONLY-1",
            serial="SERIAL-1",
            firmware="1.0",
        )
        dialog = DeviceValidationDialog(
            self.root,
            record=DiscoveryRecord(
                resource="TCPIP0::192.0.2.51::inst0::INSTR",
                interface="TCPIP0",
                state=DiscoveryState.IDENTIFIED,
                identity=identity,
            ),
            profile=profile,
            backend="",
            timeout_ms=1200,
            on_complete=lambda _result: None,
        )
        self.dialogs.append(dialog)
        operation_id = "marker.peak_search::execute"
        dialog.operation_tree.selection_set(operation_id)

        with (
            patch(
                "scpi_automation.ui.device_validation_dialog."
                "messagebox.askyesnocancel",
                return_value=True,
            ),
            patch(
                "scpi_automation.ui.device_validation_dialog."
                "simpledialog.askstring",
                return_value="Marker 1 moved to the measured peak on screen.",
            ),
        ):
            dialog._record_manual_result()

        operation = dialog._progress.operation(operation_id)
        self.assertEqual(operation.status, OperationStatus.PASS)
        self.assertEqual(operation.validation_mode, "manual_operator")

    def test_set_without_readback_accepts_explicit_operator_evidence(
        self,
    ) -> None:
        profile = InstrumentProfile(
            profile_id="manual_set_profile",
            manufacturer="Example Instruments",
            model_family="READONLY",
            models=("READONLY-1",),
            instrument_class="digital_multimeter",
            category=DeviceCategory.DIGITAL_MULTIMETER,
            idn_patterns=(),
            verification_status="candidate",
            hardware_verified=False,
            capabilities=(
                CatalogCapability(
                    capability_id="marker.peak_search.auto",
                    label_ko="Auto Peak Search",
                    group="marker",
                    risk_level="low",
                    verification="candidate",
                    operations=(
                        CatalogOperation(
                            name="set",
                            scpi="CALC:MARK1:MAX:AUTO {state}",
                        ),
                    ),
                    parameters=(
                        {
                            "name": "state",
                            "type": "enum",
                            "choices": ["ON", "OFF"],
                        },
                    ),
                ),
            ),
        )
        identity = InstrumentIdentity(
            raw="Example Instruments,READONLY-1,SERIAL-1,1.0",
            manufacturer="Example Instruments",
            model="READONLY-1",
            serial="SERIAL-1",
            firmware="1.0",
        )
        dialog = DeviceValidationDialog(
            self.root,
            record=DiscoveryRecord(
                resource="TCPIP0::192.0.2.52::inst0::INSTR",
                interface="TCPIP0",
                state=DiscoveryState.IDENTIFIED,
                identity=identity,
            ),
            profile=profile,
            backend="",
            timeout_ms=1200,
            on_complete=lambda _result: None,
        )
        self.dialogs.append(dialog)
        operation_id = "marker.peak_search.auto::set"
        dialog.operation_tree.selection_set(operation_id)

        with (
            patch(
                "scpi_automation.ui.device_validation_dialog."
                "messagebox.askyesnocancel",
                return_value=True,
            ),
            patch(
                "scpi_automation.ui.device_validation_dialog."
                "simpledialog.askstring",
                return_value="Auto peak followed the trace during three sweeps.",
            ),
        ):
            dialog._record_manual_result()

        operation = dialog._progress.operation(operation_id)
        self.assertEqual(operation.status, OperationStatus.PASS)
        self.assertEqual(operation.validation_mode, "manual_operator")

    def test_high_risk_manual_result_requires_separate_exact_approval(
        self,
    ) -> None:
        profile = InstrumentProfile(
            profile_id="hazardous_execute_profile",
            manufacturer="Example Instruments",
            model_family="READONLY",
            models=("READONLY-1",),
            instrument_class="digital_multimeter",
            category=DeviceCategory.DIGITAL_MULTIMETER,
            idn_patterns=(),
            verification_status="candidate",
            hardware_verified=False,
            capabilities=(
                CatalogCapability(
                    capability_id="trace.store.file",
                    label_ko="Trace 파일 저장",
                    group="trace",
                    risk_level="high",
                    verification="candidate",
                    operations=(
                        CatalogOperation(
                            name="execute",
                            scpi='MMEM:STOR:TRAC 1,"test.csv"',
                        ),
                    ),
                ),
            ),
        )
        identity = InstrumentIdentity(
            raw="Example Instruments,READONLY-1,SERIAL-1,1.0",
            manufacturer="Example Instruments",
            model="READONLY-1",
            serial="SERIAL-1",
            firmware="1.0",
        )
        dialog = DeviceValidationDialog(
            self.root,
            record=DiscoveryRecord(
                resource="TCPIP0::192.0.2.53::inst0::INSTR",
                interface="TCPIP0",
                state=DiscoveryState.IDENTIFIED,
                identity=identity,
            ),
            profile=profile,
            backend="",
            timeout_ms=1200,
            on_complete=lambda _result: None,
        )
        self.dialogs.append(dialog)
        operation_id = "trace.store.file::execute"
        dialog.operation_tree.selection_set(operation_id)

        with (
            patch(
                "scpi_automation.ui.device_validation_dialog."
                "messagebox.askyesno",
                return_value=True,
            ) as exact_approval,
            patch(
                "scpi_automation.ui.device_validation_dialog."
                "messagebox.askyesnocancel",
                return_value=True,
            ),
            patch(
                "scpi_automation.ui.device_validation_dialog."
                "simpledialog.askstring",
                return_value="Saved to an isolated test path and removed it.",
            ),
        ):
            dialog._record_manual_result()

        exact_approval.assert_called_once()
        operation = dialog._progress.operation(operation_id)
        self.assertEqual(operation.status, OperationStatus.PASS)
        self.assertEqual(
            operation.validation_mode,
            "manual_operator_hazardous",
        )


if __name__ == "__main__":
    unittest.main()
