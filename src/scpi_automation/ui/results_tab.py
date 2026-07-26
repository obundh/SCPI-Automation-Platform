from __future__ import annotations

import re
import tkinter as tk
import tkinter.font as tkfont
from datetime import datetime
from pathlib import Path
from tkinter import filedialog, messagebox, ttk
from typing import Any, Callable, Iterable

from scpi_automation.ui.value_formatting import (
    engineering_value_parts,
    format_display_value,
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
DANGER_LIGHT = "#FDECEC"
NEUTRAL_LIGHT = "#F2F4F6"

ResultSaver = Callable[[Any, str | Path], Path]
BundleExporter = Callable[[Any, str | Path], tuple[Path, ...]]


def _save_json(result: Any, path: str | Path) -> Path:
    from scpi_automation.results import save_result_json

    return save_result_json(result, path)


def _save_markdown(result: Any, path: str | Path) -> Path:
    from scpi_automation.results import save_result_markdown

    return save_result_markdown(result, path)


def _save_xlsx(result: Any, path: str | Path) -> Path:
    from scpi_automation.results import save_result_xlsx

    return save_result_xlsx(result, path)


def _export_bundle(result: Any, directory: str | Path) -> tuple[Path, ...]:
    from scpi_automation.results import export_result_bundle

    return export_result_bundle(result, directory)


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


class ResultsTab(tk.Frame):
    """Inspect a complete execution record and save it in offline formats."""

    def __init__(
        self,
        master: tk.Misc,
        on_back: Callable[[], None] | None = None,
        *,
        json_saver: ResultSaver | None = None,
        markdown_saver: ResultSaver | None = None,
        xlsx_saver: ResultSaver | None = None,
        bundle_exporter: BundleExporter | None = None,
    ) -> None:
        super().__init__(master, background=BACKGROUND)
        self._on_back = on_back
        self._json_saver = json_saver or _save_json
        self._markdown_saver = markdown_saver or _save_markdown
        self._xlsx_saver = xlsx_saver or _save_xlsx
        self._bundle_exporter = bundle_exporter or _export_bundle
        self._result: Any = None
        self._ui_scale = 1.0

        self.status_badge_var = tk.StringVar(value="결과 없음")
        self.run_id_var = tk.StringVar(value="-")
        self.time_var = tk.StringVar(value="-")
        self.summary_var = tk.StringVar(
            value="실행이 끝나면 측정값, 실행 단계, 통신 로그를 여기에서 확인할 수 있어요."
        )
        self.measurement_count_var = tk.StringVar(value="측정값 0개")
        self.step_count_var = tk.StringVar(value="단계 0개")
        self.log_count_var = tk.StringVar(value="로그 0개")
        self.export_status_var = tk.StringVar(
            value="결과가 준비되면 JSON, Markdown, Excel로 저장할 수 있어요."
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
        self._set_export_state(False)

    @property
    def result(self) -> Any:
        return self._result

    def _build(self) -> None:
        self.columnconfigure(0, weight=1)
        self.rowconfigure(2, weight=1)

        self.header = tk.Frame(self, background=BACKGROUND)
        self.header.grid(row=0, column=0, sticky="ew", padx=34, pady=(22, 10))
        self.header.columnconfigure(0, weight=1)
        tk.Label(
            self.header,
            text="5. 실행 결과를 확인하고 기록으로 남겨요",
            font=("Segoe UI Semibold", 20),
            background=BACKGROUND,
            foreground=TEXT,
        ).grid(row=0, column=0, sticky="w")
        tk.Label(
            self.header,
            text=(
                "측정값뿐 아니라 사용 장비, 계획, 실제 명령과 응답, 오류까지 한 실행 기록으로 보관해요."
            ),
            font=("Segoe UI", 10),
            background=BACKGROUND,
            foreground=SUBTEXT,
        ).grid(row=1, column=0, sticky="w", pady=(5, 0))
        self.back_button = _button(
            self.header,
            text="실행 화면으로 돌아가기",
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
            pady=(0, 10),
        )
        self.summary_card.columnconfigure(2, weight=1)
        self.status_badge = tk.Label(
            self.summary_card,
            textvariable=self.status_badge_var,
            font=("Segoe UI Semibold", 9),
            background=NEUTRAL_LIGHT,
            foreground=SUBTEXT,
            padx=11,
            pady=6,
        )
        self.status_badge.grid(row=0, column=0, rowspan=2, padx=14, pady=12)
        tk.Label(
            self.summary_card,
            text="실행 ID",
            font=("Segoe UI Semibold", 8),
            background=CARD,
            foreground=SUBTEXT,
        ).grid(row=0, column=1, sticky="sw", pady=(11, 0))
        tk.Label(
            self.summary_card,
            textvariable=self.run_id_var,
            font=("Cascadia Mono", 8),
            background=CARD,
            foreground=TEXT,
        ).grid(row=1, column=1, sticky="nw", pady=(1, 11))
        tk.Label(
            self.summary_card,
            textvariable=self.summary_var,
            font=("Segoe UI", 9),
            background=CARD,
            foreground=TEXT,
            anchor="w",
        ).grid(row=0, column=2, sticky="ew", padx=18, pady=(11, 0))
        tk.Label(
            self.summary_card,
            textvariable=self.time_var,
            font=("Segoe UI", 8),
            background=CARD,
            foreground=SUBTEXT,
            anchor="w",
        ).grid(row=1, column=2, sticky="ew", padx=18, pady=(1, 11))

        self.data_notebook = ttk.Notebook(self)
        self.data_notebook.grid(
            row=2,
            column=0,
            sticky="nsew",
            padx=34,
            pady=(0, 10),
        )
        self.measurement_page = tk.Frame(self.data_notebook, background=CARD)
        self.step_page = tk.Frame(self.data_notebook, background=CARD)
        self.log_page = tk.Frame(self.data_notebook, background=CARD)
        self.data_notebook.add(
            self.measurement_page,
            text=self.measurement_count_var.get(),
        )
        self.data_notebook.add(
            self.step_page,
            text=self.step_count_var.get(),
        )
        self.data_notebook.add(
            self.log_page,
            text=self.log_count_var.get(),
        )
        self.measurement_tree = self._build_tree(
            self.measurement_page,
            columns=("time", "device", "name", "value", "unit", "raw"),
            headings=(
                ("time", "시간", 115),
                ("device", "장비", 150),
                ("name", "결과 이름", 170),
                ("value", "값", 110),
                ("unit", "단위", 70),
                ("raw", "원본 응답", 230),
            ),
        )
        self.step_tree = self._build_tree(
            self.step_page,
            columns=("index", "status", "device", "action", "command", "response"),
            headings=(
                ("index", "#", 45),
                ("status", "상태", 80),
                ("device", "장비", 135),
                ("action", "기능·단계", 165),
                ("command", "전송 명령", 210),
                ("response", "응답·오류", 230),
            ),
        )
        self.log_tree = self._build_tree(
            self.log_page,
            columns=("time", "level", "step", "message"),
            headings=(
                ("time", "시간", 125),
                ("level", "수준", 75),
                ("step", "단계", 60),
                ("message", "실행 기록", 620),
            ),
        )

        self.export_card = tk.Frame(
            self,
            background=CARD,
            highlightbackground=BORDER,
            highlightthickness=1,
        )
        self.export_card.grid(
            row=3,
            column=0,
            sticky="ew",
            padx=34,
            pady=(0, 16),
        )
        self.export_card.columnconfigure(0, weight=1)
        tk.Label(
            self.export_card,
            text="결과 파일 저장",
            font=("Segoe UI Semibold", 11),
            background=CARD,
            foreground=TEXT,
        ).grid(row=0, column=0, sticky="w", padx=15, pady=(11, 1))
        tk.Label(
            self.export_card,
            textvariable=self.export_status_var,
            font=("Segoe UI", 8),
            background=CARD,
            foreground=SUBTEXT,
            anchor="w",
        ).grid(row=1, column=0, sticky="ew", padx=15, pady=(0, 11))
        actions = tk.Frame(self.export_card, background=CARD)
        actions.grid(row=0, column=1, rowspan=2, sticky="e", padx=15, pady=10)
        self.export_markdown_button = _button(
            actions,
            text="읽기용 보고서 (.md)",
            command=self._export_markdown,
            compact=True,
        )
        self.export_markdown_button.grid(row=0, column=0, padx=(0, 5))
        self.export_json_button = _button(
            actions,
            text="전체 데이터 (.json)",
            command=self._export_json,
            compact=True,
        )
        self.export_json_button.grid(row=0, column=1, padx=5)
        self.export_excel_button = _button(
            actions,
            text="Excel 표 (.xlsx)",
            command=self._export_xlsx,
            compact=True,
        )
        self.export_excel_button.grid(row=0, column=2, padx=5)
        self.export_all_button = _button(
            actions,
            text="세 가지 모두 저장",
            command=self._export_all,
            primary=True,
            compact=True,
        )
        self.export_all_button.grid(row=0, column=3, padx=(5, 0))

    def _build_tree(
        self,
        parent: tk.Frame,
        *,
        columns: tuple[str, ...],
        headings: tuple[tuple[str, str, int], ...],
    ) -> ttk.Treeview:
        parent.columnconfigure(0, weight=1)
        parent.rowconfigure(0, weight=1)
        tree = ttk.Treeview(
            parent,
            columns=columns,
            show="headings",
            selectmode="browse",
            height=10,
        )
        for column, label, width in headings:
            tree.heading(column, text=label, anchor="w")
            tree.column(
                column,
                width=width,
                minwidth=min(45, width),
                stretch=column not in {"index", "level", "step", "unit"},
                anchor="w",
            )
        tree.grid(row=0, column=0, sticky="nsew", padx=(1, 0), pady=1)
        vertical = ttk.Scrollbar(parent, orient="vertical", command=tree.yview)
        vertical.grid(row=0, column=1, sticky="ns", pady=1)
        horizontal = ttk.Scrollbar(parent, orient="horizontal", command=tree.xview)
        horizontal.grid(row=1, column=0, sticky="ew", padx=(1, 0))
        tree.configure(
            yscrollcommand=vertical.set,
            xscrollcommand=horizontal.set,
        )
        return tree

    def set_result(self, result: Any) -> None:
        """Display one immutable execution result."""

        self._result = result
        self._clear_trees()
        if result is None:
            self.status_badge_var.set("결과 없음")
            self.run_id_var.set("-")
            self.time_var.set("-")
            self.summary_var.set(
                "실행이 끝나면 측정값, 실행 단계, 통신 로그를 여기에서 확인할 수 있어요."
            )
            self.measurement_count_var.set("측정값 0개")
            self.step_count_var.set("단계 0개")
            self.log_count_var.set("로그 0개")
            self._sync_notebook_labels()
            self.export_status_var.set(
                "결과가 준비되면 JSON, Markdown, Excel로 저장할 수 있어요."
            )
            self._configure_status_badge("idle")
            self._set_export_state(False)
            return

        status = self._status_text(self._value(result, "status", default=""))
        dry_run = bool(self._value(result, "dry_run", default=False))
        run_id = str(
            self._value(result, "run_id", "execution_id", default="-")
        )
        started = self._value(
            result,
            "started_at_utc",
            "started_utc",
            "started_at",
            default="",
        )
        finished = self._value(
            result,
            "finished_at_utc",
            "finished_utc",
            "finished_at",
            default="",
        )
        measurements = self._collection(
            result,
            "measurements",
            "measurement_records",
        )
        steps = self._collection(
            result,
            "step_records",
            "steps",
            "records",
        )
        logs = self._collection(
            result,
            "events",
            "logs",
            "log_entries",
        )

        self.status_badge_var.set(
            f"DRY RUN · {self._status_label(status)}"
            if dry_run
            else self._status_label(status)
        )
        self.run_id_var.set(run_id or "-")
        self.time_var.set(
            f"시작 {self._format_datetime(started)}  ·  "
            f"종료 {self._format_datetime(finished)}"
        )
        error_count = self._error_count(result, steps, logs)
        case_count = int(
            self._value(result, "test_case_count", default=0) or 0
        )
        self.summary_var.set(
            (
                f"시험 {case_count}개 · "
                if case_count
                else ""
            )
            + f"측정값 {len(measurements)}개 · 실행 단계 {len(steps)}개 · "
            f"오류 {error_count}개"
        )
        self._configure_status_badge(self._status_color_key(status))

        for measurement in measurements:
            self.measurement_tree.insert(
                "",
                tk.END,
                values=self._measurement_values(measurement),
            )
        for offset, step in enumerate(steps, start=1):
            self.step_tree.insert(
                "",
                tk.END,
                values=self._step_values(step, offset),
            )
        for log in logs:
            self.log_tree.insert(
                "",
                tk.END,
                values=self._log_values(log),
            )

        self.measurement_count_var.set(f"측정값 {len(measurements)}개")
        self.step_count_var.set(f"단계 {len(steps)}개")
        self.log_count_var.set(f"로그 {len(logs)}개")
        self._sync_notebook_labels()
        self.export_status_var.set(
            "사람이 읽을 보고서, 전체 원본 데이터, Excel 표 중에서 고르거나 "
            "‘세 가지 모두 저장’을 눌러 한 폴더에 보관하세요."
        )
        self._set_export_state(True)

    def set_autosave_status(
        self,
        *,
        path: str | Path | None = None,
        error: str = "",
    ) -> None:
        """Show whether the automatic actual-run JSON copy was preserved."""

        if error:
            self.export_status_var.set(
                "자동 저장에 실패했어요. 아래 저장 버튼으로 결과를 바로 "
                f"보관해 주세요. 원인: {error}"
            )
            return
        if path is not None:
            self.export_status_var.set(
                f"전체 원본 JSON을 자동 저장했어요: {Path(path)}"
            )

    @staticmethod
    def _value(obj: Any, *names: str, default: Any = None) -> Any:
        for name in names:
            if isinstance(obj, dict) and name in obj:
                return obj[name]
            if hasattr(obj, name):
                return getattr(obj, name)
        return default

    @classmethod
    def _collection(cls, obj: Any, *names: str) -> tuple[Any, ...]:
        value = cls._value(obj, *names, default=())
        if value is None or isinstance(value, (str, bytes, dict)):
            return ()
        try:
            return tuple(value)
        except TypeError:
            return ()

    @staticmethod
    def _status_text(value: Any) -> str:
        return str(getattr(value, "value", value) or "").casefold()

    @staticmethod
    def _status_label(status: str) -> str:
        return {
            "completed": "완료",
            "success": "완료",
            "passed": "완료",
            "dry_run_completed": "완료",
            "stopped": "중지됨",
            "cancelled": "중지됨",
            "canceled": "중지됨",
            "emergency_stopped": "비상정지",
            "failed": "오류",
            "error": "오류",
        }.get(status, status.upper() or "상태 미확인")

    @staticmethod
    def _status_color_key(status: str) -> str:
        if status in {"completed", "success", "passed", "dry_run_completed"}:
            return "success"
        if status in {"stopped", "cancelled", "canceled"}:
            return "warning"
        if status in {"failed", "error", "emergency_stopped"}:
            return "danger"
        return "idle"

    @staticmethod
    def _format_datetime(value: Any) -> str:
        if isinstance(value, datetime):
            return value.astimezone().strftime("%Y-%m-%d %H:%M:%S")
        text = str(value or "").strip()
        if not text:
            return "-"
        normalized = (
            f"{text[:-1]}+00:00"
            if text.endswith(("Z", "z"))
            else text
        )
        try:
            parsed = datetime.fromisoformat(normalized)
        except ValueError:
            return text.replace("T", " ")[:19]
        if parsed.tzinfo is not None:
            parsed = parsed.astimezone()
        return parsed.strftime("%Y-%m-%d %H:%M:%S")

    @staticmethod
    def _text(value: Any) -> str:
        if value is None:
            return ""
        enum_value = getattr(value, "value", value)
        return str(enum_value)

    @classmethod
    def _measurement_values(cls, measurement: Any) -> tuple[str, ...]:
        timestamp = cls._format_datetime(
            cls._value(
                measurement,
                "timestamp_utc",
                "measured_at_utc",
                "timestamp",
                default="",
            )
        )
        explicit_device = cls._value(
            measurement,
            "device_name",
            "instrument_name",
            default="",
        )
        model = cls._value(measurement, "model", default="")
        resource = cls._value(
            measurement,
            "device_resource",
            "resource",
            default="",
        )
        if explicit_device:
            device = explicit_device
        elif model and resource:
            device = f"{model} · {resource}"
        else:
            device = model or resource
        name = cls._value(
            measurement,
            "result_name",
            "name",
            "feature_id",
            default="",
        )
        case_name = cls._text(
            cls._value(measurement, "case_name", default="")
        )
        repeat_index = cls._value(
            measurement,
            "repeat_index",
            default=0,
        )
        if case_name:
            repeat_text = (
                f" · 반복 {repeat_index}"
                if repeat_index
                else ""
            )
            name = f"[{case_name}{repeat_text}] {name}"
        value = cls._value(
            measurement,
            "numeric_value",
            "parsed_value",
            "value",
            default="",
        )
        unit = cls._value(measurement, "unit", default="")
        raw = cls._value(
            measurement,
            "raw_response",
            "response",
            default="",
        )
        if isinstance(value, (tuple, list)):
            display_value = format_display_value(value, cls._text(unit))
            display_unit = cls._text(unit)
        else:
            display_value, display_unit = engineering_value_parts(
                value,
                cls._text(unit),
            )
        return (
            timestamp,
            cls._text(device),
            cls._text(name),
            display_value,
            display_unit,
            cls._text(raw),
        )

    @classmethod
    def _step_values(cls, step: Any, fallback_index: int) -> tuple[str, ...]:
        index = cls._value(
            step,
            "step_index",
            "index",
            default=fallback_index,
        )
        status = cls._value(step, "status", default="")
        device = cls._value(
            step,
            "device_name",
            "device_resource",
            "resource",
            default="PC",
        )
        action = cls._value(
            step,
            "display_name",
            "feature_id",
            "step_kind",
            "step_type",
            "action",
            default="",
        )
        case_name = cls._text(cls._value(step, "case_name", default=""))
        repeat_index = cls._value(step, "repeat_index", default=0)
        if case_name:
            repeat_text = f" · 반복 {repeat_index}" if repeat_index else ""
            action = f"[{case_name}{repeat_text}] {action}"
        command = cls._value(
            step,
            "rendered_command",
            "command",
            default="",
        )
        response = cls._value(step, "response", default="")
        error = cls._value(step, "error", "error_message", default="")
        response_text = cls._text(response)
        if error:
            response_text = (
                f"{response_text} · 오류: {error}"
                if response_text
                else f"오류: {error}"
            )
        return (
            cls._text(index),
            cls._status_label(cls._status_text(status)),
            cls._text(device),
            cls._text(action),
            cls._text(command),
            response_text,
        )

    @classmethod
    def _log_values(cls, log: Any) -> tuple[str, ...]:
        timestamp = cls._format_datetime(
            cls._value(log, "timestamp_utc", "timestamp", default="")
        )
        level = cls._value(log, "level", default="INFO")
        step = cls._value(log, "step_index", default="")
        message = cls._value(log, "message", default="")
        level_text = cls._text(level).casefold()
        level_label = {
            "debug": "상세",
            "info": "안내",
            "warning": "주의",
            "warn": "주의",
            "error": "오류",
            "critical": "긴급",
        }.get(level_text, cls._text(level).upper())
        return (
            timestamp,
            level_label,
            cls._text(step),
            cls._text(message),
        )

    @classmethod
    def _error_count(
        cls,
        result: Any,
        steps: Iterable[Any],
        logs: Iterable[Any],
    ) -> int:
        errors = cls._collection(result, "errors")
        if errors:
            return len(errors)
        step_errors = sum(
            bool(cls._value(step, "error", "error_message", default=""))
            for step in steps
        )
        log_errors = sum(
            cls._text(cls._value(log, "level", default="")).casefold()
            in {"error", "critical"}
            for log in logs
        )
        return int(step_errors + log_errors)

    def _clear_trees(self) -> None:
        for tree in (self.measurement_tree, self.step_tree, self.log_tree):
            tree.delete(*tree.get_children())

    def _sync_notebook_labels(self) -> None:
        self.data_notebook.tab(
            self.measurement_page,
            text=self.measurement_count_var.get(),
        )
        self.data_notebook.tab(
            self.step_page,
            text=self.step_count_var.get(),
        )
        self.data_notebook.tab(
            self.log_page,
            text=self.log_count_var.get(),
        )

    def _configure_status_badge(self, state: str) -> None:
        colors = {
            "idle": (NEUTRAL_LIGHT, SUBTEXT),
            "success": (SUCCESS_LIGHT, SUCCESS),
            "warning": (WARNING_LIGHT, WARNING),
            "danger": (DANGER_LIGHT, DANGER),
        }
        background, foreground = colors.get(state, colors["idle"])
        self.status_badge.configure(
            background=background,
            foreground=foreground,
        )

    def _set_export_state(self, enabled: bool) -> None:
        for button in (
            self.export_markdown_button,
            self.export_json_button,
            self.export_excel_button,
            self.export_all_button,
        ):
            button.configure(
                state="normal" if enabled else "disabled",
                cursor="hand2" if enabled else "arrow",
            )

    def _default_filename(self, extension: str) -> str:
        run_id = self.run_id_var.get().strip()
        safe_id = re.sub(r"[^0-9A-Za-z가-힣._-]+", "_", run_id).strip("._")
        if not safe_id or safe_id == "-":
            safe_id = datetime.now().strftime("%Y%m%d_%H%M%S")
        return f"SCPI_측정결과_{safe_id}{extension}"

    def _choose_save_path(
        self,
        *,
        title: str,
        extension: str,
        filetype_label: str,
    ) -> Path | None:
        path_text = filedialog.asksaveasfilename(
            parent=self,
            title=title,
            initialfile=self._default_filename(extension),
            defaultextension=extension,
            filetypes=(
                (filetype_label, f"*{extension}"),
                ("모든 파일", "*.*"),
            ),
        )
        return Path(path_text) if path_text else None

    def _save_one(
        self,
        *,
        title: str,
        extension: str,
        filetype_label: str,
        saver: ResultSaver,
    ) -> None:
        if self._result is None:
            self.export_status_var.set("먼저 실행 결과를 확인해 주세요.")
            return
        path = self._choose_save_path(
            title=title,
            extension=extension,
            filetype_label=filetype_label,
        )
        if path is None:
            return
        try:
            saved_path = Path(saver(self._result, path))
        except Exception as exc:
            self.export_status_var.set("결과 파일을 저장하지 못했어요.")
            messagebox.showerror(
                "결과 저장 실패",
                "파일을 저장하지 못했어요.\n\n"
                f"{exc}\n\n"
                "저장 폴더의 쓰기 권한과 파일 이름을 확인해 주세요.",
                parent=self,
            )
            return
        self.export_status_var.set(f"‘{saved_path.name}’ 파일로 저장했어요.")
        messagebox.showinfo(
            "결과 저장 완료",
            f"측정 결과를 저장했어요.\n\n{saved_path}",
            parent=self,
        )

    def _export_markdown(self) -> None:
        self._save_one(
            title="Markdown 결과 저장",
            extension=".md",
            filetype_label="Markdown 문서",
            saver=self._markdown_saver,
        )

    def _export_json(self) -> None:
        self._save_one(
            title="JSON 결과 저장",
            extension=".json",
            filetype_label="JSON 데이터",
            saver=self._json_saver,
        )

    def _export_xlsx(self) -> None:
        self._save_one(
            title="Excel 결과 저장",
            extension=".xlsx",
            filetype_label="Excel 통합 문서",
            saver=self._xlsx_saver,
        )

    def _export_all(self) -> None:
        if self._result is None:
            self.export_status_var.set("먼저 실행 결과를 확인해 주세요.")
            return
        directory_text = filedialog.askdirectory(
            parent=self,
            title="세 가지 결과 파일을 저장할 폴더 선택",
            mustexist=True,
        )
        if not directory_text:
            return
        try:
            paths = tuple(
                Path(path)
                for path in self._bundle_exporter(
                    self._result,
                    Path(directory_text),
                )
            )
        except Exception as exc:
            self.export_status_var.set("결과 묶음을 저장하지 못했어요.")
            messagebox.showerror(
                "결과 저장 실패",
                "JSON, Markdown, Excel 파일을 모두 저장하지 못했어요.\n\n"
                f"{exc}\n\n"
                "저장 폴더의 쓰기 권한을 확인해 주세요.",
                parent=self,
            )
            return
        names = ", ".join(path.name for path in paths)
        self.export_status_var.set(
            f"{len(paths)}개 파일을 한 폴더에 저장했어요: {names}"
        )
        messagebox.showinfo(
            "결과 묶음 저장 완료",
            f"{len(paths)}개 결과 파일을 저장했어요.\n\n{directory_text}",
            parent=self,
        )

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
