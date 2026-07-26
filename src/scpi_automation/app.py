from __future__ import annotations

import argparse
import sys
import tkinter as tk
from tkinter import ttk
from typing import Sequence

from scpi_automation import __version__
from scpi_automation.execution import ExecutionResult
from scpi_automation.results import autosave_result_json
from scpi_automation.routine import SelectedInstrument
from scpi_automation.transport import DiscoveryRecord
from scpi_automation.ui import (
    ExecutionTab,
    GuidedDeviceDiscoveryTab,
    MeasurementPlanTab,
    ResultsTab,
    RoutineSetupTab,
)


class InstrumentControllerApp:
    def __init__(self, root: tk.Tk) -> None:
        self.root = root
        self._base_width = 1280
        self._base_height = 780
        self._current_scale = 1.0
        self._resize_after_id: str | None = None
        self._closing = False
        self._close_after_id: str | None = None
        self._selected_instruments: tuple[SelectedInstrument, ...] = ()
        self.root.title(f"계측기 연결 도우미 {__version__}")
        self.root.geometry("1280x780")
        self.root.minsize(900, 620)
        self.root.configure(background="#F4F6F8")

        self._configure_style()

        shell = ttk.Frame(root, padding=(0, 0), style="App.TFrame")
        shell.pack(fill="both", expand=True)
        shell.columnconfigure(0, weight=1)
        shell.rowconfigure(1, weight=1)

        self.header = ttk.Frame(shell, style="Header.TFrame", padding=(28, 15))
        self.header.grid(row=0, column=0, sticky="ew")
        self.header.columnconfigure(0, weight=1)
        ttk.Label(
            self.header,
            text="계측기 연결 도우미",
            style="AppTitle.TLabel",
        ).grid(row=0, column=0, sticky="w")
        ttk.Label(
            self.header,
            text=f"장비를 찾고 연결 상태를 확인해요 · {__version__}",
            style="HeaderMuted.TLabel",
        ).grid(row=0, column=1, sticky="e")

        self.notebook = ttk.Notebook(shell)
        self.notebook.grid(row=1, column=0, sticky="nsew")

        self.discovery_tab = GuidedDeviceDiscoveryTab(
            self.notebook,
            on_continue_to_routine=self._open_routine_setup,
        )
        self.routine_tab = RoutineSetupTab(
            self.notebook,
            on_back=self._show_discovery_tab,
            on_continue=self._show_plan_tab,
        )
        self.plan_tab = MeasurementPlanTab(
            self.notebook,
            on_back=self._show_routine_tab,
            on_continue=self._show_execution_tab,
        )
        self.execution_tab = ExecutionTab(
            self.notebook,
            on_back=self._show_plan_tab,
            on_result=self._show_results_tab,
        )
        self.results_tab = ResultsTab(
            self.notebook,
            on_back=self._show_execution_tab,
        )
        self.notebook.add(self.discovery_tab, text="  1. 장비 찾기  ")
        self.notebook.add(self.routine_tab, text="  2. 루틴 설정  ")
        self.notebook.add(self.plan_tab, text="  3. 계획서  ")
        self.notebook.add(self.execution_tab, text="  4. 실제 실행  ")
        self.notebook.add(self.results_tab, text="  5. 결과 확인  ")

        self.root.protocol("WM_DELETE_WINDOW", self.close)
        self.root.bind("<Configure>", self._schedule_rescale, add="+")
        self.root.after_idle(self._apply_rescale)

    def _show_discovery_tab(self) -> None:
        self.notebook.select(self.discovery_tab)

    def _show_routine_tab(self) -> None:
        self.notebook.select(self.routine_tab)

    def _show_plan_tab(self) -> None:
        self.plan_tab.set_routine_steps(self.routine_tab.routine_steps)
        self.notebook.select(self.plan_tab)

    def _show_execution_tab(self) -> None:
        self.execution_tab.set_connection_settings(
            backend=self.discovery_tab.selected_backend,
            timeout_ms=self.discovery_tab.selected_timeout_ms,
        )
        self.execution_tab.set_context(
            self._selected_instruments,
            self.routine_tab.routine_steps,
            self.plan_tab.plan_items,
        )
        self.notebook.select(self.execution_tab)

    def _show_results_tab(self, result) -> None:
        self.results_tab.set_result(result)
        if isinstance(result, ExecutionResult) and not result.dry_run:
            try:
                saved_path = autosave_result_json(result)
            except Exception as exc:
                self.results_tab.set_autosave_status(error=str(exc))
            else:
                self.results_tab.set_autosave_status(path=saved_path)
        self.notebook.select(self.results_tab)

    def _open_routine_setup(
        self,
        records: tuple[DiscoveryRecord, ...],
    ) -> None:
        instruments: list[SelectedInstrument] = []
        for record in records:
            if record.identity is None or record.classification is None:
                continue
            instruments.append(
                SelectedInstrument(
                    resource=record.resource,
                    category=record.classification.category,
                    manufacturer=record.identity.manufacturer,
                    model=record.identity.model,
                    serial=record.identity.serial,
                    firmware=record.identity.firmware,
                    raw_idn=record.identity.raw,
                    profile_id=record.classification.profile_id,
                    compatibility_status=(
                        "demo_catalog_preview"
                        if record.resource.startswith("DEMO::")
                        else record.classification.profile_status
                    ),
                    compatible_capability_ids=(
                        record.classification.compatible_capability_ids
                    ),
                    compatible_operation_ids=(
                        record.classification.compatible_operation_ids
                    ),
                    incompatible_operation_ids=(
                        record.classification.incompatible_operation_ids
                    ),
                    unresolved_operation_ids=(
                        record.classification.unresolved_operation_ids
                    ),
                    validation_catalog_fingerprint=(
                        record.classification.validation_catalog_fingerprint
                    ),
                    option_response=record.classification.option_response,
                    option_state=record.classification.option_state,
                )
            )
        selected_instruments = tuple(instruments)
        self._selected_instruments = selected_instruments
        self.routine_tab.set_instruments(selected_instruments)
        self.plan_tab.set_instruments(selected_instruments)
        self.notebook.select(self.routine_tab)

    def _configure_style(self) -> None:
        self.style = ttk.Style(self.root)
        available = self.style.theme_names()
        if "clam" in available:
            self.style.theme_use("clam")
        elif "vista" in available:
            self.style.theme_use("vista")

        self.style.configure("App.TFrame", background="#F4F6F8")
        self.style.configure("Header.TFrame", background="#FFFFFF")
        self._apply_style_scale(1.0)

    @staticmethod
    def _scaled(base: int, scale: float, minimum: int = 7) -> int:
        return max(minimum, int(round(base * scale)))

    def _apply_style_scale(self, scale: float) -> None:
        self.style.configure(".", font=("Segoe UI", self._scaled(9, scale)))
        self.style.configure(
            "AppTitle.TLabel",
            font=("Segoe UI Semibold", self._scaled(15, scale)),
            background="#FFFFFF",
            foreground="#191F28",
        )
        self.style.configure(
            "HeaderMuted.TLabel",
            font=("Segoe UI", self._scaled(9, scale)),
            background="#FFFFFF",
            foreground="#6B7684",
        )
        self.style.configure(
            "TNotebook",
            background="#F4F6F8",
            borderwidth=0,
            tabmargins=(
                self._scaled(24, scale, 0),
                self._scaled(8, scale, 0),
                0,
                0,
            ),
        )
        tab_padding = (
            self._scaled(18, scale, 1),
            self._scaled(9, scale, 1),
            self._scaled(18, scale, 1),
            self._scaled(9, scale, 1),
        )
        self.style.configure(
            "TNotebook.Tab",
            font=("Segoe UI Semibold", self._scaled(10, scale)),
            padding=tab_padding,
            background="#E5E8EB",
            foreground="#6B7684",
            borderwidth=0,
        )
        self.style.map(
            "TNotebook.Tab",
            background=[("selected", "#FFFFFF")],
            foreground=[("selected", "#191F28")],
            padding=[
                ("selected", tab_padding),
                ("active", tab_padding),
                ("!selected", tab_padding),
            ],
        )
        self.style.configure(
            "Friendly.Horizontal.TProgressbar",
            troughcolor="#E5E8EB",
            background="#3182F6",
            bordercolor="#E5E8EB",
            lightcolor="#3182F6",
            darkcolor="#3182F6",
        )

    def _schedule_rescale(self, event: tk.Event[tk.Misc]) -> None:
        if event.widget is not self.root:
            return
        if self._resize_after_id is not None:
            try:
                self.root.after_cancel(self._resize_after_id)
            except tk.TclError:
                pass
        # Apply the scale at the next idle layout pass.  A timed debounce can
        # fire after the user has already switched tabs, which changes the
        # notebook's inner height late and makes the tab itself appear to
        # resize.  Idle coalescing still collapses Configure bursts while
        # ensuring the visible layout is settled before interaction resumes.
        self._resize_after_id = self.root.after_idle(self._apply_rescale)

    def _apply_rescale(self) -> None:
        self._resize_after_id = None
        width = self.root.winfo_width()
        height = self.root.winfo_height()
        if width < 100 or height < 100:
            return
        scale = min(width / self._base_width, height / self._base_height)
        scale = max(0.75, min(1.4, scale))
        if abs(scale - self._current_scale) < 0.015:
            return
        self._current_scale = scale
        self._apply_style_scale(scale)
        self.header.configure(
            padding=(
                self._scaled(28, scale, 1),
                self._scaled(15, scale, 1),
            )
        )
        self.discovery_tab.apply_ui_scale(scale)
        self.routine_tab.apply_ui_scale(scale)
        self.plan_tab.apply_ui_scale(scale)
        self.execution_tab.apply_ui_scale(scale)
        self.results_tab.apply_ui_scale(scale)

    def close(self) -> None:
        if self._closing:
            return
        self._closing = True
        if self._resize_after_id is not None:
            try:
                self.root.after_cancel(self._resize_after_id)
            except tk.TclError:
                pass
        self.discovery_tab.shutdown()
        self.execution_tab.shutdown()
        if self.execution_tab.has_active_worker:
            self.root.title("계측기 연결 도우미 · 안전 종료 확인 중")
            self._wait_for_execution_shutdown()
            return
        self.root.destroy()

    def _wait_for_execution_shutdown(self) -> None:
        """Keep Tk alive until the execution worker finishes its finalizer."""

        if self.execution_tab.has_active_worker:
            try:
                self._close_after_id = self.root.after(
                    80,
                    self._wait_for_execution_shutdown,
                )
            except tk.TclError:
                self._close_after_id = None
            return
        self._close_after_id = None
        try:
            self.root.destroy()
        except tk.TclError:
            pass


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="SCPI Instrument Controller")
    parser.add_argument(
        "--smoke-test",
        action="store_true",
        help="Create and destroy the Tk GUI without opening a visible window.",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    root = tk.Tk()
    if args.smoke_test:
        root.withdraw()
    app = InstrumentControllerApp(root)
    if args.smoke_test:
        root.update_idletasks()
        app.close()
        if sys.stdout is not None:
            print("GUI_SMOKE_OK")
        return 0
    root.mainloop()
    return 0
