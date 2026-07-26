"""Editor that turns one raw manual header into a typed validation draft."""

from __future__ import annotations

import re
import string
import tkinter as tk
from tkinter import messagebox, ttk

from scpi_automation.identity import DeviceCategory, InstrumentIdentity
from scpi_automation.validation import (
    LocalExtensionDefinition,
    LocalExtensionParameter,
    ManualCommandCandidate,
    OperationKind,
    query_extension_draft,
    typed_extension_draft,
)


BACKGROUND = "#F4F6F8"
CARD = "#FFFFFF"
TEXT = "#191F28"
SUBTEXT = "#6B7684"
BORDER = "#E5E8EB"
ACCENT = "#3182F6"

_MODE_LABELS = {
    "조회(query)": OperationKind.QUERY,
    "설정(set)": OperationKind.SET,
    "실행(execute)": OperationKind.EXECUTE,
}
_RESPONSE_TYPES = (
    "string",
    "float",
    "integer",
    "boolean",
    "float_array",
    "float_pair",
    "float_triplet",
)
_PARAMETER_TYPES = {
    "string",
    "float",
    "integer",
    "number",
    "boolean",
    "enum",
    "number_or_auto",
    "float_or_enum",
    "integer_or_mnemonic",
    "float_or_mnemonic",
    "float_or_string",
}


def _placeholders(template: str) -> tuple[str, ...]:
    names: list[str] = []
    try:
        for _literal, name, format_spec, conversion in string.Formatter().parse(
            template
        ):
            if name is None:
                continue
            if (
                not name
                or format_spec
                or conversion
                or re.fullmatch(r"[A-Za-z_][A-Za-z0-9_.-]*", name) is None
            ):
                raise ValueError(
                    "입력칸은 {value}, {trace}처럼 단순한 이름으로 적어 주세요."
                )
            names.append(name)
    except ValueError as exc:
        raise ValueError(f"SCPI 입력칸 형식이 올바르지 않습니다: {exc}") from exc
    return tuple(dict.fromkeys(names))


def _pairs(
    text: str,
    *,
    separator: str = ",",
) -> dict[str, str]:
    text = text.strip()
    if not text:
        return {}
    values: dict[str, str] = {}
    for raw_item in text.split(separator):
        item = raw_item.strip()
        if not item:
            continue
        if "=" not in item:
            raise ValueError(
                f"'{item}'은 이름=값 형식이 아닙니다."
            )
        name, value = (part.strip() for part in item.split("=", 1))
        if not name or not value:
            raise ValueError("이름과 값을 모두 입력해 주세요.")
        if name in values:
            raise ValueError(f"'{name}'이 두 번 입력됐습니다.")
        values[name] = value
    return values


def _number_pairs(text: str) -> dict[str, float]:
    return {
        name: float(value)
        for name, value in _pairs(text).items()
    }


def _choice_pairs(text: str) -> dict[str, tuple[str, ...]]:
    return {
        name: tuple(
            choice.strip()
            for choice in value.split("|")
            if choice.strip()
        )
        for name, value in _pairs(text, separator=";").items()
    }


def _inferred_type(name: str) -> str:
    lowered = name.casefold()
    if lowered in {
        "trace",
        "marker",
        "channel",
        "window",
        "index",
        "source",
        "destination",
        "offset",
        "count",
    }:
        return "integer"
    if lowered in {"state", "enable", "enabled", "output"}:
        return "boolean"
    if lowered in {
        "value",
        "frequency",
        "level",
        "span",
        "rbw",
        "vbw",
        "time",
        "voltage",
        "current",
        "power",
    }:
        return "float"
    return "string"


class LocalExtensionEditorDialog(tk.Toplevel):
    """Beginner-facing metadata editor; it never communicates with hardware."""

    result: LocalExtensionDefinition | None

    def __init__(
        self,
        master: tk.Misc,
        *,
        candidate: ManualCommandCandidate,
        identity: InstrumentIdentity,
        category: DeviceCategory,
    ) -> None:
        super().__init__(master)
        self.candidate = candidate
        self.identity = identity
        self.category = category
        self.result = None

        default_kind = (
            OperationKind.QUERY
            if candidate.query_probe
            and candidate.probe_policy != "manual_only"
            else OperationKind.EXECUTE
        )
        self.mode_var = tk.StringVar(
            value=next(
                label
                for label, kind in _MODE_LABELS.items()
                if kind is default_kind
            )
        )
        self.label_var = tk.StringVar(
            value=f"{candidate.command_pattern} 기능"
        )
        self.group_var = tk.StringVar(
            value=candidate.command_group.casefold() or "manual"
        )
        self.risk_var = tk.StringVar(
            value=(
                "low"
                if default_kind is OperationKind.QUERY
                else "hazardous"
            )
        )
        self.command_var = tk.StringVar(
            value=(
                candidate.query_probe
                if default_kind is OperationKind.QUERY
                else candidate.command_pattern
            )
        )
        self.readback_var = tk.StringVar(
            value=candidate.query_probe if default_kind is OperationKind.SET else ""
        )
        self.response_var = tk.StringVar(value="string")
        self.arguments_var = tk.StringVar()
        self.types_var = tk.StringVar()
        self.units_var = tk.StringVar()
        self.minimums_var = tk.StringVar()
        self.maximums_var = tk.StringVar()
        self.choices_var = tk.StringVar()
        self.note_var = tk.StringVar(
            value=f"{candidate.source.title} p.{candidate.manual_page}"
        )

        self.title("매뉴얼 후보를 검증 가능한 기능으로 만들기")
        self.geometry("850x720")
        self.minsize(760, 640)
        self.configure(background=BACKGROUND)
        self.transient(master.winfo_toplevel())
        self.protocol("WM_DELETE_WINDOW", self._cancel)
        self._build()
        self.mode_combo.bind("<<ComboboxSelected>>", self._mode_changed)
        self.after_idle(self._focus_and_grab)

    def _focus_and_grab(self) -> None:
        try:
            self.grab_set()
            self.focus_force()
        except tk.TclError:
            pass

    def _build(self) -> None:
        self.columnconfigure(0, weight=1)
        self.rowconfigure(1, weight=1)
        header = tk.Frame(self, background=BACKGROUND)
        header.grid(row=0, column=0, sticky="ew", padx=24, pady=(20, 12))
        tk.Label(
            header,
            text="이 명령이 무엇을 하는지 먼저 정리할게요",
            font=("Segoe UI Semibold", 17),
            background=BACKGROUND,
            foreground=TEXT,
        ).pack(anchor="w")
        tk.Label(
            header,
            text=(
                f"{self.candidate.command_pattern} · "
                f"{self.candidate.source.title} p.{self.candidate.manual_page}\n"
                "여기서 저장해도 바로 사용할 수 없어요. 다음 단계의 실장비 "
                "조회·쓰기·원복 검증을 통과해야 루틴 기능이 됩니다."
            ),
            font=("Segoe UI", 9),
            background=BACKGROUND,
            foreground=SUBTEXT,
            justify="left",
        ).pack(anchor="w", pady=(5, 0))

        body = tk.Frame(
            self,
            background=CARD,
            highlightbackground=BORDER,
            highlightthickness=1,
        )
        body.grid(row=1, column=0, sticky="nsew", padx=24)
        body.columnconfigure(0, weight=1)
        body.rowconfigure(0, weight=1)
        canvas = tk.Canvas(
            body,
            background=CARD,
            highlightthickness=0,
            borderwidth=0,
        )
        scrollbar = ttk.Scrollbar(
            body,
            orient="vertical",
            command=canvas.yview,
        )
        canvas.configure(yscrollcommand=scrollbar.set)
        canvas.grid(row=0, column=0, sticky="nsew")
        scrollbar.grid(row=0, column=1, sticky="ns")
        shell = tk.Frame(canvas, background=CARD)
        shell.columnconfigure(1, weight=1)
        form_window = canvas.create_window(
            (0, 0),
            window=shell,
            anchor="nw",
        )
        shell.bind(
            "<Configure>",
            lambda _event: canvas.configure(
                scrollregion=canvas.bbox("all")
            ),
        )
        canvas.bind(
            "<Configure>",
            lambda event: canvas.itemconfigure(
                form_window,
                width=event.width,
            ),
        )
        canvas.bind(
            "<MouseWheel>",
            lambda event: canvas.yview_scroll(
                int(-event.delta / 120),
                "units",
            ),
        )
        self._form_canvas = canvas

        row = 0
        self.mode_combo = self._combo_row(
            shell,
            row,
            "기능 종류",
            self.mode_var,
            (
                ("실행(execute)",)
                if self.candidate.probe_policy == "manual_only"
                else tuple(_MODE_LABELS)
            ),
        )
        row += 1
        self._entry_row(shell, row, "화면에 보일 이름", self.label_var)
        row += 1
        self._entry_row(shell, row, "기능 그룹", self.group_var)
        row += 1
        self.risk_combo = self._combo_row(
            shell,
            row,
            "위험도",
            self.risk_var,
            ("low", "medium", "high", "hazardous", "critical"),
        )
        row += 1
        self._entry_row(
            shell,
            row,
            "실제 SCPI",
            self.command_var,
            "값이 들어갈 곳은 {value}, 채널은 {channel}처럼 적어요.",
        )
        row += 1
        self.readback_entry = self._entry_row(
            shell,
            row,
            "원래 값 읽기",
            self.readback_var,
            "설정(set) 기능은 원복을 위해 같은 값을 읽는 Query가 필수예요.",
        )
        row += 1
        self.response_combo = self._combo_row(
            shell,
            row,
            "응답 형식",
            self.response_var,
            _RESPONSE_TYPES,
        )
        row += 1
        self._entry_row(
            shell,
            row,
            "시험값",
            self.arguments_var,
            "예: trace=1, value=1000000",
        )
        row += 1
        self._entry_row(
            shell,
            row,
            "입력값 종류",
            self.types_var,
            "선택 사항. 예: trace=integer, value=float, state=enum",
        )
        row += 1
        self._entry_row(
            shell,
            row,
            "단위",
            self.units_var,
            "선택 사항. 예: value=Hz",
        )
        row += 1
        self._entry_row(
            shell,
            row,
            "최솟값",
            self.minimums_var,
            "선택 사항. 예: value=1",
        )
        row += 1
        self._entry_row(
            shell,
            row,
            "최댓값",
            self.maximums_var,
            "선택 사항. 예: value=30000000000",
        )
        row += 1
        self._entry_row(
            shell,
            row,
            "선택값",
            self.choices_var,
            "enum일 때 사용. 예: state=ON|OFF; mode=MAXH|WRIT",
        )
        row += 1
        self._entry_row(shell, row, "확인 메모", self.note_var)
        self._mode_changed()

        footer = tk.Frame(self, background=BACKGROUND)
        footer.grid(row=2, column=0, sticky="ew", padx=24, pady=(12, 20))
        footer.columnconfigure(0, weight=1)
        tk.Button(
            footer,
            text="취소",
            command=self._cancel,
            font=("Segoe UI Semibold", 9),
            relief="flat",
            borderwidth=0,
            padx=18,
            pady=10,
            background="#E5E8EB",
            foreground=TEXT,
        ).grid(row=0, column=1)
        tk.Button(
            footer,
            text="검증 후보 만들기",
            command=self._accept,
            font=("Segoe UI Semibold", 9),
            relief="flat",
            borderwidth=0,
            padx=18,
            pady=10,
            background=ACCENT,
            foreground="#FFFFFF",
        ).grid(row=0, column=2, padx=(8, 0))

    def _label(self, parent: tk.Misc, row: int, text: str) -> None:
        tk.Label(
            parent,
            text=text,
            font=("Segoe UI Semibold", 9),
            background=CARD,
            foreground=TEXT,
        ).grid(row=row, column=0, sticky="w", padx=(18, 12), pady=6)

    def _entry_row(
        self,
        parent: tk.Misc,
        row: int,
        label: str,
        variable: tk.StringVar,
        hint: str = "",
    ) -> ttk.Entry:
        self._label(parent, row, label)
        frame = tk.Frame(parent, background=CARD)
        frame.grid(row=row, column=1, sticky="ew", padx=(0, 18), pady=5)
        frame.columnconfigure(0, weight=1)
        entry = ttk.Entry(frame, textvariable=variable)
        entry.grid(row=0, column=0, sticky="ew")
        if hint:
            tk.Label(
                frame,
                text=hint,
                font=("Segoe UI", 8),
                background=CARD,
                foreground=SUBTEXT,
            ).grid(row=1, column=0, sticky="w", pady=(2, 0))
        return entry

    def _combo_row(
        self,
        parent: tk.Misc,
        row: int,
        label: str,
        variable: tk.StringVar,
        values: tuple[str, ...],
    ) -> ttk.Combobox:
        self._label(parent, row, label)
        combo = ttk.Combobox(
            parent,
            textvariable=variable,
            values=values,
            state="readonly",
        )
        combo.grid(row=row, column=1, sticky="ew", padx=(0, 18), pady=6)
        return combo

    def _mode_changed(self, _event: object | None = None) -> None:
        kind = _MODE_LABELS[self.mode_var.get()]
        if (
            self.candidate.probe_policy == "manual_only"
            and kind is not OperationKind.EXECUTE
        ):
            self.mode_var.set("실행(execute)")
            kind = OperationKind.EXECUTE
        current_command = self.command_var.get().strip()
        if kind is OperationKind.QUERY:
            if (
                not current_command
                or current_command == self.candidate.command_pattern
            ):
                self.command_var.set(self.candidate.query_probe)
            self.risk_combo.configure(
                values=("low", "medium", "high"),
            )
            if self.risk_var.get() in {
                "hazardous",
                "critical",
            }:
                self.risk_var.set("low")
        else:
            if (
                not current_command
                or current_command == self.candidate.query_probe
            ):
                self.command_var.set(self.candidate.command_pattern)
            self.risk_combo.configure(
                values=("high", "hazardous", "critical"),
            )
            if self.risk_var.get() not in {
                "high",
                "hazardous",
                "critical",
            }:
                self.risk_var.set("hazardous")
        if kind is OperationKind.SET and not self.readback_var.get().strip():
            self.readback_var.set(self.candidate.query_probe)
        state = "normal" if kind is OperationKind.SET else "disabled"
        self.readback_entry.configure(state=state)
        self.response_combo.configure(
            state=(
                "readonly"
                if kind in {OperationKind.QUERY, OperationKind.SET}
                else "disabled"
            )
        )

    def _parameter_definitions(
        self,
        names: tuple[str, ...],
    ) -> tuple[LocalExtensionParameter, ...]:
        types = _pairs(self.types_var.get())
        units = _pairs(self.units_var.get())
        minimums = _number_pairs(self.minimums_var.get())
        maximums = _number_pairs(self.maximums_var.get())
        choices = _choice_pairs(self.choices_var.get())
        metadata_names = (
            set(types)
            | set(units)
            | set(minimums)
            | set(maximums)
            | set(choices)
        )
        unknown = metadata_names - set(names)
        if unknown:
            raise ValueError(
                "SCPI에 없는 입력값 메타데이터가 있어요: "
                + ", ".join(sorted(unknown))
            )
        definitions: list[LocalExtensionParameter] = []
        for name in names:
            value_type = types.get(name, _inferred_type(name))
            if value_type not in _PARAMETER_TYPES:
                raise ValueError(
                    f"{name}의 입력값 종류 '{value_type}'를 사용할 수 없어요."
                )
            definitions.append(
                LocalExtensionParameter(
                    name=name,
                    value_type=value_type,
                    unit=units.get(name, ""),
                    minimum=minimums.get(name),
                    maximum=maximums.get(name),
                    choices=choices.get(name, ()),
                )
            )
        return tuple(definitions)

    def _build_result(self) -> LocalExtensionDefinition:
        kind = _MODE_LABELS[self.mode_var.get()]
        if (
            self.candidate.probe_policy == "manual_only"
            and kind is not OperationKind.EXECUTE
        ):
            raise ValueError(
                "수동 검토 전용 후보는 자동 Query/SET으로 바꿀 수 없어요. "
                "별도 시험 후 실행(execute) 근거만 기록해 주세요."
            )
        command = self.command_var.get().strip()
        readback = self.readback_var.get().strip()
        command_names = _placeholders(command)
        query_names = _placeholders(readback) if readback else ()
        names = tuple(dict.fromkeys(query_names + command_names))
        arguments = _pairs(self.arguments_var.get())
        if set(arguments) != set(names):
            missing = set(names) - set(arguments)
            unknown = set(arguments) - set(names)
            details = []
            if missing:
                details.append("시험값 없음: " + ", ".join(sorted(missing)))
            if unknown:
                details.append("SCPI에 없는 시험값: " + ", ".join(sorted(unknown)))
            raise ValueError("; ".join(details))
        parameters = self._parameter_definitions(names)
        common = {
            "label_ko": self.label_var.get().strip(),
            "group": self.group_var.get().strip() or "manual",
            "risk_level": self.risk_var.get(),
            "capability_slug": self.label_var.get(),
            "note_ko": self.note_var.get().strip(),
        }
        if kind is OperationKind.QUERY:
            return query_extension_draft(
                self.candidate,
                self.identity,
                self.category,
                response_type=self.response_var.get(),
                query_arguments=arguments,
                parameters=parameters,
                query_command=command,
                **common,
            )
        return typed_extension_draft(
            self.candidate,
            self.identity,
            self.category,
            operation_kind=kind,
            command_template=command,
            parameters=parameters,
            probe_arguments={
                name: arguments[name] for name in command_names
            },
            readback_query=readback,
            readback_response_type=self.response_var.get(),
            readback_arguments={
                name: arguments[name] for name in query_names
            },
            **common,
        )

    def _accept(self) -> None:
        try:
            self.result = self._build_result()
        except (KeyError, TypeError, ValueError) as exc:
            messagebox.showerror(
                "검증 후보를 만들 수 없어요",
                str(exc),
                parent=self,
            )
            return
        self._close()

    def _cancel(self) -> None:
        self.result = None
        self._close()

    def _close(self) -> None:
        try:
            self.grab_release()
        except tk.TclError:
            pass
        self.destroy()


def ask_local_extension_definition(
    master: tk.Misc,
    *,
    candidate: ManualCommandCandidate,
    identity: InstrumentIdentity,
    category: DeviceCategory,
) -> LocalExtensionDefinition | None:
    """Open the editor and wait for an explicit result."""

    dialog = LocalExtensionEditorDialog(
        master,
        candidate=candidate,
        identity=identity,
        category=category,
    )
    master.wait_window(dialog)
    return dialog.result
