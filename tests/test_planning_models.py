from __future__ import annotations

import unittest

from scpi_automation.identity import DeviceCategory
from scpi_automation.planning import (
    SignalGeneratorPlanItem,
    SpectrumPlanItem,
    generate_frequency_series,
    parse_frequency_list,
)
from scpi_automation.routine import SelectedInstrument


class SpectrumPlanItemTests(unittest.TestCase):
    def setUp(self) -> None:
        self.analyzer = SelectedInstrument(
            resource="DEMO::FSV30::INSTR",
            category=DeviceCategory.SPECTRUM_ANALYZER,
            manufacturer="Rohde&Schwarz",
            model="FSV30",
        )

    def test_values_are_normalized_and_start_stop_are_derived(self) -> None:
        item = SpectrumPlanItem(
            instrument=self.analyzer,
            center_frequency_hz=1_000_000_000,
            span_hz=100_000_000,
            rbw_hz=100_000,
            vbw_hz=None,
            reference_level_dbm=0,
        )

        self.assertEqual(item.center_frequency_hz, 1_000_000_000.0)
        self.assertEqual(item.start_frequency_hz, 950_000_000.0)
        self.assertEqual(item.stop_frequency_hz, 1_050_000_000.0)
        self.assertIsNone(item.vbw_hz)

    def test_non_spectrum_instrument_is_rejected(self) -> None:
        generator = SelectedInstrument(
            resource="DEMO::SMB100A::INSTR",
            category=DeviceCategory.SIGNAL_GENERATOR,
            model="SMB100A",
        )

        with self.assertRaisesRegex(ValueError, "스펙트럼 분석기"):
            SpectrumPlanItem(
                instrument=generator,
                center_frequency_hz=1_000_000_000,
                span_hz=100_000_000,
                rbw_hz=100_000,
                vbw_hz=None,
                reference_level_dbm=0,
            )

    def test_span_cannot_produce_a_negative_start_frequency(self) -> None:
        with self.assertRaisesRegex(ValueError, "시작 주파수"):
            SpectrumPlanItem(
                instrument=self.analyzer,
                center_frequency_hz=10,
                span_hz=100,
                rbw_hz=1,
                vbw_hz=1,
                reference_level_dbm=0,
            )


class SignalGeneratorPlanItemTests(unittest.TestCase):
    def setUp(self) -> None:
        self.generator = SelectedInstrument(
            resource="DEMO::SMB100A::INSTR",
            category=DeviceCategory.SIGNAL_GENERATOR,
            model="SMB100A",
        )

    def test_valid_signal_generator_plan(self) -> None:
        item = SignalGeneratorPlanItem(
            instrument=self.generator,
            frequency_hz=1_000_000_000,
            power_dbm=-20,
            dwell_seconds=1.5,
        )

        self.assertEqual(item.frequency_hz, 1_000_000_000)
        self.assertEqual(item.power_dbm, -20)
        self.assertEqual(item.dwell_seconds, 1.5)

    def test_wrong_category_and_invalid_values_are_rejected(self) -> None:
        analyzer = SelectedInstrument(
            resource="DEMO::FSV30::INSTR",
            category=DeviceCategory.SPECTRUM_ANALYZER,
        )
        with self.assertRaisesRegex(ValueError, "신호발생기"):
            SignalGeneratorPlanItem(analyzer, 1, -20, 1)
        for frequency, power, dwell in (
            (0, -20, 1),
            (float("nan"), -20, 1),
            (1, float("inf"), 1),
            (1, -20, 0),
            (1, -20, 3601),
        ):
            with self.subTest(
                frequency=frequency,
                power=power,
                dwell=dwell,
            ):
                with self.assertRaises(ValueError):
                    SignalGeneratorPlanItem(
                        self.generator,
                        frequency,
                        power,
                        dwell,
                    )


class FrequencySeriesTests(unittest.TestCase):
    def test_manual_list_preserves_order_and_duplicates(self) -> None:
        self.assertEqual(
            parse_frequency_list("100, 200\n100; 500", 1_000_000),
            (
                100_000_000,
                200_000_000,
                100_000_000,
                500_000_000,
            ),
        )

    def test_manual_list_enforces_point_limit_before_returning(self) -> None:
        values = ",".join(str(index + 1) for index in range(501))
        with self.assertRaisesRegex(ValueError, "최대 500개"):
            parse_frequency_list(values, 1)

    def test_linear_series_is_inclusive_and_exact(self) -> None:
        self.assertEqual(
            generate_frequency_series(100, 500, 100),
            (100, 200, 300, 400, 500),
        )
        self.assertEqual(generate_frequency_series(100, 100, 10), (100,))

    def test_linear_series_rejects_misaligned_or_invalid_ranges(self) -> None:
        for start, stop, step in (
            (100, 550, 100),
            (500, 100, 100),
            (100, 500, 0),
            (float("nan"), 500, 100),
        ):
            with self.subTest(start=start, stop=stop, step=step):
                with self.assertRaises(ValueError):
                    generate_frequency_series(start, stop, step)

    def test_linear_series_rejects_more_than_500_points(self) -> None:
        with self.assertRaisesRegex(ValueError, "최대 500개"):
            generate_frequency_series(1, 501, 1)


if __name__ == "__main__":
    unittest.main()
