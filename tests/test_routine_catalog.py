from __future__ import annotations

import unittest
from dataclasses import FrozenInstanceError

from scpi_automation.identity import DeviceCategory
from scpi_automation.routine import (
    FeatureRisk,
    FeatureVerification,
    SelectedInstrument,
    feature_by_id,
    features_for,
    select_feature,
)


class RoutineCatalogTests(unittest.TestCase):
    def test_every_device_category_has_beginner_facing_features(self) -> None:
        for category in DeviceCategory:
            with self.subTest(category=category):
                features = features_for(category)

                self.assertIsInstance(features, tuple)
                self.assertGreater(len(features), 0)
                for feature in features:
                    self.assertEqual(feature.category, category)
                    self.assertTrue(
                        feature.feature_id.startswith(f"{category.value}.")
                    )
                    self.assertTrue(feature.display_name)
                    original_name, separator, beginner_name = (
                        feature.display_name.partition(" - ")
                    )
                    self.assertEqual(separator, " - ")
                    self.assertTrue(original_name.strip())
                    self.assertTrue(beginner_name.strip())
                    self.assertTrue(feature.description)
                    self.assertEqual(
                        feature.verification,
                        FeatureVerification.PROFILE_REQUIRED,
                    )

    def test_feature_ids_are_unique_and_can_be_looked_up(self) -> None:
        all_features = [
            feature
            for category in DeviceCategory
            for feature in features_for(category)
        ]

        ids = [feature.feature_id for feature in all_features]

        self.assertEqual(len(ids), len(set(ids)))
        for feature in all_features:
            self.assertIs(feature_by_id(feature.feature_id), feature)

    def test_primary_demo_categories_have_expected_features(self) -> None:
        spectrum_names = {
            feature.display_name
            for feature in features_for(DeviceCategory.SPECTRUM_ANALYZER)
        }
        generator_names = {
            feature.display_name
            for feature in features_for(DeviceCategory.SIGNAL_GENERATOR)
        }
        scope_names = {
            feature.display_name
            for feature in features_for(DeviceCategory.OSCILLOSCOPE)
        }

        self.assertIn("Center Frequency - 중심 주파수 설정", spectrum_names)
        self.assertIn("RBW - 분해능 대역폭 설정", spectrum_names)
        self.assertIn("Peak Search - 가장 높은 신호 찾기", spectrum_names)
        self.assertIn("RF Output ON - RF 출력 켜기", generator_names)
        self.assertIn("RF Output OFF - RF 출력 끄기", generator_names)
        self.assertIn("Single Acquisition - 파형 한 번 잡기", scope_names)

    def test_output_features_are_marked_hazardous(self) -> None:
        rf_output = feature_by_id("signal_generator.output_on")
        supply_voltage = feature_by_id("power_supply.set_voltage")
        marker_read = feature_by_id("spectrum_analyzer.read_marker")

        self.assertEqual(rf_output.risk, FeatureRisk.HAZARDOUS)
        self.assertTrue(rf_output.is_dangerous)
        self.assertTrue(supply_voltage.is_dangerous)
        self.assertFalse(marker_read.is_dangerous)

    def test_selected_instrument_and_feature_are_immutable(self) -> None:
        instrument = SelectedInstrument(
            resource="DEMO::SMB100A::INSTR",
            category=DeviceCategory.SIGNAL_GENERATOR,
            manufacturer="Rohde&Schwarz",
            model="SMB100A",
            serial="100001",
        )
        selection = select_feature(
            instrument,
            "signal_generator.set_frequency",
        )

        self.assertEqual(selection.device_resource, instrument.resource)
        self.assertEqual(selection.category, DeviceCategory.SIGNAL_GENERATOR)
        self.assertEqual(selection.instrument.display_name, "Rohde&Schwarz SMB100A")
        with self.assertRaises(FrozenInstanceError):
            instrument.model = "changed"  # type: ignore[misc]
        with self.assertRaises(FrozenInstanceError):
            selection.feature_id = "changed"  # type: ignore[misc]

    def test_selection_rejects_a_feature_from_another_category(self) -> None:
        instrument = SelectedInstrument(
            resource="DEMO::FSV30::INSTR",
            category=DeviceCategory.SPECTRUM_ANALYZER,
            model="FSV30",
        )

        with self.assertRaises(ValueError):
            select_feature(instrument, "signal_generator.output_on")

    def test_unknown_feature_id_is_rejected(self) -> None:
        with self.assertRaises(KeyError):
            feature_by_id("signal_generator.not_registered")

    def test_generic_fallback_cannot_act_as_validated_hardware_command(
        self,
    ) -> None:
        instrument = SelectedInstrument(
            resource="USB0::0x1234::0x5678::SG-01::INSTR",
            category=DeviceCategory.SIGNAL_GENERATOR,
            manufacturer="Example",
            model="SG-01",
            serial="SG-01",
            firmware="1.0",
            raw_idn="Example,SG-01,SG-01,1.0",
            profile_id="rs_smb100a",
            compatibility_status="hardware_validated_partial",
            compatible_capability_ids=("generator.frequency",),
            compatible_operation_ids=("generator.frequency::query",),
            validation_catalog_fingerprint="a" * 64,
            option_state="unsupported",
        )

        with self.assertRaisesRegex(ValueError, "설명·데모용"):
            select_feature(instrument, "signal_generator.output_on")


if __name__ == "__main__":
    unittest.main()
