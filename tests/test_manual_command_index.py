from __future__ import annotations

import unittest
import tempfile
from pathlib import Path

from tools.extract_manual_command_index import (
    _canonical_probe_path,
    _command_header,
    _probe_policy,
    _require_private_output,
    _split_option_annotation,
)


class ManualCommandIndexTests(unittest.TestCase):
    def test_builds_short_query_probe_from_optional_scpi_path(self) -> None:
        self.assertEqual(
            _canonical_probe_path(
                "SENSe[:CHANnel<n>]:VALUe"
            ),
            "SENS:VALU?",
        )
        self.assertEqual(
            _canonical_probe_path(
                "[SENSe:]BANDwidth|BWIDth[:RESolution]"
            ),
            "BAND?",
        )

    def test_preserves_common_command_query(self) -> None:
        self.assertEqual(_canonical_probe_path("*IDN?"), "*IDN?")
        self.assertEqual(_canonical_probe_path("*RST"), "*RST?")

    def test_disruptive_and_large_response_commands_are_not_auto_probed(
        self,
    ) -> None:
        self.assertEqual(_probe_policy("*RST", "*RST?"), "manual_only")
        self.assertEqual(
            _probe_policy("MEMory:DATA", "MEM:DATA?"),
            "query_limited",
        )

    def test_calculate_is_not_confused_with_calibration(self) -> None:
        self.assertEqual(
            _probe_policy(
                "CALCulate:MARKer:X",
                "CALC:MARK:X?",
            ),
            "query_probe",
        )
        self.assertEqual(
            _probe_policy("CALibration:ALL", "CAL:ALL?"),
            "manual_only",
        )

    def test_old_pdf_spacing_and_query_arguments_are_conservative(
        self,
    ) -> None:
        self.assertEqual(
            _command_header(
                "READ:DATA? BLOCK"
            ),
            ("READ:DATA?", "BLOCK"),
        )
        self.assertEqual(
            _canonical_probe_path(
                "[SENSe:]FILTer | BANDwidth:STATe"
            ),
            "FILT:STAT?",
        )
        self.assertEqual(
            _probe_policy(
                "READ:DATA? BLOCK",
                "READ:DATA?",
            ),
            "manual_only",
        )

    def test_separates_option_notes_and_repairs_dangling_bracket(
        self,
    ) -> None:
        self.assertEqual(
            _split_option_annotation(
                "CALCulate:MARKer:Y? (K20)"
            ),
            ("CALCulate:MARKer:Y?", "(K20)"),
        )
        self.assertEqual(
            _split_option_annotation(
                "SYSTem:LOCK:RESet]"
            ),
            ("SYSTem:LOCK:RESet", ""),
        )

    def test_generated_extract_must_stay_outside_repository(self) -> None:
        repository_output = (
            Path(__file__).resolve().parents[1]
            / "local_manual_cache"
            / "example.json"
        )
        with self.assertRaisesRegex(ValueError, "private local data"):
            _require_private_output(repository_output)

        with tempfile.TemporaryDirectory() as folder:
            _require_private_output(Path(folder) / "example.json")


if __name__ == "__main__":
    unittest.main()
