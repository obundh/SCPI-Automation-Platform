from __future__ import annotations

import tkinter as tk
import tkinter.font as tkfont
from pathlib import Path
from tkinter import filedialog, messagebox, ttk
from typing import Any, Callable

from scpi_automation.binding_registry import plan_binding_definition
from scpi_automation.routine import (
    DelayStep,
    FeatureRisk,
    PlanBoundDelayStep,
    RoutineFile,
    RoutineStep,
    RoutineFeature,
    RoutineStorageError,
    SelectedFeature,
    SelectedInstrument,
    WaitForCompletionStep,
    create_delay,
    create_plan_bound_delay,
    feature_by_id,
    features_for,
    load_routine,
    load_routine_requirements,
    save_routine,
    select_feature,
    wait_for_completion,
)
from scpi_automation.ui.routine_parameter_dialog import RoutineParameterDialog
from scpi_automation.ui.value_formatting import format_feature_arguments


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
DANGER_LIGHT = "#FDECEC"
NEUTRAL_LIGHT = "#F2F4F6"

_GROUP_LABELS = {
    "acquisition": "Acquisition - 획득",
    "analyzer": "Analyzer - 분석 설정",
    "application": "Application - 측정 앱",
    "arb": "ARB - 임의파형",
    "averaging": "Averaging - 평균",
    "bandwidth": "Bandwidth - 대역폭",
    "bias": "Bias - 바이어스",
    "burst": "Burst - 버스트",
    "channel": "Channel - 채널",
    "correction": "Correction - 보정",
    "display": "Display - 화면",
    "frequency": "Frequency - 주파수",
    "input": "Input - 입력",
    "marker": "Marker - 마커",
    "measurement": "Measurement - 측정",
    "modulation": "Modulation - 변조",
    "output": "Output - 출력",
    "protection": "Protection - 보호",
    "sample": "Sample - 샘플",
    "sequence": "Sequence - 시퀀스",
    "source": "Source - 소스",
    "sweep": "Sweep - 스윕",
    "system": "System - 시스템",
    "timebase": "Timebase - 시간축",
    "trace": "Trace - 트레이스",
    "trigger": "Trigger - 트리거",
    "waveform": "Waveform - 파형",
}


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


class RoutineSetupTab(tk.Frame):
    """Compose conceptual device actions without opening VISA sessions.

    This tab deliberately stores only ``SelectedFeature`` values. It does not
    translate features into SCPI or send anything to an instrument.
    """

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
        self._steps: list[RoutineStep] = []
        self._all_features: tuple[RoutineFeature, ...] = ()
        self._visible_features: tuple[RoutineFeature, ...] = ()
        self._delay_plan_instruments: tuple[SelectedInstrument, ...] = ()
        self._parameter_dialog: RoutineParameterDialog | None = None
        self._ui_scale = 1.0
        self._last_routine_path: Path | None = None

        self.device_var = tk.StringVar()
        self.delay_seconds_var = tk.StringVar(value="1.0")
        self.delay_unit_var = tk.StringVar(value="초")
        self.delay_from_plan_var = tk.BooleanVar(value=False)
        self.delay_plan_device_var = tk.StringVar()
        self.completion_device_var = tk.StringVar()
        self.completion_timeout_var = tk.StringVar(value="30")
        self.completion_timeout_unit_var = tk.StringVar(value="초")
        self.selection_summary_var = tk.StringVar()
        self.selection_detail_var = tk.StringVar()
        self.feature_description_var = tk.StringVar()
        self.feature_risk_var = tk.StringVar()
        self.feature_search_var = tk.StringVar()
        self.feature_group_var = tk.StringVar(value="전체 기능")
        self.routine_count_var = tk.StringVar(value="0단계")
        self.status_var = tk.StringVar(
            value="왼쪽에서 기능을 고르면 나만의 측정 순서를 만들 수 있어요."
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
        self.set_instruments(())

    @property
    def routine_steps(self) -> tuple[RoutineStep, ...]:
        """Return a read-only snapshot of the currently composed routine."""

        return tuple(self._steps)

    def _build(self) -> None:
        self.columnconfigure(0, weight=1)
        self.rowconfigure(2, weight=1)

        self.header = tk.Frame(self, background=BACKGROUND)
        self.header.grid(row=0, column=0, sticky="ew", padx=34, pady=(24, 10))
        self.header.columnconfigure(0, weight=1)
        tk.Label(
            self.header,
            text="장비가 할 일을 순서대로 놓아볼게요",
            font=("Segoe UI Semibold", 20),
            background=BACKGROUND,
            foreground=TEXT,
        ).grid(row=0, column=0, sticky="w")
        tk.Label(
            self.header,
            text=(
                "명령어를 몰라도 괜찮아요. 장비와 기능을 고른 뒤 "
                "실행할 순서만 정하면 돼요. 주파수·출력 같은 시험값은 "
                "다음 ‘계획서’에서 입력해요."
            ),
            font=("Segoe UI", 10),
            background=BACKGROUND,
            foreground=SUBTEXT,
        ).grid(row=1, column=0, sticky="w", pady=(5, 0))

        self.back_button = _button(
            self.header,
            text="장비 다시 고르기",
            command=self._go_back,
            compact=True,
        )
        self.back_button.grid(row=0, column=1, rowspan=2, sticky="e")
        if self._on_back is None:
            self.back_button.grid_remove()

        self.summary_card = tk.Frame(
            self,
            background=CARD,
            highlightbackground=BORDER,
            highlightthickness=1,
        )
        self.summary_card.grid(
            row=1,
            column=0,
            sticky="ew",
            padx=34,
            pady=(0, 12),
        )
        self.summary_card.columnconfigure(1, weight=1)
        self.summary_badge = tk.Label(
            self.summary_card,
            text="선택 장비",
            font=("Segoe UI Semibold", 9),
            background=ACCENT_LIGHT,
            foreground=ACCENT_DARK,
            padx=11,
            pady=6,
        )
        self.summary_badge.grid(row=0, column=0, rowspan=2, padx=16, pady=12)
        tk.Label(
            self.summary_card,
            textvariable=self.selection_summary_var,
            font=("Segoe UI Semibold", 11),
            background=CARD,
            foreground=TEXT,
        ).grid(row=0, column=1, sticky="sw", pady=(11, 1))
        tk.Label(
            self.summary_card,
            textvariable=self.selection_detail_var,
            font=("Segoe UI", 9),
            background=CARD,
            foreground=SUBTEXT,
            anchor="w",
        ).grid(row=1, column=1, sticky="new", pady=(1, 11), padx=(0, 14))

        self.workspace = tk.Frame(self, background=BACKGROUND)
        self.workspace.grid(
            row=2,
            column=0,
            sticky="nsew",
            padx=34,
            pady=(0, 10),
        )
        self.workspace.columnconfigure(
            0,
            weight=4,
            minsize=280,
            uniform="routine_side_panels",
        )
        self.workspace.columnconfigure(1, weight=0, minsize=200)
        self.workspace.columnconfigure(
            2,
            weight=5,
            minsize=310,
            uniform="routine_side_panels",
        )
        self.workspace.rowconfigure(0, weight=1)

        self._build_feature_panel()
        self._build_add_column()
        self._build_routine_panel()

        self.notice = tk.Frame(
            self,
            background=ACCENT_LIGHT,
            highlightbackground="#D6E8FF",
            highlightthickness=1,
        )
        self.notice.grid(row=3, column=0, sticky="ew", padx=34, pady=(0, 16))
        self.notice.columnconfigure(1, weight=1)
        tk.Label(
            self.notice,
            text="안심 안내",
            font=("Segoe UI Semibold", 9),
            background=ACCENT_LIGHT,
            foreground=ACCENT_DARK,
        ).grid(row=0, column=0, padx=(14, 9), pady=9)
        tk.Label(
            self.notice,
            text=(
                "지금은 순서만 만드는 화면이에요. 실제 장비에는 "
                "어떤 명령도 보내지 않아요."
            ),
            font=("Segoe UI", 9),
            background=ACCENT_LIGHT,
            foreground="#3B608A",
            anchor="w",
        ).grid(row=0, column=1, sticky="ew", padx=(0, 14), pady=9)

    def _build_feature_panel(self) -> None:
        self.feature_panel = tk.Frame(
            self.workspace,
            background=CARD,
            highlightbackground=BORDER,
            highlightthickness=1,
        )
        self.feature_panel.grid(row=0, column=0, sticky="nsew")
        self.feature_panel.columnconfigure(0, weight=1)
        self.feature_panel.rowconfigure(4, weight=1)

        tk.Label(
            self.feature_panel,
            text="1. 장비와 기능 고르기",
            font=("Segoe UI Semibold", 13),
            background=CARD,
            foreground=TEXT,
        ).grid(row=0, column=0, sticky="w", padx=18, pady=(16, 2))
        tk.Label(
            self.feature_panel,
            text="어느 장비가 무엇을 할지 먼저 골라주세요.",
            font=("Segoe UI", 9),
            background=CARD,
            foreground=SUBTEXT,
        ).grid(row=1, column=0, sticky="w", padx=18, pady=(0, 10))

        self.device_combo = ttk.Combobox(
            self.feature_panel,
            textvariable=self.device_var,
            state="disabled",
            font=("Segoe UI", 10),
            takefocus=True,
        )
        self.device_combo.grid(row=2, column=0, sticky="ew", padx=18, pady=(0, 12))
        self.device_combo.bind("<<ComboboxSelected>>", self._on_device_changed)

        list_heading = tk.Frame(self.feature_panel, background=CARD)
        list_heading.grid(row=3, column=0, sticky="ew", padx=18, pady=(0, 6))
        list_heading.columnconfigure(0, weight=1)
        tk.Label(
            list_heading,
            text="사용 가능한 기능",
            font=("Segoe UI Semibold", 10),
            background=CARD,
            foreground=TEXT,
        ).grid(row=0, column=0, sticky="w")
        tk.Label(
            list_heading,
            text="더블클릭하면 바로 추가",
            font=("Segoe UI", 8),
            background=CARD,
            foreground=SUBTEXT,
        ).grid(row=0, column=1, sticky="e")
        self.feature_search_entry = ttk.Entry(
            list_heading,
            textvariable=self.feature_search_var,
        )
        self.feature_search_entry.grid(
            row=1,
            column=0,
            sticky="ew",
            pady=(7, 0),
            padx=(0, 6),
        )
        self.feature_search_entry.insert(0, "")
        self.feature_search_entry.bind("<KeyRelease>", self._apply_feature_filter)
        self.feature_group_combo = ttk.Combobox(
            list_heading,
            textvariable=self.feature_group_var,
            values=("전체 기능",),
            state="readonly",
            width=18,
        )
        self.feature_group_combo.grid(
            row=1,
            column=1,
            sticky="e",
            pady=(7, 0),
        )
        self.feature_group_combo.bind(
            "<<ComboboxSelected>>",
            self._apply_feature_filter,
        )
        tk.Label(
            list_heading,
            text="검색 예: 주파수 · Marker · 출력 · Trace",
            font=("Segoe UI", 8),
            background=CARD,
            foreground=SUBTEXT,
            anchor="w",
        ).grid(
            row=2,
            column=0,
            columnspan=2,
            sticky="w",
            pady=(4, 0),
        )

        feature_list_shell = tk.Frame(self.feature_panel, background=CARD)
        feature_list_shell.grid(
            row=4,
            column=0,
            sticky="nsew",
            padx=18,
            pady=(0, 10),
        )
        feature_list_shell.columnconfigure(0, weight=1)
        feature_list_shell.rowconfigure(0, weight=1)
        self.feature_list = tk.Listbox(
            feature_list_shell,
            activestyle="none",
            background="#FBFCFD",
            foreground=TEXT,
            selectbackground=ACCENT_LIGHT,
            selectforeground=ACCENT_DARK,
            disabledforeground="#A6ADB4",
            highlightbackground=BORDER,
            highlightcolor=ACCENT,
            highlightthickness=1,
            relief="flat",
            borderwidth=0,
            font=("Segoe UI", 10),
            exportselection=False,
            takefocus=True,
            height=7,
        )
        self.feature_list.grid(row=0, column=0, sticky="nsew")
        feature_scroll = ttk.Scrollbar(
            feature_list_shell,
            orient="vertical",
            command=self.feature_list.yview,
        )
        feature_scroll.grid(row=0, column=1, sticky="ns")
        self.feature_list.configure(yscrollcommand=feature_scroll.set)
        self.feature_list.bind("<<ListboxSelect>>", self._on_feature_selected)
        self.feature_list.bind("<Double-Button-1>", self._on_feature_double_click)
        self.feature_list.bind("<Return>", self._on_feature_return)

        self.feature_detail = tk.Frame(
            self.feature_panel,
            background=NEUTRAL_LIGHT,
            width=250,
            height=81,
        )
        self.feature_detail.grid(
            row=5,
            column=0,
            sticky="ew",
            padx=18,
            pady=(0, 16),
        )
        self.feature_detail.columnconfigure(0, weight=1)
        self.feature_detail.grid_propagate(False)
        self.feature_risk_badge = tk.Label(
            self.feature_detail,
            textvariable=self.feature_risk_var,
            font=("Segoe UI Semibold", 8),
            background=NEUTRAL_LIGHT,
            foreground=SUBTEXT,
            padx=8,
            pady=4,
            width=24,
            anchor="w",
        )
        self.feature_risk_badge.grid(
            row=0,
            column=0,
            sticky="w",
            padx=10,
            pady=(8, 3),
        )
        self.feature_description_label = tk.Label(
            self.feature_detail,
            textvariable=self.feature_description_var,
            font=("Segoe UI", 9),
            background=NEUTRAL_LIGHT,
            foreground=SUBTEXT,
            justify="left",
            anchor="nw",
            wraplength=300,
            height=2,
        )
        self.feature_description_label.grid(
            row=1,
            column=0,
            sticky="ew",
            padx=10,
            pady=(0, 9),
        )

    def _build_add_column(self) -> None:
        self.add_column = tk.Frame(self.workspace, background=BACKGROUND)
        self.add_column.grid(row=0, column=1, sticky="nsew", padx=9)
        self.add_column.columnconfigure(0, weight=1)
        tk.Label(
            self.add_column,
            text="선택한 장비 기능",
            font=("Segoe UI", 8),
            background=BACKGROUND,
            foreground=SUBTEXT,
        ).grid(row=0, column=0, pady=(4, 5))
        self.add_button = _button(
            self.add_column,
            text="추가  →",
            command=self._add_selected_feature,
            primary=True,
            compact=True,
        )
        self.add_button.grid(row=1, column=0, sticky="ew")

        self.common_panel = tk.Frame(
            self.add_column,
            background=CARD,
            highlightbackground=BORDER,
            highlightthickness=1,
            padx=11,
            pady=8,
        )
        self.common_panel.grid(row=2, column=0, sticky="nsew", pady=(12, 0))
        self.common_panel.columnconfigure(0, weight=1)

        tk.Label(
            self.common_panel,
            text="공통 단계",
            font=("Segoe UI Semibold", 11),
            background=CARD,
            foreground=TEXT,
        ).grid(row=0, column=0, sticky="w")
        tk.Label(
            self.common_panel,
            text="장비 기능 사이에 넣을 수 있어요.",
            font=("Segoe UI", 8),
            background=CARD,
            foreground=SUBTEXT,
        ).grid(row=1, column=0, sticky="w", pady=(2, 6))

        tk.Label(
            self.common_panel,
            text="Delay - 대기 시간",
            font=("Segoe UI Semibold", 9),
            background=CARD,
            foreground=TEXT,
        ).grid(row=2, column=0, sticky="w")
        delay_row = tk.Frame(self.common_panel, background=CARD)
        delay_row.grid(row=3, column=0, sticky="ew", pady=(4, 4))
        delay_row.columnconfigure(0, weight=1)
        self.delay_seconds_spin = ttk.Spinbox(
            delay_row,
            from_=0.1,
            to=3600,
            increment=0.1,
            textvariable=self.delay_seconds_var,
            width=7,
        )
        self.delay_seconds_spin.grid(row=0, column=0, sticky="ew")
        self.delay_unit_combo = ttk.Combobox(
            delay_row,
            textvariable=self.delay_unit_var,
            values=("밀리초", "초", "분"),
            state="readonly",
            width=7,
            font=("Segoe UI", 9),
        )
        self.delay_unit_combo.grid(row=0, column=1, padx=(5, 0))
        tk.Checkbutton(
            delay_row,
            text="계획 Dwell",
            variable=self.delay_from_plan_var,
            command=self._sync_delay_source_state,
            font=("Segoe UI", 8),
            background=CARD,
            foreground=ACCENT_DARK,
            activebackground=CARD,
            selectcolor=CARD,
            takefocus=True,
        ).grid(row=1, column=0, sticky="w", pady=(4, 0))
        self.delay_plan_device_combo = ttk.Combobox(
            delay_row,
            textvariable=self.delay_plan_device_var,
            state="disabled",
            font=("Segoe UI", 8),
            takefocus=True,
            width=9,
        )
        self.delay_plan_device_combo.grid(
            row=1,
            column=1,
            sticky="ew",
            padx=(5, 0),
            pady=(4, 0),
        )
        self.add_delay_button = _button(
            self.common_panel,
            text="대기 단계 추가",
            command=self._add_delay_step,
            compact=True,
        )
        self.add_delay_button.grid(row=4, column=0, sticky="ew")

        tk.Frame(self.common_panel, background=BORDER, height=1).grid(
            row=5,
            column=0,
            sticky="ew",
            pady=6,
        )
        tk.Label(
            self.common_panel,
            text="Wait for Completion",
            font=("Segoe UI Semibold", 9),
            background=CARD,
            foreground=TEXT,
        ).grid(row=6, column=0, sticky="w")
        tk.Label(
            self.common_panel,
            text="선택 장비의 앞 작업 완료 확인",
            font=("Segoe UI", 8),
            background=CARD,
            foreground=SUBTEXT,
        ).grid(row=7, column=0, sticky="w", pady=(1, 4))
        self.completion_device_combo = ttk.Combobox(
            self.common_panel,
            textvariable=self.completion_device_var,
            state="disabled",
            font=("Segoe UI", 9),
            takefocus=True,
        )
        self.completion_device_combo.grid(row=8, column=0, sticky="ew")
        timeout_row = tk.Frame(self.common_panel, background=CARD)
        timeout_row.grid(row=9, column=0, sticky="ew", pady=(4, 4))
        timeout_row.columnconfigure(1, weight=1)
        tk.Label(
            timeout_row,
            text="제한",
            font=("Segoe UI", 8),
            background=CARD,
            foreground=SUBTEXT,
        ).grid(row=0, column=0, padx=(0, 5))
        self.completion_timeout_spin = ttk.Spinbox(
            timeout_row,
            from_=0.1,
            to=3600,
            increment=1,
            textvariable=self.completion_timeout_var,
            width=6,
        )
        self.completion_timeout_spin.grid(row=0, column=1, sticky="ew")
        self.completion_timeout_unit_combo = ttk.Combobox(
            timeout_row,
            textvariable=self.completion_timeout_unit_var,
            values=("초", "분"),
            state="readonly",
            width=5,
            font=("Segoe UI", 8),
        )
        self.completion_timeout_unit_combo.grid(
            row=0,
            column=2,
            padx=(5, 0),
        )
        self.add_completion_button = _button(
            self.common_panel,
            text="완료 확인 추가",
            command=self._add_completion_wait_step,
            compact=True,
        )
        self.add_completion_button.grid(row=10, column=0, sticky="ew")

    def _build_routine_panel(self) -> None:
        self.routine_panel = tk.Frame(
            self.workspace,
            background=CARD,
            highlightbackground=BORDER,
            highlightthickness=1,
        )
        self.routine_panel.grid(row=0, column=2, sticky="nsew")
        self.routine_panel.columnconfigure(0, weight=1)
        self.routine_panel.rowconfigure(3, weight=1)

        routine_heading = tk.Frame(self.routine_panel, background=CARD)
        routine_heading.grid(row=0, column=0, sticky="ew", padx=18, pady=(16, 2))
        routine_heading.columnconfigure(0, weight=1)
        tk.Label(
            routine_heading,
            text="2. 내 루틴 만들기",
            font=("Segoe UI Semibold", 13),
            background=CARD,
            foreground=TEXT,
        ).grid(row=0, column=0, sticky="w")
        self.routine_count_badge = tk.Label(
            routine_heading,
            textvariable=self.routine_count_var,
            font=("Segoe UI Semibold", 9),
            background=ACCENT_LIGHT,
            foreground=ACCENT_DARK,
            padx=9,
            pady=4,
        )
        self.routine_count_badge.grid(row=0, column=1, sticky="e")
        tk.Label(
            self.routine_panel,
            text="위에서 아래 순서로 실행될 예정이에요.",
            font=("Segoe UI", 9),
            background=CARD,
            foreground=SUBTEXT,
        ).grid(row=1, column=0, sticky="w", padx=18, pady=(0, 10))

        routine_list_shell = tk.Frame(self.routine_panel, background=CARD)
        routine_list_shell.grid(
            row=3,
            column=0,
            sticky="nsew",
            padx=18,
            pady=(0, 10),
        )
        routine_list_shell.columnconfigure(0, weight=1)
        routine_list_shell.rowconfigure(0, weight=1)
        self.routine_list = tk.Listbox(
            routine_list_shell,
            activestyle="none",
            background="#FBFCFD",
            foreground=TEXT,
            selectbackground=ACCENT,
            selectforeground="#FFFFFF",
            highlightbackground=BORDER,
            highlightcolor=ACCENT,
            highlightthickness=1,
            relief="flat",
            borderwidth=0,
            font=("Segoe UI", 10),
            exportselection=False,
            takefocus=True,
            height=9,
        )
        self.routine_list.grid(row=0, column=0, sticky="nsew")
        routine_scroll = ttk.Scrollbar(
            routine_list_shell,
            orient="vertical",
            command=self.routine_list.yview,
        )
        routine_scroll.grid(row=0, column=1, sticky="ns")
        self.routine_list.configure(yscrollcommand=routine_scroll.set)
        self.routine_list.bind("<<ListboxSelect>>", self._on_routine_selected)
        self.routine_list.bind("<Delete>", self._on_delete_key)
        self.routine_list.bind("<Control-d>", self._on_duplicate_key)
        self.routine_list.bind("<Button-3>", self._show_routine_context_menu)

        self.routine_empty_label = tk.Label(
            routine_list_shell,
            text="아직 추가한 단계가 없어요.\n왼쪽에서 기능을 골라 추가해 보세요.",
            font=("Segoe UI", 10),
            background="#FBFCFD",
            foreground=SUBTEXT,
            justify="center",
        )

        file_actions = tk.Frame(self.routine_panel, background=CARD)
        file_actions.grid(row=2, column=0, sticky="ew", padx=18, pady=(0, 8))
        file_actions.columnconfigure(0, weight=1, uniform="routine_file_action")
        file_actions.columnconfigure(1, weight=1, uniform="routine_file_action")
        self.load_button = _button(
            file_actions,
            text="루틴 불러오기",
            command=self._load_routine_file,
            compact=True,
        )
        self.load_button.grid(row=0, column=0, sticky="ew", padx=(0, 4))
        self.save_button = _button(
            file_actions,
            text="루틴 저장",
            command=self._save_routine_file,
            compact=True,
        )
        self.save_button.grid(row=0, column=1, sticky="ew", padx=(4, 0))
        self.save_continue_button = _button(
            file_actions,
            text="루틴 저장 및 다음 단계  →",
            command=self._save_and_continue,
            primary=True,
            compact=True,
        )
        self.save_continue_button.grid(
            row=1,
            column=0,
            columnspan=2,
            sticky="ew",
            pady=(7, 0),
        )

        controls = tk.Frame(self.routine_panel, background=CARD)
        controls.grid(row=4, column=0, sticky="ew", padx=18, pady=(0, 10))
        controls.columnconfigure(4, weight=1)
        self.move_up_button = _button(
            controls,
            text="위로",
            command=self._move_up,
            compact=True,
        )
        self.move_up_button.grid(row=0, column=0, padx=(0, 5))
        self.move_down_button = _button(
            controls,
            text="아래로",
            command=self._move_down,
            compact=True,
        )
        self.move_down_button.grid(row=0, column=1, padx=(0, 5))
        self.delete_button = _button(
            controls,
            text="삭제",
            command=self._delete_selected,
            compact=True,
        )
        self.delete_button.grid(row=0, column=2)
        self.clear_button = _button(
            controls,
            text="전체 비우기",
            command=self._clear_routine,
            compact=True,
        )
        self.clear_button.grid(row=0, column=5, sticky="e")

        tk.Label(
            self.routine_panel,
            textvariable=self.status_var,
            font=("Segoe UI", 8),
            background=CARD,
            foreground=SUBTEXT,
            anchor="w",
        ).grid(row=5, column=0, sticky="ew", padx=18, pady=(0, 14))

        self.routine_context_menu = tk.Menu(
            self.routine_list,
            tearoff=False,
            font=("Segoe UI", 9),
        )
        self.routine_context_menu.add_command(
            label="단계 복제",
            command=self._duplicate_selected,
            accelerator="Ctrl+D",
        )
        self.routine_context_menu.add_separator()
        self.routine_context_menu.add_command(
            label="한 단계 위로",
            command=self._move_up,
        )
        self.routine_context_menu.add_command(
            label="한 단계 아래로",
            command=self._move_down,
        )
        self.routine_context_menu.add_command(
            label="맨 위로 보내기",
            command=self._move_to_top,
        )
        self.routine_context_menu.add_command(
            label="맨 아래로 보내기",
            command=self._move_to_bottom,
        )
        self.routine_context_menu.add_separator()
        self.routine_context_menu.add_command(
            label="단계 삭제",
            command=self._delete_selected,
            accelerator="Delete",
        )

    def set_instruments(
        self,
        instruments: tuple[SelectedInstrument, ...],
    ) -> None:
        """Set the devices available for routine composition.

        Reapplying the same tuple keeps the draft. When selection changes, keep
        PC-only steps and steps for devices that are still selected; remove only
        stale or no-longer-validated device steps.
        """

        normalized = self._normalize_instruments(instruments)
        selection_changed = normalized != self._instruments
        self._instruments = normalized
        removed_step_count = 0
        preserved_step_count = len(self._steps)
        if selection_changed:
            previous_steps = tuple(self._steps)
            current_by_resource = {
                instrument.resource: instrument
                for instrument in self._instruments
            }
            reconciled_steps: list[RoutineStep] = []
            for step in previous_steps:
                if isinstance(step, DelayStep):
                    reconciled_steps.append(step)
                    continue
                if isinstance(step, PlanBoundDelayStep):
                    current = current_by_resource.get(step.instrument.resource)
                    if current is not None:
                        try:
                            reconciled_steps.append(
                                create_plan_bound_delay(current)
                            )
                        except (TypeError, ValueError):
                            pass
                    continue
                current = current_by_resource.get(step.instrument.resource)
                if current is None:
                    continue
                if isinstance(step, SelectedFeature):
                    try:
                        reconciled_steps.append(
                            select_feature(
                                current,
                                step.feature_id,
                                arguments=step.arguments,
                                plan_bindings=step.plan_bindings,
                                result_name=step.result_name,
                            )
                        )
                    except (KeyError, TypeError, ValueError):
                        continue
                else:
                    reconciled_steps.append(
                        wait_for_completion(
                            current,
                            step.timeout_seconds,
                        )
                    )
            self._steps[:] = reconciled_steps
            preserved_step_count = len(reconciled_steps)
            removed_step_count = len(previous_steps) - preserved_step_count

        labels = tuple(
            self._instrument_option(index, instrument)
            for index, instrument in enumerate(self._instruments)
        )
        self.device_combo.configure(values=labels)
        completion_labels = tuple(
            self._routine_device_name(instrument)
            for instrument in self._instruments
        )
        self.completion_device_combo.configure(values=completion_labels)
        generator_instruments = tuple(
            instrument
            for instrument in self._instruments
            if instrument.category.value == "signal_generator"
        )
        self._delay_plan_instruments = generator_instruments
        self.delay_plan_device_combo.configure(
            values=tuple(
                self._routine_device_name(instrument)
                for instrument in generator_instruments
            )
        )

        if not self._instruments:
            self.selection_summary_var.set("아직 선택한 장비가 없어요")
            self.selection_detail_var.set(
                "장비 찾기 화면에서 사용할 장비의 체크박스를 선택해 주세요."
            )
            self.summary_badge.configure(
                text="선택 필요",
                background=WARNING_LIGHT,
                foreground=WARNING,
            )
            self.device_var.set("먼저 장비를 선택해 주세요")
            self.device_combo.configure(state="disabled")
            self.completion_device_var.set("장비 선택 필요")
            self.completion_device_combo.configure(state="disabled")
            self.delay_seconds_spin.configure(state="disabled")
            self.completion_timeout_spin.configure(state="disabled")
            self._set_button_state(self.add_delay_button, False)
            self._set_button_state(self.add_completion_button, False)
            self.delay_from_plan_var.set(False)
            self._visible_features = ()
            self._all_features = ()
            self._render_features()
            self.status_var.set("장비를 선택하면 여기서 루틴을 만들 수 있어요.")
        else:
            self.selection_summary_var.set(
                f"루틴에 사용할 장비 {len(self._instruments)}대를 골랐어요"
            )
            self.selection_detail_var.set(self._instrument_summary())
            self.summary_badge.configure(
                text=f"{len(self._instruments)}대 선택",
                background=SUCCESS_LIGHT,
                foreground=SUCCESS,
            )
            self.device_combo.configure(state="readonly")
            self.device_combo.current(0)
            self.completion_device_combo.configure(state="readonly")
            self.completion_device_combo.current(0)
            self.delay_seconds_spin.configure(state="normal")
            self.completion_timeout_spin.configure(state="normal")
            self._set_button_state(self.add_delay_button, True)
            self._set_button_state(self.add_completion_button, True)
            self._refresh_features()
            if selection_changed:
                if removed_step_count:
                    self.status_var.set(
                        f"계속 사용할 수 있는 단계 {preserved_step_count}개는 "
                        f"남기고, 빠진 장비 단계 {removed_step_count}개만 정리했어요."
                    )
                else:
                    self.status_var.set(
                        "기능을 고른 뒤 ‘추가’를 누르면 루틴에 차례대로 들어가요."
                    )
        if generator_instruments:
            self.delay_plan_device_combo.current(0)
        else:
            self.delay_from_plan_var.set(False)
            self.delay_plan_device_var.set("")
        self._sync_delay_source_state()

        self._render_routine()

    @staticmethod
    def _normalize_instruments(
        instruments: tuple[SelectedInstrument, ...],
    ) -> tuple[SelectedInstrument, ...]:
        if not isinstance(instruments, tuple):
            raise TypeError("set_instruments에는 SelectedInstrument 튜플을 전달해 주세요.")
        result: list[SelectedInstrument] = []
        seen_resources: set[str] = set()
        for instrument in instruments:
            if not isinstance(instrument, SelectedInstrument):
                raise TypeError("선택 장비에는 SelectedInstrument만 사용할 수 있어요.")
            if instrument.resource in seen_resources:
                continue
            result.append(instrument)
            seen_resources.add(instrument.resource)
        return tuple(result)

    def _instrument_option(
        self,
        index: int,
        instrument: SelectedInstrument,
    ) -> str:
        return (
            f"{index + 1}. {instrument.display_name}  ·  "
            f"{instrument.category.label_ko}"
        )

    def _instrument_summary(self) -> str:
        names = [instrument.display_name for instrument in self._instruments]
        if len(names) <= 4:
            return " · ".join(names)
        return " · ".join(names[:4]) + f" · 외 {len(names) - 4}대"

    def _on_device_changed(self, _event: tk.Event[Any] | None = None) -> None:
        index = self.device_combo.current()
        if 0 <= index < len(self._instruments):
            self.completion_device_combo.current(index)
        self._refresh_features()

    def _selected_instrument(self) -> SelectedInstrument | None:
        index = self.device_combo.current()
        if 0 <= index < len(self._instruments):
            return self._instruments[index]
        return None

    def _refresh_features(self) -> None:
        instrument = self._selected_instrument()
        self._all_features = (
            features_for(
                instrument.category,
                instrument.profile_id,
                instrument.compatible_capability_ids,
                instrument.compatibility_status,
                instrument.compatible_operation_ids,
            )
            if instrument is not None
            else ()
        )
        groups = sorted(
            {feature.group for feature in self._all_features if feature.group}
        )
        group_values = ("전체 기능",) + tuple(
            _GROUP_LABELS.get(group, group.title())
            for group in groups
        )
        self.feature_group_combo.configure(values=group_values)
        self.feature_group_var.set("전체 기능")
        self.feature_search_var.set("")
        self._apply_feature_filter()

    def _apply_feature_filter(
        self,
        _event: tk.Event[Any] | None = None,
    ) -> None:
        search = self.feature_search_var.get().strip().casefold()
        selected_group_label = self.feature_group_var.get()
        selected_group = ""
        for group, label in _GROUP_LABELS.items():
            if label == selected_group_label:
                selected_group = group
                break
        self._visible_features = tuple(
            feature
            for feature in self._all_features
            if (
                not selected_group
                or feature.group == selected_group
            )
            and (
                not search
                or search in feature.display_name.casefold()
                or search in feature.description.casefold()
                or search in feature.capability_id.casefold()
            )
        )
        self._render_features()

    def _render_features(self) -> None:
        self.feature_list.configure(state="normal")
        self.feature_list.delete(0, tk.END)
        for feature in self._visible_features:
            suffix = "  ·  출력 주의" if feature.is_dangerous else ""
            group = (
                _GROUP_LABELS.get(feature.group, feature.group.title())
                if feature.group
                else ""
            )
            prefix = f"[{group.split(' - ', 1)[0]}]  " if group else ""
            self.feature_list.insert(
                tk.END,
                f"  {prefix}{feature.display_name}{suffix}",
            )

        if self._visible_features:
            self.feature_list.configure(state="normal")
            self.feature_description_var.set(
                "목록에서 기능을 선택하면 어떤 동작인지 설명해 드려요."
            )
        else:
            self.feature_list.configure(state="disabled")
            self.feature_description_var.set(
                "선택한 장비가 생기면 사용 가능한 기능이 여기에 나타나요."
            )
        self.feature_risk_var.set("기능을 선택해 주세요")
        self.feature_risk_badge.configure(
            background=NEUTRAL_LIGHT,
            foreground=SUBTEXT,
        )
        self.add_button.configure(state="disabled", cursor="arrow")

    def _on_feature_selected(self, _event: tk.Event[Any] | None = None) -> None:
        feature = self._selected_feature()
        if feature is None:
            self.add_button.configure(state="disabled", cursor="arrow")
            return
        self.feature_description_var.set(feature.description)
        if feature.parameters:
            parameter_names = ", ".join(
                parameter.name for parameter in feature.parameters
            )
            self.feature_description_var.set(
                f"{feature.description}\n입력값: {parameter_names}"
            )
        self.feature_risk_var.set(
            f"{feature.risk.label_ko} · {feature.verification.label_ko}"
        )
        background, foreground = self._risk_colors(feature.risk)
        self.feature_risk_badge.configure(
            background=background,
            foreground=foreground,
        )
        self.add_button.configure(state="normal", cursor="hand2")

    def _selected_feature(self) -> RoutineFeature | None:
        selection = self.feature_list.curselection()
        if not selection:
            return None
        index = int(selection[0])
        if 0 <= index < len(self._visible_features):
            return self._visible_features[index]
        return None

    @staticmethod
    def _risk_colors(risk: FeatureRisk) -> tuple[str, str]:
        return {
            FeatureRisk.SAFE: (SUCCESS_LIGHT, SUCCESS),
            FeatureRisk.CAUTION: (WARNING_LIGHT, WARNING),
            FeatureRisk.HAZARDOUS: (DANGER_LIGHT, DANGER),
        }[risk]

    def _on_feature_double_click(self, _event: tk.Event[Any]) -> str:
        self._add_selected_feature()
        return "break"

    def _on_feature_return(self, _event: tk.Event[Any]) -> str:
        self._add_selected_feature()
        return "break"

    def _add_selected_feature(self) -> None:
        instrument = self._selected_instrument()
        feature = self._selected_feature()
        if instrument is None or feature is None:
            return

        if feature.capability_id:
            if self._parameter_dialog is not None:
                try:
                    if self._parameter_dialog.winfo_exists():
                        self._parameter_dialog.destroy()
                except tk.TclError:
                    pass
            self._parameter_dialog = RoutineParameterDialog(
                self,
                instrument=instrument,
                feature=feature,
                on_add=self._append_selected_feature,
            )
            return

        selected = select_feature(instrument, feature.feature_id)
        self._append_selected_feature(selected)

    def _append_selected_feature(self, selected: SelectedFeature) -> None:
        self._steps.append(selected)
        instrument = selected.instrument
        feature = feature_by_id(
            selected.feature_id,
            selected.instrument.profile_id,
        )
        instrument_index = self._instruments.index(instrument)
        self.completion_device_combo.current(instrument_index)
        index = len(self._steps) - 1
        self._render_routine(selected_index=index)
        self.status_var.set(
            f"{feature.display_name} 기능을 {len(self._steps)}번째 단계에 추가했어요."
        )

    @staticmethod
    def _seconds_from_var(
        value: str,
        label: str,
        unit: str = "초",
    ) -> float:
        try:
            number = float(value.strip())
        except (AttributeError, TypeError, ValueError) as exc:
            raise ValueError(f"{label}을 숫자로 입력해 주세요.") from exc
        factors = {
            "밀리초": 0.001,
            "초": 1.0,
            "분": 60.0,
        }
        try:
            return number * factors[unit]
        except KeyError as exc:
            raise ValueError(f"{label} 단위를 다시 선택해 주세요.") from exc

    @staticmethod
    def _format_seconds(seconds: float) -> str:
        if seconds < 1:
            return f"{seconds * 1_000:g}밀리초"
        if seconds >= 60 and seconds % 60 == 0:
            return f"{seconds / 60:g}분"
        return f"{seconds:g}초"

    def _add_delay_step(self) -> None:
        if not self._instruments:
            self.status_var.set("먼저 루틴에 사용할 장비를 선택해 주세요.")
            return
        if self.delay_from_plan_var.get():
            index = self.delay_plan_device_combo.current()
            if not 0 <= index < len(self._delay_plan_instruments):
                self.status_var.set(
                    "Dwell 값을 가져올 신호발생기를 먼저 선택해 주세요."
                )
                return
            step = create_plan_bound_delay(
                self._delay_plan_instruments[index]
            )
            self._steps.append(step)
            self._render_routine(selected_index=len(self._steps) - 1)
            self.status_var.set(
                "시험 계획의 Dwell 값을 사용하는 대기 단계를 추가했어요."
            )
            return
        try:
            seconds = self._seconds_from_var(
                self.delay_seconds_var.get(),
                "대기 시간",
                self.delay_unit_var.get(),
            )
            step = create_delay(seconds)
        except ValueError as exc:
            self.status_var.set(str(exc))
            self.delay_seconds_spin.focus_set()
            return

        self._steps.append(step)
        self._render_routine(selected_index=len(self._steps) - 1)
        self.status_var.set(
            f"Delay - {self._format_seconds(step.seconds)} 대기를 추가했어요."
        )

    def _sync_delay_source_state(self) -> None:
        use_plan = (
            bool(self._delay_plan_instruments)
            and self.delay_from_plan_var.get()
        )
        has_devices = bool(self._instruments)
        self.delay_seconds_spin.configure(
            state="disabled" if use_plan or not has_devices else "normal"
        )
        self.delay_unit_combo.configure(
            state="disabled" if use_plan or not has_devices else "readonly"
        )
        self.delay_plan_device_combo.configure(
            state="readonly" if use_plan else "disabled"
        )

    def _completion_instrument(self) -> SelectedInstrument | None:
        index = self.completion_device_combo.current()
        if 0 <= index < len(self._instruments):
            return self._instruments[index]
        return None

    def _add_completion_wait_step(self) -> None:
        instrument = self._completion_instrument()
        if instrument is None:
            self.status_var.set("완료를 확인할 장비를 먼저 선택해 주세요.")
            return
        try:
            timeout_seconds = self._seconds_from_var(
                self.completion_timeout_var.get(),
                "완료 확인 제한 시간",
                self.completion_timeout_unit_var.get(),
            )
            step = wait_for_completion(instrument, timeout_seconds)
        except ValueError as exc:
            self.status_var.set(str(exc))
            self.completion_timeout_spin.focus_set()
            return

        self._steps.append(step)
        self._render_routine(selected_index=len(self._steps) - 1)
        device_name = self._routine_device_name(instrument)
        self.status_var.set(
            f"{device_name}의 앞 작업 완료 확인 단계를 추가했어요."
        )

    def _routine_device_name(self, instrument: SelectedInstrument) -> str:
        short_name = instrument.model.strip() or instrument.display_name
        duplicates = [
            candidate
            for candidate in self._instruments
            if (candidate.model.strip() or candidate.display_name) == short_name
        ]
        if len(duplicates) <= 1:
            return short_name
        if instrument.serial.strip():
            return f"{short_name} · {instrument.serial.strip()}"
        return f"{short_name} #{duplicates.index(instrument) + 1}"

    def _render_routine(self, selected_index: int | None = None) -> None:
        self.routine_list.delete(0, tk.END)
        for index, step in enumerate(self._steps, start=1):
            if isinstance(step, SelectedFeature):
                feature = feature_by_id(
                    step.feature_id,
                    step.instrument.profile_id,
                )
                warning = " [출력 주의]" if feature.is_dangerous else ""
                device_name = self._routine_device_name(step.instrument)
                arguments = (
                    " · " + format_feature_arguments(feature, step.arguments)
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
                result_name = (
                    f" → {step.result_name}"
                    if step.result_name
                    else ""
                )
                step_text = (
                    f"{feature.display_name}{arguments}{result_name}{warning}"
                )
            elif isinstance(step, DelayStep):
                device_name = "PC"
                step_text = (
                    "Delay - "
                    f"{self._format_seconds(step.seconds)} 대기"
                )
            elif isinstance(step, PlanBoundDelayStep):
                device_name = self._routine_device_name(step.instrument)
                step_text = "Delay - 시험 계획의 Dwell만큼 대기"
            elif isinstance(step, WaitForCompletionStep):
                device_name = self._routine_device_name(step.instrument)
                step_text = (
                    "Wait for Completion - 앞 작업 완료 확인 "
                    f"(제한 {self._format_seconds(step.timeout_seconds)})"
                )
            else:
                continue
            self.routine_list.insert(
                tk.END,
                (
                    f"  {index:02d}   {device_name}"
                    f"  │  {step_text}"
                ),
            )

        self.routine_count_var.set(f"{len(self._steps)}단계")
        has_steps = bool(self._steps)
        if has_steps:
            self.routine_empty_label.place_forget()
            if selected_index is not None:
                bounded_index = min(max(0, selected_index), len(self._steps) - 1)
                self.routine_list.selection_clear(0, tk.END)
                self.routine_list.selection_set(bounded_index)
                self.routine_list.activate(bounded_index)
                self.routine_list.see(bounded_index)
        else:
            empty_text = (
                "장비를 먼저 선택해 주세요.\n"
                "선택한 장비가 있어야 루틴을 만들 수 있어요."
                if not self._instruments
                else "아직 추가한 단계가 없어요.\n"
                "왼쪽에서 기능을 골라 추가해 보세요."
            )
            self.routine_empty_label.configure(text=empty_text)
            self.routine_empty_label.place(relx=0.5, rely=0.5, anchor="center")
            self.routine_empty_label.lift()

        self.clear_button.configure(
            state="normal" if has_steps else "disabled",
            cursor="hand2" if has_steps else "arrow",
        )
        self._set_button_state(self.save_button, has_steps)
        self._set_button_state(self.save_continue_button, has_steps)
        self._update_routine_controls()

    def _routine_required_instruments(self) -> tuple[SelectedInstrument, ...]:
        """Return only devices referenced by at least one routine step."""

        required: list[SelectedInstrument] = []
        seen_resources: set[str] = set()
        for step in self._steps:
            if not isinstance(
                step,
                (
                    SelectedFeature,
                    PlanBoundDelayStep,
                    WaitForCompletionStep,
                ),
            ):
                continue
            instrument = step.instrument
            if instrument.resource in seen_resources:
                continue
            required.append(instrument)
            seen_resources.add(instrument.resource)
        return tuple(required)

    def _save_routine_file(self) -> Path | None:
        if not self._steps:
            self.status_var.set("저장할 루틴 단계가 아직 없어요.")
            return None

        initial_directory = (
            str(self._last_routine_path.parent)
            if self._last_routine_path is not None
            else None
        )
        initial_file = (
            self._last_routine_path.name
            if self._last_routine_path is not None
            else "새_측정_루틴.scpiroutine.json"
        )
        path_text = filedialog.asksaveasfilename(
            parent=self,
            title="루틴 저장",
            initialdir=initial_directory,
            initialfile=initial_file,
            defaultextension=".scpiroutine.json",
            filetypes=(
                ("SCPI 측정 루틴", "*.scpiroutine.json"),
                ("JSON 파일", "*.json"),
                ("모든 파일", "*.*"),
            ),
        )
        if not path_text:
            return None

        path = Path(path_text)
        try:
            save_routine(
                path,
                self._routine_required_instruments(),
                self._steps,
            )
        except (OSError, RoutineStorageError, ValueError) as exc:
            self.status_var.set("루틴을 저장하지 못했어요.")
            messagebox.showerror(
                "루틴 저장 실패",
                "파일을 저장하지 못했어요.\n\n"
                f"{exc}\n\n"
                "저장할 폴더의 쓰기 권한과 파일 이름을 확인해 주세요.",
                parent=self,
            )
            return None

        self._last_routine_path = path
        self.status_var.set(f"‘{path.name}’ 파일로 루틴을 저장했어요.")
        messagebox.showinfo(
            "루틴 저장 완료",
            f"{len(self._steps)}단계 루틴을 저장했어요.\n\n{path}",
            parent=self,
        )
        return path

    def _save_and_continue(self) -> None:
        saved_path = self._save_routine_file()
        if saved_path is None:
            return
        if self._on_continue is not None:
            self._on_continue()

    def _load_routine_file(self) -> None:
        initial_directory = (
            str(self._last_routine_path.parent)
            if self._last_routine_path is not None
            else None
        )
        path_text = filedialog.askopenfilename(
            parent=self,
            title="루틴 불러오기",
            initialdir=initial_directory,
            filetypes=(
                ("SCPI 측정 루틴", "*.scpiroutine.json"),
                ("JSON 파일", "*.json"),
                ("모든 파일", "*.*"),
            ),
        )
        if not path_text:
            return

        path = Path(path_text)
        try:
            requirements = load_routine_requirements(path)
            instrument_map, device_problems = self._match_loaded_instruments(
                requirements
            )
            if device_problems:
                self.status_var.set("필요한 장비가 없어 루틴을 불러오지 않았어요.")
                messagebox.showwarning(
                    "필요한 장비가 없어요",
                    "이 루틴에 필요한 장비가 현재 선택되어 있지 않거나 "
                    "연결되지 않았어요.\n\n"
                    + "\n".join(f"• {problem}" for problem in device_problems)
                    + "\n\n장비 찾기 탭에서 해당 장비를 체크한 뒤 "
                    "다시 불러와 주세요.\n"
                    "현재 작성 중인 루틴은 그대로 두었어요.",
                    parent=self,
                )
                return
            document = load_routine(
                path,
                trusted_instruments=tuple(instrument_map.values()),
            )
            loaded_steps = self._rebind_loaded_steps(document, instrument_map)
        except (OSError, RoutineStorageError, ValueError, KeyError) as exc:
            self.status_var.set("루틴 파일을 읽지 못했어요.")
            messagebox.showerror(
                "루틴 불러오기 실패",
                "이 파일은 올바른 루틴 파일이 아니거나 읽을 수 없어요.\n\n"
                f"{exc}",
                parent=self,
            )
            return

        if self._steps and not messagebox.askyesno(
            "현재 루틴을 바꿀까요?",
            f"작성 중인 {len(self._steps)}단계 루틴을 "
            f"불러온 {len(loaded_steps)}단계 루틴으로 바꿉니다.\n\n"
            "계속할까요?",
            parent=self,
            default=messagebox.NO,
            icon=messagebox.WARNING,
        ):
            self.status_var.set("불러오기를 취소해 현재 루틴을 그대로 두었어요.")
            return

        self._steps = list(loaded_steps)
        self._last_routine_path = path
        first_index = 0 if self._steps else None
        self._render_routine(selected_index=first_index)
        self.status_var.set(
            f"‘{path.name}’에서 {len(self._steps)}단계 루틴을 불러왔어요."
        )
        messagebox.showinfo(
            "루틴 불러오기 완료",
            f"{len(self._steps)}단계 루틴을 불러왔어요.\n"
            "실행 전에 장비와 각 단계를 다시 확인해 주세요.",
            parent=self,
        )

    def _match_loaded_instruments(
        self,
        document: RoutineFile,
    ) -> tuple[dict[str, SelectedInstrument], tuple[str, ...]]:
        """Match a saved identity to the currently selected physical device."""

        current_by_resource = {
            instrument.resource: instrument
            for instrument in self._instruments
        }
        matches: dict[str, SelectedInstrument] = {}
        problems: list[str] = []
        used_current_resources: set[str] = set()

        for saved in document.required_instruments:
            exact = current_by_resource.get(saved.resource)
            if exact is not None and self._resource_identity_is_compatible(
                saved,
                exact,
            ):
                if exact.resource in used_current_resources:
                    problems.append(
                        f"{self._saved_instrument_label(saved)} — "
                        "다른 저장 장비와 같은 현재 장비를 가리켜요"
                    )
                    continue
                matches[saved.resource] = exact
                used_current_resources.add(exact.resource)
                continue

            serial_matches = [
                current
                for current in self._instruments
                if self._same_physical_identity(saved, current)
            ]
            if len(serial_matches) == 1:
                serial_match = serial_matches[0]
                if serial_match.resource in used_current_resources:
                    problems.append(
                        f"{self._saved_instrument_label(saved)} — "
                        "다른 저장 장비와 같은 현재 장비를 가리켜요"
                    )
                    continue
                matches[saved.resource] = serial_match
                used_current_resources.add(serial_match.resource)
                continue

            label = self._saved_instrument_label(saved)
            if len(serial_matches) > 1:
                problems.append(f"{label} — 같은 장비 후보가 여러 대예요")
            elif exact is not None:
                problems.append(f"{label} — 현재 장비 정보와 일치하지 않아요")
            else:
                problems.append(f"{label} — 장비를 찾지 못했어요")

        return matches, tuple(problems)

    @classmethod
    def _resource_identity_is_compatible(
        cls,
        saved: SelectedInstrument,
        current: SelectedInstrument,
    ) -> bool:
        if saved.category is not current.category:
            return False
        for field_name in ("manufacturer", "model", "serial", "firmware"):
            saved_value = cls._identity_value(getattr(saved, field_name))
            current_value = cls._identity_value(getattr(current, field_name))
            if saved_value and current_value and saved_value != current_value:
                return False
        saved_profile = cls._identity_value(saved.profile_id)
        current_profile = cls._identity_value(current.profile_id)
        if saved_profile and saved_profile != current_profile:
            return False
        saved_fingerprint = cls._identity_value(
            saved.validation_catalog_fingerprint
        )
        current_fingerprint = cls._identity_value(
            current.validation_catalog_fingerprint
        )
        if (
            saved_fingerprint
            and saved_fingerprint != current_fingerprint
        ):
            return False
        saved_options = cls._identity_value(saved.option_response)
        current_options = cls._identity_value(current.option_response)
        if saved_options and saved_options != current_options:
            return False
        saved_raw = cls._identity_value(saved.raw_idn)
        current_raw = cls._identity_value(current.raw_idn)
        if saved_raw and saved_raw != current_raw:
            return False
        if (
            saved.option_state != "unqueried"
            and saved.option_state != current.option_state
        ):
            return False
        return True

    @classmethod
    def _same_physical_identity(
        cls,
        saved: SelectedInstrument,
        current: SelectedInstrument,
    ) -> bool:
        if saved.category is not current.category:
            return False
        saved_serial = cls._identity_value(saved.serial)
        saved_manufacturer = cls._identity_value(saved.manufacturer)
        saved_model = cls._identity_value(saved.model)
        if not (saved_serial and saved_manufacturer and saved_model):
            return False
        if (
            saved_serial != cls._identity_value(current.serial)
            or saved_manufacturer != cls._identity_value(current.manufacturer)
            or saved_model != cls._identity_value(current.model)
        ):
            return False
        if cls._identity_value(saved.profile_id) != cls._identity_value(
            current.profile_id
        ):
            return False
        return cls._resource_identity_is_compatible(saved, current)

    @staticmethod
    def _identity_value(value: str) -> str:
        return value.strip().casefold()

    @staticmethod
    def _saved_instrument_label(instrument: SelectedInstrument) -> str:
        details = instrument.display_name
        if instrument.serial.strip():
            details += f" · S/N {instrument.serial.strip()}"
        return f"{details} ({instrument.resource})"

    @staticmethod
    def _rebind_loaded_steps(
        document: RoutineFile,
        instrument_map: dict[str, SelectedInstrument],
    ) -> tuple[RoutineStep, ...]:
        rebound: list[RoutineStep] = []
        for step in document.steps:
            if isinstance(step, SelectedFeature):
                current = instrument_map[step.instrument.resource]
                rebound.append(
                    select_feature(
                        current,
                        step.feature_id,
                        arguments=step.arguments,
                        plan_bindings=step.plan_bindings,
                        result_name=step.result_name,
                    )
                )
            elif isinstance(step, DelayStep):
                rebound.append(create_delay(step.seconds))
            elif isinstance(step, PlanBoundDelayStep):
                current = instrument_map[step.instrument.resource]
                rebound.append(create_plan_bound_delay(current))
            elif isinstance(step, WaitForCompletionStep):
                current = instrument_map[step.instrument.resource]
                rebound.append(
                    wait_for_completion(current, step.timeout_seconds)
                )
            else:
                raise RoutineStorageError(
                    f"지원하지 않는 루틴 단계예요: {type(step).__name__}"
                )
        return tuple(rebound)

    def _selected_routine_index(self) -> int | None:
        selection = self.routine_list.curselection()
        if not selection:
            return None
        index = int(selection[0])
        return index if 0 <= index < len(self._steps) else None

    def _on_routine_selected(self, _event: tk.Event[Any] | None = None) -> None:
        self._update_routine_controls()

    def _update_routine_controls(self) -> None:
        index = self._selected_routine_index()
        last_index = len(self._steps) - 1
        self._set_button_state(self.move_up_button, index is not None and index > 0)
        self._set_button_state(
            self.move_down_button,
            index is not None and index < last_index,
        )
        self._set_button_state(self.delete_button, index is not None)

    @staticmethod
    def _set_button_state(button: tk.Button, enabled: bool) -> None:
        button.configure(
            state="normal" if enabled else "disabled",
            cursor="hand2" if enabled else "arrow",
        )

    def _move_up(self) -> None:
        index = self._selected_routine_index()
        if index is None or index <= 0:
            return
        self._steps[index - 1], self._steps[index] = (
            self._steps[index],
            self._steps[index - 1],
        )
        self._render_routine(selected_index=index - 1)
        self.status_var.set("선택한 단계를 한 칸 위로 옮겼어요.")

    def _move_down(self) -> None:
        index = self._selected_routine_index()
        if index is None or index >= len(self._steps) - 1:
            return
        self._steps[index + 1], self._steps[index] = (
            self._steps[index],
            self._steps[index + 1],
        )
        self._render_routine(selected_index=index + 1)
        self.status_var.set("선택한 단계를 한 칸 아래로 옮겼어요.")

    def _move_to_top(self) -> None:
        index = self._selected_routine_index()
        if index is None or index <= 0:
            return
        step = self._steps.pop(index)
        self._steps.insert(0, step)
        self._render_routine(selected_index=0)
        self.status_var.set("선택한 단계를 맨 위로 옮겼어요.")

    def _move_to_bottom(self) -> None:
        index = self._selected_routine_index()
        if index is None or index >= len(self._steps) - 1:
            return
        step = self._steps.pop(index)
        self._steps.append(step)
        self._render_routine(selected_index=len(self._steps) - 1)
        self.status_var.set("선택한 단계를 맨 아래로 옮겼어요.")

    def _duplicate_selected(self) -> None:
        index = self._selected_routine_index()
        if index is None:
            return
        self._steps.insert(index + 1, self._steps[index])
        self._render_routine(selected_index=index + 1)
        self.status_var.set("선택한 단계를 바로 아래에 복제했어요.")

    def _on_duplicate_key(self, _event: tk.Event[Any]) -> str:
        self._duplicate_selected()
        return "break"

    def _routine_index_at_y(self, y: int) -> int | None:
        if not self._steps:
            return None
        index = int(self.routine_list.nearest(y))
        if not 0 <= index < len(self._steps):
            return None
        bounds = self.routine_list.bbox(index)
        if bounds is None:
            return None
        _x, row_y, _width, height = (int(value) for value in bounds)
        if not row_y <= y < row_y + height:
            return None
        return index

    def _show_routine_context_menu(self, event: tk.Event[Any]) -> str:
        index = self._routine_index_at_y(int(event.y))
        if index is None:
            self.routine_list.selection_clear(0, tk.END)
            self._update_routine_controls()
            return "break"

        self.routine_list.selection_clear(0, tk.END)
        self.routine_list.selection_set(index)
        self.routine_list.activate(index)
        self.routine_list.focus_set()
        self._update_routine_controls()

        last_index = len(self._steps) - 1
        normal = "normal"
        disabled = "disabled"
        self.routine_context_menu.entryconfigure(0, state=normal)
        self.routine_context_menu.entryconfigure(
            2,
            state=normal if index > 0 else disabled,
        )
        self.routine_context_menu.entryconfigure(
            3,
            state=normal if index < last_index else disabled,
        )
        self.routine_context_menu.entryconfigure(
            4,
            state=normal if index > 0 else disabled,
        )
        self.routine_context_menu.entryconfigure(
            5,
            state=normal if index < last_index else disabled,
        )
        self.routine_context_menu.entryconfigure(7, state=normal)

        try:
            self.routine_context_menu.tk_popup(event.x_root, event.y_root)
        finally:
            self.routine_context_menu.grab_release()
        return "break"

    def _delete_selected(self) -> None:
        index = self._selected_routine_index()
        if index is None:
            return
        removed = self._steps.pop(index)
        if isinstance(removed, SelectedFeature):
            removed_name = feature_by_id(
                removed.feature_id,
                removed.instrument.profile_id,
            ).display_name
        elif isinstance(removed, DelayStep):
            removed_name = "Delay - 대기 시간"
        elif isinstance(removed, PlanBoundDelayStep):
            removed_name = "Delay - 계획 Dwell 대기"
        else:
            removed_name = "Wait for Completion - 앞 작업 완료 확인"
        next_index = min(index, len(self._steps) - 1) if self._steps else None
        self._render_routine(selected_index=next_index)
        self.status_var.set(f"{removed_name} 단계를 루틴에서 삭제했어요.")

    def _on_delete_key(self, _event: tk.Event[Any]) -> str:
        self._delete_selected()
        return "break"

    def _clear_routine(self) -> None:
        if not self._steps:
            return
        self._steps.clear()
        self._render_routine()
        self.status_var.set("루틴을 모두 비웠어요. 다시 천천히 추가해 보세요.")

    def _go_back(self) -> None:
        if self._on_back is not None:
            self._on_back()

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
        """Scale fonts and spacing for the host window's responsive size."""

        self._ui_scale = max(0.75, min(1.4, float(scale)))

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
