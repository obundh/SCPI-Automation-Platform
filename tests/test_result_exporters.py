from __future__ import annotations

import json
import tempfile
import unittest
import zipfile
from pathlib import Path

from scpi_automation.execution import (
    EXECUTION_SCHEMA_VERSION,
    ExecutionEvent,
    ExecutionResult,
    ExecutionStatus,
    MeasurementRecord,
    SafetyRecord,
    StepRecord,
)
from scpi_automation.identity import DeviceCategory
from scpi_automation.planning import SpectrumPlanItem
from scpi_automation.results import (
    autosave_result_json,
    execution_result_to_dict,
    export_result_bundle,
    save_result_json,
    save_result_markdown,
    save_result_xlsx,
)
from scpi_automation.routine import (
    DelayStep,
    SelectedInstrument,
)


def sample_result(*, raw_response: str = "=1+1") -> ExecutionResult:
    instrument = SelectedInstrument(
        resource="TCPIP0::192.0.2.30::inst0::INSTR",
        category=DeviceCategory.SPECTRUM_ANALYZER,
        manufacturer="Rohde&Schwarz",
        model="FSV30",
        serial="12345",
        firmware="3.60",
        raw_idn="Rohde&Schwarz,FSV30,12345,3.60",
        profile_id="rs_fsv_fsva",
        compatibility_status="hardware_validated_partial",
        compatible_operation_ids=("marker.y::query",),
        validation_catalog_fingerprint="a" * 64,
        option_response="K7",
        option_state="queried",
    )
    plan = SpectrumPlanItem(
        instrument=instrument,
        center_frequency_hz=1_000_000_000,
        span_hz=100_000_000,
        rbw_hz=100_000,
        vbw_hz=None,
        reference_level_dbm=0,
        case_id="case-0001",
        case_name="시험 01",
        repeat_count=2,
    )
    return ExecutionResult(
        schema_version=EXECUTION_SCHEMA_VERSION,
        run_id="run-test-001",
        started_at_utc="2026-07-25T12:00:00.000+00:00",
        finished_at_utc="2026-07-25T12:00:01.000+00:00",
        duration_ms=1_000,
        status=ExecutionStatus.COMPLETED,
        dry_run=False,
        stop_reason="루틴 실행 완료",
        instruments=(instrument,),
        routine_steps=(DelayStep(0.1),),
        plan_items=(plan,),
        step_records=(
            StepRecord(
                step_index=1,
                step_kind="feature",
                status="completed",
                started_at_utc="2026-07-25T12:00:00.000+00:00",
                finished_at_utc="2026-07-25T12:00:01.000+00:00",
                duration_ms=1_000,
                resource=instrument.resource,
                feature_id="spectrum_analyzer.cap.marker.y.query",
                capability_id="marker.y",
                operation="query",
                command="CALC:MARK1:Y?",
                response=raw_response,
                result_name="@SUM(A1:A2)",
                response_type="float_or_string",
                case_id="case-0001",
                case_name="시험 01",
                case_index=1,
                repeat_index=2,
                repeat_count=2,
                template_step_index=1,
                applied_plan_bindings=(
                    ("value", "center_frequency_hz", "1000000000"),
                ),
            ),
        ),
        measurements=(
            MeasurementRecord(
                measurement_id="measurement-001",
                sequence=1,
                timestamp_utc="2026-07-25T12:00:01.000+00:00",
                step_index=1,
                resource=instrument.resource,
                manufacturer=instrument.manufacturer,
                model=instrument.model,
                feature_id="spectrum_analyzer.cap.marker.y.query",
                capability_id="marker.y",
                operation="query",
                result_name="@SUM(A1:A2)",
                response_type="float_or_string",
                raw_response=raw_response,
                parsed_value=raw_response,
                unit="dBm",
                case_id="case-0001",
                case_name="시험 01",
                case_index=1,
                repeat_index=2,
                repeat_count=2,
                template_step_index=1,
            ),
        ),
        events=(
            ExecutionEvent(
                sequence=1,
                timestamp_utc="2026-07-25T12:00:00.000+00:00",
                level="info",
                kind="command_intent",
                message="+CMD 실행",
                step_index=1,
                total_steps=1,
                resource=instrument.resource,
                command="CALC:MARK1:Y?",
                response=raw_response,
                case_id="case-0001",
                case_name="시험 01",
                case_index=1,
                repeat_index=2,
                repeat_count=2,
                template_step_index=1,
            ),
        ),
        safety_records=(
            SafetyRecord(
                sequence=1,
                timestamp_utc="2026-07-25T12:00:01.000+00:00",
                resource=instrument.resource,
                operation_id="rf.output.state::set",
                command="OUTP 0",
                status="confirmed_off",
                response="0",
                message="출력 OFF 확인",
            ),
        ),
        executed_steps=(DelayStep(0.1),),
        compiled_digest="d" * 64,
        uses_plan_values=True,
        test_case_count=1,
    )


class ResultExporterTests(unittest.TestCase):
    def test_actual_result_autosave_uses_safe_unique_json_name(self) -> None:
        result = sample_result()

        with tempfile.TemporaryDirectory() as temporary:
            path = autosave_result_json(result, temporary)
            payload = json.loads(path.read_text(encoding="utf-8"))

        self.assertEqual(
            path.name,
            "20260725120000_actual_run-test-001.json",
        )
        self.assertEqual(payload["run_id"], result.run_id)

    def test_json_preserves_full_execution_snapshot(self) -> None:
        result = sample_result()

        payload = execution_result_to_dict(result)

        self.assertEqual(payload["document_type"], "scpi-execution-result")
        self.assertEqual(payload["summary"]["measurement_count"], 1)
        self.assertEqual(payload["instruments"][0]["raw_idn"], result.instruments[0].raw_idn)
        self.assertEqual(payload["routine"][0]["type"], "delay")
        self.assertEqual(payload["plan"][0]["values"]["rbw_hz"], 100_000)
        self.assertEqual(payload["plan"][0]["case_id"], "case-0001")
        self.assertEqual(payload["steps"][0]["repeat_index"], 2)
        self.assertEqual(
            payload["steps"][0]["applied_plan_bindings"][0]["field_id"],
            "center_frequency_hz",
        )
        self.assertTrue(payload["summary"]["uses_plan_values"])
        self.assertEqual(payload["measurements"][0]["raw_response"], "=1+1")
        self.assertEqual(payload["events"][0]["command"], "CALC:MARK1:Y?")
        self.assertEqual(payload["safety"][0]["status"], "confirmed_off")

    def test_json_and_markdown_files_are_utf8_and_complete(self) -> None:
        result = sample_result()
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            json_path = save_result_json(result, root / "result.json")
            md_path = save_result_markdown(result, root / "result.md")

            payload = json.loads(json_path.read_text(encoding="utf-8"))
            markdown = md_path.read_text(encoding="utf-8")

        self.assertEqual(payload["run_id"], "run-test-001")
        self.assertIn("SCPI 측정 자동화 실행 결과", markdown)
        self.assertIn("Rohde&Schwarz", markdown)
        self.assertIn("CALC:MARK1:Y?", markdown)
        self.assertIn("=1+1", markdown)
        self.assertIn("출력 OFF 확인", markdown)

    def test_xlsx_has_separate_scientific_record_sheets(self) -> None:
        result = sample_result()
        with tempfile.TemporaryDirectory() as temporary:
            path = save_result_xlsx(
                result,
                Path(temporary) / "result.xlsx",
            )
            with zipfile.ZipFile(path) as archive:
                workbook_xml = archive.read("xl/workbook.xml").decode("utf-8")
                worksheet_xml = "\n".join(
                    archive.read(name).decode("utf-8")
                    for name in archive.namelist()
                    if name.startswith("xl/worksheets/sheet")
                    and name.endswith(".xml")
                )

        for sheet_name in (
            "요약",
            "장비",
            "루틴",
            "시험계획",
            "측정결과",
            "실행단계",
            "명령로그",
            "안전종료",
        ):
            self.assertIn(f'name="{sheet_name}"', workbook_xml)
        self.assertIn("=1+1", worksheet_xml)
        self.assertIn("@SUM(A1:A2)", worksheet_xml)
        self.assertNotIn("<f>", worksheet_xml)

    def test_long_response_is_split_without_losing_data_in_xlsx(self) -> None:
        raw = "ABCDEFGHIJ" * 7_000
        result = sample_result(raw_response=raw)
        with tempfile.TemporaryDirectory() as temporary:
            path = save_result_xlsx(
                result,
                Path(temporary) / "long.xlsx",
            )
            with zipfile.ZipFile(path) as archive:
                worksheet_xml = "\n".join(
                    archive.read(name).decode("utf-8")
                    for name in archive.namelist()
                    if name.startswith("xl/worksheets/sheet")
                    and name.endswith(".xml")
                )

        self.assertIn(raw[:32_000], worksheet_xml)
        self.assertIn(raw[64_000:], worksheet_xml)

    def test_bundle_writes_json_markdown_and_excel(self) -> None:
        result = sample_result()
        with tempfile.TemporaryDirectory() as temporary:
            paths = export_result_bundle(result, temporary)
            suffixes = {path.suffix for path in paths}
            existing = all(path.is_file() for path in paths)

        self.assertEqual(suffixes, {".json", ".md", ".xlsx"})
        self.assertTrue(existing)

    def test_non_finite_measurement_is_rejected(self) -> None:
        with self.assertRaisesRegex(ValueError, "NaN 또는 무한대"):
            MeasurementRecord(
                measurement_id="bad",
                sequence=1,
                timestamp_utc="2026-07-25T12:00:00+00:00",
                step_index=1,
                resource="TCPIP::INSTR",
                manufacturer="Maker",
                model="Model",
                feature_id="feature",
                capability_id="cap",
                operation="query",
                result_name="bad",
                response_type="float",
                raw_response="nan",
                parsed_value=float("nan"),
            )


if __name__ == "__main__":
    unittest.main()
