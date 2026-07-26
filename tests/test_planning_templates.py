from __future__ import annotations

import unittest
from dataclasses import FrozenInstanceError

from scpi_automation.identity import DeviceCategory
from scpi_automation.planning import (
    CATEGORY_PLAN_TEMPLATES,
    COMMON_PLAN_FIELDS,
    PLAN_ASSISTANCE_NOTICE_KO,
    GenericPlanItem,
    PlanFieldDefinition,
    PlanFieldType,
    plan_supported_categories,
    template_for_category,
)
from scpi_automation.routine import SelectedInstrument


EXPECTED_CATEGORIES = {
    DeviceCategory.SPECTRUM_ANALYZER,
    DeviceCategory.SIGNAL_GENERATOR,
    DeviceCategory.FUNCTION_GENERATOR,
    DeviceCategory.OSCILLOSCOPE,
    DeviceCategory.DIGITAL_MULTIMETER,
    DeviceCategory.POWER_SUPPLY,
    DeviceCategory.LCR_METER,
    DeviceCategory.NETWORK_ANALYZER,
}

EXPECTED_COMMON_FIELDS = {
    "standard_procedure",
    "sample_description",
    "environment_conditions",
    "stabilization_seconds",
    "repeat_count",
    "acceptance_criteria",
    "calibration_status",
    "safety_confirmed",
}


def _raw_values(
    fields: tuple[PlanFieldDefinition, ...],
) -> dict[str, str | bool | int | float]:
    result: dict[str, str | bool | int | float] = {}
    for field in fields:
        if field.must_be_true:
            result[field.field_id] = True
        elif field.default is not None:
            result[field.field_id] = field.default
        elif field.required and field.field_type is PlanFieldType.BOOLEAN:
            result[field.field_id] = True
        elif field.required and field.field_type is PlanFieldType.CHOICE:
            result[field.field_id] = field.choices[0]
        elif field.required and field.field_type is PlanFieldType.INTEGER:
            result[field.field_id] = max(1, int(field.minimum or 1))
        elif field.required and field.field_type is PlanFieldType.NUMBER:
            result[field.field_id] = max(1.0, float(field.minimum or 1.0))
        elif field.required:
            result[field.field_id] = f"{field.label_ko} 시험값"
        else:
            result[field.field_id] = ""
    return result


def _build_generic_item(
    category: DeviceCategory,
    method_id: str,
    **overrides: str | bool | int | float,
) -> GenericPlanItem:
    template = template_for_category(category)
    method = template.method_by_id(method_id)
    common = _raw_values(COMMON_PLAN_FIELDS)
    common.update(
        {
            "standard_procedure": "SOP-SAFETY-001",
            "sample_description": "DUT-A",
            "acceptance_criteria": "승인 한계 적용",
        }
    )
    detail = _raw_values(template.fields_for_method(method_id))
    detail.update(dict(method.recommended_values))
    detail.update(overrides)
    return GenericPlanItem.from_raw(
        instrument=SelectedInstrument(
            resource=f"DEMO::{category.value}::INSTR",
            category=category,
            manufacturer="Demo",
            model=category.label_ko,
        ),
        method_id=method_id,
        common_values=common,
        detail_values=detail,
        assistance_notice_acknowledged=True,
    )


class PlanningTemplateTests(unittest.TestCase):
    def test_all_eight_user_categories_have_methods_fields_and_examples(self) -> None:
        self.assertEqual(set(plan_supported_categories()), EXPECTED_CATEGORIES)
        self.assertEqual(set(CATEGORY_PLAN_TEMPLATES), EXPECTED_CATEGORIES)
        for category, template in CATEGORY_PLAN_TEMPLATES.items():
            with self.subTest(category=category):
                self.assertGreaterEqual(len(template.methods), 3)
                self.assertGreaterEqual(len(template.detail_fields), 15)
                self.assertGreaterEqual(len(template.standard_examples), 2)
                self.assertTrue(template.summary_ko)
                for method in template.methods:
                    self.assertTrue(method.procedure_steps)
                    self.assertTrue(method.expected_results)
                    self.assertTrue(template.fields_for_method(method.method_id))

    def test_common_context_and_non_guarantee_notice_are_explicit(self) -> None:
        self.assertEqual(
            {field.field_id for field in COMMON_PLAN_FIELDS},
            EXPECTED_COMMON_FIELDS,
        )
        self.assertIn("표준 준수", PLAN_ASSISTANCE_NOTICE_KO)
        self.assertIn("보증하지 않습니다", PLAN_ASSISTANCE_NOTICE_KO)
        safety = next(
            field
            for field in COMMON_PLAN_FIELDS
            if field.field_id == "safety_confirmed"
        )
        self.assertTrue(safety.required)
        self.assertTrue(safety.must_be_true)

    def test_requested_category_specific_considerations_are_present(self) -> None:
        expected = {
            DeviceCategory.SPECTRUM_ANALYZER: {
                "detector",
                "rbw_hz",
                "vbw_hz",
                "dwell_seconds",
                "scan_mode",
                "overload_check",
                "preamp_state",
                "input_attenuation_db",
                "trace_mode",
                "peak_excursion_db",
            },
            DeviceCategory.SIGNAL_GENERATOR: {
                "dut_level_correction_db",
                "load_impedance_ohm",
                "pulse_modulation",
                "trigger_source",
            },
            DeviceCategory.FUNCTION_GENERATOR: {
                "dut_level_correction",
                "load_impedance",
                "trigger_source",
                "burst_state",
            },
            DeviceCategory.OSCILLOSCOPE: {
                "probe_ratio",
                "bandwidth_limit",
                "sample_rate",
                "record_length",
                "trigger_mode",
                "pretrigger_percent",
            },
            DeviceCategory.DIGITAL_MULTIMETER: {
                "connection_method",
                "nplc",
                "range_mode",
                "trigger_source",
            },
            DeviceCategory.POWER_SUPPLY: {
                "ovp_v",
                "ocp_a",
                "ramp_rate_v_per_s",
                "load_wiring",
                "dwell_seconds",
            },
            DeviceCategory.LCR_METER: {
                "open_correction",
                "short_correction",
                "load_correction",
                "fixture_description",
                "dc_bias_state",
                "signal_voltage_v",
            },
            DeviceCategory.NETWORK_ANALYZER: {
                "calibration_type",
                "calibration_kit",
                "reference_plane",
                "port_power_notes",
                "if_bandwidth_hz",
                "averaging_count",
                "fixture_deembedding",
            },
        }
        for category, field_ids in expected.items():
            with self.subTest(category=category):
                actual = {
                    field.field_id
                    for field in template_for_category(category).detail_fields
                }
                self.assertTrue(field_ids <= actual)

        spectrum = template_for_category(DeviceCategory.SPECTRUM_ANALYZER)
        self.assertIsNotNone(
            spectrum.method_by_id("emi_cispr_assist")
        )
        self.assertNotEqual(
            spectrum.method_by_id("spectrum_level"),
            spectrum.method_by_id("emi_cispr_assist"),
        )

    def test_every_method_can_create_a_normalized_generic_item(self) -> None:
        common = _raw_values(COMMON_PLAN_FIELDS)
        common["standard_procedure"] = "사내 SOP-001 Rev.3"
        common["sample_description"] = "DUT-A / S/N 1001"
        common["acceptance_criteria"] = "절차서 표 2 상·하한 적용"

        for category, template in CATEGORY_PLAN_TEMPLATES.items():
            instrument = SelectedInstrument(
                resource=f"DEMO::{category.value}::INSTR",
                category=category,
                manufacturer="Demo",
                model=category.label_ko,
            )
            for method in template.methods:
                with self.subTest(category=category, method=method.method_id):
                    detail = _raw_values(
                        template.fields_for_method(method.method_id)
                    )
                    detail.update(dict(method.recommended_values))
                    item = GenericPlanItem.from_raw(
                        instrument=instrument,
                        method_id=method.method_id,
                        common_values=common,
                        detail_values=detail,
                        assistance_notice_acknowledged=True,
                    )
                    self.assertEqual(item.instrument, instrument)
                    self.assertEqual(item.category, category)
                    self.assertEqual(item.method_label_ko, method.label_ko)
                    self.assertEqual(item.value_for("repeat_count"), 1)
                    self.assertTrue(item.value_for("safety_confirmed"))

    def test_required_safety_notice_and_numeric_ranges_are_enforced(self) -> None:
        template = template_for_category(DeviceCategory.DIGITAL_MULTIMETER)
        method = template.methods[0]
        instrument = SelectedInstrument(
            resource="DEMO::34465A::INSTR",
            category=DeviceCategory.DIGITAL_MULTIMETER,
            model="34465A",
        )
        common = _raw_values(COMMON_PLAN_FIELDS)
        common["standard_procedure"] = "SOP-DMM"
        common["sample_description"] = "10 V reference"
        common["acceptance_criteria"] = "±0.01 V"
        detail = _raw_values(template.fields_for_method(method.method_id))

        with self.assertRaisesRegex(ValueError, "계획 보조"):
            GenericPlanItem.from_raw(
                instrument=instrument,
                method_id=method.method_id,
                common_values=common,
                detail_values=detail,
                assistance_notice_acknowledged=False,
            )

        common["safety_confirmed"] = False
        with self.assertRaisesRegex(ValueError, "안전 조건 확인"):
            GenericPlanItem.from_raw(
                instrument=instrument,
                method_id=method.method_id,
                common_values=common,
                detail_values=detail,
                assistance_notice_acknowledged=True,
            )

        common["safety_confirmed"] = True
        common["repeat_count"] = 0
        with self.assertRaisesRegex(ValueError, "1"):
            GenericPlanItem.from_raw(
                instrument=instrument,
                method_id=method.method_id,
                common_values=common,
                detail_values=detail,
                assistance_notice_acknowledged=True,
            )

    def test_numeric_commas_are_never_silently_reinterpreted(self) -> None:
        field = PlanFieldDefinition(
            field_id="value",
            label_ko="시험값",
            field_type=PlanFieldType.NUMBER,
            help_ko="숫자 입력",
        )

        self.assertEqual(field.normalize("1,000"), 1000.0)
        with self.assertRaisesRegex(ValueError, "천 단위"):
            field.normalize("1,5")

    def test_spectrum_and_pulse_relationships_are_validated(self) -> None:
        with self.assertRaisesRegex(ValueError, "RMS Detector"):
            _build_generic_item(
                DeviceCategory.SPECTRUM_ANALYZER,
                "channel_power_obw",
                detector="Positive Peak",
            )
        with self.assertRaisesRegex(ValueError, "Pulse Width"):
            _build_generic_item(
                DeviceCategory.SIGNAL_GENERATOR,
                "rf_pulse_output",
                pulse_period_seconds=0.001,
                pulse_width_seconds=0.002,
            )
        with self.assertRaisesRegex(ValueError, "Pulse Width"):
            _build_generic_item(
                DeviceCategory.FUNCTION_GENERATOR,
                "pulse_timing",
                pulse_period_seconds=0.001,
                pulse_width_seconds=0.002,
            )

    def test_dmm_supply_lcr_and_vna_safety_relationships_are_validated(
        self,
    ) -> None:
        with self.assertRaisesRegex(ValueError, "Jack과 Fuse"):
            _build_generic_item(
                DeviceCategory.DIGITAL_MULTIMETER,
                "dmm_single_read",
                measurement_function="DC Current",
                range_unit="A",
                current_fuse_checked=False,
            )
        with self.assertRaisesRegex(ValueError, "OVP"):
            _build_generic_item(
                DeviceCategory.POWER_SUPPLY,
                "dc_static_output",
                voltage_setpoint_v=5,
                ovp_v=4,
            )
        with self.assertRaisesRegex(ValueError, "외부 Bias 전류 제한"):
            _build_generic_item(
                DeviceCategory.LCR_METER,
                "lcr_bias_characteristic",
                dc_bias_state="외부 Bias",
                dc_bias_voltage_v=5,
                external_bias_current_limit_a="",
            )
        with self.assertRaisesRegex(ValueError, "Segment Sweep 표"):
            _build_generic_item(
                DeviceCategory.NETWORK_ANALYZER,
                "vna_sparameter_sweep",
                sweep_type="Segment",
                segment_table="",
            )

    def test_high_risk_categories_require_explicit_connection_safety_fields(
        self,
    ) -> None:
        expected = {
            DeviceCategory.OSCILLOSCOPE: {
                "probe_input_rating",
                "ground_connection_safety",
            },
            DeviceCategory.DIGITAL_MULTIMETER: {
                "input_terminal_rating",
                "max_input_safety_confirmed",
                "current_fuse_checked",
            },
            DeviceCategory.LCR_METER: {
                "bias_discharge_confirmed",
                "external_bias_current_limit_a",
                "stored_energy_notes",
            },
        }
        for category, required_ids in expected.items():
            with self.subTest(category=category):
                field_ids = {
                    field.field_id
                    for field in template_for_category(category).detail_fields
                }
                self.assertTrue(required_ids <= field_ids)

    def test_generic_plan_is_immutable_and_rejects_unknown_fields(self) -> None:
        template = template_for_category(DeviceCategory.POWER_SUPPLY)
        method = template.methods[0]
        instrument = SelectedInstrument(
            resource="DEMO::PSU::INSTR",
            category=DeviceCategory.POWER_SUPPLY,
            model="E36312A",
        )
        common = _raw_values(COMMON_PLAN_FIELDS)
        common["standard_procedure"] = "SOP-PSU"
        common["sample_description"] = "DUT rail"
        common["acceptance_criteria"] = "5 V ±2 %"
        detail = _raw_values(template.fields_for_method(method.method_id))
        item = GenericPlanItem.from_raw(
            instrument=instrument,
            method_id=method.method_id,
            common_values=common,
            detail_values=detail,
            assistance_notice_acknowledged=True,
        )
        with self.assertRaises(FrozenInstanceError):
            item.method_id = "changed"  # type: ignore[misc]

        detail["raw_scpi"] = "OUTP ON"
        with self.assertRaisesRegex(ValueError, "등록되지 않은 필드"):
            GenericPlanItem.from_raw(
                instrument=instrument,
                method_id=method.method_id,
                common_values=common,
                detail_values=detail,
                assistance_notice_acknowledged=True,
            )


if __name__ == "__main__":
    unittest.main()
