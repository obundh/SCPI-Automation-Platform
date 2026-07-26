from __future__ import annotations

import tkinter as tk
import unittest
from unittest.mock import patch

from scpi_automation.identity import DeviceCategory
from scpi_automation.routine import (
    FeatureRisk,
    RoutineFeature,
    RoutineParameter,
    SelectedInstrument,
)
from scpi_automation.ui.routine_parameter_dialog import (
    RoutineParameterDialog,
)


class RoutineParameterUnitHelperTests(unittest.TestCase):
    def test_query_result_name_uses_beginner_friendly_display_name(self) -> None:
        feature = RoutineFeature(
            feature_id="spectrum_analyzer.test_center_query",
            category=DeviceCategory.SPECTRUM_ANALYZER,
            display_name="Center Frequency Read - 중심 주파수 읽기",
            description="현재 중심 주파수를 읽어요.",
            risk=FeatureRisk.SAFE,
            capability_id="analyzer.frequency.center",
            operation="query",
        )
        local_feature = RoutineFeature(
            feature_id="spectrum_analyzer.test_local_query",
            category=DeviceCategory.SPECTRUM_ANALYZER,
            display_name="사용자 온도 Read - 값 읽기",
            description="현재 온도를 읽어요.",
            risk=FeatureRisk.SAFE,
            capability_id="local.temperature",
            operation="query",
        )

        self.assertEqual(
            RoutineParameterDialog._default_result_name(feature),
            "중심 주파수 결과",
        )
        self.assertEqual(
            RoutineParameterDialog._default_result_name(local_feature),
            "사용자 온도 결과",
        )

    def test_frequency_hint_uses_readable_engineering_units(self) -> None:
        parameter = RoutineParameter(
            name="value",
            value_type="float",
            unit="Hz",
            minimum=100_000,
            maximum=20_000_000_000,
        )

        hint = RoutineParameterDialog._parameter_hint(parameter)

        self.assertIn("허용 범위 100 kHz ~ 20 GHz", hint)
        self.assertIn("1 + GHz", hint)

    def test_frequency_scientific_notation_normalizes_to_hz(self) -> None:
        parameter = RoutineParameter(
            name="value",
            value_type="float",
            unit="Hz",
        )

        self.assertEqual(
            RoutineParameterDialog._normalize_display_value(
                "1e-3",
                "GHz",
                parameter,
            ),
            "1000000",
        )
        self.assertEqual(
            RoutineParameterDialog._normalize_display_value(
                "2.5e6",
                "Hz",
                parameter,
            ),
            "2500000",
        )

    def test_common_si_prefixes_normalize_to_catalog_base_unit(self) -> None:
        cases = (
            ("V", "500", "mV", "0.5"),
            ("A", "250", "mA", "0.25"),
            ("W", "10", "mW", "0.01"),
            ("Ohm", "2.2", "kΩ", "2200"),
            ("seconds", "500", "ms", "0.5"),
        )

        for base_unit, value, display_unit, expected in cases:
            with self.subTest(base_unit=base_unit):
                parameter = RoutineParameter(
                    name="value",
                    value_type="float",
                    unit=base_unit,
                )
                self.assertEqual(
                    RoutineParameterDialog._normalize_display_value(
                        value,
                        display_unit,
                        parameter,
                    ),
                    expected,
                )

    def test_enum_boolean_string_and_composite_types_keep_existing_ui(self) -> None:
        parameters = (
            RoutineParameter(
                name="mode",
                value_type="enum",
                unit="Hz",
                choices=("AUTO", "MANUAL"),
            ),
            RoutineParameter(
                name="state",
                value_type="boolean",
                unit="V",
            ),
            RoutineParameter(
                name="source",
                value_type="string",
                unit="A",
            ),
            RoutineParameter(
                name="values",
                value_type="voltage_current_time_triplets",
                unit="s",
            ),
        )

        for parameter in parameters:
            with self.subTest(value_type=parameter.value_type):
                self.assertIsNone(
                    RoutineParameterDialog._unit_family(parameter)
                )

    def test_number_or_auto_keeps_auto_mnemonic(self) -> None:
        parameter = RoutineParameter(
            name="value",
            value_type="number_or_auto",
            unit="Hz",
        )

        self.assertEqual(
            RoutineParameterDialog._normalize_display_value(
                "AUTO",
                "MHz",
                parameter,
            ),
            "AUTO",
        )

    def test_internal_scpi_choices_have_friendly_labels(self) -> None:
        parameter = RoutineParameter(
            name="mode",
            value_type="enum",
            choices=("WRIT", "MAXH"),
        )

        self.assertEqual(
            RoutineParameterDialog._friendly_choice(parameter, "WRIT"),
            "Clear Write - 새로 쓰기 (WRIT)",
        )
        self.assertEqual(
            RoutineParameterDialog._friendly_choice(parameter, "MAXH"),
            "Max Hold - 최댓값 유지 (MAXH)",
        )


class RoutineParameterUnitUiTests(unittest.TestCase):
    def setUp(self) -> None:
        self.root = tk.Tk()
        self.root.withdraw()
        self.instrument = SelectedInstrument(
            resource="DEMO::SMB100A::INSTR",
            category=DeviceCategory.SIGNAL_GENERATOR,
            manufacturer="Rohde & Schwarz",
            model="SMB100A",
        )
        self.parameter = RoutineParameter(
            name="value",
            value_type="float",
            unit="Hz",
            minimum=100_000,
            maximum=20_000_000_000,
        )
        self.feature = RoutineFeature(
            feature_id="signal_generator.test_frequency",
            category=DeviceCategory.SIGNAL_GENERATOR,
            display_name="Frequency - 주파수 설정",
            description="시험용 주파수 입력",
            risk=FeatureRisk.SAFE,
            capability_id="source.frequency",
            operation="set",
            group="source",
            scpi_preview="SOUR:FREQ {value}",
            parameters=(self.parameter,),
        )
        self.added: list[object] = []
        self.dialog = RoutineParameterDialog(
            self.root,
            instrument=self.instrument,
            feature=self.feature,
            on_add=self.added.append,
        )
        self.root.update_idletasks()

    def tearDown(self) -> None:
        if self.root.winfo_exists():
            self.root.destroy()

    def test_frequency_row_offers_hz_khz_mhz_and_ghz(self) -> None:
        self.assertEqual(
            tuple(self.dialog._unit_widgets["value"].cget("values")),
            ("Hz", "kHz", "MHz", "GHz"),
        )
        self.assertEqual(self.dialog._unit_vars["value"].get(), "GHz")

    def test_bindable_setpoint_defaults_to_test_plan(self) -> None:
        self.assertTrue(self.dialog._binding_vars["value"].get())
        self.assertEqual(
            str(self.dialog._value_widgets["value"].cget("state")),
            "disabled",
        )

    def test_changing_display_unit_preserves_physical_value(self) -> None:
        self.dialog._value_vars["value"].set("1")

        self.dialog._unit_vars["value"].set("MHz")
        self.dialog._on_unit_changed("value")
        self.assertEqual(self.dialog._value_vars["value"].get(), "1000")

        self.dialog._unit_vars["value"].set("Hz")
        self.dialog._on_unit_changed("value")
        self.assertEqual(
            self.dialog._value_vars["value"].get(),
            "1000000000",
        )

    @patch(
        "scpi_automation.ui.routine_parameter_dialog.select_feature"
    )
    def test_apply_hands_normalized_hz_to_existing_validation(
        self,
        select_feature_mock,
    ) -> None:
        selected = object()
        select_feature_mock.return_value = selected
        self.dialog._binding_vars["value"].set(False)
        self.dialog._sync_binding_state("value")
        self.dialog._value_vars["value"].set("1")

        self.dialog._apply()

        self.assertEqual(self.added, [selected])
        self.assertEqual(
            select_feature_mock.call_args.kwargs["arguments"],
            {"value": "1000000000"},
        )

    @patch(
        "scpi_automation.ui.routine_parameter_dialog.select_feature"
    )
    def test_exact_probe_rejection_is_not_bypassed(
        self,
        select_feature_mock,
    ) -> None:
        select_feature_mock.side_effect = ValueError(
            "현재 허용값: value=1000000000"
        )
        self.dialog._binding_vars["value"].set(False)
        self.dialog._sync_binding_state("value")
        self.dialog._value_vars["value"].set("2")

        self.dialog._apply()

        self.assertEqual(self.added, [])
        self.assertEqual(
            select_feature_mock.call_args.kwargs["arguments"],
            {"value": "2000000000"},
        )
        self.assertIn("현재 허용값", self.dialog.status_var.get())
        self.assertTrue(self.dialog.winfo_exists())

    @patch(
        "scpi_automation.ui.routine_parameter_dialog.select_feature"
    )
    def test_friendly_choice_is_converted_back_to_catalog_value(
        self,
        select_feature_mock,
    ) -> None:
        self.dialog.destroy()
        selected = object()
        select_feature_mock.return_value = selected
        mode = RoutineParameter(
            name="mode",
            value_type="enum",
            choices=("WRIT", "MAXH"),
        )
        feature = RoutineFeature(
            feature_id="signal_generator.test_mode",
            category=DeviceCategory.SIGNAL_GENERATOR,
            display_name="Trace Mode - 트레이스 방식",
            description="트레이스 표시 방식을 선택해요.",
            risk=FeatureRisk.SAFE,
            capability_id="trace.mode",
            operation="set",
            parameters=(mode,),
        )
        self.dialog = RoutineParameterDialog(
            self.root,
            instrument=self.instrument,
            feature=feature,
            on_add=self.added.append,
        )
        self.root.update_idletasks()
        self.dialog._value_vars["mode"].set(
            "Max Hold - 최댓값 유지 (MAXH)"
        )

        self.dialog._apply()

        self.assertEqual(self.added, [selected])
        self.assertEqual(
            select_feature_mock.call_args.kwargs["arguments"],
            {"mode": "MAXH"},
        )


if __name__ == "__main__":
    unittest.main()
