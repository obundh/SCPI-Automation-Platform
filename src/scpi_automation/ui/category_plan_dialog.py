from __future__ import annotations

import tkinter as tk
from decimal import Decimal, InvalidOperation
from tkinter import ttk
from typing import Any, Callable

from scpi_automation.planning import (
    COMMON_PLAN_FIELDS,
    PLAN_ASSISTANCE_NOTICE_KO,
    CategoryPlanTemplate,
    GenericPlanItem,
    PlanFieldDefinition,
    PlanFieldType,
    PlanMethodTemplate,
    template_for_instrument,
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
WARNING = "#B45309"
WARNING_LIGHT = "#FFF4E5"
SUCCESS = "#087443"

_PLAN_UNIT_OPTIONS: dict[str, tuple[tuple[str, Decimal], ...]] = {
    "Hz": (
        ("Hz", Decimal("1")),
        ("kHz", Decimal("1e3")),
        ("MHz", Decimal("1e6")),
        ("GHz", Decimal("1e9")),
    ),
    "s": (
        ("ns", Decimal("1e-9")),
        ("µs", Decimal("1e-6")),
        ("ms", Decimal("1e-3")),
        ("s", Decimal("1")),
    ),
    "V": (
        ("µV", Decimal("1e-6")),
        ("mV", Decimal("1e-3")),
        ("V", Decimal("1")),
        ("kV", Decimal("1e3")),
    ),
    "A": (
        ("nA", Decimal("1e-9")),
        ("µA", Decimal("1e-6")),
        ("mA", Decimal("1e-3")),
        ("A", Decimal("1")),
    ),
    "W": (
        ("µW", Decimal("1e-6")),
        ("mW", Decimal("1e-3")),
        ("W", Decimal("1")),
        ("kW", Decimal("1e3")),
    ),
    "Ω": (
        ("mΩ", Decimal("1e-3")),
        ("Ω", Decimal("1")),
        ("kΩ", Decimal("1e3")),
        ("MΩ", Decimal("1e6")),
    ),
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
        takefocus=True,
    )


class CategoryPlanDialog(tk.Toplevel):
    """Build one validated planning-aid item for any supported device category."""

    def __init__(
        self,
        master: tk.Misc,
        *,
        instruments: tuple[SelectedInstrument, ...],
        on_add: Callable[[GenericPlanItem], bool],
        initial_instrument: SelectedInstrument | None = None,
    ) -> None:
        super().__init__(master)
        if not isinstance(instruments, tuple):
            raise TypeError("상세 계획 장비는 SelectedInstrument 튜플이어야 합니다.")
        supported: list[SelectedInstrument] = []
        for instrument in instruments:
            if not isinstance(instrument, SelectedInstrument):
                raise TypeError("상세 계획 장비는 SelectedInstrument여야 합니다.")
            try:
                template_for_instrument(instrument)
            except KeyError:
                continue
            supported.append(instrument)
        if not supported:
            raise ValueError("분류별 상세 계획에 사용할 지원 장비가 없습니다.")

        self._instruments = tuple(supported)
        self._on_add = on_add
        self._template: CategoryPlanTemplate | None = None
        self._method: PlanMethodTemplate | None = None
        self._common_variables: dict[str, tk.Variable] = {}
        self._common_text_widgets: dict[str, tk.Text] = {}
        self._detail_variables: dict[str, tk.Variable] = {}
        self._detail_text_widgets: dict[str, tk.Text] = {}
        self._field_widgets: dict[str, tk.Misc] = {}
        self._field_unit_vars: dict[str, tk.StringVar] = {}
        self._field_previous_units: dict[str, str] = {}
        self._visible_detail_fields: tuple[PlanFieldDefinition, ...] = ()

        self.title("장비 분류별 측정 계획 상세 설정")
        screen_width = max(800, self.winfo_screenwidth())
        screen_height = max(650, self.winfo_screenheight())
        dialog_width = min(1_020, max(760, int(screen_width * 0.74)))
        dialog_height = min(820, max(600, int(screen_height * 0.86)))
        self.geometry(f"{dialog_width}x{dialog_height}")
        self.minsize(760, 600)
        self.configure(background=BACKGROUND)
        self.transient(master.winfo_toplevel())
        self.protocol("WM_DELETE_WINDOW", self._cancel)

        self.device_var = tk.StringVar()
        self.method_var = tk.StringVar()
        self.category_summary_var = tk.StringVar()
        self.standard_examples_var = tk.StringVar()
        self.method_summary_var = tk.StringVar()
        self.method_steps_var = tk.StringVar()
        self.expected_results_var = tk.StringVar()
        self.assistance_ack_var = tk.BooleanVar(value=False)
        self.status_var = tk.StringVar(
            value="필수 항목과 안전 확인을 입력한 뒤 계획에 추가해 주세요."
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
        self._sync_scrollregion()
        self._activate_dialog()

    @property
    def visible_detail_field_ids(self) -> tuple[str, ...]:
        return tuple(field.field_id for field in self._visible_detail_fields)

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
        header.grid(row=0, column=0, sticky="ew", padx=28, pady=(22, 12))
        header.columnconfigure(0, weight=1)
        tk.Label(
            header,
            text="장비 종류에 맞는 측정 계획을 구체화해 볼게요",
            font=("Segoe UI Semibold", 18),
            background=BACKGROUND,
            foreground=TEXT,
        ).grid(row=0, column=0, sticky="w")
        tk.Label(
            header,
            text=(
                "통상적인 시험 흐름과 놓치기 쉬운 조건을 체크하되, "
                "실행 명령이나 표준 판정은 만들지 않아요."
            ),
            font=("Segoe UI", 9),
            background=BACKGROUND,
            foreground=SUBTEXT,
        ).grid(row=1, column=0, sticky="w", pady=(4, 0))

        notice = tk.Frame(
            self,
            background=WARNING_LIGHT,
            highlightbackground="#F1D6A8",
            highlightthickness=1,
        )
        notice.grid(row=1, column=0, sticky="ew", padx=28, pady=(0, 10))
        notice.columnconfigure(1, weight=1)
        tk.Label(
            notice,
            text="주의",
            font=("Segoe UI Semibold", 9),
            background=WARNING_LIGHT,
            foreground=WARNING,
            padx=10,
            pady=7,
        ).grid(row=0, column=0, sticky="nw", padx=(7, 0), pady=6)
        self.assistance_notice_label = tk.Label(
            notice,
            text=PLAN_ASSISTANCE_NOTICE_KO,
            font=("Segoe UI", 9),
            background=WARNING_LIGHT,
            foreground=WARNING,
            justify="left",
            anchor="w",
            wraplength=760,
        )
        self.assistance_notice_label.grid(
            row=0,
            column=1,
            sticky="ew",
            padx=(6, 14),
            pady=10,
        )

        selection = tk.Frame(
            self,
            background=CARD,
            highlightbackground=BORDER,
            highlightthickness=1,
        )
        selection.grid(row=2, column=0, sticky="ew", padx=28, pady=(0, 10))
        selection.columnconfigure(0, weight=1)
        selection.columnconfigure(1, weight=1)
        tk.Label(
            selection,
            text="사용 장비",
            font=("Segoe UI Semibold", 9),
            background=CARD,
            foreground=TEXT,
        ).grid(row=0, column=0, sticky="w", padx=(14, 7), pady=(11, 3))
        tk.Label(
            selection,
            text="통상 시험 방법",
            font=("Segoe UI Semibold", 9),
            background=CARD,
            foreground=TEXT,
        ).grid(row=0, column=1, sticky="w", padx=(7, 14), pady=(11, 3))
        self.device_combo = ttk.Combobox(
            selection,
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
            pady=(0, 11),
        )
        self.device_combo.bind("<<ComboboxSelected>>", self._on_device_changed)
        self.method_combo = ttk.Combobox(
            selection,
            textvariable=self.method_var,
            state="readonly",
            font=("Segoe UI", 9),
        )
        self.method_combo.grid(
            row=1,
            column=1,
            sticky="ew",
            padx=(7, 14),
            pady=(0, 11),
        )
        self.method_combo.bind("<<ComboboxSelected>>", self._on_method_changed)

        body_shell = tk.Frame(self, background=BACKGROUND)
        body_shell.grid(row=3, column=0, sticky="nsew", padx=28)
        body_shell.columnconfigure(0, weight=1)
        body_shell.rowconfigure(0, weight=1)
        self.scroll_canvas = tk.Canvas(
            body_shell,
            background=BACKGROUND,
            highlightthickness=0,
            borderwidth=0,
        )
        self.scroll_canvas.grid(row=0, column=0, sticky="nsew")
        scrollbar = ttk.Scrollbar(
            body_shell,
            orient="vertical",
            command=self.scroll_canvas.yview,
        )
        scrollbar.grid(row=0, column=1, sticky="ns")
        self.scroll_canvas.configure(yscrollcommand=scrollbar.set)

        self.scroll_content = tk.Frame(self.scroll_canvas, background=BACKGROUND)
        self.scroll_content.columnconfigure(0, weight=1)
        self.scroll_window = self.scroll_canvas.create_window(
            (0, 0),
            window=self.scroll_content,
            anchor="nw",
        )
        self.scroll_content.bind("<Configure>", self._on_content_configure)
        self.scroll_canvas.bind("<Configure>", self._on_canvas_configure)
        self.scroll_canvas.bind("<MouseWheel>", self._on_mousewheel)

        self._build_method_card()
        self.common_host = tk.Frame(self.scroll_content, background=BACKGROUND)
        self.common_host.grid(row=1, column=0, sticky="ew", pady=(0, 10))
        self.common_host.columnconfigure(0, weight=1)
        self._build_field_card(
            self.common_host,
            title="1. 모든 장비에 공통으로 확인할 조건",
            subtitle="표준·시료·환경·반복·판정·교정·안전을 기록해요.",
            fields=COMMON_PLAN_FIELDS,
            variables=self._common_variables,
            text_widgets=self._common_text_widgets,
            row=0,
        )

        self.detail_host = tk.Frame(self.scroll_content, background=BACKGROUND)
        self.detail_host.grid(row=2, column=0, sticky="ew", pady=(0, 8))
        self.detail_host.columnconfigure(0, weight=1)

        acknowledgement = tk.Frame(
            self.scroll_content,
            background=ACCENT_LIGHT,
            highlightbackground="#D6E8FF",
            highlightthickness=1,
        )
        acknowledgement.grid(row=3, column=0, sticky="ew", pady=(0, 8))
        acknowledgement.columnconfigure(0, weight=1)
        self.assistance_ack_check = tk.Checkbutton(
            acknowledgement,
            text=(
                "이 내용은 표준 준수 보증이 아니라 계획 보조이며, "
                "최종 절차와 판정은 시험 책임자가 확인함"
            ),
            variable=self.assistance_ack_var,
            font=("Segoe UI Semibold", 9),
            background=ACCENT_LIGHT,
            foreground=ACCENT_DARK,
            activebackground=ACCENT_LIGHT,
            selectcolor=CARD,
            anchor="w",
            justify="left",
            wraplength=790,
            padx=12,
            pady=9,
        )
        self.assistance_ack_check.grid(row=0, column=0, sticky="ew")

        footer = tk.Frame(self, background=BACKGROUND)
        footer.grid(row=4, column=0, sticky="ew", padx=28, pady=(10, 20))
        footer.columnconfigure(0, weight=1)
        self.status_label = tk.Label(
            footer,
            textvariable=self.status_var,
            font=("Segoe UI", 9),
            background=BACKGROUND,
            foreground=WARNING,
            anchor="w",
            justify="left",
            wraplength=590,
        )
        self.status_label.grid(row=0, column=0, sticky="ew", padx=(0, 12))
        _button(footer, text="취소", command=self._cancel).grid(
            row=0,
            column=1,
            padx=(0, 7),
        )
        self.apply_button = _button(
            footer,
            text="상세 계획 1개 추가",
            command=self._apply,
            primary=True,
        )
        self.apply_button.grid(row=0, column=2)
        self._bind_mousewheel_tree(self.scroll_content)

    def _build_method_card(self) -> None:
        card = tk.Frame(
            self.scroll_content,
            background=CARD,
            highlightbackground=BORDER,
            highlightthickness=1,
        )
        card.grid(row=0, column=0, sticky="ew", pady=(0, 10))
        card.columnconfigure(0, weight=1)
        tk.Label(
            card,
            textvariable=self.category_summary_var,
            font=("Segoe UI Semibold", 11),
            background=CARD,
            foreground=TEXT,
            justify="left",
            anchor="w",
            wraplength=820,
        ).grid(row=0, column=0, sticky="ew", padx=16, pady=(13, 4))
        tk.Label(
            card,
            textvariable=self.standard_examples_var,
            font=("Segoe UI", 8),
            background="#F8FAFC",
            foreground=SUBTEXT,
            justify="left",
            anchor="w",
            wraplength=820,
            padx=11,
            pady=7,
        ).grid(row=1, column=0, sticky="ew", padx=16, pady=(0, 9))
        tk.Label(
            card,
            textvariable=self.method_summary_var,
            font=("Segoe UI Semibold", 10),
            background=CARD,
            foreground=ACCENT_DARK,
            justify="left",
            anchor="w",
            wraplength=820,
        ).grid(row=2, column=0, sticky="ew", padx=16)
        tk.Label(
            card,
            textvariable=self.method_steps_var,
            font=("Segoe UI", 9),
            background=CARD,
            foreground=TEXT,
            justify="left",
            anchor="w",
            wraplength=820,
        ).grid(row=3, column=0, sticky="ew", padx=16, pady=(5, 5))
        tk.Label(
            card,
            textvariable=self.expected_results_var,
            font=("Segoe UI", 9),
            background=ACCENT_LIGHT,
            foreground="#3B608A",
            justify="left",
            anchor="w",
            wraplength=820,
            padx=10,
            pady=7,
        ).grid(row=4, column=0, sticky="ew", padx=16, pady=(0, 13))

    def _build_field_card(
        self,
        parent: tk.Frame,
        *,
        title: str,
        subtitle: str,
        fields: tuple[PlanFieldDefinition, ...],
        variables: dict[str, tk.Variable],
        text_widgets: dict[str, tk.Text],
        row: int,
    ) -> tk.Frame:
        card = tk.Frame(
            parent,
            background=CARD,
            highlightbackground=BORDER,
            highlightthickness=1,
        )
        card.grid(row=row, column=0, sticky="ew")
        card.columnconfigure(1, weight=1)
        tk.Label(
            card,
            text=title,
            font=("Segoe UI Semibold", 12),
            background=CARD,
            foreground=TEXT,
        ).grid(row=0, column=0, columnspan=3, sticky="w", padx=16, pady=(14, 2))
        tk.Label(
            card,
            text=subtitle,
            font=("Segoe UI", 9),
            background=CARD,
            foreground=SUBTEXT,
        ).grid(row=1, column=0, columnspan=3, sticky="w", padx=16, pady=(0, 9))

        for field_index, field in enumerate(fields):
            field_row = 2 + (field_index * 2)
            label_text = field.label_ko + (" *" if field.required else "")
            tk.Label(
                card,
                text=label_text,
                font=("Segoe UI Semibold", 9),
                background=CARD,
                foreground=TEXT,
                anchor="nw",
                justify="left",
                width=23,
            ).grid(
                row=field_row,
                column=0,
                sticky="nw",
                padx=(16, 10),
                pady=(7, 2),
            )
            input_host = tk.Frame(card, background=CARD)
            input_host.grid(
                row=field_row,
                column=1,
                sticky="ew",
                pady=(5, 3),
            )
            input_host.columnconfigure(0, weight=1)
            widget = self._build_field_widget(
                input_host,
                field,
                variables,
                text_widgets,
            )
            self._field_widgets[field.field_id] = widget
            tk.Label(
                card,
                text=field.help_ko,
                font=("Segoe UI", 8),
                background=CARD,
                foreground=SUBTEXT,
                anchor="w",
                justify="left",
                wraplength=650,
            ).grid(
                row=field_row + 1,
                column=1,
                columnspan=2,
                sticky="ew",
                pady=(0, 4),
            )
            if field.unit and field.unit not in _PLAN_UNIT_OPTIONS:
                tk.Label(
                    card,
                    text=field.unit,
                    font=("Segoe UI Semibold", 9),
                    background=CARD,
                    foreground=SUBTEXT,
                    width=8,
                ).grid(
                    row=field_row,
                    column=2,
                    sticky="nw",
                    padx=(7, 14),
                    pady=(8, 2),
                )

        last_row = 2 + (len(fields) * 2)
        tk.Frame(card, background=CARD, height=7).grid(
            row=last_row + 1,
            column=0,
            columnspan=3,
        )
        return card

    def _build_field_widget(
        self,
        parent: tk.Frame,
        field: PlanFieldDefinition,
        variables: dict[str, tk.Variable],
        text_widgets: dict[str, tk.Text],
    ) -> tk.Misc:
        default = "" if field.default is None else field.default
        if field.field_type is PlanFieldType.BOOLEAN:
            variable = tk.BooleanVar(value=bool(default))
            variables[field.field_id] = variable
            widget = tk.Checkbutton(
                parent,
                text="확인했어요",
                variable=variable,
                font=("Segoe UI", 9),
                background=CARD,
                foreground=TEXT,
                activebackground=CARD,
                selectcolor=CARD,
                anchor="w",
            )
            widget.grid(row=0, column=0, sticky="w")
            return widget

        if field.field_type is PlanFieldType.MULTILINE:
            widget = tk.Text(
                parent,
                height=3,
                wrap="word",
                font=("Segoe UI", 9),
                background="#FBFCFD",
                foreground=TEXT,
                insertbackground=TEXT,
                highlightbackground=BORDER,
                highlightcolor=ACCENT,
                highlightthickness=1,
                relief="flat",
                borderwidth=0,
                padx=8,
                pady=6,
            )
            widget.grid(row=0, column=0, sticky="ew")
            if default != "":
                widget.insert("1.0", str(default))
            text_widgets[field.field_id] = widget
            return widget

        unit_options = (
            _PLAN_UNIT_OPTIONS.get(field.unit)
            if field.field_type in {
                PlanFieldType.NUMBER,
                PlanFieldType.INTEGER,
            }
            else None
        )
        display_default = str(default)
        if unit_options is not None:
            default_unit = self._default_display_unit(field, unit_options)
            self._field_unit_vars[field.field_id] = tk.StringVar(
                value=default_unit
            )
            self._field_previous_units[field.field_id] = default_unit
            if default != "":
                display_default = self._base_to_display(
                    default,
                    default_unit,
                    unit_options,
                )
        variable = tk.StringVar(value=display_default)
        variables[field.field_id] = variable
        if field.field_type is PlanFieldType.CHOICE:
            widget = ttk.Combobox(
                parent,
                textvariable=variable,
                values=field.choices,
                state="readonly",
                font=("Segoe UI", 9),
            )
        else:
            widget = tk.Entry(
                parent,
                textvariable=variable,
                font=("Segoe UI", 9),
                background="#FBFCFD",
                foreground=TEXT,
                insertbackground=TEXT,
                highlightbackground=BORDER,
                highlightcolor=ACCENT,
                highlightthickness=1,
                relief="flat",
                borderwidth=0,
                justify=(
                    "right"
                    if field.field_type
                    in {PlanFieldType.NUMBER, PlanFieldType.INTEGER}
                    else "left"
                ),
            )
        widget.grid(row=0, column=0, sticky="ew", ipady=5)
        if unit_options is not None:
            unit_combo = ttk.Combobox(
                parent,
                textvariable=self._field_unit_vars[field.field_id],
                values=tuple(name for name, _scale in unit_options),
                state="readonly",
                width=max(6, max(len(name) for name, _scale in unit_options)),
                font=("Segoe UI", 9),
            )
            unit_combo.grid(row=0, column=1, sticky="e", padx=(7, 0))
            unit_combo.bind(
                "<<ComboboxSelected>>",
                lambda _event, field_id=field.field_id: (
                    self._on_field_unit_changed(field_id)
                ),
            )
        return widget

    @staticmethod
    def _format_decimal(value: Decimal) -> str:
        if not value:
            return "0"
        normalized = value.normalize()
        if -12 <= normalized.adjusted() <= 18:
            return format(normalized, "f")
        return format(normalized, "E")

    @staticmethod
    def _unit_scale(
        unit_name: str,
        options: tuple[tuple[str, Decimal], ...],
    ) -> Decimal:
        try:
            return dict(options)[unit_name]
        except KeyError as exc:
            raise ValueError(
                f"지원하지 않는 계획 표시 단위입니다: {unit_name}"
            ) from exc

    @classmethod
    def _default_display_unit(
        cls,
        field: PlanFieldDefinition,
        options: tuple[tuple[str, Decimal], ...],
    ) -> str:
        reference = field.default
        if not isinstance(reference, (int, float)) or not reference:
            return next(
                name
                for name, scale in options
                if scale == Decimal("1")
            )
        magnitude = abs(Decimal(str(reference)))
        suitable = [
            (name, scale)
            for name, scale in options
            if scale <= magnitude
        ]
        return max(suitable, key=lambda item: item[1])[0]

    @classmethod
    def _base_to_display(
        cls,
        value: object,
        unit_name: str,
        options: tuple[tuple[str, Decimal], ...],
    ) -> str:
        try:
            number = Decimal(str(value).strip())
        except (InvalidOperation, ValueError) as exc:
            raise ValueError("계획 값은 유한한 숫자여야 합니다.") from exc
        if not number.is_finite():
            raise ValueError("계획 값은 유한한 숫자여야 합니다.")
        return cls._format_decimal(
            number / cls._unit_scale(unit_name, options)
        )

    def _on_field_unit_changed(self, field_id: str) -> None:
        variable = self._common_variables.get(field_id)
        if variable is None:
            variable = self._detail_variables.get(field_id)
        if variable is None:
            return
        unit_var = self._field_unit_vars.get(field_id)
        if unit_var is None:
            return
        field = self._field_definition(field_id)
        options = _PLAN_UNIT_OPTIONS.get(field.unit)
        if options is None:
            return
        old_unit = self._field_previous_units.get(field_id, unit_var.get())
        new_unit = unit_var.get()
        self._field_previous_units[field_id] = new_unit
        if old_unit == new_unit:
            return
        raw = str(variable.get()).strip()
        if not raw:
            return
        try:
            number = Decimal(raw)
        except InvalidOperation:
            return
        if not number.is_finite():
            return
        base_value = number * self._unit_scale(old_unit, options)
        variable.set(
            self._format_decimal(
                base_value / self._unit_scale(new_unit, options)
            )
        )

    def _field_definition(self, field_id: str) -> PlanFieldDefinition:
        for field in (*COMMON_PLAN_FIELDS, *self._visible_detail_fields):
            if field.field_id == field_id:
                return field
        raise KeyError(f"현재 화면에 없는 계획 필드입니다: {field_id}")

    @staticmethod
    def _instrument_option(instrument: SelectedInstrument) -> str:
        return (
            f"{instrument.display_name}  ·  {instrument.category.label_ko}  ·  "
            f"{instrument.resource}"
        )

    def _selected_instrument(self) -> SelectedInstrument:
        index = self.device_combo.current()
        if not 0 <= index < len(self._instruments):
            raise ValueError("사용할 장비를 다시 선택해 주세요.")
        return self._instruments[index]

    def _on_device_changed(self, _event: tk.Event[Any] | None = None) -> None:
        instrument = self._selected_instrument()
        self._template = template_for_instrument(instrument)
        self.category_summary_var.set(
            f"{instrument.category.label_ko} · {self._template.summary_ko}"
        )
        examples = "\n".join(
            f"• {example}" for example in self._template.standard_examples
        )
        if instrument.profile_id:
            profile_note = f"연결 프로필: {instrument.profile_id}"
            if instrument.compatibility_status in {
                "hardware_validated",
                "hardware_validated_partial",
            }:
                profile_note += (
                    f" · 실장비에서 통과한 명령 "
                    f"{len(instrument.compatible_operation_ids)}개"
                )
            elif instrument.compatibility_status == "demo_catalog_preview":
                profile_note += " · 화면 확인용 데모"
            else:
                profile_note += " · operation별 실장비 검증 필요"
        else:
            profile_note = "연결 프로필: 아직 지정되지 않음"
        self.standard_examples_var.set(
            "참고할 수 있는 문서 예시이며, 이 분류를 선택했다고 적용·준수가 "
            "보장되지는 않습니다.\n"
            f"{profile_note}\n"
            "계획 입력값은 장비의 실제 범위·옵션·지원 기능 검사를 대신하지 "
            f"않습니다.\n{examples}"
        )
        self.method_combo.configure(
            values=tuple(method.label_ko for method in self._template.methods)
        )
        self.method_combo.current(0)
        self._on_method_changed()

    def _on_method_changed(self, _event: tk.Event[Any] | None = None) -> None:
        if self._template is None:
            return
        index = self.method_combo.current()
        if not 0 <= index < len(self._template.methods):
            index = 0
            self.method_combo.current(0)
        self._method = self._template.methods[index]
        self.method_summary_var.set(
            f"{self._method.label_ko} · {self._method.purpose_ko}"
        )
        self.method_steps_var.set(
            "\n".join(
                f"{step_index}. {step}"
                for step_index, step in enumerate(
                    self._method.procedure_steps,
                    start=1,
                )
            )
        )
        self.expected_results_var.set(
            "예정 결과 · " + " · ".join(self._method.expected_results)
        )
        self._rebuild_detail_fields()

    def _rebuild_detail_fields(self) -> None:
        for field in self._visible_detail_fields:
            self._field_widgets.pop(field.field_id, None)
            self._field_unit_vars.pop(field.field_id, None)
            self._field_previous_units.pop(field.field_id, None)
        for child in self.detail_host.winfo_children():
            child.destroy()
        self._detail_variables.clear()
        self._detail_text_widgets.clear()
        if self._template is None or self._method is None:
            self._visible_detail_fields = ()
            return
        self._visible_detail_fields = self._template.fields_for_method(
            self._method.method_id
        )
        self._build_field_card(
            self.detail_host,
            title=f"2. {self._template.category.label_ko} 상세 고려 항목",
            subtitle=(
                "장비 프로파일·정격·옵션에 없는 기능은 실행 전에 지원 여부를 "
                "별도로 확인해야 해요."
            ),
            fields=self._visible_detail_fields,
            variables=self._detail_variables,
            text_widgets=self._detail_text_widgets,
            row=0,
        )
        for field_id, value in self._method.recommended_values:
            self.set_field_value(field_id, value)
        self._bind_mousewheel_tree(self.detail_host)
        self._sync_scrollregion()

    def _raw_values(
        self,
        variables: dict[str, tk.Variable],
        text_widgets: dict[str, tk.Text],
        fields: tuple[PlanFieldDefinition, ...],
    ) -> dict[str, str | bool]:
        fields_by_id = {field.field_id: field for field in fields}
        values: dict[str, str | bool] = {}
        for field_id, variable in variables.items():
            raw_value = variable.get()
            field = fields_by_id[field_id]
            unit_var = self._field_unit_vars.get(field_id)
            options = _PLAN_UNIT_OPTIONS.get(field.unit)
            if (
                unit_var is not None
                and options is not None
                and str(raw_value).strip()
            ):
                try:
                    number = Decimal(str(raw_value).strip())
                except InvalidOperation:
                    values[field_id] = raw_value
                else:
                    if number.is_finite():
                        values[field_id] = self._format_decimal(
                            number
                            * self._unit_scale(unit_var.get(), options)
                        )
                    else:
                        values[field_id] = raw_value
            else:
                values[field_id] = raw_value
        for field_id, widget in text_widgets.items():
            values[field_id] = widget.get("1.0", "end-1c")
        return values

    def set_field_value(self, field_id: str, value: str | bool | int | float) -> None:
        variable = self._common_variables.get(field_id)
        if variable is None:
            variable = self._detail_variables.get(field_id)
        if variable is not None:
            unit_var = self._field_unit_vars.get(field_id)
            if (
                unit_var is not None
                and not isinstance(value, bool)
                and isinstance(value, (str, int, float))
            ):
                field = self._field_definition(field_id)
                options = _PLAN_UNIT_OPTIONS.get(field.unit)
                if options is not None:
                    variable.set(
                        self._base_to_display(
                            value,
                            unit_var.get(),
                            options,
                        )
                    )
                    return
            variable.set(value)
            return
        text_widget = self._common_text_widgets.get(field_id)
        if text_widget is None:
            text_widget = self._detail_text_widgets.get(field_id)
        if text_widget is None:
            raise KeyError(f"현재 화면에 없는 계획 필드입니다: {field_id}")
        text_widget.delete("1.0", tk.END)
        text_widget.insert("1.0", str(value))

    def field_value(self, field_id: str) -> str | bool:
        variable = self._common_variables.get(field_id)
        if variable is None:
            variable = self._detail_variables.get(field_id)
        if variable is not None:
            return variable.get()
        text_widget = self._common_text_widgets.get(field_id)
        if text_widget is None:
            text_widget = self._detail_text_widgets.get(field_id)
        if text_widget is None:
            raise KeyError(f"현재 화면에 없는 계획 필드입니다: {field_id}")
        return text_widget.get("1.0", "end-1c")

    def _build_item(self) -> GenericPlanItem:
        if self._method is None:
            raise ValueError("시험 방법을 다시 선택해 주세요.")
        return GenericPlanItem.from_raw(
            instrument=self._selected_instrument(),
            method_id=self._method.method_id,
            common_values=self._raw_values(
                self._common_variables,
                self._common_text_widgets,
                COMMON_PLAN_FIELDS,
            ),
            detail_values=self._raw_values(
                self._detail_variables,
                self._detail_text_widgets,
                self._visible_detail_fields,
            ),
            assistance_notice_acknowledged=self.assistance_ack_var.get(),
        )

    def _apply(self) -> None:
        try:
            item = self._build_item()
        except (KeyError, TypeError, ValueError) as exc:
            self.status_var.set(str(exc))
            self.status_label.configure(foreground=WARNING)
            return
        if not self._on_add(item):
            return
        self.status_label.configure(foreground=SUCCESS)
        self.destroy()

    def _cancel(self) -> None:
        self.destroy()

    def _on_content_configure(self, _event: tk.Event[Any]) -> None:
        self._sync_scrollregion()

    def _on_canvas_configure(self, event: tk.Event[Any]) -> None:
        self.scroll_canvas.itemconfigure(self.scroll_window, width=event.width)
        self._sync_scrollregion()

    def _sync_scrollregion(self) -> None:
        if not self.winfo_exists():
            return
        self.scroll_content.update_idletasks()
        bounds = self.scroll_canvas.bbox("all")
        if bounds is not None:
            self.scroll_canvas.configure(scrollregion=bounds)

    def _bind_mousewheel_tree(self, widget: tk.Misc) -> None:
        widget.bind("<MouseWheel>", self._on_mousewheel, add="+")
        for child in widget.winfo_children():
            self._bind_mousewheel_tree(child)

    def _on_mousewheel(self, event: tk.Event[Any]) -> str:
        delta = int(getattr(event, "delta", 0))
        if delta:
            self.scroll_canvas.yview_scroll(-1 if delta > 0 else 1, "units")
        return "break"
