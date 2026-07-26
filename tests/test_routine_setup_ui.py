from __future__ import annotations

import tkinter as tk
import unittest

from scpi_automation.identity import DeviceCategory
from scpi_automation.routine import (
    DelayStep,
    PlanBoundDelayStep,
    SelectedFeature,
    SelectedInstrument,
    WaitForCompletionStep,
)
from scpi_automation.ui.routine_setup_tab import RoutineSetupTab


class RoutineSetupUiTests(unittest.TestCase):
    def setUp(self) -> None:
        self.root = tk.Tk()
        self.root.withdraw()
        self.tab = RoutineSetupTab(self.root)
        self.tab.pack(fill="both", expand=True)
        self.instruments = (
            SelectedInstrument(
                resource="DEMO::FSV30::INSTR",
                category=DeviceCategory.SPECTRUM_ANALYZER,
                manufacturer="Rohde&Schwarz",
                model="FSV30",
            ),
            SelectedInstrument(
                resource="DEMO::SMB100A::INSTR",
                category=DeviceCategory.SIGNAL_GENERATOR,
                manufacturer="Rohde&Schwarz",
                model="SMB100A",
            ),
        )
        self.root.update_idletasks()

    def tearDown(self) -> None:
        if self.root.winfo_exists():
            self.root.destroy()

    def _add_feature(self, device_index: int, feature_id: str) -> None:
        self.tab.device_combo.current(device_index)
        self.tab._on_device_changed()
        self.root.update_idletasks()
        feature_index = next(
            index
            for index, feature in enumerate(self.tab._visible_features)
            if feature.feature_id == feature_id
        )
        self.tab.feature_list.selection_clear(0, tk.END)
        self.tab.feature_list.selection_set(feature_index)
        self.tab._on_feature_selected()
        self.root.update_idletasks()
        self.tab.add_button.invoke()
        self.root.update_idletasks()

    def test_empty_selection_explains_what_to_do(self) -> None:
        self.assertEqual(self.tab.routine_steps, ())
        self.assertEqual(str(self.tab.device_combo.cget("state")), "disabled")
        self.assertIn("선택한 장비가 없어요", self.tab.selection_summary_var.get())
        self.assertEqual(str(self.tab.add_button.cget("state")), "disabled")

    def test_common_step_buttons_are_disabled_without_instruments(self) -> None:
        self.assertEqual(self.tab.routine_steps, ())
        self.assertEqual(
            str(self.tab.add_delay_button.cget("state")),
            "disabled",
        )
        self.assertEqual(
            str(self.tab.add_completion_button.cget("state")),
            "disabled",
        )

    def test_features_from_multiple_devices_can_share_one_routine(self) -> None:
        self.tab.set_instruments(self.instruments)

        self._add_feature(0, "spectrum_analyzer.peak_search")
        self._add_feature(1, "signal_generator.output_off")

        steps = self.tab.routine_steps
        self.assertEqual(len(steps), 2)
        self.assertEqual(steps[0].device_resource, "DEMO::FSV30::INSTR")
        self.assertEqual(steps[1].device_resource, "DEMO::SMB100A::INSTR")
        self.assertEqual(steps[0].feature_id, "spectrum_analyzer.peak_search")
        self.assertEqual(steps[1].feature_id, "signal_generator.output_off")
        self.assertEqual(self.tab.routine_count_var.get(), "2단계")

    def test_feature_selection_does_not_resize_the_side_panels(self) -> None:
        self.root.geometry("1100x700+0+0")
        self.root.deiconify()
        self.tab.set_instruments(self.instruments)
        self.root.update()
        expected_sizes = (
            self.tab.feature_panel.winfo_width(),
            self.tab.feature_panel.winfo_height(),
            self.tab.feature_list.winfo_width(),
            self.tab.feature_list.winfo_height(),
            self.tab.feature_detail.winfo_width(),
            self.tab.feature_detail.winfo_height(),
            self.tab.feature_risk_badge.winfo_width(),
            self.tab.feature_risk_badge.winfo_height(),
            self.tab.routine_panel.winfo_width(),
            self.tab.routine_panel.winfo_height(),
        )

        for index, feature in enumerate(self.tab._visible_features):
            with self.subTest(feature_id=feature.feature_id):
                self.tab.feature_list.selection_clear(0, tk.END)
                self.tab.feature_list.selection_set(index)
                self.tab._on_feature_selected()
                self.root.update_idletasks()
                self.root.update()
                self.assertEqual(
                    (
                        self.tab.feature_panel.winfo_width(),
                        self.tab.feature_panel.winfo_height(),
                        self.tab.feature_list.winfo_width(),
                        self.tab.feature_list.winfo_height(),
                        self.tab.feature_detail.winfo_width(),
                        self.tab.feature_detail.winfo_height(),
                        self.tab.feature_risk_badge.winfo_width(),
                        self.tab.feature_risk_badge.winfo_height(),
                        self.tab.routine_panel.winfo_width(),
                        self.tab.routine_panel.winfo_height(),
                    ),
                    expected_sizes,
                )

    def test_device_feature_delay_and_completion_keep_order_and_values(self) -> None:
        self.tab.set_instruments(self.instruments)
        self._add_feature(0, "spectrum_analyzer.peak_search")

        self.tab.delay_seconds_var.set("2.5")
        self.tab.add_delay_button.invoke()
        self.tab.completion_device_combo.current(1)
        self.tab.completion_timeout_var.set("12")
        self.tab.add_completion_button.invoke()
        self.root.update_idletasks()

        steps = self.tab.routine_steps
        self.assertEqual(len(steps), 3)
        self.assertIsInstance(steps[0], SelectedFeature)
        self.assertIsInstance(steps[1], DelayStep)
        self.assertIsInstance(steps[2], WaitForCompletionStep)
        self.assertEqual(steps[0].device_resource, "DEMO::FSV30::INSTR")
        self.assertEqual(steps[1].seconds, 2.5)
        self.assertEqual(steps[2].timeout_seconds, 12.0)
        self.assertEqual(steps[2].device_resource, "DEMO::SMB100A::INSTR")

    def test_delay_can_use_signal_generator_dwell_from_plan(self) -> None:
        self.tab.set_instruments(self.instruments)
        self.tab.delay_from_plan_var.set(True)
        self.tab._sync_delay_source_state()

        self.tab.add_delay_button.invoke()

        self.assertEqual(len(self.tab.routine_steps), 1)
        step = self.tab.routine_steps[0]
        self.assertIsInstance(step, PlanBoundDelayStep)
        self.assertEqual(step.instrument, self.instruments[1])
        self.assertIn("Dwell", self.tab.routine_list.get(0))
        self.assertEqual(
            str(self.tab.delay_seconds_spin.cget("state")),
            "disabled",
        )

    def test_invalid_common_step_times_are_not_added(self) -> None:
        self.tab.set_instruments(self.instruments)

        for value, variable, button in (
            ("not-a-number", self.tab.delay_seconds_var, self.tab.add_delay_button),
            (
                "not-a-number",
                self.tab.completion_timeout_var,
                self.tab.add_completion_button,
            ),
        ):
            with self.subTest(button=str(button)):
                variable.set(value)
                button.invoke()
                self.root.update_idletasks()

                self.assertEqual(self.tab.routine_steps, ())
                self.assertIn("숫자로 입력", self.tab.status_var.get())

    def test_delay_and_completion_accept_beginner_friendly_units(self) -> None:
        self.tab.set_instruments(self.instruments)
        self.tab.delay_seconds_var.set("500")
        self.tab.delay_unit_var.set("밀리초")
        self.tab.add_delay_button.invoke()
        self.tab.completion_device_combo.current(0)
        self.tab.completion_timeout_var.set("2")
        self.tab.completion_timeout_unit_var.set("분")
        self.tab.add_completion_button.invoke()

        self.assertEqual(self.tab.routine_steps[0].seconds, 0.5)
        self.assertEqual(self.tab.routine_steps[1].timeout_seconds, 120.0)
        self.assertIn("500밀리초", self.tab.routine_list.get(0))
        self.assertIn("2분", self.tab.routine_list.get(1))

    def test_common_steps_can_be_deleted_without_type_specific_errors(self) -> None:
        self.tab.set_instruments(self.instruments)
        self.tab.delay_seconds_var.set("1.5")
        self.tab.add_delay_button.invoke()
        self.tab.completion_device_combo.current(1)
        self.tab.completion_timeout_var.set("20")
        self.tab.add_completion_button.invoke()

        self.tab.routine_list.selection_clear(0, tk.END)
        self.tab.routine_list.selection_set(0)
        self.tab._delete_selected()
        self.assertEqual(len(self.tab.routine_steps), 1)
        self.assertIsInstance(self.tab.routine_steps[0], WaitForCompletionStep)

        self.tab.routine_list.selection_clear(0, tk.END)
        self.tab.routine_list.selection_set(0)
        self.tab._delete_selected()
        self.assertEqual(self.tab.routine_steps, ())

    def test_steps_can_be_reordered_deleted_and_cleared(self) -> None:
        self.tab.set_instruments(self.instruments)
        self._add_feature(0, "spectrum_analyzer.peak_search")
        self._add_feature(1, "signal_generator.set_frequency")

        self.tab.routine_list.selection_clear(0, tk.END)
        self.tab.routine_list.selection_set(1)
        self.tab._on_routine_selected()
        self.root.update_idletasks()
        self.tab.move_up_button.invoke()
        self.assertEqual(
            self.tab.routine_steps[0].feature_id,
            "signal_generator.set_frequency",
        )

        self.tab.delete_button.invoke()
        self.assertEqual(len(self.tab.routine_steps), 1)
        self.tab.clear_button.invoke()
        self.assertEqual(self.tab.routine_steps, ())

    def test_changing_device_selection_clears_stale_steps(self) -> None:
        self.tab.set_instruments(self.instruments)
        self._add_feature(0, "spectrum_analyzer.read_marker")
        self.assertEqual(len(self.tab.routine_steps), 1)

        self.tab.set_instruments((self.instruments[1],))

        self.assertEqual(self.tab.routine_steps, ())
        self.assertEqual(len(self.tab.device_combo.cget("values")), 1)

    def test_changing_selection_preserves_pc_and_remaining_device_steps(self) -> None:
        self.tab.set_instruments(self.instruments)
        self._add_feature(0, "spectrum_analyzer.peak_search")
        self.tab.delay_seconds_var.set("0.5")
        self.tab.add_delay_button.invoke()
        self._add_feature(1, "signal_generator.set_frequency")

        self.tab.set_instruments((self.instruments[1],))

        self.assertEqual(len(self.tab.routine_steps), 2)
        self.assertIsInstance(self.tab.routine_steps[0], DelayStep)
        self.assertEqual(
            self.tab.routine_steps[1].feature_id,
            "signal_generator.set_frequency",
        )
        self.assertIn("1개만 정리", self.tab.status_var.get())

    def test_ui_scale_is_clamped_and_updates_fonts(self) -> None:
        font, base_size = next(iter(self.tab._font_metrics.values()))

        self.tab.apply_ui_scale(2.0)

        self.assertEqual(self.tab._ui_scale, 1.4)
        self.assertEqual(
            int(font.cget("size")),
            self.tab._scaled_size(base_size, 1.4),
        )


if __name__ == "__main__":
    unittest.main()
