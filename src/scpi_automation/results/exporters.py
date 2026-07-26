from __future__ import annotations

import json
import math
import os
import re
import tempfile
from pathlib import Path
from typing import Iterable, Sequence

from scpi_automation.execution import ExecutionResult

from .serialization import execution_result_to_dict


_ILLEGAL_XML = re.compile(
    "[\x00-\x08\x0B\x0C\x0E-\x1F\uD800-\uDFFF\uFFFE\uFFFF]"
)
_XLSX_CELL_LIMIT = 32_767
_XLSX_CHUNK_SIZE = 32_000


def _atomic_text(path: Path, text: str) -> Path:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{path.name}.",
        suffix=".tmp",
        dir=path.parent,
    )
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as stream:
            descriptor = -1
            stream.write(text)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    except BaseException:
        if descriptor >= 0:
            os.close(descriptor)
        try:
            temporary.unlink()
        except FileNotFoundError:
            pass
        raise
    return path


def save_result_json(
    result: ExecutionResult,
    path: str | Path,
) -> Path:
    payload = execution_result_to_dict(result)
    return _atomic_text(
        Path(path),
        json.dumps(
            payload,
            ensure_ascii=False,
            indent=2,
            allow_nan=False,
        )
        + "\n",
    )


def autosave_result_json(
    result: ExecutionResult,
    directory: str | Path | None = None,
) -> Path:
    """Atomically preserve one terminal execution result as local JSON."""

    root = (
        Path(directory)
        if directory is not None
        else Path.home() / "Documents" / "SCPI 측정결과" / "자동저장"
    )
    timestamp = re.sub(r"[^0-9]", "", result.started_at_utc)[:14]
    if not timestamp:
        timestamp = "시간미상"
    mode = "dry-run" if result.dry_run else "actual"
    run_id = re.sub(r"[^A-Za-z0-9_-]", "_", result.run_id).strip("_")
    if not run_id:
        run_id = "run"
    return save_result_json(
        result,
        root / f"{timestamp}_{mode}_{run_id}.json",
    )


def _md(value: object) -> str:
    text = "" if value is None else str(value)
    return (
        text.replace("\\", "\\\\")
        .replace("|", "\\|")
        .replace("\r\n", "<br>")
        .replace("\n", "<br>")
        .replace("\r", "<br>")
    )


def _markdown_table(
    headers: Sequence[str],
    rows: Iterable[Sequence[object]],
) -> list[str]:
    lines = [
        "| " + " | ".join(headers) + " |",
        "|" + "|".join("---" for _header in headers) + "|",
    ]
    lines.extend(
        "| " + " | ".join(_md(value) for value in row) + " |"
        for row in rows
    )
    return lines


def result_markdown(result: ExecutionResult) -> str:
    payload = execution_result_to_dict(result)
    summary = payload["summary"]
    lines = [
        "# SCPI 측정 자동화 실행 결과",
        "",
        (
            "> 시험 계획값을 명시적으로 연결해 실행했습니다. 실제 적용값과 "
            "전송 내용은 아래 실행 단계 및 명령 로그에서 확인하세요."
            if result.uses_plan_values
            else
            "> 계획값 연결이 없는 고정 루틴입니다. 실제 장비로 전송된 내용은 "
            "아래 실행 단계 및 명령 로그에서 확인하세요."
        ),
        "",
        "## 실행 요약",
        "",
        *_markdown_table(
            ("항목", "값"),
            (
                ("실행 ID", result.run_id),
                ("상태", result.status.label_ko),
                ("실행 방식", "Dry Run" if result.dry_run else "실제 장비"),
                ("시작 UTC", result.started_at_utc),
                ("종료 UTC", result.finished_at_utc),
                ("소요 시간(ms)", f"{result.duration_ms:.3f}"),
                ("종료 사유", result.stop_reason),
                ("장비 수", summary["instrument_count"]),
                ("루틴 단계 수", summary["routine_step_count"]),
                ("실제 확장 단계 수", summary["executed_step_count"]),
                ("시험 케이스 수", summary["test_case_count"]),
                (
                    "계획값 연결",
                    "사용" if summary["uses_plan_values"] else "미사용",
                ),
                ("컴파일 지문", result.compiled_digest),
                ("계획 항목 수", summary["plan_item_count"]),
                ("측정값 수", summary["measurement_count"]),
                ("오류 수", summary["error_count"]),
            ),
        ),
        "",
        "## 장비",
        "",
        *_markdown_table(
            (
                "VISA 주소",
                "분류",
                "제조사",
                "모델",
                "시리얼",
                "펌웨어",
                "프로파일",
                "옵션",
                "Raw IDN",
            ),
            (
                (
                    item["resource"],
                    item["category_label_ko"],
                    item["manufacturer"],
                    item["model"],
                    item["serial"],
                    item["firmware"],
                    item["profile_id"],
                    (
                        item["option_response"]
                        if item["option_state"] == "queried"
                        else item["option_state"]
                    ),
                    item["raw_idn"],
                )
                for item in payload["instruments"]
            ),
        ),
        "",
        "## 설정한 루틴",
        "",
        *_markdown_table(
            ("순서", "형식", "장비", "기능", "인수/시간", "결과 이름"),
            (
                (
                    item["step_index"],
                    item["type"],
                    item.get("resource", ""),
                    item.get("feature_id", ""),
                    (
                        json.dumps(
                            {
                                "fixed": item.get("arguments", {}),
                                "plan_bindings": item.get(
                                    "plan_bindings", {}
                                ),
                            },
                            ensure_ascii=False,
                            allow_nan=False,
                        )
                        if item["type"] == "feature"
                        else item.get(
                            "seconds",
                            item.get("timeout_seconds", ""),
                        )
                    ),
                    item.get("result_name", ""),
                )
                for item in payload["routine"]
            ),
        ),
        "",
        "## 측정 계획",
        "",
        *_markdown_table(
            (
                "순서",
                "시험 케이스",
                "반복",
                "장비",
                "분류",
                "시험 방법/형식",
                "계획 값",
            ),
            (
                (
                    item["plan_index"],
                    item.get("case_name", ""),
                    item.get("repeat_count", 1),
                    item["resource"],
                    item["category_label_ko"],
                    item.get("method_label_ko", item["type"]),
                    json.dumps(
                        (
                            item.get("values")
                            or {
                                "common_values": item.get(
                                    "common_values", []
                                ),
                                "detail_values": item.get(
                                    "detail_values", []
                                ),
                            }
                        ),
                        ensure_ascii=False,
                        allow_nan=False,
                    ),
                )
                for item in payload["plan"]
            ),
        ),
        "",
        "## 측정 결과",
        "",
        *_markdown_table(
            (
                "순번",
                "UTC",
                "단계",
                "시험 케이스",
                "반복",
                "장비",
                "결과 이름",
                "값",
                "단위",
                "원본 응답",
            ),
            (
                (
                    item["sequence"],
                    item["timestamp_utc"],
                    item["step_index"],
                    item.get("case_name", ""),
                    item.get("repeat_index", ""),
                    item["resource"],
                    item["result_name"],
                    json.dumps(
                        item["parsed_value"],
                        ensure_ascii=False,
                        allow_nan=False,
                    ),
                    item["unit"],
                    item["raw_response"],
                )
                for item in payload["measurements"]
            ),
        ),
        "",
        "## 실행 단계",
        "",
        *_markdown_table(
            (
                "단계",
                "시험 케이스",
                "반복",
                "원본 루틴 단계",
                "상태",
                "장비",
                "Operation",
                "적용 계획값",
                "SCPI",
                "응답",
                "측정값 ID",
                "소요(ms)",
                "오류",
            ),
            (
                (
                    item["step_index"],
                    item.get("case_name", ""),
                    item.get("repeat_index", ""),
                    item.get("template_step_index", ""),
                    item["status"],
                    item["resource"],
                    (
                        f"{item['capability_id']}::{item['operation']}"
                        if item["capability_id"]
                        else item["operation"]
                    ),
                    json.dumps(
                        item.get("applied_plan_bindings", []),
                        ensure_ascii=False,
                        allow_nan=False,
                    ),
                    item["command"],
                    item["response"],
                    item["measurement_id"],
                    f"{item['duration_ms']:.3f}",
                    item["error"],
                )
                for item in payload["steps"]
            ),
        ),
        "",
        "## 안전 종료 기록",
        "",
        *_markdown_table(
            ("순번", "UTC", "장비", "Operation", "명령", "상태", "응답", "설명"),
            (
                (
                    item["sequence"],
                    item["timestamp_utc"],
                    item["resource"],
                    item["operation_id"],
                    item["command"],
                    item["status"],
                    item["response"],
                    item["message"],
                )
                for item in payload["safety"]
            ),
        ),
        "",
        "## 전체 실행 로그",
        "",
        *_markdown_table(
            (
                "순번",
                "UTC",
                "수준",
                "종류",
                "단계",
                "시험 케이스",
                "반복",
                "장비",
                "Operation",
                "메시지",
                "SCPI",
                "응답",
                "해석값",
                "단위",
                "측정값 ID",
            ),
            (
                (
                    item["sequence"],
                    item["timestamp_utc"],
                    item["level"],
                    item["kind"],
                    item["step_index"] or "",
                    item.get("case_name", ""),
                    item.get("repeat_index", ""),
                    item["resource"],
                    item["capability_id"],
                    item["message"],
                    item["command"],
                    item["response"],
                    item["parsed_value"],
                    item["unit"],
                    item["measurement_id"],
                )
                for item in payload["events"]
            ),
        ),
        "",
    ]
    return "\n".join(lines)


def save_result_markdown(
    result: ExecutionResult,
    path: str | Path,
) -> Path:
    return _atomic_text(Path(path), result_markdown(result))


def _xlsx_text(value: object) -> str:
    text = "" if value is None else str(value)
    text = _ILLEGAL_XML.sub("", text)
    if len(text) > _XLSX_CELL_LIMIT:
        raise ValueError(
            "Excel 셀 제한보다 긴 문자열입니다. 응답 청크 열로 나누어야 합니다."
        )
    return text


def _chunks(value: object) -> tuple[str, ...]:
    text = _ILLEGAL_XML.sub("", "" if value is None else str(value))
    if not text:
        return ("",)
    return tuple(
        text[index : index + _XLSX_CHUNK_SIZE]
        for index in range(0, len(text), _XLSX_CHUNK_SIZE)
    )


def _write_value(worksheet, row: int, column: int, value, formats) -> None:
    if value is None:
        worksheet.write_blank(row, column, None, formats["body"])
    elif isinstance(value, bool):
        worksheet.write_boolean(row, column, value, formats["body"])
    elif isinstance(value, (int, float)) and not isinstance(value, bool):
        number = float(value)
        if not math.isfinite(number):
            raise ValueError("Excel에 NaN 또는 무한대 값을 쓸 수 없습니다.")
        worksheet.write_number(row, column, value, formats["number"])
    else:
        # write_string prevents formula/URL interpretation of SCPI responses
        # and user-provided result names such as '=1+1' or '@SUM(...)'.
        worksheet.write_string(
            row,
            column,
            _xlsx_text(value),
            formats["body"],
        )


def _setup_table_sheet(
    workbook,
    name: str,
    headers: Sequence[str],
    widths: Sequence[float],
    formats,
):
    worksheet = workbook.add_worksheet(name)
    worksheet.hide_gridlines(2)
    worksheet.freeze_panes(1, 0)
    worksheet.set_row(0, 24)
    for column, header in enumerate(headers):
        worksheet.write_string(0, column, header, formats["header"])
        worksheet.set_column(column, column, widths[column])
    return worksheet


def save_result_xlsx(
    result: ExecutionResult,
    path: str | Path,
) -> Path:
    try:
        import xlsxwriter
    except ImportError as exc:
        raise RuntimeError(
            "Excel 저장 구성요소(XlsxWriter)가 없습니다. 배포본에는 "
            "함께 포함되어야 합니다."
        ) from exc

    destination = Path(path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{destination.stem}.",
        suffix=".xlsx",
        dir=destination.parent,
    )
    os.close(descriptor)
    temporary = Path(temporary_name)
    payload = execution_result_to_dict(result)
    workbook = None
    try:
        workbook = xlsxwriter.Workbook(
            str(temporary),
            {
                "constant_memory": True,
                "strings_to_formulas": False,
                "strings_to_urls": False,
                "tmpdir": str(destination.parent),
            },
        )
        formats = {
            "title": workbook.add_format(
                {
                    "bold": True,
                    "font_size": 16,
                    "font_color": "#FFFFFF",
                    "bg_color": "#0B1F33",
                    "align": "left",
                    "valign": "vcenter",
                }
            ),
            "section": workbook.add_format(
                {
                    "bold": True,
                    "font_color": "#0B1F33",
                    "bg_color": "#DCEBFA",
                    "bottom": 1,
                    "bottom_color": "#9FBAD0",
                }
            ),
            "label": workbook.add_format(
                {
                    "bold": True,
                    "font_color": "#344054",
                    "bg_color": "#F2F4F7",
                    "border": 1,
                    "border_color": "#D0D5DD",
                }
            ),
            "header": workbook.add_format(
                {
                    "bold": True,
                    "font_color": "#FFFFFF",
                    "bg_color": "#1769AA",
                    "border": 1,
                    "border_color": "#D0D5DD",
                    "text_wrap": True,
                    "valign": "vcenter",
                }
            ),
            "body": workbook.add_format(
                {
                    "font_color": "#101828",
                    "border": 1,
                    "border_color": "#E4E7EC",
                    "valign": "top",
                    "text_wrap": True,
                }
            ),
            "number": workbook.add_format(
                {
                    "font_color": "#101828",
                    "border": 1,
                    "border_color": "#E4E7EC",
                    "valign": "top",
                }
            ),
            "note": workbook.add_format(
                {
                    "font_color": "#475467",
                    "italic": True,
                    "text_wrap": True,
                    "bg_color": "#F8FAFC",
                }
            ),
        }

        summary_sheet = workbook.add_worksheet("요약")
        summary_sheet.hide_gridlines(2)
        summary_sheet.set_column(0, 0, 24)
        summary_sheet.set_column(1, 1, 72)
        summary_sheet.set_row(0, 34)
        summary_sheet.merge_range(
            "A1:B1",
            "SCPI 측정 자동화 실행 결과",
            formats["title"],
        )
        summary_sheet.merge_range(
            "A2:B2",
            (
                (
                    "시험 계획값을 루틴에 연결해 실행했습니다. 실제 적용값과 "
                    "전송 내용은 실행단계·명령로그에서 확인하세요."
                )
                if result.uses_plan_values
                else (
                    "계획값 연결이 없는 고정 루틴입니다. 실제 전송 내용은 "
                    "루틴·실행단계·명령로그에서 확인하세요."
                )
            ),
            formats["note"],
        )
        summary_rows = (
            ("실행 ID", result.run_id),
            ("상태", result.status.label_ko),
            ("실행 방식", "Dry Run" if result.dry_run else "실제 장비"),
            ("시작 UTC", result.started_at_utc),
            ("종료 UTC", result.finished_at_utc),
            ("소요 시간(ms)", result.duration_ms),
            ("종료 사유", result.stop_reason),
            ("장비 수", len(result.instruments)),
            ("루틴 단계 수", len(result.routine_steps)),
            (
                "실제 확장 단계 수",
                len(result.executed_steps or result.routine_steps),
            ),
            ("시험 케이스 수", result.test_case_count),
            ("계획값 연결", "사용" if result.uses_plan_values else "미사용"),
            ("컴파일 지문", result.compiled_digest),
            ("계획 항목 수", len(result.plan_items)),
            ("측정값 수", len(result.measurements)),
            ("오류 수", result.error_count),
            ("안전 종료 기록 수", len(result.safety_records)),
        )
        for row, (label, value) in enumerate(summary_rows, start=3):
            summary_sheet.write_string(row, 0, label, formats["label"])
            _write_value(summary_sheet, row, 1, value, formats)

        equipment_headers = (
            "장비ID",
            "VISA Resource",
            "분류",
            "제조사",
            "모델",
            "시리얼",
            "펌웨어",
            "Raw IDN",
            "옵션 상태",
            "옵션 응답",
            "Profile ID",
            "검증 지문",
            "통과 Operation 수",
        )
        equipment_sheet = _setup_table_sheet(
            workbook,
            "장비",
            equipment_headers,
            (12, 28, 20, 18, 18, 18, 18, 42, 13, 35, 24, 45, 16),
            formats,
        )
        for row, item in enumerate(payload["instruments"], start=1):
            values = (
                f"DEV-{row:03d}",
                item["resource"],
                item["category_label_ko"],
                item["manufacturer"],
                item["model"],
                item["serial"],
                item["firmware"],
                item["raw_idn"],
                item["option_state"],
                item["option_response"],
                item["profile_id"],
                item["validation_catalog_fingerprint"],
                len(item["compatible_operation_ids"]),
            )
            for column, value in enumerate(values):
                _write_value(equipment_sheet, row, column, value, formats)
        if payload["instruments"]:
            equipment_sheet.autofilter(
                0, 0, len(payload["instruments"]), len(equipment_headers) - 1
            )

        routine_headers = (
            "순서",
            "단계 형식",
            "VISA Resource",
            "기능 ID",
            "인수 JSON",
            "계획값 연결 JSON",
            "결과 이름",
            "대기(초)",
            "완료 Timeout(초)",
        )
        routine_sheet = _setup_table_sheet(
            workbook,
            "루틴",
            routine_headers,
            (8, 20, 28, 42, 42, 42, 24, 14, 20),
            formats,
        )
        for row, item in enumerate(payload["routine"], start=1):
            values = (
                item["step_index"],
                item["type"],
                item.get("resource", ""),
                item.get("feature_id", ""),
                json.dumps(
                    item.get("arguments", {}),
                    ensure_ascii=False,
                    allow_nan=False,
                )
                if item["type"] == "feature"
                else "",
                json.dumps(
                    item.get("plan_bindings", {}),
                    ensure_ascii=False,
                    allow_nan=False,
                )
                if item["type"] == "feature"
                else "",
                item.get("result_name", ""),
                item.get("seconds"),
                item.get("timeout_seconds"),
            )
            for column, value in enumerate(values):
                _write_value(routine_sheet, row, column, value, formats)
        if payload["routine"]:
            routine_sheet.autofilter(
                0, 0, len(payload["routine"]), len(routine_headers) - 1
            )

        plan_headers = (
            "계획 순서",
            "Case ID",
            "시험 이름",
            "반복 횟수",
            "VISA Resource",
            "분류",
            "시험 방법/형식",
            "구역",
            "Field ID",
            "값",
            "단위",
        )
        plan_sheet = _setup_table_sheet(
            workbook,
            "시험계획",
            plan_headers,
            (12, 20, 18, 12, 28, 22, 28, 14, 32, 45, 14),
            formats,
        )
        plan_row = 1
        for item in payload["plan"]:
            if item["type"] == "generic":
                field_groups = (
                    ("공통", item.get("common_values", [])),
                    ("상세", item.get("detail_values", [])),
                )
            else:
                field_groups = (
                    (
                        "설정",
                        [
                            {"field_id": key, "value": value, "unit": ""}
                            for key, value in item.get("values", {}).items()
                        ],
                    ),
                )
            for section, fields in field_groups:
                if not fields:
                    fields = [{"field_id": "", "value": "", "unit": ""}]
                for field in fields:
                    values = (
                        item["plan_index"],
                        item.get("case_id", ""),
                        item.get("case_name", ""),
                        item.get("repeat_count", 1),
                        item["resource"],
                        item["category_label_ko"],
                        item.get("method_label_ko", item["type"]),
                        section,
                        field["field_id"],
                        field["value"],
                        field.get("unit", ""),
                    )
                    for column, value in enumerate(values):
                        _write_value(plan_sheet, plan_row, column, value, formats)
                    plan_row += 1
        if plan_row > 1:
            plan_sheet.autofilter(
                0, 0, plan_row - 1, len(plan_headers) - 1
            )

        measurement_headers = (
            "순번",
            "UTC",
            "단계",
            "Case ID",
            "시험 이름",
            "반복",
            "원본 루틴 단계",
            "VISA Resource",
            "제조사",
            "모델",
            "기능 ID",
            "Operation",
            "결과 이름",
            "응답 형식",
            "해석 값",
            "단위",
            "상태",
            "응답 청크",
            "원본 응답",
        )
        measurement_sheet = _setup_table_sheet(
            workbook,
            "측정결과",
            measurement_headers,
            (
                8, 25, 8, 20, 18, 10, 14, 28, 18, 18, 40, 34, 25, 15,
                30, 10, 12, 12, 55
            ),
            formats,
        )
        measurement_row = 1
        for item in payload["measurements"]:
            response_chunks = _chunks(item["raw_response"])
            for chunk_index, response_chunk in enumerate(
                response_chunks, start=1
            ):
                values = (
                    item["sequence"],
                    item["timestamp_utc"],
                    item["step_index"],
                    item.get("case_id", ""),
                    item.get("case_name", ""),
                    item.get("repeat_index", ""),
                    item.get("template_step_index", ""),
                    item["resource"],
                    item["manufacturer"],
                    item["model"],
                    item["feature_id"],
                    f"{item['capability_id']}::{item['operation']}",
                    item["result_name"],
                    item["response_type"],
                    (
                        (
                            lambda rendered: (
                                rendered
                                if len(rendered) <= _XLSX_CHUNK_SIZE
                                else "전체 해석 값은 원본 응답 청크를 참조하세요."
                            )
                        )(
                            json.dumps(
                                item["parsed_value"],
                                ensure_ascii=False,
                                allow_nan=False,
                            )
                        )
                        if chunk_index == 1
                        else ""
                    ),
                    item["unit"] if chunk_index == 1 else "",
                    item["status"] if chunk_index == 1 else "continuation",
                    f"{chunk_index}/{len(response_chunks)}",
                    response_chunk,
                )
                for column, value in enumerate(values):
                    _write_value(
                        measurement_sheet,
                        measurement_row,
                        column,
                        value,
                        formats,
                    )
                measurement_row += 1
        if measurement_row > 1:
            measurement_sheet.autofilter(
                0, 0, measurement_row - 1, len(measurement_headers) - 1
            )

        step_headers = (
            "단계",
            "Case ID",
            "시험 이름",
            "반복",
            "원본 루틴 단계",
            "형식",
            "상태",
            "시작 UTC",
            "종료 UTC",
            "소요(ms)",
            "VISA Resource",
            "기능 ID",
            "Operation",
            "결과 이름",
            "적용 계획값 JSON",
            "SCPI",
            "응답 청크",
            "응답",
            "측정값 ID",
            "오류",
        )
        step_sheet = _setup_table_sheet(
            workbook,
            "실행단계",
            step_headers,
            (
                8, 20, 18, 10, 14, 20, 14, 25, 25, 14, 28, 40, 34, 24,
                42, 45, 12, 50, 36, 45
            ),
            formats,
        )
        step_row = 1
        for item in payload["steps"]:
            response_chunks = _chunks(item["response"])
            for chunk_index, response_chunk in enumerate(
                response_chunks, start=1
            ):
                values = (
                    item["step_index"],
                    item.get("case_id", ""),
                    item.get("case_name", ""),
                    item.get("repeat_index", ""),
                    item.get("template_step_index", ""),
                    item["step_kind"],
                    item["status"],
                    item["started_at_utc"],
                    item["finished_at_utc"],
                    item["duration_ms"],
                    item["resource"],
                    item["feature_id"],
                    (
                        f"{item['capability_id']}::{item['operation']}"
                        if item["capability_id"]
                        else item["operation"]
                    ),
                    item["result_name"],
                    json.dumps(
                        item.get("applied_plan_bindings", []),
                        ensure_ascii=False,
                        allow_nan=False,
                    ),
                    item["command"],
                    f"{chunk_index}/{len(response_chunks)}",
                    response_chunk,
                    item["measurement_id"],
                    item["error"],
                )
                for column, value in enumerate(values):
                    _write_value(step_sheet, step_row, column, value, formats)
                step_row += 1
        if step_row > 1:
            step_sheet.autofilter(
                0, 0, step_row - 1, len(step_headers) - 1
            )

        log_headers = (
            "순번",
            "UTC",
            "수준",
            "종류",
            "단계",
            "전체 단계",
            "Case ID",
            "시험 이름",
            "반복",
            "원본 루틴 단계",
            "VISA Resource",
            "Feature ID",
            "Capability ID",
            "메시지",
            "SCPI",
            "응답 청크",
            "응답",
            "해석값",
            "단위",
            "측정값 ID",
        )
        log_sheet = _setup_table_sheet(
            workbook,
            "명령로그",
            log_headers,
            (
                8,
                25,
                12,
                24,
                8,
                12,
                20,
                18,
                10,
                14,
                28,
                42,
                34,
                55,
                45,
                12,
                55,
                28,
                12,
                36,
            ),
            formats,
        )
        log_row = 1
        for item in payload["events"]:
            response_chunks = _chunks(item["response"])
            for chunk_index, response_chunk in enumerate(
                response_chunks, start=1
            ):
                values = (
                    item["sequence"],
                    item["timestamp_utc"],
                    item["level"],
                    item["kind"],
                    item["step_index"],
                    item["total_steps"],
                    item.get("case_id", ""),
                    item.get("case_name", ""),
                    item.get("repeat_index", ""),
                    item.get("template_step_index", ""),
                    item["resource"],
                    item["feature_id"],
                    item["capability_id"],
                    item["message"],
                    item["command"],
                    f"{chunk_index}/{len(response_chunks)}",
                    response_chunk,
                    item["parsed_value"],
                    item["unit"],
                    item["measurement_id"],
                )
                for column, value in enumerate(values):
                    _write_value(log_sheet, log_row, column, value, formats)
                log_row += 1
        if log_row > 1:
            log_sheet.autofilter(
                0, 0, log_row - 1, len(log_headers) - 1
            )

        safety_headers = (
            "순번",
            "UTC",
            "VISA Resource",
            "Operation",
            "SCPI",
            "상태",
            "응답",
            "설명",
        )
        safety_sheet = _setup_table_sheet(
            workbook,
            "안전종료",
            safety_headers,
            (8, 25, 28, 34, 45, 22, 35, 55),
            formats,
        )
        for row, item in enumerate(payload["safety"], start=1):
            values = (
                item["sequence"],
                item["timestamp_utc"],
                item["resource"],
                item["operation_id"],
                item["command"],
                item["status"],
                item["response"],
                item["message"],
            )
            for column, value in enumerate(values):
                _write_value(safety_sheet, row, column, value, formats)
        if payload["safety"]:
            safety_sheet.autofilter(
                0, 0, len(payload["safety"]), len(safety_headers) - 1
            )

        workbook.set_properties(
            {
                "title": "SCPI 측정 자동화 실행 결과",
                "subject": result.run_id,
                "author": "SCPI Automation Platform",
                "comments": (
                    "Generated offline from a deterministic execution result."
                ),
            }
        )
        workbook.close()
        workbook = None
        os.replace(temporary, destination)
    except BaseException:
        if workbook is not None:
            try:
                workbook.close()
            except Exception:
                pass
        try:
            temporary.unlink()
        except FileNotFoundError:
            pass
        raise
    return destination


def export_result_bundle(
    result: ExecutionResult,
    directory: str | Path,
) -> tuple[Path, ...]:
    root = Path(directory)
    root.mkdir(parents=True, exist_ok=True)
    timestamp = (
        result.started_at_utc.replace("-", "")
        .replace(":", "")
        .replace("+00:00", "Z")
        .replace(".", "_")
    )
    stem = f"SCPI_result_{timestamp}_{result.run_id[:8]}"
    return (
        save_result_json(result, root / f"{stem}.json"),
        save_result_markdown(result, root / f"{stem}.md"),
        save_result_xlsx(result, root / f"{stem}.xlsx"),
    )
