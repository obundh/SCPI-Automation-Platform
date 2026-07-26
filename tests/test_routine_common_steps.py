from __future__ import annotations

import math
import unittest
from dataclasses import FrozenInstanceError
from typing import get_args

from scpi_automation.identity import DeviceCategory
from scpi_automation.routine import (
    DelayStep,
    PlanBoundDelayStep,
    RoutineStep,
    SelectedFeature,
    SelectedInstrument,
    WaitForCompletionStep,
    create_delay,
    create_plan_bound_delay,
    wait_for_completion,
)


class RoutineCommonStepTests(unittest.TestCase):
    def setUp(self) -> None:
        self.instrument = SelectedInstrument(
            resource="TCPIP0::192.0.2.10::inst0::INSTR",
            category=DeviceCategory.SPECTRUM_ANALYZER,
            manufacturer="Rohde&Schwarz",
            model="FSV30",
            serial="123456",
        )

    def test_create_delay_accepts_valid_values_and_normalizes_to_float(self) -> None:
        for value in (0.1, 1, 1.25, 3600):
            with self.subTest(value=value):
                step = create_delay(value)

                self.assertIsInstance(step, DelayStep)
                self.assertEqual(step.seconds, float(value))
                self.assertIsInstance(step.seconds, float)

    def test_create_delay_rejects_invalid_durations(self) -> None:
        invalid_values = (0, -1, math.nan, math.inf, -math.inf, 3600.0001)

        for value in invalid_values:
            with self.subTest(value=value):
                with self.assertRaises(ValueError):
                    create_delay(value)

    def test_wait_for_completion_keeps_explicit_target_and_timeout(self) -> None:
        step = wait_for_completion(self.instrument, 30)

        self.assertIsInstance(step, WaitForCompletionStep)
        self.assertIs(step.instrument, self.instrument)
        self.assertEqual(step.device_resource, self.instrument.resource)
        self.assertEqual(step.timeout_seconds, 30.0)

    def test_wait_for_completion_accepts_boundary_values(self) -> None:
        for value in (0.1, 3600):
            with self.subTest(value=value):
                step = wait_for_completion(self.instrument, value)
                self.assertEqual(step.timeout_seconds, float(value))

    def test_wait_for_completion_rejects_invalid_timeouts(self) -> None:
        invalid_values = (0, -0.1, math.nan, math.inf, -math.inf, 3601)

        for value in invalid_values:
            with self.subTest(value=value):
                with self.assertRaises(ValueError):
                    wait_for_completion(self.instrument, value)

    def test_common_steps_are_frozen_and_slotted(self) -> None:
        delay = create_delay(1)
        completion = wait_for_completion(self.instrument, 30)

        with self.assertRaises(FrozenInstanceError):
            delay.seconds = 2  # type: ignore[misc]
        with self.assertRaises(FrozenInstanceError):
            completion.timeout_seconds = 60  # type: ignore[misc]
        self.assertFalse(hasattr(delay, "__dict__"))
        self.assertFalse(hasattr(completion, "__dict__"))

    def test_routine_step_alias_contains_all_supported_step_types(self) -> None:
        self.assertEqual(
            set(get_args(RoutineStep)),
            {
                SelectedFeature,
                DelayStep,
                PlanBoundDelayStep,
                WaitForCompletionStep,
            },
        )

    def test_plan_bound_delay_requires_a_signal_generator(self) -> None:
        generator = SelectedInstrument(
            resource="DEMO::SMB100A::INSTR",
            category=DeviceCategory.SIGNAL_GENERATOR,
        )

        step = create_plan_bound_delay(generator)

        self.assertIsInstance(step, PlanBoundDelayStep)
        self.assertEqual(step.field_id, "dwell_seconds")
        with self.assertRaises(ValueError):
            create_plan_bound_delay(self.instrument)


if __name__ == "__main__":
    unittest.main()
