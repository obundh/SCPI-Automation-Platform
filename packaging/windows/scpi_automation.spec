# -*- mode: python ; coding: utf-8 -*-

from pathlib import Path


project_root = Path(SPECPATH).resolve().parents[1]
catalog_file = (
    project_root
    / "scpi_catalog_2026-07-25"
    / "scpi_catalog.json"
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
