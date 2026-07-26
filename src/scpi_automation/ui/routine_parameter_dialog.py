from __future__ import annotations

import tkinter as tk
from decimal import Decimal, InvalidOperation
from tkinter import ttk
from typing import Callable

from scpi_automation.binding_registry import (
    PlanBindingDefinition,
    plan_binding_definition,
)
from scpi_automation.routine import (
    FeatureRisk,
    PlanArgumentBinding,
    RoutineFeature,
    RoutineParameter,
    SelectedFeature,
    SelectedInstrument,
    select_feature,
)


BACKGROUND = "#F4F6F8"
CARD = "#FFFFFF"
TEXT = "#191F28"
SUBTEXT = "#6B7684"
BORDER = "#E5E8EB"
ACCENT = "#3182F6"
ACCENT_DARK = "#1B64DA"
ACCENT_LIGHT = "#EAF3FF"
WARNING = "#D97706"
WARNING_LIGHT = "#FFF4E5"
DANGER = "#D92D20"
DANGER_LIGHT = "#FDECEC"
NEUTRAL_LIGHT = "#F2F4F6"


_NUMERIC_VALUE_TYPES = frozenset(
    {
        "float",
        "number",
        "integer",
        "number_or_auto",
        "float_or_enum",
        "float_or_mnemonic",
        "integer_or_mnemonic",
        "float_or_string",
    }
)

# Every scale is relative to the catalog/profile unit.  The value handed to
# select_feature therefore remains in the original base unit and continues
# through the existing range and exact-probe validation unchanged.
_UNIT_FAMILIES: dict[
    str,
    tuple[str, tuple[tuple[str, Decimal], ...], str],
] = {
    "hz": (
        "Hz",
        (
            ("Hz", Decimal("1")),
            ("kHz", Decimal("1e3")),
            ("MHz", Decimal("1e6")),
            ("GHz", Decimal("1e9")),
        ),
        "예: 1 + GHz = 1,000,000,000 Hz",
    ),
    "s": (
        "s",
        (
            ("s", Decimal("1")),
            ("ms", Decimal("1e-3")),
            ("µs", Decimal("1e-6")),
        ),
        "예: 500 + ms = 0.5 s",
    ),
    "v": (
        "V",
        (
            ("µV", Decimal("1e-6")),
            ("mV", Decimal("1e-3")),
            ("V", Decimal("1")),
            ("kV", Decimal("1e3")),
        ),
        "예: 500 + mV = 0.5 V",
    ),
    "a": (
        "A",
        (
            ("nA", Decimal("1e-9")),
            ("µA", Decimal("1e-6")),
            ("mA", Decimal("1e-3")),
            ("A", Decimal("1")),
        ),
        "예: 250 + mA = 0.25 A",
    ),
    "w": (
        "W",
        (
            ("µW", Decimal("1e-6")),
            ("mW", Decimal("1e-3")),
            ("W", Decimal("1")),
            ("kW", Decimal("1e3")),
        ),
        "예: 10 + mW = 0.01 W",
    ),
    "ohm": (
        "Ohm",
        (
            ("mΩ", Decimal("1e-3")),
            ("Ω", Decimal("1")),
            ("kΩ", Decimal("1e3")),
            ("MΩ", Decimal("1e6")),
        ),
        "예: 50 + Ω = 50 Ohm",
    ),
}

_UNIT_ALIASES = {
    "hz": "hz",
    "s": "s",
    "sec": "s",
    "second": "s",
    "seconds": "s",
    "v": "v",
    "a": "a",
    "w": "w",
    "ohm": "ohm",
    "ohms": "ohm",
    "ω": "ohm",
    "Ω": "ohm",
}


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
        padx=18,
        pady=9,
    )


class RoutineParameterDialog(tk.Toplevel):
    """Collect operation arguments without exposing raw SCPI as the main UI."""

    def __init__(
        self,
        master: tk.Misc,
        *,
        instrument: SelectedInstrument,
        feature: RoutineFeature,
        on_add: Callable[[SelectedFeature], None],
    ) -> None:
        super().__init__(master)
        self.instrument = instrument
        self.feature = feature
        self._on_add = on_add
        self._value_vars: dict[str, tk.StringVar] = {}
        self._choice_display_to_value: dict[str, dict[str, str]] = {}
        self._unit_vars: dict[str, tk.StringVar] = {}
        self._unit_widgets: dict[str, ttk.Combobox] = {}
        self._value_widgets: dict[str, tk.Misc] = {}
        self._binding_vars: dict[str, tk.BooleanVar] = {}
        self._binding_definitions: dict[str, PlanBindingDefinition] = {}
        self._previous_units: dict[str, str] = {}
        self._parameters_by_name = {
            parameter.name: parameter
            for parameter in feature.parameters
        }
        for parameter in feature.parameters:
            definition = plan_binding_definition(
                feature.capability_id,
                feature.operation,
                parameter.name,
            )
            if definition is not None:
                self._binding_definitions[parameter.name] = definition
        self.result_name_var = tk.StringVar(
            value=(
                self._default_result_name(feature)
                if feature.operation == "query"
                else ""
            )
        )
        self.confirm_risk_var = tk.BooleanVar(value=False)
        self.status_var = tk.StringVar(
            value="필요한 값을 입력한 뒤 루틴에 추가해 주세요."
        )
        self.command_visible = False

        self.title("기능 상세 설정")
        screen_width = max(640, self.winfo_screenwidth())
        screen_height = max(560, self.winfo_screenheight())
        dialog_width = min(700, max(560, int(screen_width * 0.55)))
        dialog_height = min(720, max(500, int(screen_height * 0.80)))
        self.geometry(f"{dialog_width}x{dialog_height}")
        self.minsize(560, 500)
        self.configure(background=BACKGROUND)
        self.transient(master.winfo_toplevel())
        self.protocol("WM_DELETE_WINDOW", self.destroy)

        self._build()
        self.after_idle(self._activate)

    def _activate(self) -> None:
        try:
            self.grab_set()
            self.focus_force()
        except tk.TclError:
            pass

    def _build(self) -> None:
        shell = tk.Frame(
            self,
            background=CARD,
            highlightbackground=BORDER,
            highlightthickness=1,
        )
        shell.pack(fill="both", expand=True, padx=18, pady=18)
        shell.columnconfigure(0, weight=1)
        shell.rowconfigure(3, weight=1)

        header = tk.Frame(shell, background=CARD)
        header.grid(row=0, column=0, sticky="ew", padx=22, pady=(20, 8))
        header.columnconfigure(0, weight=1)
        tk.Label(
            header,
            text=self.feature.display_name,
            font=("Segoe UI Semibold", 16),
            background=CARD,
            foreground=TEXT,
            anchor="w",
            justify="left",
            wraplength=520,
        ).grid(row=0, column=0, sticky="w")
        tk.Label(
            header,
            text=f"{self.instrument.display_name} · {self.feature.group or '기능'}",
            font=("Segoe UI", 9),
            background=CARD,
            foreground=SUBTEXT,
        ).grid(row=1, column=0, sticky="w", pady=(5, 0))

        tk.Label(
            shell,
            text=self.feature.description,
            font=("Segoe UI", 9),
            background="#F8FAFC",
            foreground=SUBTEXT,
            anchor="w",
            justify="left",
            wraplength=570,
            padx=12,
            pady=9,
        ).grid(row=1, column=0, sticky="ew", padx=22, pady=(0, 10))

        if self.feature.risk is FeatureRisk.HAZARDOUS:
            risk = tk.Frame(shell, background=DANGER_LIGHT, padx=12, pady=9)
            risk.grid(row=2, column=0, sticky="ew", padx=22, pady=(0, 10))
            risk.columnconfigure(0, weight=1)
            tk.Label(
                risk,
                text="출력·전압·전류·전력에 영향을 줄 수 있는 기능이에요.",
                font=("Segoe UI Semibold", 9),
                background=DANGER_LIGHT,
                foreground=DANGER,
                anchor="w",
            ).grid(row=0, column=0, sticky="w")
            tk.Checkbutton(
                risk,
                text="시험 한계와 배선 상태를 확인했어요",
                variable=self.confirm_risk_var,
                font=("Segoe UI", 9),
                background=DANGER_LIGHT,
                foreground=TEXT,
                activebackground=DANGER_LIGHT,
                selectcolor=DANGER_LIGHT,
            ).grid(row=1, column=0, sticky="w", pady=(5, 0))

        body_shell = tk.Frame(shell, background=CARD)
        body_shell.grid(row=3, column=0, sticky="nsew", padx=22)
        body_shell.columnconfigure(0, weight=1)
        body_shell.rowconfigure(0, weight=1)
        canvas = tk.Canvas(
            body_shell,
            background=CARD,
            highlightthickness=0,
        )
        scrollbar = ttk.Scrollbar(
            body_shell,
            orient="vertical",
            command=canvas.yview,
        )
        canvas.configure(yscrollcommand=scrollbar.set)
        canvas.grid(row=0, column=0, sticky="nsew")
        scrollbar.grid(row=0, column=1, sticky="ns")
        form = tk.Frame(canvas, background=CARD)
        window = canvas.create_window((0, 0), window=form, anchor="nw")
        form.columnconfigure(1, weight=1)
        form.bind(
            "<Configure>",
            lambda _event: canvas.configure(scrollregion=canvas.bbox("all")),
        )
        canvas.bind(
            "<Configure>",
            lambda event: canvas.itemconfigure(window, width=event.width),
        )

        if not self.feature.parameters:
            tk.Label(
                form,
                text="이 기능은 추가로 입력할 값이 없어요.",
                font=("Segoe UI", 10),
                background=NEUTRAL_LIGHT,
                foreground=SUBTEXT,
                padx=12,
                pady=12,
            ).grid(row=0, column=0, columnspan=3, sticky="ew")
            next_row = 1
        else:
            for row, parameter in enumerate(self.feature.parameters):
                self._build_parameter_row(form, row, parameter)
            next_row = len(self.feature.parameters)

        if self.feature.operation == "query":
            tk.Label(
                form,
                text="Result Name - 결과 저장 이름",
                font=("Segoe UI Semibold", 9),
                background=CARD,
                foreground=TEXT,
            ).grid(row=next_row, column=0, sticky="w", pady=(10, 5))
            ttk.Entry(
                form,
                textvariable=self.result_name_var,
            ).grid(
                row=next_row,
                column=1,
                sticky="ew",
                padx=(12, 8),
                pady=(10, 5),
            )
            tk.Label(
                form,
                text="변수",
                font=("Segoe UI", 8),
                background=CARD,
                foreground=SUBTEXT,
            ).grid(row=next_row, column=2, sticky="w", pady=(10, 5))
            next_row += 1

        command_card = tk.Frame(
            form,
            background=NEUTRAL_LIGHT,
            padx=10,
            pady=8,
        )
        command_card.grid(
            row=next_row,
            column=0,
            columnspan=3,
            sticky="ew",
            pady=(12, 3),
        )
        command_card.columnconfigure(0, weight=1)
        self.command_label = tk.Label(
            command_card,
            text="필요하면 사용될 명령 형식을 확인할 수 있어요.",
            font=("Consolas", 8),
            background=NEUTRAL_LIGHT,
            foreground=SUBTEXT,
            anchor="w",
        )
        self.command_label.grid(row=0, column=0, sticky="ew")
        _button(
            command_card,
            text="명령 보기",
            command=self._toggle_command,
        ).grid(row=0, column=1, padx=(8, 0))

        footer = tk.Frame(shell, background=CARD)
        footer.grid(row=4, column=0, sticky="ew", padx=22, pady=(10, 20))
        footer.columnconfigure(0, weight=1)
        tk.Label(
            footer,
            textvariable=self.status_var,
            font=("Segoe UI", 8),
            background=CARD,
            foreground=WARNING,
            anchor="w",
            wraplength=360,
        ).grid(row=0, column=0, sticky="w")
        _button(
            footer,
            text="취소",
            command=self.destroy,
        ).grid(row=0, column=1, padx=(8, 0))
        _button(
            footer,
            text="루틴에 추가",
            command=self._apply,
            primary=True,
        ).grid(row=0, column=2, padx=(8, 0))

    def _build_parameter_row(
        self,
        parent: tk.Frame,
        row: int,
        parameter: RoutineParameter,
    ) -> None:
        tk.Label(
            parent,
            text=self._parameter_label(parameter),
            font=("Segoe UI Semibold", 9),
            background=CARD,
            foreground=TEXT,
        ).grid(row=row, column=0, sticky="w", pady=5)

        choices = self._parameter_choices(parameter)
        default_value = self._default_value(parameter)
        if choices:
            display_pairs = tuple(
                (self._friendly_choice(parameter, choice), choice)
                for choice in choices
            )
            self._choice_display_to_value[parameter.name] = dict(
                display_pairs
            )
            display_choices = tuple(
                display for display, _choice in display_pairs
            )
            display_default = next(
                (
                    display
                    for display, choice in display_pairs
                    if choice == default_value
                ),
                display_choices[0],
            )
            variable = tk.StringVar(value=display_default)
        else:
            display_choices = ()
            variable = tk.StringVar(value=default_value)
        self._value_vars[parameter.name] = variable
        if choices:
            widget: tk.Misc = ttk.Combobox(
                parent,
                textvariable=variable,
                values=display_choices,
                state="readonly",
            )
        else:
            unit_family = self._unit_family(parameter)
            if unit_family is None:
                widget = ttk.Entry(parent, textvariable=variable)
            else:
                input_shell = tk.Frame(parent, background=CARD)
                input_shell.columnconfigure(0, weight=1)
                widget = ttk.Entry(input_shell, textvariable=variable)
                widget.grid(row=0, column=0, sticky="ew")
                unit_names = tuple(
                    unit_name
                    for unit_name, _scale in unit_family[1]
                )
                default_unit = self._default_display_unit(
                    parameter,
                    unit_family,
                )
                unit_var = tk.StringVar(value=default_unit)
                unit_widget = ttk.Combobox(
                    input_shell,
                    textvariable=unit_var,
                    values=unit_names,
                    state="readonly",
                    width=max(5, max(map(len, unit_names))),
                )
                unit_widget.grid(row=0, column=1, padx=(6, 0))
                unit_widget.bind(
                    "<<ComboboxSelected>>",
                    lambda _event, name=parameter.name: (
                        self._on_unit_changed(name)
                    ),
                )
                self._unit_vars[parameter.name] = unit_var
                self._unit_widgets[parameter.name] = unit_widget
                self._previous_units[parameter.name] = default_unit
                input_shell.grid(
                    row=row,
                    column=1,
                    sticky="ew",
                    padx=(12, 8),
                    pady=5,
                )
                self._value_widgets[parameter.name] = widget
        if not choices and self._unit_family(parameter) is None:
            widget.grid(
                row=row,
                column=1,
                sticky="ew",
                padx=(12, 8),
                pady=5,
            )
        elif choices:
            widget.grid(
                row=row,
                column=1,
                sticky="ew",
                padx=(12, 8),
                pady=5,
            )
        self._value_widgets.setdefault(parameter.name, widget)
        side = tk.Frame(parent, background=CARD)
        side.grid(row=row, column=2, sticky="w", pady=5)
        definition = self._binding_definitions.get(parameter.name)
        if definition is not None:
            binding_var = tk.BooleanVar(value=True)
            self._binding_vars[parameter.name] = binding_var
            tk.Checkbutton(
                side,
                text="시험 계획에서 가져오기",
                variable=binding_var,
                command=lambda name=parameter.name: self._sync_binding_state(name),
                font=("Segoe UI Semibold", 8),
                background=ACCENT_LIGHT,
                foreground=ACCENT_DARK,
                activebackground=ACCENT_LIGHT,
                activeforeground=ACCENT_DARK,
                selectcolor=ACCENT_LIGHT,
                takefocus=True,
            ).grid(row=0, column=0, sticky="w")
            hint_text = (
                f"{definition.label_ko}\n"
                "체크 해제 시에만 이 루틴에 고정값을 저장해요."
            )
        else:
            hint_text = self._parameter_hint(parameter)
        tk.Label(
            side,
            text=hint_text,
            font=("Segoe UI", 8),
            background=CARD,
            foreground=SUBTEXT,
            anchor="w",
            justify="left",
            wraplength=210,
        ).grid(
            row=1 if definition is not None else 0,
            column=0,
            sticky="w",
            pady=(2, 0) if definition is not None else 0,
        )
        if definition is not None:
            self.after_idle(
                lambda name=parameter.name: self._sync_binding_state(name)
            )

    def _sync_binding_state(self, parameter_name: str) -> None:
        bound = self._binding_vars[parameter_name].get()
        widget = self._value_widgets[parameter_name]
        try:
            if isinstance(widget, ttk.Combobox):
                widget.configure(state="disabled" if bound else "readonly")
            else:
                widget.configure(state="disabled" if bound else "normal")
            unit_widget = self._unit_widgets.get(parameter_name)
            if unit_widget is not None:
                unit_widget.configure(
                    state="disabled" if bound else "readonly"
                )
        except tk.TclError:
            pass

    @staticmethod
    def _parameter_label(parameter: RoutineParameter) -> str:
        friendly = {
            "value": "Value - 설정값",
            "state": "State - 상태",
            "channel": "Channel - 채널",
            "marker": "Marker - 마커 번호",
            "trace": "Trace - 트레이스 번호",
            "port": "Port - 포트 번호",
            "mode": "Mode - 동작 방식",
            "source": "Source - 소스",
            "format": "Format - 데이터 형식",
        }
        return friendly.get(
            parameter.name,
            f"{parameter.name.replace('_', ' ').title()} - 입력값",
        )

    @staticmethod
    def _default_result_name(feature: RoutineFeature) -> str:
        display_name = feature.display_name.strip()
        if " - " in display_name:
            leading, friendly = display_name.rsplit(" - ", 1)
            if (
                friendly.strip() in {"값 읽기", "값 조회", "읽기", "조회"}
                and any("\uac00" <= char <= "\ud7a3" for char in leading)
            ):
                display_name = leading.removesuffix(" Read").strip()
            else:
                display_name = friendly.strip()
        for suffix in (" 읽기", " 조회"):
            if display_name.endswith(suffix):
                display_name = display_name.removesuffix(suffix).strip()
                break
        if not display_name:
            display_name = feature.capability_id.replace(".", " ").strip()
        if display_name.endswith("결과"):
            return display_name
        return f"{display_name} 결과"

    @staticmethod
    def _default_value(parameter: RoutineParameter) -> str:
        choices = RoutineParameterDialog._parameter_choices(parameter)
        if parameter.name == "state":
            for safe in ("false", "OFF", "0"):
                if safe in choices:
                    return safe
        if choices:
            return choices[0]
        if parameter.name in {"channel", "marker", "trace", "port"}:
            return str(parameter.minimum if parameter.minimum is not None else 1)
        if (
            parameter.minimum is not None
            and parameter.maximum is not None
            and parameter.minimum <= 0 <= parameter.maximum
        ):
            return "0"
        return ""

    @staticmethod
    def _parameter_choices(parameter: RoutineParameter) -> tuple[str, ...]:
        if parameter.choices:
            return parameter.choices
        if parameter.mapping:
            return tuple(key for key, _value in parameter.mapping)
        if parameter.value_type == "boolean":
            return ("false", "true")
        return ()

    @staticmethod
    def _friendly_choice(
        parameter: RoutineParameter,
        value: str,
    ) -> str:
        normalized = value.strip().upper()
        labels = {
            "FALSE": "끄기 (OFF · false)",
            "OFF": "끄기 (OFF)",
            "TRUE": "켜기 (ON · true)",
            "ON": "켜기 (ON)",
            "WRIT": "Clear Write - 새로 쓰기 (WRIT)",
            "MAXH": "Max Hold - 최댓값 유지 (MAXH)",
            "MINH": "Min Hold - 최솟값 유지 (MINH)",
            "AVER": "Average - 평균 (AVER)",
            "VIEW": "View - 표시 유지 (VIEW)",
            "BLAN": "Blank - 숨기기 (BLAN)",
            "AUTO": "자동 (AUTO)",
            "MIN": "최솟값 (MIN)",
            "MAX": "최댓값 (MAX)",
        }
        if parameter.name == "state":
            if normalized == "0":
                return "끄기 (OFF · 0)"
            if normalized == "1":
                return "켜기 (ON · 1)"
        return labels.get(normalized, value)

    @staticmethod
    def _parameter_hint(parameter: RoutineParameter) -> str:
        unit_family = RoutineParameterDialog._unit_family(parameter)
        if unit_family is not None:
            minimum = (
                RoutineParameterDialog._format_engineering_value(
                    parameter.minimum,
                    unit_family,
                )
                if parameter.minimum is not None
                else ""
            )
            maximum = (
                RoutineParameterDialog._format_engineering_value(
                    parameter.maximum,
                    unit_family,
                )
                if parameter.maximum is not None
                else ""
            )
            if minimum and maximum:
                allowed = f"허용 범위 {minimum} ~ {maximum}"
            elif minimum:
                allowed = f"허용 최솟값 {minimum}"
            elif maximum:
                allowed = f"허용 최댓값 {maximum}"
            else:
                allowed = f"기준 단위 {unit_family[0]}"
            return f"{allowed}\n{unit_family[2]}"

        bounds: list[str] = []
        if parameter.minimum is not None:
            bounds.append(f"최소 {parameter.minimum:g}")
        if parameter.maximum is not None:
            bounds.append(f"최대 {parameter.maximum:g}")
        if parameter.unit:
            bounds.append(parameter.unit)
        return " · ".join(bounds) or parameter.value_type

    @staticmethod
    def _unit_family(
        parameter: RoutineParameter,
    ) -> tuple[str, tuple[tuple[str, Decimal], ...], str] | None:
        if parameter.value_type not in _NUMERIC_VALUE_TYPES:
            return None
        family_name = _UNIT_ALIASES.get(parameter.unit.strip().casefold())
        if family_name is None:
            return None
        return _UNIT_FAMILIES[family_name]

    @staticmethod
    def _default_display_unit(
        parameter: RoutineParameter,
        unit_family: tuple[
            str,
            tuple[tuple[str, Decimal], ...],
            str,
        ],
    ) -> str:
        base_unit, units, _example = unit_family
        if base_unit != "Hz":
            return next(
                name
                for name, scale in units
                if scale == Decimal("1")
            )
        reference = parameter.maximum
        if reference is None:
            reference = parameter.minimum
        if reference is None:
            return "MHz"
        magnitude = abs(Decimal(str(reference)))
        suitable = [
            name
            for name, scale in units
            if scale <= magnitude and scale >= Decimal("1")
        ]
        return suitable[-1] if suitable else "Hz"

    @staticmethod
    def _scale_for_unit(
        unit_family: tuple[
            str,
            tuple[tuple[str, Decimal], ...],
            str,
        ],
        unit_name: str,
    ) -> Decimal:
        try:
            return dict(unit_family[1])[unit_name]
        except KeyError as exc:
            raise ValueError(f"지원하지 않는 표시 단위입니다: {unit_name}") from exc

    @staticmethod
    def _parse_decimal(value: str) -> Decimal:
        try:
            number = Decimal(value.strip())
        except InvalidOperation as exc:
            raise ValueError("숫자나 과학 표기법(예: 1e6)으로 입력해 주세요.") from exc
        if not number.is_finite():
            raise ValueError("유한한 숫자를 입력해 주세요.")
        return number

    @staticmethod
    def _format_decimal(value: Decimal) -> str:
        if not value:
            return "0"
        normalized = value.normalize()
        adjusted = normalized.adjusted()
        if -12 <= adjusted <= 18:
            return format(normalized, "f")
        return format(normalized, "E")

    @staticmethod
    def _format_engineering_value(
        value: float | int,
        unit_family: tuple[
            str,
            tuple[tuple[str, Decimal], ...],
            str,
        ],
    ) -> str:
        number = Decimal(str(value))
        magnitude = abs(number)
        units = unit_family[1]
        if not magnitude:
            unit_name, scale = next(
                (name, factor)
                for name, factor in units
                if factor == Decimal("1")
            )
        else:
            suitable = [
                (name, factor)
                for name, factor in units
                if factor <= magnitude
            ]
            if suitable:
                unit_name, scale = max(
                    suitable,
                    key=lambda item: item[1],
                )
            else:
                unit_name, scale = min(
                    units,
                    key=lambda item: item[1],
                )
        return (
            f"{RoutineParameterDialog._format_decimal(number / scale)} "
            f"{unit_name}"
        )

    @classmethod
    def _normalize_display_value(
        cls,
        value: str,
        unit_name: str,
        parameter: RoutineParameter,
    ) -> str:
        unit_family = cls._unit_family(parameter)
        if unit_family is None:
            return value.strip()
        stripped = value.strip()
        try:
            number = cls._parse_decimal(stripped)
        except ValueError:
            # Mnemonics such as AUTO/MIN/MAX still belong to the established
            # select_feature validation path and must not be rewritten here.
            return stripped
        scale = cls._scale_for_unit(unit_family, unit_name)
        return cls._format_decimal(number * scale)

    def _on_unit_changed(self, parameter_name: str) -> None:
        parameter = self._parameters_by_name[parameter_name]
        unit_family = self._unit_family(parameter)
        if unit_family is None:
            return
        unit_var = self._unit_vars[parameter_name]
        new_unit = unit_var.get()
        old_unit = self._previous_units[parameter_name]
        self._previous_units[parameter_name] = new_unit
        if new_unit == old_unit:
            return
        current = self._value_vars[parameter_name].get().strip()
        if not current:
            return
        try:
            number = self._parse_decimal(current)
        except ValueError:
            # AUTO and other supported mnemonics are unit-independent.
            return
        base_value = number * self._scale_for_unit(unit_family, old_unit)
        converted = base_value / self._scale_for_unit(
            unit_family,
            new_unit,
        )
        self._value_vars[parameter_name].set(
            self._format_decimal(converted)
        )

    def _toggle_command(self) -> None:
        self.command_visible = not self.command_visible
        self.command_label.configure(
            text=(
                self.feature.scpi_preview
                if self.command_visible
                else "필요하면 사용될 명령 형식을 확인할 수 있어요."
            ),
            foreground=TEXT if self.command_visible else SUBTEXT,
        )

    def _apply(self) -> None:
        if (
            self.feature.risk is FeatureRisk.HAZARDOUS
            and not self.confirm_risk_var.get()
        ):
            self.status_var.set("출력 주의사항을 확인한 뒤 체크해 주세요.")
            return
        arguments: dict[str, str] = {}
        plan_bindings: list[PlanArgumentBinding] = []
        for name, variable in self._value_vars.items():
            if self._binding_vars.get(name) is not None and self._binding_vars[
                name
            ].get():
                definition = self._binding_definitions[name]
                plan_bindings.append(
                    PlanArgumentBinding(
                        parameter_name=name,
                        field_id=definition.field_id,
                    )
                )
                continue
            parameter = self._parameters_by_name[name]
            unit_var = self._unit_vars.get(name)
            entered_value = self._choice_display_to_value.get(
                name,
                {},
            ).get(variable.get(), variable.get())
            try:
                arguments[name] = (
                    self._normalize_display_value(
                        entered_value,
                        unit_var.get(),
                        parameter,
                    )
                    if unit_var is not None
                    else entered_value
                )
            except ValueError as exc:
                self.status_var.set(f"{self._parameter_label(parameter)}: {exc}")
                return
        try:
            selected = select_feature(
                self.instrument,
                self.feature.feature_id,
                arguments=arguments,
                plan_bindings=plan_bindings,
                result_name=self.result_name_var.get(),
            )
        except ValueError as exc:
            self.status_var.set(str(exc))
            return
        self._on_add(selected)
        self.destroy()
