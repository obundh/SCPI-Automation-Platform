from __future__ import annotations

import hashlib
import json
import tempfile
import unittest
from pathlib import Path

from tools.prepare_windows_release import (
    PROJECT_ROOT,
    LicenseSource,
    ReleasePackagingError,
    audit_pyinstaller_tocs,
    prepare_release,
)


class WindowsReleasePackagingTests(unittest.TestCase):
    def test_release_contains_only_allowlisted_payload_and_valid_hashes(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            exe = root / "candidate.exe"
            exe.write_bytes(b"MZ test executable")
            toc = root / "Analysis-00.toc"
            toc.write_text(
                "('scpi_catalog_2026-07-25/scpi_catalog.json', 'DATA')\n"
                "('pyvisa/ctwrapper/functions.py', 'PYMODULE')\n",
                encoding="utf-8",
            )
            component_license = root / "dependency-LICENSE.txt"
            component_license.write_text(
                "Permission is hereby granted.\n",
                encoding="utf-8",
            )
            output = root / "release"
            release = prepare_release(
                exe_path=exe,
                output_dir=output,
                toc_paths=(toc,),
                license_sources=(
                    LicenseSource(
                        component="Example Dependency",
                        version="1.2.3",
                        scope="runtime",
                        source=component_license,
                        relative_name=Path("licenses/LICENSE.txt"),
                    ),
                ),
                lock={
                    "target": "Windows x86-64",
                    "external_components": ["Vendor VISA runtime"],
                },
            )

            self.assertEqual(release, output.resolve())
            self.assertEqual(
                {
                    path.relative_to(release).as_posix()
                    for path in release.rglob("*")
                    if path.is_file()
                },
                {
                    "LICENSE.txt",
                    "README-KO.txt",
                    "SCPI-Automation-Platform.exe",
                    "SHA256SUMS.txt",
                    "THIRD_PARTY_INVENTORY.json",
                    "THIRD_PARTY_NOTICES.md",
                    (
                        "LICENSES/Example-Dependency-1.2.3/"
                        "licenses/LICENSE.txt"
                    ),
                },
            )
            inventory = json.loads(
                (release / "THIRD_PARTY_INVENTORY.json").read_text(
                    encoding="utf-8"
                )
            )
            self.assertFalse(inventory["manufacturer_visa_runtime_bundled"])
            self.assertFalse(
                inventory["manufacturer_manual_content_bundled"]
            )
            self.assertEqual(
                inventory["components"][0]["name"],
                "Example Dependency",
            )
            for line in (release / "SHA256SUMS.txt").read_text(
                encoding="utf-8"
            ).splitlines():
                expected, relative = line.split("  ", 1)
                self.assertEqual(
                    hashlib.sha256((release / relative).read_bytes()).hexdigest(),
                    expected,
                )

    def test_vendor_visa_dll_in_toc_blocks_release(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            toc = Path(temporary) / "Analysis-00.toc"
            toc.write_text(
                r"('C:\Program Files\IVI Foundation\VISA\Win64\Bin\nivisa64.dll', 'BINARY')",
                encoding="utf-8",
            )
            with self.assertRaisesRegex(
                ReleasePackagingError,
                "Manufacturer VISA DLL",
            ):
                audit_pyinstaller_tocs((toc,))

    def test_manual_extract_in_toc_blocks_release(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            toc = Path(temporary) / "Analysis-00.toc"
            toc.write_text(
                "('manual_commands/fsv30.json', 'DATA')",
                encoding="utf-8",
            )
            with self.assertRaisesRegex(
                ReleasePackagingError,
                "manual_commands",
            ):
                audit_pyinstaller_tocs((toc,))

    def test_existing_output_is_never_overwritten(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            exe = root / "candidate.exe"
            exe.write_bytes(b"MZ")
            toc = root / "Analysis-00.toc"
            toc.write_text("('app.py', 'PYMODULE')", encoding="utf-8")
            license_path = root / "LICENSE"
            license_path.write_text("license", encoding="utf-8")
            output = root / "release"
            output.mkdir()
            marker = output / "keep.txt"
            marker.write_text("keep", encoding="utf-8")
            with self.assertRaisesRegex(
                ReleasePackagingError,
                "must not already exist",
            ):
                prepare_release(
                    exe_path=exe,
                    output_dir=output,
                    toc_paths=(toc,),
                    license_sources=(
                        LicenseSource(
                            component="Example",
                            version="1",
                            scope="runtime",
                            source=license_path,
                            relative_name=Path("LICENSE"),
                        ),
                    ),
                    lock={},
                    project_root=PROJECT_ROOT,
                )
            self.assertEqual(marker.read_text(encoding="utf-8"), "keep")


if __name__ == "__main__":
    unittest.main()
