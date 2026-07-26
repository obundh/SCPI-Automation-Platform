from __future__ import annotations

import csv
import hashlib
import json
import sqlite3
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
CATALOG = ROOT / "scpi_catalog_2026-07-25"


class CatalogArtifactTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.catalog = json.loads(
            (CATALOG / "scpi_catalog.json").read_text(encoding="utf-8")
        )
        cls.profiles = {
            profile["profile_id"]: profile
            for profile in cls.catalog["profiles"]
        }

    def test_split_profiles_match_unified_catalog(self) -> None:
        split_profiles = {
            profile["profile_id"]: profile
            for path in (CATALOG / "profiles").glob("*.json")
            for profile in (json.loads(path.read_text(encoding="utf-8")),)
        }
        self.assertEqual(split_profiles, self.profiles)

    def test_json_csv_and_sqlite_operation_counts_match(self) -> None:
        expected = sum(
            len(capability.get("operations", {}))
            for profile in self.profiles.values()
            for capability in profile.get("capabilities", ())
        )
        with (CATALOG / "command_bindings.csv").open(
            encoding="utf-8",
            newline="",
        ) as stream:
            csv_count = sum(1 for _row in csv.DictReader(stream))
        connection = sqlite3.connect(CATALOG / "scpi_catalog.sqlite")
        try:
            sqlite_count = int(
                connection.execute("SELECT COUNT(*) FROM operations").fetchone()[
                    0
                ]
            )
            integrity = connection.execute("PRAGMA quick_check").fetchone()[0]
        finally:
            connection.close()
        self.assertEqual(expected, csv_count)
        self.assertEqual(expected, sqlite_count)
        self.assertEqual(integrity, "ok")

    def test_fsv_profile_contains_only_mit_driver_backed_surface(self) -> None:
        profile = self.profiles["rs_fsv_fsva"]
        capabilities = {
            item["capability_id"]: item for item in profile["capabilities"]
        }
        self.assertEqual(
            set(capabilities),
            {
                "analyzer.frequency.center",
                "analyzer.frequency.span",
                "display.reference_level",
                "analyzer.rbw",
                "analyzer.vbw",
                "sweep.time",
                "sweep.continuous",
                "trigger.source",
                "trigger.level",
                "correction.state",
                "input.impedance",
                "measurement.initiate",
                "measurement.acp_power.fetch",
                "system.reset",
            },
        )
        self.assertEqual(len(capabilities), 14)
        self.assertEqual(
            sum(len(item["operations"]) for item in capabilities.values()),
            25,
        )
        self.assertTrue(
            all(
                item["source_ids"] == ["qcodes_contrib_rs_fsv3013"]
                and not item.get("manual_references")
                for item in capabilities.values()
            )
        )

    def test_distribution_contains_no_manufacturer_manual_extracts(
        self,
    ) -> None:
        self.assertEqual(
            list((CATALOG / "manual_commands").glob("*.json")),
            [],
        )
        source_by_id = {
            source["source_id"]: source
            for source in self.catalog["sources"]
        }
        for profile in self.profiles.values():
            self.assertFalse(profile.get("manual_ids"))
            for capability in profile["capabilities"]:
                self.assertFalse(capability.get("manual_references"))
                self.assertFalse(
                    capability["verification"].startswith("manual_")
                )
                self.assertTrue(capability.get("source_ids"))
                for source_id in capability["source_ids"]:
                    source = source_by_id[source_id]
                    self.assertEqual(source["license"], "MIT")
                    self.assertTrue(source["license_verified"])
                    self.assertTrue(source["license_url"])
                    self.assertTrue(source["copyright"])

    def test_manual_catalog_contains_bibliography_only(self) -> None:
        manuals = json.loads(
            (CATALOG / "manual_catalog.json").read_text(encoding="utf-8")
        )
        serialized = json.dumps(manuals, ensure_ascii=False)
        for forbidden in (
            "command_pattern",
            "manual_page",
            "query_scpi_candidate",
            "source_pdf_page",
        ):
            self.assertNotIn(forbidden, serialized)

    def test_checksum_manifest_matches_every_listed_file(self) -> None:
        for line in (CATALOG / "SHA256SUMS.txt").read_text(
            encoding="utf-8"
        ).splitlines():
            expected, relative = line.split("  ", 1)
            path = CATALOG / Path(relative)
            self.assertTrue(path.is_file(), relative)
            self.assertEqual(hashlib.sha256(path.read_bytes()).hexdigest(), expected)


if __name__ == "__main__":
    unittest.main()
