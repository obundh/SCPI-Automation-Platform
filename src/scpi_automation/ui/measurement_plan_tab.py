from __future__ import annotations

import math
import tkinter as tk
import tkinter.font as tkfont
from dataclasses import replace
from tkinter import ttk
from typing import Any, Callable

from scpi_automation.identity import DeviceCategory
from scpi_automation.planning import (
    GenericPlanItem,
    MeasurementPlanItem,
    SignalGeneratorPlanItem,
    SpectrumPlanItem,
    template_for_instrument,
)
from scpi_automation.routine import (
    PlanBoundDelayStep,
    RoutineStep,
    SelectedFeature,
    SelectedInstrument,
)
from scpi_automation.ui.category_plan_dialog import CategoryPlanDialog
from scpi_automation.ui.plan_detail_dialog import PlanDetailDialog


BACKGROUND = "#F4F6F8"
CARD = "#FFFFFF"
TEXT = "#191F28"
SUBTEXT = "#6B7684"
BORDER = "#E5E8EB"
ACCENT = "#3182F6"
ACCENT_DARK = "#1B64DA"
ACCENT_LIGHT = "#EAF3FF"
NEUTRAL_LIGHT = "#F2F4F6"
WARNING = "#D97706"

FREQUENCY_UNITS = {
    "Hz": 1.0,
    "kHz": 1_000.0,
    "MHz": 1_000_000.0,
    "GHz": 1_000_000_000.0,
}
MAX_TOTAL_PLAN_ITEMS = 2_000


def _button(
    parent: tk.Misc,
    *,
    text: str,
    command: Callable[[], None],
    primary: bool = False,
    compact: bool = False,
) -> tk.Button:
    if primary:
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


class MeasurementPlanTab(tk.Frame):
    """Build analyzer and signal-generator plans without controlling hardware."""

    def __init__(
        self,
        master: tk.Misc,
        on_back: Callable[[], None] | None = None,
        on_continue: Callable[[], None] | None = None,
    ) -> None:
        super().__init__(master, background=BACKGROUND)
        self._on_back = on_back
        self._on_continue = on_continue
        self._instruments: tuple[SelectedInstrument, ...] = ()
        self._spectrum_instruments: tuple[SelectedInstrument, ...] = ()
        self._signal_generator_instruments: tuple[SelectedInstrument, ...] = ()
        self._supported_instruments: tuple[SelectedInstrument, ...] = ()
        self._plan_instruments: tuple[SelectedInstrument, ...] = ()
        self._items: list[MeasurementPlanItem] = []
        self._routine_steps: tuple[RoutineStep, ...] = ()
        self._case_serial = 0
        self._current_case_id = ""
        self._detail_dialog: PlanDetailDialog | None = None
        self._category_dialog: CategoryPlanDialog | None = None
        self._ui_scale = 1.0

        self.device_var = tk.StringVar()
        self.case_name_var = tk.StringVar()
        self.case_repeat_var = tk.StringVar(value="1")
        self.required_plan_var = tk.StringVar(
            value="루틴에서 계획값을 쓰는 기능이 아직 없어요."
        )
        self.device_help_var = tk.StringVar()
        self.center_value_var = tk.StringVar(value="1")
        self.center_unit_var = tk.StringVar(value="GHz")
        self.span_value_var = tk.StringVar(value="100")
        self.span_unit_var = tk.StringVar(value="MHz")
        self.rbw_value_var = tk.StringVar(value="100")
        self.rbw_unit_var = tk.StringVar(value="kHz")
        self.rbw_auto_var = tk.BooleanVar(value=False)
        self.vbw_value_var = tk.StringVar(value="100")
        self.vbw_unit_var = tk.StringVar(value="kHz")
        self.vbw_auto_var = tk.BooleanVar(value=True)
        self.reference_level_var = tk.StringVar(value="0")
        self.generator_frequency_var = tk.StringVar(value="1")
        self.generator_frequency_unit_var = tk.StringVar(value="GHz")
        self.generator_power_var = tk.StringVar(value="-20")
        self.generator_dwell_var = tk.StringVar(value="1")
        self.result_primary_var = tk.StringVar()
        self.result_secondary_var = tk.StringVar()
        self.plan_count_var = tk.StringVar(value="0개")
        self.plan_detail_var = tk.StringVar(
            value="목록에서 계획을 고르면 전체 설정을 여기에서 확인할 수 있어요."
        )
        self.status_var = tk.StringVar(
            value="왼쪽에서 한 번 측정할 조건을 입력해 주세요."
        )

        self._input_widgets: list[tk.Misc] = []
        self._font_metrics: dict[tk.Misc, tuple[tkfont.Font, int]] = {}
        self._widget_metrics: dict[tuple[tk.Misc, str], float] = {}
        self._layout_metrics: dict[
            tuple[tk.Misc, str, str],
            tuple[float, ...],
        ] = {}

        self._build()
        self._start_new_case()
        self._capture_scalable_widgets()
        self.apply_ui_scale(1.0)
        self.set_instruments(())

    @property
    def plan_items(self) -> tuple[MeasurementPlanItem, ...]:
        return tuple(self._items)

    def set_routine_steps(
        self,
        routine_steps: tuple[RoutineStep, ...],
    ) -> None:
        if not isinstance(routine_steps, tuple):
            raise TypeError("set_routine_steps에는 루틴 단계 튜플을 전달해 주세요.")
        self._routine_steps = routine_steps
        required: list[str] = []
        for step in routine_steps:
            if isinstance(step, SelectedFeature):
                required.extend(
                    binding.field_id for binding in step.plan_bindings
                )
            elif isinstance(step, PlanBoundDelayStep):
                required.append(step.field_id)
        unique = tuple(dict.fromkeys(required))
        if unique:
            friendly = {
                "center_frequency_hz": "Center",
                "span_hz": "Span",
                "start_frequency_hz": "Start",
                "stop_frequency_hz": "Stop",
                "rbw_hz": "RBW",
                "vbw_hz": "VBW",
                "reference_level_dbm": "Ref. Level",
                "frequency_hz": "발생기 Frequency",
                "power_dbm": "발생기 Power",
                "dwell_seconds": "Dwell",
            }
            self.required_plan_var.set(
                "루틴이 이 계획값을 사용해요: "
                + ", ".join(friendly.get(value, value) for value in unique)
            )
        else:
            self.required_plan_var.set(
                "현재 루틴은 모두 고정값이에요. 계획은 결과 조건으로만 저장돼요."
            )

    def _start_new_case(self) -> None:
        self._case_serial += 1
        self._current_case_id = f"case-{self._case_serial:04d}"
        self.case_name_var.set(f"시험 {self._case_serial:02d}")
        self.case_repeat_var.set("1")
        if hasattr(self, "status_var"):
            self.status_var.set(
                f"{self.case_name_var.get()}을 만들 준비가 됐어요. "
                "사용할 장비를 하나씩 저장해 주세요."
            )

    def _case_metadata(self) -> tuple[str, str, int]:
        name = self.case_name_var.get().strip()
        if not name:
            raise ValueError("시험 이름을 입력해 주세요.")
        try:
            repeat_count = int(self.case_repeat_var.get().strip())
        except ValueError as exc:
            raise ValueError("반복 횟수는 1~1000 사이 정수로 입력해 주세요.") from exc
        if not 1 <= repeat_count <= 1_000:
            raise ValueError("반복 횟수는 1~1000회 범위여야 해요.")
        return self._current_case_id, name, repeat_count

    def _sync_case_metadata(
        self,
        case_id: str,
        case_name: str,
        repeat_count: int,
    ) -> None:
        self._items = [
            (
                replace(
                    item,
                    case_name=case_name,
                    repeat_count=repeat_count,
                )
                if item.case_id == case_id
                else item
            )
            for item in self._items
        ]

    def _build(self) -> None:
        self.columnconfigure(0, weight=1)
        self.rowconfigure(1, weight=1)

        self.header = tk.Frame(self, background=BACKGROUND)
        self.header.grid(row=0, column=0, sticky="ew", padx=34, pady=(24, 12))
        self.header.columnconfigure(0, weight=1)
        tk.Label(
            self.header,
            text="장비별 측정 계획을 만들어볼게요",
            font=("Segoe UI Semibold", 20),
            background=BACKGROUND,
            foreground=TEXT,
        ).grid(row=0, column=0, sticky="w")
        tk.Label(
            self.header,
            text=(
                "같은 시험 이름에 분석기와 신호발생기 값을 함께 저장하면, "
                "실행 단계에서 루틴이 그 값을 가져다 써요."
            ),
            font=("Segoe UI", 10),
            background=BACKGROUND,
            foreground=SUBTEXT,
        ).grid(row=1, column=0, sticky="w", pady=(5, 0))

        self.header_actions = tk.Frame(self.header, background=BACKGROUND)
        self.header_actions.grid(row=0, column=1, rowspan=2, sticky="e")
        self.back_button = _button(
            self.header_actions,
            text="루틴 설정으로 돌아가기",
            command=self._go_back,
            compact=True,
        )
        self.back_button.grid(row=0, column=0, padx=(0, 6))
        if self._on_back is None:
            self.back_button.grid_remove()
        self.continue_button = _button(
            self.header_actions,
            text="실제 실행 단계로 이동",
            command=self._continue_to_execution,
            primary=True,
            compact=True,
        )
        self.continue_button.grid(row=0, column=1)
        if self._on_continue is None:
            self.continue_button.grid_remove()

        self.workspace = tk.Frame(self, background=BACKGROUND)
        self.workspace.grid(
            row=1,
            column=0,
            sticky="nsew",
            padx=34,
            pady=(0, 12),
        )
        self.workspace.columnconfigure(
            0,
            weight=5,
            minsize=390,
            uniform="plan_side_panels",
        )
        self.workspace.columnconfigure(
            1,
            weight=4,
            minsize=330,
            uniform="plan_side_panels",
        )
        self.workspace.rowconfigure(0, weight=1)

        self._build_settings_panel()
        self._build_plan_panel()

        self.notice = tk.Frame(
            self,
            background=ACCENT_LIGHT,
            highlightbackground="#D6E8FF",
            highlightthickness=1,
        )
        self.notice.grid(row=2, column=0, sticky="ew", padx=34, pady=(0, 16))
        self.notice.columnconfigure(1, weight=1)
        tk.Label(
            self.notice,
            text="계획 단계",
            font=("Segoe UI Semibold", 9),
            background=ACCENT_LIGHT,
            foreground=ACCENT_DARK,
        ).grid(row=0, column=0, padx=(14, 9), pady=9)
        self.status_label = tk.Label(
            self.notice,
            textvariable=self.status_var,
            font=("Segoe UI", 9),
            background=ACCENT_LIGHT,
            foreground="#3B608A",
            anchor="w",
            height=1,
        )
        self.status_label.grid(
            row=0,
            column=1,
            sticky="ew",
            padx=(0, 14),
            pady=9,
        )

    def _build_settings_panel(self) -> None:
        self.settings_panel = tk.Frame(
            self.workspace,
            background=CARD,
            highlightbackground=BORDER,
            highlightthickness=1,
        )
        self.settings_panel.grid(row=0, column=0, sticky="nsew", padx=(0, 7))
        self.settings_panel.columnconfigure(0, weight=1)

        tk.Label(
            self.settings_panel,
            text="1. 시험 설정 입력",
            font=("Segoe UI Semibold", 13),
            background=CARD,
            foreground=TEXT,
        ).grid(row=0, column=0, sticky="w", padx=18, pady=(13, 2))
        tk.Label(
            self.settings_panel,
            text="한 시험에 함께 사용할 장비 값을 같은 시험 이름에 저장해 주세요.",
            font=("Segoe UI", 9),
            background=CARD,
            foreground=SUBTEXT,
        ).grid(row=1, column=0, sticky="w", padx=18, pady=(0, 7))

        case_frame = tk.Frame(
            self.settings_panel,
            background=ACCENT_LIGHT,
            padx=10,
            pady=8,
        )
        case_frame.grid(row=2, column=0, sticky="ew", padx=18, pady=(0, 8))
        case_frame.columnconfigure(1, weight=1)
        tk.Label(
            case_frame,
            text="현재 시험 묶음",
            font=("Segoe UI Semibold", 9),
            background=ACCENT_LIGHT,
            foreground=ACCENT_DARK,
        ).grid(row=0, column=0, sticky="w", padx=(0, 7))
        ttk.Entry(
            case_frame,
            textvariable=self.case_name_var,
        ).grid(row=0, column=1, sticky="ew")
        tk.Label(
            case_frame,
            text="반복",
            font=("Segoe UI", 8),
            background=ACCENT_LIGHT,
            foreground=SUBTEXT,
        ).grid(row=0, column=2, padx=(8, 3))
        ttk.Spinbox(
            case_frame,
            from_=1,
            to=1000,
            textvariable=self.case_repeat_var,
            width=5,
        ).grid(row=0, column=3)
        _button(
            case_frame,
            text="새 시험 만들기",
            command=self._start_new_case,
            compact=True,
        ).grid(row=0, column=4, padx=(7, 0))
        tk.Label(
            case_frame,
            textvariable=self.required_plan_var,
            font=("Segoe UI", 8),
            background=ACCENT_LIGHT,
            foreground="#3B608A",
            anchor="w",
            justify="left",
            wraplength=470,
        ).grid(
            row=1,
            column=0,
            columnspan=5,
            sticky="ew",
            pady=(5, 0),
        )

        device_frame = tk.Frame(self.settings_panel, background=CARD)
        device_frame.grid(row=3, column=0, sticky="ew", padx=18)
        device_frame.columnconfigure(0, weight=1)
        tk.Label(
            device_frame,
            text="사용 장비",
            font=("Segoe UI Semibold", 9),
            background=CARD,
            foreground=TEXT,
        ).grid(row=0, column=0, sticky="w")
        self.device_combo = ttk.Combobox(
            device_frame,
            textvariable=self.device_var,
            state="disabled",
            font=("Segoe UI", 9),
            takefocus=True,
        )
        self.device_combo.grid(row=1, column=0, sticky="ew", pady=(4, 2))
        self.device_combo.bind("<<ComboboxSelected>>", self._on_device_changed)
        self.device_help_label = tk.Label(
            device_frame,
            textvariable=self.device_help_var,
            font=("Segoe UI", 8),
            background=CARD,
            foreground=SUBTEXT,
            anchor="nw",
            justify="left",
            height=2,
        )
        self.device_help_label.grid(row=2, column=0, sticky="ew", pady=(2, 5))

        self.form_host = tk.Frame(self.settings_panel, background=CARD)
        self.form_host.grid(row=4, column=0, sticky="ew", padx=18)
        self.form_host.columnconfigure(0, weight=1)

        form = tk.Frame(self.form_host, background=CARD)
        form.grid(row=0, column=0, sticky="ew")
        form.columnconfigure(1, weight=1)
        self.spectrum_form = form

        (
            self.center_entry,
            self.center_unit_combo,
        ) = self._build_frequency_row(
            form,
            row=0,
            label="Center - 중심 주파수",
            value_var=self.center_value_var,
            unit_var=self.center_unit_var,
        )
        self.span_entry, self.span_unit_combo = self._build_frequency_row(
            form,
            row=1,
            label="Span - 주파수 분석 범위",
            value_var=self.span_value_var,
            unit_var=self.span_unit_var,
        )
        self.rbw_entry, self.rbw_unit_combo = self._build_frequency_row(
            form,
            row=2,
            label="RBW - 분해능 대역폭",
            value_var=self.rbw_value_var,
            unit_var=self.rbw_unit_var,
            auto_var=self.rbw_auto_var,
            auto_command=self._sync_input_states,
        )
        self.vbw_entry, self.vbw_unit_combo = self._build_frequency_row(
            form,
            row=3,
            label="VBW - 비디오 대역폭",
            value_var=self.vbw_value_var,
            unit_var=self.vbw_unit_var,
            auto_var=self.vbw_auto_var,
            auto_command=self._sync_input_states,
        )

        tk.Label(
            form,
            text="Ref. Level - 화면 기준 레벨",
            font=("Segoe UI", 9),
            background=CARD,
            foreground=TEXT,
        ).grid(row=4, column=0, sticky="w", pady=3)
        self.reference_level_entry = tk.Entry(
            form,
            textvariable=self.reference_level_var,
            font=("Segoe UI", 9),
            background="#FBFCFD",
            foreground=TEXT,
            disabledbackground=NEUTRAL_LIGHT,
            disabledforeground="#A6ADB4",
            highlightbackground=BORDER,
            highlightcolor=ACCENT,
            highlightthickness=1,
            relief="flat",
            borderwidth=0,
            justify="right",
        )
        self.reference_level_entry.grid(
            row=4,
            column=1,
            sticky="ew",
            padx=(12, 7),
            pady=3,
            ipady=4,
        )
        tk.Label(
            form,
            text="dBm",
            font=("Segoe UI Semibold", 9),
            background=CARD,
            foreground=SUBTEXT,
            width=6,
        ).grid(row=4, column=2, sticky="ew", pady=3)

        self.signal_generator_form = tk.Frame(self.form_host, background=CARD)
        self.signal_generator_form.grid(row=0, column=0, sticky="ew")
        self.signal_generator_form.columnconfigure(1, weight=1)
        (
            self.generator_frequency_entry,
            self.generator_frequency_unit_combo,
        ) = self._build_frequency_row(
            self.signal_generator_form,
            row=0,
            label="Frequency - 출력 주파수",
            value_var=self.generator_frequency_var,
            unit_var=self.generator_frequency_unit_var,
        )
        tk.Label(
            self.signal_generator_form,
            text="Power - 출력 설정값",
            font=("Segoe UI", 9),
            background=CARD,
            foreground=TEXT,
        ).grid(row=1, column=0, sticky="w", pady=3)
        self.generator_power_entry = tk.Entry(
            self.signal_generator_form,
            textvariable=self.generator_power_var,
            font=("Segoe UI", 9),
            background="#FBFCFD",
            foreground=TEXT,
            disabledbackground=NEUTRAL_LIGHT,
            disabledforeground="#A6ADB4",
            highlightbackground=BORDER,
            highlightcolor=ACCENT,
            highlightthickness=1,
            relief="flat",
            borderwidth=0,
            justify="right",
        )
        self.generator_power_entry.grid(
            row=1,
            column=1,
            sticky="ew",
            padx=(12, 7),
            pady=3,
            ipady=4,
        )
        tk.Label(
            self.signal_generator_form,
            text="dBm",
            font=("Segoe UI Semibold", 9),
            background=CARD,
            foreground=SUBTEXT,
            width=6,
        ).grid(row=1, column=2, sticky="ew", pady=3)
        tk.Label(
            self.signal_generator_form,
            text="Dwell - 주파수 유지 시간",
            font=("Segoe UI", 9),
            background=CARD,
            foreground=TEXT,
        ).grid(row=2, column=0, sticky="w", pady=3)
        self.generator_dwell_entry = tk.Entry(
            self.signal_generator_form,
            textvariable=self.generator_dwell_var,
            font=("Segoe UI", 9),
            background="#FBFCFD",
            foreground=TEXT,
            disabledbackground=NEUTRAL_LIGHT,
            disabledforeground="#A6ADB4",
            highlightbackground=BORDER,
            highlightcolor=ACCENT,
            highlightthickness=1,
            relief="flat",
            borderwidth=0,
            justify="right",
        )
        self.generator_dwell_entry.grid(
            row=2,
            column=1,
            sticky="ew",
            padx=(12, 7),
            pady=3,
            ipady=4,
        )
        tk.Label(
            self.signal_generator_form,
            text="초",
            font=("Segoe UI Semibold", 9),
            background=CARD,
            foreground=SUBTEXT,
            width=6,
        ).grid(row=2, column=2, sticky="ew", pady=3)
        tk.Label(
            self.signal_generator_form,
            text="RF 출력 동작",
            font=("Segoe UI", 9),
            background=CARD,
            foreground=TEXT,
        ).grid(row=3, column=0, sticky="w", pady=3)
        tk.Label(
            self.signal_generator_form,
            text="루틴에서 ON/OFF를 별도 설정해요",
            font=("Segoe UI", 9),
            background=NEUTRAL_LIGHT,
            foreground=SUBTEXT,
            anchor="w",
            padx=9,
            pady=6,
        ).grid(
            row=3,
            column=1,
            columnspan=2,
            sticky="ew",
            padx=(12, 0),
            pady=3,
        )

        result_card = tk.Frame(
            self.settings_panel,
            background="#F8FAFC",
            highlightbackground=BORDER,
            highlightthickness=1,
        )
        result_card.grid(row=4, column=0, sticky="ew", padx=18, pady=(6, 7))
        result_card.grid_remove()
        result_card.columnconfigure(0, weight=1)
        tk.Label(
            result_card,
            text="결과로 남길 값",
            font=("Segoe UI Semibold", 9),
            background="#F8FAFC",
            foreground=TEXT,
        ).grid(row=0, column=0, sticky="w", padx=11, pady=(6, 1))
        tk.Label(
            result_card,
            textvariable=self.result_primary_var,
            font=("Segoe UI", 9),
            background="#F8FAFC",
            foreground=ACCENT_DARK,
        ).grid(row=1, column=0, sticky="w", padx=11)
        tk.Label(
            result_card,
            textvariable=self.result_secondary_var,
            font=("Segoe UI", 8),
            background="#F8FAFC",
            foreground=SUBTEXT,
            anchor="w",
        ).grid(row=2, column=0, sticky="ew", padx=11, pady=(1, 6))

        actions = tk.Frame(self.settings_panel, background=CARD)
        actions.grid(row=5, column=0, sticky="ew", padx=18, pady=(6, 11))
        for column in range(3):
            actions.columnconfigure(
                column,
                weight=1,
                uniform="plan_actions",
            )
        self.detail_button = _button(
            actions,
            text="계획 상세 설정",
            command=self._open_detail_dialog,
        )
        self.detail_button.grid(row=0, column=0, sticky="ew", padx=(0, 3))
        self.add_button = _button(
            actions,
            text="현재 설정 1개 추가  →",
            command=self._add_plan_item,
            primary=True,
        )
        self.add_button.grid(row=0, column=1, sticky="ew", padx=3)
        self.category_detail_button = _button(
            actions,
            text="분류별 시험 계획",
            command=self._open_category_plan_dialog,
        )
        self.category_detail_button.grid(
            row=0,
            column=2,
            sticky="ew",
            padx=(3, 0),
        )

        self._input_widgets.extend(
            [
                self.center_entry,
                self.center_unit_combo,
                self.span_entry,
                self.span_unit_combo,
                self.rbw_entry,
                self.rbw_unit_combo,
                self.vbw_entry,
                self.vbw_unit_combo,
                self.reference_level_entry,
                self.generator_frequency_entry,
                self.generator_frequency_unit_combo,
                self.generator_power_entry,
                self.generator_dwell_entry,
            ]
        )

    def _build_frequency_row(
        self,
        parent: tk.Frame,
        *,
        row: int,
        label: str,
        value_var: tk.StringVar,
        unit_var: tk.StringVar,
        auto_var: tk.BooleanVar | None = None,
        auto_command: Callable[[], None] | None = None,
    ) -> tuple[tk.Entry, ttk.Combobox]:
        tk.Label(
            parent,
            text=label,
            font=("Segoe UI", 9),
            background=CARD,
            foreground=TEXT,
        ).grid(row=row, column=0, sticky="w", pady=3)
        entry = tk.Entry(
            parent,
            textvariable=value_var,
            font=("Segoe UI", 9),
            background="#FBFCFD",
            foreground=TEXT,
            disabledbackground=NEUTRAL_LIGHT,
            disabledforeground="#A6ADB4",
            highlightbackground=BORDER,
            highlightcolor=ACCENT,
            highlightthickness=1,
            relief="flat",
            borderwidth=0,
            justify="right",
        )
        entry.grid(
            row=row,
            column=1,
            sticky="ew",
            padx=(12, 7),
            pady=3,
            ipady=4,
        )
        unit_combo = ttk.Combobox(
            parent,
            textvariable=unit_var,
            values=tuple(FREQUENCY_UNITS),
            state="readonly",
            width=6,
            font=("Segoe UI", 9),
            takefocus=True,
        )
        unit_combo.grid(row=row, column=2, sticky="ew", pady=3)

        if auto_var is not None:
            checkbutton = tk.Checkbutton(
                parent,
                text="자동",
                variable=auto_var,
                command=auto_command,
                font=("Segoe UI", 8),
                background=CARD,
                foreground=SUBTEXT,
                activebackground=CARD,
                activeforeground=TEXT,
                selectcolor=CARD,
                takefocus=True,
            )
            checkbutton.grid(row=row, column=3, sticky="w", padx=(5, 0), pady=3)
            self._input_widgets.append(checkbutton)
        else:
            tk.Label(
                parent,
                text="",
                font=("Segoe UI", 8),
                background=CARD,
                width=5,
            ).grid(row=row, column=3)

        return entry, unit_combo

    def _build_plan_panel(self) -> None:
        self.plan_panel = tk.Frame(
            self.workspace,
            background=CARD,
            highlightbackground=BORDER,
            highlightthickness=1,
        )
        self.plan_panel.grid(row=0, column=1, sticky="nsew", padx=(7, 0))
        self.plan_panel.columnconfigure(0, weight=1)
        self.plan_panel.rowconfigure(3, weight=1)

        heading = tk.Frame(self.plan_panel, background=CARD)
        heading.grid(row=0, column=0, sticky="ew", padx=18, pady=(16, 2))
        heading.columnconfigure(0, weight=1)
        tk.Label(
            heading,
            text="2. 만들어진 계획",
            font=("Segoe UI Semibold", 13),
            background=CARD,
            foreground=TEXT,
        ).grid(row=0, column=0, sticky="w")
        self.plan_count_label = tk.Label(
            heading,
            textvariable=self.plan_count_var,
            font=("Segoe UI Semibold", 9),
            background=ACCENT_LIGHT,
            foreground=ACCENT_DARK,
            width=7,
            padx=7,
            pady=4,
        )
        self.plan_count_label.grid(row=0, column=1, sticky="e")

        tk.Label(
            self.plan_panel,
            text=(
                "같은 [시험] 표시는 한 번에 함께 실행될 장비 설정이에요. "
                "계획 연결 루틴은 이 값을 검증한 뒤 실제 명령에 넣어요."
            ),
            font=("Segoe UI", 9),
            background=CARD,
            foreground=SUBTEXT,
            anchor="w",
            justify="left",
            wraplength=500,
        ).grid(row=1, column=0, sticky="ew", padx=18, pady=(0, 10))

        list_shell = tk.Frame(self.plan_panel, background=CARD)
        list_shell.grid(row=3, column=0, sticky="nsew", padx=18)
        list_shell.columnconfigure(0, weight=1)
        list_shell.rowconfigure(0, weight=1)
        self.plan_list = tk.Listbox(
            list_shell,
            activestyle="none",
            background="#FBFCFD",
            foreground=TEXT,
            selectbackground=ACCENT_LIGHT,
            selectforeground=ACCENT_DARK,
            highlightbackground=BORDER,
            highlightcolor=ACCENT,
            highlightthickness=1,
            relief="flat",
            borderwidth=0,
            font=("Segoe UI", 9),
            exportselection=False,
            takefocus=True,
            height=9,
        )
        self.plan_list.grid(row=0, column=0, sticky="nsew")
        plan_scroll = ttk.Scrollbar(
            list_shell,
            orient="vertical",
            command=self.plan_list.yview,
        )
        plan_scroll.grid(row=0, column=1, sticky="ns")
        self.plan_list.configure(yscrollcommand=plan_scroll.set)
        self.plan_list.bind("<<ListboxSelect>>", self._on_plan_selected)
        self.plan_list.bind("<Delete>", self._on_delete_key)

        self.empty_label = tk.Label(
            list_shell,
            text=(
                "아직 만든 측정 계획이 없어요.\n"
                "왼쪽에 값을 입력하고 ‘계획에 추가’를 눌러보세요."
            ),
            font=("Segoe UI", 9),
            background="#FBFCFD",
            foreground=SUBTEXT,
            justify="center",
        )
        self.empty_label.grid(row=0, column=0, sticky="nsew", padx=1, pady=1)

        detail_card = tk.Frame(
            self.plan_panel,
            background="#F8FAFC",
            highlightbackground=BORDER,
            highlightthickness=1,
        )
        detail_card.grid(row=4, column=0, sticky="ew", padx=18, pady=(9, 8))
        detail_card.columnconfigure(0, weight=1)
        self.plan_detail_label = tk.Label(
            detail_card,
            textvariable=self.plan_detail_var,
            font=("Segoe UI", 8),
            background="#F8FAFC",
            foreground=SUBTEXT,
            anchor="nw",
            justify="left",
            height=3,
        )
        self.plan_detail_label.grid(
            row=0,
            column=0,
            sticky="ew",
            padx=10,
            pady=7,
        )

        controls = tk.Frame(self.plan_panel, background=CARD)
        controls.grid(row=5, column=0, sticky="ew", padx=18, pady=(0, 15))
        for column in range(4):
            controls.columnconfigure(column, weight=1)
        self.move_up_button = _button(
            controls,
            text="위로",
            command=self._move_up,
            compact=True,
        )
        self.move_up_button.grid(row=0, column=0, sticky="ew", padx=(0, 3))
        self.move_down_button = _button(
            controls,
            text="아래로",
            command=self._move_down,
            compact=True,
        )
        self.move_down_button.grid(row=0, column=1, sticky="ew", padx=3)
        self.delete_button = _button(
            controls,
            text="삭제",
            command=self._delete_selected,
            compact=True,
        )
        self.delete_button.grid(row=0, column=2, sticky="ew", padx=3)
        self.clear_button = _button(
            controls,
            text="전체 비우기",
            command=self._clear_items,
            compact=True,
        )
        self.clear_button.grid(row=0, column=3, sticky="ew", padx=(3, 0))

    def set_instruments(
        self,
        instruments: tuple[SelectedInstrument, ...],
    ) -> None:
        if not isinstance(instruments, tuple) or not all(
            isinstance(instrument, SelectedInstrument)
            for instrument in instruments
        ):
            raise TypeError("set_instruments에는 SelectedInstrument 튜플을 전달해 주세요.")

        spectrum_instruments = tuple(
            instrument
            for instrument in instruments
            if instrument.category is DeviceCategory.SPECTRUM_ANALYZER
        )
        signal_generator_instruments = tuple(
            instrument
            for instrument in instruments
            if instrument.category is DeviceCategory.SIGNAL_GENERATOR
        )
        supported_instruments = tuple(
            instrument
            for instrument in instruments
            if instrument.category
            in {
                DeviceCategory.SPECTRUM_ANALYZER,
                DeviceCategory.SIGNAL_GENERATOR,
            }
        )
        plan_instruments: list[SelectedInstrument] = []
        for instrument in instruments:
            try:
                template_for_instrument(instrument)
            except KeyError:
                continue
            plan_instruments.append(instrument)
        normalized_plan_instruments = tuple(plan_instruments)
        previous_selected = self._selected_instrument()
        selection_changed = (
            supported_instruments != self._supported_instruments
            or normalized_plan_instruments != self._plan_instruments
        )
        self._instruments = instruments
        self._spectrum_instruments = spectrum_instruments
        self._signal_generator_instruments = signal_generator_instruments
        self._supported_instruments = supported_instruments
        self._plan_instruments = normalized_plan_instruments

        if selection_changed:
            kept_items = [
                item
                for item in self._items
                if item.instrument in self._plan_instruments
            ]
            removed_count = len(self._items) - len(kept_items)
            self._items = kept_items
            if self._detail_dialog is not None:
                try:
                    if self._detail_dialog.winfo_exists():
                        self._detail_dialog.destroy()
                except tk.TclError:
                    pass
                self._detail_dialog = None
            if self._category_dialog is not None:
                try:
                    if self._category_dialog.winfo_exists():
                        self._category_dialog.destroy()
                except tk.TclError:
                    pass
                self._category_dialog = None
        else:
            removed_count = 0

        options = tuple(
            self._instrument_option(instrument)
            for instrument in self._supported_instruments
        )
        self.device_combo.configure(values=options)
        if options:
            selected_index = 0
            if previous_selected is not None:
                for index, instrument in enumerate(self._supported_instruments):
                    if instrument == previous_selected:
                        selected_index = index
                        break
            self.device_combo.current(selected_index)
            self.status_var.set(
                (
                    f"사용할 수 없어진 장비의 계획 {removed_count}개를 목록에서 뺐어요."
                    if removed_count
                    else "설정을 입력해도 장비에는 전송되지 않아요. 계획에만 추가됩니다."
                )
            )
        else:
            self.device_var.set("")
            self.device_help_var.set(
                "현재 계획서는 스펙트럼 분석기와 신호발생기를 지원해요. "
                + (
                    "선택한 다른 장비는 아래의 분류별 통상 시험 계획을 이용해 주세요."
                    if self._plan_instruments
                    else "장비 찾기 탭에서 사용할 장비를 먼저 선택해 주세요."
                )
            )
            self.status_var.set(
                (
                    "분류별 통상 시험 계획에서 선택 장비의 계획을 만들 수 있어요."
                    if self._plan_instruments
                    else "지원 장비를 먼저 선택해 주세요. 현재 장비에는 명령을 보내지 않아요."
                )
            )
        self._show_selected_editor()
        self._sync_input_states()
        self._render_items()

    @staticmethod
    def _instrument_option(instrument: SelectedInstrument) -> str:
        return f"{instrument.display_name}  ·  {instrument.resource}"

    def _selected_instrument(self) -> SelectedInstrument | None:
        index = self.device_combo.current()
        if 0 <= index < len(self._supported_instruments):
            return self._supported_instruments[index]
        return None

    def _on_device_changed(self, _event: tk.Event[Any] | None = None) -> None:
        self._show_selected_editor()
        self._sync_input_states()
        instrument = self._selected_instrument()
        if instrument is not None:
            self.status_var.set(
                f"{instrument.display_name}용 기본 계획 입력 화면으로 바꿨어요."
            )

    def _show_selected_editor(self) -> None:
        instrument = self._selected_instrument()
        analyzer_count = len(self._spectrum_instruments)
        generator_count = len(self._signal_generator_instruments)
        if instrument is None:
            self.signal_generator_form.grid_remove()
            self.spectrum_form.grid()
            self.result_primary_var.set("장비를 선택하면 필요한 조회 루틴을 알려드려요.")
            self.result_secondary_var.set(
                "계획서 화면에서는 실제 장비 설정이나 출력을 바꾸지 않아요."
            )
            return

        self.device_help_var.set(
            f"스펙트럼 분석기 {analyzer_count}대 · 신호발생기 {generator_count}대 중 "
            f"{instrument.category.label_ko}를 선택했어요."
        )
        if instrument.category is DeviceCategory.SPECTRUM_ANALYZER:
            self.signal_generator_form.grid_remove()
            self.spectrum_form.grid()
            self.result_primary_var.set("계획한 결과 · Marker 주파수와 레벨")
            self.result_secondary_var.set(
                "Center·Span·RBW 같은 설정은 루틴에서 ‘시험 계획에서 "
                "가져오기’로 연결할 수 있어요. 결과 조회 단계는 별도로 넣어 주세요."
            )
        else:
            self.spectrum_form.grid_remove()
            self.signal_generator_form.grid()
            self.result_primary_var.set("계획한 결과 · 설정 주파수와 출력 레벨")
            self.result_secondary_var.set(
                "Frequency·Power·Dwell은 연결된 루틴에서 가져가요. RF ON/OFF와 "
                "Readback은 안전을 위해 루틴에 명시적으로 넣어 주세요."
            )

    def _sync_input_states(self) -> None:
        enabled = bool(self._supported_instruments)
        self.device_combo.configure(state="readonly" if enabled else "disabled")

        for widget in self._input_widgets:
            if isinstance(widget, ttk.Combobox):
                widget.configure(state="readonly" if enabled else "disabled")
            else:
                widget.configure(state="normal" if enabled else "disabled")

        if enabled and self.rbw_auto_var.get():
            self.rbw_entry.configure(state="disabled")
            self.rbw_unit_combo.configure(state="disabled")
        if enabled and self.vbw_auto_var.get():
            self.vbw_entry.configure(state="disabled")
            self.vbw_unit_combo.configure(state="disabled")
        self._set_button_state(self.add_button, enabled)
        self._set_button_state(self.detail_button, enabled)
        self._set_button_state(
            self.category_detail_button,
            bool(self._plan_instruments),
        )

    @staticmethod
    def _parse_number(value: str, field_name: str) -> float:
        normalized = value.strip().replace(",", "")
        if not normalized:
            raise ValueError(f"{field_name} 값을 입력해 주세요.")
        try:
            parsed = float(normalized)
        except ValueError as exc:
            raise ValueError(
                f"{field_name}은(는) 숫자로 입력해 주세요. 예: 1 또는 1000"
            ) from exc
        if not math.isfinite(parsed):
            raise ValueError(f"{field_name}은(는) 유한한 숫자여야 해요.")
        return parsed

    def _frequency_hz(
        self,
        value_var: tk.StringVar,
        unit_var: tk.StringVar,
        field_name: str,
    ) -> float:
        value = self._parse_number(value_var.get(), field_name)
        factor = FREQUENCY_UNITS.get(unit_var.get())
        if factor is None:
            raise ValueError(f"{field_name} 단위를 다시 선택해 주세요.")
        return value * factor

    def _add_plan_item(self) -> None:
        instrument = self._selected_instrument()
        if instrument is None:
            self.status_var.set(
                "지원 장비를 먼저 선택해 주세요. 장비 찾기 탭으로 돌아가면 돼요."
            )
            return
        try:
            case_id, case_name, repeat_count = self._case_metadata()
            if instrument.category is DeviceCategory.SPECTRUM_ANALYZER:
                item: MeasurementPlanItem = SpectrumPlanItem(
                    instrument=instrument,
                    center_frequency_hz=self._frequency_hz(
                        self.center_value_var,
                        self.center_unit_var,
                        "중심 주파수",
                    ),
                    span_hz=self._frequency_hz(
                        self.span_value_var,
                        self.span_unit_var,
                        "Span",
                    ),
                    rbw_hz=(
                        None
                        if self.rbw_auto_var.get()
                        else self._frequency_hz(
                            self.rbw_value_var,
                            self.rbw_unit_var,
                            "RBW",
                        )
                    ),
                    vbw_hz=(
                        None
                        if self.vbw_auto_var.get()
                        else self._frequency_hz(
                            self.vbw_value_var,
                            self.vbw_unit_var,
                            "VBW",
                        )
                    ),
                    reference_level_dbm=self._parse_number(
                        self.reference_level_var.get(),
                        "Ref. Level",
                    ),
                    case_id=case_id,
                    case_name=case_name,
                    repeat_count=repeat_count,
                )
            else:
                item = SignalGeneratorPlanItem(
                    instrument=instrument,
                    frequency_hz=self._frequency_hz(
                        self.generator_frequency_var,
                        self.generator_frequency_unit_var,
                        "출력 주파수",
                    ),
                    power_dbm=self._parse_number(
                        self.generator_power_var.get(),
                        "Power",
                    ),
                    dwell_seconds=self._parse_number(
                        self.generator_dwell_var.get(),
                        "Dwell",
                    ),
                    case_id=case_id,
                    case_name=case_name,
                    repeat_count=repeat_count,
                )
        except ValueError as exc:
            self.status_var.set(str(exc))
            return
        self._sync_case_metadata(case_id, case_name, repeat_count)

        replacement_index = next(
            (
                index
                for index, existing in enumerate(self._items)
                if (
                    existing.case_id == case_id
                    and existing.instrument.resource == instrument.resource
                    and isinstance(
                        existing,
                        (SpectrumPlanItem, SignalGeneratorPlanItem),
                    )
                )
            ),
            None,
        )
        if (
            replacement_index is None
            and len(self._items) >= MAX_TOTAL_PLAN_ITEMS
        ):
            self.status_var.set(
                f"전체 계획은 최대 {MAX_TOTAL_PLAN_ITEMS}개까지 만들 수 있어요."
            )
            return
        if replacement_index is None:
            self._items.append(item)
            selected_index = len(self._items) - 1
            action = "저장했어요"
        else:
            self._items[replacement_index] = item
            selected_index = replacement_index
            action = "새 값으로 바꿨어요"
        self._render_items(selected_index=selected_index)
        self.status_var.set(
            f"{case_name}에 {instrument.display_name} 설정을 {action}. "
            "다른 장비도 같은 시험에 저장할 수 있어요."
        )

    def _open_detail_dialog(self) -> None:
        if not self._supported_instruments:
            self.status_var.set(
                "상세 계획을 만들 장비를 먼저 선택해 주세요."
            )
            return
        if self._detail_dialog is not None:
            try:
                if self._detail_dialog.winfo_exists():
                    self._detail_dialog.deiconify()
                    self._detail_dialog.lift()
                    self._detail_dialog.focus_force()
                    return
            except tk.TclError:
                pass
            self._detail_dialog = None

        self._detail_dialog = PlanDetailDialog(
            self,
            instruments=self._supported_instruments,
            initial_instrument=self._selected_instrument(),
            on_add=self._add_detailed_items,
        )
        self._detail_dialog.bind(
            "<Destroy>",
            self._on_detail_dialog_destroyed,
            add="+",
        )

    def _on_detail_dialog_destroyed(self, event: tk.Event[Any]) -> None:
        if event.widget is self._detail_dialog:
            self._detail_dialog = None

    def _open_category_plan_dialog(self) -> None:
        if not self._plan_instruments:
            self.status_var.set(
                "분류별 계획을 만들 수 있는 장비를 먼저 선택해 주세요."
            )
            return
        if self._category_dialog is not None:
            try:
                if self._category_dialog.winfo_exists():
                    self._category_dialog.deiconify()
                    self._category_dialog.lift()
                    self._category_dialog.focus_force()
                    return
            except tk.TclError:
                pass
            self._category_dialog = None

        initial_instrument = self._selected_instrument()
        if initial_instrument not in self._plan_instruments:
            initial_instrument = self._plan_instruments[0]
        self._category_dialog = CategoryPlanDialog(
            self,
            instruments=self._plan_instruments,
            initial_instrument=initial_instrument,
            on_add=self._add_category_plan_item,
        )
        self._category_dialog.bind(
            "<Destroy>",
            self._on_category_dialog_destroyed,
            add="+",
        )

    def _on_category_dialog_destroyed(self, event: tk.Event[Any]) -> None:
        if event.widget is self._category_dialog:
            self._category_dialog = None

    def _add_category_plan_item(self, item: GenericPlanItem) -> bool:
        if not isinstance(item, GenericPlanItem):
            self.status_var.set("분류별 상세 계획 형식이 올바르지 않아요.")
            return False
        if item.instrument not in self._plan_instruments:
            self.status_var.set(
                "현재 선택되지 않은 장비의 계획이라 추가하지 않았어요."
            )
            return False
        try:
            case_id, case_name, repeat_count = self._case_metadata()
            self._sync_case_metadata(case_id, case_name, repeat_count)
        except ValueError as exc:
            self.status_var.set(str(exc))
            return False
        item = replace(
            item,
            case_id=case_id,
            case_name=case_name,
            repeat_count=repeat_count,
        )
        if len(self._items) >= MAX_TOTAL_PLAN_ITEMS:
            self.status_var.set(
                f"전체 계획은 최대 {MAX_TOTAL_PLAN_ITEMS}개까지 만들 수 있어요."
            )
            return False

        self._items.append(item)
        self._render_items(selected_index=len(self._items) - 1)
        self.status_var.set(
            f"{item.instrument.display_name}의 {item.method_label_ko} 계획을 추가했어요."
        )
        return True

    def _add_detailed_items(
        self,
        items: tuple[MeasurementPlanItem, ...],
    ) -> bool:
        if not items:
            self.status_var.set("추가할 상세 계획이 없어요.")
            return False
        if any(
            item.instrument not in self._supported_instruments
            for item in items
        ):
            self.status_var.set(
                "상세 계획에 현재 선택되지 않은 장비가 있어 추가하지 않았어요."
            )
            return False
        new_total = len(self._items) + len(items)
        if new_total > MAX_TOTAL_PLAN_ITEMS:
            self.status_var.set(
                f"전체 계획은 최대 {MAX_TOTAL_PLAN_ITEMS}개까지 만들 수 있어요. "
                f"현재 {len(self._items)}개라서 {len(items)}개를 모두 추가할 수 없어요."
            )
            return False

        first_new_index = len(self._items)
        normalized_items: list[MeasurementPlanItem] = []
        for item in items:
            self._case_serial += 1
            normalized_items.append(
                replace(
                    item,
                    case_id=f"case-{self._case_serial:04d}",
                    case_name=f"시험 {self._case_serial:02d}",
                    repeat_count=1,
                )
            )
        self._items.extend(normalized_items)
        self._render_items(selected_index=first_new_index)
        instrument = normalized_items[0].instrument
        last_item = normalized_items[-1]
        self._current_case_id = last_item.case_id
        self.case_name_var.set(last_item.case_name)
        self.case_repeat_var.set(str(last_item.repeat_count))
        self.status_var.set(
            f"{instrument.display_name}의 상세 계획 {len(items)}개를 각각의 "
            "시험 케이스로 추가했어요."
        )
        return True

    @staticmethod
    def _format_frequency(value_hz: float) -> str:
        for unit, factor in (
            ("GHz", 1_000_000_000.0),
            ("MHz", 1_000_000.0),
            ("kHz", 1_000.0),
        ):
            if value_hz >= factor:
                value = value_hz / factor
                return f"{value:g} {unit}"
        return f"{value_hz:g} Hz"

    @classmethod
    def _format_bandwidth(cls, value_hz: float | None) -> str:
        return "자동" if value_hz is None else cls._format_frequency(value_hz)

    @staticmethod
    def _device_short_name(instrument: SelectedInstrument) -> str:
        return instrument.model.strip() or instrument.display_name

    def _item_list_text(
        self,
        index: int,
        item: MeasurementPlanItem,
    ) -> str:
        prefix = (
            f"{index + 1:02d}  [{item.case_name or '기존 계획'}]  "
            f"[{self._device_short_name(item.instrument)}]  "
        )
        if isinstance(item, SpectrumPlanItem):
            return (
                f"{prefix}측정 · "
                f"Center {self._format_frequency(item.center_frequency_hz)}  ·  "
                f"Span {self._format_frequency(item.span_hz)}  ·  "
                f"RBW {self._format_bandwidth(item.rbw_hz)}"
            )
        if isinstance(item, GenericPlanItem):
            return (
                f"{prefix}{item.category.label_ko} · "
                f"{item.method_label_ko}"
            )
        return (
            f"{prefix}출력 설정 · "
            f"Frequency {self._format_frequency(item.frequency_hz)}  ·  "
            f"Power {item.power_dbm:g} dBm  ·  "
            f"Dwell {item.dwell_seconds:g} s"
        )

    def _item_detail_text(self, item: MeasurementPlanItem) -> str:
        if isinstance(item, SpectrumPlanItem):
            return (
                f"{item.case_name or '기존 계획'} · 반복 {item.repeat_count}회\n"
                f"{item.instrument.display_name}  |  스펙트럼 측정\n"
                f"Center {self._format_frequency(item.center_frequency_hz)}  ·  "
                f"Span {self._format_frequency(item.span_hz)}  ·  "
                f"RBW {self._format_bandwidth(item.rbw_hz)}  ·  "
                f"VBW {self._format_bandwidth(item.vbw_hz)}  ·  "
                f"Ref. Level {item.reference_level_dbm:g} dBm\n"
                "필요 루틴: Peak Search와 Marker 조회를 추가한 경우에만 결과 기록"
            )
        if isinstance(item, GenericPlanItem):
            return (
                f"{item.case_name or '기존 계획'} · 반복 {item.repeat_count}회\n"
                f"{item.instrument.display_name}  |  "
                f"{item.category.label_ko} · {item.method_label_ko}\n"
                f"표준/절차: {item.value_for('standard_procedure')}  ·  "
                f"시료: {item.value_for('sample_description')}  ·  "
                f"반복: {item.value_for('repeat_count')}회\n"
                f"합격 기준: {item.value_for('acceptance_criteria')}\n"
                "주의: 측정 계획 보조 항목이며 표준 준수·인증 통과를 보증하지 않음"
            )
        return (
            f"{item.case_name or '기존 계획'} · 반복 {item.repeat_count}회\n"
            f"{item.instrument.display_name}  |  신호 출력 설정\n"
            f"Frequency {self._format_frequency(item.frequency_hz)}  ·  "
            f"Power {item.power_dbm:g} dBm  ·  "
            f"Dwell {item.dwell_seconds:g}초\n"
            "필요 루틴: 설정 Readback과 RF ON/OFF 단계를 직접 추가해야 실행·기록"
        )

    def _render_items(self, selected_index: int | None = None) -> None:
        self.plan_list.delete(0, tk.END)
        for index, item in enumerate(self._items):
            self.plan_list.insert(tk.END, self._item_list_text(index, item))
        case_count = len(
            {
                item.case_id or f"legacy-{index}"
                for index, item in enumerate(self._items)
            }
        )
        self.plan_count_var.set(f"{case_count}시험")

        if self._items:
            self.empty_label.grid_remove()
            if selected_index is None:
                selected_index = min(
                    self._selected_plan_index() or 0,
                    len(self._items) - 1,
                )
            self.plan_list.selection_set(selected_index)
            self.plan_list.activate(selected_index)
            self.plan_list.see(selected_index)
            selected_item = self._items[selected_index]
            if selected_item.case_id:
                self._current_case_id = selected_item.case_id
                self.case_name_var.set(selected_item.case_name)
                self.case_repeat_var.set(str(selected_item.repeat_count))
            self.plan_detail_var.set(
                self._item_detail_text(selected_item)
            )
        else:
            self.empty_label.grid()
            self.plan_detail_var.set(
                "목록에서 계획을 고르면 전체 설정을 여기에서 확인할 수 있어요."
            )
        self._update_plan_controls()

    def _selected_plan_index(self) -> int | None:
        selection = self.plan_list.curselection()
        if not selection:
            return None
        index = int(selection[0])
        return index if 0 <= index < len(self._items) else None

    def _on_plan_selected(self, _event: tk.Event[Any] | None = None) -> None:
        index = self._selected_plan_index()
        if index is None:
            self.plan_detail_var.set(
                "목록에서 계획을 고르면 전체 설정을 여기에서 확인할 수 있어요."
            )
        else:
            selected_item = self._items[index]
            if selected_item.case_id:
                self._current_case_id = selected_item.case_id
                self.case_name_var.set(selected_item.case_name)
                self.case_repeat_var.set(str(selected_item.repeat_count))
            self.plan_detail_var.set(self._item_detail_text(self._items[index]))
        self._update_plan_controls()

    def _update_plan_controls(self) -> None:
        index = self._selected_plan_index()
        has_selection = index is not None
        self._set_button_state(
            self.move_up_button,
            has_selection and index is not None and index > 0,
        )
        self._set_button_state(
            self.move_down_button,
            has_selection
            and index is not None
            and index < len(self._items) - 1,
        )
        self._set_button_state(self.delete_button, has_selection)
        self._set_button_state(self.clear_button, bool(self._items))

    @staticmethod
    def _set_button_state(button: tk.Button, enabled: bool) -> None:
        button.configure(
            state="normal" if enabled else "disabled",
            cursor="hand2" if enabled else "arrow",
        )

    def _move_up(self) -> None:
        index = self._selected_plan_index()
        if index is None or index <= 0:
            return
        self._items[index - 1], self._items[index] = (
            self._items[index],
            self._items[index - 1],
        )
        self._render_items(selected_index=index - 1)
        self.status_var.set("선택한 계획을 한 칸 위로 옮겼어요.")

    def _move_down(self) -> None:
        index = self._selected_plan_index()
        if index is None or index >= len(self._items) - 1:
            return
        self._items[index + 1], self._items[index] = (
            self._items[index],
            self._items[index + 1],
        )
        self._render_items(selected_index=index + 1)
        self.status_var.set("선택한 계획을 한 칸 아래로 옮겼어요.")

    def _delete_selected(self) -> None:
        index = self._selected_plan_index()
        if index is None:
            return
        del self._items[index]
        next_index = min(index, len(self._items) - 1) if self._items else None
        self._render_items(selected_index=next_index)
        self.status_var.set("선택한 측정 계획을 삭제했어요.")

    def _on_delete_key(self, _event: tk.Event[Any]) -> str:
        self._delete_selected()
        return "break"

    def _clear_items(self) -> None:
        if not self._items:
            return
        self._items.clear()
        self._render_items()
        self.status_var.set("측정 계획을 모두 비웠어요.")

    def _go_back(self) -> None:
        if self._on_back is not None:
            self._on_back()

    def _continue_to_execution(self) -> None:
        self.status_var.set(
            "루틴과 시험 케이스를 실행 화면에 함께 고정했어요. "
            "‘시험 계획에서 가져오기’ 단계는 Dry Run 전에 실제 값으로 "
            "연결하고 장비 허용 범위를 다시 확인해요."
        )
        if self._on_continue is not None:
            self._on_continue()

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
