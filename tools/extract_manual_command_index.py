from __future__ import annotations

import argparse
import hashlib
import json
import re
from pathlib import Path
from typing import Any

INDEX_ROW = re.compile(r"^(.+?)\.{4,}\s*(\d+)\s*$", re.MULTILINE)
CHAPTER_INDEX_ROW = re.compile(
    r"^(.+?)\.{4,}\s*(\d+)\.(\d+)"
    r"(?:\s*,\s*\d+\.\d+)*\s*$",
    re.MULTILINE,
)
OPTION_ANNOTATION = re.compile(
    r"\s+\((?=[^)]*(?:K|B)\d)[^)]*\)\s*$",
    re.IGNORECASE,
)
OPTIONAL_GROUP = re.compile(r"\[([^\[\]]*)\]")
PLACEHOLDER = re.compile(r"<[^>]+>")
TOKEN = re.compile(r"[A-Za-z]+")
SCPI_HEADER_CHARACTERS = frozenset(
    "ABCDEFGHIJKLMNOPQRSTUVWXYZ"
    "abcdefghijklmnopqrstuvwxyz"
    "0123456789"
    "*?:|[].<>-"
)
NON_SCPI_INTERFACE_HEADERS = frozenset({"DCL", "GET", "GTL", "LLO", "SDC"})

MANUAL_ONLY_GROUPS = (
    "ADJ",
    "CAL",
    "DIAG",
    "SERV",
)
LARGE_RESPONSE_TOKENS = (
    ":DATA",
    "WAV",
    "IQ:DATA",
    "MMEM:DATA",
    "HCOP:DATA",
)
DISRUPTIVE_COMMON_COMMANDS = (
    "*CLS",
    "*RCL",
    "*RST",
    "*SAV",
    "*TRG",
    "*TST",
    "*WAI",
)


def _short_mnemonic(value: str) -> str:
    """Return the mandatory uppercase portion of one SCPI mnemonic."""

    letters = "".join(character for character in value if character.isupper())
    return letters or value.upper()


def _remove_optional_groups(value: str) -> str:
    previous = None
    current = value
    while current != previous:
        previous = current
        current = OPTIONAL_GROUP.sub("", current)
    return current


def _normalize_scpi_spacing(value: str) -> str:
    """Repair harmless spacing introduced by old PDF text extraction."""

    value = re.sub(r"\s*:\s*", ":", value.strip())
    value = re.sub(r"\s*\|\s*", "|", value)
    value = re.sub(r"(?<=\d)\s*\.\.\.\s*(?=\d)", "...", value)
    return value


def _command_header(command_pattern: str) -> tuple[str, str]:
    """Return the SCPI header and any trailing fixed argument/annotation."""

    normalized = _normalize_scpi_spacing(command_pattern)
    parts = normalized.split(maxsplit=1)
    return parts[0], parts[1] if len(parts) == 2 else ""


def _canonical_probe_path(command_pattern: str) -> str:
    """Build a conservative short-form query candidate from an index entry.

    Optional path elements are omitted, the first spelling in an alternative is
    selected, and numeric suffix placeholders use 1.  This is only a probe
    candidate.  A successful instrument test is still required before use.
    """

    header, _trailing = _command_header(command_pattern)
    explicit_query = header.endswith("?")
    value = header.removesuffix("?")
    value = _remove_optional_groups(value)
    value = PLACEHOLDER.sub("1", value)
    value = re.sub(r"\s+", "", value)
    if value.startswith("*"):
        common = re.match(r"\*[A-Za-z]+", value)
        if common is None:
            return ""
        rendered = common.group(0).upper()
        return f"{rendered}?"

    rendered_segments: list[str] = []
    for raw_segment in value.split(":"):
        if not raw_segment:
            continue
        selected = raw_segment.split("|", 1)[0]
        match = TOKEN.match(selected)
        if match is None:
            continue
        mnemonic = _short_mnemonic(match.group(0))
        suffix = selected[match.end() :]
        rendered_segments.append(f"{mnemonic}{suffix}")

    rendered = ":".join(rendered_segments)
    return f"{rendered}?" if rendered or explicit_query else ""


def _command_group(command_pattern: str) -> str:
    canonical = _canonical_probe_path(command_pattern).removesuffix("?")
    if canonical.startswith("*"):
        return canonical
    return canonical.split(":", 1)[0] if canonical else "OTHER"


def _probe_policy(command_pattern: str, query_scpi: str) -> str:
    group = _command_group(command_pattern)
    upper = query_scpi.upper()
    header, trailing = _command_header(command_pattern)
    if any(upper.startswith(command) for command in DISRUPTIVE_COMMON_COMMANDS):
        return "manual_only"
    if group in MANUAL_ONLY_GROUPS:
        return "manual_only"
    if header.endswith("?") and trailing:
        # The query needs a documented argument.  Probing without it would
        # produce a false negative and guessing an argument is not safe.
        return "manual_only"
    if any(token in upper for token in LARGE_RESPONSE_TOKENS):
        return "query_limited"
    if command_pattern.rstrip().endswith("?"):
        return "query_explicit"
    return "query_probe"


def _candidate(
    *,
    profile_id: str,
    command_pattern: str,
    manual_page: int,
    manual_reference: str = "",
    option_annotations: tuple[str, ...] = (),
    source_pdf_page: int | None = None,
) -> dict[str, Any]:
    digest = hashlib.sha1(
        f"{profile_id}\0{command_pattern}".encode("utf-8")
    ).hexdigest()[:16]
    query_scpi = _canonical_probe_path(command_pattern)
    candidate = {
        "command_id": f"{profile_id}.manual.{digest}",
        "command_pattern": command_pattern,
        "command_group": _command_group(command_pattern),
        "manual_page": manual_page,
        "query_scpi_candidate": query_scpi,
        "query_support": (
            "manual_explicit"
            if _command_header(command_pattern)[0].endswith("?")
            else "unverified_probe"
        ),
        "write_support": "unknown",
        "probe_policy": _probe_policy(command_pattern, query_scpi),
        "verification": "manual_index_candidate",
    }
    if manual_reference:
        candidate["manual_reference"] = manual_reference
    if option_annotations:
        candidate["option_annotations"] = list(option_annotations)
    if source_pdf_page is not None:
        candidate["source_pdf_page"] = source_pdf_page
    return candidate


def _split_option_annotation(command: str) -> tuple[str, str]:
    """Separate an option note such as ``(K20)`` from a SCPI header."""

    match = OPTION_ANNOTATION.search(command)
    if match is None:
        normalized = command.strip()
        annotation = ""
    else:
        normalized = command[: match.start()].strip()
        annotation = match.group(0).strip()

    # PDF text layers can introduce a dangling closing bracket. Preserve
    # balanced optional groups, but remove a final unmatched bracket.
    while (
        normalized.endswith("]")
        and normalized.count("]") > normalized.count("[")
    ):
        normalized = normalized[:-1].rstrip()
    return normalized, annotation


def extract_index(
    *,
    pdf_path: Path,
    profile_id: str,
    start_page: int,
    end_page: int,
    page_reference_mode: str = "integer",
    reference_chapter: int | None = None,
    reference_page_offset: int = 0,
) -> list[dict[str, Any]]:
    try:
        from pypdf import PdfReader
    except ImportError as exc:
        raise RuntimeError(
            "PDF extraction requires pypdf. Use the bundled document runtime "
            "or install pypdf for this developer-only tool."
        ) from exc

    reader = PdfReader(str(pdf_path))
    if not 1 <= start_page <= end_page <= len(reader.pages):
        raise ValueError(
            f"page range must be inside 1..{len(reader.pages)}"
        )

    if page_reference_mode not in {"integer", "chapter_decimal"}:
        raise ValueError(
            "page_reference_mode must be 'integer' or 'chapter_decimal'"
        )
    if (
        page_reference_mode == "chapter_decimal"
        and reference_chapter is None
    ):
        raise ValueError(
            "reference_chapter is required for chapter_decimal indexes"
        )

    commands: dict[str, dict[str, Any]] = {}
    for page_number in range(start_page, end_page + 1):
        text = reader.pages[page_number - 1].extract_text() or ""
        if page_reference_mode == "integer":
            rows = (
                (raw_command, int(raw_manual_page), "", "")
                for raw_command, raw_manual_page in INDEX_ROW.findall(text)
            )
        else:
            rows = (
                (
                    raw_command,
                    int(raw_reference_page) + reference_page_offset,
                    f"{raw_chapter}.{raw_reference_page}",
                    raw_chapter,
                )
                for (
                    raw_command,
                    raw_chapter,
                    raw_reference_page,
                ) in CHAPTER_INDEX_ROW.findall(text)
            )

        for (
            raw_command,
            manual_page,
            manual_reference,
            raw_chapter,
        ) in rows:
            if (
                page_reference_mode == "chapter_decimal"
                and int(raw_chapter) != reference_chapter
            ):
                continue
            command, option_annotation = _split_option_annotation(
                raw_command.strip()
            )
            if not command:
                continue
            entry = commands.setdefault(
                command,
                {
                    "manual_page": manual_page,
                    "manual_reference": manual_reference,
                    "option_annotations": set(),
                },
            )
            if option_annotation:
                entry["option_annotations"].add(option_annotation)

    return [
        _candidate(
            profile_id=profile_id,
            command_pattern=command,
            manual_page=entry["manual_page"],
            manual_reference=entry["manual_reference"],
            option_annotations=tuple(
                sorted(entry["option_annotations"])
            ),
        )
        for command, entry in sorted(
            commands.items(),
            key=lambda item: (item[1]["manual_page"], item[0]),
        )
    ]


def _leading_scpi_header(line: str) -> str:
    """Return a literal SCPI header at the beginning of a manual line.

    Some official manuals do not contain an alphabetical command index.  Their
    command-reference bookmarks or ``Remote Interface Operation`` sections do
    still begin command syntax lines with a SCPI header.  This parser keeps
    only that published header and deliberately ignores parameters and prose.
    It is conservative: a plain all-uppercase section title such as ``FSK
    Rate`` is not treated as a command.
    """

    normalized = _normalize_scpi_spacing(line)
    if not normalized:
        return ""
    raw_header, *rest_parts = normalized.split(maxsplit=1)
    raw_header = raw_header.split("{", 1)[0].split("[(@", 1)[0]
    for placeholder in tuple(PLACEHOLDER.finditer(raw_header)):
        following = (
            raw_header[placeholder.end()]
            if placeholder.end() < len(raw_header)
            else ""
        )
        if following != ":":
            raw_header = raw_header[: placeholder.start()]
            break
    header = raw_header.rstrip(".,;")
    if header.startswith("*") and "*" in header[1:]:
        header = header[: header.index("*", 1)]
    if header.startswith("[SOURce:]") and "[SOURce:]" in header[1:]:
        header = header[: header.index("[SOURce:]", 1)]
    if header.upper() in NON_SCPI_INTERFACE_HEADERS:
        return ""
    if not header or any(
        character not in SCPI_HEADER_CHARACTERS for character in header
    ):
        return ""
    if not re.search(r"[A-Za-z]", header):
        return ""
    first_mnemonic = re.search(r"[A-Za-z]+", header)
    if first_mnemonic is None:
        return ""
    mandatory_prefix = re.match(r"[A-Z]+", first_mnemonic.group(0))
    if mandatory_prefix is None or len(mandatory_prefix.group(0)) < 2:
        return ""

    if header.startswith("*") or ":" in header or "?" in header:
        return header

    # Root-level set commands such as ``FUNCtion PULSe`` and
    # ``VOLTage {<amplitude>|...}`` are valid.  A prose line such as
    # ``SOURceN keyword ...`` is not.
    rest = rest_parts[0].strip() if rest_parts else ""
    if not rest:
        return header if re.search(r"[A-Z].*[a-z]", header) else ""
    first_argument = rest.split(maxsplit=1)[0].rstrip(".,;")
    if rest.startswith(("{", "<", "[", "(", '"')):
        return header
    if (
        (
            re.fullmatch(r"[A-Z0-9|]+", first_argument)
            or (
                re.fullmatch(r"[A-Z][A-Za-z0-9|]*", first_argument)
                and len((re.match(r"[A-Z]+", first_argument) or [""])[0])
                >= 2
            )
        )
        and re.search(r"[A-Z].*[a-z]", header)
    ):
        return header
    return ""


def _conservative_candidate(
    *,
    profile_id: str,
    command_pattern: str,
    manual_page: int,
    source_pdf_page: int,
) -> dict[str, Any]:
    """Create a candidate and disable automatic probing of malformed headers."""

    candidate = _candidate(
        profile_id=profile_id,
        command_pattern=command_pattern,
        manual_page=manual_page,
        source_pdf_page=source_pdf_page,
    )
    if (
        command_pattern.count("[") != command_pattern.count("]")
        or (
            not command_pattern.startswith("[")
            and re.search(r"\][A-Za-z]", command_pattern)
        )
    ):
        candidate["probe_policy"] = "manual_only"
        candidate["query_support"] = "manual_review_required"
    return candidate


def extract_outline_commands(
    *,
    pdf_path: Path,
    profile_id: str,
    start_page: int,
    end_page: int,
    manual_page_offset: int = 0,
) -> list[dict[str, Any]]:
    """Extract command headings from PDF outline destinations.

    This mode is intended for programming guides whose bookmarks contain one
    command heading per destination but whose table of contents is not in the
    dotted alphabetical-index format handled by :func:`extract_index`.
    """

    try:
        from pypdf import PdfReader
    except ImportError as exc:
        raise RuntimeError(
            "PDF extraction requires pypdf. Use the bundled document runtime "
            "or install pypdf for this developer-only tool."
        ) from exc

    reader = PdfReader(str(pdf_path))
    if not 1 <= start_page <= end_page <= len(reader.pages):
        raise ValueError(
            f"page range must be inside 1..{len(reader.pages)}"
        )

    rows: dict[str, tuple[int, int]] = {}

    def visit(items: list[Any]) -> None:
        for item in items:
            if isinstance(item, list):
                visit(item)
                continue
            try:
                pdf_page = reader.get_destination_page_number(item) + 1
            except Exception:
                continue
            if not start_page <= pdf_page <= end_page:
                continue
            header = _leading_scpi_header(getattr(item, "title", str(item)))
            if not header:
                continue
            rows.setdefault(
                header,
                (pdf_page + manual_page_offset, pdf_page),
            )

    visit(reader.outline)
    return [
        _conservative_candidate(
            profile_id=profile_id,
            command_pattern=command_pattern,
            manual_page=manual_page,
            source_pdf_page=pdf_page,
        )
        for command_pattern, (manual_page, pdf_page) in sorted(
            rows.items(),
            key=lambda item: (item[1][0], item[0]),
        )
    ]


def extract_command_lines(
    *,
    pdf_path: Path,
    profile_id: str,
    start_page: int,
    end_page: int,
    manual_page_offset: int = 0,
) -> list[dict[str, Any]]:
    """Extract literal SCPI headers from documented command syntax lines.

    This is a fallback for user guides that describe remote commands inline
    but explicitly defer the complete command set to a separate programmer's
    reference.  The result is therefore a documented subset, not an exhaustive
    command reference.
    """

    try:
        from pypdf import PdfReader
    except ImportError as exc:
        raise RuntimeError(
            "PDF extraction requires pypdf. Use the bundled document runtime "
            "or install pypdf for this developer-only tool."
        ) from exc

    reader = PdfReader(str(pdf_path))
    if not 1 <= start_page <= end_page <= len(reader.pages):
        raise ValueError(
            f"page range must be inside 1..{len(reader.pages)}"
        )

    rows: dict[str, tuple[int, int]] = {}
    for pdf_page in range(start_page, end_page + 1):
        text = reader.pages[pdf_page - 1].extract_text() or ""
        for line in text.splitlines():
            header = _leading_scpi_header(line)
            if not header:
                continue
            rows.setdefault(
                header,
                (pdf_page + manual_page_offset, pdf_page),
            )

    return [
        _conservative_candidate(
            profile_id=profile_id,
            command_pattern=command_pattern,
            manual_page=manual_page,
            source_pdf_page=pdf_page,
        )
        for command_pattern, (manual_page, pdf_page) in sorted(
            rows.items(),
            key=lambda item: (item[1][0], item[0]),
        )
    ]


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Extract the SCPI command index from an official PDF manual."
    )
    parser.add_argument("pdf", type=Path)
    parser.add_argument("output", type=Path)
    parser.add_argument("--profile-id", required=True)
    parser.add_argument("--manual-id", required=True)
    parser.add_argument("--title", required=True)
    parser.add_argument("--document-reference", required=True)
    parser.add_argument("--version", required=True)
    parser.add_argument("--firmware", default="")
    parser.add_argument("--source-url", required=True)
    parser.add_argument("--start-page", required=True, type=int)
    parser.add_argument("--end-page", required=True, type=int)
    parser.add_argument(
        "--extraction-mode",
        choices=("index", "outline", "command_lines"),
        default="index",
        help=(
            "Use outline for command-reference bookmarks or command_lines "
            "for inline Remote Interface Operation syntax."
        ),
    )
    parser.add_argument(
        "--manual-page-offset",
        type=int,
        default=0,
        help="Printed manual page minus physical PDF page.",
    )
    parser.add_argument(
        "--extraction-notes",
        default="",
        help="Manual-specific extraction limitation appended to the JSON.",
    )
    parser.add_argument(
        "--page-reference-mode",
        choices=("integer", "chapter_decimal"),
        default="integer",
        help=(
            "Use chapter_decimal for indexes whose references look like "
            "'6.174' instead of a physical PDF page."
        ),
    )
    parser.add_argument("--reference-chapter", type=int)
    parser.add_argument(
        "--reference-page-offset",
        type=int,
        default=0,
        help=(
            "Physical PDF page minus the page component in chapter_decimal "
            "references."
        ),
    )
    return parser


def _require_private_output(output: Path) -> None:
    """Prevent generated manual extracts from entering the source repository."""

    project_root = Path(__file__).resolve().parents[1]
    resolved = output.expanduser().resolve()
    if resolved == project_root or project_root in resolved.parents:
        raise ValueError(
            "Manual-derived command indexes are private local data and cannot "
            "be written inside this Git repository. Choose a path under "
            "%LOCALAPPDATA%\\SCPI-Automation-Platform\\manual_commands or "
            "another private folder."
        )


def main() -> int:
    args = build_parser().parse_args()
    _require_private_output(args.output)
    if args.extraction_mode == "index":
        commands = extract_index(
            pdf_path=args.pdf,
            profile_id=args.profile_id,
            start_page=args.start_page,
            end_page=args.end_page,
            page_reference_mode=args.page_reference_mode,
            reference_chapter=args.reference_chapter,
            reference_page_offset=args.reference_page_offset,
        )
        extraction_method = "official_manual_command_index"
    elif args.extraction_mode == "outline":
        commands = extract_outline_commands(
            pdf_path=args.pdf,
            profile_id=args.profile_id,
            start_page=args.start_page,
            end_page=args.end_page,
            manual_page_offset=args.manual_page_offset,
        )
        extraction_method = "official_manual_command_outline"
    else:
        commands = extract_command_lines(
            pdf_path=args.pdf,
            profile_id=args.profile_id,
            start_page=args.start_page,
            end_page=args.end_page,
            manual_page_offset=args.manual_page_offset,
        )
        extraction_method = "official_manual_documented_command_lines"

    notes = (
        "Command names and manual pages are candidates. Query probes and "
        "write support require live-instrument validation."
    )
    if args.extraction_notes.strip():
        notes += " " + args.extraction_notes.strip()
    payload = {
        "schema_version": 1,
        "profile_id": args.profile_id,
        "manual": {
            "manual_id": args.manual_id,
            "title": args.title,
            "document_reference": args.document_reference,
            "version": args.version,
            "firmware": args.firmware,
            "source_url": args.source_url,
            "index_pdf_pages": [args.start_page, args.end_page],
        },
        "extraction": {
            "method": extraction_method,
            "command_count": len(commands),
            "page_reference_mode": args.page_reference_mode,
            "notes": notes,
        },
        "commands": commands,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(f"wrote {len(commands)} commands to {args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
