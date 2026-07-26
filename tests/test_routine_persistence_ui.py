from __future__ import annotations

import tempfile
import tkinter as tk
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from scpi_automation.identity import DeviceCategory
from scpi_automation.routine import (
    DelayStep,
    SelectedFeature,
    SelectedInstrument,
    WaitForCompletionStep,
    save_routine,
)
from scpi_automation.ui.routine_setup_tab import RoutineSetupTab


UI_MODULE = "scpi_automation.ui.routine_setup_tab"


class RoutinePersistenceUiTests(unittest.TestCase):
    def setUp(self) -> None:
        self.root = tk.Tk()
        self.root.withdraw()
        self.tab = RoutineSetupTab(self.root)
        self.tab.pack(fill="both", expand=True)
        self.analyzer = SelectedInstrument(
            resource="TCPIP0::192.0.2.10::inst0::INSTR",
            category=DeviceCategory.SPECTRUM_ANALYZER,
            manufacturer="Rohde&Schwarz",
            model="FSV30",
            serial="AN-001",
            firmware="3.60",
            raw_idn="Rohde&Schwarz,FSV30,AN-001,3.60",
            profile_id="rohde-schwarz.fsv30",
        )
        self.generator = SelectedInstrument(
            resource="USB0::0x1234::0x5678::SG-001::INSTR",
            category=DeviceCategory.SIGNAL_GENERATOR,
            manufacturer="Rohde&Schwarz",
            model="SMB100A",
            serial="SG-001",
            firmware="4.70",
            raw_idn="Rohde&Schwarz,SMB100A,SG-001,4.70",
            profile_id="rohde-schwarz.smb100a",
        )
        self.instruments = (self.analyzer, self.generator)
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary_directory.cleanup)
        self.root.update_idletasks()

    def tearDown(self) -> None:
        if self.root.winfo_exists():
            self.root.destroy()

    def _path(self, filename: str = "routine.scpiroutine.json") -> Path:
        return Path(self.temporary_directory.name, filename)

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
        self.tab.add_button.invoke()
        self.root.update_idletasks()

    def _add_delay(self, seconds: float) -> None:
        self.tab.delay_seconds_var.set(str(seconds))
        self.tab.add_delay_button.invoke()
        self.root.update_idletasks()

    def _select_routine_row(self, index: int) -> None:
        self.tab.routine_list.selection_clear(0, tk.END)
        self.tab.routine_list.selection_set(index)
        self.tab.routine_list.activate(index)
        self.tab._on_routine_selected()
        self.root.update_idletasks()

    def _map_window_for_list_coordinates(self) -> None:
        self.root.geometry("1100x760+0+0")
        self.root.deiconify()
        self.root.update()

    def _right_click_event(self, index: int) -> SimpleNamespace:
        bounds = self.tab.routine_list.bbox(index)
        self.assertIsNotNone(bounds)
        _x, y, _width, height = bounds
        return SimpleNamespace(
            y=int(y + max(1, height // 2)),
            x_root=int(self.tab.routine_list.winfo_rootx() + 10),
            y_root=int(
                self.tab.routine_list.winfo_rooty() + y + max(1, height // 2)
            ),
        )

    def test_save_and_load_round_trip_through_dialog_buttons(self) -> None:
        path = self._path("차폐효율.scpiroutine.json")
        self.tab.set_instruments(self.instruments)
        self._add_feature(0, "spectrum_analyzer.peak_search")
        self._add_delay(2.5)
        self.tab.completion_device_combo.current(1)
        self.tab.completion_timeout_var.set("12")
        self.tab.add_completion_button.invoke()
        expected_steps = self.tab.routine_steps

        with (
            patch(
                f"{UI_MODULE}.filedialog.asksaveasfilename",
                return_value=str(path),
            ) as choose_save,
            patch(f"{UI_MODULE}.messagebox.showinfo") as save_info,
            patch(f"{UI_MODULE}.messagebox.showerror") as save_error,
        ):
            self.tab.save_button.invoke()

        choose_save.assert_called_once()
        save_info.assert_called_once()
        save_error.assert_not_called()
        self.assertTrue(path.is_file())
        self.assertIn("저장했어요", self.tab.status_var.get())

        self.tab.clear_button.invoke()
        self.assertEqual(self.tab.routine_steps, ())

        with (
            patch(
                f"{UI_MODULE}.filedialog.askopenfilename",
                return_value=str(path),
            ) as choose_load,
            patch(f"{UI_MODULE}.messagebox.showinfo") as load_info,
            patch(f"{UI_MODULE}.messagebox.showwarning") as load_warning,
            patch(f"{UI_MODULE}.messagebox.showerror") as load_error,
            patch(f"{UI_MODULE}.messagebox.askyesno") as replace_prompt,
        ):
            self.tab.load_button.invoke()

        choose_load.assert_called_once()
        load_info.assert_called_once()
        load_warning.assert_not_called()
        load_error.assert_not_called()
        replace_prompt.assert_not_called()
        self.assertEqual(self.tab.routine_steps, expected_steps)
        self.assertIs(self.tab.routine_steps[0].instrument, self.analyzer)
        self.assertIs(self.tab.routine_steps[2].instrument, self.generator)
        self.assertIn("불러왔어요", self.tab.status_var.get())

    def test_save_button_stays_disabled_until_a_step_exists(self) -> None:
        self.assertEqual(str(self.tab.save_button.cget("state")), "disabled")
        self.assertEqual(
            str(self.tab.save_continue_button.cget("state")),
            "disabled",
        )

        self.tab.set_instruments(self.instruments)

        self.assertEqual(str(self.tab.save_button.cget("state")), "disabled")
        self.assertEqual(
            str(self.tab.save_continue_button.cget("state")),
            "disabled",
        )
        with patch(
            f"{UI_MODULE}.filedialog.asksaveasfilename"
        ) as choose_save:
            self.tab.save_button.invoke()
        choose_save.assert_not_called()

        self._add_delay(1)
        self.assertEqual(str(self.tab.save_button.cget("state")), "normal")
        self.assertEqual(
            str(self.tab.save_continue_button.cget("state")),
            "normal",
        )
        self.tab.clear_button.invoke()
        self.assertEqual(str(self.tab.save_button.cget("state")), "disabled")
        self.assertEqual(
            str(self.tab.save_continue_button.cget("state")),
            "disabled",
        )

    def test_save_and_continue_moves_only_after_successful_save(self) -> None:
        path = self._path("save-and-next.scpiroutine.json")
        continue_callback = Mock()
        self.tab._on_continue = continue_callback
        self.tab.set_instruments(self.instruments)
        self._add_delay(1)

        with (
            patch(
                f"{UI_MODULE}.filedialog.asksaveasfilename",
                return_value=str(path),
            ),
            patch(f"{UI_MODULE}.messagebox.showinfo"),
            patch(f"{UI_MODULE}.messagebox.showerror") as save_error,
        ):
            self.tab.save_continue_button.invoke()

        self.assertTrue(path.is_file())
        continue_callback.assert_called_once_with()
        save_error.assert_not_called()

    def test_save_and_continue_stays_on_current_step_when_canceled(self) -> None:
        continue_callback = Mock()
        self.tab._on_continue = continue_callback
        self.tab.set_instruments(self.instruments)
        self._add_delay(1)

        with patch(
            f"{UI_MODULE}.filedialog.asksaveasfilename",
            return_value="",
        ):
            self.tab.save_continue_button.invoke()

        continue_callback.assert_not_called()

    def test_save_and_continue_does_not_move_after_save_error(self) -> None:
        continue_callback = Mock()
        self.tab._on_continue = continue_callback
        self.tab.set_instruments(self.instruments)
        self._add_delay(1)

        with (
            patch(
                f"{UI_MODULE}.filedialog.asksaveasfilename",
                return_value=str(self._path("cannot-save.scpiroutine.json")),
            ),
            patch(
                f"{UI_MODULE}.save_routine",
                side_effect=OSError("쓰기 실패"),
            ),
            patch(f"{UI_MODULE}.messagebox.showerror") as save_error,
        ):
            self.tab.save_continue_button.invoke()

        save_error.assert_called_once()
        continue_callback.assert_not_called()

    def test_exact_resource_load_rebinds_steps_to_current_object(self) -> None:
        path = self._path("exact-resource.scpiroutine.json")
        saved_analyzer = SelectedInstrument(
            resource=self.analyzer.resource,
            category=self.analyzer.category,
            manufacturer=self.analyzer.manufacturer,
            model=self.analyzer.model,
            serial=self.analyzer.serial,
            firmware=self.analyzer.firmware,
            raw_idn=self.analyzer.raw_idn,
            profile_id=self.analyzer.profile_id,
        )
        self.assertIsNot(saved_analyzer, self.analyzer)
        save_routine(
            path,
            (saved_analyzer,),
            (
                SelectedFeature(
                    instrument=saved_analyzer,
                    feature_id="spectrum_analyzer.read_marker",
                ),
                WaitForCompletionStep(
                    instrument=saved_analyzer,
                    timeout_seconds=10,
                ),
            ),
        )
        self.tab.set_instruments((self.analyzer,))

        with (
            patch(
                f"{UI_MODULE}.filedialog.askopenfilename",
                return_value=str(path),
            ),
            patch(f"{UI_MODULE}.messagebox.showinfo"),
            patch(f"{UI_MODULE}.messagebox.showwarning") as warning,
        ):
            self.tab.load_button.invoke()

        warning.assert_not_called()
        self.assertEqual(len(self.tab.routine_steps), 2)
        self.assertIs(self.tab.routine_steps[0].instrument, self.analyzer)
        self.assertIs(self.tab.routine_steps[1].instrument, self.analyzer)

    def test_changed_resource_loads_by_unique_serial_identity(self) -> None:
        path = self._path("serial-fallback.scpiroutine.json")
        saved_analyzer = SelectedInstrument(
            resource="TCPIP0::198.51.100.20::inst0::INSTR",
            category=self.analyzer.category,
            manufacturer=self.analyzer.manufacturer,
            model=self.analyzer.model,
            serial=self.analyzer.serial,
            firmware=self.analyzer.firmware,
            raw_idn=self.analyzer.raw_idn,
            profile_id=self.analyzer.profile_id,
        )
        save_routine(
            path,
            (saved_analyzer,),
            (
                SelectedFeature(
                    instrument=saved_analyzer,
                    feature_id="spectrum_analyzer.peak_search",
                ),
            ),
        )
        self.tab.set_instruments((self.analyzer,))

        with (
            patch(
                f"{UI_MODULE}.filedialog.askopenfilename",
                return_value=str(path),
            ),
            patch(f"{UI_MODULE}.messagebox.showinfo") as info,
            patch(f"{UI_MODULE}.messagebox.showwarning") as warning,
        ):
            self.tab.load_button.invoke()

        info.assert_called_once()
        warning.assert_not_called()
        loaded_step = self.tab.routine_steps[0]
        self.assertIsInstance(loaded_step, SelectedFeature)
        self.assertIs(loaded_step.instrument, self.analyzer)
        self.assertEqual(loaded_step.device_resource, self.analyzer.resource)

    def test_missing_device_preserves_draft_and_shows_warning(self) -> None:
        path = self._path("missing-device.scpiroutine.json")
        save_routine(
            path,
            (self.generator,),
            (
                SelectedFeature(
                    instrument=self.generator,
                    feature_id="signal_generator.output_off",
                ),
            ),
        )
        self.tab.set_instruments((self.analyzer,))
        self._add_feature(0, "spectrum_analyzer.read_marker")
        draft_before = self.tab.routine_steps

        with (
            patch(
                f"{UI_MODULE}.filedialog.askopenfilename",
                return_value=str(path),
            ),
            patch(f"{UI_MODULE}.messagebox.showwarning") as warning,
            patch(f"{UI_MODULE}.messagebox.showinfo") as info,
            patch(f"{UI_MODULE}.messagebox.askyesno") as replace_prompt,
        ):
            self.tab.load_button.invoke()

        warning.assert_called_once()
        warning_text = warning.call_args.args[1]
        self.assertIn(self.generator.model, warning_text)
        self.assertIn(self.generator.resource, warning_text)
        self.assertIn("그대로", warning_text)
        info.assert_not_called()
        replace_prompt.assert_not_called()
        self.assertEqual(self.tab.routine_steps, draft_before)
        self.assertIs(self.tab.routine_steps[0], draft_before[0])

    def test_same_model_without_serial_is_not_treated_as_same_device(self) -> None:
        path = self._path("model-only.scpiroutine.json")
        saved = SelectedInstrument(
            resource="TCPIP0::192.0.2.30::inst0::INSTR",
            category=DeviceCategory.SPECTRUM_ANALYZER,
            manufacturer="Rohde&Schwarz",
            model="FSV30",
            serial="",
            profile_id="rohde-schwarz.fsv30",
        )
        current = SelectedInstrument(
            resource="TCPIP0::192.0.2.31::inst0::INSTR",
            category=DeviceCategory.SPECTRUM_ANALYZER,
            manufacturer=saved.manufacturer,
            model=saved.model,
            serial="",
            profile_id=saved.profile_id,
        )
        save_routine(
            path,
            (saved,),
            (
                SelectedFeature(
                    instrument=saved,
                    feature_id="spectrum_analyzer.peak_search",
                ),
            ),
        )
        self.tab.set_instruments((current,))

        with (
            patch(
                f"{UI_MODULE}.filedialog.askopenfilename",
                return_value=str(path),
            ),
            patch(f"{UI_MODULE}.messagebox.showwarning") as warning,
            patch(f"{UI_MODULE}.messagebox.showinfo") as info,
        ):
            self.tab.load_button.invoke()

        warning.assert_called_once()
        self.assertIn("장비를 찾지 못했어요", warning.call_args.args[1])
        info.assert_not_called()
        self.assertEqual(self.tab.routine_steps, ())

    def test_canceling_draft_replacement_keeps_every_existing_step(self) -> None:
        path = self._path("replacement.scpiroutine.json")
        save_routine(
            path,
            (self.analyzer,),
            (
                SelectedFeature(
                    instrument=self.analyzer,
                    feature_id="spectrum_analyzer.peak_search",
                ),
                DelayStep(seconds=3),
            ),
        )
        self.tab.set_instruments((self.analyzer,))
        self._add_feature(0, "spectrum_analyzer.read_marker")
        draft_before = self.tab.routine_steps

        with (
            patch(
                f"{UI_MODULE}.filedialog.askopenfilename",
                return_value=str(path),
            ),
            patch(
                f"{UI_MODULE}.messagebox.askyesno",
                return_value=False,
            ) as replace_prompt,
            patch(f"{UI_MODULE}.messagebox.showwarning") as warning,
            patch(f"{UI_MODULE}.messagebox.showinfo") as info,
        ):
            self.tab.load_button.invoke()

        replace_prompt.assert_called_once()
        warning.assert_not_called()
        info.assert_not_called()
        self.assertEqual(self.tab.routine_steps, draft_before)
        self.assertIs(self.tab.routine_steps[0], draft_before[0])
        self.assertIn("그대로", self.tab.status_var.get())

    def test_right_click_selects_clicked_row_and_sets_boundary_actions(self) -> None:
        self.tab.set_instruments((self.analyzer,))
        for seconds in (1, 2, 3):
            self._add_delay(seconds)
        self._map_window_for_list_coordinates()

        middle_event = self._right_click_event(1)
        with patch.object(
            self.tab.routine_context_menu,
            "tk_popup",
        ) as popup:
            result = self.tab._show_routine_context_menu(middle_event)

        self.assertEqual(result, "break")
        self.assertEqual(self.tab.routine_list.curselection(), (1,))
        popup.assert_called_once_with(middle_event.x_root, middle_event.y_root)
        for entry_index in (0, 2, 3, 4, 5, 7):
            self.assertEqual(
                str(
                    self.tab.routine_context_menu.entrycget(
                        entry_index,
                        "state",
                    )
                ),
                "normal",
            )

        first_event = self._right_click_event(0)
        with patch.object(self.tab.routine_context_menu, "tk_popup"):
            self.tab._show_routine_context_menu(first_event)
        self.assertEqual(
            str(self.tab.routine_context_menu.entrycget(2, "state")),
            "disabled",
        )
        self.assertEqual(
            str(self.tab.routine_context_menu.entrycget(4, "state")),
            "disabled",
        )
        self.assertEqual(
            str(self.tab.routine_context_menu.entrycget(3, "state")),
            "normal",
        )

        last_event = self._right_click_event(2)
        with patch.object(self.tab.routine_context_menu, "tk_popup"):
            self.tab._show_routine_context_menu(last_event)
        self.assertEqual(
            str(self.tab.routine_context_menu.entrycget(3, "state")),
            "disabled",
        )
        self.assertEqual(
            str(self.tab.routine_context_menu.entrycget(5, "state")),
            "disabled",
        )

    def test_context_commands_move_duplicate_and_delete_selected_step(self) -> None:
        self.tab.set_instruments((self.analyzer,))
        for seconds in (1, 2, 3):
            self._add_delay(seconds)
        self._map_window_for_list_coordinates()

        with patch.object(self.tab.routine_context_menu, "tk_popup"):
            self.tab._show_routine_context_menu(self._right_click_event(1))

        self.tab.routine_context_menu.invoke(4)
        self.assertEqual(
            [step.seconds for step in self.tab.routine_steps],
            [2.0, 1.0, 3.0],
        )
        self.assertEqual(self.tab.routine_list.curselection(), (0,))

        self.tab.routine_context_menu.invoke(5)
        self.assertEqual(
            [step.seconds for step in self.tab.routine_steps],
            [1.0, 3.0, 2.0],
        )
        self.assertEqual(self.tab.routine_list.curselection(), (2,))

        self.tab.routine_context_menu.invoke(0)
        self.assertEqual(
            [step.seconds for step in self.tab.routine_steps],
            [1.0, 3.0, 2.0, 2.0],
        )
        self.assertEqual(self.tab.routine_list.curselection(), (3,))

        self.tab.routine_context_menu.invoke(7)
        self.assertEqual(
            [step.seconds for step in self.tab.routine_steps],
            [1.0, 3.0, 2.0],
        )
        self.assertEqual(self.tab.routine_list.curselection(), (2,))

    def test_right_clicking_blank_space_clears_selection_without_menu(self) -> None:
        self.tab.set_instruments((self.analyzer,))
        self._add_delay(1)
        self._add_delay(2)
        self._map_window_for_list_coordinates()
        self._select_routine_row(0)
        last_bounds = self.tab.routine_list.bbox(1)
        self.assertIsNotNone(last_bounds)
        _x, last_y, _width, last_height = last_bounds
        blank_y = int(last_y + last_height + 20)
        self.assertLess(blank_y, self.tab.routine_list.winfo_height())
        event = SimpleNamespace(
            y=blank_y,
            x_root=self.tab.routine_list.winfo_rootx() + 10,
            y_root=self.tab.routine_list.winfo_rooty() + blank_y,
        )

        with patch.object(
            self.tab.routine_context_menu,
            "tk_popup",
        ) as popup:
            result = self.tab._show_routine_context_menu(event)

        self.assertEqual(result, "break")
        self.assertEqual(self.tab.routine_list.curselection(), ())
        popup.assert_not_called()
        self.assertEqual(str(self.tab.delete_button.cget("state")), "disabled")


if __name__ == "__main__":
    unittest.main()
