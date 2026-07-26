from __future__ import annotations

import tkinter as tk
import unittest
from types import SimpleNamespace

from scpi_automation.identity import DeviceCategory
from scpi_automation.routine import SelectedFeature, SelectedInstrument
from scpi_automation.ui.instrument_display_window import (
    EMPTY_MESSAGE,
    InstrumentDisplayWindow,
)


class InstrumentDisplayWindowTests(unittest.TestCase):
    def setUp(self) -> None:
        self.root = tk.Tk()
        self.root.withdraw()
        self.analyzer = SelectedInstrument(
            resource="DEMO::FSV30::INSTR",
            category=DeviceCategory.SPECTRUM_ANALYZER,
            manufacturer="Rohde&Schwarz",
            model="FSV30",
            serial="100001",
        )
        self.scope = SelectedInstrument(
            resource="DEMO::SCOPE::INSTR",
            category=DeviceCategory.OSCILLOSCOPE,
            manufacturer="Keysight",
            model="DSOX",
            serial="200002",
        )
        self.analyzer_query = SelectedFeature(
            instrument=self.analyzer,
            feature_id="spectrum_analyzer.cap.trace.read.query",
            result_name="Trace 1",
        )
        self.scope_query = SelectedFeature(
            instrument=self.scope,
            feature_id="oscilloscope.cap.waveform.data.query",
            result_name="CH1 Waveform",
        )
        self.window = InstrumentDisplayWindow(
            self.root,
            (self.analyzer, self.scope),
            (self.analyzer_query, self.scope_query),
        )
        self.root.update_idletasks()

    def tearDown(self) -> None:
        if self.window.winfo_exists():
            self.window.destroy()
        self.root.destroy()

    def test_empty_panels_never_draw_a_fake_trace(self) -> None:
        for panel in self.window.panels.values():
            self.assertFalse(panel.has_series)
            self.assertEqual(panel.trace_item_count, 0)
            empty_items = panel.display_canvas.find_withtag("empty_message")
            self.assertTrue(empty_items)
            self.assertEqual(
                panel.display_canvas.itemcget(empty_items[0], "text"),
                EMPTY_MESSAGE,
            )

    def test_actual_numeric_array_event_is_the_only_source_of_trace(self) -> None:
        self.window.update_from_event(
            SimpleNamespace(
                kind="measurement_recorded",
                step_index=1,
                resource=self.analyzer.resource,
                response="-80.0,-62.5,-41.25,-69.0",
                timestamp_utc="2026-07-25T13:00:00Z",
                capability_id="trace.read",
                response_type="float_array",
                unit="dBm",
            )
        )
        self.root.update_idletasks()

        panel = self.window.panels[self.analyzer.resource]
        self.assertEqual(panel.series, (-80.0, -62.5, -41.25, -69.0))
        self.assertTrue(panel.has_series)
        self.assertGreater(panel.trace_item_count, 0)
        self.assertIn("4 points", panel.value_var.get())

    def test_single_real_value_uses_numeric_readout_not_a_curve(self) -> None:
        self.window.update_from_event(
            SimpleNamespace(
                kind="measurement_recorded",
                step_index=1,
                resource=self.analyzer.resource,
                response="-42.75",
                timestamp_utc="2026-07-25T13:01:00Z",
            )
        )
        self.root.update_idletasks()

        panel = self.window.panels[self.analyzer.resource]
        self.assertFalse(panel.has_series)
        self.assertEqual(panel.trace_item_count, 0)
        self.assertEqual(panel.value_var.get(), "-42.75")
        self.assertTrue(panel.display_canvas.find_withtag("actual_scalar"))

    def test_multiple_devices_can_be_seen_together_or_switched(self) -> None:
        self.window.show_all()
        self.root.update_idletasks()
        self.assertTrue(self.window.panels[self.analyzer.resource].winfo_ismapped())
        self.assertTrue(self.window.panels[self.scope.resource].winfo_ismapped())

        self.assertTrue(self.window.show_device(self.scope.resource))
        self.root.update_idletasks()
        self.assertFalse(self.window.panels[self.analyzer.resource].winfo_ismapped())
        self.assertTrue(self.window.panels[self.scope.resource].winfo_ismapped())
        self.assertEqual(
            self.window.selected_resource_var.get(),
            self.scope.resource,
        )

    def test_terminal_result_uses_parsed_array_and_updates_run_status(self) -> None:
        result = SimpleNamespace(
            instruments=(self.analyzer, self.scope),
            routine_steps=(self.analyzer_query, self.scope_query),
            step_records=(),
            measurements=(
                SimpleNamespace(
                    resource=self.scope.resource,
                    result_name="CH1 Waveform",
                    feature_id=self.scope_query.feature_id,
                    raw_response="0.0,0.5,1.0,0.5,0.0",
                    parsed_value=(0.0, 0.5, 1.0, 0.5, 0.0),
                    unit="V",
                    step_index=2,
                    timestamp_utc="2026-07-25T13:02:00Z",
                    capability_id="waveform.data",
                    response_type="array",
                ),
            ),
            status="completed",
        )

        self.window.update_from_result(result)
        self.root.update_idletasks()

        scope_panel = self.window.panels[self.scope.resource]
        self.assertEqual(scope_panel.series, (0.0, 0.5, 1.0, 0.5, 0.0))
        self.assertGreater(scope_panel.trace_item_count, 0)
        self.assertEqual(scope_panel.status_var.get(), "실행 완료")
        self.assertIn("0.0,0.5,1.0", scope_panel.raw_var.get())

    def test_text_response_is_shown_as_text_and_never_as_trace(self) -> None:
        self.window.update_from_event(
            SimpleNamespace(
                kind="measurement_recorded",
                step_index=1,
                resource=self.analyzer.resource,
                response="AUTO",
                timestamp_utc="2026-07-25T13:03:00Z",
            )
        )
        self.root.update_idletasks()

        panel = self.window.panels[self.analyzer.resource]
        self.assertEqual(panel.value_var.get(), "AUTO")
        self.assertFalse(panel.has_series)
        self.assertEqual(panel.trace_item_count, 0)

    def test_untyped_numeric_array_is_summarized_not_drawn(self) -> None:
        self.window.update_from_event(
            SimpleNamespace(
                kind="measurement_recorded",
                step_index=1,
                resource=self.analyzer.resource,
                response="1,2,3,4,5",
                parsed_value=(1, 2, 3, 4, 5),
                timestamp_utc="2026-07-25T13:04:00Z",
                capability_id="measurement.unknown",
                response_type="float_array",
                unit="V",
            )
        )
        self.root.update_idletasks()

        panel = self.window.panels[self.analyzer.resource]
        self.assertFalse(panel.has_series)
        self.assertEqual(panel.trace_item_count, 0)
        self.assertIn("5개 값", panel.value_var.get())

    def test_focus_existing_and_unknown_device_selection_are_safe(self) -> None:
        self.assertTrue(self.window.focus_existing())
        self.assertFalse(self.window.show_device("NOT::FOUND"))

    def test_small_window_wraps_header_and_keeps_single_column_cards(self) -> None:
        self.window.geometry("760x560")
        self.window.update()

        self.assertLessEqual(
            int(self.window.header_subtitle.cget("wraplength")),
            600,
        )
        self.assertEqual(
            self.window.header_subtitle.winfo_reqheight(),
            self.window.header_subtitle.winfo_height(),
        )
        self.assertEqual(
            self.window.panels[self.analyzer.resource].grid_info()["column"],
            0,
        )
        self.assertEqual(
            self.window.panels[self.scope.resource].grid_info()["column"],
            0,
        )


if __name__ == "__main__":
    unittest.main()
