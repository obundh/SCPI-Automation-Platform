from __future__ import annotations

import math
import re
import tkinter as tk
from dataclasses import dataclass
from datetime import datetime
from tkinter import ttk
from typing import Any, Iterable, Sequence

from scpi_automation.identity import DeviceCategory
from scpi_automation.routine import (
    RoutineStep,
    SelectedFeature,
    SelectedInstrument,
    feature_by_id,
)
from scpi_automation.ui.value_formatting import (
    format_display_value,
    format_engineering_value,
    format_feature_arguments,
)


BACKGROUND = "#F4F6F8"
CARD = "#FFFFFF"
TEXT = "#191F28"
SUBTEXT = "#6B7684"
BORDER = "#E5E8EB"
ACCENT = "#3182F6"
ACCENT_DARK = "#1B64DA"
ACCENT_LIGHT = "#EAF3FF"
SCREEN = "#09131F"
SCREEN_GRID = "#223447"
SCREEN_TEXT = "#D8E7F5"
SUCCESS = "#15B86A"
WARNING = "#F59E0B"

EMPTY_MESSAGE = "아직 실제 조회값이 없어요"

_CATEGORY_COLORS: dict[DeviceCategory, str] = {
    DeviceCategory.SPECTRUM_ANALYZER: "#25D48A",
    DeviceCategory.SIGNAL_GENERATOR: "#4EA1FF",
    DeviceCategory.FUNCTION_GENERATOR: "#6B8CFF",
    DeviceCategory.OSCILLOSCOPE: "#B180FF",
    DeviceCategory.DIGITAL_MULTIMETER: "#FFB547",
    DeviceCategory.POWER_SUPPLY: "#FF6B6B",
    DeviceCategory.LCR_METER: "#FF8E53",
    DeviceCategory.NETWORK_ANALYZER: "#2DD4BF",
    DeviceCategory.UNKNOWN: "#A6ADB4",
}

_CATEGORY_HINTS: dict[DeviceCategory, str] = {
    DeviceCategory.SPECTRUM_ANALYZER: "Trace 또는 Marker 조회값",
    DeviceCategory.SIGNAL_GENERATOR: "주파수·레벨·출력 조회값",
    DeviceCategory.FUNCTION_GENERATOR: "주파수·진폭·파형 조회값",
    DeviceCategory.OSCILLOSCOPE: "Waveform 또는 측정 조회값",
    DeviceCategory.DIGITAL_MULTIMETER: "전압·전류·저항 조회값",
    DeviceCategory.POWER_SUPPLY: "출력 전압·전류 조회값",
    DeviceCategory.LCR_METER: "L·C·R·Q·D 조회값",
    DeviceCategory.NETWORK_ANALYZER: "S-parameter Trace 또는 Marker 조회값",
    DeviceCategory.UNKNOWN: "장비 조회값",
}

_NUMBER_TOKEN = re.compile(
    r"^[+-]?(?:\d+(?:\.\d*)?|\.\d+)(?:[Ee][+-]?\d+)?$"
)


@dataclass(frozen=True, slots=True)
class _DisplaySample:
    label: str
    raw: str
    parsed: Any
    unit: str
    step_index: int | None
    timestamp: str
    capability_id: str = ""
    response_type: str = ""


def _safe_text(value: Any) -> str:
    return "" if value is None else str(value).strip()


def _finite_number(value: Any) -> float | None:
    if isinstance(value, bool):
        return None
    try:
        converted = float(value)
    except (TypeError, ValueError):
        return None
    return converted if math.isfinite(converted) else None


def _numeric_series(value: Any, raw: str = "") -> tuple[float, ...] | None:
    """Return a real numeric array, never a generated or interpolated trace."""

    if isinstance(value, (tuple, list)):
        converted = tuple(_finite_number(item) for item in value)
        if len(converted) >= 2 and all(item is not None for item in converted):
            return tuple(float(item) for item in converted if item is not None)
        return None

    text = _safe_text(value) or _safe_text(raw)
    if not text:
        return None
    # A single scalar must stay a scalar. Requiring an explicit delimiter also
    # prevents an IDN string or ordinary text from becoming a graph.
    if not any(delimiter in text for delimiter in (",", ";", " ", "\t", "\n")):
        return None
    tokens = [token for token in re.split(r"[,;\s]+", text) if token]
    if len(tokens) < 2 or any(not _NUMBER_TOKEN.fullmatch(token) for token in tokens):
        return None
    numbers = tuple(float(token) for token in tokens)
    return numbers if all(math.isfinite(number) for number in numbers) else None


def _is_plot_capability(
    category: DeviceCategory,
    capability_id: str,
    response_type: str,
) -> bool:
    capability = capability_id.strip().casefold()
    response = response_type.strip().casefold()
    if response not in {"array", "float_array"}:
        return False
    if category is DeviceCategory.SPECTRUM_ANALYZER:
        return capability in {"trace.read", "trace.data.memory"}
    if category is DeviceCategory.OSCILLOSCOPE:
        return capability == "waveform.data"
    if category is DeviceCategory.NETWORK_ANALYZER:
        return capability == "trace.data.formatted"
    return False


def _minmax_decimate(
    values: Sequence[float],
    max_points: int,
) -> tuple[float, ...]:
    """Keep bucket extrema so very large real traces cannot freeze Tk."""

    normalized_limit = max(4, int(max_points))
    if len(values) <= normalized_limit:
        return tuple(values)
    bucket_count = max(2, normalized_limit // 2)
    bucket_size = max(1, math.ceil(len(values) / bucket_count))
    reduced: list[float] = []
    for start in range(0, len(values), bucket_size):
        bucket = values[start : start + bucket_size]
        if not bucket:
            continue
        minimum = min(enumerate(bucket), key=lambda item: item[1])
        maximum = max(enumerate(bucket), key=lambda item: item[1])
        for _index, value in sorted((minimum, maximum)):
            if not reduced or reduced[-1] != value:
                reduced.append(float(value))
    return tuple(reduced[:normalized_limit])


def _scalar_value(value: Any, raw: str = "") -> Any:
    if isinstance(value, (tuple, list)):
        if len(value) == 1:
            return value[0]
        return _safe_text(raw) or str(value)
    text = _safe_text(value)
    if not text:
        text = _safe_text(raw)
    if _NUMBER_TOKEN.fullmatch(text):
        number = float(text)
        if math.isfinite(number):
            return number
    return text


def _format_engineering(value: Any, unit: str = "") -> str:
    return format_engineering_value(value, unit) or "—"


def _feature_for(step: SelectedFeature) -> Any | None:
    try:
        return feature_by_id(step.feature_id, step.instrument.profile_id)
    except (KeyError, ValueError):
        return None


def _step_instrument(step: RoutineStep | Any) -> SelectedInstrument | None:
    instrument = getattr(step, "instrument", None)
    return instrument if isinstance(instrument, SelectedInstrument) else None


class _InstrumentPanel(tk.Frame):
    """One responsive panel backed only by values received from execution."""

    def __init__(self, master: tk.Misc, instrument: SelectedInstrument) -> None:
        super().__init__(
            master,
            background=CARD,
            highlightbackground=BORDER,
            highlightthickness=1,
        )
        self.instrument = instrument
        self.series: tuple[float, ...] | None = None
        self.array_values: tuple[float, ...] | None = None
        self.latest_sample: _DisplaySample | None = None
        self.samples: list[_DisplaySample] = []
        self.settings: list[tuple[str, str]] = []
        self.has_series = False
        self._last_sample_key: tuple[Any, ...] | None = None

        self.status_var = tk.StringVar(value="조회 대기")
        self.value_var = tk.StringVar(value=EMPTY_MESSAGE)
        self.value_detail_var = tk.StringVar(
            value=f"{_CATEGORY_HINTS[instrument.category]}이 들어오면 여기에 표시해요."
        )
        self.raw_var = tk.StringVar(value="원본 응답: —")
        self.settings_var = tk.StringVar(value="장비에 보낸 설정이 아직 없어요.")

        self._build()

    @property
    def trace_item_count(self) -> int:
        return len(self.display_canvas.find_withtag("data_trace"))

    def _build(self) -> None:
        self.columnconfigure(0, weight=1)
        self.rowconfigure(2, weight=1)

        header = tk.Frame(self, background=CARD)
        header.grid(row=0, column=0, sticky="ew", padx=20, pady=(17, 10))
        header.columnconfigure(0, weight=1)
        name = self.instrument.display_name
        if self.instrument.serial.strip():
            name += f" · S/N {self.instrument.serial.strip()}"
        tk.Label(
            header,
            text=name,
            font=("Segoe UI Semibold", 14),
            background=CARD,
            foreground=TEXT,
            anchor="w",
        ).grid(row=0, column=0, sticky="ew")
        tk.Label(
            header,
            text=self.instrument.category.label_ko,
            font=("Segoe UI", 9),
            background=CARD,
            foreground=SUBTEXT,
            anchor="w",
        ).grid(row=1, column=0, sticky="ew", pady=(3, 0))
        tk.Label(
            header,
            textvariable=self.status_var,
            font=("Segoe UI Semibold", 9),
            background=ACCENT_LIGHT,
            foreground=ACCENT_DARK,
            padx=10,
            pady=5,
        ).grid(row=0, column=1, rowspan=2, sticky="e")

        tk.Frame(self, height=1, background=BORDER).grid(
            row=1, column=0, sticky="ew"
        )

        body = tk.Frame(self, background=CARD)
        body.grid(row=2, column=0, sticky="nsew", padx=20, pady=16)
        body.columnconfigure(0, weight=3, minsize=300)
        body.columnconfigure(1, weight=2, minsize=190)
        body.rowconfigure(0, weight=1)

        self.display_canvas = tk.Canvas(
            body,
            background=SCREEN,
            highlightthickness=0,
            height=220,
        )
        self.display_canvas.grid(row=0, column=0, sticky="nsew", padx=(0, 14))
        self.display_canvas.bind("<Configure>", self._redraw)

        side = tk.Frame(body, background=CARD)
        side.grid(row=0, column=1, sticky="nsew")
        side.columnconfigure(0, weight=1)
        tk.Label(
            side,
            text="최근 실제 값",
            font=("Segoe UI Semibold", 10),
            background=CARD,
            foreground=TEXT,
            anchor="w",
        ).grid(row=0, column=0, sticky="ew")
        tk.Label(
            side,
            textvariable=self.value_var,
            font=("Segoe UI Semibold", 15),
            background=CARD,
            foreground=TEXT,
            anchor="w",
            justify="left",
            wraplength=270,
        ).grid(row=1, column=0, sticky="ew", pady=(7, 2))
        tk.Label(
            side,
            textvariable=self.value_detail_var,
            font=("Segoe UI", 9),
            background=CARD,
            foreground=SUBTEXT,
            anchor="nw",
            justify="left",
            wraplength=270,
        ).grid(row=2, column=0, sticky="ew")
        tk.Label(
            side,
            textvariable=self.raw_var,
            font=("Consolas", 8),
            background="#F8F9FA",
            foreground="#4E5968",
            anchor="nw",
            justify="left",
            wraplength=270,
            padx=9,
            pady=8,
        ).grid(row=3, column=0, sticky="ew", pady=(12, 0))
        tk.Label(
            side,
            text="장비에 보낸 설정",
            font=("Segoe UI Semibold", 10),
            background=CARD,
            foreground=TEXT,
            anchor="w",
        ).grid(row=4, column=0, sticky="ew", pady=(14, 3))
        tk.Label(
            side,
            textvariable=self.settings_var,
            font=("Segoe UI", 9),
            background=CARD,
            foreground=SUBTEXT,
            anchor="nw",
            justify="left",
            wraplength=270,
        ).grid(row=5, column=0, sticky="ew")

        self._redraw()

    def mark_status(self, text: str) -> None:
        self.status_var.set(text)

    def apply_setting(
        self,
        label: str,
        arguments: Sequence[tuple[str, str]],
        feature: Any | None = None,
    ) -> None:
        if not arguments:
            return
        values = (
            format_feature_arguments(feature, arguments)
            if feature is not None
            else ", ".join(f"{name}={value}" for name, value in arguments)
        )
        entry = (label, values)
        if entry in self.settings:
            self.settings.remove(entry)
        self.settings.append(entry)
        del self.settings[:-4]
        self.settings_var.set(
            "\n".join(f"• {name}: {value}" for name, value in reversed(self.settings))
        )
        self.status_var.set("설정 명령 전송 완료")

    def apply_sample(self, sample: _DisplaySample) -> None:
        key = (sample.step_index, sample.label, sample.raw, repr(sample.parsed))
        if key == self._last_sample_key:
            return
        self._last_sample_key = key
        self.latest_sample = sample
        self.samples.append(sample)
        del self.samples[:-20]
        self.array_values = _numeric_series(sample.parsed, sample.raw)
        self.series = (
            self.array_values
            if (
                self.array_values is not None
                and _is_plot_capability(
                    self.instrument.category,
                    sample.capability_id,
                    sample.response_type,
                )
            )
            else None
        )
        self.has_series = self.series is not None

        if self.series is not None:
            self.value_var.set(f"{sample.label} · {len(self.series):,} points")
            low = min(self.series)
            high = max(self.series)
            self.value_detail_var.set(
                f"실제 배열 응답 · Min {_format_engineering(low, sample.unit)}"
                f" · Max {_format_engineering(high, sample.unit)}"
            )
        elif self.array_values is not None:
            self.value_var.set(
                format_display_value(self.array_values, sample.unit)
            )
            self.value_detail_var.set(
                f"{sample.label} · 실제 배열 응답 · "
                "그래프 형식이 확인되지 않아 숫자 요약만 표시"
            )
        else:
            scalar = _scalar_value(sample.parsed, sample.raw)
            self.value_var.set(_format_engineering(scalar, sample.unit))
            suffix = f" · {sample.timestamp}" if sample.timestamp else ""
            self.value_detail_var.set(f"{sample.label}{suffix}")

        raw = sample.raw.replace("\r", " ").replace("\n", " ")
        if len(raw) > 280:
            raw = raw[:277] + "…"
        self.raw_var.set(f"원본 응답: {raw or '—'}")
        self.status_var.set("실제 조회값 수신")
        self._redraw()

    def _redraw(self, _event: tk.Event | None = None) -> None:
        canvas = self.display_canvas
        canvas.delete("all")
        width = max(canvas.winfo_width(), 280)
        height = max(canvas.winfo_height(), 190)
        pad_x, pad_y = 36, 27
        plot_w = max(width - pad_x * 2, 10)
        plot_h = max(height - pad_y * 2, 10)

        for index in range(6):
            x = pad_x + plot_w * index / 5
            canvas.create_line(
                x, pad_y, x, pad_y + plot_h, fill=SCREEN_GRID, width=1
            )
        for index in range(5):
            y = pad_y + plot_h * index / 4
            canvas.create_line(
                pad_x, y, pad_x + plot_w, y, fill=SCREEN_GRID, width=1
            )

        accent = _CATEGORY_COLORS[self.instrument.category]
        if self.series is not None:
            low, high = min(self.series), max(self.series)
            if math.isclose(low, high):
                spread = max(abs(low) * 0.05, 1.0)
                low -= spread
                high += spread
            points: list[float] = []
            display_series = _minmax_decimate(
                self.series,
                max(200, min(2_000, int(plot_w * 2))),
            )
            count = len(self.series)
            displayed_count = len(display_series)
            for index, value in enumerate(display_series):
                x = pad_x + (
                    plot_w * index / max(displayed_count - 1, 1)
                )
                y = pad_y + (high - value) / (high - low) * plot_h
                points.extend((x, y))
            canvas.create_line(
                *points,
                fill=accent,
                width=2,
                smooth=False,
                tags=("data_trace",),
            )
            if displayed_count <= 200:
                for index in range(0, len(points), 2):
                    canvas.create_oval(
                        points[index] - 1.5,
                        points[index + 1] - 1.5,
                        points[index] + 1.5,
                        points[index + 1] + 1.5,
                        fill=accent,
                        outline="",
                        tags=("data_trace",),
                    )
            canvas.create_text(
                pad_x,
                11,
                text=f"실제 응답 {count:,} points",
                fill=SCREEN_TEXT,
                font=("Segoe UI Semibold", 9),
                anchor="nw",
            )
            canvas.create_text(
                pad_x,
                height - 8,
                text=f"{low:.6g}",
                fill="#8FA5B8",
                font=("Consolas", 8),
                anchor="sw",
            )
            canvas.create_text(
                width - pad_x,
                11,
                text=f"Max {high:.6g}",
                fill="#8FA5B8",
                font=("Consolas", 8),
                anchor="ne",
            )
            return

        if self.latest_sample is not None:
            value_text = (
                format_display_value(
                    self.array_values,
                    self.latest_sample.unit,
                )
                if self.array_values is not None
                else _format_engineering(
                    _scalar_value(
                        self.latest_sample.parsed,
                        self.latest_sample.raw,
                    ),
                    self.latest_sample.unit,
                )
            )
            canvas.create_text(
                width / 2,
                height / 2 - 12,
                text=value_text,
                fill=accent,
                font=("Consolas", max(18, min(32, width // 16)), "bold"),
                anchor="center",
                tags=("actual_scalar",),
            )
            canvas.create_text(
                width / 2,
                height / 2 + 25,
                text=self.latest_sample.label,
                fill=SCREEN_TEXT,
                font=("Segoe UI", 10),
                anchor="center",
            )
            return

        canvas.create_text(
            width / 2,
            height / 2 - 10,
            text=EMPTY_MESSAGE,
            fill=SCREEN_TEXT,
            font=("Segoe UI Semibold", 13),
            anchor="center",
            tags=("empty_message",),
        )
        canvas.create_text(
            width / 2,
            height / 2 + 19,
            text="루틴의 조회 명령이 끝나면 실제 응답만 그려요.",
            fill="#8FA5B8",
            font=("Segoe UI", 9),
            anchor="center",
        )


class InstrumentDisplayWindow(tk.Toplevel):
    """Live execution display that never opens or polls a VISA session."""

    ALL_DEVICES = "__all__"

    def __init__(
        self,
        parent: tk.Misc,
        instruments: Iterable[SelectedInstrument],
        routine_steps: Iterable[RoutineStep] = (),
    ) -> None:
        super().__init__(parent)
        self.title("실제 장비값 디스플레이")
        self.configure(background=BACKGROUND)
        self.minsize(760, 560)
        screen_width = max(800, self.winfo_screenwidth())
        screen_height = max(640, self.winfo_screenheight())
        initial_width = min(1_060, max(760, screen_width - 80))
        initial_height = min(680, max(560, screen_height - 100))
        self.geometry(f"{initial_width}x{initial_height}")
        self.protocol("WM_DELETE_WINDOW", self.destroy)

        self._instruments: tuple[SelectedInstrument, ...] = ()
        self._routine_steps: tuple[RoutineStep, ...] = ()
        self._steps_by_index: dict[int, RoutineStep] = {}
        self._panels: dict[str, _InstrumentPanel] = {}
        self._selector_resources: list[str] = []
        self._reflow_after_id: str | None = None
        self.selected_resource_var = tk.StringVar(value=self.ALL_DEVICES)
        self.selector_label_var = tk.StringVar(value="모든 장비를 함께 보기")
        self.summary_var = tk.StringVar(value="선택된 장비가 없어요.")

        self._build()
        self.set_instruments(instruments, routine_steps)
        self.bind("<Configure>", self._window_configured, add="+")

    @property
    def instruments(self) -> tuple[SelectedInstrument, ...]:
        return self._instruments

    @property
    def routine_steps(self) -> tuple[RoutineStep, ...]:
        return self._routine_steps

    @property
    def panels(self) -> dict[str, _InstrumentPanel]:
        return dict(self._panels)

    def _build(self) -> None:
        self.columnconfigure(0, weight=1)
        self.rowconfigure(2, weight=1)

        header = tk.Frame(self, background=BACKGROUND)
        header.grid(row=0, column=0, sticky="ew", padx=30, pady=(24, 12))
        header.columnconfigure(0, weight=1)
        tk.Label(
            header,
            text="실제 장비값 디스플레이",
            font=("Segoe UI Semibold", 20),
            background=BACKGROUND,
            foreground=TEXT,
        ).grid(row=0, column=0, sticky="w")
        self.header_subtitle = tk.Label(
            header,
            text=(
                "별도 조회를 반복하지 않고, 실행 루틴이 받은 실제 응답만 보여줘요. "
                "숫자 배열 응답이 있을 때만 Trace·Waveform을 그립니다."
            ),
            font=("Segoe UI", 10),
            background=BACKGROUND,
            foreground=SUBTEXT,
            justify="left",
            anchor="w",
            wraplength=820,
        )
        self.header_subtitle.grid(row=1, column=0, sticky="ew", pady=(5, 0))
        tk.Button(
            header,
            text="닫기",
            command=self.destroy,
            font=("Segoe UI Semibold", 9),
            background="#E9ECEF",
            foreground=TEXT,
            activebackground=BORDER,
            relief="flat",
            borderwidth=0,
            padx=15,
            pady=8,
            cursor="hand2",
        ).grid(row=0, column=1, rowspan=2, sticky="e")

        toolbar = tk.Frame(
            self,
            background=CARD,
            highlightbackground=BORDER,
            highlightthickness=1,
        )
        toolbar.grid(row=1, column=0, sticky="ew", padx=30, pady=(0, 12))
        toolbar.columnconfigure(1, weight=1)
        tk.Label(
            toolbar,
            text="화면 선택",
            font=("Segoe UI Semibold", 10),
            background=CARD,
            foreground=TEXT,
        ).grid(row=0, column=0, sticky="w", padx=(16, 10), pady=12)
        self.device_selector = ttk.Combobox(
            toolbar,
            state="readonly",
            textvariable=self.selector_label_var,
            font=("Segoe UI", 10),
        )
        self.device_selector.grid(row=0, column=1, sticky="ew", pady=12)
        self.device_selector.bind("<<ComboboxSelected>>", self._selector_changed)
        tk.Label(
            toolbar,
            textvariable=self.summary_var,
            font=("Segoe UI", 9),
            background=CARD,
            foreground=SUBTEXT,
        ).grid(row=0, column=2, sticky="e", padx=16)

        self.scroll_canvas = tk.Canvas(
            self,
            background=BACKGROUND,
            highlightthickness=0,
        )
        self.scroll_canvas.grid(row=2, column=0, sticky="nsew", padx=(30, 12))
        scrollbar = ttk.Scrollbar(
            self,
            orient="vertical",
            command=self.scroll_canvas.yview,
        )
        scrollbar.grid(row=2, column=1, sticky="ns", padx=(0, 16))
        self.scroll_canvas.configure(yscrollcommand=scrollbar.set)

        self.card_host = tk.Frame(self.scroll_canvas, background=BACKGROUND)
        self._host_window = self.scroll_canvas.create_window(
            (0, 0),
            window=self.card_host,
            anchor="nw",
        )
        self.card_host.bind("<Configure>", self._host_configured)
        self.scroll_canvas.bind("<Configure>", self._canvas_configured)
        self.bind("<MouseWheel>", self._mousewheel, add="+")

    def focus_existing(self) -> bool:
        """Bring this window forward; return False only after it was destroyed."""

        try:
            if not self.winfo_exists():
                return False
            self.deiconify()
            self.lift()
            self.focus_force()
            return True
        except tk.TclError:
            return False

    def set_instruments(
        self,
        instruments: Iterable[SelectedInstrument],
        routine_steps: Iterable[RoutineStep] | None = None,
    ) -> None:
        """Replace displayed devices without performing any instrument I/O."""

        normalized: list[SelectedInstrument] = []
        seen: set[str] = set()
        for instrument in instruments:
            if not isinstance(instrument, SelectedInstrument):
                raise TypeError("instruments에는 SelectedInstrument만 넣을 수 있어요.")
            if instrument.resource in seen:
                continue
            seen.add(instrument.resource)
            normalized.append(instrument)
        self._instruments = tuple(normalized)
        if routine_steps is not None:
            self._routine_steps = tuple(routine_steps)
            self._steps_by_index = {
                index: step
                for index, step in enumerate(self._routine_steps, start=1)
            }

        for panel in self._panels.values():
            panel.destroy()
        self._panels.clear()
        for instrument in self._instruments:
            self._panels[instrument.resource] = _InstrumentPanel(
                self.card_host,
                instrument,
            )

        self._selector_resources = [
            self.ALL_DEVICES,
            *(instrument.resource for instrument in self._instruments),
        ]
        labels = ["모든 장비를 함께 보기"]
        labels.extend(
            f"{instrument.display_name} · {instrument.resource}"
            for instrument in self._instruments
        )
        self.device_selector.configure(values=labels)
        if self._instruments:
            self.device_selector.current(0)
            self.selected_resource_var.set(self.ALL_DEVICES)
            self.summary_var.set(f"선택 장비 {len(self._instruments)}대 · VISA 추가 조회 없음")
        else:
            self.selector_label_var.set("선택된 장비가 없어요")
            self.selected_resource_var.set("")
            self.summary_var.set("선택된 장비가 없어요.")
        self._reflow()

    def set_routine_steps(self, routine_steps: Iterable[RoutineStep]) -> None:
        self._routine_steps = tuple(routine_steps)
        self._steps_by_index = {
            index: step for index, step in enumerate(self._routine_steps, start=1)
        }

    def show_all(self) -> None:
        if self._instruments:
            self.device_selector.current(0)
            self.selected_resource_var.set(self.ALL_DEVICES)
        self._reflow()

    def show_device(self, resource: str) -> bool:
        try:
            index = self._selector_resources.index(resource)
        except ValueError:
            return False
        self.device_selector.current(index)
        self.selected_resource_var.set(resource)
        self._reflow()
        return True

    def update_from_event(self, event: Any) -> None:
        """Apply an execution callback event; this method performs no VISA I/O."""

        step_index = getattr(event, "step_index", None)
        step = self._steps_by_index.get(step_index) if isinstance(step_index, int) else None
        resource = _safe_text(getattr(event, "resource", ""))
        if not resource and step is not None:
            instrument = _step_instrument(step)
            resource = instrument.resource if instrument is not None else ""
        panel = self._panels.get(resource)
        if panel is None:
            return

        kind = _safe_text(getattr(event, "kind", "")).lower()
        response = _safe_text(getattr(event, "response", ""))
        timestamp = _safe_text(getattr(event, "timestamp_utc", ""))
        if kind == "identity_verified":
            panel.mark_status("장비 식별 확인")
            return
        if kind in {"run_failed", "preflight_failed"}:
            panel.mark_status("실행 오류")
            return
        if kind.startswith("safety_shutdown"):
            panel.mark_status("안전 종료 확인 중")
            return

        if not isinstance(step, SelectedFeature):
            return
        feature = _feature_for(step)
        operation = _safe_text(getattr(feature, "operation", "")).lower()
        label = (
            _safe_text(getattr(feature, "display_name", ""))
            or step.result_name.strip()
            or step.feature_id
        )

        if kind == "step_completed" and operation == "set":
            panel.apply_setting(label, step.arguments, feature)
            return
        if kind != "measurement_recorded" or not response:
            return
        parsed_value = getattr(event, "parsed_value", None)
        if parsed_value is None:
            parsed_value = _scalar_value(response, response)
        panel.apply_sample(
            _DisplaySample(
                label=step.result_name.strip() or label,
                raw=response,
                parsed=parsed_value,
                unit=_safe_text(getattr(event, "unit", "")),
                step_index=step_index,
                timestamp=timestamp,
                capability_id=(
                    _safe_text(getattr(event, "capability_id", ""))
                    or _safe_text(getattr(feature, "capability_id", ""))
                ),
                response_type=(
                    _safe_text(getattr(event, "response_type", ""))
                    or _safe_text(getattr(feature, "response_type", ""))
                ),
            )
        )

    def update_from_result(self, result: Any) -> None:
        """Apply the terminal execution snapshot, preferring parsed measurements."""

        dry_run = bool(getattr(result, "dry_run", False))
        result_instruments = tuple(getattr(result, "instruments", ()) or ())
        result_steps = tuple(
            getattr(result, "executed_steps", ())
            or getattr(result, "routine_steps", ())
            or ()
        )
        if result_instruments and not self._instruments:
            self.set_instruments(result_instruments, result_steps)
        elif result_steps:
            self.set_routine_steps(result_steps)

        for record in tuple(getattr(result, "step_records", ()) or ()):
            if _safe_text(getattr(record, "status", "")).lower() != "completed":
                continue
            if _safe_text(getattr(record, "operation", "")).lower() != "set":
                continue
            step_index = getattr(record, "step_index", None)
            step = self._steps_by_index.get(step_index)
            if not isinstance(step, SelectedFeature):
                continue
            resource = _safe_text(getattr(record, "resource", "")) or step.instrument.resource
            panel = self._panels.get(resource)
            if panel is None:
                continue
            feature = _feature_for(step)
            label = _safe_text(getattr(feature, "display_name", "")) or step.feature_id
            panel.apply_setting(label, step.arguments, feature)

        for measurement in tuple(getattr(result, "measurements", ()) or ()):
            resource = _safe_text(getattr(measurement, "resource", ""))
            panel = self._panels.get(resource)
            if panel is None:
                continue
            panel.apply_sample(
                _DisplaySample(
                    label=(
                        _safe_text(getattr(measurement, "result_name", ""))
                        or _safe_text(getattr(measurement, "feature_id", ""))
                        or "측정값"
                    ),
                    raw=_safe_text(getattr(measurement, "raw_response", "")),
                    parsed=getattr(measurement, "parsed_value", ""),
                    unit=_safe_text(getattr(measurement, "unit", "")),
                    step_index=getattr(measurement, "step_index", None),
                    timestamp=_safe_text(getattr(measurement, "timestamp_utc", "")),
                    capability_id=_safe_text(
                        getattr(measurement, "capability_id", "")
                    ),
                    response_type=_safe_text(
                        getattr(measurement, "response_type", "")
                    ),
                )
            )

        status = getattr(result, "status", "")
        status_text = _safe_text(getattr(status, "value", status)).lower()
        status_label = (
            "Dry Run · 실제 조회값 없음"
            if dry_run
            else {
                "completed": "실행 완료",
                "stopped": "사용자 중지",
                "emergency_stopped": "비상정지",
                "failed": "실행 실패",
            }.get(status_text, "결과 반영 완료")
        )
        for panel in self._panels.values():
            panel.mark_status(status_label)

    def _selector_changed(self, _event: tk.Event | None = None) -> None:
        index = self.device_selector.current()
        if 0 <= index < len(self._selector_resources):
            self.selected_resource_var.set(self._selector_resources[index])
        self._reflow()

    def _window_configured(self, event: tk.Event) -> None:
        if event.widget is self:
            self.header_subtitle.configure(
                wraplength=max(430, int(event.width) - 190)
            )

    def _visible_panels(self) -> list[_InstrumentPanel]:
        selected = self.selected_resource_var.get()
        if selected and selected != self.ALL_DEVICES:
            panel = self._panels.get(selected)
            return [panel] if panel is not None else []
        return list(self._panels.values())

    def _reflow(self) -> None:
        for panel in self._panels.values():
            panel.grid_forget()
        visible = self._visible_panels()
        if not visible:
            return
        available = max(self.scroll_canvas.winfo_width(), self.winfo_width() - 80)
        columns = 2 if len(visible) > 1 and available >= 1080 else 1
        for column in range(2):
            self.card_host.columnconfigure(column, weight=1 if column < columns else 0)
        for index, panel in enumerate(visible):
            row, column = divmod(index, columns)
            panel.grid(
                row=row,
                column=column,
                sticky="nsew",
                padx=(0, 12),
                pady=(0, 12),
            )
        self.card_host.update_idletasks()
        self.scroll_canvas.configure(scrollregion=self.scroll_canvas.bbox("all"))

    def _host_configured(self, _event: tk.Event) -> None:
        self.scroll_canvas.configure(scrollregion=self.scroll_canvas.bbox("all"))

    def _canvas_configured(self, event: tk.Event) -> None:
        self.scroll_canvas.itemconfigure(
            self._host_window,
            width=max(int(event.width) - 2, 1),
        )
        if self._reflow_after_id is not None:
            self.after_cancel(self._reflow_after_id)
        self._reflow_after_id = self.after(60, self._finish_reflow)

    def _finish_reflow(self) -> None:
        self._reflow_after_id = None
        if self.winfo_exists():
            self._reflow()

    def _mousewheel(self, event: tk.Event) -> None:
        try:
            if self.winfo_containing(event.x_root, event.y_root) is not None:
                self.scroll_canvas.yview_scroll(int(-event.delta / 120), "units")
        except tk.TclError:
            pass
