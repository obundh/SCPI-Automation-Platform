from __future__ import annotations

import tkinter as tk
import unittest
from dataclasses import replace

from scpi_automation.identity import (
    DeviceCategory,
    InstrumentIdentity,
)
from scpi_automation.ui.local_extension_dialog import (
    LocalExtensionEditorDialog,
)
from scpi_automation.validation import (
    ManualCommandCandidate,
    ManualSource,
    OperationKind,
)


class LocalExtensionEditorDialogTests(unittest.TestCase):
    def setUp(self) -> None:
        self.root = tk.Tk()
        self.root.withdraw()
        self.dialogs: list[LocalExtensionEditorDialog] = []
        self.identity = InstrumentIdentity(
            raw="Rohde&Schwarz,FSV30,SERIAL-1,3.60",
            manufacturer="Rohde&Schwarz",
            model="FSV30",
            serial="SERIAL-1",
            firmware="3.60",
        )
        source = ManualSource(
            manual_id="example_manual_v1",
            title="Example Programming Manual",
            document_reference="EX-100",
            version="1",
            firmware="1.0",
            source_url="https://example.invalid/manual.pdf",
            index_pdf_pages=(10,),
        )
        self.candidate = ManualCommandCandidate(
            profile_id="rs_fsv_fsva",
            command_id="example.manual.configure",
            command_pattern="CONFigure",
            command_group="CONF",
            manual_page=100,
            query_scpi_candidate="CONF?",
            query_support="manual_explicit",
            write_support="unknown",
            probe_policy="query_explicit",
            verification="manual_index_candidate",
            source=source,
        )

    def tearDown(self) -> None:
        for dialog in self.dialogs:
            if dialog.winfo_exists():
                dialog.destroy()
        if self.root.winfo_exists():
            self.root.update_idletasks()
            self.root.destroy()

    def test_set_editor_builds_typed_reversible_draft(self) -> None:
        dialog = LocalExtensionEditorDialog(
            self.root,
            candidate=self.candidate,
            identity=self.identity,
            category=DeviceCategory.SPECTRUM_ANALYZER,
        )
        self.dialogs.append(dialog)
        dialog.mode_var.set("설정(set)")
        dialog._mode_changed()
        self.assertEqual(dialog.risk_var.get(), "hazardous")
        dialog.label_var.set("설정값 변경")
        dialog.command_var.set("CONF {value}")
        dialog.readback_var.set("CONF?")
        dialog.response_var.set("float")
        dialog.arguments_var.set("value=20")
        dialog.types_var.set("value=float")
        dialog.units_var.set("value=Hz")
        dialog.minimums_var.set("value=0")
        dialog.maximums_var.set("value=100")

        definition = dialog._build_result()

        self.assertEqual(
            tuple(operation.name for operation in definition.operations),
            (OperationKind.QUERY.value, OperationKind.SET.value),
        )
        self.assertEqual(definition.parameters[0].name, "value")
        self.assertEqual(definition.parameters[0].minimum, 0.0)
        self.assertEqual(definition.parameters[0].maximum, 100.0)
        self.assertEqual(
            dict(definition.probe_arguments)["set"],
            (("value", "20"),),
        )

    def test_set_editor_rejects_missing_readback(self) -> None:
        dialog = LocalExtensionEditorDialog(
            self.root,
            candidate=self.candidate,
            identity=self.identity,
            category=DeviceCategory.SPECTRUM_ANALYZER,
        )
        self.dialogs.append(dialog)
        dialog.mode_var.set("설정(set)")
        dialog.command_var.set("CONF {value}")
        dialog.readback_var.set("")
        dialog.arguments_var.set("value=20")
        dialog.types_var.set("value=float")

        with self.assertRaisesRegex(ValueError, "readback"):
            dialog._build_result()

    def test_manual_only_candidate_is_locked_to_execute_evidence(
        self,
    ) -> None:
        candidate = replace(
            self.candidate,
            command_pattern="*TST?",
            query_scpi_candidate="*TST?",
            probe_policy="manual_only",
        )
        dialog = LocalExtensionEditorDialog(
            self.root,
            candidate=candidate,
            identity=self.identity,
            category=DeviceCategory.SPECTRUM_ANALYZER,
        )
        self.dialogs.append(dialog)

        self.assertEqual(
            tuple(dialog.mode_combo.cget("values")),
            ("실행(execute)",),
        )
        dialog.mode_var.set("조회(query)")
        with self.assertRaisesRegex(ValueError, "수동 검토 전용"):
            dialog._build_result()


if __name__ == "__main__":
    unittest.main()
