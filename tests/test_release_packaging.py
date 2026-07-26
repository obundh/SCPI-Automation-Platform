from __future__ import annotations

import hashlib
import json
import tempfile
import unittest
import zipfile
from pathlib import Path

from scpi_automation import __version__
from tools.build_windows_installer import (
    project_version,
    release_version_label,
    verify_payload,
)
from tools.create_windows_release_assets import create_release_assets
from tools.prepare_windows_release import (
    PROJECT_ROOT,
    LicenseSource,
    ReleasePackagingError,
    audit_pyinstaller_tocs,
    prepare_release,
)


class WindowsReleasePackagingTests(unittest.TestCase):
    def test_project_versions_and_release_label_match(self) -> None:
        self.assertEqual(project_version(), __version__)
        self.assertEqual(
            release_version_label(project_version()),
            "0.1.0-dev.1",
        )

    def test_release_workflow_is_fail_closed_and_least_privilege(self) -> None:
        workflow = (
            PROJECT_ROOT / ".github" / "workflows" / "release-windows.yml"
        ).read_text(encoding="utf-8")

        self.assertIn("permissions: {}", workflow)
        self.assertIn("persist-credentials: false", workflow)
        self.assertIn("git merge-base --is-ancestor", workflow)
        self.assertIn("contents: read", workflow)
        self.assertIn("contents: write", workflow)
        self.assertIn("--draft", workflow)
        self.assertIn("gh release edit", workflow)
        self.assertIn("--draft=false", workflow)
        self.assertIn("GH_REPO: ${{ github.repository }}", workflow)
        self.assertIn("Verify the exact public asset set", workflow)
        self.assertIn("Verify Windows executable metadata and icon", workflow)
        self.assertNotIn("uses: actions/checkout@v4", workflow)
        self.assertNotIn("uses: actions/setup-python@v5", workflow)

    def test_ci_audits_the_pr_source_commit_not_a_synthetic_merge(self) -> None:
        workflow = (
            PROJECT_ROOT / ".github" / "workflows" / "ci.yml"
        ).read_text(encoding="utf-8")

        self.assertIn(
            "ref: ${{ github.event.pull_request.head.sha || github.sha }}",
            workflow,
        )
        self.assertIn("persist-credentials: false", workflow)
        self.assertNotIn("uses: actions/checkout@v4", workflow)
        self.assertNotIn("uses: actions/setup-python@v5", workflow)

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

    def test_payload_verification_blocks_tampering(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            exe = root / "candidate.exe"
            exe.write_bytes(b"MZ release")
            toc = root / "Analysis-00.toc"
            toc.write_text("('app.py', 'PYMODULE')", encoding="utf-8")
            license_path = root / "dependency-LICENSE.txt"
            license_path.write_text("license", encoding="utf-8")
            release = prepare_release(
                exe_path=exe,
                output_dir=root / "release",
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
            )
            verify_payload(release)
            (release / "README-KO.txt").write_text(
                "tampered",
                encoding="utf-8",
            )
            with self.assertRaisesRegex(
                ReleasePackagingError,
                "checksum mismatch",
            ):
                verify_payload(release)

    def test_portable_asset_has_one_friendly_root_and_public_hashes(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            exe = root / "candidate.exe"
            exe.write_bytes(b"MZ release")
            toc = root / "Analysis-00.toc"
            toc.write_text("('app.py', 'PYMODULE')", encoding="utf-8")
            license_path = root / "dependency-LICENSE.txt"
            license_path.write_text("license", encoding="utf-8")
            release = prepare_release(
                exe_path=exe,
                output_dir=root / "release",
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
            )
            assets = root / "assets"
            assets.mkdir()
            installer = assets / (
                "SCPI-Automation-Platform-Setup-0.1.0-dev.1-win64.exe"
            )
            installer.write_bytes(b"MZ setup")
            portable, checksums = create_release_assets(
                payload_dir=release,
                installer=installer,
                output_dir=assets,
            )

            with zipfile.ZipFile(portable) as archive:
                names = archive.namelist()
            self.assertTrue(names)
            self.assertTrue(
                all(
                    name.startswith(
                        "SCPI-Automation-Platform-0.1.0-dev.1-win64/"
                    )
                    for name in names
                )
            )
            public_hashes = {
                name: digest
                for digest, name in (
                    line.split("  ", 1)
                    for line in checksums.read_text(
                        encoding="utf-8"
                    ).splitlines()
                )
            }
            self.assertEqual(set(public_hashes), {installer.name, portable.name})
            self.assertEqual(
                public_hashes[portable.name],
                hashlib.sha256(portable.read_bytes()).hexdigest(),
            )


if __name__ == "__main__":
    unittest.main()
