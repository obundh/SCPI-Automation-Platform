from __future__ import annotations

import tkinter as tk
import unittest
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from scpi_automation.ui.results_tab import ResultsTab


class ResultsTabUiTests(unittest.TestCase):
    def setUp(self) -> None:
        self.root = tk.Tk()
        self.root.withdraw()
        self.saved: list[tuple[str, object, Path]] = []

        def saver(kind: str):
            def save(result: object, path: str | Path) -> Path:
                normalized = Path(path)
                self.saved.append((kind, result, normalized))
                return normalized

            return save

        def bundle(result: object, directory: str | Path) -> tuple[Path, ...]:
            base = Path(directory)
            paths = (
                base / "result.md",
                base / "result.json",
                base / "result.xlsx",
            )
            self.saved.extend(
                ("bundle", result, path)
                for path in paths
            )
            return paths

        self.back_calls: list[bool] = []
        self.tab = ResultsTab(
            self.root,
            on_back=lambda: self.back_calls.append(True),
            json_saver=saver("json"),
            markdown_saver=saver("markdown"),
            xlsx_saver=saver("xlsx"),
            bundle_exporter=bundle,
        )
        self.tab.pack(fill="both", expand=True)
        self.result = SimpleNamespace(
            status="completed",
            dry_run=False,
            run_id="RUN-20260725-001",
            started_at_utc="2026-07-25T12:00:00Z",
            finished_at_utc="2026-07-25T12:00:03Z",
            measurements=(
                SimpleNamespace(
                    timestamp_utc="2026-07-25T12:00:02Z",
                    device_name="FSV30",
                    result_name="Marker Level",
                    numeric_value=-32.5,
                    unit="dBm",
                    raw_response="-3.250000E+01",
                ),
            ),
            step_records=(
                SimpleNamespace(
                    step_index=1,
                    status="completed",
                    device_resource="TCPIP0::FSV30::INSTR",
                    feature_id="spectrum_analyzer.peak_search",
                    rendered_command="CALC:MARK:MAX",
                    response="",
                    error="",
                ),
                SimpleNamespace(
                    step_index=2,
                    status="completed",
                    device_resource="TCPIP0::FSV30::INSTR",
                    feature_id="spectrum_analyzer.read_marker",
                    rendered_command="CALC:MARK:Y?",
                    response="-3.250000E+01",
                    error="",
                ),
            ),
            events=(
                SimpleNamespace(
                    timestamp_utc="2026-07-25T12:00:01Z",
                    level="INFO",
                    step_index=1,
                    message="Peak Search를 실행했어요.",
                ),
                SimpleNamespace(
                    timestamp_utc="2026-07-25T12:00:02Z",
                    level="INFO",
                    step_index=2,
                    message="Marker 값을 읽었어요.",
                ),
            ),
            errors=(),
        )
        self.root.update_idletasks()

    def tearDown(self) -> None:
        if self.root.winfo_exists():
            self.root.destroy()

    def test_initial_state_has_no_exportable_result(self) -> None:
        self.assertIsNone(self.tab.result)
        self.assertEqual(self.tab.status_badge_var.get(), "결과 없음")
        self.assertEqual(
            str(self.tab.export_excel_button.cget("state")),
            "disabled",
        )
        self.assertEqual(len(self.tab.measurement_tree.get_children()), 0)

    def test_set_result_populates_summary_measurements_steps_and_logs(self) -> None:
        self.tab.set_result(self.result)

        self.assertIs(self.tab.result, self.result)
        self.assertEqual(self.tab.status_badge_var.get(), "완료")
        self.assertEqual(self.tab.run_id_var.get(), "RUN-20260725-001")
        self.assertIn("측정값 1개", self.tab.summary_var.get())
        self.assertEqual(len(self.tab.measurement_tree.get_children()), 1)
        self.assertEqual(len(self.tab.step_tree.get_children()), 2)
        self.assertEqual(len(self.tab.log_tree.get_children()), 2)
        measurement_id = self.tab.measurement_tree.get_children()[0]
        values = self.tab.measurement_tree.item(measurement_id, "values")
        self.assertIn("FSV30", values)
        self.assertIn("Marker Level", values)
        self.assertIn("-32.5", values)
        self.assertEqual(
            str(self.tab.export_excel_button.cget("state")),
            "normal",
        )
        self.assertEqual(
            self.tab.data_notebook.tab(self.tab.measurement_page, "text"),
            "측정값 1개",
        )
        expected_local = datetime.fromisoformat(
            "2026-07-25T12:00:02+00:00"
        ).astimezone().strftime("%Y-%m-%d %H:%M:%S")
        self.assertEqual(values[0], expected_local)
        log_id = self.tab.log_tree.get_children()[0]
        self.assertIn("안내", self.tab.log_tree.item(log_id, "values"))

    def test_dry_run_and_error_are_clearly_labeled(self) -> None:
        failed = SimpleNamespace(
            status="failed",
            dry_run=True,
            run_id="DRY-FAIL",
            started_at_utc="2026-07-25T12:00:00Z",
            finished_at_utc="2026-07-25T12:00:01Z",
            measurements=(),
            step_records=(
                {
                    "step_index": 1,
                    "status": "failed",
                    "error": "IDN mismatch",
                },
            ),
            events=(
                {
                    "timestamp_utc": "2026-07-25T12:00:01Z",
                    "level": "ERROR",
                    "step_index": 1,
                    "message": "IDN mismatch",
                },
            ),
            errors=("IDN mismatch",),
        )

        self.tab.set_result(failed)

        self.assertEqual(self.tab.status_badge_var.get(), "DRY RUN · 오류")
        self.assertIn("오류 1개", self.tab.summary_var.get())
        step_id = self.tab.step_tree.get_children()[0]
        self.assertIn(
            "오류: IDN mismatch",
            self.tab.step_tree.item(step_id, "values"),
        )

    def test_json_markdown_and_excel_buttons_use_selected_paths(self) -> None:
        self.tab.set_result(self.result)
        destinations = iter(
            (
                "C:/results/report.md",
                "C:/results/report.json",
                "C:/results/report.xlsx",
            )
        )

        with (
            patch(
                "scpi_automation.ui.results_tab.filedialog.asksaveasfilename",
                side_effect=lambda **_kwargs: next(destinations),
            ),
            patch(
                "scpi_automation.ui.results_tab.messagebox.showinfo",
            ),
        ):
            self.tab.export_markdown_button.invoke()
            self.tab.export_json_button.invoke()
            self.tab.export_excel_button.invoke()

        self.assertEqual(
            [(kind, path.suffix) for kind, _result, path in self.saved],
            [
                ("markdown", ".md"),
                ("json", ".json"),
                ("xlsx", ".xlsx"),
            ],
        )
        self.assertTrue(all(result is self.result for _, result, _ in self.saved))

    def test_export_all_saves_three_formats_to_one_directory(self) -> None:
        self.tab.set_result(self.result)

        with (
            patch(
                "scpi_automation.ui.results_tab.filedialog.askdirectory",
                return_value="C:/results/run",
            ),
            patch(
                "scpi_automation.ui.results_tab.messagebox.showinfo",
            ),
        ):
            self.tab.export_all_button.invoke()

        self.assertEqual(len(self.saved), 3)
        self.assertTrue(all(kind == "bundle" for kind, _, _ in self.saved))
        self.assertEqual(
            {path.suffix for _, _, path in self.saved},
            {".md", ".json", ".xlsx"},
        )
        self.assertIn("3개 파일", self.tab.export_status_var.get())

    def test_clearing_result_disables_exports_and_rows(self) -> None:
        self.tab.set_result(self.result)
        self.tab.set_result(None)

        self.assertIsNone(self.tab.result)
        self.assertEqual(len(self.tab.measurement_tree.get_children()), 0)
        self.assertEqual(len(self.tab.step_tree.get_children()), 0)
        self.assertEqual(len(self.tab.log_tree.get_children()), 0)
        self.assertEqual(
            str(self.tab.export_all_button.cget("state")),
            "disabled",
        )

    def test_autosave_status_explains_success_and_recovery_action(self) -> None:
        self.tab.set_result(self.result)

        self.tab.set_autosave_status(path="C:/results/auto/run.json")
        self.assertIn("자동 저장했어요", self.tab.export_status_var.get())
        self.assertIn("run.json", self.tab.export_status_var.get())

        self.tab.set_autosave_status(error="disk full")
        self.assertIn("자동 저장에 실패", self.tab.export_status_var.get())
        self.assertIn("저장 버튼", self.tab.export_status_var.get())

    def test_back_and_scaling_public_apis(self) -> None:
        font, base_size = next(iter(self.tab._font_metrics.values()))

        self.tab.back_button.invoke()
        self.tab.apply_ui_scale(2.0)

        self.assertEqual(self.back_calls, [True])
        self.assertEqual(self.tab._ui_scale, 1.4)
        self.assertEqual(
            int(font.cget("size")),
            self.tab._scaled_size(base_size, 1.4),
        )


if __name__ == "__main__":
    unittest.main()
