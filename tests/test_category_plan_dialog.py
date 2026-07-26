from __future__ import annotations

import tkinter as tk
import unittest

from scpi_automation.identity import DeviceCategory
from scpi_automation.planning import GenericPlanItem
from scpi_automation.routine import SelectedInstrument
from scpi_automation.ui.category_plan_dialog import CategoryPlanDialog


class CategoryPlanDialogTests(unittest.TestCase):
    def setUp(self) -> None:
        self.root = tk.Tk()
        self.root.withdraw()
        self.analyzer = SelectedInstrument(
            resource="DEMO::FSW::INSTR",
            category=DeviceCategory.SPECTRUM_ANALYZER,
            manufacturer="Rohde&Schwarz",
            model="FSW",
        )
        self.dmm = SelectedInstrument(
            resource="DEMO::34465A::INSTR",
            category=DeviceCategory.DIGITAL_MULTIMETER,
            manufacturer="Keysight",
            model="34465A",
        )
        self.lcr = SelectedInstrument(
            resource="DEMO::E4980A::INSTR",
            category=DeviceCategory.LCR_METER,
            manufacturer="Keysight",
            model="E4980A",
        )
        self.added: list[GenericPlanItem] = []
        self.dialog = CategoryPlanDialog(
            self.root,
            instruments=(self.analyzer, self.dmm, self.lcr),
            on_add=self._on_add,
            initial_instrument=self.dmm,
        )
        self.root.update_idletasks()
        self.root.update()

    def tearDown(self) -> None:
        try:
            if self.dialog.winfo_exists():
                self.dialog.destroy()
        except tk.TclError:
            pass
        if self.root.winfo_exists():
            self.root.destroy()

    def _on_add(self, item: GenericPlanItem) -> bool:
        self.added.append(item)
        return True

    def test_dialog_shows_non_guarantee_notice_and_scrollable_full_form(self) -> None:
        notice = self.dialog.assistance_notice_label.cget("text")
        self.assertIn("표준 준수", notice)
        self.assertIn("보증하지 않습니다", notice)
        self.assertIn("standard_procedure", self.dialog._common_variables)
        self.assertIn("acceptance_criteria", self.dialog._common_text_widgets)
        self.assertIn("measurement_function", self.dialog.visible_detail_field_ids)
        self.assertIn("JCGM", self.dialog.standard_examples_var.get())

        bounds = self.dialog.scroll_canvas.bbox("all")
        self.assertIsNotNone(bounds)
        assert bounds is not None
        self.assertGreater(bounds[3] - bounds[1], self.dialog.scroll_canvas.winfo_height())
        self.dialog.scroll_canvas.yview_moveto(1.0)
        self.assertGreater(self.dialog.scroll_canvas.yview()[0], 0)

    def test_switching_category_and_method_rebuilds_relevant_fields(self) -> None:
        self.dialog.device_combo.current(0)
        self.dialog._on_device_changed()
        self.assertIn("detector", self.dialog.visible_detail_field_ids)
        self.assertNotIn("load_correction", self.dialog.visible_detail_field_ids)

        self.dialog.method_combo.current(3)
        self.dialog._on_method_changed()
        self.assertIn("emi_receiver_mode", self.dialog.visible_detail_field_ids)
        self.assertIn("CISPR", self.dialog.method_var.get())

        self.dialog.device_combo.current(2)
        self.dialog._on_device_changed()
        self.assertIn("load_correction", self.dialog.visible_detail_field_ids)
        self.assertIn("fixture_description", self.dialog.visible_detail_field_ids)
        self.assertNotIn("detector", self.dialog.visible_detail_field_ids)

    def test_detail_frequency_and_time_fields_offer_readable_units(self) -> None:
        self.dialog.device_combo.current(0)
        self.dialog._on_device_changed()

        self.assertEqual(
            self.dialog._field_unit_vars["rbw_hz"].get(),
            "kHz",
        )
        self.assertEqual(self.dialog.field_value("rbw_hz"), "100")
        self.dialog._field_unit_vars["rbw_hz"].set("MHz")
        self.dialog._on_field_unit_changed("rbw_hz")
        self.dialog.set_field_value("rbw_hz", 1_000_000)

        raw = self.dialog._raw_values(
            self.dialog._detail_variables,
            self.dialog._detail_text_widgets,
            self.dialog._visible_detail_fields,
        )
        self.assertEqual(self.dialog.field_value("rbw_hz"), "1")
        self.assertEqual(raw["rbw_hz"], "1000000")

    def test_required_safety_and_planning_notice_block_then_allow_add(self) -> None:
        self.dialog.set_field_value("standard_procedure", "SOP-DMM-001 Rev.2")
        self.dialog.set_field_value("sample_description", "10 V reference / S-N 01")
        self.dialog.set_field_value("acceptance_criteria", "10.000 V ±0.010 V")
        self.dialog.set_field_value(
            "input_terminal_rating",
            "Front V/Ω, 최대 1000 V, CAT II",
        )
        self.dialog.set_field_value("max_input_safety_confirmed", True)

        self.dialog.assistance_ack_var.set(True)
        self.dialog._apply()
        self.assertEqual(self.added, [])
        self.assertIn("안전 조건 확인", self.dialog.status_var.get())
        self.assertTrue(self.dialog.winfo_exists())

        self.dialog.set_field_value("safety_confirmed", True)
        self.dialog._apply()
        self.assertEqual(len(self.added), 1)
        item = self.added[0]
        self.assertEqual(item.instrument, self.dmm)
        self.assertEqual(item.method_id, "dmm_single_read")
        self.assertEqual(item.value_for("standard_procedure"), "SOP-DMM-001 Rev.2")
        self.assertTrue(item.value_for("safety_confirmed"))

    def test_notice_acknowledgement_is_separate_from_electrical_safety(self) -> None:
        self.dialog.set_field_value("standard_procedure", "SOP-DMM")
        self.dialog.set_field_value("sample_description", "DUT")
        self.dialog.set_field_value("acceptance_criteria", "기준표 적용")
        self.dialog.set_field_value("safety_confirmed", True)
        self.dialog.set_field_value(
            "input_terminal_rating",
            "Front V/Ω, DUT 저전압",
        )
        self.dialog.set_field_value("max_input_safety_confirmed", True)

        self.dialog.assistance_ack_var.set(False)
        self.dialog._apply()
        self.assertEqual(self.added, [])
        self.assertIn("계획 보조", self.dialog.status_var.get())
        self.assertTrue(self.dialog.winfo_exists())


if __name__ == "__main__":
    unittest.main()
