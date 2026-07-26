from __future__ import annotations

import unittest
from dataclasses import replace

from scpi_automation.execution import ExecutionStatus, run_execution
from scpi_automation.identity import DeviceCategory
from scpi_automation.planning import (
    PlanCompilationError,
    SignalGeneratorPlanItem,
    SpectrumPlanItem,
    compile_routine_with_plan,
)
from scpi_automation.routine import (
    DelayStep,
    PlanArgumentBinding,
    SelectedFeature,
    SelectedInstrument,
    create_plan_bound_delay,
    select_feature,
)


def analyzer() -> SelectedInstrument:
    return SelectedInstrument(
        resource="DEMO::FSV30::INSTR",
        category=DeviceCategory.SPECTRUM_ANALYZER,
        manufacturer="Rohde&Schwarz",
        model="FSV30",
        serial="SA-01",
        profile_id="rs_fsv_fsva",
        compatibility_status="demo_catalog_preview",
    )


def generator() -> SelectedInstrument:
    return SelectedInstrument(
        resource="DEMO::SMB100A::INSTR",
        category=DeviceCategory.SIGNAL_GENERATOR,
        manufacturer="Rohde&Schwarz",
        model="SMB100A",
        serial="SG-01",
        profile_id="rs_smb100a",
        compatibility_status="demo_catalog_preview",
    )


def bound_feature(
    instrument: SelectedInstrument,
    feature_id: str,
    field_id: str,
) -> SelectedFeature:
    return select_feature(
        instrument,
        feature_id,
        plan_bindings=(PlanArgumentBinding("value", field_id),),
    )


def analyzer_plan(
    instrument: SelectedInstrument,
    *,
    case_id: str,
    case_name: str,
    center: float,
    repeat_count: int = 1,
    rbw: float | None = 100_000,
) -> SpectrumPlanItem:
    return SpectrumPlanItem(
        instrument=instrument,
        center_frequency_hz=center,
        span_hz=100_000_000,
        rbw_hz=rbw,
        vbw_hz=100_000,
        reference_level_dbm=0,
        case_id=case_id,
        case_name=case_name,
        repeat_count=repeat_count,
    )


def generator_plan(
    instrument: SelectedInstrument,
    *,
    case_id: str,
    case_name: str,
    frequency: float,
    repeat_count: int = 1,
) -> SignalGeneratorPlanItem:
    return SignalGeneratorPlanItem(
        instrument=instrument,
        frequency_hz=frequency,
        power_dbm=-20,
        dwell_seconds=0.5,
        case_id=case_id,
        case_name=case_name,
        repeat_count=repeat_count,
    )


class PlanBindingCompilerTests(unittest.TestCase):
    def test_binding_registry_rejects_wrong_fields_and_control_state(self) -> None:
        sa = analyzer()
        sg = generator()
        with self.assertRaisesRegex(ValueError, "center_frequency_hz"):
            select_feature(
                sa,
                "spectrum_analyzer.cap.analyzer.frequency.center.set",
                plan_bindings=(
                    PlanArgumentBinding("value", "power_dbm"),
                ),
            )
        with self.assertRaisesRegex(ValueError, "검토된 항목"):
            select_feature(
                sg,
                "signal_generator.cap.rf.output.state.set",
                plan_bindings=(
                    PlanArgumentBinding("state", "power_dbm"),
                ),
            )
        with self.assertRaisesRegex(ValueError, "고정값과 계획값"):
            select_feature(
                sa,
                "spectrum_analyzer.cap.analyzer.frequency.center.set",
                arguments={"value": "1000000000"},
                plan_bindings=(
                    PlanArgumentBinding("value", "center_frequency_hz"),
                ),
            )

    def test_multi_device_cases_expand_case_repeat_then_routine(self) -> None:
        sa = analyzer()
        sg = generator()
        routine = (
            bound_feature(
                sg,
                "signal_generator.cap.source.frequency.set",
                "frequency_hz",
            ),
            bound_feature(
                sa,
                "spectrum_analyzer.cap.analyzer.frequency.center.set",
                "center_frequency_hz",
            ),
            create_plan_bound_delay(sg),
        )
        plans = (
            generator_plan(
                sg,
                case_id="case-1",
                case_name="시험 01",
                frequency=100_000_000,
            ),
            analyzer_plan(
                sa,
                case_id="case-1",
                case_name="시험 01",
                center=100_000_000,
            ),
            generator_plan(
                sg,
                case_id="case-2",
                case_name="시험 02",
                frequency=200_000_000,
                repeat_count=2,
            ),
            analyzer_plan(
                sa,
                case_id="case-2",
                case_name="시험 02",
                center=200_000_000,
                repeat_count=2,
            ),
        )

        compiled = compile_routine_with_plan(
            routine,
            plans,
            selected_instruments=(sg, sa),
        )

        self.assertTrue(compiled.uses_plan_values)
        self.assertEqual(len(compiled.cases), 2)
        self.assertEqual(len(compiled.steps), 9)
        self.assertEqual(
            [metadata.case_id for metadata in compiled.metadata],
            ["case-1"] * 3 + ["case-2"] * 6,
        )
        self.assertEqual(
            [metadata.repeat_index for metadata in compiled.metadata],
            [1, 1, 1, 1, 1, 1, 2, 2, 2],
        )
        self.assertEqual(
            dict(compiled.steps[0].arguments)["value"],
            "100000000",
        )
        self.assertEqual(
            dict(compiled.steps[3].arguments)["value"],
            "200000000",
        )
        self.assertIsInstance(compiled.steps[2], DelayStep)
        self.assertEqual(compiled.steps[2].seconds, 0.5)

    def test_missing_device_in_case_fails(self) -> None:
        sa = analyzer()
        sg = generator()
        routine = (
            bound_feature(
                sg,
                "signal_generator.cap.source.frequency.set",
                "frequency_hz",
            ),
            bound_feature(
                sa,
                "spectrum_analyzer.cap.analyzer.frequency.center.set",
                "center_frequency_hz",
            ),
        )
        plans = (
            generator_plan(
                sg,
                case_id="case-1",
                case_name="시험 01",
                frequency=100_000_000,
            ),
        )

        with self.assertRaisesRegex(PlanCompilationError, "실행 설정이 없습니다"):
            compile_routine_with_plan(
                routine,
                plans,
                selected_instruments=(sg, sa),
            )

    def test_flat_multi_device_plans_are_never_paired_by_position(self) -> None:
        sa = analyzer()
        sg = generator()
        routine = (
            bound_feature(
                sg,
                "signal_generator.cap.source.frequency.set",
                "frequency_hz",
            ),
            bound_feature(
                sa,
                "spectrum_analyzer.cap.analyzer.frequency.center.set",
                "center_frequency_hz",
            ),
        )
        plans = (
            SignalGeneratorPlanItem(sg, 100_000_000, -20, 0.5),
            SpectrumPlanItem(
                sa,
                100_000_000,
                10_000_000,
                100_000,
                100_000,
                0,
            ),
        )

        with self.assertRaisesRegex(PlanCompilationError, "같은 시험 케이스"):
            compile_routine_with_plan(
                routine,
                plans,
                selected_instruments=(sg, sa),
            )

    def test_auto_rbw_bound_to_manual_set_fails_before_execution(self) -> None:
        sa = analyzer()
        routine = (
            bound_feature(
                sa,
                "spectrum_analyzer.cap.analyzer.rbw.set",
                "rbw_hz",
            ),
        )
        plans = (
            analyzer_plan(
                sa,
                case_id="case-1",
                case_name="시험 01",
                center=1_000_000_000,
                rbw=None,
            ),
        )

        with self.assertRaisesRegex(PlanCompilationError, "RBW가 자동"):
            compile_routine_with_plan(
                routine,
                plans,
                selected_instruments=(sa,),
            )

        opened: list[str] = []

        def forbidden_factory(**kwargs):
            opened.append(kwargs["resource"])
            raise AssertionError("VISA must not open")

        result = run_execution(
            instruments=(sa,),
            routine_steps=routine,
            plan_items=plans,
            dry_run=False,
            operator_confirmed=True,
            session_factory=forbidden_factory,
        )
        self.assertEqual(result.status, ExecutionStatus.FAILED)
        self.assertEqual(opened, [])
        self.assertIn("RBW가 자동", result.stop_reason)

    def test_model_range_is_rechecked_after_binding(self) -> None:
        sa = replace(
            analyzer(),
            compatibility_status="hardware_validated_partial",
            compatible_operation_ids=(
                "analyzer.frequency.center::set",
            ),
        )
        routine = (
            bound_feature(
                sa,
                "spectrum_analyzer.cap.analyzer.frequency.center.set",
                "center_frequency_hz",
            ),
        )
        plans = (
            analyzer_plan(
                sa,
                case_id="case-1",
                case_name="시험 01",
                center=31_000_000_000,
            ),
        )

        with self.assertRaisesRegex(PlanCompilationError, "상한"):
            compile_routine_with_plan(
                routine,
                plans,
                selected_instruments=(sa,),
            )

    def test_engine_dry_run_records_concrete_case_commands(self) -> None:
        sa = analyzer()
        routine = (
            bound_feature(
                sa,
                "spectrum_analyzer.cap.analyzer.frequency.center.set",
                "center_frequency_hz",
            ),
        )
        plans = (
            analyzer_plan(
                sa,
                case_id="case-1",
                case_name="시험 01",
                center=1_000_000_000,
            ),
            analyzer_plan(
                sa,
                case_id="case-2",
                case_name="시험 02",
                center=2_000_000_000,
            ),
        )

        result = run_execution(
            instruments=(sa,),
            routine_steps=routine,
            plan_items=plans,
            dry_run=True,
        )

        self.assertEqual(result.status, ExecutionStatus.COMPLETED)
        self.assertTrue(result.uses_plan_values)
        self.assertEqual(result.test_case_count, 2)
        self.assertEqual(
            [record.command for record in result.step_records],
            [":FREQ:CENT 1000000000", ":FREQ:CENT 2000000000"],
        )
        self.assertEqual(
            [record.case_id for record in result.step_records],
            ["case-1", "case-2"],
        )
        self.assertEqual(len(result.executed_steps), 2)


if __name__ == "__main__":
    unittest.main()
