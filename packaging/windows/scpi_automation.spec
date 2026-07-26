# -*- mode: python ; coding: utf-8 -*-

import re
import tomllib
from pathlib import Path

from PyInstaller.utils.win32.versioninfo import (
    FixedFileInfo,
    StringFileInfo,
    StringStruct,
    StringTable,
    VarFileInfo,
    VarStruct,
    VSVersionInfo,
)


project_root = Path(SPECPATH).resolve().parents[1]
catalog_file = (
    project_root
    / "scpi_catalog_2026-07-25"
    / "scpi_catalog.json"
)
icon_file = project_root / "assets" / "scpi-automation-platform.ico"
project_metadata = tomllib.loads(
    (project_root / "pyproject.toml").read_text(encoding="utf-8")
)["project"]
project_version = str(project_metadata["version"])
version_match = re.fullmatch(
    r"(?P<major>\d+)\.(?P<minor>\d+)\.(?P<patch>\d+)"
    r"(?:\.dev(?P<build>\d+))?",
    project_version,
)
if version_match is None:
    raise ValueError(f"Unsupported Windows version format: {project_version}")
file_version = (
    int(version_match.group("major")),
    int(version_match.group("minor")),
    int(version_match.group("patch")),
    int(version_match.group("build") or 0),
)
version_info = VSVersionInfo(
    ffi=FixedFileInfo(
        filevers=file_version,
        prodvers=file_version,
        mask=0x3F,
        flags=0x0,
        OS=0x40004,
        fileType=0x1,
        subtype=0x0,
        date=(0, 0),
    ),
    kids=[
        StringFileInfo(
            [
                StringTable(
                    "040904B0",
                    [
                        StringStruct(
                            "CompanyName",
                            "SCPI Automation Platform contributors",
                        ),
                        StringStruct(
                            "FileDescription",
                            "SCPI 계측기 자동화 플랫폼",
                        ),
                        StringStruct("FileVersion", project_version),
                        StringStruct(
                            "InternalName",
                            "SCPI-Automation-Platform",
                        ),
                        StringStruct(
                            "LegalCopyright",
                            "Copyright (c) 2026 SCPI Automation Platform contributors",
                        ),
                        StringStruct(
                            "OriginalFilename",
                            "SCPI-Automation-Platform.exe",
                        ),
                        StringStruct(
                            "ProductName",
                            "SCPI Automation Platform",
                        ),
                        StringStruct("ProductVersion", project_version),
                    ],
                )
            ]
        ),
        VarFileInfo([VarStruct("Translation", [1033, 1200])]),
    ],
)

analysis = Analysis(
    [str(project_root / "run_app.py")],
    pathex=[str(project_root / "src")],
    binaries=[],
    datas=[
        (
            str(catalog_file),
            "scpi_catalog_2026-07-25",
        ),
        (
            str(icon_file),
            "assets",
        ),
    ],
    hiddenimports=[],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=["pyvisa_py"],
    noarchive=False,
    optimize=0,
)
pyz = PYZ(analysis.pure)

exe = EXE(
    pyz,
    analysis.scripts,
    analysis.binaries,
    analysis.datas,
    [],
    name="SCPI-Automation-Platform",
    icon=str(icon_file),
    version=version_info,
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)
