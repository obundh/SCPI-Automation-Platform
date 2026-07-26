from __future__ import annotations

import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from scpi_automation.validation import (
    ManualCatalogError,
    load_manual_command_catalog,
    load_manual_command_catalogs,
    manual_command_catalog_directory,
)


def _payload(
    *,
    manual_id: str = "private_example_manual",
) -> dict:
    commands = [
        {
            "command_id": "private_example.manual.read_value",
            "command_pattern": "SENSe:VALUe?",
            "command_group": "SENS",
            "manual_page": 10,
            "query_scpi_candidate": "SENS:VAL?",
            "query_support": "manual_explicit",
            "write_support": "unknown",
            "probe_policy": "query_explicit",
            "verification": "manual_index_candidate",
        },
        {
            "command_id": "private_example.manual.configure",
            "command_pattern": "CONFigure:MODE",
            "command_group": "CONF",
            "manual_page": 11,
            "query_scpi_candidate": "CONF:MODE?",
            "query_support": "unverified_probe",
            "write_support": "unknown",
            "probe_policy": "query_probe",
            "verification": "manual_index_candidate",
        },
    ]
    return {
        "schema_version": 1,
        "profile_id": "private_example",
        "manual": {
            "manual_id": manual_id,
            "title": "User-supplied private example",
            "document_reference": "LOCAL-ONLY",
            "version": "1",
            "firmware": "",
            "source_url": "https://example.invalid/private-manual",
            "index_pdf_pages": [1, 2],
        },
        "extraction": {
            "method": "user_local_fixture",
            "command_count": len(commands),
            "notes": "Synthetic test data; not a manufacturer manual extract.",
        },
        "commands": commands,
    }


def _write_payload(
    directory: Path,
    *,
    name: str = "catalog.json",
    payload: dict | None = None,
) -> Path:
    path = directory / name
    path.write_text(
        json.dumps(payload or _payload(), ensure_ascii=False),
        encoding="utf-8",
    )
    return path


class ManualCommandCatalogTests(unittest.TestCase):
    def test_default_catalog_is_user_local_and_empty_when_not_created(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as folder:
            target = Path(folder) / "not-created"
            with patch.dict(
                os.environ,
                {"SCPI_AUTOMATION_MANUAL_CATALOG": str(target)},
            ):
                self.assertEqual(
                    manual_command_catalog_directory(),
                    target,
                )
                index = load_manual_command_catalogs()
        self.assertEqual(index.catalogs, ())
        self.assertEqual(index.command_count, 0)

    def test_loads_and_searches_private_local_candidates(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            path = _write_payload(Path(folder))
            catalog = load_manual_command_catalog(
                path,
                expected_profile_id="private_example",
            )

        self.assertEqual(catalog.command_count, 2)
        self.assertEqual(catalog.source.manual_id, "private_example_manual")
        self.assertEqual(catalog.count(group="sens"), 1)
        self.assertEqual(
            catalog.search("value")[0].query_probe,
            "SENS:VAL?",
        )

    def test_expected_profile_and_malformed_page_fail_clearly(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            directory = Path(folder)
            path = _write_payload(directory)
            with self.assertRaisesRegex(
                ManualCatalogError,
                r"profile_id.*expected 'wrong'",
            ):
                load_manual_command_catalog(
                    path,
                    expected_profile_id="wrong",
                )

            broken = _payload()
            broken["commands"][0]["manual_page"] = 0
            broken_path = _write_payload(
                directory,
                name="broken.json",
                payload=broken,
            )
            with self.assertRaisesRegex(
                ManualCatalogError,
                r"broken\.json: commands\[0\]\.manual_page",
            ):
                load_manual_command_catalog(broken_path)

    def test_declared_count_and_duplicate_manual_fail_clearly(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            directory = Path(folder)
            bad_count = _payload()
            bad_count["extraction"]["command_count"] = 3
            bad_path = _write_payload(
                directory,
                name="bad-count.json",
                payload=bad_count,
            )
            with self.assertRaisesRegex(
                ManualCatalogError,
                r"declares 3, but commands has 2",
            ):
                load_manual_command_catalog(bad_path)

            bad_path.unlink()
            _write_payload(directory, name="one.json")
            _write_payload(directory, name="two.json")
            with self.assertRaisesRegex(
                ManualCatalogError,
                "Duplicate manual-command profile/manual IDs",
            ):
                load_manual_command_catalogs(directory)

    def test_multiple_private_manuals_merge_and_bad_peer_can_skip(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as folder:
            directory = Path(folder)
            _write_payload(directory, name="base.json")
            option = _payload(manual_id="private_example_option")
            option["commands"] = option["commands"][:1]
            option["extraction"]["command_count"] = 1
            _write_payload(
                directory,
                name="option.json",
                payload=option,
            )
            (directory / "broken.json").write_text("{", encoding="utf-8")

            index = load_manual_command_catalogs(
                directory,
                strict=False,
            )

        self.assertEqual(
            len(index.catalogs_for_profile("private_example")),
            2,
        )
        self.assertEqual(
            len(index.search(profile_id="private_example")),
            3,
        )
        self.assertEqual(len(index.load_errors), 1)


if __name__ == "__main__":
    unittest.main()
