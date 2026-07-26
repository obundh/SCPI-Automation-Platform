from __future__ import annotations

import unittest

from scpi_automation.identity import (
    ClassificationConfidence,
    DeviceCategory,
    IdentityParseError,
    classify_identity,
    parse_idn_response,
)


class IdentityParserTests(unittest.TestCase):
    def test_parses_standard_four_field_response(self) -> None:
        identity = parse_idn_response(
            "Rohde&Schwarz,FSV30,123456,3.50\n"
        )

        self.assertEqual(identity.manufacturer, "Rohde&Schwarz")
        self.assertEqual(identity.model, "FSV30")
        self.assertEqual(identity.serial, "123456")
        self.assertEqual(identity.firmware, "3.50")

    def test_keeps_commas_in_firmware_field(self) -> None:
        identity = parse_idn_response("Vendor,Model,Serial,FW1,FPGA2")

        self.assertEqual(identity.firmware, "FW1,FPGA2")

    def test_rejects_empty_response(self) -> None:
        with self.assertRaises(IdentityParseError):
            parse_idn_response("  \x00 ")

    def test_rejects_response_without_model(self) -> None:
        with self.assertRaises(IdentityParseError):
            parse_idn_response("VendorOnly")


class ClassifierTests(unittest.TestCase):
    def test_fsv30_is_exact_profile_candidate(self) -> None:
        identity = parse_idn_response("Rohde&Schwarz,FSV30,123,3.50")

        result = classify_identity(identity)

        self.assertEqual(result.category, DeviceCategory.SPECTRUM_ANALYZER)
        self.assertEqual(
            result.confidence,
            ClassificationConfidence.EXACT_PROFILE,
        )
        self.assertEqual(result.profile_id, "rs_fsv_fsva")
        self.assertEqual(
            result.profile_status,
            "candidate_pack_unvalidated",
        )

    def test_rohde_schwarz_smb100a_is_exact_representative(self) -> None:
        identity = parse_idn_response("Rohde&Schwarz,SMB100A,100001,4.1")

        result = classify_identity(identity)

        self.assertEqual(result.category, DeviceCategory.SIGNAL_GENERATOR)
        self.assertEqual(
            result.confidence,
            ClassificationConfidence.EXACT_PROFILE,
        )
        self.assertEqual(result.profile_id, "rs_smb100a")

    def test_unknown_model_stays_unclassified(self) -> None:
        identity = parse_idn_response("Example,MODEL-X,1,1.0")

        result = classify_identity(identity)

        self.assertEqual(result.category, DeviceCategory.UNKNOWN)
        self.assertEqual(result.confidence, ClassificationConfidence.UNKNOWN)


if __name__ == "__main__":
    unittest.main()
