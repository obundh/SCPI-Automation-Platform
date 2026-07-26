from __future__ import annotations

import unittest

from scpi_automation.identity import (
    ClassificationConfidence,
    DeviceCategory,
    catalog_profiles,
    classify_identity,
    parse_idn_response,
    profile_by_id,
)
from scpi_automation.routine import (
    FeatureRisk,
    FeatureVerification,
    SelectedInstrument,
    feature_by_id,
    features_for,
    select_feature,
)


_PROFILE_EXPECTATIONS = {
    "kikusui_pmx35_3a": (
        DeviceCategory.POWER_SUPPLY,
        27,
        "KIKUSUI,PMX35-3A,S1,1.0",
    ),
    "rs_smb100a": (
        DeviceCategory.SIGNAL_GENERATOR,
        27,
        "Rohde&Schwarz,SMB100A,S2,5.00",
    ),
    "rs_fsl": (
        DeviceCategory.SPECTRUM_ANALYZER,
        32,
        "Rohde&Schwarz,FSL18,S3,2.0",
    ),
    "rs_fsw": (
        DeviceCategory.SPECTRUM_ANALYZER,
        39,
        "Rohde&Schwarz,FSW43,S4,6.30",
    ),
    "rs_fsv_fsva": (
        DeviceCategory.SPECTRUM_ANALYZER,
        25,
        "Rohde&Schwarz,FSV30,S5,3.50",
    ),
    "keysight_e36312a": (
        DeviceCategory.POWER_SUPPLY,
        8,
        "Keysight Technologies,E36312A,S6,1.0",
    ),
    "keysight_33500_series": (
        DeviceCategory.FUNCTION_GENERATOR,
        46,
        "Keysight Technologies,33522B,S7,1.0",
    ),
    "keysight_344xxa_truevolt": (
        DeviceCategory.DIGITAL_MULTIMETER,
        33,
        "Keysight Technologies,34470A,S8,1.0",
    ),
    "rigol_ds1000z": (
        DeviceCategory.OSCILLOSCOPE,
        55,
        "RIGOL TECHNOLOGIES,DS1054Z,S9,00.04",
    ),
    "keysight_e4980a": (
        DeviceCategory.LCR_METER,
        29,
        "Keysight Technologies,E4980A,S10,A.03",
    ),
    "keysight_n52xx_pna": (
        DeviceCategory.NETWORK_ANALYZER,
        39,
        "Keysight Technologies,N5245A,S11,A.10",
    ),
    "rs_hmp2000_hmp4000": (
        DeviceCategory.POWER_SUPPLY,
        30,
        "Rohde&Schwarz,HMP4040,S12,2.71",
    ),
}


class RepresentativeProfileIntegrationTests(unittest.TestCase):
    def _profile_feature(
        self,
        profile_id: str,
        capability_id: str,
        operation: str,
    ):
        profile = profile_by_id(profile_id)
        self.assertIsNotNone(profile)
        assert profile is not None
        matches = [
            feature
            for feature in features_for(profile.category, profile_id)
            if feature.capability_id == capability_id
            and feature.operation == operation
        ]
        self.assertEqual(
            len(matches),
            1,
            f"{profile_id}: {capability_id}.{operation}",
        )
        return matches[0]

    @staticmethod
    def _instrument(
        profile_id: str,
        *,
        model: str = "",
        compatibility_status: str = "",
        compatible_capability_ids: tuple[str, ...] = (),
        compatible_operation_ids: tuple[str, ...] = (),
    ) -> SelectedInstrument:
        profile = profile_by_id(profile_id)
        assert profile is not None
        return SelectedInstrument(
            resource=f"DEMO::{profile_id}::INSTR",
            category=profile.category,
            manufacturer=profile.manufacturer,
            model=model or profile.model_family,
            profile_id=profile_id,
            compatibility_status=compatibility_status,
            compatible_capability_ids=compatible_capability_ids,
            compatible_operation_ids=compatible_operation_ids,
        )

    @staticmethod
    def _valid_arguments(feature) -> dict[str, str]:
        arguments: dict[str, str] = {}
        for parameter in feature.parameters:
            if parameter.choices:
                value = parameter.choices[0]
            elif parameter.mapping:
                value = parameter.mapping[0][0]
            elif parameter.value_type == "boolean":
                value = "false"
            elif parameter.value_type == "integer":
                candidate = (
                    parameter.minimum
                    if parameter.minimum is not None
                    else 1
                )
                value = str(candidate)
            elif (
                "float" in parameter.value_type
                or "number" in parameter.value_type
            ):
                candidate = (
                    parameter.minimum
                    if parameter.minimum is not None
                    else 1
                )
                value = str(candidate)
            elif parameter.value_type == "voltage_current_time_triplets":
                value = "1,1,1"
            else:
                value = "test"
            arguments[parameter.name] = value
        return arguments

    def test_catalog_has_the_12_expected_profiles_and_operation_counts(
        self,
    ) -> None:
        profiles = {profile.profile_id: profile for profile in catalog_profiles()}

        self.assertEqual(set(profiles), set(_PROFILE_EXPECTATIONS))
        for profile_id, (category, operation_count, _idn) in (
            _PROFILE_EXPECTATIONS.items()
        ):
            with self.subTest(profile_id=profile_id):
                profile = profiles[profile_id]
                actual_count = sum(
                    len(capability.operations)
                    for capability in profile.capabilities
                )
                self.assertEqual(profile.category, category)
                self.assertEqual(actual_count, operation_count)
                self.assertEqual(
                    len(features_for(category, profile_id)),
                    operation_count,
                )

    def test_each_catalog_operation_is_exposed_with_profile_metadata(
        self,
    ) -> None:
        risk_map = {
            "low": FeatureRisk.SAFE,
            "medium": FeatureRisk.CAUTION,
            "high": FeatureRisk.HAZARDOUS,
        }

        for profile in catalog_profiles():
            exposed = {
                (feature.capability_id, feature.operation): feature
                for feature in features_for(profile.category, profile.profile_id)
            }
            expected_keys = {
                (capability.capability_id, operation.name)
                for capability in profile.capabilities
                for operation in capability.operations
            }
            self.assertEqual(
                set(exposed),
                expected_keys,
                profile.profile_id,
            )
            for capability in profile.capabilities:
                for operation in capability.operations:
                    with self.subTest(
                        profile_id=profile.profile_id,
                        capability_id=capability.capability_id,
                        operation=operation.name,
                    ):
                        feature = exposed[
                            (capability.capability_id, operation.name)
                        ]
                        expected_parameter_names = tuple(
                            str(parameter.get("name", ""))
                            for parameter in capability.parameters
                            if (
                                parameter.get("name")
                                and (
                                    "{"
                                    + str(parameter["name"])
                                    + "}"
                                )
                                in operation.scpi
                            )
                        )
                        self.assertEqual(feature.scpi_preview, operation.scpi)
                        self.assertEqual(
                            feature.response_type,
                            operation.response_type,
                        )
                        self.assertEqual(
                            tuple(
                                parameter.name
                                for parameter in feature.parameters
                            ),
                            expected_parameter_names,
                        )
                        self.assertEqual(
                            feature.risk,
                            risk_map[capability.risk_level],
                        )
                        self.assertEqual(
                            feature.profile_ids,
                            (profile.profile_id,),
                        )

    def test_every_profile_operation_accepts_valid_placeholder_arguments(
        self,
    ) -> None:
        for profile in catalog_profiles():
            instrument = self._instrument(profile.profile_id)
            for feature in features_for(profile.category, profile.profile_id):
                with self.subTest(
                    profile_id=profile.profile_id,
                    feature_id=feature.feature_id,
                ):
                    selected = select_feature(
                        instrument,
                        feature.feature_id,
                        arguments=self._valid_arguments(feature),
                    )
                    self.assertEqual(selected.feature_id, feature.feature_id)

    def test_all_12_idn_examples_match_the_exact_profile(self) -> None:
        for profile_id, (category, _count, raw_idn) in (
            _PROFILE_EXPECTATIONS.items()
        ):
            with self.subTest(profile_id=profile_id):
                result = classify_identity(parse_idn_response(raw_idn))
                profile = profile_by_id(profile_id)
                self.assertIsNotNone(profile)
                assert profile is not None

                self.assertEqual(result.category, category)
                self.assertEqual(
                    result.confidence,
                    ClassificationConfidence.EXACT_PROFILE,
                )
                self.assertEqual(result.profile_id, profile_id)
                self.assertEqual(
                    result.profile_status,
                    "candidate_pack_unvalidated",
                )

    def test_fsv3000_generation_does_not_match_legacy_fsv30_profile(
        self,
    ) -> None:
        for model in ("FSV3000", "FSVA3000", "FSV3013"):
            with self.subTest(model=model):
                result = classify_identity(
                    parse_idn_response(
                        f"Rohde&Schwarz,{model},SERIAL,5.00"
                    )
                )
                self.assertNotEqual(
                    result.confidence,
                    ClassificationConfidence.EXACT_PROFILE,
                )
                self.assertNotEqual(result.profile_id, "rs_fsv_fsva")

    def test_profile_specific_parameters_do_not_leak_between_models(
        self,
    ) -> None:
        fsl_center = self._profile_feature(
            "rs_fsl",
            "analyzer.frequency.center",
            "set",
        )
        fsv_center = self._profile_feature(
            "rs_fsv_fsva",
            "analyzer.frequency.center",
            "set",
        )
        kikusui_voltage = self._profile_feature(
            "kikusui_pmx35_3a",
            "source.voltage",
            "set",
        )
        hmp_voltage = self._profile_feature(
            "rs_hmp2000_hmp4000",
            "source.voltage",
            "set",
        )

        self.assertIsNot(fsl_center, fsv_center)
        self.assertEqual(fsl_center.scpi_preview, "FREQ:CENT {value}")
        self.assertIsNone(fsl_center.parameters[0].minimum)
        self.assertEqual(fsv_center.scpi_preview, ":FREQ:CENT {value}")
        self.assertEqual(fsv_center.parameters[0].minimum, 1_000_000.0)
        self.assertEqual(fsv_center.parameters[0].maximum, 44_000_000_000.0)
        self.assertEqual(kikusui_voltage.parameters[0].maximum, 36.75)
        self.assertIsNone(hmp_voltage.parameters[0].maximum)
        self.assertIs(
            feature_by_id(fsl_center.feature_id, "rs_fsl"),
            fsl_center,
        )
        self.assertIs(
            feature_by_id(fsv_center.feature_id, "rs_fsv_fsva"),
            fsv_center,
        )

    def test_query_features_only_request_placeholders_used_by_query(
        self,
    ) -> None:
        smb_power_query = self._profile_feature(
            "rs_smb100a",
            "source.power",
            "query",
        )
        pna_port_power_query = self._profile_feature(
            "keysight_n52xx_pna",
            "source.port.power",
            "query",
        )
        fsl_trace_query = self._profile_feature(
            "rs_fsl",
            "trace.read",
            "query",
        )

        self.assertEqual(smb_power_query.parameters, ())
        self.assertEqual(
            tuple(item.name for item in pna_port_power_query.parameters),
            ("port",),
        )
        self.assertEqual(
            tuple(item.name for item in fsl_trace_query.parameters),
            ("trace",),
        )

        select_feature(
            self._instrument("rs_smb100a"),
            smb_power_query.feature_id,
        )
        select_feature(
            self._instrument("keysight_n52xx_pna"),
            pna_port_power_query.feature_id,
            arguments={"port": "1"},
        )
        with self.assertRaises(ValueError):
            select_feature(
                self._instrument("keysight_n52xx_pna"),
                pna_port_power_query.feature_id,
                arguments={"port": "1", "value": "0"},
            )

    def test_numeric_enum_and_boolean_parameters_are_enforced(self) -> None:
        fsv_center = self._profile_feature(
            "rs_fsv_fsva",
            "analyzer.frequency.center",
            "set",
        )
        fsv = self._instrument("rs_fsv_fsva")
        for accepted in ("1000000", "44000000000"):
            select_feature(
                fsv,
                fsv_center.feature_id,
                arguments={"value": accepted},
            )
        for rejected in ("999999", "44000000001", "nan", "inf"):
            with self.subTest(rejected_center=rejected):
                with self.assertRaises(ValueError):
                    select_feature(
                        fsv,
                        fsv_center.feature_id,
                        arguments={"value": rejected},
                    )

        pna_port_power = self._profile_feature(
            "keysight_n52xx_pna",
            "source.port.power",
            "set",
        )
        pna = self._instrument("keysight_n52xx_pna")
        select_feature(
            pna,
            pna_port_power.feature_id,
            arguments={"port": "4", "value": "13"},
        )
        for port in ("0", "4.5", "5"):
            with self.subTest(rejected_port=port):
                with self.assertRaises(ValueError):
                    select_feature(
                        pna,
                        pna_port_power.feature_id,
                        arguments={"port": port, "value": "0"},
                    )

        waveform = self._profile_feature(
            "keysight_33500_series",
            "waveform.shape",
            "set",
        )
        generator = self._instrument("keysight_33500_series")
        select_feature(
            generator,
            waveform.feature_id,
            arguments={"channel": "1", "shape": "SIN"},
        )
        with self.assertRaises(ValueError):
            select_feature(
                generator,
                waveform.feature_id,
                arguments={"channel": "1", "shape": "NOT_A_WAVEFORM"},
            )

        smb_output = self._profile_feature(
            "rs_smb100a",
            "rf.output.state",
            "set",
        )
        smb = self._instrument("rs_smb100a")
        select_feature(
            smb,
            smb_output.feature_id,
            arguments={"state": "false"},
        )
        with self.assertRaises(ValueError):
            select_feature(
                smb,
                smb_output.feature_id,
                arguments={"state": "maybe"},
            )

    def test_validated_fsv30_uses_model_limit_not_family_maximum(self) -> None:
        feature = self._profile_feature(
            "rs_fsv_fsva",
            "analyzer.frequency.center",
            "set",
        )
        instrument = self._instrument(
            "rs_fsv_fsva",
            model="FSV30",
            compatibility_status="hardware_validated_partial",
            compatible_operation_ids=(
                "analyzer.frequency.center::set",
            ),
        )

        select_feature(
            instrument,
            feature.feature_id,
            arguments={"value": "30000000000"},
        )
        with self.assertRaisesRegex(ValueError, "30000000000"):
            select_feature(
                instrument,
                feature.feature_id,
                arguments={"value": "30000000001"},
            )

    def test_unknown_validated_model_does_not_inherit_numeric_range(
        self,
    ) -> None:
        feature = self._profile_feature(
            "rs_fsv_fsva",
            "analyzer.frequency.center",
            "set",
        )
        instrument = self._instrument(
            "rs_fsv_fsva",
            model="UNLISTED-SA",
            compatibility_status="hardware_validated_partial",
            compatible_operation_ids=(
                "analyzer.frequency.center::set",
            ),
        )

        with self.assertRaisesRegex(ValueError, "수치 허용 범위"):
            select_feature(
                instrument,
                feature.feature_id,
                arguments={"value": "1000000"},
            )

    def test_auto_and_mnemonic_numeric_parameters_are_enforced(self) -> None:
        fsl = self._instrument("rs_fsl")
        rbw = self._profile_feature("rs_fsl", "analyzer.rbw", "set")
        for value in ("AUTO", "1000"):
            select_feature(
                fsl,
                rbw.feature_id,
                arguments={"value": value},
            )
        for value in ("not-a-number", "nan", "inf"):
            with self.subTest(rbw=value):
                with self.assertRaises(ValueError):
                    select_feature(
                        fsl,
                        rbw.feature_id,
                        arguments={"value": value},
                    )

        dmm = self._instrument("keysight_344xxa_truevolt")
        trigger_count = self._profile_feature(
            "keysight_344xxa_truevolt",
            "trigger.count",
            "set",
        )
        for value in ("1", "MIN", "MAX", "DEF", "INF"):
            select_feature(
                dmm,
                trigger_count.feature_id,
                arguments={"value": value},
            )
        for value in ("0", "1.5", "none"):
            with self.subTest(trigger_count=value):
                with self.assertRaises(ValueError):
                    select_feature(
                        dmm,
                        trigger_count.feature_id,
                        arguments={"value": value},
                    )

        trigger_delay = self._profile_feature(
            "keysight_344xxa_truevolt",
            "trigger.delay",
            "set",
        )
        for value in ("0", "3600", "DEF"):
            select_feature(
                dmm,
                trigger_delay.feature_id,
                arguments={"value": value},
            )
        for value in ("-1", "3600.1", "nan"):
            with self.subTest(trigger_delay=value):
                with self.assertRaises(ValueError):
                    select_feature(
                        dmm,
                        trigger_delay.feature_id,
                        arguments={"value": value},
                    )

    def test_special_value_and_sequence_parameters_are_enforced(self) -> None:
        generator = self._instrument("keysight_33500_series")
        output_load = self._profile_feature(
            "keysight_33500_series",
            "output.load",
            "set",
        )
        for value in ("50", "INF", "MIN", "MAX", "DEF"):
            select_feature(
                generator,
                output_load.feature_id,
                arguments={"channel": "1", "value": value},
            )
        for value in ("OPEN", "nan"):
            with self.subTest(output_load=value):
                with self.assertRaises(ValueError):
                    select_feature(
                        generator,
                        output_load.feature_id,
                        arguments={"channel": "1", "value": value},
                    )

        lcr = self._instrument("keysight_e4980a")
        bias_voltage = self._profile_feature(
            "keysight_e4980a",
            "bias.dc.voltage",
            "set",
        )
        select_feature(
            lcr,
            bias_voltage.feature_id,
            arguments={"value": "1.5"},
        )
        for value in ("MAX", "nan", "inf"):
            with self.subTest(bias_voltage=value):
                with self.assertRaises(ValueError):
                    select_feature(
                        lcr,
                        bias_voltage.feature_id,
                        arguments={"value": value},
                    )

        hmp = self._instrument("rs_hmp2000_hmp4000")
        sequence_data = self._profile_feature(
            "rs_hmp2000_hmp4000",
            "sequence.data",
            "set",
        )
        select_feature(
            hmp,
            sequence_data.feature_id,
            arguments={"triplets": "1,0.5,0.06"},
        )
        select_feature(
            hmp,
            sequence_data.feature_id,
            arguments={
                "triplets": ",".join(("1", "0.5", "10") * 128)
            },
        )
        invalid_sequences = (
            "1,0.5",
            "1,0.5,0.059",
            "1,0.5,10.1",
            "1,0.5,nan",
            "1,not-current,1",
            ",".join(("1", "0.5", "1") * 129),
        )
        for value in invalid_sequences:
            with self.subTest(sequence=value[:40]):
                with self.assertRaises(ValueError):
                    select_feature(
                        hmp,
                        sequence_data.feature_id,
                        arguments={"triplets": value},
                    )

    def test_abbreviations_have_beginner_facing_korean_labels(self) -> None:
        rbw = self._profile_feature("rs_fsl", "analyzer.rbw", "set")
        vbw = self._profile_feature("rs_fsl", "analyzer.vbw", "set")
        span = self._profile_feature(
            "rs_fsl",
            "analyzer.frequency.span",
            "set",
        )

        self.assertEqual(rbw.display_name, "RBW - 분해능 대역폭 설정")
        self.assertEqual(vbw.display_name, "VBW - 비디오 대역폭 설정")
        self.assertEqual(span.display_name, "Span - 주파수 분석 범위 설정")

        for profile in catalog_profiles():
            for feature in features_for(profile.category, profile.profile_id):
                if not feature.capability_id.startswith("trace."):
                    continue
                with self.subTest(
                    profile_id=profile.profile_id,
                    feature_id=feature.feature_id,
                ):
                    _english, separator, korean = (
                        feature.display_name.partition(" - ")
                    )
                    self.assertEqual(separator, " - ")
                    self.assertIn("트레이스", korean)

    def test_trace_marker_and_waveform_operations_are_not_flattened(
        self,
    ) -> None:
        fsl_peak = self._profile_feature(
            "rs_fsl",
            "marker.peak_search",
            "execute",
        )
        fsl_marker_y = self._profile_feature(
            "rs_fsl",
            "marker.amplitude",
            "query",
        )
        fsl_trace = self._profile_feature("rs_fsl", "trace.read", "query")
        scope_waveform = self._profile_feature(
            "rigol_ds1000z",
            "waveform.data",
            "query",
        )
        pna_trace = self._profile_feature(
            "keysight_n52xx_pna",
            "trace.data.formatted",
            "query",
        )

        self.assertEqual(fsl_peak.scpi_preview, "CALC:MARK{marker}:MAX")
        self.assertEqual(fsl_marker_y.response_type, "float")
        self.assertEqual(fsl_trace.response_type, "float_array")
        self.assertEqual(scope_waveform.scpi_preview, ":WAV:DATA?")
        self.assertEqual(scope_waveform.response_type, "array")
        self.assertEqual(pna_trace.scpi_preview, "CALC:DATA? FDATA")
        self.assertEqual(pna_trace.response_type, "float_array")
        for feature in (
            fsl_peak,
            fsl_marker_y,
            fsl_trace,
            scope_waveform,
            pna_trace,
        ):
            self.assertEqual(feature.risk, FeatureRisk.SAFE)

    def test_output_and_source_high_risk_operations_remain_hazardous(
        self,
    ) -> None:
        critical_operations = (
            ("kikusui_pmx35_3a", "output.state", "set"),
            ("rs_smb100a", "source.power", "set"),
            ("rs_smb100a", "rf.output.state", "set"),
            ("keysight_e36312a", "channel.output.state", "set"),
            ("keysight_33500_series", "waveform.amplitude", "set"),
            ("keysight_33500_series", "output.state", "set"),
            ("keysight_e4980a", "bias.dc.state", "set"),
            ("keysight_n52xx_pna", "source.port.power", "set"),
            ("keysight_n52xx_pna", "rf.output.state", "set"),
            ("rs_hmp2000_hmp4000", "output.master_state", "set"),
        )

        for profile_id, capability_id, operation in critical_operations:
            with self.subTest(
                profile_id=profile_id,
                capability_id=capability_id,
            ):
                feature = self._profile_feature(
                    profile_id,
                    capability_id,
                    operation,
                )
                self.assertEqual(feature.risk, FeatureRisk.HAZARDOUS)
                self.assertTrue(feature.is_dangerous)

        rigol_waveform = self._profile_feature(
            "rigol_ds1000z",
            "waveform.preamble",
            "query",
        )
        self.assertEqual(
            rigol_waveform.verification,
            FeatureVerification.BENCH_OBSERVED,
        )
        self.assertFalse(rigol_waveform.is_dangerous)

    def test_known_profile_rejects_another_profile_only_operation(self) -> None:
        kikusui = self._instrument("kikusui_pmx35_3a")
        hmp_sequence_start = self._profile_feature(
            "rs_hmp2000_hmp4000",
            "sequence.start",
            "execute",
        )
        fsv = self._instrument("rs_fsv_fsva")
        fsw_channel_create = self._profile_feature(
            "rs_fsw",
            "application.channel.create",
            "execute",
        )

        with self.assertRaises(ValueError):
            select_feature(
                kikusui,
                hmp_sequence_start.feature_id,
                arguments=self._valid_arguments(hmp_sequence_start),
            )
        with self.assertRaises(ValueError):
            select_feature(
                fsv,
                fsw_channel_create.feature_id,
                arguments=self._valid_arguments(fsw_channel_create),
            )

    def test_validated_operation_allowlist_filters_browse_and_add(
        self,
    ) -> None:
        allowed_operations = (
            "analyzer.frequency.center::set",
            "analyzer.frequency.center::query",
            "trace.read::query",
        )
        instrument = self._instrument(
            "rs_fsl",
            model="UNLISTED-SA",
            compatibility_status="hardware_validated_partial",
            compatible_operation_ids=allowed_operations,
        )
        visible = features_for(
            instrument.category,
            instrument.profile_id,
            instrument.compatible_capability_ids,
            instrument.compatibility_status,
            instrument.compatible_operation_ids,
        )

        self.assertGreater(len(visible), 0)
        self.assertEqual(
            {
                f"{feature.capability_id}::{feature.operation}"
                for feature in visible
            },
            set(allowed_operations),
        )
        for feature in visible:
            if (
                feature.capability_id == "analyzer.frequency.center"
                and feature.operation == "set"
            ):
                with self.assertRaisesRegex(ValueError, "수치 허용 범위"):
                    select_feature(
                        instrument,
                        feature.feature_id,
                        arguments=self._valid_arguments(feature),
                    )
            else:
                select_feature(
                    instrument,
                    feature.feature_id,
                    arguments=self._valid_arguments(feature),
                )

        disallowed = self._profile_feature(
            "rs_fsl",
            "marker.peak_search",
            "execute",
        )
        with self.assertRaises(ValueError):
            select_feature(
                instrument,
                disallowed.feature_id,
                arguments={"marker": "1"},
            )

        no_capabilities = self._instrument(
            "rs_fsl",
            model="UNLISTED-SA",
            compatibility_status="hardware_validated_partial",
        )
        self.assertEqual(
            features_for(
                no_capabilities.category,
                no_capabilities.profile_id,
                no_capabilities.compatible_capability_ids,
                no_capabilities.compatibility_status,
            ),
            (),
        )

    def test_category_fallback_feature_ids_remain_compatible(self) -> None:
        instrument = SelectedInstrument(
            resource="DEMO::UNCLASSIFIED-SMB::INSTR",
            category=DeviceCategory.SIGNAL_GENERATOR,
            model="UNLISTED-SG",
        )
        fallback_ids = {
            feature.feature_id
            for feature in features_for(instrument.category)
        }

        self.assertIn("signal_generator.set_frequency", fallback_ids)
        selected = select_feature(
            instrument,
            "signal_generator.set_frequency",
        )
        self.assertEqual(
            selected.feature_id,
            "signal_generator.set_frequency",
        )


if __name__ == "__main__":
    unittest.main()
