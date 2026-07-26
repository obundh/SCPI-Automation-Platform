from __future__ import annotations

import unittest
from pathlib import Path

from tools.check_public_release import (
    _path_violation,
    _scan_text,
    run_checks,
)


class PublicReleasePolicyTests(unittest.TestCase):
    def test_current_working_tree_passes_public_policy(self) -> None:
        self.assertEqual(run_checks(include_history=False), [])

    def test_private_manual_and_binary_paths_are_rejected(self) -> None:
        self.assertIsNotNone(
            _path_violation(Path("tmp/pdfs/vendor-manual.pdf"))
        )
        self.assertIsNotNone(
            _path_violation(Path("manuals_private/instrument.pdf"))
        )
        self.assertIsNotNone(
            _path_violation(Path("release/nivisa64.dll"))
        )

    def test_personal_paths_and_credentials_are_rejected(self) -> None:
        self.assertIn(
            "personal Windows user path",
            _scan_text("C:" + "\\Users\\example\\project"),
        )
        self.assertIn(
            "absolute non-system drive path",
            _scan_text("E:" + "\\private-workspace\\project"),
        )
        self.assertIn(
            "private key material",
            _scan_text("-----BEGIN " + "PRIVATE KEY-----"),
        )
        self.assertEqual(_scan_text("C:/results/demo.json"), [])


if __name__ == "__main__":
    unittest.main()
