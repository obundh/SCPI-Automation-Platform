from __future__ import annotations

import queue
import threading
import tkinter as tk
import tkinter.font as tkfont
from dataclasses import replace
from tkinter import ttk
from typing import Any, Callable

from scpi_automation.identity import (
    CatalogCapability,
    ClassificationConfidence,
    ClassificationResult,
    DeviceCategory,
    IdentityParseError,
    InstrumentProfile,
    classify_identity,
    parse_idn_response,
    profile_by_id,
    recommended_profile,
    representative_profiles,
)
from scpi_automation.transport import (
    DiscoveryRecord,
    DiscoveryState,
    VisaDiscoveryError,
    discover_resources,
    identify_resource,
)
from scpi_automation.validation import OperationStatus, ValidationResult
from .category_art import (
    CategoryArtwork,
    TimelineConnector,
    category_colors,
    category_description,
)
from .device_validation_dialog import DeviceValidationDialog


BACKGROUND = "#F4F6F8"
CARD = "#FFFFFF"
TEXT = "#191F28"
SUBTEXT = "#6B7684"
BORDER = "#E5E8EB"
ACCENT = "#3182F6"
ACCENT_DARK = "#1B64DA"
SUCCESS = "#0F9D58"
SUCCESS_LIGHT = "#E8F7EF"
WARNING = "#D97706"
WARNING_LIGHT = "#FFF4E5"
DANGER = "#D92D20"
DANGER_LIGHT = "#FDECEC"
NEUTRAL_LIGHT = "#F2F4F6"

_BACKEND_VALUES = {
    "자동 선택": "",
    "설치된 VISA 사용 (@ivi)": "@ivi",
}

_CATEGORY_ORDER = (
    DeviceCategory.SPECTRUM_ANALYZER,
    DeviceCategory.SIGNAL_GENERATOR,
    DeviceCategory.FUNCTION_GENERATOR,
    DeviceCategory.OSCILLOSCOPE,
    DeviceCategory.DIGITAL_MULTIMETER,
    DeviceCategory.POWER_SUPPLY,
    DeviceCategory.LCR_METER,
    DeviceCategory.NETWORK_ANALYZER,
    DeviceCategory.UNKNOWN,
)

_CONFIRMABLE_CATEGORIES = tuple(
    category
    for category in _CATEGORY_ORDER
    if category is not DeviceCategory.UNKNOWN
)
_CATEGORY_BY_LABEL = {
    category.label_ko: category
    for category in _CONFIRMABLE_CATEGORIES
}


def _button(
    parent: tk.Misc,
    *,
    text: str,
    command: Callable[[], None],
    primary: bool = False,
    width: int | None = None,
) -> tk.Button:
    options: dict[str, Any] = {
        "text": text,
        "command": command,
        "font": ("Segoe UI Semibold", 10),
        "relief": "flat",
        "borderwidth": 0,
        "cursor": "hand2",
        "padx": 20,
        "pady": 10,
        "takefocus": True,
    }
    if width is not None:
        options["width"] = width
    if primary:
        options.update(
            {
                "background": ACCENT,
                "foreground": "#FFFFFF",
                "activebackground": ACCENT_DARK,
                "activeforeground": "#FFFFFF",
            }
        )
    else:
        options.update(
            {
                "background": NEUTRAL_LIGHT,
                "foreground": TEXT,
                "activebackground": "#E5E8EB",
                "activeforeground": TEXT,
            }
        )
    return tk.Button(parent, **options)


def _link_button(
    parent: tk.Misc,
    *,
    text: str,
    command: Callable[[], None],
) -> tk.Button:
    return tk.Button(
        parent,
        text=text,
        command=command,
        font=("Segoe UI", 9, "underline"),
        background=CARD,
        foreground=ACCENT,
        activebackground=CARD,
        activeforeground=ACCENT_DARK,
        relief="flat",
        borderwidth=0,
        cursor="hand2",
        takefocus=True,
    )


class GuidedDeviceDiscoveryTab(tk.Frame):
    """Beginner-friendly, step-by-step VISA discovery and IDN classification."""

    def __init__(
        self,
        master: tk.Misc,
        *,
        on_continue_to_routine: Callable[
            [tuple[DiscoveryRecord, ...]],
            None,
        ]
        | None = None,
    ) -> None:
        super().__init__(master, background=BACKGROUND)
        self._on_continue_to_routine = on_continue_to_routine
        self._events: queue.Queue[tuple[str, Any]] = queue.Queue()
        self._stop_event = threading.Event()
        self._worker: threading.Thread | None = None
        self._poll_after_id: str | None = None
        self._idle_after_ids: set[str] = set()
        self._records: list[DiscoveryRecord] = []
        self._selection_vars: dict[str, tk.BooleanVar] = {}
        self._active_step = 1
        self._direct_input_mode = False
        self._demo_mode = False
        self._pending_confirmation_resources: list[str] = []
        self._confirmation_skipped_resources: set[str] = set()
        self._confirmation_index = 0
        self._confirmation_stopped = False
        self._capability_choice_vars: dict[str, tk.StringVar] = {}
        self._profile_display_to_id: dict[str, str] = {}
        self._validation_dialog: DeviceValidationDialog | None = None

        self.backend_var = tk.StringVar(value="자동 선택")
        self.timeout_var = tk.IntVar(value=1500)
        self.manual_resource_var = tk.StringVar()
        self.manual_idn_var = tk.StringVar()
        self.direct_result_var = tk.StringVar(
            value="장비가 보낸 이름표를 알고 있다면 붙여넣어 확인할 수 있어요."
        )
        self.status_var = tk.StringVar(value="아직 검색을 시작하지 않았어요.")
        self.footer_safety_var = tk.StringVar(
            value="장비 찾기에서는 설정 명령을 보내지 않아요."
        )
        self.search_message_var = tk.StringVar(value="장비를 찾고 있어요…")
        self.result_title_var = tk.StringVar(value="검색 결과")
        self.result_body_var = tk.StringVar(value="")
        self.selection_count_var = tk.StringVar(
            value="루틴에 사용할 장비를 선택해 주세요."
        )
        self.confirmation_progress_var = tk.StringVar(value="")
        self.confirmation_device_var = tk.StringVar(value="")
        self.confirmation_resource_var = tk.StringVar(value="")
        self.confirmation_category_var = tk.StringVar(value="")
        self.confirmation_profile_var = tk.StringVar(value="")
        self.confirmation_profile_note_var = tk.StringVar(value="")
        self.confirmation_feedback_var = tk.StringVar(value="")
        self.advanced_visible = False
        self._ui_scale = 1.0
        self._font_metrics: dict[tk.Misc, tuple[tkfont.Font, int]] = {}
        self._widget_metrics: dict[tuple[tk.Misc, str], float] = {}
        self._layout_metrics: dict[tuple[tk.Misc, str, str], tuple[float, ...]] = {}

        self._build()
        self._capture_scalable_widgets()
        self.apply_ui_scale(1.0)
        self._set_step(1)
        self._poll_after_id = self.after(100, self._poll_events)

    def _build(self) -> None:
        self.columnconfigure(0, weight=1)
        self.rowconfigure(2, weight=1)

        self.header = tk.Frame(self, background=BACKGROUND)
        self.header.grid(row=0, column=0, sticky="ew", padx=34, pady=(26, 10))
        self.header.columnconfigure(0, weight=1)
        tk.Label(
            self.header,
            text="연결된 장비를 함께 찾아볼게요",
            font=("Segoe UI Semibold", 20),
            background=BACKGROUND,
            foreground=TEXT,
        ).grid(row=0, column=0, sticky="w")
        tk.Label(
            self.header,
            text="어려운 명령어는 몰라도 괜찮아요. 연결 확인부터 장비 종류 구분까지 순서대로 안내할게요.",
            font=("Segoe UI", 10),
            background=BACKGROUND,
            foreground=SUBTEXT,
        ).grid(row=1, column=0, sticky="w", pady=(5, 0))

        self.stepper = tk.Frame(self, background=BACKGROUND)
        self.stepper.grid(row=1, column=0, sticky="ew", padx=34, pady=(4, 16))
        for connector_column in (1, 3, 5):
            self.stepper.columnconfigure(connector_column, weight=1)
        self._step_badges: list[tk.Label] = []
        self._step_labels: list[tk.Label] = []
        step_names = ("장비 찾기", "연결 확인", "분류 확인", "선택 완료")
        for index, name in enumerate(step_names, start=1):
            column = (index - 1) * 2
            item = tk.Frame(self.stepper, background=BACKGROUND)
            item.grid(row=0, column=column, sticky="w")
            badge = tk.Label(
                item,
                text=str(index),
                width=3,
                height=1,
                font=("Segoe UI Semibold", 10),
                relief="flat",
            )
            badge.grid(row=0, column=0)
            label = tk.Label(
                item,
                text=name,
                font=("Segoe UI Semibold", 10),
                background=BACKGROUND,
            )
            label.grid(row=0, column=1, padx=(7, 0))
            self._step_badges.append(badge)
            self._step_labels.append(label)
            if index < len(step_names):
                tk.Frame(
                    self.stepper,
                    background=BORDER,
                    height=2,
                ).grid(row=0, column=column + 1, sticky="ew", padx=14)

        self.stage = tk.Frame(self, background=BACKGROUND)
        self.stage.grid(row=2, column=0, sticky="nsew", padx=34)
        self.stage.columnconfigure(0, weight=1)
        self.stage.rowconfigure(0, weight=1)

        self._build_intro_card()
        self._build_searching_card()
        self._build_classification_card()
        self._build_result_card()

        self.footer = tk.Frame(self, background=BACKGROUND)
        self.footer.grid(row=3, column=0, sticky="ew", padx=34, pady=(12, 18))
        self.footer.columnconfigure(0, weight=1)
        tk.Label(
            self.footer,
            textvariable=self.status_var,
            font=("Segoe UI", 9),
            background=BACKGROUND,
            foreground=SUBTEXT,
        ).grid(row=0, column=0, sticky="w")
        tk.Label(
            self.footer,
            textvariable=self.footer_safety_var,
            font=("Segoe UI Semibold", 9),
            background=BACKGROUND,
            foreground=SUCCESS,
        ).grid(row=0, column=1, sticky="e")

    def _new_card(self) -> tk.Frame:
        return tk.Frame(
            self.stage,
            background=CARD,
            highlightbackground=BORDER,
            highlightthickness=1,
            padx=30,
            pady=26,
        )

    def _build_intro_card(self) -> None:
        self.intro_card = self._new_card()
        self.intro_card.grid(row=0, column=0, sticky="nsew")
        self.intro_card.columnconfigure(0, weight=1)

        tk.Label(
            self.intro_card,
            text="1. 먼저, PC에 연결된 장비를 찾아볼게요",
            font=("Segoe UI Semibold", 15),
            background=CARD,
            foreground=TEXT,
        ).grid(row=0, column=0, sticky="w")
        tk.Label(
            self.intro_card,
            text=(
                "VISA는 PC와 계측기가 대화할 때 사용하는 공용 통로예요.\n"
                "검색을 시작하면 이 통로에 연결된 장비가 있는지 확인할게요."
            ),
            font=("Segoe UI", 11),
            background=CARD,
            foreground=SUBTEXT,
            justify="left",
            wraplength=850,
        ).grid(row=1, column=0, sticky="w", pady=(12, 22))

        name_tag = tk.Frame(
            self.intro_card,
            background="#F0F6FF",
            padx=18,
            pady=15,
        )
        name_tag.grid(row=2, column=0, sticky="ew", pady=(0, 24))
        name_tag.columnconfigure(1, weight=1)
        tk.Label(
            name_tag,
            text="IDN?",
            font=("Segoe UI Semibold", 12),
            background="#F0F6FF",
            foreground=ACCENT,
        ).grid(row=0, column=0, sticky="nw", padx=(0, 16))
        tk.Label(
            name_tag,
            text=(
                "장비가 건네는 ‘이름표’라고 생각하면 쉬워요. "
                "제조사, 모델명, 시리얼 번호와 펌웨어 정보가 들어 있어요."
            ),
            font=("Segoe UI", 10),
            background="#F0F6FF",
            foreground=TEXT,
            justify="left",
            wraplength=720,
        ).grid(row=0, column=1, sticky="w")

        action_row = tk.Frame(self.intro_card, background=CARD)
        action_row.grid(row=3, column=0, sticky="w")
        self.primary_search_button = _button(
            action_row,
            text="연결된 장비 찾아보기",
            command=self.start_scan,
            primary=True,
        )
        self.primary_search_button.grid(row=0, column=0, padx=(0, 12))
        self.demo_button = _button(
            action_row,
            text="데모 장비 4대 보기",
            command=self.show_demo_devices,
        )
        self.demo_button.grid(row=0, column=1, padx=(0, 12))
        self.advanced_toggle_button = _link_button(
            action_row,
            text="고급 설정 보기",
            command=self.toggle_advanced,
        )
        self.advanced_toggle_button.grid(row=0, column=2)

        self.advanced_frame = tk.Frame(
            self.intro_card,
            background="#FAFAFA",
            highlightbackground=BORDER,
            highlightthickness=1,
            padx=18,
            pady=16,
        )
        self.advanced_frame.grid(row=4, column=0, sticky="ew", pady=(22, 0))
        self.advanced_frame.columnconfigure(1, weight=1)
        self.advanced_frame.grid_remove()

        tk.Label(
            self.advanced_frame,
            text="연결 방식",
            font=("Segoe UI Semibold", 9),
            background="#FAFAFA",
            foreground=TEXT,
        ).grid(row=0, column=0, sticky="w")
        self.backend_combo = ttk.Combobox(
            self.advanced_frame,
            textvariable=self.backend_var,
            values=tuple(_BACKEND_VALUES),
            state="readonly",
            width=28,
        )
        self.backend_combo.grid(row=0, column=1, sticky="w", padx=(12, 22))
        tk.Label(
            self.advanced_frame,
            text="기다리는 시간",
            font=("Segoe UI Semibold", 9),
            background="#FAFAFA",
            foreground=TEXT,
        ).grid(row=0, column=2, sticky="w")
        self.timeout_spin = ttk.Spinbox(
            self.advanced_frame,
            from_=200,
            to=10_000,
            increment=100,
            textvariable=self.timeout_var,
            width=8,
        )
        self.timeout_spin.grid(row=0, column=3, sticky="w", padx=(12, 4))
        tk.Label(
            self.advanced_frame,
            text="ms",
            font=("Segoe UI", 9),
            background="#FAFAFA",
            foreground=SUBTEXT,
        ).grid(row=0, column=4, sticky="w")

        tk.Frame(self.advanced_frame, background=BORDER, height=1).grid(
            row=1, column=0, columnspan=5, sticky="ew", pady=14
        )
        tk.Label(
            self.advanced_frame,
            text="장비 주소를 알고 있나요?",
            font=("Segoe UI Semibold", 9),
            background="#FAFAFA",
            foreground=TEXT,
        ).grid(row=2, column=0, sticky="w")
        self.manual_resource_entry = ttk.Entry(
            self.advanced_frame,
            textvariable=self.manual_resource_var,
        )
        self.manual_resource_entry.grid(
            row=2, column=1, columnspan=3, sticky="ew", padx=(12, 10)
        )
        self.manual_identify_button = _button(
            self.advanced_frame,
            text="이 주소만 확인",
            command=self.start_manual_resource_identification,
        )
        self.manual_identify_button.configure(padx=12, pady=5, font=("Segoe UI", 9))
        self.manual_identify_button.grid(row=2, column=4, sticky="e")

        tk.Label(
            self.advanced_frame,
            text="장비가 보낸 이름표를 직접 확인할 수도 있어요.",
            font=("Segoe UI Semibold", 9),
            background="#FAFAFA",
            foreground=TEXT,
        ).grid(row=3, column=0, sticky="w", pady=(14, 0))
        self.manual_idn_entry = ttk.Entry(
            self.advanced_frame,
            textvariable=self.manual_idn_var,
        )
        self.manual_idn_entry.grid(
            row=3, column=1, columnspan=3, sticky="ew", padx=(12, 10), pady=(14, 0)
        )
        direct_button = _button(
            self.advanced_frame,
            text="이름표 분류",
            command=self.classify_manual_idn,
        )
        direct_button.configure(padx=12, pady=5, font=("Segoe UI", 9))
        direct_button.grid(row=3, column=4, sticky="e", pady=(14, 0))
        tk.Label(
            self.advanced_frame,
            textvariable=self.direct_result_var,
            font=("Segoe UI", 9),
            background="#FAFAFA",
            foreground=SUBTEXT,
            wraplength=850,
            justify="left",
        ).grid(row=4, column=0, columnspan=5, sticky="w", pady=(9, 0))

    def _build_searching_card(self) -> None:
        self.searching_card = self._new_card()
        self.searching_card.grid(row=0, column=0, sticky="nsew")
        self.searching_card.columnconfigure(0, weight=1)
        self.searching_card.grid_remove()

        icon = tk.Label(
            self.searching_card,
            text="···",
            font=("Segoe UI Semibold", 24),
            background=CARD,
            foreground=ACCENT,
        )
        icon.grid(row=0, column=0, pady=(30, 8))
        tk.Label(
            self.searching_card,
            textvariable=self.search_message_var,
            font=("Segoe UI Semibold", 16),
            background=CARD,
            foreground=TEXT,
        ).grid(row=1, column=0)
        tk.Label(
            self.searching_card,
            text=(
                "연결 주소를 찾은 뒤 장비가 보낸 이름표를 읽고 있어요.\n"
                "이 과정에서는 주파수나 출력을 바꾸지 않아요."
            ),
            font=("Segoe UI", 10),
            background=CARD,
            foreground=SUBTEXT,
            justify="center",
        ).grid(row=2, column=0, pady=(9, 22))
        self.progress = ttk.Progressbar(
            self.searching_card,
            mode="indeterminate",
            length=390,
            style="Friendly.Horizontal.TProgressbar",
        )
        self.progress.grid(row=3, column=0)
        self.cancel_button = _button(
            self.searching_card,
            text="검색 중지",
            command=self.stop_operation,
        )
        self.cancel_button.grid(row=4, column=0, pady=(22, 30))

    def _build_classification_card(self) -> None:
        self.classification_card = self._new_card()
        self.classification_card.grid(row=0, column=0, sticky="nsew")
        self.classification_card.columnconfigure(0, weight=1)
        self.classification_card.rowconfigure(7, weight=1)
        self.classification_card.grid_remove()

        heading = tk.Frame(self.classification_card, background=CARD)
        heading.grid(row=0, column=0, sticky="ew")
        heading.columnconfigure(0, weight=1)
        tk.Label(
            heading,
            text="3. 처음 보는 모델의 명령을 확인할게요",
            font=("Segoe UI Semibold", 15),
            background=CARD,
            foreground=TEXT,
        ).grid(row=0, column=0, sticky="w")
        tk.Label(
            heading,
            textvariable=self.confirmation_progress_var,
            font=("Segoe UI Semibold", 9),
            background="#F0F6FF",
            foreground=ACCENT,
            padx=10,
            pady=4,
        ).grid(row=0, column=1, sticky="e")

        tk.Label(
            self.classification_card,
            text=(
                "장비 이름표가 명령팩과 일치하더라도 바로 지원 완료로 보지 않아요. "
                "기준 명령팩을 고른 뒤 이 실장비에서 통과한 기능만 최종 등록합니다."
            ),
            font=("Segoe UI", 10),
            background=CARD,
            foreground=SUBTEXT,
            justify="left",
            wraplength=900,
        ).grid(row=1, column=0, sticky="w", pady=(7, 12))

        device_box = tk.Frame(
            self.classification_card,
            background="#F8FAFC",
            highlightbackground=BORDER,
            highlightthickness=1,
            padx=15,
            pady=10,
        )
        device_box.grid(row=2, column=0, sticky="ew")
        device_box.columnconfigure(0, weight=1)
        tk.Label(
            device_box,
            textvariable=self.confirmation_device_var,
            font=("Segoe UI Semibold", 11),
            background="#F8FAFC",
            foreground=TEXT,
            anchor="w",
        ).grid(row=0, column=0, sticky="w")
        tk.Label(
            device_box,
            textvariable=self.confirmation_resource_var,
            font=("Segoe UI", 8),
            background="#F8FAFC",
            foreground=SUBTEXT,
            anchor="w",
        ).grid(row=1, column=0, sticky="w", pady=(3, 0))

        chooser = tk.Frame(self.classification_card, background=CARD)
        chooser.grid(row=3, column=0, sticky="ew", pady=(14, 0))
        chooser.columnconfigure(1, weight=1)
        chooser.columnconfigure(3, weight=2)
        tk.Label(
            chooser,
            text="장비 종류",
            font=("Segoe UI Semibold", 9),
            background=CARD,
            foreground=TEXT,
        ).grid(row=0, column=0, sticky="w")
        self.confirmation_category_combo = ttk.Combobox(
            chooser,
            textvariable=self.confirmation_category_var,
            values=tuple(_CATEGORY_BY_LABEL),
            state="readonly",
            width=24,
        )
        self.confirmation_category_combo.grid(
            row=0,
            column=1,
            sticky="ew",
            padx=(10, 22),
        )
        self.confirmation_category_combo.bind(
            "<<ComboboxSelected>>",
            self._on_confirmation_category_changed,
        )
        tk.Label(
            chooser,
            text="비교할 기준 명령팩",
            font=("Segoe UI Semibold", 9),
            background=CARD,
            foreground=TEXT,
        ).grid(row=0, column=2, sticky="w")
        self.confirmation_profile_combo = ttk.Combobox(
            chooser,
            textvariable=self.confirmation_profile_var,
            state="readonly",
            width=36,
        )
        self.confirmation_profile_combo.grid(
            row=0,
            column=3,
            sticky="ew",
            padx=(10, 0),
        )
        self.confirmation_profile_combo.bind(
            "<<ComboboxSelected>>",
            self._on_confirmation_profile_changed,
        )

        tk.Label(
            self.classification_card,
            textvariable=self.confirmation_profile_note_var,
            font=("Segoe UI", 9),
            background=CARD,
            foreground=SUBTEXT,
            justify="left",
            wraplength=900,
        ).grid(row=4, column=0, sticky="w", pady=(7, 9))

        capability_heading = tk.Frame(self.classification_card, background=CARD)
        capability_heading.grid(row=5, column=0, sticky="ew")
        capability_heading.columnconfigure(0, weight=1)
        tk.Label(
            capability_heading,
            text="이 명령팩에서 검증할 기능",
            font=("Segoe UI Semibold", 10),
            background=CARD,
            foreground=TEXT,
        ).grid(row=0, column=0, sticky="w")
        tk.Label(
            capability_heading,
            text="기능마다 조회·쓰기·복원 결과를 따로 남겨요.",
            font=("Segoe UI", 8),
            background=CARD,
            foreground=WARNING,
        ).grid(row=0, column=1, sticky="e")

        safety_note = tk.Frame(
            self.classification_card,
            background=WARNING_LIGHT,
            padx=12,
            pady=8,
        )
        safety_note.grid(row=6, column=0, sticky="ew", pady=(7, 0))
        safety_note.columnconfigure(1, weight=1)
        tk.Label(
            safety_note,
            text="주의",
            font=("Segoe UI Semibold", 8),
            background=WARNING_LIGHT,
            foreground=WARNING,
        ).grid(row=0, column=0, sticky="nw", padx=(0, 10))
        tk.Label(
            safety_note,
            text=(
                "조회부터 확인한 뒤 되돌릴 수 있는 설정만 시험해요.\n"
                "출력·리셋·파일·교정 명령은 별도 승인 또는 수동 확인으로 남깁니다."
            ),
            font=("Segoe UI", 8),
            background=WARNING_LIGHT,
            foreground=TEXT,
            justify="left",
            wraplength=820,
        ).grid(row=0, column=1, sticky="w")

        self.confirmation_capability_shell = tk.Frame(
            self.classification_card,
            background="#FAFBFC",
            highlightbackground=BORDER,
            highlightthickness=1,
        )
        self.confirmation_capability_shell.grid(
            row=7,
            column=0,
            sticky="nsew",
            pady=(7, 0),
        )
        self.confirmation_capability_shell.columnconfigure(0, weight=1)
        self.confirmation_capability_shell.rowconfigure(0, weight=1)
        self.confirmation_capability_canvas = tk.Canvas(
            self.confirmation_capability_shell,
            background="#FAFBFC",
            highlightthickness=0,
            height=180,
        )
        self.confirmation_capability_scroll = ttk.Scrollbar(
            self.confirmation_capability_shell,
            orient="vertical",
            command=self.confirmation_capability_canvas.yview,
        )
        self.confirmation_capability_canvas.configure(
            yscrollcommand=self.confirmation_capability_scroll.set,
        )
        self.confirmation_capability_canvas.grid(
            row=0,
            column=0,
            sticky="nsew",
        )
        self.confirmation_capability_scroll.grid(
            row=0,
            column=1,
            sticky="ns",
        )
        self.confirmation_capability_list = tk.Frame(
            self.confirmation_capability_canvas,
            background="#FAFBFC",
        )
        self.confirmation_capability_window = (
            self.confirmation_capability_canvas.create_window(
                (0, 0),
                window=self.confirmation_capability_list,
                anchor="nw",
            )
        )
        self.confirmation_capability_list.bind(
            "<Configure>",
            self._update_confirmation_scrollregion,
        )
        self.confirmation_capability_canvas.bind(
            "<Configure>",
            self._resize_confirmation_window,
        )

        feedback = tk.Label(
            self.classification_card,
            textvariable=self.confirmation_feedback_var,
            font=("Segoe UI Semibold", 9),
            background=CARD,
            foreground=WARNING,
            justify="left",
            wraplength=900,
        )
        feedback.grid(row=8, column=0, sticky="w", pady=(8, 0))

        actions = tk.Frame(self.classification_card, background=CARD)
        actions.grid(row=9, column=0, sticky="ew", pady=(10, 0))
        actions.columnconfigure(1, weight=1)
        self.skip_confirmation_button = _button(
            actions,
            text="이 장비는 나중에 확인",
            command=self._skip_current_confirmation,
        )
        self.skip_confirmation_button.grid(row=0, column=0, sticky="w")
        tk.Label(
            actions,
            text="첫 적용: 출력 OFF · 조회 명령 · 낮은 설정값부터",
            font=("Segoe UI", 8),
            background=CARD,
            foreground=WARNING,
        ).grid(row=0, column=1, sticky="e", padx=14)
        self.confirm_compatibility_button = _button(
            actions,
            text="기능별 검증 시작",
            command=self._confirm_current_compatibility,
            primary=True,
        )
        self.confirm_compatibility_button.grid(row=0, column=2, sticky="e")

        # Keep the questions usable at the application's normal 780 px height:
        # device/profile context stays on the left while the long capability
        # checklist receives the full right-hand height.
        self.classification_card.configure(pady=18)
        self.classification_card.columnconfigure(0, weight=0, minsize=350)
        self.classification_card.columnconfigure(1, weight=1)
        self.classification_card.rowconfigure(7, weight=0)
        self.classification_card.rowconfigure(5, weight=1)
        heading.grid_configure(columnspan=2)
        self.classification_card.grid_slaves(row=1, column=0)[0].grid_configure(
            columnspan=2,
        )
        device_box.grid_configure(
            row=2,
            column=0,
            sticky="new",
            padx=(0, 18),
        )

        chooser.columnconfigure(0, weight=1)
        chooser.columnconfigure(1, weight=0)
        chooser.columnconfigure(2, weight=0)
        chooser.columnconfigure(3, weight=0)
        category_label = chooser.grid_slaves(row=0, column=0)[0]
        profile_label = chooser.grid_slaves(row=0, column=2)[0]
        category_label.grid_configure(row=0, column=0, sticky="w")
        self.confirmation_category_combo.grid_configure(
            row=1,
            column=0,
            sticky="ew",
            padx=0,
            pady=(5, 11),
        )
        profile_label.grid_configure(row=2, column=0, sticky="w")
        self.confirmation_profile_combo.grid_configure(
            row=3,
            column=0,
            sticky="ew",
            padx=0,
            pady=(5, 0),
        )
        chooser.grid_configure(
            row=3,
            column=0,
            sticky="new",
            padx=(0, 18),
            pady=(12, 0),
        )
        self.classification_card.grid_slaves(row=4, column=0)[0].configure(
            wraplength=320,
        )
        self.classification_card.grid_slaves(row=4, column=0)[0].grid_configure(
            row=4,
            column=0,
            sticky="nw",
            padx=(0, 18),
        )
        safety_note.grid_configure(
            row=5,
            column=0,
            sticky="new",
            padx=(0, 18),
            pady=(8, 0),
        )
        for label in safety_note.winfo_children():
            if isinstance(label, tk.Label) and label.cget("text") != "주의":
                label.configure(wraplength=285)

        capability_heading.grid_configure(
            row=2,
            column=1,
            sticky="ew",
        )
        self.confirmation_capability_shell.grid_configure(
            row=3,
            column=1,
            rowspan=3,
            sticky="nsew",
            pady=(7, 0),
        )
        feedback.grid_configure(
            row=6,
            column=1,
            sticky="w",
            pady=(7, 0),
        )
        actions.grid_configure(
            row=7,
            column=0,
            columnspan=2,
            sticky="ew",
            pady=(9, 0),
        )

    def _build_result_card(self) -> None:
        self.result_card = self._new_card()
        self.result_card.grid(row=0, column=0, sticky="nsew")
        self.result_card.columnconfigure(0, weight=1)
        self.result_card.rowconfigure(3, weight=1)
        self.result_card.grid_remove()

        self.result_icon = tk.Label(
            self.result_card,
            text="✓",
            width=3,
            font=("Segoe UI Semibold", 16),
            background=SUCCESS_LIGHT,
            foreground=SUCCESS,
        )
        self.result_icon.grid(row=0, column=0, sticky="w")
        tk.Label(
            self.result_card,
            textvariable=self.result_title_var,
            font=("Segoe UI Semibold", 16),
            background=CARD,
            foreground=TEXT,
        ).grid(row=1, column=0, sticky="w", pady=(12, 0))
        tk.Label(
            self.result_card,
            textvariable=self.result_body_var,
            font=("Segoe UI", 10),
            background=CARD,
            foreground=SUBTEXT,
            justify="left",
            wraplength=900,
        ).grid(row=2, column=0, sticky="w", pady=(5, 10))

        self.result_list_shell = tk.Frame(self.result_card, background=CARD)
        self.result_list_shell.grid(row=3, column=0, sticky="nsew")
        self.result_list_shell.columnconfigure(0, weight=1)
        self.result_list_shell.rowconfigure(0, weight=1)
        self.result_canvas = tk.Canvas(
            self.result_list_shell,
            background=CARD,
            highlightthickness=0,
            height=300,
        )
        self.result_scroll = ttk.Scrollbar(
            self.result_list_shell,
            orient="vertical",
            command=self.result_canvas.yview,
        )
        self.result_canvas.configure(yscrollcommand=self.result_scroll.set)
        self.result_canvas.grid(row=0, column=0, sticky="nsew")
        self.result_scroll.grid(row=0, column=1, sticky="ns")
        self.result_list = tk.Frame(self.result_canvas, background=CARD)
        self.result_window = self.result_canvas.create_window(
            (0, 0),
            window=self.result_list,
            anchor="nw",
        )
        self.result_list.bind("<Configure>", self._update_result_scrollregion)
        self.result_canvas.bind("<Configure>", self._resize_result_window)

        self.help_frame = tk.Frame(
            self.result_card,
            background=WARNING_LIGHT,
            padx=18,
            pady=15,
        )
        self.help_frame.grid(row=4, column=0, sticky="ew", pady=(14, 0))
        self.help_frame.columnconfigure(0, weight=1)
        self.help_frame.grid_remove()
        self.help_title_var = tk.StringVar(value="장비가 보이지 않나요?")
        self.help_body_var = tk.StringVar(value="")
        tk.Label(
            self.help_frame,
            textvariable=self.help_title_var,
            font=("Segoe UI Semibold", 11),
            background=WARNING_LIGHT,
            foreground=TEXT,
        ).grid(row=0, column=0, sticky="w")
        tk.Label(
            self.help_frame,
            textvariable=self.help_body_var,
            font=("Segoe UI", 9),
            background=WARNING_LIGHT,
            foreground=TEXT,
            justify="left",
            wraplength=880,
        ).grid(row=1, column=0, sticky="w", pady=(7, 0))

        actions = tk.Frame(self.result_card, background=CARD)
        actions.grid(row=5, column=0, sticky="ew", pady=(14, 0))
        actions.columnconfigure(1, weight=1)
        left_actions = tk.Frame(actions, background=CARD)
        left_actions.grid(row=0, column=0, sticky="w")
        self.retry_button = _button(
            left_actions,
            text="다시 찾아보기",
            command=self.start_scan,
        )
        self.retry_button.grid(row=0, column=0, padx=(0, 10))
        _button(
            left_actions,
            text="연결 설정 확인",
            command=self.return_to_advanced,
        ).grid(row=0, column=1)

        routine_actions = tk.Frame(actions, background=CARD)
        routine_actions.grid(row=0, column=2, sticky="e")
        self.selection_count_label = tk.Label(
            routine_actions,
            textvariable=self.selection_count_var,
            font=("Segoe UI Semibold", 9),
            background=CARD,
            foreground=SUBTEXT,
            width=28,
            anchor="e",
        )
        self.selection_count_label.grid(
            row=0,
            column=0,
            sticky="e",
            padx=(0, 10),
        )
        self.continue_to_routine_button = _button(
            routine_actions,
            text="선택한 장비로 루틴 만들기",
            command=self._continue_to_routine,
            primary=True,
            width=23,
        )
        self.continue_to_routine_button.configure(state="disabled")
        self.continue_to_routine_button.grid(row=0, column=1, sticky="e")

    def _set_step(self, step: int) -> None:
        self._active_step = max(1, min(4, step))
        self.footer_safety_var.set(
            {
                1: "장비 찾기에서는 설정 명령을 보내지 않아요.",
                2: "연결 확인은 *IDN? 이름표 조회만 사용해요.",
                3: "설정 검증은 시험값을 쓴 뒤 원래값 복원까지 확인해요.",
                4: "루틴에는 실장비에서 통과한 operation만 사용할 수 있어요.",
            }[self._active_step]
        )
        for index, (badge, label) in enumerate(
            zip(self._step_badges, self._step_labels),
            start=1,
        ):
            if index < self._active_step:
                badge.configure(text="✓", background=SUCCESS, foreground="#FFFFFF")
                label.configure(foreground=SUCCESS)
            elif index == self._active_step:
                badge.configure(text=str(index), background=ACCENT, foreground="#FFFFFF")
                label.configure(foreground=TEXT)
            else:
                badge.configure(text=str(index), background="#E5E8EB", foreground=SUBTEXT)
                label.configure(foreground=SUBTEXT)

    def _show_stage(self, target: tk.Frame) -> None:
        for frame in (
            self.intro_card,
            self.searching_card,
            self.classification_card,
            self.result_card,
        ):
            frame.grid_remove()
        target.grid()

    def _schedule_idle(self, callback: Callable[[], None]) -> None:
        """Run one layout callback and keep it cancellable during shutdown."""

        holder: dict[str, str] = {}

        def run() -> None:
            after_id = holder.get("id")
            if after_id is not None:
                self._idle_after_ids.discard(after_id)
            try:
                if self.winfo_exists():
                    callback()
            except tk.TclError:
                pass

        try:
            after_id = self.after_idle(run)
        except tk.TclError:
            return
        holder["id"] = after_id
        self._idle_after_ids.add(after_id)

    def toggle_advanced(self) -> None:
        self.advanced_visible = not self.advanced_visible
        if self.advanced_visible:
            self.advanced_frame.grid()
            self.advanced_toggle_button.configure(text="고급 설정 접기")
        else:
            self.advanced_frame.grid_remove()
            self.advanced_toggle_button.configure(text="고급 설정 보기")

    def return_to_advanced(self) -> None:
        self._show_stage(self.intro_card)
        self._set_step(1)
        if not self.advanced_visible:
            self.toggle_advanced()
        self.status_var.set("연결 설정을 확인한 뒤 다시 검색해 주세요.")

    def _selected_backend(self) -> str:
        return _BACKEND_VALUES.get(self.backend_var.get(), "")

    @property
    def selected_backend(self) -> str:
        """Return the backend token used for subsequent VISA sessions."""

        return self._selected_backend()

    @property
    def selected_timeout_ms(self) -> int:
        """Return a bounded timeout without showing a validation dialog."""

        try:
            timeout = int(self.timeout_var.get())
        except (tk.TclError, TypeError, ValueError):
            return 2_000
        return timeout if 200 <= timeout <= 10_000 else 2_000

    def _validated_timeout(self) -> int | None:
        try:
            timeout = int(self.timeout_var.get())
        except (TypeError, ValueError, tk.TclError):
            self.direct_result_var.set("기다리는 시간은 숫자로 입력해 주세요.")
            return None
        if not 200 <= timeout <= 10_000:
            self.direct_result_var.set("기다리는 시간은 200~10000 ms 사이로 입력해 주세요.")
            return None
        return timeout

    def _start_worker(self, target: Callable[[], None], message: str) -> None:
        if self._worker is not None and self._worker.is_alive():
            self.status_var.set("지금 진행 중인 검색이 끝난 뒤 다시 시도해 주세요.")
            return
        self._records.clear()
        self._reset_routine_selection()
        self._reset_confirmation_flow()
        self._direct_input_mode = False
        self._demo_mode = False
        self._clear_result_cards()
        self._stop_event.clear()
        self.search_message_var.set(message)
        self._show_stage(self.searching_card)
        self._set_step(2)
        self.progress.start(12)
        self.primary_search_button.configure(state="disabled")
        self.demo_button.configure(state="disabled")
        self.manual_identify_button.configure(state="disabled")
        self.status_var.set("장비 연결을 확인하고 있어요…")
        self._worker = threading.Thread(target=target, daemon=True)
        self._worker.start()

    def start_scan(self) -> None:
        timeout = self._validated_timeout()
        if timeout is None:
            return
        backend = self._selected_backend()

        def worker() -> None:
            try:
                records = discover_resources(
                    backend=backend,
                    timeout_ms=timeout,
                    stop_event=self._stop_event,
                    on_record=lambda record: self._events.put(("record", record)),
                )
                self._events.put(
                    (
                        "done",
                        {
                            "count": len(records),
                            "stopped": self._stop_event.is_set(),
                        },
                    )
                )
            except Exception as exc:
                self._events.put(("fatal", exc))

        self._start_worker(worker, "연결된 장비를 찾고 있어요…")

    def start_manual_resource_identification(self) -> None:
        timeout = self._validated_timeout()
        if timeout is None:
            return
        resource = self.manual_resource_var.get().strip()
        if not resource:
            self.direct_result_var.set("먼저 확인할 장비 주소를 입력해 주세요.")
            self.manual_resource_entry.focus_set()
            return
        backend = self._selected_backend()

        def worker() -> None:
            try:
                record = identify_resource(resource, backend=backend, timeout_ms=timeout)
                self._events.put(("record", record))
                self._events.put(("done", {"count": 1, "stopped": False}))
            except Exception as exc:
                self._events.put(("fatal", exc))

        self._start_worker(worker, "입력한 주소에서 장비 이름표를 확인하고 있어요…")

    def stop_operation(self) -> None:
        self._stop_event.set()
        self.search_message_var.set("검색을 안전하게 마무리하고 있어요…")
        self.status_var.set(
            "중지를 요청했어요. 이미 확인 중인 장비는 기다리는 시간이 끝난 뒤 멈출 수 있어요."
        )

    def classify_manual_idn(self) -> None:
        try:
            identity = parse_idn_response(self.manual_idn_var.get())
            result = classify_identity(identity)
        except IdentityParseError as exc:
            self.direct_result_var.set(f"이름표 형식을 확인해 주세요. {exc}")
            return

        record = DiscoveryRecord(
            resource="직접 입력한 이름표",
            interface="-",
            state=DiscoveryState.IDENTIFIED,
            identity=identity,
            classification=result,
            message="직접 입력한 IDN을 분류했습니다. 실제 연결 확인은 아직 하지 않았습니다.",
        )
        self._records = [record]
        self._reset_routine_selection()
        self._reset_confirmation_flow()
        self._direct_input_mode = True
        self._demo_mode = False
        self.direct_result_var.set(self._friendly_classification(record))
        self._finish_results(stopped=False)

    def show_demo_devices(self) -> None:
        if self._worker is not None and self._worker.is_alive():
            self.status_var.set("지금 진행 중인 검색이 끝난 뒤 데모를 열어 주세요.")
            return

        demo_specs = (
            (
                "DEMO::TCPIP::FSV30",
                "TCPIP0",
                "Rohde&Schwarz,FSV30,DEMO-FSV30,3.50",
            ),
            (
                "DEMO::USB::SMB100A",
                "USB0",
                "Rohde&Schwarz,SMB100A,DEMO-SMB100A,4.10",
            ),
            (
                "DEMO::USB::DS1054Z",
                "USB0",
                "RIGOL TECHNOLOGIES,DS1054Z,DEMO-DS1054Z,00.04.05",
            ),
            (
                "DEMO::LAN::34461A",
                "TCPIP0",
                "Keysight Technologies,34461A,DEMO-34461A,A.03.01",
            ),
        )
        records: list[DiscoveryRecord] = []
        for resource, interface, raw_idn in demo_specs:
            identity = parse_idn_response(raw_idn)
            records.append(
                DiscoveryRecord(
                    resource=resource,
                    interface=interface,
                    state=DiscoveryState.IDENTIFIED,
                    identity=identity,
                    classification=classify_identity(identity),
                    message=(
                        "화면 확인용 데모 장비입니다. 실제 VISA 연결이나 실장비 검증 결과가 아닙니다."
                    ),
                )
            )

        self._records = records
        self._reset_routine_selection()
        self._reset_confirmation_flow()
        self._direct_input_mode = False
        self._demo_mode = True
        self._finish_results(stopped=False)

    def _reset_confirmation_flow(self) -> None:
        self._pending_confirmation_resources.clear()
        self._confirmation_skipped_resources.clear()
        self._confirmation_index = 0
        self._confirmation_stopped = False
        self._capability_choice_vars.clear()
        self._profile_display_to_id.clear()
        self.confirmation_category_var.set("")
        self.confirmation_profile_var.set("")
        self.confirmation_profile_note_var.set("")
        self.confirmation_feedback_var.set("")

    def _requires_profile_confirmation(self, record: DiscoveryRecord) -> bool:
        if (
            self._direct_input_mode
            or record.resource == "직접 입력한 이름표"
            or record.resource.startswith("DEMO::")
            or record.resource in self._confirmation_skipped_resources
            or record.state != DiscoveryState.IDENTIFIED
            or record.identity is None
        ):
            return False
        result = record.classification
        if result is None:
            return True
        if (
            result.confidence == ClassificationConfidence.VALIDATED_PROFILE
            and result.profile_id
            and result.compatible_operation_ids
        ):
            return False
        return True

    def _begin_classification_confirmation(self, *, stopped: bool) -> bool:
        pending = [
            record.resource
            for record in self._records
            if self._requires_profile_confirmation(record)
        ]
        if not pending:
            return False
        self._pending_confirmation_resources = pending
        self._confirmation_index = 0
        self._confirmation_stopped = stopped
        self._show_stage(self.classification_card)
        self._set_step(3)
        self._show_current_confirmation()
        return True

    def _current_confirmation_record(self) -> DiscoveryRecord | None:
        if not (
            0
            <= self._confirmation_index
            < len(self._pending_confirmation_resources)
        ):
            return None
        resource = self._pending_confirmation_resources[self._confirmation_index]
        return next(
            (record for record in self._records if record.resource == resource),
            None,
        )

    def _show_current_confirmation(self) -> None:
        record = self._current_confirmation_record()
        if record is None or record.identity is None:
            self._advance_confirmation()
            return

        identity = record.identity
        self.confirmation_progress_var.set(
            f"{self._confirmation_index + 1} / "
            f"{len(self._pending_confirmation_resources)}대"
        )
        self.confirmation_device_var.set(
            f"{identity.manufacturer} {identity.model}".strip()
        )
        self.confirmation_resource_var.set(
            f"연결 주소  {record.resource}   ·   펌웨어  "
            f"{identity.firmware or '정보 없음'}"
        )
        self.confirmation_feedback_var.set("")

        result = record.classification
        suggested_category = (
            result.category
            if result is not None
            and result.category is not DeviceCategory.UNKNOWN
            else None
        )
        if suggested_category is None:
            self.confirmation_category_var.set("")
            self.confirmation_profile_var.set("")
            self.confirmation_profile_combo.configure(values=())
            self._profile_display_to_id.clear()
            self.confirmation_profile_note_var.set(
                "먼저 장비 종류를 선택하면 비교할 기준 명령팩을 보여드릴게요."
            )
            self._render_confirmation_capabilities(None)
            return

        self.confirmation_category_var.set(suggested_category.label_ko)
        self._load_confirmation_profiles(suggested_category)

    def _on_confirmation_category_changed(
        self,
        _event: tk.Event[Any] | None = None,
    ) -> None:
        category = _CATEGORY_BY_LABEL.get(self.confirmation_category_var.get())
        if category is None:
            self.confirmation_profile_var.set("")
            self.confirmation_profile_combo.configure(values=())
            self._profile_display_to_id.clear()
            self._render_confirmation_capabilities(None)
            return
        self.confirmation_feedback_var.set("")
        self._load_confirmation_profiles(category)

    @staticmethod
    def _profile_display_text(profile: InstrumentProfile) -> str:
        models = ", ".join(profile.models)
        if not models or models.casefold() == profile.model_family.casefold():
            return profile.display_name
        return f"{profile.display_name} · {models}"

    def _load_confirmation_profiles(self, category: DeviceCategory) -> None:
        try:
            profiles = representative_profiles(category)
        except (OSError, ValueError) as exc:
            self._profile_display_to_id.clear()
            self.confirmation_profile_var.set("")
            self.confirmation_profile_combo.configure(values=())
            self.confirmation_profile_note_var.set(
                "기준 명령팩 목록을 읽지 못했어요. "
                "프로그램의 로컬 카탈로그 파일을 확인해 주세요."
            )
            self.confirmation_feedback_var.set(str(exc))
            self._render_confirmation_capabilities(None)
            return
        self._profile_display_to_id = {
            self._profile_display_text(profile): profile.profile_id
            for profile in profiles
        }
        displays = tuple(self._profile_display_to_id)
        self.confirmation_profile_combo.configure(values=displays)
        if not profiles:
            self.confirmation_profile_var.set("")
            self.confirmation_profile_note_var.set(
                "이 종류에는 아직 비교할 기준 명령팩이 등록되지 않았어요."
            )
            self._render_confirmation_capabilities(None)
            return

        record = self._current_confirmation_record()
        preferred = recommended_profile(
            category,
            record.identity if record is not None else None,
        )
        selected = preferred or profiles[0]
        self.confirmation_profile_var.set(self._profile_display_text(selected))
        self._show_confirmation_profile(selected)

    def _on_confirmation_profile_changed(
        self,
        _event: tk.Event[Any] | None = None,
    ) -> None:
        profile_id = self._profile_display_to_id.get(
            self.confirmation_profile_var.get(),
            "",
        )
        profile = profile_by_id(profile_id)
        self.confirmation_feedback_var.set("")
        self._show_confirmation_profile(profile)

    def _show_confirmation_profile(
        self,
        profile: InstrumentProfile | None,
    ) -> None:
        if profile is None:
            self.confirmation_profile_note_var.set(
                "기준 명령팩을 선택해 주세요."
            )
            self._render_confirmation_capabilities(None)
            return
        verification = (
            "과거 실장비 확인 기록 있음 · 현재 장비는 다시 검증"
            if profile.hardware_verified
            else "매뉴얼·드라이버 자료 기반 · 현재 장비에서 기능별 검증 필요"
        )
        self.confirmation_profile_note_var.set(
            f"후보 명령팩  {profile.profile_id}   ·   {verification}"
        )
        self._render_confirmation_capabilities(profile)

    def _render_confirmation_capabilities(
        self,
        profile: InstrumentProfile | None,
    ) -> None:
        for child in self.confirmation_capability_list.winfo_children():
            child.destroy()
        self._capability_choice_vars.clear()

        if profile is None or not profile.capabilities:
            message = (
                "장비 종류와 기준 명령팩을 선택하면 검증할 기능이 여기에 나타나요."
                if profile is None
                else "이 기준 명령팩에는 아직 확인할 기능이 등록되지 않았어요."
            )
            tk.Label(
                self.confirmation_capability_list,
                text=message,
                font=("Segoe UI", 10),
                background="#FAFBFC",
                foreground=SUBTEXT,
                pady=36,
            ).grid(row=0, column=0, sticky="ew")
            self.confirmation_capability_list.columnconfigure(0, weight=1)
            self.confirm_compatibility_button.configure(state="disabled")
        else:
            self.confirmation_capability_list.columnconfigure(0, weight=1)
            for row, capability in enumerate(profile.capabilities):
                self._render_capability_question(
                    row=row,
                    capability=capability,
                )
            self.confirm_compatibility_button.configure(state="normal")

        self.confirmation_capability_canvas.yview_moveto(0.0)
        self._bind_confirmation_mousewheel()
        self._capture_scalable_widgets()
        self.apply_ui_scale(self._ui_scale)
        self._schedule_idle(self._sync_confirmation_scrollbar)

    def _render_capability_question(
        self,
        *,
        row: int,
        capability: CatalogCapability,
    ) -> None:
        row_background = "#FAFBFC" if row % 2 == 0 else "#F5F7F9"
        item = tk.Frame(
            self.confirmation_capability_list,
            background=row_background,
            padx=12,
            pady=8,
        )
        item.grid(row=row, column=0, sticky="ew")
        item.columnconfigure(0, weight=1)

        label = capability.label_ko.strip() or capability.capability_id
        risk_note = (
            "  ·  출력·보호 관련 주의 기능"
            if capability.risk_level.lower() in {"high", "critical"}
            else ""
        )
        tk.Label(
            item,
            text=f"{label}{risk_note}",
            font=("Segoe UI Semibold", 9),
            background=row_background,
            foreground=DANGER if risk_note else TEXT,
            anchor="w",
            justify="left",
            wraplength=470,
        ).grid(row=0, column=0, sticky="w")
        tk.Label(
            item,
            text=(
                f"{capability.capability_id}  ·  "
                f"{', '.join(operation.name.upper() for operation in capability.operations)}"
            ),
            font=("Segoe UI", 7),
            background=row_background,
            foreground=SUBTEXT,
            anchor="w",
        ).grid(row=1, column=0, sticky="w", pady=(2, 0))

        operation_count = len(capability.operations)
        tk.Label(
            item,
            text=f"{operation_count}개 명령 · 미검증",
            font=("Segoe UI Semibold", 8),
            background="#FFF4E5",
            foreground=WARNING,
            padx=9,
            pady=4,
        ).grid(
            row=0,
            column=1,
            rowspan=2,
            sticky="e",
            padx=(12, 0),
        )

    def _confirm_current_compatibility(self) -> None:
        record = self._current_confirmation_record()
        if record is None:
            return
        category = _CATEGORY_BY_LABEL.get(self.confirmation_category_var.get())
        if category is None:
            self.confirmation_feedback_var.set(
                "장비 종류를 먼저 선택해 주세요."
            )
            self.confirmation_category_combo.focus_set()
            return
        profile_id = self._profile_display_to_id.get(
            self.confirmation_profile_var.get(),
            "",
        )
        profile = profile_by_id(profile_id)
        if profile is None or profile.category is not category:
            self.confirmation_feedback_var.set(
                "이 장비와 비교할 기준 명령팩을 선택해 주세요."
            )
            self.confirmation_profile_combo.focus_set()
            return
        timeout = self._validated_timeout()
        if timeout is None:
            return
        if (
            self._validation_dialog is not None
            and self._validation_dialog.winfo_exists()
        ):
            self._validation_dialog.lift()
            self._validation_dialog.focus_force()
            return

        def completed(result: ValidationResult) -> None:
            self._apply_validation_result(
                resource=record.resource,
                category=category,
                profile=profile,
                result=result,
            )

        self._validation_dialog = DeviceValidationDialog(
            self,
            record=record,
            profile=profile,
            backend=self._selected_backend(),
            timeout_ms=timeout,
            on_complete=completed,
        )
        self.confirmation_feedback_var.set(
            "별도 검증 창을 열었어요. 통과한 operation만 최종 기능으로 저장됩니다."
        )

    def _apply_validation_result(
        self,
        *,
        resource: str,
        category: DeviceCategory,
        profile: InstrumentProfile,
        result: ValidationResult,
    ) -> None:
        record = next(
            (item for item in self._records if item.resource == resource),
            None,
        )
        if record is None:
            return
        compatible_capabilities = result.compatible_capability_ids
        incompatible_capabilities = tuple(
            dict.fromkeys(
                operation_id.rsplit("::", 1)[0]
                for operation_id in result.incompatible_operation_ids
                if operation_id.rsplit("::", 1)[0]
                not in compatible_capabilities
            )
        )
        option_response = next(
            (
                operation.response
                for operation in result.operations
                if (
                    operation.kind.value == "query"
                    and "".join(
                        operation.command_template.split()
                    ).upper()
                    == "*OPT?"
                )
                and operation.status is OperationStatus.PASS
            ),
            "",
        )
        has_option_query = any(
            operation.kind.value == "query"
            and "".join(operation.command_template.split()).upper()
            == "*OPT?"
            for operation in result.operations
        )
        option_state = (
            "queried"
            if option_response.strip()
            else ("unqueried" if has_option_query else "unsupported")
        )
        base_result = record.classification or ClassificationResult(
            category=category,
            confidence=ClassificationConfidence.UNKNOWN,
            matched_rule="실장비 검증 전",
        )
        updated_result = replace(
            base_result,
            category=category,
            confidence=ClassificationConfidence.VALIDATED_PROFILE,
            matched_rule=(
                f"후보 명령팩 {profile.profile_id}을 사용해 VISA 실장비에서 "
                "operation별 조회·쓰기·복구 결과를 확인함"
            ),
            profile_id=profile.profile_id,
            # ``fully_resolved`` covers only the currently structured
            # operations.  The manual-index audit layer may still contain raw
            # candidates or an incomplete manual extract, so this seed
            # catalog must not claim exhaustive model support.
            profile_status="hardware_validated_partial",
            compatible_capability_ids=compatible_capabilities,
            incompatible_capability_ids=incompatible_capabilities,
            compatible_operation_ids=result.compatible_operation_ids,
            incompatible_operation_ids=result.incompatible_operation_ids,
            unresolved_operation_ids=result.unresolved_operation_ids,
            validation_catalog_fingerprint=result.catalog_fingerprint,
            option_response=option_response,
            option_state=option_state,
        )
        for index, existing in enumerate(self._records):
            if existing.resource == resource:
                self._records[index] = replace(
                    existing,
                    classification=updated_result,
                )
                break

        model = record.identity.model if record.identity else resource
        self.status_var.set(
            f"{model} · 통과한 명령 {len(result.compatible_operation_ids)}개만 "
            "최종 기능으로 저장했어요."
        )
        self._validation_dialog = None
        self._advance_confirmation()

    def _skip_current_confirmation(self) -> None:
        record = self._current_confirmation_record()
        if record is None:
            return
        self._confirmation_skipped_resources.add(record.resource)
        self.status_var.set(
            "이 장비는 확인 전 상태로 남겼어요. 루틴에서는 선택할 수 없어요."
        )
        self._advance_confirmation()

    def _advance_confirmation(self) -> None:
        self._confirmation_index += 1
        if self._confirmation_index < len(
            self._pending_confirmation_resources
        ):
            self._show_current_confirmation()
            return
        self._pending_confirmation_resources.clear()
        self._finish_results(stopped=self._confirmation_stopped)

    def _update_confirmation_scrollregion(
        self,
        _event: tk.Event[Any],
    ) -> None:
        self.confirmation_capability_canvas.configure(
            scrollregion=self.confirmation_capability_canvas.bbox("all"),
        )
        self._schedule_idle(self._sync_confirmation_scrollbar)

    def _resize_confirmation_window(self, event: tk.Event[Any]) -> None:
        self.confirmation_capability_canvas.itemconfigure(
            self.confirmation_capability_window,
            width=event.width,
        )
        self._schedule_idle(self._sync_confirmation_scrollbar)

    def _sync_confirmation_scrollbar(self) -> None:
        bbox = self.confirmation_capability_canvas.bbox("all")
        content_height = 0 if bbox is None else bbox[3] - bbox[1]
        if (
            content_height
            > self.confirmation_capability_canvas.winfo_height() + 2
        ):
            self.confirmation_capability_scroll.grid()
        else:
            self.confirmation_capability_scroll.grid_remove()

    def _on_confirmation_mousewheel(self, event: tk.Event[Any]) -> str:
        if getattr(event, "num", None) == 4:
            units = -1
        elif getattr(event, "num", None) == 5:
            units = 1
        else:
            delta = int(getattr(event, "delta", 0))
            if delta == 0:
                return "break"
            units = -1 if delta > 0 else 1
        self.confirmation_capability_canvas.yview_scroll(units, "units")
        return "break"

    def _bind_confirmation_mousewheel(self) -> None:
        widgets = [
            self.confirmation_capability_canvas,
            self.confirmation_capability_list,
            *self._descendants(self.confirmation_capability_list),
        ]
        for widget in widgets:
            widget.bind("<MouseWheel>", self._on_confirmation_mousewheel)
            widget.bind("<Button-4>", self._on_confirmation_mousewheel)
            widget.bind("<Button-5>", self._on_confirmation_mousewheel)

    def _poll_events(self) -> None:
        try:
            while True:
                event, payload = self._events.get_nowait()
                if event == "record":
                    self._records.append(payload)
                    self.status_var.set(
                        f"장비 주소 {len(self._records)}개를 확인했어요. 이름표를 읽는 중이에요…"
                    )
                elif event == "done":
                    self._finish_results(stopped=bool(payload["stopped"]))
                elif event == "fatal":
                    self._finish_fatal(payload)
        except queue.Empty:
            pass
        try:
            self._poll_after_id = self.after(100, self._poll_events)
        except tk.TclError:
            self._poll_after_id = None

    def _finish_results(self, *, stopped: bool) -> None:
        self.progress.stop()
        self.primary_search_button.configure(state="normal")
        self.demo_button.configure(state="normal")
        self.manual_identify_button.configure(state="normal")
        self._show_stage(self.result_card)
        self._render_result_cards()
        if self._records:
            self.result_card.rowconfigure(3, weight=1)
            self.result_list_shell.grid()
        else:
            self.result_card.rowconfigure(3, weight=0)
            self.result_list_shell.grid_remove()

        identified = [
            record
            for record in self._records
            if record.state == DiscoveryState.IDENTIFIED and record.identity is not None
        ]
        unknown = [
            record
            for record in identified
            if record.classification is None
            or record.classification.confidence
            not in {
                ClassificationConfidence.VALIDATED_PROFILE,
            }
        ]
        failures = [
            record
            for record in self._records
            if record.state in {DiscoveryState.ERROR, DiscoveryState.SKIPPED}
        ]

        if self._demo_mode:
            self.result_icon.configure(text="✓", background=SUCCESS_LIGHT, foreground=SUCCESS)
            self.result_title_var.set("데모 장비 4대를 분류했어요!")
            self.result_body_var.set(
                "실제 장비와 통신하지 않은 화면 예시예요. 아래 장비명과 시리얼은 모두 데모입니다."
            )
            self.help_frame.grid_remove()
        elif self._direct_input_mode:
            self.result_icon.configure(text="✓", background=SUCCESS_LIGHT, foreground=SUCCESS)
            self.result_title_var.set("입력한 장비 이름표를 분류했어요!")
            self.result_body_var.set(
                "이 결과는 입력한 IDN 문자열만 분류한 것이며, 실제 장비 연결은 아직 확인하지 않았어요."
            )
            self.help_frame.grid_remove()
        elif stopped:
            self.result_icon.configure(text="!", background=WARNING_LIGHT, foreground=WARNING)
            self.result_title_var.set("검색을 중지했어요")
            self.result_body_var.set(
                "중지하기 전까지 확인한 결과만 보여드릴게요. 필요하면 다시 검색할 수 있어요."
            )
            self._show_help(
                "다시 확인하고 싶나요?",
                "장비 연결을 그대로 둔 상태에서 ‘다시 찾아보기’를 눌러 주세요.",
            )
        elif not self._records:
            self.result_icon.configure(text="?", background=WARNING_LIGHT, foreground=WARNING)
            self.result_title_var.set("아직 연결된 장비가 보이지 않아요")
            self.result_body_var.set(
                "괜찮아요. 대부분 전원, 케이블 또는 VISA 연결 프로그램을 확인하면 해결돼요."
            )
            self._show_standard_help()
        elif not identified:
            self.result_icon.configure(text="!", background=WARNING_LIGHT, foreground=WARNING)
            self.result_title_var.set("장비 주소는 찾았지만 이름표를 읽지 못했어요")
            self.result_body_var.set(
                f"{len(self._records)}개 연결 주소를 찾았어요. 아래 항목의 해결 방법을 확인해 주세요."
            )
            self._show_standard_help()
        else:
            self.result_icon.configure(text="✓", background=SUCCESS_LIGHT, foreground=SUCCESS)
            self.result_title_var.set(f"{len(identified)}대의 장비를 분류했어요!")
            extra = []
            if unknown:
                extra.append(f"종류를 더 확인해야 하는 장비 {len(unknown)}대")
            if failures:
                extra.append(f"이름표를 읽지 못한 주소 {len(failures)}개")
            suffix = f" ({', '.join(extra)})" if extra else ""
            self.result_body_var.set(
                "장비가 건넨 이름표를 바탕으로 검색 결과를 정리했어요."
                f"{suffix}"
            )
            if unknown or failures:
                self._show_help(
                    "일부 장비는 추가 확인이 필요해요",
                    "‘자세히 보기’에서 전체 이름표와 연결 메시지를 확인해 주세요. "
                    "분류되지 않았다고 해서 장비가 고장 난 것은 아니에요.",
                )
            else:
                self.help_frame.grid_remove()

        if self._demo_mode:
            self.status_var.set("데모 화면 · 실제 장비 연결이나 명령 전송은 하지 않았어요.")
        elif self._direct_input_mode:
            self.status_var.set("이름표 분류 완료 · 실제 장비 연결은 확인하지 않았어요.")
        else:
            self.status_var.set(
                f"검색 완료 · 연결 주소 {len(self._records)}개 · 분류 완료 {len(identified)}대"
            )

        if self._begin_classification_confirmation(stopped=stopped):
            self.status_var.set(
                "장비별 최종 기능을 만들기 위해 기준 명령팩의 명령을 실제로 검증해 주세요."
            )
            return
        if self._direct_input_mode:
            self._set_step(3)
        elif identified:
            self._set_step(4)
        else:
            self._set_step(2)

    def _finish_fatal(self, error: Exception) -> None:
        self.progress.stop()
        self.primary_search_button.configure(state="normal")
        self.demo_button.configure(state="normal")
        self.manual_identify_button.configure(state="normal")
        self._show_stage(self.result_card)
        self._set_step(2)
        self._clear_result_cards()
        self.result_card.rowconfigure(3, weight=0)
        self.result_list_shell.grid_remove()
        self.result_icon.configure(text="!", background=DANGER_LIGHT, foreground=DANGER)
        if isinstance(error, VisaDiscoveryError) and "PyVISA가 설치" in str(error):
            self.result_title_var.set("장비를 찾는 연결 도구가 아직 준비되지 않았어요")
            self.result_body_var.set(
                "현재 실행 환경에는 장비 검색 기능이 포함되지 않았어요. "
                "IDN 이름표 직접 분류는 계속 사용할 수 있어요."
            )
            self._show_help(
                "이렇게 해보세요",
                "1. 개발 PC에는 PyVISA를 설치해 주세요.\n"
                "2. 시험 PC에는 사용 중인 NI·Keysight·R&S VISA 드라이버가 필요할 수 있어요.\n"
                "3. 설치 후 프로그램을 다시 시작하고 ‘다시 찾아보기’를 눌러 주세요.",
            )
        else:
            self.result_title_var.set("장비 검색을 시작하지 못했어요")
            self.result_body_var.set(
                "연결 프로그램이나 VISA 드라이버 상태를 확인한 뒤 다시 시도해 주세요."
            )
            self._show_help(
                "기술 정보",
                str(error),
            )
        self.status_var.set(f"검색 실패 · {error}")

    def _show_standard_help(self) -> None:
        self._show_help(
            "이렇게 해보세요",
            "1. 장비 전원이 켜져 있는지 확인해 주세요.\n"
            "2. USB·LAN·GPIB 케이블을 다시 연결해 주세요.\n"
            "3. NI-VISA, Keysight IO Libraries 또는 R&S VISA가 설치되어 있는지 확인해 주세요.\n"
            "4. SEreport나 다른 제어 프로그램이 장비를 사용 중이라면 먼저 종료해 주세요.\n"
            "5. 그래도 보이지 않으면 ‘연결 설정 확인’에서 장비 주소를 직접 입력해 보세요.",
        )

    def _show_help(self, title: str, body: str) -> None:
        self.help_title_var.set(title)
        self.help_body_var.set(body)
        self.help_frame.grid()

    def _render_result_cards(self) -> None:
        self._clear_result_cards()
        grouped: dict[DeviceCategory, list[DiscoveryRecord]] = {}
        for record in self._records:
            category = self._record_category(record)
            grouped.setdefault(category, []).append(record)

        ordered_categories = [
            category for category in _CATEGORY_ORDER if category in grouped
        ]
        ordered_categories.extend(
            category
            for category in grouped
            if category not in ordered_categories
        )
        for row, category in enumerate(ordered_categories):
            self._render_category_group(
                row=row,
                category=category,
                records=grouped[category],
            )

        if not self._records:
            tk.Label(
                self.result_list,
                text="표시할 장비가 아직 없어요.",
                font=("Segoe UI", 10),
                background=CARD,
                foreground=SUBTEXT,
                pady=25,
            ).grid(row=0, column=0)
        self.result_list.columnconfigure(0, weight=1)
        self.result_list.update_idletasks()
        self.result_canvas.configure(scrollregion=self.result_canvas.bbox("all"))
        self.result_canvas.yview_moveto(0.0)
        self._bind_result_mousewheel()
        self._capture_scalable_widgets()
        self.apply_ui_scale(self._ui_scale)
        self._schedule_idle(self._sync_result_scrollbar)

    def _render_category_group(
        self,
        *,
        row: int,
        category: DeviceCategory,
        records: list[DiscoveryRecord],
    ) -> None:
        accent, light = category_colors(category)
        group = tk.Frame(
            self.result_list,
            background="#F9FAFB",
            highlightbackground=BORDER,
            highlightthickness=1,
            padx=17,
            pady=14,
        )
        group.grid(row=row, column=0, sticky="ew", pady=(0, 12))
        group.columnconfigure(0, weight=1)

        header = tk.Frame(group, background="#F9FAFB")
        header.grid(row=0, column=0, sticky="ew")
        header.columnconfigure(1, weight=1)
        artwork = CategoryArtwork(
            header,
            category,
            width=160,
            height=94,
            background="#F9FAFB",
        )
        artwork.grid(row=0, column=0, rowspan=2, sticky="nw", padx=(0, 20))

        title_row = tk.Frame(header, background="#F9FAFB")
        title_row.grid(row=0, column=1, sticky="ew", pady=(4, 0))
        tk.Label(
            title_row,
            text=category.label_ko,
            font=("Segoe UI Semibold", 15),
            background="#F9FAFB",
            foreground=TEXT,
        ).grid(row=0, column=0, sticky="w")
        tk.Label(
            title_row,
            text=f"{len(records)}대",
            font=("Segoe UI Semibold", 9),
            background=light,
            foreground=accent,
            padx=9,
            pady=3,
        ).grid(row=0, column=1, sticky="w", padx=(10, 0))
        tk.Label(
            header,
            text=self._category_group_description(category, records),
            font=("Segoe UI", 10),
            background="#F9FAFB",
            foreground=SUBTEXT,
            justify="left",
            anchor="w",
            wraplength=690,
        ).grid(row=1, column=1, sticky="new", pady=(7, 0))

        device_list = tk.Frame(group, background="#F9FAFB")
        device_list.grid(row=1, column=0, sticky="ew", pady=(12, 0))
        device_list.columnconfigure(1, weight=1)
        for index, record in enumerate(records):
            connector = TimelineConnector(
                device_list,
                color=accent,
                last=index == len(records) - 1,
                width=28,
                height=58,
                background="#F9FAFB",
            )
            connector.grid(row=index, column=0, sticky="ns", padx=(8, 0))

            device_card = tk.Frame(
                device_list,
                background=CARD,
                highlightbackground=BORDER,
                highlightthickness=1,
                padx=13,
                pady=8,
            )
            device_card.grid(
                row=index,
                column=1,
                sticky="ew",
                pady=(0, 7 if index < len(records) - 1 else 0),
            )
            device_card.columnconfigure(0, weight=1)

            identity = record.identity
            if identity is not None:
                title = f"{identity.manufacturer} {identity.model}".strip()
                if identity.serial.strip():
                    title += f" · S/N {identity.serial.strip()}"
                subtitle = self._friendly_classification(record)
            else:
                title = record.resource
                if record.state == DiscoveryState.SKIPPED:
                    subtitle = (
                        "자동으로 열지 않은 연결 주소예요. "
                        "필요하면 주소를 직접 확인해 주세요."
                    )
                else:
                    subtitle = "장비의 이름표를 읽지 못했어요."

            heading = tk.Frame(device_card, background=CARD)
            heading.grid(row=0, column=0, sticky="ew")
            heading.columnconfigure(0, weight=1)
            tk.Label(
                heading,
                text=title,
                font=("Segoe UI Semibold", 11),
                background=CARD,
                foreground=TEXT,
                anchor="w",
                justify="left",
                wraplength=580,
            ).grid(row=0, column=0, sticky="w")

            badge_text, badge_bg, badge_fg = self._device_badge_style(record)
            tk.Label(
                heading,
                text=badge_text,
                font=("Segoe UI Semibold", 8),
                background=badge_bg,
                foreground=badge_fg,
                padx=8,
                pady=2,
            ).grid(row=0, column=1, sticky="e", padx=(12, 0))

            detail_button = _button(
                heading,
                text="자세히",
                command=lambda selected=record: self._open_details(selected),
            )
            detail_button.configure(padx=10, pady=3, font=("Segoe UI", 9))
            detail_button.grid(row=0, column=2, sticky="e", padx=(8, 0))

            tk.Label(
                device_card,
                text=subtitle,
                font=("Segoe UI", 9),
                background=CARD,
                foreground=SUBTEXT,
                anchor="w",
                justify="left",
                wraplength=760,
            ).grid(row=1, column=0, sticky="ew", pady=(4, 0))
            tk.Label(
                device_card,
                text=f"연결 주소  {record.resource}",
                font=("Cascadia Mono", 8),
                background=CARD,
                foreground=SUBTEXT,
                anchor="w",
                justify="left",
                wraplength=760,
            ).grid(row=2, column=0, sticky="ew", pady=(4, 0))

            selectable = self._is_routine_selectable(record)
            if selectable:
                selector_text = "이 장비 사용"
            elif (
                record.state == DiscoveryState.IDENTIFIED
                and record.identity is not None
                and record.resource != "직접 입력한 이름표"
            ):
                selector_text = "분류 확인 필요"
            else:
                selector_text = "루틴 사용 불가"
            selection_var = self._selection_vars.setdefault(
                record.resource,
                tk.BooleanVar(master=self, value=False),
            )
            selector = tk.Checkbutton(
                device_card,
                text=selector_text,
                variable=selection_var,
                command=self._update_selection_controls,
                state="normal" if selectable else "disabled",
                font=("Segoe UI Semibold", 9),
                background=CARD,
                foreground=TEXT if selectable else SUBTEXT,
                activebackground=CARD,
                activeforeground=TEXT,
                disabledforeground=SUBTEXT,
                selectcolor=CARD,
                relief="flat",
                borderwidth=0,
                highlightthickness=0,
                cursor="hand2" if selectable else "arrow",
                takefocus=selectable,
            )
            selector.grid(
                row=0,
                column=1,
                rowspan=3,
                sticky="ne",
                padx=(14, 0),
                pady=(1, 0),
            )

        self._update_selection_controls()

    @staticmethod
    def _record_category(record: DiscoveryRecord) -> DeviceCategory:
        if record.classification is not None:
            return record.classification.category
        return DeviceCategory.UNKNOWN

    @staticmethod
    def _category_group_description(
        category: DeviceCategory,
        records: list[DiscoveryRecord],
    ) -> str:
        if category == DeviceCategory.UNKNOWN and any(
            record.identity is None for record in records
        ):
            return (
                "이름표를 읽지 못했거나 자동 확인 대상이 아닌 연결 주소예요. "
                "케이블과 연결 설정을 확인해 주세요."
            )
        return category_description(category)

    def _is_routine_selectable(self, record: DiscoveryRecord) -> bool:
        if (
            record.state != DiscoveryState.IDENTIFIED
            or record.identity is None
            or record.classification is None
            or record.classification.category == DeviceCategory.UNKNOWN
            or record.resource == "직접 입력한 이름표"
        ):
            return False
        result = record.classification
        if (
            result.confidence == ClassificationConfidence.EXACT_PROFILE
            and result.profile_id
            and record.resource.startswith("DEMO::")
        ):
            return True
        return bool(
            result.confidence == ClassificationConfidence.VALIDATED_PROFILE
            and result.profile_id
            and result.compatible_operation_ids
        )

    def selected_records(self) -> tuple[DiscoveryRecord, ...]:
        return tuple(
            record
            for record in self._records
            if self._is_routine_selectable(record)
            and self._selection_vars.get(record.resource) is not None
            and self._selection_vars[record.resource].get()
        )

    def _reset_routine_selection(self) -> None:
        self._selection_vars.clear()
        self._update_selection_controls()

    def _update_selection_controls(self) -> None:
        selected_count = len(self.selected_records())
        if selected_count:
            self.selection_count_var.set(f"{selected_count}대 선택됨")
            self.continue_to_routine_button.configure(
                text=f"선택한 장비 {selected_count}대로 루틴 만들기",
                state="normal",
            )
        else:
            self.selection_count_var.set("루틴에 사용할 장비를 선택해 주세요.")
            self.continue_to_routine_button.configure(
                text="선택한 장비로 루틴 만들기",
                state="disabled",
            )

    def _continue_to_routine(self) -> None:
        selected = self.selected_records()
        if not selected:
            self.status_var.set("루틴에 사용할 장비를 한 대 이상 선택해 주세요.")
            return
        if self._on_continue_to_routine is None:
            self.status_var.set("루틴 설정 화면을 준비하지 못했어요.")
            return
        self._on_continue_to_routine(selected)

    def _device_badge_style(self, record: DiscoveryRecord) -> tuple[str, str, str]:
        result = record.classification
        if record.state == DiscoveryState.ERROR:
            return "확인 실패", DANGER_LIGHT, DANGER
        if record.state == DiscoveryState.SKIPPED:
            return "수동 확인", NEUTRAL_LIGHT, SUBTEXT
        if result is None:
            return "확인 필요", WARNING_LIGHT, WARNING
        if result.confidence == ClassificationConfidence.EXACT_PROFILE:
            return "기준 명령팩 일치", "#F0F6FF", ACCENT
        if result.confidence == ClassificationConfidence.VALIDATED_PROFILE:
            return "실장비 기능 검증", SUCCESS_LIGHT, SUCCESS
        if (
            result.confidence
            == ClassificationConfidence.REPRESENTATIVE_CONFIRMED
        ):
            return "이전 확인 기록", WARNING_LIGHT, WARNING
        if result.confidence == ClassificationConfidence.FAMILY_HEURISTIC:
            return "모델명으로 추정", "#F0F6FF", ACCENT
        return "종류 확인 필요", WARNING_LIGHT, WARNING

    def _clear_result_cards(self) -> None:
        for child in self.result_list.winfo_children():
            child.destroy()

    def _friendly_classification(self, record: DiscoveryRecord) -> str:
        result = record.classification
        demo_prefix = "화면 확인용 데모예요. " if record.resource.startswith("DEMO::") else ""
        if result is None:
            return demo_prefix + "장비 종류를 아직 분류하지 못했어요."
        if result.confidence == ClassificationConfidence.EXACT_PROFILE:
            return (
                f"{demo_prefix}{result.category.label_ko}로 확인했어요. "
                "기준 명령팩과 이름표가 일치하지만, 실제 기능은 아직 검증 전이에요."
            )
        if result.confidence == ClassificationConfidence.VALIDATED_PROFILE:
            return (
                f"{demo_prefix}{result.category.label_ko}로 최종 분류했어요. "
                f"실장비에서 통과한 명령 "
                f"{len(result.compatible_operation_ids)}개만 사용할 수 있어요."
            )
        if (
            result.confidence
            == ClassificationConfidence.REPRESENTATIVE_CONFIRMED
        ):
            return (
                f"{demo_prefix}{result.category.label_ko}로 확인했어요. "
                "이전 방식의 사용자 확인 기록이라 operation별 재검증이 필요해요."
            )
        if result.confidence == ClassificationConfidence.FAMILY_HEURISTIC:
            return (
                f"{demo_prefix}모델명을 보고 {result.category.label_ko}로 추정했어요. "
                "실제 조작 전에는 정확한 프로파일 확인이 필요해요."
            )
        return (
            f"{demo_prefix}장비 이름표는 읽었지만 종류는 아직 확정하지 못했어요. "
            "전체 모델명을 확인해 주세요."
        )

    def _open_details(self, record: DiscoveryRecord) -> None:
        dialog = tk.Toplevel(self)
        dialog.title("장비 상세 정보")
        dialog.geometry("680x460")
        dialog.minsize(560, 380)
        dialog.configure(background=BACKGROUND)
        dialog.transient(self.winfo_toplevel())

        shell = tk.Frame(
            dialog,
            background=CARD,
            highlightbackground=BORDER,
            highlightthickness=1,
            padx=24,
            pady=22,
        )
        shell.pack(fill="both", expand=True, padx=20, pady=20)
        tk.Label(
            shell,
            text="장비 상세 정보",
            font=("Segoe UI Semibold", 15),
            background=CARD,
            foreground=TEXT,
        ).pack(anchor="w")
        tk.Label(
            shell,
            text="문제 해결이나 프로파일 확인에 필요한 기술 정보예요.",
            font=("Segoe UI", 9),
            background=CARD,
            foreground=SUBTEXT,
        ).pack(anchor="w", pady=(4, 14))

        text = tk.Text(
            shell,
            wrap="word",
            font=("Consolas", 10),
            background="#F8F9FA",
            foreground=TEXT,
            relief="flat",
            padx=12,
            pady=12,
        )
        text.pack(fill="both", expand=True)
        text.insert("1.0", self._record_details(record))
        text.configure(state="disabled")
        _button(shell, text="닫기", command=dialog.destroy, primary=True).pack(
            anchor="e", pady=(14, 0)
        )

    def _record_details(self, record: DiscoveryRecord) -> str:
        lines = [
            f"VISA Resource: {record.resource}",
            f"Interface: {record.interface}",
            f"확인 상태: {record.state.label_ko}",
        ]
        identity = record.identity
        if identity is not None:
            lines.extend(
                [
                    "",
                    f"Raw *IDN?: {identity.raw}",
                    f"Manufacturer: {identity.manufacturer}",
                    f"Model: {identity.model}",
                    f"Serial: {identity.serial or '(없음)'}",
                    f"Firmware: {identity.firmware or '(없음)'}",
                ]
            )
        result = record.classification
        if result is not None:
            lines.extend(
                [
                    "",
                    f"분류: {result.category.label_ko}",
                    f"판정 수준: {result.confidence.label_ko}",
                    f"판정 규칙: {result.matched_rule}",
                    f"Profile ID: {result.profile_id or '(아직 없음)'}",
                    f"Profile 상태: {result.profile_status or '(미검증)'}",
                ]
            )
            if result.compatible_capability_ids:
                lines.extend(
                    [
                        "",
                        "실장비 검증에서 하나 이상 통과한 기능:",
                        *(
                            f"- {capability_id}"
                            for capability_id
                            in result.compatible_capability_ids
                        ),
                    ]
                )
            if result.incompatible_capability_ids:
                lines.extend(
                    [
                        "",
                        "실장비 검증에서 실패한 기능:",
                        *(
                            f"- {capability_id}"
                            for capability_id
                            in result.incompatible_capability_ids
                        ),
                    ]
                )
            if result.compatible_operation_ids:
                lines.extend(
                    [
                        "",
                        "통과한 operation:",
                        *(
                            f"- {operation_id}"
                            for operation_id in result.compatible_operation_ids
                        ),
                    ]
                )
            if result.unresolved_operation_ids:
                lines.extend(
                    [
                        "",
                        f"아직 확인하지 못한 operation: "
                        f"{len(result.unresolved_operation_ids)}개",
                    ]
                )
        if record.message:
            lines.extend(["", f"메시지: {record.message}"])
        return "\n".join(lines)

    def _update_result_scrollregion(self, _event: tk.Event[Any]) -> None:
        self.result_canvas.configure(scrollregion=self.result_canvas.bbox("all"))
        self._schedule_idle(self._sync_result_scrollbar)

    def _resize_result_window(self, event: tk.Event[Any]) -> None:
        self.result_canvas.itemconfigure(self.result_window, width=event.width)
        self._schedule_idle(self._sync_result_scrollbar)

    def _sync_result_scrollbar(self) -> None:
        bbox = self.result_canvas.bbox("all")
        content_height = 0 if bbox is None else bbox[3] - bbox[1]
        if content_height > self.result_canvas.winfo_height() + 2:
            self.result_scroll.grid()
        else:
            self.result_scroll.grid_remove()

    def _bind_result_mousewheel(self) -> None:
        widgets = [
            self.result_canvas,
            self.result_list,
            *self._descendants(self.result_list),
        ]
        for widget in widgets:
            widget.bind("<MouseWheel>", self._on_result_mousewheel)
            widget.bind("<Button-4>", self._on_result_mousewheel)
            widget.bind("<Button-5>", self._on_result_mousewheel)

    def _on_result_mousewheel(self, event: tk.Event[Any]) -> str:
        if getattr(event, "num", None) == 4:
            units = -1
        elif getattr(event, "num", None) == 5:
            units = 1
        else:
            delta = int(getattr(event, "delta", 0))
            if delta == 0:
                return "break"
            units = -max(1, abs(delta) // 120) if delta > 0 else max(
                1,
                abs(delta) // 120,
            )
        before = self.result_canvas.yview()
        self.result_canvas.yview_scroll(units, "units")
        after = self.result_canvas.yview()
        # A withdrawn/minimized Tk window can temporarily report a one-pixel
        # canvas. Tk then rounds one scroll unit to zero; keep the wheel
        # deterministic so the first gesture is not silently lost.
        if after == before and before:
            step = 0.05 if units > 0 else -0.05
            self.result_canvas.yview_moveto(
                max(0.0, min(1.0, before[0] + step))
            )
        return "break"

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

            if isinstance(widget, tk.Canvas):
                for option in ("width", "height"):
                    key = (widget, option)
                    if key in self._widget_metrics:
                        continue
                    try:
                        value = float(widget.cget(option))
                    except (tk.TclError, ValueError):
                        continue
                    if value > 10:
                        self._widget_metrics[key] = value
            if isinstance(widget, ttk.Progressbar):
                key = (widget, "length")
                if key not in self._widget_metrics:
                    try:
                        self._widget_metrics[key] = float(widget.cget("length"))
                    except (tk.TclError, ValueError):
                        pass

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
                widget.configure(**{option: max(1, int(round(base_value * self._ui_scale)))})
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
                scaled = tuple(max(0, int(round(value * self._ui_scale))) for value in values)
                rendered: int | tuple[int, ...] = scaled[0] if len(scaled) == 1 else scaled
                if manager == "grid":
                    widget.grid_configure(**{option: rendered})
                else:
                    widget.pack_configure(**{option: rendered})
            except tk.TclError:
                stale_layouts.append((widget, manager, option))
        for key in stale_layouts:
            self._layout_metrics.pop(key, None)

    def shutdown(self) -> None:
        self._stop_event.set()
        if (
            self._validation_dialog is not None
            and self._validation_dialog.winfo_exists()
        ):
            try:
                self._validation_dialog.destroy()
            except tk.TclError:
                pass
            self._validation_dialog = None
        for after_id in tuple(self._idle_after_ids):
            try:
                self.after_cancel(after_id)
            except tk.TclError:
                pass
        self._idle_after_ids.clear()
        if self._poll_after_id is not None:
            try:
                self.after_cancel(self._poll_after_id)
            except tk.TclError:
                pass
            self._poll_after_id = None
