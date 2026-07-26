from __future__ import annotations

import tkinter as tk
import unittest

from scpi_automation.identity import DeviceCategory
from scpi_automation.planning import GenericPlanItem
from scpi_automation.routine import SelectedInstrument
from scpi_automation.ui import MeasurementPlanTab


class MeasurementPlanTabTests(unittest.TestCase):
    def setUp(self) -> None:
        self.root = tk.Tk()
        self.root.withdraw()
        self.tab = MeasurementPlanTab(self.root)
        self.tab.pack(fill="both", expand=True)
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
        self.root.update_idletasks()

    def tearDown(self) -> None:
        if self.root.winfo_exists():
            self.root.destroy()

    def test_analyzers_and_signal_generators_are_offered(self) -> None:
        self.tab.set_instruments((self.generator, self.analyzer))

        self.assertEqual(self.tab._spectrum_instruments, (self.analyzer,))
        self.assertEqual(
            self.tab._signal_generator_instruments,
            (self.generator,),
        )
        self.assertEqual(len(self.tab.device_combo.cget("values")), 2)
        self.assertIn("SMB100A", self.tab.device_var.get())
        self.assertEqual(str(self.tab.add_button.cget("state")), "normal")

    def test_unsupported_device_disables_plan_input(self) -> None:
        oscilloscope = SelectedInstrument(
            resource="DEMO::MSO58::INSTR",
            category=DeviceCategory.OSCILLOSCOPE,
            manufacturer="Tektronix",
            model="MSO58",
        )
        self.tab.set_instruments((oscilloscope,))

        self.assertEqual(self.tab.plan_items, ())
        self.assertEqual(str(self.tab.device_combo.cget("state")), "disabled")
        self.assertEqual(str(self.tab.center_entry.cget("state")), "disabled")
        self.assertEqual(str(self.tab.add_button.cget("state")), "disabled")
        self.assertEqual(str(self.tab.detail_button.cget("state")), "disabled")
        self.assertEqual(
            str(self.tab.category_detail_button.cget("state")),
            "normal",
        )
        self.assertIn("스펙트럼 분석기와 신호발생기", self.tab.device_help_var.get())

    def test_category_dialog_adds_scope_plan_while_quick_editor_stays_disabled(
        self,
    ) -> None:
        oscilloscope = SelectedInstrument(
            resource="DEMO::MSO58::INSTR",
            category=DeviceCategory.OSCILLOSCOPE,
            manufacturer="Tektronix",
            model="MSO58",
        )
        self.tab.set_instruments((oscilloscope,))

        self.tab.category_detail_button.invoke()
        dialog = self.tab._category_dialog
        self.assertIsNotNone(dialog)
        assert dialog is not None
        dialog.set_field_value("standard_procedure", "SOP-SCOPE-001 Rev.1")
        dialog.set_field_value("sample_description", "DUT CH1 / S-N 01")
        dialog.set_field_value("acceptance_criteria", "Rise time <= 5 ns")
        dialog.set_field_value("safety_confirmed", True)
        dialog.set_field_value(
            "probe_input_rating",
            "10:1 Probe, 300 V CAT II, Scope 1 MΩ 입력",
        )
        dialog.set_field_value("ground_connection_safety", True)
        dialog.assistance_ack_var.set(True)

        dialog._apply()
        self.root.update_idletasks()

        self.assertEqual(len(self.tab.plan_items), 1)
        item = self.tab.plan_items[0]
        self.assertIsInstance(item, GenericPlanItem)
        assert isinstance(item, GenericPlanItem)
        self.assertEqual(item.instrument, oscilloscope)
        self.assertEqual(item.category, DeviceCategory.OSCILLOSCOPE)
        self.assertIn("오실로스코프", self.tab.plan_list.get(0))
        self.assertIn("표준 준수", self.tab.plan_detail_var.get())
        self.assertEqual(str(self.tab.add_button.cget("state")), "disabled")

    def test_default_setup_is_added_in_canonical_units(self) -> None:
        self.tab.set_instruments((self.analyzer,))

        self.tab.add_button.invoke()
        self.root.update_idletasks()

        self.assertEqual(len(self.tab.plan_items), 1)
        item = self.tab.plan_items[0]
        self.assertEqual(item.instrument, self.analyzer)
        self.assertEqual(item.center_frequency_hz, 1_000_000_000)
        self.assertEqual(item.span_hz, 100_000_000)
        self.assertEqual(item.rbw_hz, 100_000)
        self.assertIsNone(item.vbw_hz)
        self.assertEqual(item.reference_level_dbm, 0)
        self.assertEqual(self.tab.plan_count_var.get(), "1시험")
        self.assertIn("FSV30", self.tab.plan_list.get(0))
        self.assertIn("Peak Search", self.tab.plan_detail_var.get())

    def test_invalid_number_does_not_add_a_plan(self) -> None:
        self.tab.set_instruments((self.analyzer,))
        self.tab.center_value_var.set("잘못된 값")

        self.tab.add_button.invoke()

        self.assertEqual(self.tab.plan_items, ())
        self.assertIn("숫자로 입력", self.tab.status_var.get())

    def test_auto_bandwidths_are_stored_as_none(self) -> None:
        self.tab.set_instruments((self.analyzer,))
        self.tab.rbw_auto_var.set(True)
        self.tab.vbw_auto_var.set(True)
        self.tab._sync_input_states()

        self.assertEqual(str(self.tab.rbw_entry.cget("state")), "disabled")
        self.assertEqual(str(self.tab.vbw_entry.cget("state")), "disabled")
        self.tab.add_button.invoke()

        self.assertIsNone(self.tab.plan_items[0].rbw_hz)
        self.assertIsNone(self.tab.plan_items[0].vbw_hz)

    def test_signal_generator_uses_its_own_basic_plan_fields(self) -> None:
        self.tab.set_instruments((self.analyzer, self.generator))
        self.tab.device_combo.current(1)
        self.tab._on_device_changed()
        self.tab.generator_frequency_var.set("2.45")
        self.tab.generator_frequency_unit_var.set("GHz")
        self.tab.generator_power_var.set("-15")
        self.tab.generator_dwell_var.set("2.5")

        self.tab.add_button.invoke()

        self.assertEqual(len(self.tab.plan_items), 1)
        item = self.tab.plan_items[0]
        self.assertEqual(item.instrument, self.generator)
        self.assertEqual(item.frequency_hz, 2_450_000_000)
        self.assertEqual(item.power_dbm, -15)
        self.assertEqual(item.dwell_seconds, 2.5)
        self.assertIn("출력 설정", self.tab.plan_list.get(0))
        self.assertIn("RF ON/OFF", self.tab.plan_detail_var.get())

    def test_analyzer_and_generator_are_saved_in_one_explicit_case(
        self,
    ) -> None:
        self.tab.set_instruments((self.analyzer, self.generator))

        self.tab.device_combo.current(0)
        self.tab._on_device_changed()
        self.tab.add_button.invoke()
        self.tab.device_combo.current(1)
        self.tab._on_device_changed()
        self.tab.add_button.invoke()

        self.assertEqual(len(self.tab.plan_items), 2)
        analyzer_item, generator_item = self.tab.plan_items
        self.assertEqual(analyzer_item.case_id, generator_item.case_id)
        self.assertEqual(analyzer_item.case_name, generator_item.case_name)
        self.assertTrue(analyzer_item.case_id)
        self.assertEqual(self.tab.plan_count_var.get(), "1시험")
        self.assertIn("[시험 01]", self.tab.plan_list.get(0))
        self.assertIn("[시험 01]", self.tab.plan_list.get(1))

        self.tab.generator_power_var.set("-10")
        self.tab.add_button.invoke()
        self.assertEqual(len(self.tab.plan_items), 2)
        self.assertEqual(self.tab.plan_items[1].power_dbm, -10)

    def test_adding_generator_keeps_existing_analyzer_plan(self) -> None:
        self.tab.set_instruments((self.analyzer,))
        self.tab.add_button.invoke()
        self.assertEqual(len(self.tab.plan_items), 1)

        self.tab.set_instruments((self.analyzer, self.generator))

        self.assertEqual(len(self.tab.plan_items), 1)
        self.assertEqual(self.tab.plan_items[0].instrument, self.analyzer)

    def test_changed_analyzer_selection_clears_old_plan(self) -> None:
        second_analyzer = SelectedInstrument(
            resource="DEMO::N9020B::INSTR",
            category=DeviceCategory.SPECTRUM_ANALYZER,
            manufacturer="Keysight",
            model="N9020B",
        )
        self.tab.set_instruments((self.analyzer,))
        self.tab.add_button.invoke()
        self.assertEqual(len(self.tab.plan_items), 1)

        self.tab.set_instruments((second_analyzer,))

        self.assertEqual(self.tab.plan_items, ())
        self.assertIn("N9020B", self.tab.device_var.get())

    def test_selecting_items_does_not_resize_side_panels(self) -> None:
        self.root.geometry("900x620+0+0")
        self.root.deiconify()
        self.tab.set_instruments((self.analyzer,))
        self.tab.add_button.invoke()
        self.tab.center_value_var.set("2")
        self.tab.span_value_var.set("50")
        self.tab._start_new_case()
        self.tab.add_button.invoke()
        self.root.update()

        expected_sizes = (
            self.tab.settings_panel.winfo_width(),
            self.tab.settings_panel.winfo_height(),
            self.tab.plan_panel.winfo_width(),
            self.tab.plan_panel.winfo_height(),
            self.tab.plan_list.winfo_width(),
            self.tab.plan_list.winfo_height(),
            self.tab.plan_detail_label.winfo_width(),
            self.tab.plan_detail_label.winfo_height(),
        )

        for index in range(2):
            self.tab.plan_list.selection_clear(0, tk.END)
            self.tab.plan_list.selection_set(index)
            self.tab._on_plan_selected()
            self.root.update_idletasks()
            self.root.update()
            self.assertEqual(
                (
                    self.tab.settings_panel.winfo_width(),
                    self.tab.settings_panel.winfo_height(),
                    self.tab.plan_panel.winfo_width(),
                    self.tab.plan_panel.winfo_height(),
                    self.tab.plan_list.winfo_width(),
                    self.tab.plan_list.winfo_height(),
                    self.tab.plan_detail_label.winfo_width(),
                    self.tab.plan_detail_label.winfo_height(),
                ),
                expected_sizes,
            )

    def test_switching_device_category_does_not_resize_side_panels(self) -> None:
        self.root.geometry("900x620+0+0")
        self.root.deiconify()
        self.tab.set_instruments((self.analyzer, self.generator))
        self.root.update()
        expected_sizes = (
            self.tab.settings_panel.winfo_width(),
            self.tab.settings_panel.winfo_height(),
            self.tab.plan_panel.winfo_width(),
            self.tab.plan_panel.winfo_height(),
        )

        for index in (1, 0, 1, 0):
            self.tab.device_combo.current(index)
            self.tab._on_device_changed()
            self.root.update_idletasks()
            self.root.update()
            self.assertEqual(
                (
                    self.tab.settings_panel.winfo_width(),
                    self.tab.settings_panel.winfo_height(),
                    self.tab.plan_panel.winfo_width(),
                    self.tab.plan_panel.winfo_height(),
                ),
                expected_sizes,
            )

    def test_ui_scale_is_clamped(self) -> None:
        self.tab.apply_ui_scale(2.0)

        self.assertEqual(self.tab._ui_scale, 1.4)

    def test_continue_button_opens_execution_step(self) -> None:
        calls: list[str] = []
        other = MeasurementPlanTab(
            self.root,
            on_continue=lambda: calls.append("continue"),
        )
        other.pack(fill="both", expand=True)
        other.set_instruments((self.analyzer,))

        other.continue_button.invoke()

        self.assertEqual(calls, ["continue"])
        self.assertIn("계획에서 가져오기", other.status_var.get())
        other.destroy()

    def test_detail_button_reuses_an_open_window(self) -> None:
        self.tab.set_instruments((self.analyzer, self.generator))

        self.tab.detail_button.invoke()
        first_dialog = self.tab._detail_dialog
        self.assertIsNotNone(first_dialog)
        self.tab.detail_button.invoke()

        self.assertIs(self.tab._detail_dialog, first_dialog)
        first_dialog.destroy()

    def test_detail_dialog_adds_multiple_analyzer_points_at_once(self) -> None:
        self.tab.set_instruments((self.analyzer, self.generator))
        self.tab.detail_button.invoke()
        dialog = self.tab._detail_dialog
        self.assertIsNotNone(dialog)
        dialog.frequency_text.delete("1.0", tk.END)
        dialog.frequency_text.insert("1.0", "100, 200, 300")
        dialog.list_unit_var.set("MHz")

        dialog._apply()
        self.root.update_idletasks()

        self.assertEqual(len(self.tab.plan_items), 3)
        self.assertEqual(
            tuple(item.center_frequency_hz for item in self.tab.plan_items),
            (100_000_000, 200_000_000, 300_000_000),
        )
        self.assertIn("3개를 각각의", self.tab.status_var.get())


if __name__ == "__main__":
    unittest.main()
