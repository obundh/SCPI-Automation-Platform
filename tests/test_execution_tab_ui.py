from __future__ import annotations

import threading
import time
import tkinter as tk
import unittest
from dataclasses import replace
from types import SimpleNamespace
from unittest.mock import patch

from scpi_automation.identity import DeviceCategory
from scpi_automation.planning import SpectrumPlanItem
from scpi_automation.routine import (
    DelayStep,
    SelectedFeature,
    SelectedInstrument,
    WaitForCompletionStep,
)
from scpi_automation.ui.execution_tab import ExecutionTab


class ExecutionTabUiTests(unittest.TestCase):
    def setUp(self) -> None:
        self.root = tk.Tk()
        self.root.withdraw()
        self.calls: list[dict[str, object]] = []
        self.results: list[object] = []
        self.fake_result = SimpleNamespace(
            status="completed",
            dry_run=True,
            run_id="RUN-UI-001",
        )

        def runner(**kwargs: object) -> object:
            self.calls.append(kwargs)
            callback = kwargs["event_callback"]
            callback(
                SimpleNamespace(
                    message="1단계를 확인했어요.",
                    level="INFO",
                    step_index=1,
                    total_steps=3,
                    timestamp_utc="2026-07-25T12:34:56Z",
                )
            )
            callback(
                SimpleNamespace(
                    message="모든 단계를 확인했어요.",
                    level="INFO",
                    step_index=3,
                    total_steps=3,
                    timestamp_utc="2026-07-25T12:34:57Z",
                )
            )
            return self.fake_result

        self.tab = ExecutionTab(
            self.root,
            on_result=self.results.append,
            execution_runner=runner,
        )
        self.tab.pack(fill="both", expand=True)
        self.analyzer = SelectedInstrument(
            resource="TCPIP0::192.0.2.30::inst0::INSTR",
            category=DeviceCategory.SPECTRUM_ANALYZER,
            manufacturer="Rohde&Schwarz",
            model="FSV30",
            serial="100001",
        )
        self.steps = (
            SelectedFeature(
                instrument=self.analyzer,
                feature_id="spectrum_analyzer.peak_search",
            ),
            DelayStep(seconds=0.5),
            WaitForCompletionStep(
                instrument=self.analyzer,
                timeout_seconds=5,
            ),
        )
        self.plan = (
            SpectrumPlanItem(
                instrument=self.analyzer,
                center_frequency_hz=1_000_000_000,
                span_hz=100_000_000,
                rbw_hz=100_000,
                vbw_hz=None,
                reference_level_dbm=0,
            ),
        )
        self.root.update_idletasks()

    def tearDown(self) -> None:
        if self.root.winfo_exists():
            self.tab.shutdown()
            self.root.destroy()

    def _wait_for_completion(self, timeout: float = 2.0) -> None:
        deadline = time.monotonic() + timeout
        while self.tab.is_running and time.monotonic() < deadline:
            self.root.update()
            time.sleep(0.01)
        self.root.update()
        self.assertFalse(self.tab.is_running, "background execution did not finish")

    def test_empty_context_explains_next_action_and_disables_run(self) -> None:
        self.assertTrue(self.tab.dry_run_var.get())
        self.assertEqual(str(self.tab.dry_run_button.cget("state")), "disabled")
        self.assertEqual(str(self.tab.live_run_button.cget("state")), "disabled")
        self.assertIn("루틴", self.tab.routine_list.get(0))
        self.assertIn("계획", self.tab.plan_list.get(0))

    def test_context_shows_routine_and_plan_side_by_side(self) -> None:
        self.tab.set_context((self.analyzer,), self.steps, self.plan)

        self.assertEqual(self.tab.instruments, (self.analyzer,))
        self.assertEqual(self.tab.routine_steps, self.steps)
        self.assertEqual(self.tab.plan_items, self.plan)
        self.assertEqual(self.tab.routine_list.size(), 3)
        self.assertIn("Peak Search", self.tab.routine_list.get(0))
        self.assertIn("0.5초", self.tab.routine_list.get(1))
        self.assertIn("완료 확인", self.tab.routine_list.get(2))
        self.assertEqual(self.tab.plan_list.size(), 1)
        self.assertIn("Center 1 GHz", self.tab.plan_list.get(0))
        self.assertIn("장비 1대", self.tab.context_summary_var.get())
        self.assertEqual(str(self.tab.dry_run_button.cget("state")), "normal")
        self.assertEqual(str(self.tab.live_run_button.cget("state")), "disabled")

    def test_dry_run_uses_background_runner_and_forwards_full_snapshot(self) -> None:
        self.tab.set_context((self.analyzer,), self.steps, self.plan)

        self.tab.dry_run_button.invoke()
        self._wait_for_completion()

        self.assertEqual(len(self.calls), 1)
        call = self.calls[0]
        self.assertTrue(call["dry_run"])
        self.assertEqual(call["instruments"], (self.analyzer,))
        self.assertEqual(call["routine_steps"], self.steps)
        self.assertEqual(call["plan_items"], self.plan)
        self.assertIsInstance(call["stop_event"], threading.Event)
        self.assertIsInstance(call["emergency_event"], threading.Event)
        self.assertEqual(self.tab.progress_var.get(), 100.0)
        self.assertIn("모든 단계를", self.tab.log_text.get("1.0", tk.END))
        self.assertEqual(self.results, [self.fake_result])

    def test_actual_run_requires_explicit_confirmation(self) -> None:
        self.tab.set_context((self.analyzer,), self.steps, self.plan)
        self.tab.dry_run_button.invoke()
        self._wait_for_completion()

        with patch(
            "scpi_automation.ui.execution_tab.messagebox.askyesno",
            return_value=False,
        ):
            self.tab.live_run_button.invoke()

        self.assertEqual(len(self.calls), 1)
        self.assertTrue(self.tab.dry_run_var.get())
        self.assertIn("취소", self.tab.status_var.get())

    def test_confirmed_actual_run_is_not_dry_run(self) -> None:
        self.tab.set_context((self.analyzer,), self.steps, self.plan)
        self.tab.dry_run_button.invoke()
        self._wait_for_completion()
        self.fake_result = SimpleNamespace(
            status="completed",
            dry_run=False,
            run_id="RUN-LIVE-001",
        )

        with patch(
            "scpi_automation.ui.execution_tab.messagebox.askyesno",
            return_value=True,
        ):
            self.tab.live_run_button.invoke()
        self._wait_for_completion()

        self.assertFalse(self.calls[1]["dry_run"])
        self.assertTrue(self.calls[1]["operator_confirmed"])
        self.assertFalse(self.tab.dry_run_var.get())

    def test_demo_device_can_dry_run_but_cannot_send_live_commands(self) -> None:
        demo = replace(self.analyzer, resource="DEMO::FSV30::INSTR")
        demo_steps = tuple(
            replace(step, instrument=demo)
            if isinstance(step, (SelectedFeature, WaitForCompletionStep))
            else step
            for step in self.steps
        )
        demo_plan = (replace(self.plan[0], instrument=demo),)
        self.tab.set_context((demo,), demo_steps, demo_plan)

        self.tab.dry_run_button.invoke()
        self._wait_for_completion()

        self.assertEqual(len(self.calls), 1)
        self.assertEqual(
            str(self.tab.live_run_button.cget("state")),
            "disabled",
        )
        self.tab.start_live_run()
        self.assertIn("데모 장비", self.tab.status_var.get())

    def test_actual_value_display_replays_last_result_and_receives_events(self) -> None:
        class FakeDisplay:
            def __init__(self, *_args: object) -> None:
                self.events: list[object] = []
                self.results: list[object] = []

            def focus_existing(self) -> bool:
                return True

            def set_routine_steps(self, _steps: object) -> None:
                return

            def set_instruments(self, *_args: object) -> None:
                return

            def update_from_event(self, event: object) -> None:
                self.events.append(event)

            def update_from_result(self, result: object) -> None:
                self.results.append(result)

            def winfo_exists(self) -> bool:
                return True

            def destroy(self) -> None:
                return

        self.tab.set_context((self.analyzer,), self.steps, self.plan)
        self.assertEqual(
            str(self.tab.display_button.cget("state")),
            "normal",
        )

        with patch(
            "scpi_automation.ui.execution_tab.InstrumentDisplayWindow",
            side_effect=FakeDisplay,
        ):
            self.tab.open_actual_value_display()
            display = self.tab._display_window
            self.tab.dry_run_button.invoke()
            self._wait_for_completion()

        self.assertIsNotNone(display)
        self.assertEqual(len(display.events), 2)
        self.assertEqual(display.results, [self.fake_result])

        self.tab._display_window = None
        with patch(
            "scpi_automation.ui.execution_tab.InstrumentDisplayWindow",
            side_effect=FakeDisplay,
        ):
            self.tab.open_actual_value_display()
        self.assertEqual(
            self.tab._display_window.results,
            [self.fake_result],
        )

    def test_emergency_stop_sets_both_engine_signals(self) -> None:
        started = threading.Event()
        captured: dict[str, object] = {}

        def blocking_runner(**kwargs: object) -> object:
            captured.update(kwargs)
            started.set()
            emergency_event = kwargs["emergency_event"]
            emergency_event.wait(timeout=2)
            return SimpleNamespace(
                status="emergency_stopped",
                dry_run=True,
                run_id="RUN-STOP-001",
            )

        self.tab._execution_runner = blocking_runner
        self.tab.set_context((self.analyzer,), self.steps, self.plan)
        self.tab.start_dry_run()
        self.assertTrue(started.wait(timeout=1))

        self.tab.emergency_button.invoke()
        self._wait_for_completion()

        self.assertTrue(captured["stop_event"].is_set())
        self.assertTrue(captured["emergency_event"].is_set())
        self.assertIn("비상정지", self.tab.log_text.get("1.0", tk.END))

    def test_context_cannot_change_during_a_run(self) -> None:
        release = threading.Event()
        started = threading.Event()

        def blocking_runner(**_kwargs: object) -> object:
            started.set()
            release.wait(timeout=2)
            return self.fake_result

        self.tab._execution_runner = blocking_runner
        self.tab.set_context((self.analyzer,), self.steps, self.plan)
        self.tab.start_dry_run()
        self.assertTrue(started.wait(timeout=1))

        self.tab.set_context((), (), ())

        self.assertEqual(self.tab.instruments, (self.analyzer,))
        self.assertIn("실행 중", self.tab.status_var.get())
        release.set()
        self._wait_for_completion()

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
