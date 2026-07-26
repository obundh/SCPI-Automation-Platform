from __future__ import annotations

import argparse
import hashlib
import json
import platform
import re
import shutil
import sys
import tkinter
import tomllib
from dataclasses import dataclass
from importlib import metadata
from pathlib import Path
from typing import Iterable, Mapping, Sequence


PROJECT_ROOT = Path(__file__).resolve().parents[1]
WINDOWS_PACKAGING = PROJECT_ROOT / "packaging" / "windows"
LICENSE_INPUTS = PROJECT_ROOT / "packaging" / "licenses"
LOCK_PATH = WINDOWS_PACKAGING / "build-lock.json"

_LICENSE_MARKERS = ("license", "copying", "notice", "authors")
_VENDOR_VISA_DLL = re.compile(
    r"(?i)(?:^|[\\/\"'])"
    r"(?:(?:ni|ag|kt|rs)?visa(?:32|64)?\.dll)"
    r"(?:$|[\\/\s\"',)])"
)
_FORBIDDEN_TOC_MARKERS = (
    "manual_commands",
    "local_manual_cache",
    "manuals_private",
)
_PYVISA_PY_TOC_ENTRY = re.compile(
    r"(?im)^\s*\('pyvisa_py(?:[.'\\/]|')"
)
_FORBIDDEN_RELEASE_SUFFIXES = {
    ".7z",
    ".doc",
    ".docx",
    ".dll",
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


class ReleasePackagingError(RuntimeError):
    """Raised when a candidate Windows release is not safe to publish."""


@dataclass(frozen=True, slots=True)
class LicenseSource:
    component: str
    version: str
    scope: str
    source: Path
    relative_name: Path


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _safe_component(value: str) -> str:
    normalized = re.sub(r"[^A-Za-z0-9._-]+", "-", value).strip("-")
    if not normalized:
        raise ReleasePackagingError("Empty component name in license inventory")
    return normalized


def load_build_lock(path: Path = LOCK_PATH) -> dict[str, object]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ReleasePackagingError(f"Cannot read build lock: {path}") from exc
    if payload.get("schema_version") != 1:
        raise ReleasePackagingError("Unsupported Windows build-lock schema")
    distributions = payload.get("distributions")
    if not isinstance(distributions, dict) or not distributions:
        raise ReleasePackagingError("Build lock has no distributions")
    return payload


def _project_version(project_root: Path) -> str:
    payload = tomllib.loads(
        (project_root / "pyproject.toml").read_text(encoding="utf-8")
    )
    try:
        return str(payload["project"]["version"])
    except (KeyError, TypeError) as exc:
        raise ReleasePackagingError(
            "pyproject.toml has no project version"
        ) from exc


def _distribution_license_sources(
    name: str,
    version: str,
    *,
    scope: str,
) -> tuple[LicenseSource, ...]:
    try:
        distribution = metadata.distribution(name)
    except metadata.PackageNotFoundError as exc:
        raise ReleasePackagingError(
            f"Required build distribution is not installed: {name}=={version}"
        ) from exc
    installed = distribution.version
    if installed != version:
        raise ReleasePackagingError(
            f"Build lock mismatch: {name}=={installed}, expected {version}"
        )
    candidates = tuple(
        entry
        for entry in (distribution.files or ())
        if any(marker in entry.name.lower() for marker in _LICENSE_MARKERS)
    )
    if not candidates:
        raise ReleasePackagingError(
            f"No license or notice file found in installed {name}=={version}"
        )

    sources: list[LicenseSource] = []
    for entry in candidates:
        source = Path(distribution.locate_file(entry)).resolve()
        if not source.is_file():
            raise ReleasePackagingError(
                f"Missing installed license file for {name}: {entry}"
            )
        sources.append(
            LicenseSource(
                component=name,
                version=version,
                scope=scope,
                source=source,
                relative_name=Path(*entry.parts),
            )
        )
    return tuple(sources)


def collect_build_environment_licenses(
    lock_path: Path = LOCK_PATH,
) -> tuple[tuple[LicenseSource, ...], dict[str, object]]:
    lock = load_build_lock(lock_path)
    expected_python = str(lock["python"])
    actual_python = platform.python_version()
    if actual_python != expected_python:
        raise ReleasePackagingError(
            f"CPython {actual_python} is active; the release lock requires "
            f"CPython {expected_python}"
        )
    if sys.platform != "win32":
        raise ReleasePackagingError("The Windows EXE must be built on Windows")

    expected_tcl = str(lock["tcl"])
    expected_tk = str(lock["tk"])
    actual_tcl = str(tkinter.TclVersion)
    actual_tk = str(tkinter.TkVersion)
    if (actual_tcl, actual_tk) != (expected_tcl, expected_tk):
        raise ReleasePackagingError(
            "Tcl/Tk build-lock mismatch: "
            f"found {actual_tcl}/{actual_tk}, "
            f"expected {expected_tcl}/{expected_tk}"
        )

    python_license = Path(sys.base_prefix) / "LICENSE.txt"
    if not python_license.is_file():
        raise ReleasePackagingError(
            f"CPython binary license was not found: {python_license}"
        )

    sources: list[LicenseSource] = [
        LicenseSource(
            component="CPython",
            version=actual_python,
            scope="runtime",
            source=python_license.resolve(),
            relative_name=Path("LICENSE.txt"),
        ),
        LicenseSource(
            component="Tcl",
            version=actual_tcl,
            scope="runtime",
            source=(LICENSE_INPUTS / "Tcl-8.6-license.terms").resolve(),
            relative_name=Path("license.terms"),
        ),
        LicenseSource(
            component="Tk",
            version=actual_tk,
            scope="runtime",
            source=(LICENSE_INPUTS / "Tk-8.6-license.terms").resolve(),
            relative_name=Path("license.terms"),
        ),
    ]
    for source in sources:
        if not source.source.is_file():
            raise ReleasePackagingError(
                f"Required runtime license is missing: {source.source}"
            )

    runtime_names = {
        str(item).casefold()
        for item in lock.get("runtime_distributions", ())
    }
    distributions = lock["distributions"]
    assert isinstance(distributions, dict)
    for name, version in distributions.items():
        scope = (
            "runtime"
            if str(name).casefold() in runtime_names
            else "build"
        )
        sources.extend(
            _distribution_license_sources(
                str(name),
                str(version),
                scope=scope,
            )
        )
    return tuple(sources), lock


def audit_pyinstaller_tocs(toc_paths: Iterable[Path]) -> dict[str, str]:
    paths = tuple(sorted({Path(path).resolve() for path in toc_paths}))
    if not paths:
        raise ReleasePackagingError(
            "No PyInstaller TOC was supplied; bundled files cannot be audited"
        )
    evidence: dict[str, str] = {}
    for path in paths:
        if not path.is_file():
            raise ReleasePackagingError(f"PyInstaller TOC is missing: {path}")
        text = path.read_text(encoding="utf-8", errors="replace")
        lowered = text.casefold()
        if _VENDOR_VISA_DLL.search(text):
            raise ReleasePackagingError(
                f"Manufacturer VISA DLL appears in PyInstaller TOC: {path.name}"
            )
        if _PYVISA_PY_TOC_ENTRY.search(text):
            raise ReleasePackagingError(
                f"Unaudited pyvisa-py module appears in {path.name}"
            )
        for marker in _FORBIDDEN_TOC_MARKERS:
            if marker in lowered:
                raise ReleasePackagingError(
                    f"Forbidden bundled input {marker!r} appears in {path.name}"
                )
        for line in text.splitlines():
            suffix = Path(line.strip(" '\"(),")).suffix.casefold()
            if suffix in {".pdf", ".msi", ".pfx", ".p12", ".key", ".pem"}:
                raise ReleasePackagingError(
                    f"Forbidden bundled file appears in {path.name}: {line}"
                )
        evidence[path.name] = _sha256(path)
    return evidence


def _copy_license_sources(
    sources: Sequence[LicenseSource],
    destination: Path,
) -> list[dict[str, object]]:
    grouped: dict[tuple[str, str, str], list[LicenseSource]] = {}
    for source in sources:
        grouped.setdefault(
            (source.component, source.version, source.scope),
            [],
        ).append(source)

    inventory: list[dict[str, object]] = []
    for (component, version, scope), items in sorted(grouped.items()):
        component_dir = destination / (
            f"{_safe_component(component)}-{_safe_component(version)}"
        )
        copied: list[str] = []
        for item in sorted(items, key=lambda value: value.relative_name.as_posix()):
            relative = item.relative_name
            if relative.is_absolute() or ".." in relative.parts:
                raise ReleasePackagingError(
                    f"Unsafe installed license path: {relative}"
                )
            target = component_dir / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(item.source, target)
            copied.append(target.relative_to(destination.parent).as_posix())
        inventory.append(
            {
                "name": component,
                "version": version,
                "scope": scope,
                "license_files": copied,
            }
        )
    return inventory


def _audit_release_tree(root: Path, expected_exe: str) -> None:
    executable_count = 0
    for path in root.rglob("*"):
        if not path.is_file():
            continue
        relative = path.relative_to(root)
        lowered_parts = {part.casefold() for part in relative.parts}
        if lowered_parts.intersection(_FORBIDDEN_TOC_MARKERS):
            raise ReleasePackagingError(
                f"Forbidden directory in release: {relative.as_posix()}"
            )
        if path.suffix.casefold() == ".exe":
            executable_count += 1
            if relative.as_posix() != expected_exe:
                raise ReleasePackagingError(
                    f"Unexpected executable in release: {relative.as_posix()}"
                )
            continue
        if path.suffix.casefold() in _FORBIDDEN_RELEASE_SUFFIXES:
            raise ReleasePackagingError(
                f"Forbidden file in release: {relative.as_posix()}"
            )
    if executable_count != 1:
        raise ReleasePackagingError(
            f"Release must contain exactly one EXE, found {executable_count}"
        )


def _write_checksums(root: Path) -> Path:
    manifest = root / "SHA256SUMS.txt"
    lines = [
        f"{_sha256(path)}  {path.relative_to(root).as_posix()}"
        for path in sorted(root.rglob("*"))
        if path.is_file() and path != manifest
    ]
    manifest.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return manifest


def prepare_release(
    *,
    exe_path: Path,
    output_dir: Path,
    toc_paths: Sequence[Path],
    license_sources: Sequence[LicenseSource],
    lock: Mapping[str, object],
    project_root: Path = PROJECT_ROOT,
) -> Path:
    exe_path = exe_path.resolve()
    output_dir = output_dir.resolve()
    project_root = project_root.resolve()
    if not exe_path.is_file() or exe_path.suffix.casefold() != ".exe":
        raise ReleasePackagingError(f"Windows EXE was not found: {exe_path}")
    if output_dir.exists():
        raise ReleasePackagingError(
            f"Output directory must not already exist: {output_dir}"
        )

    toc_evidence = audit_pyinstaller_tocs(toc_paths)
    required = {
        "LICENSE.txt": project_root / "LICENSE",
        "THIRD_PARTY_NOTICES.md": project_root / "THIRD_PARTY_NOTICES.md",
        "README-KO.txt": WINDOWS_PACKAGING / "README-KO.txt",
    }
    for source in required.values():
        if not source.is_file():
            raise ReleasePackagingError(
                f"Required release document is missing: {source}"
            )

    output_dir.mkdir(parents=True)
    executable_name = "SCPI-Automation-Platform.exe"
    shutil.copy2(exe_path, output_dir / executable_name)
    for target_name, source in required.items():
        shutil.copy2(source, output_dir / target_name)

    inventory = _copy_license_sources(
        license_sources,
        output_dir / "LICENSES",
    )
    inventory_payload = {
        "schema_version": 1,
        "application": "SCPI Automation Platform",
        "application_version": _project_version(project_root),
        "target": lock.get("target", "Windows"),
        "components": inventory,
        "external_not_bundled": list(lock.get("external_components", ())),
        "pyinstaller_toc_sha256": toc_evidence,
        "manufacturer_visa_runtime_bundled": False,
        "manufacturer_manual_content_bundled": False,
    }
    (output_dir / "THIRD_PARTY_INVENTORY.json").write_text(
        json.dumps(inventory_payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    _audit_release_tree(output_dir, executable_name)
    _write_checksums(output_dir)
    return output_dir


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Create an allowlisted Windows release folder.",
    )
    parser.add_argument("--exe", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument(
        "--toc",
        required=True,
        type=Path,
        action="append",
        help="PyInstaller TOC file; repeat for every generated TOC.",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        sources, lock = collect_build_environment_licenses()
        release = prepare_release(
            exe_path=args.exe,
            output_dir=args.output,
            toc_paths=args.toc,
            license_sources=sources,
            lock=lock,
        )
    except ReleasePackagingError as exc:
        print(f"RELEASE_BLOCKED: {exc}", file=sys.stderr)
        return 2
    print(f"RELEASE_READY: {release}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
