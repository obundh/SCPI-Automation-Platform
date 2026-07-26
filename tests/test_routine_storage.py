from __future__ import annotations

import json
import tempfile
import unittest
from dataclasses import FrozenInstanceError
from pathlib import Path

from scpi_automation.identity import DeviceCategory
from scpi_automation.routine import (
    DelayStep,
    PlanArgumentBinding,
    PlanBoundDelayStep,
    RoutineFile,
    RoutineStorageError,
    SelectedFeature,
    SelectedInstrument,
    WaitForCompletionStep,
    create_plan_bound_delay,
    features_for,
    load_routine,
    load_routine_requirements,
    save_routine,
    select_feature,
)


class RoutineStorageTests(unittest.TestCase):
    def setUp(self) -> None:
        self.analyzer = SelectedInstrument(
            resource="TCPIP0::192.0.2.10::inst0::INSTR",
            category=DeviceCategory.SPECTRUM_ANALYZER,
            manufacturer="Rohde&Schwarz",
            model="FSV30",
            serial="한글-1234",
            firmware="3.60",
            raw_idn="Rohde&Schwarz,FSV30,한글-1234,3.60",
            profile_id="rohde-schwarz.fsv30",
        )
        self.generator = SelectedInstrument(
            resource="USB0::0x1234::0x5678::1001::INSTR",
            category=DeviceCategory.SIGNAL_GENERATOR,
            manufacturer="Rohde&Schwarz",
            model="SMB100A",
            serial="1001",
            raw_idn="Rohde&Schwarz,SMB100A,1001,",
            profile_id="",
        )
        self.instruments = (self.analyzer, self.generator)
        self.steps = (
            SelectedFeature(
                instrument=self.analyzer,
                feature_id="spectrum_analyzer.set_rbw",
            ),
            DelayStep(seconds=0.5),
            WaitForCompletionStep(
                instrument=self.analyzer,
                timeout_seconds=30,
            ),
            SelectedFeature(
                instrument=self.generator,
                feature_id="signal_generator.output_off",
            ),
        )

    def test_round_trip_preserves_equipment_and_ordered_steps(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory, "차폐효율 루틴.scpiroutine.json")

            save_routine(path, self.instruments, self.steps)
            loaded = load_routine(
                path,
                trusted_instruments=self.instruments,
            )

            self.assertIsInstance(loaded, RoutineFile)
            self.assertEqual(loaded.schema_version, 6)
            self.assertEqual(loaded.instruments, self.instruments)
            self.assertEqual(loaded.required_instruments, self.instruments)
            self.assertEqual(loaded.steps, self.steps)
            self.assertIs(loaded.steps[0].instrument, loaded.instruments[0])
            self.assertIs(loaded.steps[2].instrument, loaded.instruments[0])

    def test_saved_json_is_readable_utf8_and_contains_no_raw_scpi(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory, "routine.json")

            save_routine(path, self.instruments, self.steps)
            text = path.read_text(encoding="utf-8")
            payload = json.loads(text)

            self.assertIn("\n  ", text)
            self.assertIn("한글-1234", text)
            self.assertEqual(payload["schema_version"], 6)
            self.assertNotIn("command", text.casefold())
            self.assertNotIn("scpi", text.casefold())
            self.assertNotIn("SOUR:", text)
            self.assertEqual(
                [step["type"] for step in payload["steps"]],
                [
                    "feature",
                    "delay",
                    "wait_for_completion",
                    "feature",
                ],
            )

    def test_save_atomically_replaces_existing_file_and_cleans_temp_file(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory, "routine.json")
            path.write_text("old content", encoding="utf-8")

            save_routine(path, self.instruments, self.steps)

            self.assertEqual(
                load_routine(
                    path,
                    trusted_instruments=self.instruments,
                ).steps,
                self.steps,
            )
            self.assertEqual(
                list(Path(directory).glob(f".{path.name}.*.tmp")),
                [],
            )

    def test_loaded_document_is_immutable_and_uses_tuples(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory, "routine.json")
            save_routine(path, self.instruments, self.steps)

            loaded = load_routine(
                path,
                trusted_instruments=self.instruments,
            )

            self.assertIsInstance(loaded.instruments, tuple)
            self.assertIsInstance(loaded.steps, tuple)
            with self.assertRaises(FrozenInstanceError):
                loaded.steps = ()  # type: ignore[misc]

    def test_version_1_file_is_loaded_with_safe_compatibility_defaults(self) -> None:
        loaded = self._load_payload(self._valid_payload())

        self.assertEqual(loaded.schema_version, 1)
        self.assertEqual(loaded.instruments[0].compatibility_status, "")
        self.assertEqual(
            loaded.instruments[0].compatible_capability_ids,
            (),
        )
        self.assertEqual(loaded.instruments[0].compatible_operation_ids, ())
        self.assertEqual(loaded.instruments[0].incompatible_operation_ids, ())
        self.assertEqual(loaded.instruments[0].unresolved_operation_ids, ())
        self.assertEqual(loaded.steps[0].arguments, ())
        self.assertEqual(loaded.steps[0].result_name, "")

    def test_version_2_migrates_with_safe_operation_defaults(self) -> None:
        payload = self._valid_payload()
        payload["schema_version"] = 2
        for instrument in payload["instruments"]:
            instrument["compatibility_status"] = "user_compatible"
            instrument["compatible_capability_ids"] = []
        for step in payload["steps"]:
            if step["type"] == "feature":
                step["arguments"] = {}
                step["result_name"] = ""

        loaded = self._load_payload(payload)

        self.assertEqual(loaded.schema_version, 2)
        self.assertEqual(loaded.instruments[0].compatibility_status, "")
        self.assertEqual(loaded.instruments[0].compatible_operation_ids, ())
        self.assertEqual(loaded.instruments[0].incompatible_operation_ids, ())
        self.assertEqual(loaded.instruments[0].unresolved_operation_ids, ())
        self.assertEqual(loaded.steps[0].arguments, ())
        self.assertEqual(loaded.steps[0].result_name, "")

    def test_version_5_fixed_steps_remain_fixed(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory, "version5.json")
            save_routine(path, self.instruments, self.steps)
            payload = json.loads(path.read_text(encoding="utf-8"))
            payload["schema_version"] = 5
            for step in payload["steps"]:
                step.pop("plan_bindings", None)
            path.write_text(
                json.dumps(payload, ensure_ascii=False),
                encoding="utf-8",
            )

            loaded = load_routine(
                path,
                trusted_instruments=self.instruments,
            )

        self.assertEqual(loaded.schema_version, 5)
        self.assertEqual(loaded.steps, self.steps)
        for step in loaded.steps:
            if isinstance(step, SelectedFeature):
                self.assertEqual(step.plan_bindings, ())

    def test_version_6_preserves_operation_validation_identity_and_steps(
        self,
    ) -> None:
        instrument = SelectedInstrument(
            resource="TCPIP0::192.0.2.30::inst0::INSTR",
            category=DeviceCategory.SPECTRUM_ANALYZER,
            manufacturer="Example",
            model="FSV30",
            serial="A-30",
            firmware="3.60",
            raw_idn="Example,FSV30,A-30,3.60",
            profile_id="rs_fsv_fsva",
            compatibility_status="hardware_validated_partial",
            compatible_capability_ids=("analyzer.frequency.center",),
            compatible_operation_ids=("analyzer.frequency.center::set",),
            incompatible_operation_ids=("analyzer.frequency.center::query",),
            unresolved_operation_ids=("analyzer.frequency.span::set",),
            validation_catalog_fingerprint="a" * 64,
            option_response="B25,K54",
            option_state="queried",
        )
        feature = next(
            item
            for item in features_for(
                instrument.category,
                instrument.profile_id,
                instrument.compatible_capability_ids,
                instrument.compatibility_status,
                instrument.compatible_operation_ids,
            )
            if item.capability_id == "analyzer.frequency.center"
            and item.operation == "set"
        )
        step = select_feature(
            instrument,
            feature.feature_id,
            arguments={"value": "1000000000"},
            result_name="",
        )

        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory, "profile-compatible.json")
            save_routine(path, (instrument,), (step,))
            loaded = load_routine(
                path,
                trusted_instruments=(instrument,),
            )

        self.assertEqual(loaded.schema_version, 6)
        self.assertEqual(loaded.instruments, (instrument,))
        self.assertEqual(loaded.steps, (step,))

    def test_version_6_round_trip_preserves_plan_bindings_and_plan_delay(
        self,
    ) -> None:
        analyzer = SelectedInstrument(
            resource="DEMO::FSV30::INSTR",
            category=DeviceCategory.SPECTRUM_ANALYZER,
            manufacturer="Rohde&Schwarz",
            model="FSV30",
            serial="SA-01",
            firmware="3.60",
            raw_idn="Rohde&Schwarz,FSV30,SA-01,3.60",
            profile_id="rs_fsv_fsva",
        )
        generator = SelectedInstrument(
            resource="DEMO::SMB100A::INSTR",
            category=DeviceCategory.SIGNAL_GENERATOR,
            manufacturer="Rohde&Schwarz",
            model="SMB100A",
            serial="SG-01",
            firmware="4.10",
            raw_idn="Rohde&Schwarz,SMB100A,SG-01,4.10",
            profile_id="rs_smb100a",
        )
        bound = select_feature(
            analyzer,
            "spectrum_analyzer.cap.analyzer.frequency.center.set",
            plan_bindings=(
                PlanArgumentBinding("value", "center_frequency_hz"),
            ),
        )
        plan_delay = create_plan_bound_delay(generator)

        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory, "bound-routine.json")
            save_routine(
                path,
                (analyzer, generator),
                (bound, plan_delay),
            )
            payload = json.loads(path.read_text(encoding="utf-8"))
            loaded = load_routine(
                path,
                trusted_instruments=(analyzer, generator),
            )

        self.assertEqual(
            payload["steps"][0]["plan_bindings"],
            {"value": "center_frequency_hz"},
        )
        self.assertEqual(
            [step["type"] for step in payload["steps"]],
            ["feature", "plan_bound_delay"],
        )
        self.assertEqual(loaded.steps, (bound, plan_delay))
        self.assertIsInstance(loaded.steps[1], PlanBoundDelayStep)

    def test_version_4_uses_current_trusted_raw_identity(self) -> None:
        instrument = SelectedInstrument(
            resource="TCPIP0::192.0.2.32::inst0::INSTR",
            category=DeviceCategory.SPECTRUM_ANALYZER,
            manufacturer="Rohde&Schwarz",
            model="FSV30",
            serial="A-32",
            firmware="3.60",
            raw_idn="Rohde&Schwarz,FSV30,A-32,3.60",
            profile_id="rs_fsv_fsva",
            compatibility_status="hardware_validated_partial",
            compatible_capability_ids=("analyzer.frequency.center",),
            compatible_operation_ids=("analyzer.frequency.center::set",),
            validation_catalog_fingerprint="c" * 64,
            option_response="K54",
            option_state="queried",
        )
        feature = next(
            item
            for item in features_for(
                instrument.category,
                instrument.profile_id,
                instrument.compatible_capability_ids,
                instrument.compatibility_status,
                instrument.compatible_operation_ids,
            )
            if item.capability_id == "analyzer.frequency.center"
            and item.operation == "set"
        )
        step = select_feature(
            instrument,
            feature.feature_id,
            arguments={"value": "1000000000"},
        )
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory, "version4.json")
            save_routine(path, (instrument,), (step,))
            payload = json.loads(path.read_text(encoding="utf-8"))
            payload["schema_version"] = 4
            payload["instruments"][0].pop("raw_idn")
            payload["instruments"][0].pop("option_state")
            for saved_step in payload["steps"]:
                saved_step.pop("plan_bindings", None)
            path.write_text(
                json.dumps(payload, ensure_ascii=False),
                encoding="utf-8",
            )
            loaded = load_routine(
                path,
                trusted_instruments=(instrument,),
            )

        self.assertEqual(loaded.schema_version, 4)
        self.assertEqual(loaded.instruments[0].raw_idn, instrument.raw_idn)
        self.assertEqual(loaded.instruments[0].option_state, "queried")

    def test_saved_validation_allowlist_is_never_trusted_by_itself(
        self,
    ) -> None:
        instrument = SelectedInstrument(
            resource="TCPIP0::192.0.2.31::inst0::INSTR",
            category=DeviceCategory.SPECTRUM_ANALYZER,
            manufacturer="Rohde&Schwarz",
            model="FSV30",
            serial="A-31",
            firmware="3.60",
            raw_idn="Rohde&Schwarz,FSV30,A-31,3.60",
            profile_id="rs_fsv_fsva",
            compatibility_status="hardware_validated_partial",
            compatible_capability_ids=("analyzer.frequency.center",),
            compatible_operation_ids=("analyzer.frequency.center::set",),
            validation_catalog_fingerprint="b" * 64,
            option_response="K54",
            option_state="queried",
        )
        center = next(
            item
            for item in features_for(
                instrument.category,
                instrument.profile_id,
                compatibility_status="demo_catalog_preview",
            )
            if item.capability_id == "analyzer.frequency.center"
            and item.operation == "set"
        )
        span = next(
            item
            for item in features_for(
                instrument.category,
                instrument.profile_id,
                compatibility_status="demo_catalog_preview",
            )
            if item.capability_id == "analyzer.frequency.span"
            and item.operation == "set"
        )
        step = select_feature(
            instrument,
            center.feature_id,
            arguments={"value": "1000000000"},
        )
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory, "tampered-allowlist.json")
            save_routine(path, (instrument,), (step,))
            payload = json.loads(path.read_text(encoding="utf-8"))
            payload["instruments"][0]["compatible_operation_ids"] = [
                "analyzer.frequency.span::set"
            ]
            payload["steps"][0]["feature_id"] = span.feature_id
            payload["steps"][0]["arguments"] = {"value": "1000000"}
            path.write_text(
                json.dumps(payload, ensure_ascii=False),
                encoding="utf-8",
            )

            requirements = load_routine_requirements(path)
            self.assertEqual(
                requirements.instruments[0].serial,
                instrument.serial,
            )
            with self.assertRaises(RoutineStorageError):
                load_routine(path)
            with self.assertRaises(RoutineStorageError):
                load_routine(
                    path,
                    trusted_instruments=(instrument,),
                )

    def test_empty_preview_allowlist_cannot_bypass_current_validation(
        self,
    ) -> None:
        preview = SelectedInstrument(
            resource="DEMO::FSV30::INSTR",
            category=DeviceCategory.SPECTRUM_ANALYZER,
            manufacturer="Rohde&Schwarz",
            model="FSV30",
            serial="DEMO-30",
            firmware="3.60",
            raw_idn="Rohde&Schwarz,FSV30,DEMO-30,3.60",
            profile_id="rs_fsv_fsva",
            compatibility_status="demo_catalog_preview",
            compatible_capability_ids=(),
            compatible_operation_ids=(),
            validation_catalog_fingerprint="",
            option_state="unsupported",
        )
        center = next(
            item
            for item in features_for(
                preview.category,
                preview.profile_id,
                compatibility_status="demo_catalog_preview",
            )
            if item.capability_id == "analyzer.frequency.center"
            and item.operation == "set"
        )
        step = select_feature(
            preview,
            center.feature_id,
            arguments={"value": "1000000000"},
        )

        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory, "preview-empty-allowlist.json")
            save_routine(path, (preview,), (step,))

            with self.assertRaises(RoutineStorageError):
                load_routine(path)
            with self.assertRaises(RoutineStorageError):
                load_routine(path, trusted_instruments=())

    def test_schema_downgrade_cannot_restore_unvalidated_command_feature(
        self,
    ) -> None:
        preview = SelectedInstrument(
            resource="TCPIP0::192.0.2.33::inst0::INSTR",
            category=DeviceCategory.SPECTRUM_ANALYZER,
            manufacturer="Rohde&Schwarz",
            model="FSV30",
            serial="A-33",
            firmware="3.60",
            raw_idn="Rohde&Schwarz,FSV30,A-33,3.60",
            profile_id="rs_fsv_fsva",
            compatibility_status="demo_catalog_preview",
            option_state="unsupported",
        )
        reset = next(
            item
            for item in features_for(
                preview.category,
                preview.profile_id,
                compatibility_status="demo_catalog_preview",
            )
            if item.capability_id == "system.reset"
            and item.operation == "execute"
        )
        step = select_feature(preview, reset.feature_id)

        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory, "downgraded-v1.json")
            save_routine(path, (preview,), (step,))
            payload = json.loads(path.read_text(encoding="utf-8"))
            payload["schema_version"] = 1
            payload["instruments"][0] = {
                key: payload["instruments"][0][key]
                for key in (
                    "resource",
                    "category",
                    "manufacturer",
                    "model",
                    "serial",
                    "profile_id",
                )
            }
            payload["steps"][0] = {
                key: payload["steps"][0][key]
                for key in ("type", "instrument_resource", "feature_id")
            }
            path.write_text(
                json.dumps(payload, ensure_ascii=False),
                encoding="utf-8",
            )

            with self.assertRaises(RoutineStorageError):
                load_routine(path)
            with self.assertRaises(RoutineStorageError):
                load_routine(path, trusted_instruments=())

    def test_generic_fallback_cannot_bypass_operation_allowlist(
        self,
    ) -> None:
        saved = SelectedInstrument(
            resource="USB0::0x1234::0x5678::SG-34::INSTR",
            category=DeviceCategory.SIGNAL_GENERATOR,
            manufacturer="Rohde&Schwarz",
            model="SMB100A",
            serial="SG-34",
            firmware="4.70",
            raw_idn="Rohde&Schwarz,SMB100A,SG-34,4.70",
            profile_id="rs_smb100a",
            compatibility_status="",
            option_state="unsupported",
        )
        current = SelectedInstrument(
            resource=saved.resource,
            category=saved.category,
            manufacturer=saved.manufacturer,
            model=saved.model,
            serial=saved.serial,
            firmware=saved.firmware,
            raw_idn=saved.raw_idn,
            profile_id=saved.profile_id,
            compatibility_status="hardware_validated_partial",
            compatible_capability_ids=("generator.frequency",),
            compatible_operation_ids=("generator.frequency::query",),
            validation_catalog_fingerprint="d" * 64,
            option_state="unsupported",
        )
        fallback_step = select_feature(
            saved,
            "signal_generator.output_on",
        )

        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory, "fallback-bypass.json")
            save_routine(path, (saved,), (fallback_step,))

            with self.assertRaises(RoutineStorageError):
                load_routine(path)
            with self.assertRaises(RoutineStorageError):
                load_routine(path, trusted_instruments=())
            with self.assertRaises(RoutineStorageError):
                load_routine(
                    path,
                    trusted_instruments=(current,),
                )

    def test_version_3_migrates_with_empty_firmware_option_defaults(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory, "version4.json")
            save_routine(path, self.instruments, self.steps)
            payload = json.loads(path.read_text(encoding="utf-8"))
            payload["schema_version"] = 3
            payload["instruments"][0]["compatibility_status"] = (
                "hardware_validated_partial"
            )
            payload["instruments"][0]["compatible_capability_ids"] = [
                "analyzer.frequency.center"
            ]
            payload["instruments"][0]["compatible_operation_ids"] = [
                "analyzer.frequency.center::set"
            ]
            for instrument in payload["instruments"]:
                instrument.pop("firmware")
                instrument.pop("validation_catalog_fingerprint")
                instrument.pop("option_response")
                instrument.pop("raw_idn")
                instrument.pop("option_state")
            path.write_text(
                json.dumps(payload, ensure_ascii=False),
                encoding="utf-8",
            )
            loaded = load_routine_requirements(path)

        self.assertEqual(loaded.schema_version, 3)
        self.assertEqual(
            loaded.instruments[0].compatibility_status,
            "candidate_pack_unvalidated",
        )
        self.assertEqual(
            loaded.instruments[0].compatible_operation_ids,
            (),
        )
        self.assertIn(
            "analyzer.frequency.center::set",
            loaded.instruments[0].unresolved_operation_ids,
        )
        self.assertEqual(loaded.instruments[0].firmware, "")
        self.assertEqual(
            loaded.instruments[0].validation_catalog_fingerprint,
            "",
        )
        self.assertEqual(loaded.instruments[0].option_response, "")

    def test_unknown_schema_step_feature_and_category_are_rejected(self) -> None:
        base = self._valid_payload()
        mutations = {
            "schema": lambda payload: payload.update(schema_version=7),
            "step": lambda payload: payload["steps"][0].update(
                type="run_arbitrary_command"
            ),
            "feature": lambda payload: payload["steps"][0].update(
                feature_id="spectrum_analyzer.not_registered"
            ),
            "category": lambda payload: payload["instruments"][0].update(
                category="imaginary_instrument"
            ),
        }

        for name, mutate in mutations.items():
            with self.subTest(name=name):
                payload = json.loads(json.dumps(base))
                mutate(payload)
                with self.assertRaises(RoutineStorageError):
                    self._load_payload(payload)

    def test_wrong_json_types_and_invalid_durations_are_rejected(self) -> None:
        cases = (
            ("schema bool", ("schema_version",), True),
            ("instruments object", ("instruments",), {}),
            (
                "resource number",
                ("instruments", 0, "resource"),
                123,
            ),
            ("seconds string", ("steps", 1, "seconds"), "0.5"),
            ("seconds bool", ("steps", 1, "seconds"), True),
            ("seconds too short", ("steps", 1, "seconds"), 0),
            ("seconds too long", ("steps", 1, "seconds"), 3601),
            ("seconds overflow", ("steps", 1, "seconds"), 10**1000),
            (
                "timeout string",
                ("steps", 2, "timeout_seconds"),
                "30",
            ),
        )

        for name, path, value in cases:
            with self.subTest(name=name):
                payload = json.loads(json.dumps(self._valid_payload()))
                self._set_nested(payload, path, value)
                with self.assertRaises(RoutineStorageError):
                    self._load_payload(payload)

    def test_unknown_missing_and_duplicate_json_properties_are_rejected(self) -> None:
        payload = self._valid_payload()
        payload["raw_scpi"] = "*RST"
        with self.assertRaises(RoutineStorageError):
            self._load_payload(payload)

        payload = self._valid_payload()
        del payload["instruments"][0]["profile_id"]
        with self.assertRaises(RoutineStorageError):
            self._load_payload(payload)

        duplicate_key_json = (
            '{"schema_version":1,"schema_version":1,'
            '"instruments":[],"steps":[]}'
        )
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory, "duplicate.json")
            path.write_text(duplicate_key_json, encoding="utf-8")
            with self.assertRaises(RoutineStorageError):
                load_routine(path)

    def test_non_finite_numbers_and_unregistered_equipment_are_rejected(self) -> None:
        non_finite_json = (
            '{"schema_version":1,"instruments":[],"steps":'
            '[{"type":"delay","seconds":NaN}]}'
        )
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory, "nan.json")
            path.write_text(non_finite_json, encoding="utf-8")
            with self.assertRaises(RoutineStorageError):
                load_routine(path)

        payload = self._valid_payload()
        payload["steps"][0]["instrument_resource"] = "MISSING::INSTR"
        with self.assertRaises(RoutineStorageError):
            self._load_payload(payload)

    def test_save_rejects_unknown_steps_features_and_instrument_mismatch(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory, "routine.json")
            missing_instrument_step = SelectedFeature(
                instrument=self.generator,
                feature_id="signal_generator.output_off",
            )
            with self.assertRaises(RoutineStorageError):
                save_routine(path, (self.analyzer,), (missing_instrument_step,))

            unknown_feature = SelectedFeature(
                instrument=self.analyzer,
                feature_id="spectrum_analyzer.not_registered",
            )
            with self.assertRaises(RoutineStorageError):
                save_routine(path, (self.analyzer,), (unknown_feature,))

            with self.assertRaises(RoutineStorageError):
                save_routine(
                    path,
                    (self.analyzer,),
                    (object(),),  # type: ignore[arg-type]
                )

    def _valid_payload(self) -> dict[str, object]:
        return {
            "schema_version": 1,
            "instruments": [
                {
                    "resource": self.analyzer.resource,
                    "category": self.analyzer.category.value,
                    "manufacturer": self.analyzer.manufacturer,
                    "model": self.analyzer.model,
                    "serial": self.analyzer.serial,
                    "profile_id": self.analyzer.profile_id,
                },
                {
                    "resource": self.generator.resource,
                    "category": self.generator.category.value,
                    "manufacturer": self.generator.manufacturer,
                    "model": self.generator.model,
                    "serial": self.generator.serial,
                    "profile_id": self.generator.profile_id,
                },
            ],
            "steps": [
                {
                    "type": "feature",
                    "instrument_resource": self.analyzer.resource,
                    "feature_id": "spectrum_analyzer.set_rbw",
                },
                {
                    "type": "delay",
                    "seconds": 0.5,
                },
                {
                    "type": "wait_for_completion",
                    "instrument_resource": self.analyzer.resource,
                    "timeout_seconds": 30,
                },
            ],
        }

    def _load_payload(self, payload: object) -> RoutineFile:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory, "routine.json")
            path.write_text(
                json.dumps(payload, ensure_ascii=False),
                encoding="utf-8",
            )
            return load_routine(
                path,
                trusted_instruments=self.instruments,
            )

    @staticmethod
    def _set_nested(
        payload: object,
        path: tuple[object, ...],
        value: object,
    ) -> None:
        target = payload
        for part in path[:-1]:
            target = target[part]  # type: ignore[index]
        target[path[-1]] = value  # type: ignore[index]


if __name__ == "__main__":
    unittest.main()
