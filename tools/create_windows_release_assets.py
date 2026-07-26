from __future__ import annotations

import argparse
import hashlib
import stat
import zipfile
from pathlib import Path
from typing import Sequence

try:
    from tools.build_windows_installer import (
        project_version,
        release_version_label,
        verify_payload,
    )
    from tools.prepare_windows_release import ReleasePackagingError
except ModuleNotFoundError:
    from build_windows_installer import (
        project_version,
        release_version_label,
        verify_payload,
    )
    from prepare_windows_release import ReleasePackagingError


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _write_reproducible_file(
    archive: zipfile.ZipFile,
    *,
    source: Path,
    archive_name: str,
) -> None:
    info = zipfile.ZipInfo(archive_name, date_time=(2026, 1, 1, 0, 0, 0))
    info.compress_type = zipfile.ZIP_DEFLATED
    info.create_system = 3
    info.external_attr = (stat.S_IFREG | 0o644) << 16
    archive.writestr(info, source.read_bytes(), compresslevel=9)


def create_release_assets(
    *,
    payload_dir: Path,
    installer: Path,
    output_dir: Path,
) -> tuple[Path, Path]:
    payload_dir = payload_dir.resolve()
    installer = installer.resolve()
    output_dir = output_dir.resolve()
    verify_payload(payload_dir)
    if not installer.is_file() or installer.suffix.casefold() != ".exe":
        raise ReleasePackagingError(f"Installer is missing: {installer}")
    try:
        installer.relative_to(output_dir)
    except ValueError as exc:
        raise ReleasePackagingError(
            "Installer must already be inside the asset output directory"
        ) from exc

    label = release_version_label(project_version())
    portable = output_dir / (
        f"SCPI-Automation-Platform-Portable-{label}-win64.zip"
    )
    checksums = output_dir / "SHA256SUMS-Windows.txt"
    for target in (portable, checksums):
        if target.exists():
            raise ReleasePackagingError(
                f"Release asset must not be overwritten: {target}"
            )

    root_name = f"SCPI-Automation-Platform-{label}-win64"
    with zipfile.ZipFile(portable, "x") as archive:
        for source in sorted(
            (path for path in payload_dir.rglob("*") if path.is_file()),
            key=lambda path: path.relative_to(payload_dir).as_posix(),
        ):
            relative = source.relative_to(payload_dir).as_posix()
            _write_reproducible_file(
                archive,
                source=source,
                archive_name=f"{root_name}/{relative}",
            )

    asset_lines = [
        f"{_sha256(path)}  {path.name}"
        for path in sorted((installer, portable), key=lambda item: item.name)
    ]
    checksums.write_text("\n".join(asset_lines) + "\n", encoding="utf-8")
    return portable, checksums


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Create the portable ZIP and public SHA-256 manifest.",
    )
    parser.add_argument("--payload", required=True, type=Path)
    parser.add_argument("--installer", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        portable, checksums = create_release_assets(
            payload_dir=args.payload,
            installer=args.installer,
            output_dir=args.output,
        )
    except (OSError, ReleasePackagingError, zipfile.BadZipFile) as exc:
        print(f"RELEASE_ASSETS_BLOCKED: {exc}")
        return 2
    print(f"PORTABLE_READY: {portable}")
    print(f"CHECKSUMS_READY: {checksums}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
