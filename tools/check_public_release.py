from __future__ import annotations

import argparse
import csv
import hashlib
import json
import re
import sqlite3
import subprocess
import sys
import tomllib
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Sequence


PROJECT_ROOT = Path(__file__).resolve().parents[1]
CATALOG_ROOT = PROJECT_ROOT / "scpi_catalog_2026-07-25"

_REQUIRED_FILES = (
    "LICENSE",
    "THIRD_PARTY_NOTICES.md",
    "LICENSE_AUDIT.md",
    "CONTRIBUTING.md",
    "SECURITY.md",
    "docs/comics/ASSET_PROVENANCE.md",
    "packaging/windows/build-lock.json",
    "packaging/windows/inno-lock.json",
    "packaging/windows/requirements-build-win-py311.txt",
    "packaging/windows/scpi_automation.iss",
    "packaging/windows/scpi_automation.spec",
    "packaging/windows/README-KO.txt",
    "assets/scpi-automation-platform.ico",
    "assets/README.md",
    "docs/WINDOWS_INSTALL_KO.md",
    "packaging/licenses/Tcl-8.6-license.terms",
    "packaging/licenses/Tk-8.6-license.terms",
    "packaging/licenses/Inno-Setup-6.7.3-license.txt",
    ".github/workflows/release-windows.yml",
    "tools/build_windows_installer.py",
    "tools/build_windows_release.py",
    "tools/create_windows_release_assets.py",
    "tools/prepare_windows_release.py",
)
_FORBIDDEN_SUFFIXES = {
    ".7z",
    ".dll",
    ".doc",
    ".docx",
    ".exe",
    ".key",
    ".msi",
    ".p12",
    ".pdf",
    ".pem",
    ".pfx",
    ".ppt",
    ".pptx",
    ".zip",
}
_FORBIDDEN_PATH_PARTS = {
    "local_manual_cache",
    "manual_commands",
    "manuals_private",
    "tmp",
}
_FORBIDDEN_FILENAMES = {
    ".env",
}
_TEXT_SUFFIXES = {
    "",
    ".bat",
    ".csv",
    ".in",
    ".iss",
    ".json",
    ".md",
    ".ps1",
    ".py",
    ".spec",
    ".svg",
    ".toml",
    ".txt",
    ".yaml",
    ".yml",
}
_TEXT_PATTERNS = (
    (
        "personal Windows user path",
        re.compile(
            r"(?i)[A-Z]:[\\/](?:Users|Documents and Settings)[\\/]"
        ),
    ),
    (
        "absolute non-system drive path",
        re.compile(r"(?i)(?<![A-Za-z0-9])[D-Z]:[\\/]"),
    ),
    (
        "private key material",
        re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----"),
    ),
    (
        "GitHub token",
        re.compile(
            r"\b(?:gh[oprsu]_[A-Za-z0-9]{20,}|github_pat_[A-Za-z0-9_]{20,})\b"
        ),
    ),
    (
        "OpenAI API key",
        re.compile(r"\bsk-(?:proj-|svcacct-)?[A-Za-z0-9_-]{20,}\b"),
    ),
    ("Google API key", re.compile(r"\bAIza[0-9A-Za-z_-]{35}\b")),
    ("AWS access key", re.compile(r"\bAKIA[0-9A-Z]{16}\b")),
    ("Slack token", re.compile(r"\bxox[baprs]-[A-Za-z0-9-]{20,}\b")),
)
_GITHUB_NOREPLY_EMAIL = re.compile(
    r"(?i)^(?:\d+\+)?[A-Za-z0-9-]+@users\.noreply\.github\.com$"
)
_FORBIDDEN_MANUAL_FIELDS = {
    "command_pattern",
    "manual_page",
    "page_map",
    "query_scpi_candidate",
    "source_pdf_page",
}


@dataclass(frozen=True, slots=True)
class Violation:
    location: str
    reason: str

    def render(self) -> str:
        return f"{self.location}: {self.reason}"


def _run_git(*args: str, cwd: Path = PROJECT_ROOT) -> bytes:
    completed = subprocess.run(
        ["git", *args],
        cwd=cwd,
        check=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    return completed.stdout


def _candidate_paths(root: Path) -> tuple[Path, ...]:
    output = _run_git(
        "ls-files",
        "--cached",
        "--others",
        "--exclude-standard",
        "-z",
        cwd=root,
    )
    return tuple(
        root / item.decode("utf-8", errors="surrogateescape")
        for item in output.split(b"\0")
        if item
    )


def _untracked_python_sources(root: Path) -> tuple[Path, ...]:
    tracked = {
        item.decode("utf-8", errors="surrogateescape")
        for item in _run_git("ls-files", "-z", cwd=root).split(b"\0")
        if item
    }
    missing: list[Path] = []
    for directory in ("src", "tests", "tools"):
        source_root = root / directory
        if not source_root.is_dir():
            continue
        for path in source_root.rglob("*.py"):
            relative = path.relative_to(root)
            if "__pycache__" in relative.parts:
                continue
            if relative.as_posix() not in tracked:
                missing.append(relative)
    return tuple(sorted(missing))


def _path_violation(relative: Path) -> str | None:
    lowered_parts = {part.casefold() for part in relative.parts}
    forbidden = sorted(lowered_parts.intersection(_FORBIDDEN_PATH_PARTS))
    if forbidden:
        return f"forbidden private/generated directory {forbidden[0]!r}"
    if relative.name.casefold() in _FORBIDDEN_FILENAMES:
        return f"forbidden credential file {relative.name!r}"
    if relative.suffix.casefold() in _FORBIDDEN_SUFFIXES:
        return f"forbidden distributable file type {relative.suffix}"
    return None


def _scan_text(text: str) -> list[str]:
    return [
        label
        for label, pattern in _TEXT_PATTERNS
        if pattern.search(text)
    ]


def scan_working_tree(root: Path = PROJECT_ROOT) -> list[Violation]:
    violations: list[Violation] = []
    for required in _REQUIRED_FILES:
        if not (root / required).is_file():
            violations.append(Violation(required, "required file is missing"))
    icon_path = root / "assets" / "scpi-automation-platform.ico"
    if icon_path.is_file() and icon_path.read_bytes()[:4] != b"\x00\x00\x01\x00":
        violations.append(
            Violation(
                "assets/scpi-automation-platform.ico",
                "Windows icon header is invalid",
            )
        )
    for relative in _untracked_python_sources(root):
        violations.append(
            Violation(
                relative.as_posix(),
                "Python source exists locally but is not tracked by Git",
            )
        )
    for path in _candidate_paths(root):
        if not path.is_file():
            continue
        relative = path.relative_to(root)
        reason = _path_violation(relative)
        if reason:
            violations.append(Violation(relative.as_posix(), reason))
            continue
        if relative.suffix.casefold() not in _TEXT_SUFFIXES:
            continue
        if path.stat().st_size > 2 * 1024 * 1024:
            violations.append(
                Violation(relative.as_posix(), "text file exceeds 2 MiB audit limit")
            )
            continue
        text = path.read_text(encoding="utf-8", errors="replace")
        for match in _scan_text(text):
            violations.append(Violation(relative.as_posix(), match))
    return violations


def scan_reachable_history(root: Path = PROJECT_ROOT) -> list[Violation]:
    violations: list[Violation] = []
    commits = [
        item.decode("ascii")
        for item in _run_git("rev-list", "--all", cwd=root).splitlines()
        if item
    ]
    for commit in commits:
        short = commit[:12]
        identities = _run_git(
            "show",
            "-s",
            "--format=%ae%x00%ce",
            commit,
            cwd=root,
        ).decode("utf-8", errors="replace")
        for raw_email in identities.split("\0"):
            email = raw_email.strip()
            if email and not _GITHUB_NOREPLY_EMAIL.fullmatch(email):
                violations.append(
                    Violation(
                        f"commit {short}",
                        "commit identity is not a GitHub noreply address",
                    )
                )

        tree = _run_git(
            "ls-tree",
            "-r",
            "--name-only",
            "-z",
            commit,
            cwd=root,
        )
        for raw_path in tree.split(b"\0"):
            if not raw_path:
                continue
            path_text = raw_path.decode("utf-8", errors="surrogateescape")
            relative = Path(path_text)
            reason = _path_violation(relative)
            if reason:
                violations.append(
                    Violation(f"{short}:{path_text}", reason)
                )
                continue
            if relative.suffix.casefold() not in _TEXT_SUFFIXES:
                continue
            try:
                blob = _run_git("show", f"{commit}:{path_text}", cwd=root)
            except subprocess.CalledProcessError:
                violations.append(
                    Violation(f"{short}:{path_text}", "cannot read Git blob")
                )
                continue
            if len(blob) > 2 * 1024 * 1024:
                violations.append(
                    Violation(
                        f"{short}:{path_text}",
                        "historical text file exceeds 2 MiB audit limit",
                    )
                )
                continue
            text = blob.decode("utf-8", errors="replace")
            for match in _scan_text(text):
                violations.append(
                    Violation(f"{short}:{path_text}", match)
                )
    return violations


def _walk_json(value: object) -> Iterable[tuple[str, object]]:
    if isinstance(value, dict):
        for key, nested in value.items():
            yield str(key), nested
            yield from _walk_json(nested)
    elif isinstance(value, list):
        for nested in value:
            yield from _walk_json(nested)


def check_catalog(root: Path = PROJECT_ROOT) -> list[Violation]:
    catalog_root = root / "scpi_catalog_2026-07-25"
    violations: list[Violation] = []
    sources = json.loads(
        (catalog_root / "source_catalog.json").read_text(encoding="utf-8")
    )
    source_by_id = {str(source["source_id"]): source for source in sources}
    notices = (root / "THIRD_PARTY_NOTICES.md").read_text(encoding="utf-8")
    for source in sources:
        source_id = str(source.get("source_id", ""))
        if source.get("license") != "MIT" or not source.get(
            "license_verified"
        ):
            violations.append(
                Violation(
                    f"source_catalog:{source_id}",
                    "catalog source is not verified MIT",
                )
            )
        if not str(source.get("license_url", "")).startswith("https://"):
            violations.append(
                Violation(
                    f"source_catalog:{source_id}",
                    "license URL is missing or not HTTPS",
                )
            )
        copyright_line = str(source.get("copyright", "")).strip()
        if not copyright_line or copyright_line not in notices:
            violations.append(
                Violation(
                    f"source_catalog:{source_id}",
                    "copyright notice is missing from THIRD_PARTY_NOTICES.md",
                )
            )

    profile_paths = tuple(sorted((catalog_root / "profiles").glob("*.json")))
    if len(profile_paths) != 12:
        violations.append(
            Violation(
                "profiles",
                f"expected 12 profiles, found {len(profile_paths)}",
            )
        )
    split_profiles: list[dict[str, object]] = []
    for path in profile_paths:
        profile = json.loads(path.read_text(encoding="utf-8"))
        split_profiles.append(profile)
        if profile.get("manual_ids"):
            violations.append(
                Violation(path.name, "manual_ids must not be distributed")
            )
        for capability in profile.get("capabilities", ()):
            location = (
                f"{path.name}:{capability.get('capability_id', '<unknown>')}"
            )
            if capability.get("manual_references"):
                violations.append(
                    Violation(location, "manual references must not be distributed")
                )
            if str(capability.get("verification", "")).startswith("manual_"):
                violations.append(
                    Violation(location, "manual-only verification is forbidden")
                )
            source_ids = capability.get("source_ids", ())
            if not source_ids:
                violations.append(Violation(location, "source_ids is empty"))
            for source_id in source_ids:
                if str(source_id) not in source_by_id:
                    violations.append(
                        Violation(location, f"unknown source_id {source_id!r}")
                    )

    manual_catalog = json.loads(
        (catalog_root / "manual_catalog.json").read_text(encoding="utf-8")
    )
    for index, manual in enumerate(manual_catalog):
        location = f"manual_catalog[{index}]"
        url = str(manual.get("official_url", ""))
        if not url.startswith("https://") or url.casefold().endswith(".pdf"):
            violations.append(
                Violation(
                    location,
                    "manual bibliography must link to an HTTPS landing page",
                )
            )
        for key, _value in _walk_json(manual):
            if key in _FORBIDDEN_MANUAL_FIELDS:
                violations.append(
                    Violation(location, f"forbidden manual-derived field {key}")
                )

    unified = json.loads(
        (catalog_root / "scpi_catalog.json").read_text(encoding="utf-8")
    )
    if unified.get("sources") != sources:
        violations.append(
            Violation(
                "scpi_catalog.json",
                "embedded sources do not match source_catalog.json",
            )
        )
    if unified.get("manuals") != manual_catalog:
        violations.append(
            Violation(
                "scpi_catalog.json",
                "embedded manuals do not match manual_catalog.json",
            )
        )
    split_by_id = {
        str(profile.get("profile_id", "")): profile
        for profile in split_profiles
    }
    unified_by_id = {
        str(profile.get("profile_id", "")): profile
        for profile in unified.get("profiles", ())
    }
    if unified_by_id != split_by_id:
        violations.append(
            Violation(
                "scpi_catalog.json",
                "embedded profiles do not match split profiles",
            )
        )

    expected_operations = sum(
        len(capability.get("operations", {}))
        for profile in split_profiles
        for capability in profile.get("capabilities", ())
    )
    with (catalog_root / "command_bindings.csv").open(
        encoding="utf-8",
        newline="",
    ) as stream:
        csv_operations = sum(1 for _row in csv.DictReader(stream))
    if csv_operations != expected_operations:
        violations.append(
            Violation(
                "command_bindings.csv",
                f"expected {expected_operations} operations, found {csv_operations}",
            )
        )

    connection = sqlite3.connect(catalog_root / "scpi_catalog.sqlite")
    try:
        integrity = connection.execute("PRAGMA quick_check").fetchone()[0]
        sqlite_operations = connection.execute(
            "SELECT COUNT(*) FROM operations"
        ).fetchone()[0]
        profile_manuals = connection.execute(
            "SELECT COUNT(*) FROM profile_manuals"
        ).fetchone()[0]
        manual_verification = connection.execute(
            "SELECT COUNT(*) FROM capabilities "
            "WHERE verification LIKE 'manual_%'"
        ).fetchone()[0]
    finally:
        connection.close()
    if integrity != "ok":
        violations.append(
            Violation("scpi_catalog.sqlite", f"quick_check returned {integrity}")
        )
    if sqlite_operations != expected_operations:
        violations.append(
            Violation(
                "scpi_catalog.sqlite",
                (
                    f"expected {expected_operations} operations, "
                    f"found {sqlite_operations}"
                ),
            )
        )
    if profile_manuals:
        violations.append(
            Violation(
                "scpi_catalog.sqlite",
                f"profile_manuals must be empty, found {profile_manuals}",
            )
        )
    if manual_verification:
        violations.append(
            Violation(
                "scpi_catalog.sqlite",
                (
                    "manual-only capability verification must be empty, "
                    f"found {manual_verification}"
                ),
            )
        )
    return violations


def check_project_metadata(root: Path = PROJECT_ROOT) -> list[Violation]:
    violations: list[Violation] = []
    pyproject = tomllib.loads(
        (root / "pyproject.toml").read_text(encoding="utf-8")
    )
    project = pyproject.get("project", {})
    license_info = project.get("license", {})
    if license_info != {"file": "LICENSE"}:
        violations.append(
            Violation("pyproject.toml", "project license must reference LICENSE")
        )
    license_text = (root / "LICENSE").read_text(encoding="utf-8")
    if (
        "MIT License" not in license_text
        or "Copyright (c) 2026 obundh" not in license_text
        or "Permission is hereby granted" not in license_text
    ):
        violations.append(Violation("LICENSE", "expected MIT license is incomplete"))

    lock = json.loads(
        (root / "packaging/windows/build-lock.json").read_text(
            encoding="utf-8"
        )
    )
    requirements = (
        root / "packaging/windows/requirements-build-win-py311.txt"
    ).read_text(encoding="utf-8")
    for name, version in lock.get("distributions", {}).items():
        pattern = re.compile(
            rf"(?im)^{re.escape(str(name))}=={re.escape(str(version))}\s*\\"
        )
        if not pattern.search(requirements):
            violations.append(
                Violation(
                    "requirements-build-win-py311.txt",
                    f"missing locked distribution {name}=={version}",
                )
            )
    hash_count = requirements.count("--hash=sha256:")
    if hash_count != len(lock.get("distributions", {})):
        violations.append(
            Violation(
                "requirements-build-win-py311.txt",
                "every build distribution must have exactly one SHA-256 hash",
            )
        )
    return violations


def check_comic_provenance(root: Path = PROJECT_ROOT) -> list[Violation]:
    violations: list[Violation] = []
    provenance = (
        root / "docs/comics/ASSET_PROVENANCE.md"
    ).read_text(encoding="utf-8")
    for image in sorted((root / "docs/comics").glob("*.webp")):
        digest = hashlib.sha256(image.read_bytes()).hexdigest()
        if image.name not in provenance or digest not in provenance:
            violations.append(
                Violation(
                    image.relative_to(root).as_posix(),
                    "asset name or SHA-256 is missing from provenance",
                )
            )
    return violations


def run_checks(
    *,
    root: Path = PROJECT_ROOT,
    include_history: bool = True,
) -> list[Violation]:
    checks = [
        scan_working_tree(root),
        check_catalog(root),
        check_project_metadata(root),
        check_comic_provenance(root),
    ]
    if include_history:
        checks.append(scan_reachable_history(root))
    return [violation for group in checks for violation in group]


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Fail closed when a public source release is unsafe.",
    )
    parser.add_argument(
        "--skip-history",
        action="store_true",
        help="Check the working tree only (useful before the final history rewrite).",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        violations = run_checks(include_history=not args.skip_history)
    except (
        OSError,
        ValueError,
        json.JSONDecodeError,
        subprocess.CalledProcessError,
        tomllib.TOMLDecodeError,
    ) as exc:
        print(f"PUBLIC_RELEASE_BLOCKED: audit failed: {exc}", file=sys.stderr)
        return 2
    if violations:
        print("PUBLIC_RELEASE_BLOCKED", file=sys.stderr)
        for violation in violations:
            print(f"- {violation.render()}", file=sys.stderr)
        return 1
    scope = "tree+history" if not args.skip_history else "tree"
    print(f"PUBLIC_RELEASE_OK: {scope}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
