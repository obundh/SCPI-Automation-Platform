from __future__ import annotations

import math
import tkinter as tk
from tkinter import ttk
from typing import Callable

from scpi_automation.identity import DeviceCategory
from scpi_automation.planning import (
    MeasurementPlanItem,
    SignalGeneratorPlanItem,
    SpectrumPlanItem,
    generate_frequency_series,
    parse_frequency_list,
)
from scpi_automation.routine import SelectedInstrument


BACKGROUND = "#F4F6F8"
CARD = "#FFFFFF"
TEXT = "#191F28"
SUBTEXT = "#6B7684"
BORDER = "#E5E8EB"
ACCENT = "#3182F6"
ACCENT_DARK = "#1B64DA"
ACCENT_LIGHT = "#EAF3FF"
NEUTRAL_LIGHT = "#F2F4F6"
WARNING_LIGHT = "#FFF4E5"
WARNING = "#B45309"

FREQUENCY_UNITS = {
    "Hz": 1.0,
    "kHz": 1_000.0,
    "MHz": 1_000_000.0,
    "GHz": 1_000_000_000.0,
}

ANALYZER_METHODS = (
    "여러 중심 주파수에서 Peak/Marker 측정",
    "일정 간격으로 중심 주파수 측정",
)
GENERATOR_METHODS = (
    "여러 CW 주파수를 순서대로 설정",
    "일정 간격으로 CW 주파수 단계 계획",
)


def _button(
    parent: tk.Misc,
    *,
    text: str,
    command: Callable[[], None],
    primary: bool = False,
) -> tk.Button:
    return tk.Button(
        parent,
        text=text,
        command=command,
        font=("Segoe UI Semibold", 10),
        background=ACCENT if primary else NEUTRAL_LIGHT,
        foreground="#FFFFFF" if primary else TEXT,
        activebackground=ACCENT_DARK if primary else BORDER,
        activeforeground="#FFFFFF" if primary else TEXT,
        relief="flat",
        borderwidth=0,
        cursor="hand2",
        padx=17,
        pady=9,
        takefocus=True,
    )


def _entry(parent: tk.Misc, variable: tk.StringVar) -> tk.Entry:
    return tk.Entry(
        parent,
        textvariable=variable,
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


class PlanDetailDialog(tk.Toplevel):
    """Create several atomic plan items from a category-specific test method."""

    def __init__(
        self,
        master: tk.Misc,
        *,
        instruments: tuple[SelectedInstrument, ...],
        on_add: Callable[[tuple[MeasurementPlanItem, ...]], bool],
        initial_instrument: SelectedInstrument | None = None,
    ) -> None:
        super().__init__(master)
        supported = tuple(
            instrument
            for instrument in instruments
            if instrument.category
            in {
                DeviceCategory.SPECTRUM_ANALYZER,
                DeviceCategory.SIGNAL_GENERATOR,
            }
        )
        if not supported:
            raise ValueError("상세 계획에 사용할 지원 장비가 없습니다.")

        self._instruments = supported
        self._on_add = on_add
        self.title("계획 상세 설정")
        self.geometry("840x650")
        self.minsize(780, 600)
        self.configure(background=BACKGROUND)
        self.transient(master.winfo_toplevel())
        self.protocol("WM_DELETE_WINDOW", self._cancel)

        self.device_var = tk.StringVar()
        self.method_var = tk.StringVar()
        self.method_description_var = tk.StringVar()
        self.frequency_label_var = tk.StringVar()
        self.range_start_label_var = tk.StringVar()
        self.range_stop_label_var = tk.StringVar()
        self.list_unit_var = tk.StringVar(value="MHz")
        self.range_unit_var = tk.StringVar(value="MHz")
        self.range_start_var = tk.StringVar(value="100")
        self.range_stop_var = tk.StringVar(value="1000")
        self.range_step_var = tk.StringVar(value="100")

        self.span_value_var = tk.StringVar(value="100")
        self.span_unit_var = tk.StringVar(value="MHz")
        self.rbw_value_var = tk.StringVar(value="100")
        self.rbw_unit_var = tk.StringVar(value="kHz")
        self.rbw_auto_var = tk.BooleanVar(value=False)
        self.vbw_value_var = tk.StringVar(value="100")
        self.vbw_unit_var = tk.StringVar(value="kHz")
        self.vbw_auto_var = tk.BooleanVar(value=True)
        self.reference_level_var = tk.StringVar(value="0")

        self.power_var = tk.StringVar(value="-20")
        self.dwell_var = tk.StringVar(value="1")
        self.status_var = tk.StringVar(
            value="주파수를 만들 방법과 공통 설정을 확인해 주세요."
        )

        self._build()
        initial_index = 0
        if initial_instrument is not None:
            for index, instrument in enumerate(self._instruments):
                if instrument == initial_instrument:
                    initial_index = index
                    break
        self.device_combo.current(initial_index)
        self._on_device_changed()
        self.update_idletasks()
        self._activate_dialog()

    def _activate_dialog(self) -> None:
        if not self.winfo_exists():
            return
        try:
            self.grab_set()
            self.focus_force()
        except tk.TclError:
            pass

    def _build(self) -> None:
        self.columnconfigure(0, weight=1)
        self.rowconfigure(3, weight=1)

        header = tk.Frame(self, background=BACKGROUND)
        header.grid(row=0, column=0, sticky="ew", padx=26, pady=(22, 12))
        header.columnconfigure(0, weight=1)
        tk.Label(
            header,
            text="여러 측정 조건을 한 번에 만들어볼게요",
            font=("Segoe UI Semibold", 18),
            background=BACKGROUND,
            foreground=TEXT,
        ).grid(row=0, column=0, sticky="w")
        tk.Label(
            header,
            text=(
                "장비 종류에 맞는 시험 방법을 고르면 주파수 여러 개를 "
                "각각의 안전한 계획 항목으로 만들어 줘요."
            ),
            font=("Segoe UI", 9),
            background=BACKGROUND,
            foreground=SUBTEXT,
        ).grid(row=1, column=0, sticky="w", pady=(4, 0))

        selection_card = tk.Frame(
            self,
            background=CARD,
            highlightbackground=BORDER,
            highlightthickness=1,
        )
        selection_card.grid(row=1, column=0, sticky="ew", padx=26, pady=(0, 10))
        selection_card.columnconfigure(0, weight=1)
        selection_card.columnconfigure(1, weight=1)
        tk.Label(
            selection_card,
            text="사용 장비",
            font=("Segoe UI Semibold", 9),
            background=CARD,
            foreground=TEXT,
        ).grid(row=0, column=0, sticky="w", padx=(14, 7), pady=(11, 3))
        tk.Label(
            selection_card,
            text="자주 사용하는 시험 방법",
            font=("Segoe UI Semibold", 9),
            background=CARD,
            foreground=TEXT,
        ).grid(row=0, column=1, sticky="w", padx=(7, 14), pady=(11, 3))
        self.device_combo = ttk.Combobox(
            selection_card,
            textvariable=self.device_var,
            values=tuple(self._instrument_option(item) for item in self._instruments),
            state="readonly",
            font=("Segoe UI", 9),
        )
        self.device_combo.grid(
            row=1,
            column=0,
            sticky="ew",
            padx=(14, 7),
            pady=(0, 12),
        )
        self.device_combo.bind("<<ComboboxSelected>>", self._on_device_changed)
        self.method_combo = ttk.Combobox(
            selection_card,
            textvariable=self.method_var,
            state="readonly",
            font=("Segoe UI", 9),
        )
        self.method_combo.grid(
            row=1,
            column=1,
            sticky="ew",
            padx=(7, 14),
            pady=(0, 12),
        )
        self.method_combo.bind("<<ComboboxSelected>>", self._on_method_changed)

        method_note = tk.Frame(self, background=ACCENT_LIGHT)
        method_note.grid(row=2, column=0, sticky="ew", padx=26, pady=(0, 10))
        method_note.columnconfigure(0, weight=1)
        tk.Label(
            method_note,
            textvariable=self.method_description_var,
            font=("Segoe UI", 9),
            background=ACCENT_LIGHT,
            foreground="#3B608A",
            anchor="w",
        ).grid(row=0, column=0, sticky="ew", padx=12, pady=8)

        body = tk.Frame(self, background=BACKGROUND)
        body.grid(row=3, column=0, sticky="nsew", padx=26)
        body.columnconfigure(0, weight=1, uniform="detail_body")
        body.columnconfigure(1, weight=1, uniform="detail_body")
        body.rowconfigure(0, weight=1)
        self._build_frequency_card(body)
        self._build_settings_card(body)

        footer = tk.Frame(self, background=BACKGROUND)
        footer.grid(row=4, column=0, sticky="ew", padx=26, pady=(10, 20))
        footer.columnconfigure(0, weight=1)
        self.status_label = tk.Label(
            footer,
            textvariable=self.status_var,
            font=("Segoe UI", 9),
            background=BACKGROUND,
            foreground=WARNING,
            anchor="w",
            justify="left",
            height=2,
        )
        self.status_label.grid(row=0, column=0, sticky="ew", padx=(0, 12))
        _button(
            footer,
            text="취소",
            command=self._cancel,
        ).grid(row=0, column=1, padx=(0, 7))
        self.apply_button = _button(
            footer,
            text="계획에 한꺼번에 추가",
            command=self._apply,
            primary=True,
        )
        self.apply_button.grid(row=0, column=2)

    def _build_frequency_card(self, parent: tk.Frame) -> None:
        card = tk.Frame(
            parent,
            background=CARD,
            highlightbackground=BORDER,
            highlightthickness=1,
        )
        card.grid(row=0, column=0, sticky="nsew", padx=(0, 6))
        card.columnconfigure(0, weight=1)
        card.rowconfigure(3, weight=1)
        tk.Label(
            card,
            text="1. 주파수 여러 개 만들기",
            font=("Segoe UI Semibold", 12),
            background=CARD,
            foreground=TEXT,
        ).grid(row=0, column=0, sticky="w", padx=16, pady=(14, 2))
        tk.Label(
            card,
            textvariable=self.frequency_label_var,
            font=("Segoe UI", 9),
            background=CARD,
            foreground=SUBTEXT,
        ).grid(row=1, column=0, sticky="w", padx=16, pady=(0, 10))

        self.frequency_mode_host = tk.Frame(card, background=CARD)
        self.frequency_mode_host.grid(
            row=3,
            column=0,
            sticky="nsew",
            padx=16,
            pady=(0, 14),
        )
        self.frequency_mode_host.columnconfigure(0, weight=1)
        self.frequency_mode_host.rowconfigure(0, weight=1)

        self.list_frame = tk.Frame(self.frequency_mode_host, background=CARD)
        self.list_frame.grid(row=0, column=0, sticky="nsew")
        self.list_frame.columnconfigure(0, weight=1)
        self.list_frame.rowconfigure(1, weight=1)
        tk.Label(
            self.list_frame,
            text="쉼표나 줄바꿈으로 구분해 주세요.",
            font=("Segoe UI", 8),
            background=CARD,
            foreground=SUBTEXT,
        ).grid(row=0, column=0, sticky="w", pady=(0, 5))
        self.frequency_text = tk.Text(
            self.list_frame,
            height=8,
            wrap="word",
            font=("Segoe UI", 10),
            background="#FBFCFD",
            foreground=TEXT,
            insertbackground=TEXT,
            highlightbackground=BORDER,
            highlightcolor=ACCENT,
            highlightthickness=1,
            relief="flat",
            borderwidth=0,
            padx=9,
            pady=8,
        )
        self.frequency_text.grid(row=1, column=0, sticky="nsew")
        self.frequency_text.insert("1.0", "100, 200, 500, 1000")
        list_unit_row = tk.Frame(self.list_frame, background=CARD)
        list_unit_row.grid(row=2, column=0, sticky="ew", pady=(7, 0))
        tk.Label(
            list_unit_row,
            text="목록 전체 단위",
            font=("Segoe UI", 8),
            background=CARD,
            foreground=SUBTEXT,
        ).pack(side="left")
        ttk.Combobox(
            list_unit_row,
            textvariable=self.list_unit_var,
            values=tuple(FREQUENCY_UNITS),
            state="readonly",
            width=7,
        ).pack(side="right")

        self.range_frame = tk.Frame(self.frequency_mode_host, background=CARD)
        self.range_frame.columnconfigure(1, weight=1)
        self._range_row(
            self.range_frame,
            0,
            self.range_start_label_var,
            self.range_start_var,
        )
        self._range_row(
            self.range_frame,
            1,
            self.range_stop_label_var,
            self.range_stop_var,
        )
        self._range_row(
            self.range_frame,
            2,
            tk.StringVar(value="주파수 간격"),
            self.range_step_var,
        )
        tk.Label(
            self.range_frame,
            text=(
                "끝 주파수까지 간격이 정확히 맞아야 해요.\n"
                "예: 100 → 500, 간격 100 = 5개"
            ),
            font=("Segoe UI", 8),
            background=CARD,
            foreground=SUBTEXT,
            justify="left",
        ).grid(row=3, column=0, columnspan=3, sticky="w", pady=(10, 0))

    def _range_row(
        self,
        parent: tk.Frame,
        row: int,
        label_var: tk.StringVar,
        value_var: tk.StringVar,
    ) -> None:
        tk.Label(
            parent,
            textvariable=label_var,
            font=("Segoe UI", 9),
            background=CARD,
            foreground=TEXT,
        ).grid(row=row, column=0, sticky="w", pady=5)
        field = _entry(parent, value_var)
        field.grid(row=row, column=1, sticky="ew", padx=(9, 7), pady=5, ipady=5)
        if row == 0:
            ttk.Combobox(
                parent,
                textvariable=self.range_unit_var,
                values=tuple(FREQUENCY_UNITS),
                state="readonly",
                width=7,
            ).grid(row=0, column=2, rowspan=3, sticky="n")

    def _build_settings_card(self, parent: tk.Frame) -> None:
        card = tk.Frame(
            parent,
            background=CARD,
            highlightbackground=BORDER,
            highlightthickness=1,
        )
        card.grid(row=0, column=1, sticky="nsew", padx=(6, 0))
        card.columnconfigure(0, weight=1)
        card.rowconfigure(2, weight=1)
        tk.Label(
            card,
            text="2. 공통 시험 설정",
            font=("Segoe UI Semibold", 12),
            background=CARD,
            foreground=TEXT,
        ).grid(row=0, column=0, sticky="w", padx=16, pady=(14, 2))
        tk.Label(
            card,
            text="만들어진 모든 주파수에 똑같이 적용돼요.",
            font=("Segoe UI", 9),
            background=CARD,
            foreground=SUBTEXT,
        ).grid(row=1, column=0, sticky="w", padx=16, pady=(0, 10))

        self.settings_host = tk.Frame(card, background=CARD)
        self.settings_host.grid(
            row=2,
            column=0,
            sticky="nsew",
            padx=16,
            pady=(0, 14),
        )
        self.settings_host.columnconfigure(0, weight=1)
        self.settings_host.rowconfigure(0, weight=1)
        self._build_spectrum_settings()
        self._build_generator_settings()

    def _build_spectrum_settings(self) -> None:
        self.spectrum_settings = tk.Frame(self.settings_host, background=CARD)
        self.spectrum_settings.grid(row=0, column=0, sticky="nsew")
        self.spectrum_settings.columnconfigure(1, weight=1)
        self._frequency_setting_row(
            self.spectrum_settings,
            0,
            "Span - 분석 범위",
            self.span_value_var,
            self.span_unit_var,
        )
        self.rbw_entry, self.rbw_unit_combo = self._frequency_setting_row(
            self.spectrum_settings,
            1,
            "RBW - 분해능 대역폭",
            self.rbw_value_var,
            self.rbw_unit_var,
            auto_var=self.rbw_auto_var,
        )
        self.vbw_entry, self.vbw_unit_combo = self._frequency_setting_row(
            self.spectrum_settings,
            2,
            "VBW - 비디오 대역폭",
            self.vbw_value_var,
            self.vbw_unit_var,
            auto_var=self.vbw_auto_var,
        )
        self.rbw_auto_var.trace_add("write", self._on_auto_changed)
        self.vbw_auto_var.trace_add("write", self._on_auto_changed)

        tk.Label(
            self.spectrum_settings,
            text="Ref. Level - 화면 기준 레벨",
            font=("Segoe UI", 9),
            background=CARD,
            foreground=TEXT,
        ).grid(row=3, column=0, sticky="w", pady=5)
        ref_entry = _entry(self.spectrum_settings, self.reference_level_var)
        ref_entry.grid(row=3, column=1, sticky="ew", padx=(9, 7), pady=5, ipady=5)
        tk.Label(
            self.spectrum_settings,
            text="dBm",
            font=("Segoe UI Semibold", 9),
            background=CARD,
            foreground=SUBTEXT,
            width=6,
        ).grid(row=3, column=2)
        tk.Label(
            self.spectrum_settings,
            text=(
                "예정 결과: 각 중심 주파수에서 Peak Search 후\n"
                "Marker 주파수와 레벨을 기록해요."
            ),
            font=("Segoe UI", 9),
            background=ACCENT_LIGHT,
            foreground="#3B608A",
            justify="left",
            anchor="nw",
            padx=10,
            pady=9,
        ).grid(row=4, column=0, columnspan=4, sticky="ew", pady=(12, 0))
        self._sync_auto_states()

    def _frequency_setting_row(
        self,
        parent: tk.Frame,
        row: int,
        label: str,
        value_var: tk.StringVar,
        unit_var: tk.StringVar,
        *,
        auto_var: tk.BooleanVar | None = None,
    ) -> tuple[tk.Entry, ttk.Combobox]:
        tk.Label(
            parent,
            text=label,
            font=("Segoe UI", 9),
            background=CARD,
            foreground=TEXT,
        ).grid(row=row, column=0, sticky="w", pady=5)
        field = _entry(parent, value_var)
        field.grid(row=row, column=1, sticky="ew", padx=(9, 7), pady=5, ipady=5)
        unit = ttk.Combobox(
            parent,
            textvariable=unit_var,
            values=tuple(FREQUENCY_UNITS),
            state="readonly",
            width=6,
        )
        unit.grid(row=row, column=2, pady=5)
        if auto_var is not None:
            tk.Checkbutton(
                parent,
                text="자동",
                variable=auto_var,
                font=("Segoe UI", 8),
                background=CARD,
                foreground=SUBTEXT,
                activebackground=CARD,
                selectcolor=CARD,
            ).grid(row=row, column=3, padx=(4, 0), pady=5)
        return field, unit

    def _build_generator_settings(self) -> None:
        self.generator_settings = tk.Frame(self.settings_host, background=CARD)
        self.generator_settings.grid(row=0, column=0, sticky="nsew")
        self.generator_settings.columnconfigure(1, weight=1)
        tk.Label(
            self.generator_settings,
            text="Power - 출력 설정값",
            font=("Segoe UI", 9),
            background=CARD,
            foreground=TEXT,
        ).grid(row=0, column=0, sticky="w", pady=5)
        power_entry = _entry(self.generator_settings, self.power_var)
        power_entry.grid(row=0, column=1, sticky="ew", padx=(9, 7), pady=5, ipady=5)
        tk.Label(
            self.generator_settings,
            text="dBm",
            font=("Segoe UI Semibold", 9),
            background=CARD,
            foreground=SUBTEXT,
            width=6,
        ).grid(row=0, column=2)
        tk.Label(
            self.generator_settings,
            text="Dwell - 각 주파수 유지 시간",
            font=("Segoe UI", 9),
            background=CARD,
            foreground=TEXT,
        ).grid(row=1, column=0, sticky="w", pady=5)
        dwell_entry = _entry(self.generator_settings, self.dwell_var)
        dwell_entry.grid(row=1, column=1, sticky="ew", padx=(9, 7), pady=5, ipady=5)
        tk.Label(
            self.generator_settings,
            text="초",
            font=("Segoe UI Semibold", 9),
            background=CARD,
            foreground=SUBTEXT,
            width=6,
        ).grid(row=1, column=2)
        tk.Label(
            self.generator_settings,
            text=(
                "계획에는 주파수·출력 설정값과 유지 시간만 넣어요.\n"
                "RF 출력 ON/OFF는 루틴에서 별도 단계로 설정해야 해요."
            ),
            font=("Segoe UI", 9),
            background=WARNING_LIGHT,
            foreground=WARNING,
            justify="left",
            anchor="nw",
            padx=10,
            pady=9,
        ).grid(row=2, column=0, columnspan=3, sticky="ew", pady=(12, 0))

    def _on_auto_changed(self, *_args: object) -> None:
        self._sync_auto_states()

    def _sync_auto_states(self) -> None:
        if not hasattr(self, "rbw_entry"):
            return
        self.rbw_entry.configure(
            state="disabled" if self.rbw_auto_var.get() else "normal"
        )
        self.rbw_unit_combo.configure(
            state="disabled" if self.rbw_auto_var.get() else "readonly"
        )
        self.vbw_entry.configure(
            state="disabled" if self.vbw_auto_var.get() else "normal"
        )
        self.vbw_unit_combo.configure(
            state="disabled" if self.vbw_auto_var.get() else "readonly"
        )

    @staticmethod
    def _instrument_option(instrument: SelectedInstrument) -> str:
        return f"{instrument.display_name}  ·  {instrument.resource}"

    def _selected_instrument(self) -> SelectedInstrument:
        index = self.device_combo.current()
        if not 0 <= index < len(self._instruments):
            raise ValueError("사용할 장비를 다시 선택해 주세요.")
        return self._instruments[index]

    def _on_device_changed(self, _event: tk.Event[tk.Misc] | None = None) -> None:
        instrument = self._selected_instrument()
        is_analyzer = instrument.category is DeviceCategory.SPECTRUM_ANALYZER
        methods = ANALYZER_METHODS if is_analyzer else GENERATOR_METHODS
        self.method_combo.configure(values=methods)
        self.method_combo.current(0)
        if is_analyzer:
            self.frequency_label_var.set(
                "측정할 중심 주파수 목록 또는 중심 주파수 구간을 정해요."
            )
            self.range_start_label_var.set("중심 주파수 시작")
            self.range_stop_label_var.set("중심 주파수 끝")
            self.generator_settings.grid_remove()
            self.spectrum_settings.grid()
        else:
            self.frequency_label_var.set(
                "순서대로 설정할 CW 출력 주파수 목록 또는 구간을 정해요."
            )
            self.range_start_label_var.set("출력 주파수 시작")
            self.range_stop_label_var.set("출력 주파수 끝")
            self.spectrum_settings.grid_remove()
            self.generator_settings.grid()
        self._on_method_changed()

    def _on_method_changed(self, _event: tk.Event[tk.Misc] | None = None) -> None:
        is_list = self.method_combo.current() <= 0
        if is_list:
            self.range_frame.grid_remove()
            self.list_frame.grid()
            self.method_description_var.set(
                "직접 적은 순서와 중복을 그대로 유지해 반복 측정도 계획할 수 있어요."
            )
        else:
            self.list_frame.grid_remove()
            self.range_frame.grid(row=0, column=0, sticky="nsew")
            self.method_description_var.set(
                "시작·끝·간격을 사용해 같은 조건의 주파수 계획을 자동으로 만들어요."
            )

    @staticmethod
    def _number(value: str, field_name: str) -> float:
        normalized = value.strip().replace(",", "")
        if not normalized:
            raise ValueError(f"{field_name} 값을 입력해 주세요.")
        try:
            number = float(normalized)
        except ValueError as exc:
            raise ValueError(f"{field_name}은(는) 숫자로 입력해 주세요.") from exc
        if not math.isfinite(number):
            raise ValueError(f"{field_name}은(는) 유한한 숫자여야 해요.")
        return number

    def _frequency_value(
        self,
        value: str,
        unit: str,
        field_name: str,
    ) -> float:
        factor = FREQUENCY_UNITS.get(unit)
        if factor is None:
            raise ValueError(f"{field_name} 단위를 다시 선택해 주세요.")
        return self._number(value, field_name) * factor

    def _frequency_points(self) -> tuple[float, ...]:
        if self.method_combo.current() <= 0:
            factor = FREQUENCY_UNITS.get(self.list_unit_var.get())
            if factor is None:
                raise ValueError("주파수 목록 단위를 다시 선택해 주세요.")
            return parse_frequency_list(
                self.frequency_text.get("1.0", "end-1c"),
                factor,
            )
        unit = self.range_unit_var.get()
        return generate_frequency_series(
            self._frequency_value(
                self.range_start_var.get(),
                unit,
                self.range_start_label_var.get(),
            ),
            self._frequency_value(
                self.range_stop_var.get(),
                unit,
                self.range_stop_label_var.get(),
            ),
            self._frequency_value(
                self.range_step_var.get(),
                unit,
                "주파수 간격",
            ),
        )

    def _build_items(self) -> tuple[MeasurementPlanItem, ...]:
        instrument = self._selected_instrument()
        frequencies = self._frequency_points()
        if instrument.category is DeviceCategory.SPECTRUM_ANALYZER:
            span = self._frequency_value(
                self.span_value_var.get(),
                self.span_unit_var.get(),
                "Span",
            )
            rbw = (
                None
                if self.rbw_auto_var.get()
                else self._frequency_value(
                    self.rbw_value_var.get(),
                    self.rbw_unit_var.get(),
                    "RBW",
                )
            )
            vbw = (
                None
                if self.vbw_auto_var.get()
                else self._frequency_value(
                    self.vbw_value_var.get(),
                    self.vbw_unit_var.get(),
                    "VBW",
                )
            )
            reference_level = self._number(
                self.reference_level_var.get(),
                "Ref. Level",
            )
            return tuple(
                SpectrumPlanItem(
                    instrument=instrument,
                    center_frequency_hz=frequency,
                    span_hz=span,
                    rbw_hz=rbw,
                    vbw_hz=vbw,
                    reference_level_dbm=reference_level,
                )
                for frequency in frequencies
            )

        power = self._number(self.power_var.get(), "Power")
        dwell = self._number(self.dwell_var.get(), "Dwell")
        return tuple(
            SignalGeneratorPlanItem(
                instrument=instrument,
                frequency_hz=frequency,
                power_dbm=power,
                dwell_seconds=dwell,
            )
            for frequency in frequencies
        )

    def _apply(self) -> None:
        try:
            items = self._build_items()
        except (TypeError, ValueError) as exc:
            self.status_var.set(str(exc))
            return
        if not self._on_add(items):
            return
        self.destroy()

    def _cancel(self) -> None:
        self.destroy()
