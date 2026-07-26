from __future__ import annotations

import argparse
import hashlib
import json
import re
import subprocess
import tempfile
import tomllib
from pathlib import Path
from typing import Sequence

try:
    from tools.prepare_windows_release import (
        PROJECT_ROOT,
        WINDOWS_PACKAGING,
        ReleasePackagingError,
    )
except ModuleNotFoundError:
    from prepare_windows_release import (
        PROJECT_ROOT,
        WINDOWS_PACKAGING,
        ReleasePackagingError,
    )


INNO_LOCK_PATH = WINDOWS_PACKAGING / "inno-lock.json"
INSTALLER_SCRIPT = WINDOWS_PACKAGING / "scpi_automation.iss"
REQUIRED_PAYLOAD_FILES = {
    "LICENSE.txt",
    "README-KO.txt",
    "SCPI-Automation-Platform.exe",
    "SHA256SUMS.txt",
    "THIRD_PARTY_INVENTORY.json",
    "THIRD_PARTY_NOTICES.md",
}


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def load_inno_lock(path: Path = INNO_LOCK_PATH) -> dict[str, object]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ReleasePackagingError(f"Cannot read Inno Setup lock: {path}") from exc
    if payload.get("schema_version") != 1:
        raise ReleasePackagingError("Unsupported Inno Setup lock schema")
    for field in (
        "version",
        "download_url",
        "sha256",
        "iscc_sha256",
        "license_url",
    ):
        if not str(payload.get(field, "")).strip():
            raise ReleasePackagingError(
                f"Inno Setup lock is missing required field: {field}"
            )
    return payload


def project_version(project_root: Path = PROJECT_ROOT) -> str:
    payload = tomllib.loads(
        (project_root / "pyproject.toml").read_text(encoding="utf-8")
    )
    return str(payload["project"]["version"])


def release_version_label(version: str) -> str:
    match = re.fullmatch(
        r"(?P<core>\d+\.\d+\.\d+)(?:\.dev(?P<dev>\d+))?",
        version,
    )
    if match is None:
        raise ReleasePackagingError(
            f"Unsupported release version format: {version}"
        )
    if match.group("dev") is None:
        return match.group("core")
    return f"{match.group('core')}-dev.{match.group('dev')}"


def verify_payload(payload_dir: Path) -> dict[str, str]:
    payload_dir = payload_dir.resolve()
    if not payload_dir.is_dir():
        raise ReleasePackagingError(
            f"Windows release payload is missing: {payload_dir}"
        )
    missing = sorted(
        name for name in REQUIRED_PAYLOAD_FILES if not (payload_dir / name).is_file()
    )
    if missing:
        raise ReleasePackagingError(
            "Windows release payload is incomplete: " + ", ".join(missing)
        )

    manifest = payload_dir / "SHA256SUMS.txt"
    verified: dict[str, str] = {}
    for line_number, raw_line in enumerate(
        manifest.read_text(encoding="utf-8").splitlines(),
        start=1,
    ):
        if not raw_line.strip():
            continue
        try:
            expected, relative_text = raw_line.split("  ", 1)
        except ValueError as exc:
            raise ReleasePackagingError(
                f"Malformed payload checksum at line {line_number}"
            ) from exc
        relative = Path(relative_text)
        if relative.is_absolute() or ".." in relative.parts:
            raise ReleasePackagingError(
                f"Unsafe payload checksum path: {relative_text}"
            )
        target = (payload_dir / relative).resolve()
        try:
            target.relative_to(payload_dir)
        except ValueError as exc:
            raise ReleasePackagingError(
                f"Payload checksum escapes the release folder: {relative_text}"
            ) from exc
        if not target.is_file():
            raise ReleasePackagingError(
                f"Payload checksum target is missing: {relative_text}"
            )
        actual = _sha256(target)
        if actual.casefold() != expected.casefold():
            raise ReleasePackagingError(
                f"Payload checksum mismatch: {relative_text}"
            )
        verified[relative.as_posix()] = actual

    expected_files = {
        path.relative_to(payload_dir).as_posix()
        for path in payload_dir.rglob("*")
        if path.is_file() and path != manifest
    }
    if set(verified) != expected_files:
        missing_from_manifest = sorted(expected_files - set(verified))
        extra_in_manifest = sorted(set(verified) - expected_files)
        details = []
        if missing_from_manifest:
            details.append("missing " + ", ".join(missing_from_manifest))
        if extra_in_manifest:
            details.append("extra " + ", ".join(extra_in_manifest))
        raise ReleasePackagingError(
            "Payload checksum inventory is incomplete: " + "; ".join(details)
        )

    inventory = json.loads(
        (payload_dir / "THIRD_PARTY_INVENTORY.json").read_text(encoding="utf-8")
    )
    if inventory.get("manufacturer_visa_runtime_bundled") is not False:
        raise ReleasePackagingError(
            "The installer payload claims to bundle a manufacturer VISA runtime"
        )
    if inventory.get("manufacturer_manual_content_bundled") is not False:
        raise ReleasePackagingError(
            "The installer payload claims to bundle manufacturer manual content"
        )
    return verified


def verify_iscc(iscc_path: Path, expected_sha256: str) -> None:
    iscc_path = iscc_path.resolve()
    if not iscc_path.is_file():
        raise ReleasePackagingError(f"ISCC.exe is missing: {iscc_path}")
    actual_sha256 = _sha256(iscc_path)
    if actual_sha256.casefold() != expected_sha256.casefold():
        raise ReleasePackagingError(
            "ISCC.exe SHA-256 does not match the locked compiler"
        )


def _smoke_test_installer(installer: Path) -> None:
    with tempfile.TemporaryDirectory(prefix="scpi-installer-smoke-") as temporary:
        install_dir = Path(temporary) / "installed"
        install_command = [
            str(installer),
            "/VERYSILENT",
            "/SUPPRESSMSGBOXES",
            "/NORESTART",
            "/NOICONS",
            f"/DIR={install_dir}",
        ]
        subprocess.run(install_command, check=True, timeout=180)
        executable = install_dir / "SCPI-Automation-Platform.exe"
        if not executable.is_file():
            raise ReleasePackagingError(
                "The silent installer did not create the application EXE"
            )
        inno_license = (
            install_dir
            / "LICENSES"
            / "Inno-Setup-6.7.3"
            / "Inno-Setup-6.7.3-license.txt"
        )
        if not inno_license.is_file():
            raise ReleasePackagingError(
                "The installed application is missing the Inno Setup notice"
            )
        try:
            subprocess.run(
                [str(executable), "--release-self-check"],
                cwd=install_dir,
                check=True,
                timeout=180,
            )
        finally:
            uninstallers = tuple(sorted(install_dir.glob("unins*.exe")))
            if uninstallers:
                subprocess.run(
                    [
                        str(uninstallers[0]),
                        "/VERYSILENT",
                        "/SUPPRESSMSGBOXES",
                        "/NORESTART",
                    ],
                    check=False,
                    timeout=180,
                )


def build_installer(
    *,
    payload_dir: Path,
    output_dir: Path,
    iscc_path: Path,
    smoke_test: bool,
) -> Path:
    payload_dir = payload_dir.resolve()
    output_dir = output_dir.resolve()
    if output_dir.exists():
        raise ReleasePackagingError(
            f"Installer output directory must not already exist: {output_dir}"
        )
    verify_payload(payload_dir)
    lock = load_inno_lock()
    verify_iscc(iscc_path, str(lock["iscc_sha256"]))
    if not INSTALLER_SCRIPT.is_file():
        raise ReleasePackagingError(
            f"Inno Setup script is missing: {INSTALLER_SCRIPT}"
        )

    version = project_version()
    label = release_version_label(version)
    base_name = f"SCPI-Automation-Platform-Setup-{label}-win64"
    output_dir.mkdir(parents=True)
    command = [
        str(iscc_path.resolve()),
        "/Qp",
        f"/DAppVersion={version}",
        f"/DPayloadDir={payload_dir}",
        f"/DOutputDir={output_dir}",
        f"/DOutputBaseFilename={base_name}",
        str(INSTALLER_SCRIPT),
    ]
    subprocess.run(command, cwd=WINDOWS_PACKAGING, check=True, timeout=180)
    installer = output_dir / f"{base_name}.exe"
    if not installer.is_file() or installer.read_bytes()[:2] != b"MZ":
        raise ReleasePackagingError(
            f"Inno Setup did not create the expected installer: {installer}"
        )
    if smoke_test:
        _smoke_test_installer(installer)
    return installer


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Compile and optionally silent-install-test the beginner Windows setup."
        ),
    )
    parser.add_argument("--payload", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--iscc", required=True, type=Path)
    parser.add_argument(
        "--smoke-test",
        action="store_true",
        help="Install to a temporary user folder, self-check, and uninstall.",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        installer = build_installer(
            payload_dir=args.payload,
            output_dir=args.output,
            iscc_path=args.iscc,
            smoke_test=args.smoke_test,
        )
    except (
        OSError,
        ReleasePackagingError,
        subprocess.CalledProcessError,
        subprocess.TimeoutExpired,
    ) as exc:
        print(f"INSTALLER_BUILD_BLOCKED: {exc}")
        return 2
    print(f"INSTALLER_BUILD_READY: {installer}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
