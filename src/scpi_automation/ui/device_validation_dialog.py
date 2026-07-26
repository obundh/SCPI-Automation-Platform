from __future__ import annotations

import queue
import threading
import tkinter as tk
from dataclasses import replace
from pathlib import Path
from tkinter import filedialog, messagebox, simpledialog, ttk
from typing import Callable, ContextManager

from scpi_automation.identity import (
    InstrumentProfile,
    parse_idn_response,
)
from scpi_automation.transport import DiscoveryRecord, open_resource_session
from scpi_automation.validation import (
    LocalExtensionDefinition,
    LocalExtensionRegistry,
    ManualCommandCandidate,
    ManualProbeEvidence,
    OperationKind,
    OperationStatus,
    OPTION_STATE_QUERIED,
    OPTION_STATE_UNQUERIED,
    OPTION_STATE_UNSUPPORTED,
    ValidationProgress,
    ValidationResult,
    apply_manual_result,
    attest_local_extension,
    bind_local_extension_options,
    build_safe_validation_policy,
    build_validation_result,
    create_validation_progress,
    ensure_progress_matches_profile,
    load_local_extension_registry,
    load_manual_command_catalogs,
    load_validation_progress,
    merge_profile_extensions,
    new_stop_flag,
    reset_operations,
    promote_local_extension,
    save_local_extension_registry,
    save_validation_progress,
    save_validation_result,
    validate_local_extension,
    validate_profile,
    verify_local_extension_identity,
)

from .local_extension_dialog import ask_local_extension_definition


BACKGROUND = "#F4F6F8"
CARD = "#FFFFFF"
TEXT = "#191F28"
SUBTEXT = "#6B7684"
BORDER = "#E5E8EB"
ACCENT = "#3182F6"
ACCENT_DARK = "#1B64DA"
SUCCESS = "#0F9D58"
WARNING = "#D97706"
DANGER = "#D92D20"


SessionFactory = Callable[..., ContextManager[object]]
CompletionCallback = Callable[[ValidationResult], None]


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
        font=("Segoe UI Semibold", 9),
        relief="flat",
        borderwidth=0,
        cursor="hand2",
        padx=16,
        pady=9,
        background=ACCENT if primary else "#F2F4F6",
        foreground="#FFFFFF" if primary else TEXT,
        activebackground=ACCENT_DARK if primary else "#E5E8EB",
        activeforeground="#FFFFFF" if primary else TEXT,
    )


class DeviceValidationDialog(tk.Toplevel):
    """Beginner-facing operation validation bound to one physical resource."""

    def __init__(
        self,
        master: tk.Misc,
        *,
        record: DiscoveryRecord,
        profile: InstrumentProfile,
        backend: str,
        timeout_ms: int,
        on_complete: CompletionCallback,
        session_factory: SessionFactory = open_resource_session,
        extension_registry_path: str | Path | None = None,
    ) -> None:
        super().__init__(master)
        if record.identity is None:
            raise ValueError("Hardware validation requires an identified device")
        self.record = record
        self._base_profile = profile
        self.backend = backend
        self.timeout_ms = timeout_ms
        self._on_complete = on_complete
        self._session_factory = session_factory
        self._events: queue.Queue[tuple[str, object]] = queue.Queue()
        self._worker: threading.Thread | None = None
        self._stop_flag = new_stop_flag()
        self._poll_id: str | None = None
        self._extension_registry_path = extension_registry_path
        self._extension_load_error = ""
        try:
            self._extension_registry = load_local_extension_registry(
                extension_registry_path,
            )
        except (OSError, ValueError) as exc:
            self._extension_registry = LocalExtensionRegistry()
            self._extension_load_error = str(exc)
        self._known_option_response = (
            record.classification.option_response
            if record.classification is not None
            else ""
        )
        self._known_option_state = (
            record.classification.option_state
            if record.classification is not None
            else (
                OPTION_STATE_UNQUERIED
                if self._profile_has_option_query(profile)
                else OPTION_STATE_UNSUPPORTED
            )
        )
        exact_extensions = self._extension_registry.for_identity(
            profile.profile_id,
            record.identity,
            self._known_option_response,
            self._known_option_state,
        )
        self.profile = merge_profile_extensions(
            profile,
            exact_extensions,
        )
        # Plain JSON validation files are useful audit/checkpoint documents,
        # but they are not an execution authority.  Only evidence produced in
        # this live dialog run, or an authenticated local-extension registry,
        # may be projected into the runnable device classification.
        self._has_current_live_evidence = bool(exact_extensions)
        self._progress = create_validation_progress(
            self.profile,
            record.resource,
            record.identity,
        )
        for promoted in exact_extensions:
            for operation in promoted.validation_result.operations:
                self._progress = self._progress.replace_operation(operation)
        self._manual_results: dict[str, ManualProbeEvidence] = {}
        self._manual_candidates: tuple[ManualCommandCandidate, ...] = ()
        self._manual_by_id: dict[str, ManualCommandCandidate] = {}
        self._manual_load_errors: tuple[str, ...] = ()

        self.title("장비 기능 검증")
        self.geometry("1180x760")
        self.minsize(900, 620)
        self.configure(background=BACKGROUND)
        self.transient(master.winfo_toplevel())
        self.protocol("WM_DELETE_WINDOW", self._request_close)

        self.summary_var = tk.StringVar()
        self.guide_var = tk.StringVar(
            value=(
                "1 이름표 재확인 → 2 조회 명령 확인 → 3 설정값 시험·복원 → "
                "4 실행·복원 불가 명령 수동 확인 → 5 통과 기능만 최종 분류"
            )
        )
        self.manual_summary_var = tk.StringVar()
        self.manual_search_var = tk.StringVar()

        self._build()
        self._load_manual_candidates()
        self._refresh_operation_tree()
        self._refresh_manual_tree()
        self._poll_id = self.after(100, self._poll_events)

    def _build(self) -> None:
        self.columnconfigure(0, weight=1)
        self.rowconfigure(1, weight=1)

        header = tk.Frame(self, background=BACKGROUND)
        header.grid(row=0, column=0, sticky="ew", padx=24, pady=(20, 12))
        header.columnconfigure(0, weight=1)
        tk.Label(
            header,
            text="이 장비에서 실제로 되는 기능만 확인할게요",
            font=("Segoe UI Semibold", 18),
            background=BACKGROUND,
            foreground=TEXT,
        ).grid(row=0, column=0, sticky="w")
        identity = self.record.identity
        tk.Label(
            header,
            text=(
                f"{identity.manufacturer} {identity.model}  ·  "
                f"{self.record.resource}  ·  펌웨어 {identity.firmware or '정보 없음'}"
            ),
            font=("Segoe UI", 9),
            background=BACKGROUND,
            foreground=SUBTEXT,
        ).grid(row=1, column=0, sticky="w", pady=(4, 0))
        tk.Label(
            header,
            textvariable=self.summary_var,
            font=("Segoe UI Semibold", 9),
            background="#E8F7EF",
            foreground=SUCCESS,
            padx=12,
            pady=6,
        ).grid(row=0, column=1, rowspan=2, sticky="e")

        shell = tk.Frame(
            self,
            background=CARD,
            highlightbackground=BORDER,
            highlightthickness=1,
        )
        shell.grid(row=1, column=0, sticky="nsew", padx=24)
        shell.columnconfigure(0, weight=1)
        shell.rowconfigure(0, weight=1)

        self.notebook = ttk.Notebook(shell)
        self.notebook.grid(row=0, column=0, sticky="nsew")
        self.operations_page = tk.Frame(self.notebook, background=CARD)
        self.manual_page = tk.Frame(self.notebook, background=CARD)
        self.notebook.add(
            self.operations_page,
            text="기능별 검증",
        )
        self.notebook.add(
            self.manual_page,
            text="내 로컬 명령 후보",
        )
        self._build_operations_page()
        self._build_manual_page()

        footer = tk.Frame(self, background=BACKGROUND)
        footer.grid(row=2, column=0, sticky="ew", padx=24, pady=(12, 20))
        footer.columnconfigure(2, weight=1)
        _button(
            footer,
            text="검증 기록 저장",
            command=self._save_progress,
        ).grid(row=0, column=0, sticky="w")
        _button(
            footer,
            text="기록 불러오기",
            command=self._load_progress,
        ).grid(row=0, column=1, sticky="w", padx=(8, 0))
        self.stop_button = _button(
            footer,
            text="검증 중지",
            command=self._stop_validation,
        )
        self.stop_button.grid(row=0, column=3, sticky="e", padx=(0, 8))
        self.stop_button.configure(state="disabled")
        self.finish_button = _button(
            footer,
            text="통과한 기능으로 최종 분류",
            command=self._finish,
            primary=True,
        )
        self.finish_button.grid(row=0, column=4, sticky="e")

    def _build_operations_page(self) -> None:
        page = self.operations_page
        page.columnconfigure(0, weight=1)
        page.rowconfigure(2, weight=1)

        guide = tk.Frame(page, background="#F0F6FF", padx=14, pady=10)
        guide.grid(row=0, column=0, sticky="ew", padx=16, pady=(14, 8))
        guide.columnconfigure(1, weight=1)
        tk.Label(
            guide,
            text="검증 순서",
            font=("Segoe UI Semibold", 9),
            background="#F0F6FF",
            foreground=ACCENT,
        ).grid(row=0, column=0, sticky="nw", padx=(0, 12))
        tk.Label(
            guide,
            textvariable=self.guide_var,
            font=("Segoe UI", 9),
            background="#F0F6FF",
            foreground=TEXT,
            justify="left",
            wraplength=890,
        ).grid(row=0, column=1, sticky="w")

        actions = tk.Frame(page, background=CARD)
        actions.grid(row=1, column=0, sticky="ew", padx=16, pady=(0, 8))
        self.query_button = _button(
            actions,
            text="1. 조회 명령 모두 확인",
            command=self._run_all_queries,
            primary=True,
        )
        self.query_button.grid(row=0, column=0)
        self.write_button = _button(
            actions,
            text="2. 선택한 설정 명령 시험·복원",
            command=self._run_selected_writes,
        )
        self.write_button.grid(row=0, column=1, padx=(8, 0))
        self.manual_button = _button(
            actions,
            text="3. 실행·복원 불가 기능 수동 기록",
            command=self._record_manual_result,
        )
        self.manual_button.grid(row=0, column=2, padx=(8, 0))
        tk.Label(
            actions,
            text="Ctrl/Shift로 여러 줄 선택",
            font=("Segoe UI", 8),
            background=CARD,
            foreground=SUBTEXT,
        ).grid(row=0, column=3, padx=(14, 0))

        tree_shell = tk.Frame(
            page,
            background=CARD,
            highlightbackground=BORDER,
            highlightthickness=1,
        )
        tree_shell.grid(row=2, column=0, sticky="nsew", padx=16, pady=(0, 14))
        tree_shell.columnconfigure(0, weight=1)
        tree_shell.rowconfigure(0, weight=1)
        columns = ("status", "kind", "risk", "feature", "command", "message")
        self.operation_tree = ttk.Treeview(
            tree_shell,
            columns=columns,
            show="headings",
            selectmode="extended",
        )
        headings = {
            "status": "검증 상태",
            "kind": "종류",
            "risk": "주의",
            "feature": "기능",
            "command": "SCPI 후보",
            "message": "결과·다음 행동",
        }
        widths = {
            "status": 90,
            "kind": 65,
            "risk": 60,
            "feature": 210,
            "command": 250,
            "message": 330,
        }
        for column in columns:
            self.operation_tree.heading(column, text=headings[column])
            self.operation_tree.column(
                column,
                width=widths[column],
                minwidth=55,
                stretch=column in {"feature", "command", "message"},
            )
        scrollbar = ttk.Scrollbar(
            tree_shell,
            orient="vertical",
            command=self.operation_tree.yview,
        )
        self.operation_tree.configure(yscrollcommand=scrollbar.set)
        self.operation_tree.grid(row=0, column=0, sticky="nsew")
        scrollbar.grid(row=0, column=1, sticky="ns")
        self.operation_tree.tag_configure("pass", foreground=SUCCESS)
        self.operation_tree.tag_configure("fail", foreground=DANGER)
        self.operation_tree.tag_configure("unsafe", foreground=WARNING)
        self.operation_tree.tag_configure("manual", foreground=WARNING)

    def _build_manual_page(self) -> None:
        page = self.manual_page
        page.columnconfigure(0, weight=1)
        page.rowconfigure(2, weight=1)
        self.manual_intro_label = tk.Label(
            page,
            text=(
                "제조사 매뉴얼과 그 명령 색인은 저작권 보호를 위해 프로그램에 "
                "넣지 않아요. 사용 권한이 있는 매뉴얼로 직접 만든 비공개 후보만 "
                "이 PC의 로컬 폴더에서 불러옵니다. 후보는 자동 실행되지 않으며, "
                "Query → 쓰기 → Readback → 원복 검증을 통과해야 로컬 기능으로 "
                "등록할 수 있어요."
            ),
            font=("Segoe UI", 9),
            background="#FFF4E5",
            foreground=TEXT,
            justify="left",
            wraplength=1030,
            padx=14,
            pady=10,
        )
        self.manual_intro_label.grid(
            row=0,
            column=0,
            sticky="ew",
            padx=16,
            pady=(14, 8),
        )
        page.bind(
            "<Configure>",
            lambda event: self.manual_intro_label.configure(
                wraplength=max(520, event.width - 64)
            ),
            add="+",
        )

        search = tk.Frame(page, background=CARD)
        search.grid(row=1, column=0, sticky="ew", padx=16, pady=(0, 8))
        search.columnconfigure(1, weight=1)
        tk.Label(
            search,
            textvariable=self.manual_summary_var,
            font=("Segoe UI Semibold", 9),
            background=CARD,
            foreground=TEXT,
        ).grid(row=0, column=0, sticky="w")
        entry = ttk.Entry(search, textvariable=self.manual_search_var)
        entry.grid(row=0, column=1, sticky="ew", padx=(18, 8))
        entry.bind("<KeyRelease>", lambda _event: self._refresh_manual_tree())
        self.manual_probe_button = _button(
            search,
            text="선택한 Query 후보 시험",
            command=self._run_manual_probe,
        )
        self.manual_probe_button.grid(row=0, column=2)
        self.manual_promote_button = _button(
            search,
            text="선택 후보를 기능으로 검증·등록",
            command=self._start_manual_extension_flow,
            primary=True,
        )
        self.manual_promote_button.grid(row=0, column=3, padx=(8, 0))

        tree_shell = tk.Frame(
            page,
            background=CARD,
            highlightbackground=BORDER,
            highlightthickness=1,
        )
        tree_shell.grid(row=2, column=0, sticky="nsew", padx=16, pady=(0, 14))
        tree_shell.columnconfigure(0, weight=1)
        tree_shell.rowconfigure(0, weight=1)
        columns = ("status", "group", "pattern", "probe", "policy", "page")
        self.manual_tree = ttk.Treeview(
            tree_shell,
            columns=columns,
            show="headings",
            selectmode="browse",
        )
        for column, heading, width in (
            ("status", "상태", 85),
            ("group", "명령군", 105),
            ("pattern", "매뉴얼 명령", 300),
            ("probe", "Query 후보", 300),
            ("policy", "검증 단계", 130),
            ("page", "매뉴얼", 80),
        ):
            self.manual_tree.heading(column, text=heading)
            self.manual_tree.column(
                column,
                width=width,
                stretch=column in {"pattern", "probe"},
            )
        scrollbar = ttk.Scrollbar(
            tree_shell,
            orient="vertical",
            command=self.manual_tree.yview,
        )
        horizontal_scrollbar = ttk.Scrollbar(
            tree_shell,
            orient="horizontal",
            command=self.manual_tree.xview,
        )
        self.manual_tree.configure(
            yscrollcommand=scrollbar.set,
            xscrollcommand=horizontal_scrollbar.set,
        )
        self.manual_tree.grid(row=0, column=0, sticky="nsew")
        scrollbar.grid(row=0, column=1, sticky="ns")
        horizontal_scrollbar.grid(row=1, column=0, sticky="ew")
        self.manual_tree.tag_configure("pass", foreground=SUCCESS)
        self.manual_tree.tag_configure("fail", foreground=DANGER)
        self.manual_tree.tag_configure("blocked", foreground=WARNING)
        self.manual_tree.tag_configure("response", foreground=WARNING)

    def _load_manual_candidates(self) -> None:
        try:
            index = load_manual_command_catalogs(strict=False)
            candidates = index.search(
                profile_id=self.profile.profile_id,
            )
        except (OSError, ValueError):
            candidates = ()
            self._manual_load_errors = (
                "매뉴얼 명령 후보 파일을 읽지 못했습니다.",
            )
        else:
            self._manual_load_errors = index.load_errors
        if not candidates:
            self._manual_candidates = ()
            self._manual_by_id = {}
            return
        self._manual_candidates = candidates
        self._manual_by_id = {
            self._manual_candidate_key(candidate): candidate
            for candidate in self._manual_candidates
        }

    @staticmethod
    def _manual_candidate_key(candidate: ManualCommandCandidate) -> str:
        return f"{candidate.manual_id}::{candidate.command_id}"

    @staticmethod
    def _is_option_query_command(command: str) -> bool:
        return "".join(command.split()).upper() == "*OPT?"

    @classmethod
    def _profile_has_option_query(
        cls,
        profile: InstrumentProfile,
    ) -> bool:
        return any(
            operation.name == OperationKind.QUERY.value
            and cls._is_option_query_command(operation.scpi)
            for capability in profile.capabilities
            for operation in capability.operations
        )

    def _option_operation(self):
        return next(
            (
                operation
                for operation in self._progress.operations
                if (
                    operation.kind is OperationKind.QUERY
                    and self._is_option_query_command(
                        operation.command_template
                    )
                )
            ),
            None,
        )

    def _current_option_binding(self) -> tuple[str, str]:
        operation = self._option_operation()
        if operation is None:
            return self._known_option_state, self._known_option_response
        if (
            operation.status is OperationStatus.PASS
            and operation.response.strip()
        ):
            return OPTION_STATE_QUERIED, operation.response.strip()
        return OPTION_STATE_UNQUERIED, ""

    def _current_option_response(self) -> str:
        return self._current_option_binding()[1]

    @staticmethod
    def _status_text(status: OperationStatus) -> str:
        return {
            OperationStatus.PENDING: "미검증",
            OperationStatus.PASS: "통과",
            OperationStatus.FAIL: "실패",
            OperationStatus.SKIPPED: "다음 단계",
            OperationStatus.UNSAFE: "승인 필요",
            OperationStatus.MANUAL: "수동 확인",
        }[status]

    @staticmethod
    def _kind_text(kind: OperationKind) -> str:
        return {
            OperationKind.QUERY: "조회",
            OperationKind.SET: "설정",
            OperationKind.EXECUTE: "실행",
        }[kind]

    def _capability_labels(self) -> dict[str, str]:
        return {
            capability.capability_id: (
                capability.label_ko.strip() or capability.capability_id
            )
            for capability in self.profile.capabilities
        }

    def _refresh_operation_tree(self) -> None:
        selected = set(self.operation_tree.selection())
        labels = self._capability_labels()
        current_ids = set(self.operation_tree.get_children())
        wanted_ids = {item.operation_id for item in self._progress.operations}
        for operation_id in current_ids - wanted_ids:
            self.operation_tree.delete(operation_id)
        phase_order = {
            OperationKind.QUERY: 0,
            OperationKind.SET: 1,
            OperationKind.EXECUTE: 2,
        }
        ordered_operations = sorted(
            enumerate(self._progress.operations),
            key=lambda indexed: (
                phase_order[indexed[1].kind],
                indexed[0],
            ),
        )
        for _index, item in ordered_operations:
            values = (
                self._status_text(item.status),
                self._kind_text(item.kind),
                item.risk_level or "-",
                labels.get(item.capability_id, item.capability_id),
                item.command_template,
                item.message or "아직 확인하지 않았어요.",
            )
            tag = item.status.value
            if self.operation_tree.exists(item.operation_id):
                self.operation_tree.item(
                    item.operation_id,
                    values=values,
                    tags=(tag,),
                )
            else:
                self.operation_tree.insert(
                    "",
                    "end",
                    iid=item.operation_id,
                    values=values,
                    tags=(tag,),
                )
        for operation_id in selected & wanted_ids:
            self.operation_tree.selection_add(operation_id)

        result = build_validation_result(self._progress)
        counts = dict(result.status_counts)
        self.summary_var.set(
            f"통과 {counts.get('pass', 0)}  ·  실패 {counts.get('fail', 0)}  ·  "
            f"확인 필요 {len(result.unresolved_operation_ids)}"
        )

    @staticmethod
    def _manual_policy_text(policy: str) -> str:
        return {
            "query_explicit": "매뉴얼 Query",
            "query_probe": "생성 Query 후보",
            "query_limited": "조건부 Query",
            "manual_only": "수동 검토 전용",
        }.get(policy, policy)

    def _promoted_records_for_candidate(
        self,
        candidate: ManualCommandCandidate,
    ) -> tuple[object, ...]:
        identity = self.record.identity
        if identity is None:
            return ()
        option_state, option_response = self._current_option_binding()
        return tuple(
            record
            for record in self._extension_registry.for_identity(
                self._base_profile.profile_id,
                identity,
                option_response,
                option_state,
            )
            if (
                record.definition.manual_id == candidate.manual_id
                and record.definition.source_command_id
                == candidate.command_id
            )
        )

    def _refresh_manual_tree(self) -> None:
        needle = self.manual_search_var.get().strip().casefold()
        for iid in self.manual_tree.get_children():
            self.manual_tree.delete(iid)
        shown = 0
        for candidate in self._manual_candidates:
            searchable = " ".join(
                (
                    candidate.command_pattern,
                    candidate.command_group,
                    candidate.query_probe,
                )
            ).casefold()
            if needle and needle not in searchable:
                continue
            candidate_key = self._manual_candidate_key(candidate)
            result = self._manual_results.get(candidate_key)
            promoted = self._promoted_records_for_candidate(candidate)
            if promoted:
                status = f"기능 등록 {len(promoted)}개"
                tag = "pass"
            elif candidate.probe_policy == "manual_only":
                status = "수동 검토"
                tag = "blocked"
            elif result is None:
                status = "미검증"
                tag = ""
            else:
                status = (
                    "응답 수신·미승격"
                    if result.status == "response"
                    else "실패"
                )
                tag = result.status
            self.manual_tree.insert(
                "",
                "end",
                iid=candidate_key,
                values=(
                    status,
                    candidate.command_group,
                    candidate.command_pattern,
                    candidate.query_probe or "-",
                    self._manual_policy_text(candidate.probe_policy),
                    f"p.{candidate.manual_page}",
                ),
                tags=(tag,),
            )
            shown += 1
        tested = len(self._manual_results)
        option_state, option_response = self._current_option_binding()
        registered = len(
            self._extension_registry.for_identity(
                self._base_profile.profile_id,
                self.record.identity,
                option_response,
                option_state,
            )
        )
        if not self._manual_candidates and not self._manual_load_errors:
            summary = "내장 후보 없음 · 사용자 로컬 카탈로그만 지원"
        else:
            summary = (
                f"전체 {len(self._manual_candidates)}개 · 현재 표시 {shown}개 · "
                f"Query 시험 {tested}개 · 등록 기능 {registered}개"
            )
        if self._manual_load_errors:
            summary += f" · 읽기 오류 {len(self._manual_load_errors)}개"
        if self._extension_load_error:
            summary += " · 로컬 기능 파일 오류"
        self.manual_summary_var.set(summary)

    def _set_running(self, running: bool, guide: str = "") -> None:
        state = "disabled" if running else "normal"
        for button in (
            self.query_button,
            self.write_button,
            self.manual_button,
            self.manual_probe_button,
            self.manual_promote_button,
            self.finish_button,
        ):
            button.configure(state=state)
        self.stop_button.configure(state="normal" if running else "disabled")
        if guide:
            self.guide_var.set(guide)

    def _verify_live_identity(self, session: object) -> None:
        raw = str(session.query("*IDN?"))
        live = parse_idn_response(raw)
        expected = self.record.identity
        if expected is None:
            raise RuntimeError("검증 대상 장비 이름표가 없습니다.")
        if (
            live.manufacturer.strip().casefold()
            != expected.manufacturer.strip().casefold()
            or live.model.strip().casefold() != expected.model.strip().casefold()
            or (
                expected.serial.strip()
                and live.serial.strip().casefold()
                != expected.serial.strip().casefold()
            )
            or (
                expected.firmware.strip()
                and live.firmware.strip().casefold()
                != expected.firmware.strip().casefold()
            )
        ):
            raise RuntimeError(
                "검색할 때 확인한 장비와 지금 열린 장비가 다릅니다. "
                "케이블과 VISA 주소를 다시 확인해 주세요."
            )

    def _verify_active_local_extensions(self, session: object) -> None:
        """Recheck option bindings before any stored local command is sent."""

        local_capability_ids = {
            capability.capability_id
            for capability in self.profile.capabilities
            if capability.capability_id.startswith("local.")
        }
        if not local_capability_ids:
            return
        checked_bindings: set[tuple[str, str]] = set()
        for record in self._extension_registry.for_profile(
            self._base_profile.profile_id
        ):
            definition = record.definition
            if definition.capability_id not in local_capability_ids:
                continue
            binding = (
                definition.identity_options_state,
                definition.identity_options.strip().casefold(),
            )
            if binding in checked_bindings:
                continue
            verify_local_extension_identity(definition, session)
            checked_bindings.add(binding)

    def _start_profile_validation(
        self,
        *,
        target_ids: set[str],
        approved_hazardous_ids: set[str] | None = None,
        guide: str,
    ) -> None:
        if self._worker is not None and self._worker.is_alive():
            return
        self._stop_flag = new_stop_flag()
        self._set_running(True, guide)

        base_build = build_safe_validation_policy(
            self.profile,
            timeout_ms=self.timeout_ms,
        )
        known = {
            item.operation_id for item in self._progress.operations
        }
        target_ids &= known
        current_progress = reset_operations(
            self._progress,
            target_ids,
        )
        skipped = frozenset(
            item.operation_id
            for item in current_progress.operations
            if item.status is OperationStatus.PENDING
            and item.operation_id not in target_ids
        )
        policy = type(base_build.policy)(
            timeout_ms=base_build.policy.timeout_ms,
            error_query=base_build.policy.error_query,
            max_error_entries=base_build.policy.max_error_entries,
            operation_arguments=base_build.policy.operation_arguments,
            approved_hazardous_operation_ids=frozenset(
                approved_hazardous_ids or ()
            ),
            skipped_operation_ids=skipped,
            numeric_relative_tolerance=(
                base_build.policy.numeric_relative_tolerance
            ),
            numeric_absolute_tolerance=(
                base_build.policy.numeric_absolute_tolerance
            ),
        )

        def worker() -> None:
            try:
                with self._session_factory(
                    self.record.resource,
                    backend=self.backend,
                    timeout_ms=self.timeout_ms,
                ) as session:
                    self._verify_live_identity(session)
                    self._verify_active_local_extensions(session)

                    def checkpoint(progress: ValidationProgress) -> None:
                        self._events.put(("progress", progress))

                    progress = validate_profile(
                        self.profile,
                        session,
                        resource=self.record.resource,
                        policy=policy,
                        progress=current_progress,
                        stop_flag=self._stop_flag,
                        on_progress=checkpoint,
                    )
                self._events.put(("validation_done", progress))
            except Exception as exc:
                self._events.put(("validation_error", exc))

        self._worker = threading.Thread(target=worker, daemon=True)
        self._worker.start()

    def _run_all_queries(self) -> None:
        query_ids = {
            item.operation_id
            for item in self._progress.operations
            if item.kind is OperationKind.QUERY
        }
        self._start_profile_validation(
            target_ids=query_ids,
            guide=(
                "장비 이름표를 다시 확인한 뒤 조회 명령을 차례로 시험하고 있어요. "
                "이 단계에서는 프로파일의 설정·실행 명령을 보내지 않아요."
            ),
        )

    def _run_selected_writes(self) -> None:
        selected_ids = set(self.operation_tree.selection())
        selected = [
            self._progress.operation(operation_id)
            for operation_id in selected_ids
            if self.operation_tree.exists(operation_id)
        ]
        writes = [item for item in selected if item.kind is OperationKind.SET]
        if not writes:
            messagebox.showinfo(
                "설정 명령 선택",
                "시험할 ‘설정’ 줄을 하나 이상 선택해 주세요.",
                parent=self,
            )
            return
        missing_query = []
        manual_only = []
        policy_build = build_safe_validation_policy(
            self.profile,
            timeout_ms=self.timeout_ms,
        )
        for item in writes:
            if policy_build.reason_for(item.operation_id):
                manual_only.append(item.operation_id)
                continue
            query_id = f"{item.capability_id}::query"
            try:
                query = self._progress.operation(query_id)
            except KeyError:
                manual_only.append(item.operation_id)
                continue
            if query.status is not OperationStatus.PASS:
                missing_query.append(item.operation_id)
        if manual_only:
            messagebox.showwarning(
                "자동 복원할 수 없는 설정이에요",
                (
                    "선택한 설정 중에는 원래값을 읽는 Query가 없어 자동 시험·복원을 "
                    "할 수 없는 항목이 있어요.\n\n"
                    "해당 줄을 한 개 선택한 뒤 ‘3. 실행·복원 불가 기능 수동 기록’에서 "
                    "실장비 확인 근거를 남겨 주세요.\n\n"
                    + "\n".join(
                        "• "
                        + operation_id
                        + (
                            f"\n  {policy_build.reason_for(operation_id)}"
                            if policy_build.reason_for(operation_id)
                            else ""
                        )
                        for operation_id in manual_only[:6]
                    )
                ),
                parent=self,
            )
            return
        if missing_query:
            messagebox.showwarning(
                "조회 검증이 먼저 필요해요",
                "원래값을 읽고 복원하려면 먼저 ‘조회 명령 모두 확인’을 실행해 주세요.",
                parent=self,
            )
            return

        medium_or_higher = [
            item
            for item in writes
            if item.risk_level in {"medium", "high", "hazardous", "critical"}
        ]
        approved_hazardous: set[str] = set()
        if medium_or_higher:
            preview = "\n".join(
                f"• {item.operation_id}  →  {item.command_template}"
                for item in medium_or_higher[:8]
            )
            if len(medium_or_higher) > 8:
                preview += f"\n• 그 외 {len(medium_or_higher) - 8}개"
            accepted = messagebox.askyesno(
                "설정값 변경과 원복을 진행할까요?",
                (
                    "선택한 명령은 장비 설정 또는 측정 메모리를 잠시 바꿉니다.\n"
                    "프로그램은 현재값 저장 → 시험값 쓰기 → Readback → 원복 → "
                    "원복 확인 순서로 진행합니다.\n\n"
                    f"{preview}\n\n"
                    "시험 대상과 출력 상태를 확인했다면 ‘예’를 누르세요."
                ),
                parent=self,
            )
            if not accepted:
                return
            approved_hazardous = {
                item.operation_id
                for item in writes
                if item.risk_level in {"high", "hazardous", "critical"}
            }
        self._start_profile_validation(
            target_ids={item.operation_id for item in writes},
            approved_hazardous_ids=approved_hazardous,
            guide=(
                "선택한 설정 기능을 하나씩 시험하고 원래값으로 되돌리고 있어요. "
                "복원 확인이 실패하면 즉시 그 기능을 실패로 기록합니다."
            ),
        )

    def _record_manual_result(self) -> None:
        selected = self.operation_tree.selection()
        if len(selected) != 1:
            messagebox.showinfo(
                "한 줄을 선택해 주세요",
                "수동으로 확인할 기능 한 줄만 선택해 주세요.",
                parent=self,
            )
            return
        operation_id = selected[0]
        record = self._progress.operation(operation_id)
        policy_build = build_safe_validation_policy(
            self.profile,
            timeout_ms=self.timeout_ms,
        )
        manual_reason = policy_build.reason_for(operation_id)
        if (
            (record.kind is OperationKind.EXECUTE or manual_reason)
            and record.status
            in {
                OperationStatus.PENDING,
                OperationStatus.SKIPPED,
                OperationStatus.FAIL,
            }
        ):
            record = replace(
                record,
                status=OperationStatus.MANUAL,
                validation_mode="manual_required",
                message=(
                    manual_reason
                    or (
                        "실행 명령은 일반적인 Readback·원복 방법이 없어 "
                        "사용자 관찰 근거가 필요합니다."
                    )
                ),
            )
            self._progress = self._progress.replace_operation(record)
        if record.status is not OperationStatus.MANUAL:
            messagebox.showinfo(
                "수동 확인 대상이 아니에요",
                (
                    "일반 조회·자동 복원 가능한 설정은 1·2단계에서 확인해 주세요. "
                    "여기서는 실행, binary, Query가 없어 자동 복원할 수 없는 기능만 "
                    "근거와 함께 기록할 수 있어요."
                ),
                parent=self,
            )
            return
        hazardous_manual = record.risk_level in {
            "high",
            "hazardous",
            "critical",
        }
        if hazardous_manual:
            approved = messagebox.askyesno(
                "고위험 기능을 수동 검증 기록할까요?",
                (
                    "이 기능은 출력, 전력, 파일·메모리 또는 장비 상태를 바꿀 수 "
                    "있어 프로그램이 자동 시험하지 않았습니다.\n\n"
                    f"기능: {record.operation_id}\n"
                    f"명령 후보: {record.command_template}\n\n"
                    "시험 대상 보호, 출력 연결, 허용 한계, 복구 절차를 직접 "
                    "확인하고 실장비에서 별도로 시험했다면 ‘예’를 누르세요."
                ),
                parent=self,
            )
            if not approved:
                return
        decision = messagebox.askyesnocancel(
            "수동 확인 결과",
            "실장비에서 이 기능이 정상 동작했나요?\n\n예 = 통과, 아니요 = 실패",
            parent=self,
        )
        if decision is None:
            return
        note = simpledialog.askstring(
            "확인 근거",
            "확인한 조건, 화면 결과, 옵션 또는 매뉴얼 페이지를 적어 주세요.",
            parent=self,
        )
        if not note or not note.strip():
            return
        try:
            self._progress = apply_manual_result(
                self._progress,
                operation_id,
                passed=decision,
                note=note,
                validation_mode=(
                    "manual_operator_hazardous"
                    if hazardous_manual
                    else "manual_operator"
                ),
            )
        except ValueError as exc:
            messagebox.showerror("기록하지 못했어요", str(exc), parent=self)
            return
        self._refresh_operation_tree()

    def _start_manual_extension_flow(self) -> None:
        selected = self.manual_tree.selection()
        if len(selected) != 1:
            messagebox.showinfo(
                "매뉴얼 후보 선택",
                "기능으로 검증할 매뉴얼 후보 한 줄을 선택해 주세요.",
                parent=self,
            )
            return
        candidate = self._manual_by_id.get(selected[0])
        identity = self.record.identity
        if candidate is None or identity is None:
            return
        option_operation = self._option_operation()
        if (
            option_operation is not None
            and option_operation.status is not OperationStatus.PASS
        ):
            messagebox.showwarning(
                "장비 옵션을 먼저 확인해 주세요",
                (
                    "같은 모델도 설치된 옵션에 따라 명령 지원 여부가 달라질 수 "
                    "있어요. 먼저 '1. 조회 명령 모두 확인'을 실행해 *OPT? "
                    "응답을 저장한 뒤 기능 등록을 진행해 주세요."
                ),
                parent=self,
            )
            return
        definition = ask_local_extension_definition(
            self,
            candidate=candidate,
            identity=identity,
            category=self.profile.category,
        )
        if definition is None:
            return
        option_state, option_response = self._current_option_binding()
        definition = replace(
            definition,
            identity_options=option_response,
            identity_options_state=option_state,
        )
        operation_kinds = {
            operation.name for operation in definition.operations
        }
        if operation_kinds == {OperationKind.EXECUTE.value}:
            self._attest_execute_extension(definition)
            return

        preview = "\n".join(
            f"• {operation.name.upper()}: {operation.scpi}"
            for operation in definition.operations
        )
        is_set = OperationKind.SET.value in operation_kinds
        hazardous = definition.risk_level in {
            "high",
            "hazardous",
            "critical",
        }
        if hazardous:
            accepted = messagebox.askyesno(
                "고위험 로컬 기능을 정확히 승인할까요?",
                (
                    "이 승인은 아래 기능 하나에만 적용됩니다.\n\n"
                    f"기능: {definition.label_ko}\n"
                    f"명령:\n{preview}\n\n"
                    "시험 대상 보호, 케이블 연결, 장비 허용 범위와 비상정지 "
                    "수단을 직접 확인했다면 '예'를 눌러 주세요."
                ),
                parent=self,
            )
            if not accepted:
                return
        elif is_set:
            accepted = messagebox.askyesno(
                "설정값 시험과 원복을 진행할까요?",
                (
                    "프로그램은 먼저 현재값을 읽고, 입력한 시험값을 한 번 쓴 뒤, "
                    "Readback으로 확인하고 원래 값으로 되돌립니다.\n\n"
                    f"{preview}\n\n"
                    "실패하거나 Timeout이 나도 원복을 시도하지만, 장비 상태를 "
                    "직접 확인할 준비가 됐을 때만 진행해 주세요."
                ),
                parent=self,
            )
            if not accepted:
                return
        else:
            accepted = messagebox.askyesno(
                "구조화한 Query를 검증할까요?",
                (
                    "장비 이름표를 다시 확인하고, 오류 큐를 비운 뒤 아래 Query의 "
                    "응답 형식까지 검사합니다.\n\n"
                    f"{preview}"
                ),
                parent=self,
            )
            if not accepted:
                return
        if self._worker is not None and self._worker.is_alive():
            return
        self._stop_flag = new_stop_flag()
        self._set_running(
            True,
            (
                "매뉴얼 후보를 구조화한 뒤 실장비에서 조회·Readback·원복 "
                "증거를 확인하고 있어요."
            ),
        )

        def worker() -> None:
            try:
                with self._session_factory(
                    self.record.resource,
                    backend=self.backend,
                    timeout_ms=self.timeout_ms,
                ) as session:
                    self._verify_live_identity(session)
                    bound_definition = bind_local_extension_options(
                        definition,
                        session,
                    )
                    error_query = build_safe_validation_policy(
                        self._base_profile,
                        timeout_ms=self.timeout_ms,
                    ).policy.error_query
                    result = validate_local_extension(
                        bound_definition,
                        self._base_profile,
                        session,
                        timeout_ms=self.timeout_ms,
                        error_query=error_query,
                        approved_hazardous=hazardous,
                    )
                self._events.put(
                    ("extension_done", (bound_definition, result))
                )
            except Exception as exc:
                self._events.put(
                    ("extension_error", (definition, exc))
                )

        self._worker = threading.Thread(target=worker, daemon=True)
        self._worker.start()

    def _attest_execute_extension(
        self,
        definition: LocalExtensionDefinition,
    ) -> None:
        operation = definition.operations[0]
        hazardous = definition.risk_level in {
            "high",
            "hazardous",
            "critical",
        }
        if hazardous:
            accepted = messagebox.askyesno(
                "고위험 실행 기능을 정확히 승인할까요?",
                (
                    "프로그램은 이 명령을 자동 전송하지 않습니다.\n\n"
                    f"기능: {definition.label_ko}\n"
                    f"명령: {operation.scpi}\n\n"
                    "격리된 시험 조건과 복구 절차를 갖추고 이 기능 하나를 "
                    "직접 시험했다면 '예'를 눌러 주세요."
                ),
                parent=self,
            )
            if not accepted:
                return
        decision = messagebox.askyesnocancel(
            "직접 시험한 결과",
            (
                "위 명령을 별도의 안전한 시험 절차에서 직접 확인했나요?\n\n"
                "예 = 정상 동작, 아니요 = 지원하지 않음, 취소 = 기록하지 않음"
            ),
            parent=self,
        )
        if decision is None:
            return
        note = simpledialog.askstring(
            "시험 증거",
            (
                "시험 조건, 장비 화면에서 확인한 변화, 복구 결과와 매뉴얼 "
                "근거를 적어 주세요."
            ),
            parent=self,
        )
        if not note or not note.strip():
            return
        if not decision:
            self._record_extension_failure(
                definition,
                "사용자가 안전한 별도 시험에서 지원하지 않는 기능으로 확인했습니다.",
            )
            messagebox.showwarning(
                "지원하지 않는 기능으로 확인했어요",
                "실패 증거는 화면에만 표시했고 실행 기능으로 등록하지 않았어요.",
                parent=self,
            )
            return
        if self._worker is not None and self._worker.is_alive():
            return
        self._set_running(
            True,
            (
                "수동 시험 증거를 저장하기 전에 현재 연결 장비의 IDN과 옵션을 "
                "마지막으로 다시 확인하고 있어요."
            ),
        )

        def worker() -> None:
            try:
                with self._session_factory(
                    self.record.resource,
                    backend=self.backend,
                    timeout_ms=self.timeout_ms,
                ) as session:
                    session.timeout = self.timeout_ms
                    bound_definition = bind_local_extension_options(
                        definition,
                        session,
                    )
                    result = attest_local_extension(
                        bound_definition,
                        self._base_profile,
                        session,
                        passed=True,
                        note=note,
                        hazardous_approved=hazardous,
                    )
                self._events.put(
                    ("extension_done", (bound_definition, result))
                )
            except Exception as exc:
                self._events.put(
                    ("extension_error", (definition, exc))
                )

        self._worker = threading.Thread(target=worker, daemon=True)
        self._worker.start()

    def _record_extension_failure(
        self,
        definition: LocalExtensionDefinition,
        message: str,
    ) -> None:
        candidate_key = (
            f"{definition.manual_id}::{definition.source_command_id}"
        )
        previous = self._manual_results.get(candidate_key)
        attempts = 1 if previous is None else previous.attempts + 1
        self._manual_results[candidate_key] = ManualProbeEvidence(
            candidate_key=candidate_key,
            manual_id=definition.manual_id,
            command_id=definition.source_command_id,
            command_pattern=definition.source_command_pattern,
            query_command=" | ".join(
                operation.scpi
                for operation in definition.operations
            ),
            manual_page=definition.manual_page,
            status="fail",
            message=message,
            attempts=attempts,
        )
        self._sync_manual_probe_evidence()
        self._refresh_manual_tree()

    def _promote_extension(
        self,
        definition: LocalExtensionDefinition,
        result: ValidationResult,
    ) -> None:
        try:
            registry = promote_local_extension(
                definition,
                result,
                self._extension_registry,
            )
            save_local_extension_registry(
                registry,
                self._extension_registry_path,
            )
            registry = load_local_extension_registry(
                self._extension_registry_path,
                missing_ok=False,
            )
        except (OSError, ValueError) as exc:
            messagebox.showerror(
                "로컬 기능을 저장할 수 없어요",
                str(exc),
                parent=self,
            )
            return
        self._extension_registry = registry
        self._rebuild_progress_with_local_extensions()
        self._refresh_operation_tree()
        self._refresh_manual_tree()
        self.guide_var.set(
            (
                f"'{definition.label_ko}' 기능이 이 장비의 제조사·모델·시리얼·"
                "펌웨어와 검증 증거에 묶여 등록됐어요."
            )
        )
        messagebox.showinfo(
            "검증된 로컬 기능을 등록했어요",
            (
                f"{definition.label_ko}\n\n"
                "최종 분류에 포함됐고, 루틴 설정에서는 통과한 operation만 "
                "선택할 수 있어요."
            ),
            parent=self,
        )

    def _rebuild_progress_with_local_extensions(self) -> None:
        identity = self.record.identity
        if identity is None:
            return
        option_state, option_response = self._current_option_binding()
        records = self._extension_registry.for_identity(
            self._base_profile.profile_id,
            identity,
            option_response,
            option_state,
        )
        merged_profile = merge_profile_extensions(
            self._base_profile,
            records,
        )
        rebuilt = create_validation_progress(
            merged_profile,
            self.record.resource,
            identity,
        )
        evidence = {
            item.operation_id: item
            for item in self._progress.operations
        }
        for record in records:
            evidence.update(
                {
                    item.operation_id: item
                    for item in record.validation_result.operations
                }
            )
        for item in rebuilt.operations:
            previous = evidence.get(item.operation_id)
            if (
                previous is not None
                and previous.command_template == item.command_template
                and previous.kind is item.kind
                and previous.response_type == item.response_type
            ):
                rebuilt = rebuilt.replace_operation(previous)
        self.profile = merged_profile
        self._progress = replace(
            rebuilt,
            manual_probes=self._progress.manual_probes,
            run_count=self._progress.run_count,
        )

    def _revoke_failed_local_extensions(self) -> tuple[str, ...]:
        failed_operation_ids = {
            item.operation_id
            for item in self._progress.operations
            if (
                item.capability_id.startswith("local.")
                and item.status is OperationStatus.FAIL
            )
        }
        if not failed_operation_ids:
            return ()
        revoked = tuple(
            record
            for record in self._extension_registry.records
            if failed_operation_ids
            & set(record.compatible_operation_ids)
        )
        if not revoked:
            return ()
        registry = self._extension_registry
        for record in revoked:
            registry = registry.remove(record.definition.extension_id)
        try:
            save_local_extension_registry(
                registry,
                self._extension_registry_path,
            )
            registry = load_local_extension_registry(
                self._extension_registry_path,
                missing_ok=False,
            )
        except (OSError, ValueError) as exc:
            messagebox.showerror(
                "실패한 로컬 기능을 해제하지 못했어요",
                (
                    f"{exc}\n\n현재 최종 분류에서는 제외되지만, 저장 파일을 "
                    "수정할 수 없어 다음 실행 전에 직접 확인이 필요해요."
                ),
                parent=self,
            )
            return ()
        self._extension_registry = registry
        return tuple(
            record.definition.label_ko for record in revoked
        )

    def _run_manual_probe(self) -> None:
        selected = self.manual_tree.selection()
        if len(selected) != 1:
            messagebox.showinfo(
                "Query 후보 선택",
                "시험할 매뉴얼 Query 후보 한 줄을 선택해 주세요.",
                parent=self,
            )
            return
        candidate = self._manual_by_id.get(selected[0])
        if candidate is None:
            return
        if candidate.probe_policy == "manual_only" or not candidate.query_probe:
            messagebox.showwarning(
                "자동 시험할 수 없는 명령이에요",
                "이 명령은 Query가 확정되지 않았거나 동작 명령일 수 있어 매뉴얼 검토 전에는 보내지 않아요.",
                parent=self,
            )
            return
        accepted = messagebox.askyesno(
            "이 Query 후보를 한 번 시험할까요?",
            (
                "매뉴얼 색인에서 만든 후보이며 아직 기능으로 등록되지 않았습니다.\n\n"
                f"명령: {candidate.query_probe}\n"
                f"매뉴얼: p.{candidate.manual_page}\n"
                f"단계: {self._manual_policy_text(candidate.probe_policy)}\n\n"
                "명령과 장비 상태를 직접 확인했다면 ‘예’를 누르세요."
            ),
            parent=self,
        )
        if not accepted:
            return
        if self._worker is not None and self._worker.is_alive():
            return
        candidate_key = selected[0]
        previous = self._manual_results.get(candidate_key)
        attempts = 1 if previous is None else previous.attempts + 1
        self._stop_flag = new_stop_flag()
        self._set_running(
            True,
            "선택한 매뉴얼 Query 후보 한 개를 시험하고 있어요.",
        )

        def worker() -> None:
            try:
                with self._session_factory(
                    self.record.resource,
                    backend=self.backend,
                    timeout_ms=self.timeout_ms,
                ) as session:
                    self._verify_live_identity(session)
                    session.timeout = self.timeout_ms
                    response = str(session.query(candidate.query_probe)).strip()
                    if not response:
                        raise RuntimeError("장비가 빈 응답을 보냈습니다.")
                result = ManualProbeEvidence(
                    candidate_key=candidate_key,
                    manual_id=candidate.manual_id,
                    command_id=candidate.command_id,
                    command_pattern=candidate.command_pattern,
                    query_command=candidate.query_probe,
                    manual_page=candidate.manual_page,
                    status="response",
                    response=response,
                    message=(
                        "Query 응답을 받았습니다. 응답형·파라미터·복구 규칙이 "
                        "구조화되기 전에는 기능으로 승격하지 않습니다."
                    ),
                    attempts=attempts,
                )
                self._events.put(
                    ("manual_done", (candidate_key, result))
                )
            except Exception as exc:
                result = ManualProbeEvidence(
                    candidate_key=candidate_key,
                    manual_id=candidate.manual_id,
                    command_id=candidate.command_id,
                    command_pattern=candidate.command_pattern,
                    query_command=candidate.query_probe,
                    manual_page=candidate.manual_page,
                    status="fail",
                    message=str(exc),
                    attempts=attempts,
                )
                self._events.put(
                    ("manual_done", (candidate_key, result))
                )

        self._worker = threading.Thread(target=worker, daemon=True)
        self._worker.start()

    def _stop_validation(self) -> None:
        self._stop_flag.set()
        self.guide_var.set(
            "중지를 요청했어요. 현재 VISA Timeout이 끝나는 즉시 남은 기능은 미검증으로 보존합니다."
        )

    def _poll_events(self) -> None:
        try:
            while True:
                event, payload = self._events.get_nowait()
                if event == "progress":
                    self._progress = payload
                    self._refresh_operation_tree()
                elif event == "validation_done":
                    self._progress = payload
                    self._has_current_live_evidence = True
                    revoked_local_features = (
                        self._revoke_failed_local_extensions()
                    )
                    current_option_state, current_options = (
                        self._current_option_binding()
                    )
                    if (
                        current_option_state
                        != self._known_option_state
                        or current_options
                        != self._known_option_response
                    ):
                        self._known_option_state = current_option_state
                        self._known_option_response = current_options
                        self._rebuild_progress_with_local_extensions()
                    self._set_running(
                        False,
                        "이번 단계가 끝났어요. 실패·수동 확인 항목의 설명을 확인한 뒤 다음 단계를 진행하세요.",
                    )
                    self._refresh_operation_tree()
                    self._refresh_manual_tree()
                    if revoked_local_features:
                        messagebox.showwarning(
                            "재검증에 실패한 로컬 기능을 해제했어요",
                            (
                                "\n".join(
                                    f"• {name}"
                                    for name in revoked_local_features
                                )
                                + "\n\n이 기능들은 현재 장비의 최종 분류와 "
                                "루틴 목록에서 제외됩니다."
                            ),
                            parent=self,
                        )
                elif event == "validation_error":
                    self._set_running(
                        False,
                        "장비 연결 또는 검증 중 오류가 발생했어요. VISA 주소와 장비 상태를 확인해 주세요.",
                    )
                    messagebox.showerror(
                        "검증을 계속하지 못했어요",
                        str(payload),
                        parent=self,
                    )
                elif event == "extension_done":
                    definition, result = payload
                    self._has_current_live_evidence = True
                    self._known_option_state = (
                        definition.identity_options_state
                    )
                    self._known_option_response = (
                        definition.identity_options
                    )
                    self._set_running(False)
                    expected = set(definition.operation_ids)
                    passed = set(result.compatible_operation_ids)
                    if (
                        passed == expected
                        and not result.incompatible_operation_ids
                        and not result.unresolved_operation_ids
                    ):
                        self._promote_extension(definition, result)
                    else:
                        details = "\n".join(
                            f"• {item.operation_id}: "
                            f"{self._status_text(item.status)} · {item.message}"
                            for item in result.operations
                        )
                        self._record_extension_failure(
                            definition,
                            details,
                        )
                        self.guide_var.set(
                            "구조화한 후보가 검증을 통과하지 못해 기능으로 등록하지 않았어요."
                        )
                        messagebox.showwarning(
                            "로컬 기능 검증을 통과하지 못했어요",
                            (
                                f"통과 {len(result.compatible_operation_ids)}개 · "
                                f"실패 {len(result.incompatible_operation_ids)}개 · "
                                f"확인 필요 {len(result.unresolved_operation_ids)}개\n\n"
                                f"{details}"
                            ),
                            parent=self,
                        )
                elif event == "extension_error":
                    definition, extension_error = payload
                    self._record_extension_failure(
                        definition,
                        str(extension_error),
                    )
                    self._set_running(
                        False,
                        "로컬 기능 검증 중 오류가 발생했어요. 장비 연결과 입력한 명령을 확인해 주세요.",
                    )
                    messagebox.showerror(
                        "로컬 기능 검증을 계속할 수 없어요",
                        str(extension_error),
                        parent=self,
                    )
                elif event == "manual_done":
                    command_id, result = payload
                    self._manual_results[command_id] = result
                    self._set_running(
                        False,
                        "매뉴얼 Query 후보 시험이 끝났어요. 이 결과만으로 쓰기 기능이 자동 등록되지는 않아요.",
                    )
                    self._refresh_manual_tree()
        except queue.Empty:
            pass
        try:
            self._poll_id = self.after(100, self._poll_events)
        except tk.TclError:
            self._poll_id = None

    def _save_progress(self) -> None:
        destination = filedialog.asksaveasfilename(
            parent=self,
            title="장비 기능 검증 기록 저장",
            defaultextension=".json",
            filetypes=(("검증 기록", "*.json"), ("모든 파일", "*.*")),
            initialfile=(
                f"{self.profile.profile_id}_{self.record.identity.model}_validation.json"
            ),
        )
        if not destination:
            return
        try:
            self._sync_manual_probe_evidence()
            save_validation_progress(destination, self._progress)
        except (OSError, ValueError) as exc:
            messagebox.showerror("저장하지 못했어요", str(exc), parent=self)
            return
        self.guide_var.set(f"검증 기록을 저장했어요: {Path(destination).name}")

    def _load_progress(self) -> None:
        source = filedialog.askopenfilename(
            parent=self,
            title="장비 기능 검증 기록 불러오기",
            filetypes=(("검증 기록", "*.json"), ("모든 파일", "*.*")),
        )
        if not source:
            return
        try:
            progress = load_validation_progress(source)
            if progress.resource and progress.resource != self.record.resource:
                raise ValueError(
                    "이 기록은 현재 선택한 VISA 주소의 장비 기록이 아닙니다."
                )
            expected = self.record.identity
            saved_identity = (
                progress.identity_manufacturer,
                progress.identity_model,
                progress.identity_serial,
                progress.identity_firmware,
            )
            current_identity = (
                expected.manufacturer,
                expected.model,
                expected.serial,
                expected.firmware,
            )
            if not progress.identity_model:
                raise ValueError(
                    "장비 이름표가 저장되지 않은 예전 검증 기록입니다. "
                    "현재 장비에서 새 검증을 시작해 주세요."
                )
            if tuple(
                item.strip().casefold() for item in saved_identity
            ) != tuple(
                item.strip().casefold() for item in current_identity
            ):
                raise ValueError(
                    "이 기록의 제조사·모델·시리얼·펌웨어가 현재 장비와 다릅니다."
                )
            saved_option_operation = next(
                (
                    item
                    for item in progress.operations
                    if (
                        item.kind is OperationKind.QUERY
                        and self._is_option_query_command(
                            item.command_template
                        )
                    )
                ),
                None,
            )
            if (
                saved_option_operation is not None
                and saved_option_operation.status
                is OperationStatus.PASS
                and saved_option_operation.response.strip()
            ):
                saved_option_state = OPTION_STATE_QUERIED
                saved_options = (
                    saved_option_operation.response.strip()
                )
            elif saved_option_operation is None:
                saved_option_state = OPTION_STATE_UNSUPPORTED
                saved_options = ""
            else:
                saved_option_state = OPTION_STATE_UNQUERIED
                saved_options = ""
            exact_extensions = self._extension_registry.for_identity(
                self._base_profile.profile_id,
                expected,
                saved_options,
                saved_option_state,
            )
            merged_profile = merge_profile_extensions(
                self._base_profile,
                exact_extensions,
            )
            profile_candidates = (
                (self._base_profile, merged_profile)
                if merged_profile is not self._base_profile
                else (self._base_profile,)
            )
            loaded_profile = None
            profile_errors: list[str] = []
            for candidate_profile in profile_candidates:
                try:
                    ensure_progress_matches_profile(
                        progress,
                        candidate_profile,
                    )
                except ValueError as exc:
                    profile_errors.append(str(exc))
                else:
                    loaded_profile = candidate_profile
                    break
            if loaded_profile is None:
                raise ValueError(
                    "검증 기록의 후보 명령팩·로컬 기능 정의가 현재 파일과 "
                    "일치하지 않습니다. "
                    + " / ".join(profile_errors)
                )
        except (OSError, ValueError, KeyError) as exc:
            messagebox.showerror("불러오지 못했어요", str(exc), parent=self)
            return
        self.profile = loaded_profile
        self._has_current_live_evidence = False
        self._known_option_response = saved_options
        self._known_option_state = saved_option_state
        self._progress = progress
        self._manual_results = {
            evidence.candidate_key: evidence
            for evidence in progress.manual_probes
            if evidence.candidate_key in self._manual_by_id
        }
        self._refresh_operation_tree()
        self._refresh_manual_tree()
        self.guide_var.set(
            "검증 기록을 감사 자료로 불러왔어요. JSON의 PASS 표시는 실행 권한이 "
            "아니므로 필요한 항목을 현재 장비에서 다시 확인해 주세요."
        )

    def _finish(self) -> None:
        if not self._has_current_live_evidence:
            messagebox.showwarning(
                "현재 장비에서 다시 확인해 주세요",
                (
                    "불러온 JSON 검증 기록은 내용을 손으로 바꿀 수 있는 감사·"
                    "참고 파일이라 실제 실행 권한으로 사용하지 않아요.\n\n"
                    "조회 명령 또는 필요한 설정 명령을 현재 연결된 장비에서 "
                    "다시 시험한 뒤 완료해 주세요."
                ),
                parent=self,
            )
            return
        self._sync_manual_probe_evidence()
        result = build_validation_result(self._progress)
        if not result.compatible_operation_ids:
            messagebox.showwarning(
                "통과한 기능이 아직 없어요",
                "먼저 조회 명령을 확인하고, 필요한 설정 기능을 시험해 주세요.",
                parent=self,
            )
            return
        if result.unresolved_operation_ids:
            accepted = messagebox.askyesno(
                "부분 검증 결과로 분류할까요?",
                (
                    f"통과 {len(result.compatible_operation_ids)}개, "
                    f"실패 {len(result.incompatible_operation_ids)}개, "
                    f"미확정 {len(result.unresolved_operation_ids)}개입니다.\n\n"
                    "지금 완료하면 통과한 operation만 루틴에서 사용할 수 있어요."
                ),
                parent=self,
            )
            if not accepted:
                return
        destination = filedialog.asksaveasfilename(
            parent=self,
            title="최종 장비 검증 결과 저장",
            defaultextension=".json",
            filetypes=(("최종 검증 결과", "*.json"), ("모든 파일", "*.*")),
            initialfile=(
                f"{self.profile.profile_id}_{self.record.identity.model}_result.json"
            ),
        )
        if destination:
            try:
                save_validation_result(destination, result)
            except (OSError, ValueError) as exc:
                messagebox.showerror(
                    "최종 결과를 저장하지 못했어요",
                    str(exc),
                    parent=self,
                )
                return
        self._on_complete(result)
        self._destroy_dialog()

    def _sync_manual_probe_evidence(self) -> None:
        self._progress = replace(
            self._progress,
            manual_probes=tuple(
                self._manual_results[key]
                for key in sorted(self._manual_results)
            ),
        )

    def _request_close(self) -> None:
        if self._worker is not None and self._worker.is_alive():
            accepted = messagebox.askyesno(
                "검증을 중지하고 닫을까요?",
                "현재 명령의 Timeout이 끝난 뒤 작업이 멈춥니다. 저장하지 않은 결과는 사라질 수 있어요.",
                parent=self,
            )
            if not accepted:
                return
            self._stop_flag.set()
        self._destroy_dialog()

    def _destroy_dialog(self) -> None:
        if self._poll_id is not None:
            try:
                self.after_cancel(self._poll_id)
            except tk.TclError:
                pass
            self._poll_id = None
        try:
            self.destroy()
        except tk.TclError:
            pass
