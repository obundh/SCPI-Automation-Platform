from __future__ import annotations

import argparse
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Sequence

from prepare_windows_release import (
    PROJECT_ROOT,
    WINDOWS_PACKAGING,
    ReleasePackagingError,
    collect_build_environment_licenses,
    prepare_release,
)


def build_release(output_dir: Path) -> Path:
    sources, lock = collect_build_environment_licenses()
    spec = WINDOWS_PACKAGING / "scpi_automation.spec"
    if not spec.is_file():
        raise ReleasePackagingError(f"PyInstaller spec is missing: {spec}")

    with tempfile.TemporaryDirectory(prefix="scpi-release-build-") as temporary:
        temporary_root = Path(temporary)
        work_dir = temporary_root / "build"
        dist_dir = temporary_root / "dist"
        command = [
            sys.executable,
            "-m",
            "PyInstaller",
            "--clean",
            "--noconfirm",
            "--workpath",
            str(work_dir),
            "--distpath",
            str(dist_dir),
            str(spec),
        ]
        subprocess.run(command, cwd=PROJECT_ROOT, check=True)
        executable = dist_dir / "SCPI-Automation-Platform.exe"
        if not executable.is_file():
            raise ReleasePackagingError(
                f"PyInstaller did not create the expected EXE: {executable}"
            )
        subprocess.run(
            [str(executable), "--smoke-test"],
            cwd=dist_dir,
            check=True,
            timeout=120,
        )
        toc_paths = tuple(sorted(work_dir.rglob("*.toc")))
        return prepare_release(
            exe_path=executable,
            output_dir=output_dir,
            toc_paths=toc_paths,
            license_sources=sources,
            lock=lock,
        )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Build, smoke-test, audit, and package the Windows EXE.",
    )
    parser.add_argument("--output", required=True, type=Path)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        release = build_release(args.output)
    except (
        OSError,
        ReleasePackagingError,
        subprocess.CalledProcessError,
        subprocess.TimeoutExpired,
    ) as exc:
        print(f"RELEASE_BUILD_BLOCKED: {exc}", file=sys.stderr)
        return 2
    print(f"RELEASE_BUILD_READY: {release}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
