from __future__ import annotations

import tkinter as tk
import unittest

from scpi_automation.identity import DeviceCategory
from scpi_automation.planning import (
    SignalGeneratorPlanItem,
    SpectrumPlanItem,
)
from scpi_automation.routine import SelectedInstrument
from scpi_automation.ui import PlanDetailDialog


class PlanDetailDialogTests(unittest.TestCase):
    def setUp(self) -> None:
        self.root = tk.Tk()
        self.root.withdraw()
        self.analyzer = SelectedInstrument(
            resource="DEMO::FSV30::INSTR",
            category=DeviceCategory.SPECTRUM_ANALYZER,
            manufacturer="Rohde&Schwarz",
            model="FSV30",
        )
        self.generator = SelectedInstrument(
            resource="DEMO::SMB100A::INSTR",
            category=DeviceCategory.SIGNAL_GENERATOR,
            manufacturer="Rohde&Schwarz",
            model="SMB100A",
        )
        self.added: list[tuple[object, ...]] = []
        self.dialog = PlanDetailDialog(
            self.root,
            instruments=(self.analyzer, self.generator),
            initial_instrument=self.analyzer,
            on_add=self._collect,
        )
        self.root.update_idletasks()

    def tearDown(self) -> None:
        if self.dialog.winfo_exists():
            self.dialog.destroy()
        if self.root.winfo_exists():
            self.root.destroy()

    def _collect(self, items: tuple[object, ...]) -> bool:
        self.added.append(items)
        return True

    def _set_frequency_list(self, value: str, unit: str = "MHz") -> None:
        self.dialog.frequency_text.delete("1.0", tk.END)
        self.dialog.frequency_text.insert("1.0", value)
        self.dialog.list_unit_var.set(unit)

    def test_analyzer_list_creates_atomic_items_with_common_settings(self) -> None:
        self._set_frequency_list("100\n200\n500")
        self.dialog.span_value_var.set("20")
        self.dialog.span_unit_var.set("MHz")
        self.dialog.rbw_value_var.set("10")
        self.dialog.rbw_unit_var.set("kHz")
        self.dialog.vbw_auto_var.set(True)
        self.dialog.reference_level_var.set("-5")

        items = self.dialog._build_items()

        self.assertEqual(len(items), 3)
        self.assertTrue(all(isinstance(item, SpectrumPlanItem) for item in items))
        self.assertEqual(
            tuple(item.center_frequency_hz for item in items),
            (100_000_000, 200_000_000, 500_000_000),
        )
        self.assertTrue(all(item.span_hz == 20_000_000 for item in items))
        self.assertTrue(all(item.rbw_hz == 10_000 for item in items))
        self.assertTrue(all(item.vbw_hz is None for item in items))
        self.assertTrue(all(item.reference_level_dbm == -5 for item in items))

    def test_analyzer_range_method_builds_inclusive_frequency_points(self) -> None:
        self.dialog.method_combo.current(1)
        self.dialog._on_method_changed()
        self.dialog.range_start_var.set("100")
        self.dialog.range_stop_var.set("300")
        self.dialog.range_step_var.set("100")
        self.dialog.range_unit_var.set("MHz")

        items = self.dialog._build_items()

        self.assertEqual(
            tuple(item.center_frequency_hz for item in items),
            (100_000_000, 200_000_000, 300_000_000),
        )

    def test_generator_uses_frequency_power_and_dwell(self) -> None:
        self.dialog.device_combo.current(1)
        self.dialog._on_device_changed()
        self._set_frequency_list("1, 2, 2.5", "GHz")
        self.dialog.power_var.set("-12.5")
        self.dialog.dwell_var.set("2")

        items = self.dialog._build_items()

        self.assertEqual(len(items), 3)
        self.assertTrue(
            all(isinstance(item, SignalGeneratorPlanItem) for item in items)
        )
        self.assertEqual(
            tuple(item.frequency_hz for item in items),
            (1_000_000_000, 2_000_000_000, 2_500_000_000),
        )
        self.assertTrue(all(item.power_dbm == -12.5 for item in items))
        self.assertTrue(all(item.dwell_seconds == 2 for item in items))

    def test_invalid_batch_does_not_call_parent_or_close_dialog(self) -> None:
        self._set_frequency_list("100, 잘못된 값, 300")

        self.dialog._apply()

        self.assertEqual(self.added, [])
        self.assertTrue(self.dialog.winfo_exists())
        self.assertIn("2번째 주파수", self.dialog.status_var.get())

    def test_apply_sends_complete_batch_once_and_closes(self) -> None:
        self._set_frequency_list("100, 200, 300")

        self.dialog._apply()
        self.root.update_idletasks()

        self.assertEqual(len(self.added), 1)
        self.assertEqual(len(self.added[0]), 3)
        self.assertFalse(self.dialog.winfo_exists())


if __name__ == "__main__":
    unittest.main()
