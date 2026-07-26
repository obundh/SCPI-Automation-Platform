from __future__ import annotations

import queue
import threading
import tkinter as tk
import tkinter.font as tkfont
from datetime import datetime
from tkinter import messagebox, ttk
from typing import Any, Callable

from scpi_automation.binding_registry import plan_binding_definition
from scpi_automation.planning import (
    PlanCompilationError,
    GenericPlanItem,
    MeasurementPlanItem,
    SignalGeneratorPlanItem,
    SpectrumPlanItem,
    compile_routine_with_plan,
)
from scpi_automation.routine import (
    DelayStep,
    PlanBoundDelayStep,
    RoutineStep,
    SelectedFeature,
    SelectedInstrument,
    WaitForCompletionStep,
    feature_by_id,
)
from scpi_automation.ui.value_formatting import format_feature_arguments
from scpi_automation.ui.instrument_display_window import (
    InstrumentDisplayWindow,
)


BACKGROUND = "#F4F6F8"
CARD = "#FFFFFF"
TEXT = "#191F28"
SUBTEXT = "#6B7684"
BORDER = "#E5E8EB"
ACCENT = "#3182F6"
ACCENT_DARK = "#1B64DA"
ACCENT_LIGHT = "#EAF3FF"
SUCCESS = "#0F9D58"
SUCCESS_LIGHT = "#E8F7EF"
WARNING = "#D97706"
WARNING_LIGHT = "#FFF4E5"
DANGER = "#D92D20"
DANGER_DARK = "#B42318"
DANGER_LIGHT = "#FDECEC"
NEUTRAL_LIGHT = "#F2F4F6"

ExecutionRunner = Callable[..., Any]
ResultCallback = Callable[[Any], None]


def _default_execution_runner(**kwargs: Any) -> Any:
    """Import the execution engine only when the operator starts a run."""

    from scpi_automation.execution import run_execution

    return run_execution(**kwargs)


def _button(
    parent: tk.Misc,
    *,
    text: str,
    command: Callable[[], None],
    primary: bool = False,
    danger: bool = False,
    compact: bool = False,
) -> tk.Button:
    if danger:
        background = DANGER
        foreground = "#FFFFFF"
        active_background = DANGER_DARK
        active_foreground = "#FFFFFF"
    elif primary:
        background = ACCENT
        foreground = "#FFFFFF"
        active_background = ACCENT_DARK
        active_foreground = "#FFFFFF"
    else:
        background = NEUTRAL_LIGHT
        foreground = TEXT
        active_background = BORDER
        active_foreground = TEXT
    return tk.Button(
        parent,
        text=text,
        command=command,
        font=("Segoe UI Semibold", 9 if compact else 10),
        background=background,
        foreground=foreground,
        activebackground=active_background,
        activeforeground=active_foreground,
        disabledforeground="#A6ADB4",
        relief="flat",
        borderwidth=0,
        cursor="hand2",
        takefocus=True,
        padx=12 if compact else 18,
        pady=7 if compact else 10,
    )


class ExecutionTab(tk.Frame):
    """Run a verified routine while keeping its measurement plan visible."""

    def __init__(
        self,
        master: tk.Misc,
        on_back: Callable[[], None] | None = None,
        on_result: ResultCallback | None = None,
        *,
        execution_runner: ExecutionRunner | None = None,
        backend: str = "",
        timeout_ms: int = 2_000,
    ) -> None:
        super().__init__(master, background=BACKGROUND)
        self._on_back = on_back
        self._on_result = on_result
        self._execution_runner = execution_runner or _default_execution_runner
        self._backend = backend
        self._timeout_ms = int(timeout_ms)

        self._instruments: tuple[SelectedInstrument, ...] = ()
        self._routine_steps: tuple[RoutineStep, ...] = ()
        self._plan_items: tuple[MeasurementPlanItem, ...] = ()
        self._worker: threading.Thread | None = None
        self._stop_event: threading.Event | None = None
        self._emergency_event: threading.Event | None = None
        self._event_queue: queue.Queue[tuple[str, Any]] = queue.Queue()
        self._poll_after_id: str | None = None
        self._display_window: InstrumentDisplayWindow | None = None
        self._last_result: Any = None
        self._dry_run_approved_context: tuple[Any, ...] | None = None
        self._running = False
        self._destroying = False
        self._ui_scale = 1.0

        self.dry_run_var = tk.BooleanVar(value=True)
        self.status_var = tk.StringVar(
            value="루틴과 계획을 확인한 뒤 먼저 Dry Run을 실행해 주세요."
        )
        self.progress_var = tk.DoubleVar(value=0.0)
        self.progress_text_var = tk.StringVar(value="실행 전 · 0%")
        self.context_summary_var = tk.StringVar(value="장비 0대 · 루틴 0단계 · 계획 0개")
        self.mode_badge_var = tk.StringVar(value="실행 대기")
        self.dry_run_gate_var = tk.StringVar(
            value="1단계 · Dry Run 확인 필요"
        )

        self._font_metrics: dict[tk.Misc, tuple[tkfont.Font, int]] = {}
        self._widget_metrics: dict[tuple[tk.Misc, str], float] = {}
        self._layout_metrics: dict[
            tuple[tk.Misc, str, str],
            tuple[float, ...],
        ] = {}

        self._build()
        self._capture_scalable_widgets()
        self.apply_ui_scale(1.0)
        self._render_context()
        self._sync_action_states()

    @property
    def instruments(self) -> tuple[SelectedInstrument, ...]:
        return self._instruments

    @property
    def routine_steps(self) -> tuple[RoutineStep, ...]:
        return self._routine_steps

    @property
    def plan_items(self) -> tuple[MeasurementPlanItem, ...]:
        return self._plan_items

    @property
    def is_running(self) -> bool:
        return self._running

    def _build(self) -> None:
        self.columnconfigure(0, weight=1)
        self.rowconfigure(1, weight=5)
        self.rowconfigure(2, weight=3)

        self.header = tk.Frame(self, background=BACKGROUND)
        self.header.grid(row=0, column=0, sticky="ew", padx=34, pady=(22, 11))
        self.header.columnconfigure(0, weight=1)
        tk.Label(
            self.header,
            text="4. 설정한 순서를 실제 장비에 실행해요",
            font=("Segoe UI Semibold", 20),
            background=BACKGROUND,
            foreground=TEXT,
        ).grid(row=0, column=0, sticky="w")
        self.header_subtitle = tk.Label(
            self.header,
            text=(
                "왼쪽은 장비에 보낼 검증 루틴이고, 오른쪽은 결과와 함께 남길 시험 계획이에요. "
                "처음에는 장비에 명령을 보내지 않는 Dry Run으로 확인해 주세요."
            ),
            font=("Segoe UI", 10),
            background=BACKGROUND,
            foreground=SUBTEXT,
            anchor="w",
            justify="left",
        )
        self.header_subtitle.grid(row=1, column=0, sticky="ew", pady=(5, 0))
        self.back_button = _button(
            self.header,
            text="계획 설정으로 돌아가기",
            command=self._go_back,
            compact=True,
        )
        self.back_button.grid(row=0, column=1, rowspan=2, sticky="e")
        if self._on_back is None:
            self.back_button.grid_remove()
        self.header.bind("<Configure>", self._resize_header_copy, add="+")

        self.context_host = tk.Frame(self, background=BACKGROUND)
        self.context_host.grid(
            row=1,
            column=0,
            sticky="nsew",
            padx=34,
            pady=(0, 10),
        )
        self.context_host.columnconfigure(
            0,
            weight=1,
            minsize=360,
            uniform="execution_context_panels",
        )
        self.context_host.columnconfigure(
            1,
            weight=1,
            minsize=360,
            uniform="execution_context_panels",
        )
        self.context_host.rowconfigure(0, weight=1)
        self._build_routine_card()
        self._build_plan_card()

        self.run_card = tk.Frame(
            self,
            background=CARD,
            highlightbackground=BORDER,
            highlightthickness=1,
        )
        self.run_card.grid(
            row=2,
            column=0,
            sticky="nsew",
            padx=34,
            pady=(0, 16),
        )
        self.run_card.columnconfigure(0, weight=1)
        self.run_card.rowconfigure(3, weight=1)
        self._build_run_controls()

    def _build_routine_card(self) -> None:
        self.routine_card = tk.Frame(
            self.context_host,
            background=CARD,
            highlightbackground=BORDER,
            highlightthickness=1,
        )
        self.routine_card.grid(row=0, column=0, sticky="nsew", padx=(0, 7))
        self.routine_card.columnconfigure(0, weight=1)
        self.routine_card.rowconfigure(3, weight=1)

        tk.Label(
            self.routine_card,
            text="실행할 루틴",
            font=("Segoe UI Semibold", 13),
            background=CARD,
            foreground=TEXT,
        ).grid(row=0, column=0, sticky="w", padx=17, pady=(13, 2))
        tk.Label(
            self.routine_card,
            text="검증을 통과한 기능만 실제 SCPI 명령으로 바뀌어 전송돼요.",
            font=("Segoe UI", 9),
            background=CARD,
            foreground=SUBTEXT,
        ).grid(row=1, column=0, sticky="w", padx=17, pady=(0, 8))
        tk.Label(
            self.routine_card,
            text="장비에 실행",
            font=("Segoe UI Semibold", 8),
            background=SUCCESS_LIGHT,
            foreground=SUCCESS,
            padx=9,
            pady=4,
        ).grid(row=0, column=1, rowspan=2, sticky="ne", padx=17, pady=(13, 0))

        routine_list_host = tk.Frame(self.routine_card, background=CARD)
        routine_list_host.grid(
            row=3,
            column=0,
            columnspan=2,
            sticky="nsew",
            padx=17,
            pady=(0, 13),
        )
        routine_list_host.columnconfigure(0, weight=1)
        routine_list_host.rowconfigure(0, weight=1)
        self.routine_list = tk.Listbox(
            routine_list_host,
            height=6,
            width=1,
            font=("Segoe UI", 9),
            background="#FBFCFD",
            foreground=TEXT,
            selectbackground=ACCENT_LIGHT,
            selectforeground=TEXT,
            activestyle="none",
            relief="flat",
            borderwidth=0,
            highlightbackground=BORDER,
            highlightcolor=ACCENT,
            highlightthickness=1,
            exportselection=False,
        )
        self.routine_list.grid(row=0, column=0, sticky="nsew")
        routine_scroll = ttk.Scrollbar(
            routine_list_host,
            orient="vertical",
            command=self.routine_list.yview,
        )
        routine_scroll.grid(row=0, column=1, sticky="ns")
        self.routine_list.configure(yscrollcommand=routine_scroll.set)

    def _build_plan_card(self) -> None:
        self.plan_card = tk.Frame(
            self.context_host,
            background=CARD,
            highlightbackground=BORDER,
            highlightthickness=1,
        )
        self.plan_card.grid(row=0, column=1, sticky="nsew", padx=(7, 0))
        self.plan_card.columnconfigure(0, weight=1)
        self.plan_card.rowconfigure(3, weight=1)

        tk.Label(
            self.plan_card,
            text="함께 기록할 시험 계획",
            font=("Segoe UI Semibold", 13),
            background=CARD,
            foreground=TEXT,
        ).grid(row=0, column=0, sticky="w", padx=17, pady=(13, 2))
        tk.Label(
            self.plan_card,
            text="계획은 실행 지시가 아니라 결과를 해석하기 위한 시험 기준이에요.",
            font=("Segoe UI", 9),
            background=CARD,
            foreground=SUBTEXT,
        ).grid(row=1, column=0, sticky="w", padx=17, pady=(0, 8))
        tk.Label(
            self.plan_card,
            text="결과에 보관",
            font=("Segoe UI Semibold", 8),
            background=ACCENT_LIGHT,
            foreground=ACCENT_DARK,
            padx=9,
            pady=4,
        ).grid(row=0, column=1, rowspan=2, sticky="ne", padx=17, pady=(13, 0))

        plan_list_host = tk.Frame(self.plan_card, background=CARD)
        plan_list_host.grid(
            row=3,
            column=0,
            columnspan=2,
            sticky="nsew",
            padx=17,
            pady=(0, 13),
        )
        plan_list_host.columnconfigure(0, weight=1)
        plan_list_host.rowconfigure(0, weight=1)
        self.plan_list = tk.Listbox(
            plan_list_host,
            height=6,
            width=1,
            font=("Segoe UI", 9),
            background="#FBFCFD",
            foreground=TEXT,
            selectbackground=ACCENT_LIGHT,
            selectforeground=TEXT,
            activestyle="none",
            relief="flat",
            borderwidth=0,
            highlightbackground=BORDER,
            highlightcolor=ACCENT,
            highlightthickness=1,
            exportselection=False,
        )
        self.plan_list.grid(row=0, column=0, sticky="nsew")
        plan_scroll = ttk.Scrollbar(
            plan_list_host,
            orient="vertical",
            command=self.plan_list.yview,
        )
        plan_scroll.grid(row=0, column=1, sticky="ns")
        self.plan_list.configure(yscrollcommand=plan_scroll.set)

    def _build_run_controls(self) -> None:
        summary = tk.Frame(self.run_card, background=CARD)
        summary.grid(row=0, column=0, sticky="ew", padx=17, pady=(12, 5))
        summary.columnconfigure(0, weight=1)
        tk.Label(
            summary,
            textvariable=self.context_summary_var,
            font=("Segoe UI Semibold", 10),
            background=CARD,
            foreground=TEXT,
        ).grid(row=0, column=0, sticky="w")
        self.display_button = _button(
            summary,
            text="실제 값 디스플레이 보기",
            command=self.open_actual_value_display,
            compact=True,
        )
        self.display_button.grid(row=0, column=1, sticky="e", padx=(8, 8))
        self.mode_badge = tk.Label(
            summary,
            textvariable=self.mode_badge_var,
            font=("Segoe UI Semibold", 8),
            background=NEUTRAL_LIGHT,
            foreground=SUBTEXT,
            padx=9,
            pady=4,
        )
        self.mode_badge.grid(row=0, column=2, sticky="e")

        progress_host = tk.Frame(self.run_card, background=CARD)
        progress_host.grid(row=1, column=0, sticky="ew", padx=17)
        progress_host.columnconfigure(0, weight=1)
        self.progress = ttk.Progressbar(
            progress_host,
            orient="horizontal",
            mode="determinate",
            maximum=100,
            variable=self.progress_var,
        )
        self.progress.grid(row=0, column=0, sticky="ew")
        tk.Label(
            progress_host,
            textvariable=self.progress_text_var,
            font=("Segoe UI", 8),
            background=CARD,
            foreground=SUBTEXT,
            width=18,
            anchor="e",
        ).grid(row=0, column=1, padx=(12, 0))

        controls = tk.Frame(self.run_card, background=CARD)
        controls.grid(row=2, column=0, sticky="ew", padx=17, pady=(8, 7))
        controls.columnconfigure(0, weight=1)
        self.dry_run_gate_label = tk.Label(
            controls,
            textvariable=self.dry_run_gate_var,
            font=("Segoe UI", 9),
            background=ACCENT_LIGHT,
            foreground=ACCENT_DARK,
            padx=9,
            pady=5,
        )
        self.dry_run_gate_label.grid(row=0, column=0, sticky="w")
        self.dry_run_button = _button(
            controls,
            text="Dry Run 확인",
            command=self.start_dry_run,
            compact=True,
        )
        self.dry_run_button.grid(row=0, column=1, padx=(6, 0))
        self.live_run_button = _button(
            controls,
            text="실제 실행 시작",
            command=self.start_live_run,
            primary=True,
            compact=True,
        )
        self.live_run_button.grid(row=0, column=2, padx=(6, 0))
        self.stop_button = _button(
            controls,
            text="중지 요청",
            command=self.request_stop,
            compact=True,
        )
        self.stop_button.grid(row=0, column=3, padx=(6, 0))
        self.emergency_button = _button(
            controls,
            text="비상정지",
            command=self.request_emergency_stop,
            danger=True,
            compact=True,
        )
        self.emergency_button.grid(row=0, column=4, padx=(6, 0))

        log_host = tk.Frame(self.run_card, background=CARD)
        log_host.grid(
            row=3,
            column=0,
            sticky="nsew",
            padx=17,
            pady=(0, 7),
        )
        log_host.columnconfigure(0, weight=1)
        log_host.rowconfigure(0, weight=1)
        self.log_text = tk.Text(
            log_host,
            height=5,
            wrap="word",
            font=("Cascadia Mono", 8),
            background="#111827",
            foreground="#D1D5DB",
            insertbackground="#FFFFFF",
            relief="flat",
            borderwidth=0,
            padx=10,
            pady=7,
            state="disabled",
        )
        self.log_text.grid(row=0, column=0, sticky="nsew")
        log_scroll = ttk.Scrollbar(
            log_host,
            orient="vertical",
            command=self.log_text.yview,
        )
        log_scroll.grid(row=0, column=1, sticky="ns")
        self.log_text.configure(yscrollcommand=log_scroll.set)

        self.status_label = tk.Label(
            self.run_card,
            textvariable=self.status_var,
            font=("Segoe UI", 9),
            background=ACCENT_LIGHT,
            foreground="#3B608A",
            anchor="w",
            padx=12,
            pady=7,
        )
        self.status_label.grid(row=4, column=0, sticky="ew")

    def set_context(
        self,
        instruments: tuple[SelectedInstrument, ...] | list[SelectedInstrument],
        routine_steps: tuple[RoutineStep, ...] | list[RoutineStep],
        plan_items: (
            tuple[MeasurementPlanItem, ...] | list[MeasurementPlanItem]
        ),
    ) -> None:
        """Replace the run snapshot shown on this tab.

        The current snapshot is immutable from this tab. A running job keeps
        its original snapshot and therefore cannot be replaced midway.
        """

        if self._running:
            self.status_var.set(
                "실행 중에는 루틴과 계획을 바꿀 수 없어요. 먼저 중지해 주세요."
            )
            return
        next_instruments = tuple(instruments)
        next_routine_steps = tuple(routine_steps)
        next_plan_items = tuple(plan_items)
        context_changed = (
            next_instruments != self._instruments
            or next_routine_steps != self._routine_steps
            or next_plan_items != self._plan_items
        )
        self._instruments = next_instruments
        self._routine_steps = next_routine_steps
        self._plan_items = next_plan_items
        if context_changed:
            self._last_result = None
            self._dry_run_approved_context = None
            self.dry_run_gate_var.set("1단계 · Dry Run 확인 필요")
        if self._display_window is not None:
            try:
                if not self._display_window.winfo_exists():
                    self._display_window = None
                elif context_changed:
                    self._display_window.set_instruments(
                        self._instruments,
                        self._routine_steps,
                    )
            except tk.TclError:
                self._display_window = None
        self.dry_run_var.set(True)
        self.progress_var.set(0.0)
        self.progress_text_var.set("실행 전 · 0%")
        self.mode_badge_var.set("실행 대기")
        self._configure_mode_badge("idle")
        self._clear_log()
        self._render_context()
        if not self._instruments:
            self.status_var.set(
                "실행할 장비가 없어요. 장비 찾기에서 사용할 장비를 선택해 주세요."
            )
        elif not self._routine_steps:
            self.status_var.set(
                "실행할 루틴이 비어 있어요. 루틴 설정에서 단계를 추가해 주세요."
            )
        else:
            self.status_var.set(
                "준비됐어요. Dry Run을 통과하면 실제 실행 버튼이 열려요."
            )
        self._sync_action_states()

    def set_connection_settings(
        self,
        *,
        backend: str = "",
        timeout_ms: int = 2_000,
    ) -> None:
        """Use the VISA selection made during device discovery."""

        if self._running:
            raise RuntimeError("실행 중에는 VISA 연결 설정을 바꿀 수 없습니다.")
        timeout = int(timeout_ms)
        if not 1 <= timeout <= 600_000:
            raise ValueError("VISA Timeout은 1~600000 ms 범위여야 합니다.")
        self._backend = str(backend)
        self._timeout_ms = timeout

    def _render_context(self) -> None:
        self.routine_list.delete(0, tk.END)
        for index, step in enumerate(self._routine_steps, start=1):
            self.routine_list.insert(
                tk.END,
                self._routine_line(index, step),
            )
        if not self._routine_steps:
            self.routine_list.insert(
                tk.END,
                "아직 실행할 루틴이 없어요.",
            )

        self.plan_list.delete(0, tk.END)
        for index, item in enumerate(self._plan_items, start=1):
            self.plan_list.insert(
                tk.END,
                self._plan_line(index, item),
            )
        if not self._plan_items:
            has_bindings = any(
                (
                    isinstance(step, SelectedFeature)
                    and step.plan_bindings
                )
                or isinstance(step, PlanBoundDelayStep)
                for step in self._routine_steps
            )
            self.plan_list.insert(
                tk.END,
                (
                    "루틴이 계획값을 기다리고 있어요. 계획서에서 시험을 만들어 주세요."
                    if has_bindings
                    else "고정값 루틴이라 계획 없이 한 번 실행할 수 있어요."
                ),
            )

        try:
            compiled = compile_routine_with_plan(
                self._routine_steps,
                self._plan_items,
                selected_instruments=self._instruments,
            )
        except (PlanCompilationError, KeyError, TypeError, ValueError):
            compiled = None
        if compiled is not None and compiled.uses_plan_values:
            execution_summary = (
                f"시험 {len(compiled.cases)}개 · "
                f"실행 {len(compiled.steps)}단계"
            )
        else:
            execution_summary = f"루틴 {len(self._routine_steps)}단계"
        self.context_summary_var.set(
            f"장비 {len(self._instruments)}대 · "
            f"{execution_summary} · 계획 설정 {len(self._plan_items)}개"
        )

    def _resize_header_copy(self, event: tk.Event) -> None:
        reserved = (
            self.back_button.winfo_reqwidth() + 24
            if self.back_button.winfo_manager()
            else 0
        )
        self.header_subtitle.configure(
            wraplength=max(320, int(event.width) - reserved)
        )

    @staticmethod
    def _device_name(instrument: SelectedInstrument) -> str:
        model = instrument.model.strip()
        if model and instrument.serial.strip():
            return f"{model} · {instrument.serial.strip()}"
        return model or instrument.display_name

    @classmethod
    def _routine_line(cls, index: int, step: RoutineStep) -> str:
        if isinstance(step, SelectedFeature):
            try:
                feature = feature_by_id(
                    step.feature_id,
                    step.instrument.profile_id,
                )
                feature_name = feature.display_name
            except (KeyError, ValueError):
                feature = None
                feature_name = step.feature_id
            arguments = (
                " · "
                + (
                    format_feature_arguments(feature, step.arguments)
                    if feature is not None
                    else ", ".join(
                        f"{name}={value}" for name, value in step.arguments
                    )
                )
                if step.arguments
                else ""
            )
            if step.plan_bindings:
                binding_text = ", ".join(
                    (
                        definition.label_ko
                        if (
                            definition := plan_binding_definition(
                                feature.capability_id,
                                feature.operation,
                                binding.parameter_name,
                            )
                        )
                        is not None
                        else binding.field_id
                    )
                    for binding in step.plan_bindings
                )
                arguments += f" · 계획값 [{binding_text}]"
            result_name = f" → {step.result_name}" if step.result_name else ""
            return (
                f"{index:02d}  [{cls._device_name(step.instrument)}]  "
                f"{feature_name}{arguments}{result_name}"
            )
        if isinstance(step, DelayStep):
            return f"{index:02d}  [PC]  Delay - {step.seconds:g}초 대기"
        if isinstance(step, PlanBoundDelayStep):
            return (
                f"{index:02d}  [{cls._device_name(step.instrument)}]  "
                "Delay - 계획의 Dwell만큼 대기"
            )
        if isinstance(step, WaitForCompletionStep):
            return (
                f"{index:02d}  [{cls._device_name(step.instrument)}]  "
                f"앞 명령 완료 확인 · 제한 {step.timeout_seconds:g}초"
            )
        return f"{index:02d}  알 수 없는 루틴 단계"

    @staticmethod
    def _format_frequency(value_hz: float) -> str:
        for unit, factor in (
            ("GHz", 1_000_000_000.0),
            ("MHz", 1_000_000.0),
            ("kHz", 1_000.0),
        ):
            if value_hz >= factor:
                return f"{value_hz / factor:g} {unit}"
        return f"{value_hz:g} Hz"

    @classmethod
    def _plan_line(cls, index: int, item: MeasurementPlanItem) -> str:
        device = cls._device_name(item.instrument)
        case = item.case_name or "기존 계획"
        if isinstance(item, SpectrumPlanItem):
            rbw = (
                "자동"
                if item.rbw_hz is None
                else cls._format_frequency(item.rbw_hz)
            )
            return (
                f"{index:02d}  [{case}] [{device}]  Center "
                f"{cls._format_frequency(item.center_frequency_hz)} · "
                f"Span {cls._format_frequency(item.span_hz)} · RBW {rbw}"
            )
        if isinstance(item, SignalGeneratorPlanItem):
            return (
                f"{index:02d}  [{case}] [{device}]  Frequency "
                f"{cls._format_frequency(item.frequency_hz)} · "
                f"Power {item.power_dbm:g} dBm · Dwell {item.dwell_seconds:g}초"
            )
        if isinstance(item, GenericPlanItem):
            return (
                f"{index:02d}  [{case}] [{device}]  "
                f"{item.category.label_ko} · {item.method_label_ko}"
            )
        return f"{index:02d}  [{case}] [{device}]  시험 계획"

    def start_dry_run(self) -> None:
        self.dry_run_var.set(True)
        self._start_execution(dry_run=True)

    def start_live_run(self) -> None:
        self.dry_run_var.set(False)
        if not self._preflight_ui():
            self.dry_run_var.set(True)
            return
        if self._contains_demo_instrument():
            self.dry_run_var.set(True)
            self.status_var.set(
                "데모 장비는 화면 연습용이라 실제 실행할 수 없어요."
            )
            return
        if self._dry_run_approved_context != self._context_signature():
            self.dry_run_var.set(True)
            self.status_var.set(
                "현재 루틴과 계획으로 Dry Run을 먼저 완료해 주세요."
            )
            return
        risk_summary = self._live_confirmation_summary()
        confirmed = messagebox.askyesno(
            "실제 장비 실행 확인",
            "지금부터 PC에 연결된 장비에 실제 SCPI 명령을 보냅니다.\n\n"
            f"{risk_summary}\n\n"
            "• 출력·전압·전류·주파수 설정값을 다시 확인했나요?\n"
            "• 케이블과 시험 대상의 허용 범위를 확인했나요?\n"
            "• 문제가 생기면 ‘비상정지’를 누를 준비가 되었나요?\n\n"
            "계속하려면 ‘예’를 눌러 주세요.",
            icon="warning",
            parent=self,
        )
        if not confirmed:
            self.dry_run_var.set(True)
            self.status_var.set(
                "실제 실행을 취소했어요. 장비에는 아무 명령도 보내지 않았어요."
            )
            return
        self._start_execution(dry_run=False, preflight_done=True)

    def _preflight_ui(self) -> bool:
        if self._running:
            self.status_var.set("이미 실행 중이에요. 현재 실행이 끝날 때까지 기다려 주세요.")
            return False
        if not self._instruments:
            self.status_var.set(
                "실행할 장비가 없어요. 장비 찾기에서 사용할 장비를 선택해 주세요."
            )
            return False
        if not self._routine_steps:
            self.status_var.set(
                "실행할 루틴이 비어 있어요. 루틴 설정에서 단계를 추가해 주세요."
            )
            return False
        try:
            compiled = compile_routine_with_plan(
                self._routine_steps,
                self._plan_items,
                selected_instruments=self._instruments,
            )
        except (PlanCompilationError, KeyError, TypeError, ValueError) as exc:
            self.status_var.set(f"루틴과 시험 계획을 연결할 수 없어요: {exc}")
            return False
        if compiled.uses_plan_values:
            self.status_var.set(
                f"시험 케이스 {len(compiled.cases)}개를 "
                f"총 {len(compiled.steps)}단계로 확인했어요."
            )
        return True

    def _start_execution(
        self,
        *,
        dry_run: bool,
        preflight_done: bool = False,
    ) -> None:
        if not preflight_done and not self._preflight_ui():
            return

        self._running = True
        self._last_result = None
        if dry_run:
            self._dry_run_approved_context = None
            self.dry_run_gate_var.set("Dry Run 확인 중")
        self._stop_event = threading.Event()
        self._emergency_event = threading.Event()
        self.progress_var.set(0.0)
        self.progress_text_var.set(
            "Dry Run 시작 · 0%" if dry_run else "실제 실행 시작 · 0%"
        )
        self.mode_badge_var.set("DRY RUN" if dry_run else "실제 실행 중")
        self._configure_mode_badge("dry_run" if dry_run else "live")
        self.status_var.set(
            "장비에 명령을 보내지 않고 순서를 확인하고 있어요."
            if dry_run
            else "실제 장비에 루틴을 실행하고 있어요. 창을 닫지 마세요."
        )
        self._clear_log()
        try:
            display_steps = compile_routine_with_plan(
                self._routine_steps,
                self._plan_items,
                selected_instruments=self._instruments,
            ).steps
        except (PlanCompilationError, KeyError, TypeError, ValueError):
            display_steps = self._routine_steps
        if self._display_window is not None:
            try:
                if self._display_window.winfo_exists():
                    self._display_window.set_instruments(
                        self._instruments,
                        display_steps,
                    )
            except tk.TclError:
                self._display_window = None
        self._append_log(
            "INFO",
            (
                "Dry Run을 시작합니다. 장비에는 명령을 보내지 않습니다."
                if dry_run
                else "실제 장비 실행을 시작합니다."
            ),
        )
        self._sync_action_states()

        instruments = self._instruments
        routine_steps = self._routine_steps
        plan_items = self._plan_items
        stop_event = self._stop_event
        emergency_event = self._emergency_event

        def event_callback(event: Any) -> None:
            self._event_queue.put(("event", event))

        def worker() -> None:
            try:
                result = self._execution_runner(
                    instruments=instruments,
                    routine_steps=routine_steps,
                    plan_items=plan_items,
                    dry_run=dry_run,
                    backend=self._backend,
                    timeout_ms=self._timeout_ms,
                    stop_event=stop_event,
                    emergency_event=emergency_event,
                    event_callback=event_callback,
                    operator_confirmed=not dry_run,
                )
            except BaseException as exc:
                self._event_queue.put(("error", exc))
            else:
                self._event_queue.put(("result", result))

        self._worker = threading.Thread(
            target=worker,
            name="scpi-execution-ui-worker",
            daemon=True,
        )
        self._worker.start()
        self._schedule_queue_poll()

    def request_stop(self) -> None:
        if not self._running or self._stop_event is None:
            self.status_var.set("현재 실행 중인 작업이 없어요.")
            return
        self._stop_event.set()
        self.status_var.set(
            "현재 단계가 안전하게 끝나는 즉시 멈추도록 요청했어요."
        )
        self._append_log("WARNING", "사용자가 일반 중지를 요청했습니다.")
        self._set_button_state(self.stop_button, False)

    def request_emergency_stop(self) -> None:
        if not self._running:
            self.status_var.set("현재 실행 중인 작업이 없어요.")
            return
        if self._emergency_event is not None:
            self._emergency_event.set()
        if self._stop_event is not None:
            self._stop_event.set()
        self.status_var.set(
            "비상정지를 요청했어요. 가능한 다음 통신 지점에서 "
            "검증된 출력 OFF 절차를 시도합니다."
        )
        self._append_log(
            "ERROR",
            "사용자가 비상정지를 요청했습니다. 통신 중이면 즉시 완료되지 않을 수 "
            "있으며, 가능한 다음 지점에서 안전 출력 OFF 절차를 요청합니다.",
        )
        self.mode_badge_var.set("비상정지 요청")
        self._configure_mode_badge("danger")
        self._set_button_state(self.stop_button, False)
        self._set_button_state(self.emergency_button, False)

    def _schedule_queue_poll(self) -> None:
        if self._destroying or self._poll_after_id is not None:
            return
        try:
            self._poll_after_id = self.after(60, self._drain_event_queue)
        except tk.TclError:
            self._poll_after_id = None

    def _drain_event_queue(self) -> None:
        self._poll_after_id = None
        if self._destroying:
            return
        terminal = False
        while True:
            try:
                kind, payload = self._event_queue.get_nowait()
            except queue.Empty:
                break
            if kind == "event":
                self._handle_execution_event(payload)
            elif kind == "result":
                self._finish_with_result(payload)
                terminal = True
            elif kind == "error":
                self._finish_with_error(payload)
                terminal = True
        if self._running and not terminal:
            self._schedule_queue_poll()

    @staticmethod
    def _event_value(event: Any, name: str, default: Any = None) -> Any:
        if isinstance(event, dict):
            return event.get(name, default)
        return getattr(event, name, default)

    @staticmethod
    def _display_timestamp(value: Any) -> str:
        if isinstance(value, datetime):
            return value.astimezone().strftime("%H:%M:%S")
        text = str(value or "").strip()
        if not text:
            return ""
        normalized = (
            f"{text[:-1]}+00:00"
            if text.endswith(("Z", "z"))
            else text
        )
        try:
            parsed = datetime.fromisoformat(normalized)
        except ValueError:
            if "T" in text:
                text = text.split("T", 1)[1]
            return text[:8] if len(text) >= 8 else text
        if parsed.tzinfo is not None:
            parsed = parsed.astimezone()
        return parsed.strftime("%H:%M:%S")

    def _handle_execution_event(self, event: Any) -> None:
        if self._display_window is not None:
            try:
                if self._display_window.winfo_exists():
                    self._display_window.update_from_event(event)
                else:
                    self._display_window = None
            except tk.TclError:
                self._display_window = None
        level = str(self._event_value(event, "level", "INFO")).upper()
        message = str(self._event_value(event, "message", "")).strip()
        case_name = str(
            self._event_value(event, "case_name", "")
        ).strip()
        repeat_index = self._event_value(event, "repeat_index", 0)
        repeat_count = self._event_value(event, "repeat_count", 0)
        if case_name:
            repeat_text = (
                f" · 반복 {repeat_index}/{repeat_count}"
                if repeat_index and repeat_count
                else ""
            )
            message = f"[{case_name}{repeat_text}] {message}"
        timestamp = self._display_timestamp(
            self._event_value(event, "timestamp_utc", "")
        )
        self._append_log(level, message or "실행 상태가 갱신되었습니다.", timestamp)

        step_index = self._event_value(event, "step_index")
        total_steps = self._event_value(event, "total_steps")
        try:
            step_number = int(step_index)
            total = int(total_steps)
        except (TypeError, ValueError):
            return
        if total <= 0:
            return
        step_number = min(max(step_number, 0), total)
        percent = (step_number / total) * 100
        self.progress_var.set(percent)
        mode = "Dry Run" if self.dry_run_var.get() else "실제 실행"
        self.progress_text_var.set(
            f"{mode} · {step_number}/{total} · {percent:.0f}%"
            + (f" · {case_name}" if case_name else "")
        )

    def _finish_with_result(self, result: Any) -> None:
        self._running = False
        self._worker = None
        self._last_result = result
        if self._display_window is not None:
            try:
                if self._display_window.winfo_exists():
                    self._display_window.update_from_result(result)
                else:
                    self._display_window = None
            except tk.TclError:
                self._display_window = None
        status = self._result_status(result)
        status_key = status.casefold()
        result_is_dry_run = bool(
            self._event_value(result, "dry_run", False)
        )
        if status_key in {"completed", "success", "passed", "dry_run_completed"}:
            if result_is_dry_run:
                self._dry_run_approved_context = self._context_signature()
                self.dry_run_gate_var.set(
                    "Dry Run 통과 · 실제 실행 가능"
                )
            self.progress_var.set(100.0)
            self.progress_text_var.set("완료 · 100%")
            self.mode_badge_var.set("실행 완료")
            self._configure_mode_badge("success")
            self.status_var.set(
                "실행이 끝났어요. 5단계 결과 화면에서 측정값과 기록을 확인해 주세요."
            )
            self._append_log("INFO", "실행 결과 기록이 완성되었습니다.")
        elif status_key in {"stopped", "cancelled", "canceled", "emergency_stopped"}:
            self.progress_text_var.set("중지됨")
            self.mode_badge_var.set("실행 중지")
            self._configure_mode_badge("warning")
            self.status_var.set(
                "실행이 중지됐어요. 중지 전까지의 기록은 결과 화면에서 확인할 수 있어요."
            )
            self._append_log("WARNING", "실행이 중지되었습니다.")
        else:
            if result_is_dry_run:
                self._dry_run_approved_context = None
                self.dry_run_gate_var.set(
                    "Dry Run 실패 · 로그 확인 필요"
                )
            self.progress_text_var.set("오류로 종료")
            self.mode_badge_var.set("실행 오류")
            self._configure_mode_badge("danger")
            self.status_var.set(
                "실행 중 오류가 발생했어요. 결과 화면의 오류와 명령 로그를 확인해 주세요."
            )
            self._append_log("ERROR", f"실행 결과 상태: {status or '알 수 없음'}")
        self._sync_action_states()
        if self._on_result is not None:
            try:
                self._on_result(result)
            except Exception as exc:
                self.status_var.set(
                    "실행은 끝났지만 결과 화면을 여는 중 문제가 생겼어요."
                )
                self._append_log("ERROR", f"결과 화면 연결 오류: {exc}")

    def _finish_with_error(self, error: BaseException) -> None:
        self._running = False
        self._worker = None
        self._last_result = None
        self.progress_text_var.set("실행 엔진 오류")
        self.mode_badge_var.set("실행 오류")
        self._configure_mode_badge("danger")
        self.status_var.set(
            "실행 엔진을 시작하거나 완료하지 못했어요. 로그를 확인해 주세요."
        )
        self._append_log("ERROR", f"{type(error).__name__}: {error}")
        self._sync_action_states()
        messagebox.showerror(
            "실행 실패",
            "측정 실행을 완료하지 못했어요.\n\n"
            f"{error}\n\n"
            "장비 연결, VISA 드라이버, 검증 상태를 확인해 주세요.",
            parent=self,
        )

    @classmethod
    def _result_status(cls, result: Any) -> str:
        value = cls._event_value(result, "status", "")
        return str(getattr(value, "value", value) or "")

    def _append_log(
        self,
        level: str,
        message: str,
        timestamp: str = "",
    ) -> None:
        if not message:
            return
        if not timestamp:
            timestamp = datetime.now().strftime("%H:%M:%S")
        line = f"{timestamp}  {level:<7}  {message}\n"
        try:
            self.log_text.configure(state="normal")
            self.log_text.insert(tk.END, line)
            line_count = int(self.log_text.index("end-1c").split(".", 1)[0])
            if line_count > 2_000:
                self.log_text.delete("1.0", "201.0")
            self.log_text.see(tk.END)
            self.log_text.configure(state="disabled")
        except tk.TclError:
            pass

    def _clear_log(self) -> None:
        try:
            self.log_text.configure(state="normal")
            self.log_text.delete("1.0", tk.END)
            self.log_text.configure(state="disabled")
        except tk.TclError:
            pass

    def _configure_mode_badge(self, mode: str) -> None:
        colors = {
            "idle": (NEUTRAL_LIGHT, SUBTEXT),
            "dry_run": (ACCENT_LIGHT, ACCENT_DARK),
            "live": (WARNING_LIGHT, WARNING),
            "success": (SUCCESS_LIGHT, SUCCESS),
            "warning": (WARNING_LIGHT, WARNING),
            "danger": (DANGER_LIGHT, DANGER),
        }
        background, foreground = colors.get(mode, colors["idle"])
        self.mode_badge.configure(
            background=background,
            foreground=foreground,
        )

    def _sync_action_states(self) -> None:
        ready = bool(self._instruments and self._routine_steps)
        live_ready = (
            ready
            and not self._running
            and not self._contains_demo_instrument()
            and self._dry_run_approved_context == self._context_signature()
        )
        self._set_button_state(self.dry_run_button, ready and not self._running)
        self._set_button_state(self.live_run_button, live_ready)
        self._set_button_state(self.stop_button, self._running)
        self._set_button_state(self.emergency_button, self._running)
        self._set_button_state(self.display_button, bool(self._instruments))
        self._set_button_state(self.back_button, not self._running)

    @staticmethod
    def _set_button_state(widget: tk.Misc, enabled: bool) -> None:
        try:
            widget.configure(
                state="normal" if enabled else "disabled",
                cursor="hand2" if enabled else "arrow",
            )
        except tk.TclError:
            pass

    def _go_back(self) -> None:
        if self._running:
            self.status_var.set("실행 중에는 이전 단계로 갈 수 없어요. 먼저 중지해 주세요.")
            return
        if self._on_back is not None:
            self._on_back()

    def open_actual_value_display(self) -> None:
        """Open a read-only display fed only by execution responses."""

        if not self._instruments:
            self.status_var.set(
                "표시할 장비가 없어요. 장비 찾기에서 사용할 장비를 선택해 주세요."
            )
            return
        if (
            self._display_window is not None
            and self._display_window.focus_existing()
        ):
            try:
                steps = compile_routine_with_plan(
                    self._routine_steps,
                    self._plan_items,
                    selected_instruments=self._instruments,
                ).steps
            except (PlanCompilationError, KeyError, TypeError, ValueError):
                steps = self._routine_steps
            self._display_window.set_routine_steps(steps)
            return
        try:
            display_steps = compile_routine_with_plan(
                self._routine_steps,
                self._plan_items,
                selected_instruments=self._instruments,
            ).steps
        except (PlanCompilationError, KeyError, TypeError, ValueError):
            display_steps = self._routine_steps
        self._display_window = InstrumentDisplayWindow(
            self,
            self._instruments,
            display_steps,
        )
        if self._last_result is not None:
            self._display_window.update_from_result(self._last_result)
        self.status_var.set(
            "장비 디스플레이를 열었어요. 루틴에서 실제로 조회한 값만 표시합니다."
        )

    def _context_signature(self) -> tuple[Any, ...]:
        return (
            self._instruments,
            self._routine_steps,
            self._plan_items,
        )

    def _contains_demo_instrument(self) -> bool:
        return any(
            instrument.resource.startswith("DEMO::")
            for instrument in self._instruments
        )

    def _live_confirmation_summary(self) -> str:
        device_lines = [
            (
                f"• {self._device_name(instrument)}"
                f" · {instrument.resource}"
            )
            for instrument in self._instruments
        ]
        try:
            compiled = compile_routine_with_plan(
                self._routine_steps,
                self._plan_items,
                selected_instruments=self._instruments,
            )
            preview_steps = compiled.steps
            plan_summary = (
                f"시험 케이스 {len(compiled.cases)}개 · "
                f"실제 실행 {len(compiled.steps)}단계"
                if compiled.uses_plan_values
                else f"고정 루틴 {len(compiled.steps)}단계"
            )
        except (PlanCompilationError, KeyError, TypeError, ValueError) as exc:
            preview_steps = self._routine_steps
            plan_summary = f"계획 연결 오류: {exc}"
        setting_lines: list[str] = []
        for step in preview_steps:
            if not isinstance(step, SelectedFeature):
                continue
            try:
                feature = feature_by_id(
                    step.feature_id,
                    step.instrument.profile_id,
                )
            except (KeyError, ValueError):
                continue
            if feature.operation not in {"set", "execute"}:
                continue
            arguments = format_feature_arguments(
                feature,
                step.arguments,
            )
            setting_lines.append(
                f"• {feature.display_name}"
                + (f" · {arguments}" if arguments else "")
            )
        if not setting_lines:
            setting_lines.append("• 설정 변경 없음 · 조회와 대기 단계 중심")
        if len(setting_lines) > 8:
            hidden = len(setting_lines) - 8
            setting_lines = [
                *setting_lines[:8],
                f"• 그 밖의 설정 {hidden}개",
            ]
        plan_ranges: list[str] = []
        for instrument in self._instruments:
            generator_items = [
                item
                for item in self._plan_items
                if (
                    isinstance(item, SignalGeneratorPlanItem)
                    and item.instrument.resource == instrument.resource
                )
            ]
            if generator_items:
                frequencies = [item.frequency_hz for item in generator_items]
                powers = [item.power_dbm for item in generator_items]
                plan_ranges.append(
                    f"• {self._device_name(instrument)} · Frequency "
                    f"{self._format_frequency(min(frequencies))} ~ "
                    f"{self._format_frequency(max(frequencies))} · Power "
                    f"{min(powers):g} ~ {max(powers):g} dBm"
                )
            analyzer_items = [
                item
                for item in self._plan_items
                if (
                    isinstance(item, SpectrumPlanItem)
                    and item.instrument.resource == instrument.resource
                )
            ]
            if analyzer_items:
                centers = [item.center_frequency_hz for item in analyzer_items]
                plan_ranges.append(
                    f"• {self._device_name(instrument)} · Center "
                    f"{self._format_frequency(min(centers))} ~ "
                    f"{self._format_frequency(max(centers))}"
                )
        range_section = (
            "\n\n계획값 범위\n" + "\n".join(plan_ranges)
            if plan_ranges
            else ""
        )
        return (
            "실행 대상\n"
            + "\n".join(device_lines)
            + f"\n\n시험 구성\n• {plan_summary}"
            + range_section
            + "\n\n장비에 보낼 설정\n"
            + "\n".join(setting_lines)
        )

    @property
    def has_active_worker(self) -> bool:
        """Return whether the execution worker is still using the VISA layer."""

        return self._worker is not None and self._worker.is_alive()

    def shutdown(self) -> None:
        """Request a safe stop and release scheduled UI callbacks."""

        if self._destroying:
            return
        self._destroying = True
        if self._emergency_event is not None and self._running:
            self._emergency_event.set()
        if self._stop_event is not None:
            self._stop_event.set()
        if self._poll_after_id is not None:
            try:
                self.after_cancel(self._poll_after_id)
            except tk.TclError:
                pass
            self._poll_after_id = None
        if self._display_window is not None:
            try:
                if self._display_window.winfo_exists():
                    self._display_window.destroy()
            except tk.TclError:
                pass
            self._display_window = None

    @staticmethod
    def _scaled_size(base_size: int, scale: float) -> int:
        return max(7, int(round(base_size * scale)))

    def _descendants(self, parent: tk.Misc) -> list[tk.Misc]:
        result: list[tk.Misc] = []
        for child in parent.winfo_children():
            result.append(child)
            result.extend(self._descendants(child))
        return result

    def _parse_padding(self, value: Any) -> tuple[float, ...] | None:
        if isinstance(value, (tuple, list)):
            parts = value
        else:
            try:
                parts = self.tk.splitlist(value)
            except (tk.TclError, TypeError):
                parts = (value,)
        parsed: list[float] = []
        for part in parts:
            try:
                parsed.append(float(part))
            except (TypeError, ValueError):
                return None
        return tuple(parsed) if parsed else None

    def _capture_scalable_widgets(self) -> None:
        for widget in [self, *self._descendants(self)]:
            if widget not in self._font_metrics:
                try:
                    raw_font = widget.cget("font")
                except (tk.TclError, KeyError):
                    raw_font = ""
                if raw_font:
                    try:
                        resolved = tkfont.Font(root=self, font=raw_font)
                        base_size = abs(int(resolved.cget("size"))) or 9
                        responsive_font = tkfont.Font(
                            root=self,
                            family=resolved.cget("family"),
                            size=base_size,
                            weight=resolved.cget("weight"),
                            slant=resolved.cget("slant"),
                            underline=resolved.cget("underline"),
                            overstrike=resolved.cget("overstrike"),
                        )
                        widget.configure(font=responsive_font)
                        self._font_metrics[widget] = (responsive_font, base_size)
                    except (tk.TclError, ValueError):
                        pass

            for option in ("padx", "pady", "wraplength"):
                key = (widget, option)
                if key in self._widget_metrics:
                    continue
                try:
                    value = float(widget.cget(option))
                except (tk.TclError, KeyError, TypeError, ValueError):
                    continue
                if value > 0:
                    self._widget_metrics[key] = value

            manager = widget.winfo_manager()
            if manager not in {"grid", "pack"}:
                continue
            try:
                info = widget.grid_info() if manager == "grid" else widget.pack_info()
            except tk.TclError:
                continue
            for option in ("padx", "pady", "ipadx", "ipady"):
                key = (widget, manager, option)
                if key in self._layout_metrics or option not in info:
                    continue
                parsed = self._parse_padding(info[option])
                if parsed and any(value > 0 for value in parsed):
                    self._layout_metrics[key] = parsed

    def apply_ui_scale(self, scale: float) -> None:
        self._ui_scale = max(0.75, min(1.4, float(scale)))
        self._capture_scalable_widgets()

        stale_fonts: list[tk.Misc] = []
        for widget, (font, base_size) in self._font_metrics.items():
            try:
                if not widget.winfo_exists():
                    stale_fonts.append(widget)
                    continue
                font.configure(size=self._scaled_size(base_size, self._ui_scale))
            except tk.TclError:
                stale_fonts.append(widget)
        for widget in stale_fonts:
            self._font_metrics.pop(widget, None)

        stale_metrics: list[tuple[tk.Misc, str]] = []
        for (widget, option), base_value in self._widget_metrics.items():
            try:
                if not widget.winfo_exists():
                    stale_metrics.append((widget, option))
                    continue
                widget.configure(
                    **{option: max(1, int(round(base_value * self._ui_scale)))}
                )
            except tk.TclError:
                stale_metrics.append((widget, option))
        for key in stale_metrics:
            self._widget_metrics.pop(key, None)

        stale_layouts: list[tuple[tk.Misc, str, str]] = []
        for (widget, manager, option), values in self._layout_metrics.items():
            try:
                if not widget.winfo_exists() or widget.winfo_manager() != manager:
                    stale_layouts.append((widget, manager, option))
                    continue
                scaled = tuple(
                    max(0, int(round(value * self._ui_scale))) for value in values
                )
                rendered: int | tuple[int, ...] = (
                    scaled[0] if len(scaled) == 1 else scaled
                )
                if manager == "grid":
                    widget.grid_configure(**{option: rendered})
                else:
                    widget.pack_configure(**{option: rendered})
            except tk.TclError:
                stale_layouts.append((widget, manager, option))
        for key in stale_layouts:
            self._layout_metrics.pop(key, None)
