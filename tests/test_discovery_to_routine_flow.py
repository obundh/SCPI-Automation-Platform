from __future__ import annotations

import tkinter as tk
import unittest

from scpi_automation.app import InstrumentControllerApp
from scpi_automation.identity import (
    ClassificationConfidence,
    ClassificationResult,
    DeviceCategory,
    InstrumentIdentity,
)
from scpi_automation.routine import DelayStep, SelectedInstrument
from scpi_automation.transport import DiscoveryRecord, DiscoveryState


class DiscoveryToRoutineFlowTests(unittest.TestCase):
    def setUp(self) -> None:
        self.root = tk.Tk()
        self.root.withdraw()
        self.app = InstrumentControllerApp(self.root)
        self.root.update_idletasks()
        self.discovery = self.app.discovery_tab

    def tearDown(self) -> None:
        if self.root.winfo_exists():
            self.app.close()

    def _result_checkbuttons(self) -> list[tk.Checkbutton]:
        def descendants(widget: tk.Misc) -> list[tk.Misc]:
            children = list(widget.winfo_children())
            return children + [
                nested
                for child in children
                for nested in descendants(child)
            ]

        return [
            widget
            for widget in descendants(self.discovery.result_list)
            if isinstance(widget, tk.Checkbutton)
        ]

    def _demo_checkbuttons(self) -> list[tk.Checkbutton]:
        return [
            widget
            for widget in self._result_checkbuttons()
            if widget.cget("text") == "이 장비 사용"
        ]

    def test_demo_has_four_independent_checkboxes(self) -> None:
        self.discovery.show_demo_devices()
        self.root.update_idletasks()

        checkbuttons = self._demo_checkbuttons()
        selection_vars = tuple(self.discovery._selection_vars.values())

        self.assertEqual(len(checkbuttons), 4)
        self.assertEqual(len(selection_vars), 4)
        self.assertEqual(len({str(variable) for variable in selection_vars}), 4)
        self.assertTrue(all(not variable.get() for variable in selection_vars))

        checkbuttons[0].invoke()
        self.assertEqual(
            [variable.get() for variable in selection_vars],
            [True, False, False, False],
        )
        checkbuttons[1].invoke()
        self.assertEqual(
            [variable.get() for variable in selection_vars],
            [True, True, False, False],
        )

    def test_continue_button_is_disabled_with_no_device_selected(self) -> None:
        self.discovery.show_demo_devices()
        self.root.update_idletasks()

        self.assertEqual(self.discovery.selected_records(), ())
        self.assertEqual(
            str(self.discovery.continue_to_routine_button.cget("state")),
            "disabled",
        )

    def test_checking_one_device_does_not_switch_tabs_automatically(self) -> None:
        self.discovery.show_demo_devices()
        self.root.update_idletasks()
        self.app.notebook.select(self.discovery)

        self._demo_checkbuttons()[0].invoke()
        self.root.update_idletasks()

        self.assertEqual(
            self.app.notebook.select(),
            str(self.discovery),
        )
        self.assertEqual(len(self.discovery.selected_records()), 1)
        self.assertEqual(
            str(self.discovery.continue_to_routine_button.cget("state")),
            "normal",
        )

    def test_device_selection_does_not_resize_the_routine_action_box(self) -> None:
        self.root.geometry("1280x780+0+0")
        self.root.deiconify()
        self.discovery.show_demo_devices()
        self.root.update()

        routine_actions = self.discovery.continue_to_routine_button.master
        expected_sizes = (
            self.discovery.selection_count_label.winfo_width(),
            self.discovery.selection_count_label.winfo_height(),
            self.discovery.continue_to_routine_button.winfo_width(),
            self.discovery.continue_to_routine_button.winfo_height(),
            routine_actions.winfo_width(),
            routine_actions.winfo_height(),
        )

        for checkbutton in self._demo_checkbuttons():
            checkbutton.invoke()
            self.root.update_idletasks()
            self.root.update()
            self.assertEqual(
                (
                    self.discovery.selection_count_label.winfo_width(),
                    self.discovery.selection_count_label.winfo_height(),
                    self.discovery.continue_to_routine_button.winfo_width(),
                    self.discovery.continue_to_routine_button.winfo_height(),
                    routine_actions.winfo_width(),
                    routine_actions.winfo_height(),
                ),
                expected_sizes,
            )

    def test_two_selected_devices_are_passed_to_routine_tab(self) -> None:
        self.discovery.show_demo_devices()
        self.root.update_idletasks()
        selected_resources = (
            self.discovery._records[0].resource,
            self.discovery._records[2].resource,
        )
        for resource in selected_resources:
            self.discovery._selection_vars[resource].set(True)
        self.discovery._update_selection_controls()

        self.discovery.continue_to_routine_button.invoke()
        self.root.update_idletasks()

        instruments = self.app.routine_tab._instruments
        self.assertEqual(self.app.notebook.select(), str(self.app.routine_tab))
        self.assertEqual(len(instruments), 2)
        self.assertTrue(
            all(isinstance(instrument, SelectedInstrument) for instrument in instruments)
        )
        self.assertEqual(
            tuple(instrument.resource for instrument in instruments),
            selected_resources,
        )

    def test_operation_validation_results_are_passed_to_routine_tab(self) -> None:
        classification = ClassificationResult(
            category=DeviceCategory.SPECTRUM_ANALYZER,
            confidence=ClassificationConfidence.VALIDATED_PROFILE,
            matched_rule="bench validation",
            profile_id="rs_fsv_fsva",
            profile_status="hardware_validated_partial",
            compatible_capability_ids=("analyzer.frequency.center",),
            compatible_operation_ids=("analyzer.frequency.center::set",),
            incompatible_operation_ids=("analyzer.frequency.center::query",),
            unresolved_operation_ids=("analyzer.frequency.span::set",),
        )
        record = DiscoveryRecord(
            resource="TCPIP0::192.0.2.40::inst0::INSTR",
            interface="TCPIP0",
            state=DiscoveryState.IDENTIFIED,
            identity=InstrumentIdentity(
                raw="Rohde&Schwarz,FSV30,1234,3.60",
                manufacturer="Rohde&Schwarz",
                model="FSV30",
                serial="1234",
                firmware="3.60",
            ),
            classification=classification,
        )

        self.app._open_routine_setup((record,))

        instrument = self.app.routine_tab._instruments[0]
        self.assertEqual(
            instrument.compatible_operation_ids,
            classification.compatible_operation_ids,
        )
        self.assertEqual(
            instrument.incompatible_operation_ids,
            classification.incompatible_operation_ids,
        )
        self.assertEqual(
            instrument.unresolved_operation_ids,
            classification.unresolved_operation_ids,
        )
        self.assertEqual(
            self.app.plan_tab._spectrum_instruments[0],
            instrument,
        )

    def test_direct_idn_classification_cannot_be_selected_for_routine(self) -> None:
        self.discovery.manual_idn_var.set("Rohde&Schwarz,FSV30,123456,3.50")

        self.discovery.classify_manual_idn()
        self.root.update_idletasks()

        self.assertEqual(self.discovery.selected_records(), ())
        self.assertEqual(len(self.discovery._selection_vars), 1)
        self.assertFalse(next(iter(self.discovery._selection_vars.values())).get())
        self.assertEqual(
            str(self.discovery.continue_to_routine_button.cget("state")),
            "disabled",
        )
        selectors = self._result_checkbuttons()
        self.assertEqual(len(selectors), 1)
        self.assertEqual(selectors[0].cget("text"), "루틴 사용 불가")
        self.assertEqual(str(selectors[0].cget("state")), "disabled")

    def test_reopening_demo_clears_previous_selection(self) -> None:
        self.discovery.show_demo_devices()
        self.root.update_idletasks()
        first_resource = self.discovery._records[0].resource
        first_variable = self.discovery._selection_vars[first_resource]
        first_variable.set(True)
        self.discovery._update_selection_controls()
        self.assertEqual(len(self.discovery.selected_records()), 1)

        self.discovery.show_demo_devices()
        self.root.update_idletasks()

        self.assertEqual(self.discovery.selected_records(), ())
        self.assertTrue(
            all(not variable.get() for variable in self.discovery._selection_vars.values())
        )
        self.assertIsNot(self.discovery._selection_vars[first_resource], first_variable)
        self.assertEqual(
            str(self.discovery.continue_to_routine_button.cget("state")),
            "disabled",
        )

    def test_selected_and_unselected_tabs_use_identical_padding(self) -> None:
        selected_padding = tuple(
            str(value)
            for value in self.app.style.lookup(
                "TNotebook.Tab",
                "padding",
                ("selected",),
            )
        )
        unselected_padding = tuple(
            str(value)
            for value in self.app.style.lookup(
                "TNotebook.Tab",
                "padding",
                ("!selected",),
            )
        )

        self.assertEqual(selected_padding, unselected_padding)
        self.assertTrue(selected_padding)

    def test_app_has_five_guided_workflow_tabs(self) -> None:
        self.assertEqual(len(self.app.notebook.tabs()), 5)
        self.assertIn(
            "3. 계획서",
            self.app.notebook.tab(self.app.plan_tab, "text"),
        )
        self.assertIn(
            "4. 실제 실행",
            self.app.notebook.tab(self.app.execution_tab, "text"),
        )
        self.assertIn(
            "5. 결과 확인",
            self.app.notebook.tab(self.app.results_tab, "text"),
        )

    def test_plan_continue_copies_routine_and_plan_to_execution_tab(self) -> None:
        self.discovery.show_demo_devices()
        self.root.update_idletasks()
        resource = self.discovery._records[0].resource
        self.discovery._selection_vars[resource].set(True)
        self.discovery._update_selection_controls()
        self.discovery.continue_to_routine_button.invoke()
        self.app.routine_tab._steps = [DelayStep(0.5)]
        self.app._show_plan_tab()

        self.app.plan_tab.continue_button.invoke()
        self.root.update_idletasks()

        self.assertEqual(
            self.app.notebook.select(),
            str(self.app.execution_tab),
        )
        self.assertEqual(
            self.app.execution_tab.instruments,
            self.app.routine_tab._instruments,
        )
        self.assertEqual(
            self.app.execution_tab.routine_steps,
            (DelayStep(0.5),),
        )

    def test_selected_devices_are_also_passed_to_plan_tab(self) -> None:
        self.discovery.show_demo_devices()
        self.root.update_idletasks()
        analyzer_resource = self.discovery._records[0].resource
        self.discovery._selection_vars[analyzer_resource].set(True)
        self.discovery._update_selection_controls()

        self.discovery.continue_to_routine_button.invoke()
        self.root.update_idletasks()

        self.assertEqual(len(self.app.plan_tab._spectrum_instruments), 1)
        self.assertEqual(
            self.app.plan_tab._spectrum_instruments[0].resource,
            analyzer_resource,
        )

    def test_routine_continue_callback_opens_plan_tab(self) -> None:
        self.app.notebook.select(self.app.routine_tab)

        self.app.routine_tab._on_continue()
        self.root.update_idletasks()

        self.assertEqual(
            self.app.notebook.select(),
            str(self.app.plan_tab),
        )

    def test_switching_tabs_does_not_resize_root_or_notebook(self) -> None:
        self.root.geometry("1100x700+0+0")
        self.root.deiconify()
        self.root.update()
        expected_size = (
            self.root.winfo_width(),
            self.root.winfo_height(),
            self.app.notebook.winfo_width(),
            self.app.notebook.winfo_height(),
        )

        for tab in (
            self.app.routine_tab,
            self.app.plan_tab,
            self.discovery,
            self.app.routine_tab,
            self.app.plan_tab,
            self.discovery,
        ):
            with self.subTest(tab=str(tab)):
                self.app.notebook.select(tab)
                self.root.update_idletasks()
                self.root.update()
                actual_size = (
                    self.root.winfo_width(),
                    self.root.winfo_height(),
                    self.app.notebook.winfo_width(),
                    self.app.notebook.winfo_height(),
                )
                self.assertEqual(actual_size, expected_size)


if __name__ == "__main__":
    unittest.main()
