from __future__ import annotations

import unittest

from scpi_automation.identity import DeviceCategory
from scpi_automation.routine import (
    FeatureRisk,
    RoutineFeature,
    RoutineParameter,
)
from scpi_automation.ui.value_formatting import (
    format_display_value,
    format_engineering_value,
    format_feature_arguments,
)


class ValueFormattingTests(unittest.TestCase):
    def test_frequency_uses_readable_si_prefix(self) -> None:
        self.assertEqual(
            format_engineering_value(1_500_000_000, "Hz"),
            "1.5 GHz",
        )
        self.assertEqual(format_engineering_value(100_000, "Hz"), "100 kHz")

    def test_time_and_electrical_values_use_readable_prefixes(self) -> None:
        self.assertEqual(format_engineering_value(0.0005, "s"), "500 µs")
        self.assertEqual(format_engineering_value(0.025, "V"), "25 mV")
        self.assertEqual(format_engineering_value(0.002, "A"), "2 mA")

    def test_large_arrays_are_summarized_without_inventing_points(self) -> None:
        self.assertEqual(
            format_display_value((1, 2, 3, 4, 5), "V"),
            "5개 값 · 최소 1 V · 최대 5 V",
        )

    def test_feature_arguments_replace_raw_names_and_base_units(self) -> None:
        feature = RoutineFeature(
            feature_id="signal_generator.test",
            category=DeviceCategory.SIGNAL_GENERATOR,
            display_name="테스트",
            description="테스트",
            risk=FeatureRisk.SAFE,
            parameters=(
                RoutineParameter("value", "number", unit="Hz"),
                RoutineParameter("state", "boolean"),
            ),
        )

        self.assertEqual(
            format_feature_arguments(
                feature,
                (("value", "1000000000"), ("state", "false")),
            ),
            "설정값 1 GHz · 상태 끄기 (OFF)",
        )


if __name__ == "__main__":
    unittest.main()
