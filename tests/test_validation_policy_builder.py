from __future__ import annotations

import unittest

from scpi_automation.identity import (
    CatalogCapability,
    CatalogOperation,
    DeviceCategory,
    InstrumentProfile,
    profile_by_id,
)
from scpi_automation.validation import (
    build_safe_validation_policy,
    operation_id,
)


def _capability(
    capability_id: str,
    operations: tuple[CatalogOperation, ...],
    *,
    risk: str = "low",
    parameters: tuple[dict[str, object], ...] = (),
) -> CatalogCapability:
    return CatalogCapability(
        capability_id=capability_id,
        label_ko=capability_id,
        group="test",
        risk_level=risk,
        verification="profile_required",
        operations=operations,
        parameters=parameters,
    )


def _profile(*capabilities: CatalogCapability) -> InstrumentProfile:
    return InstrumentProfile(
        profile_id="policy_test",
        manufacturer="Test",
        model_family="Safe",
        models=("SAFE",),
        instrument_class="signal_and_spectrum_analyzer",
        category=DeviceCategory.SPECTRUM_ANALYZER,
        idn_patterns=(),
        verification_status="candidate",
        hardware_verified=False,
        capabilities=capabilities,
    )


class SafeValidationPolicyBuilderTests(unittest.TestCase):
    def test_populates_selectors_off_enum_and_safe_numeric_probes(self) -> None:
        state = _capability(
            "trace.state",
            (
                CatalogOperation(
                    "set",
                    "DISP:TRAC{trace}:STAT {state}",
                ),
                CatalogOperation(
                    "query",
                    "DISP:TRAC{trace}:STAT?",
                    "boolean",
                ),
            ),
            parameters=(
                {
                    "name": "trace",
                    "type": "integer",
                    "minimum": 1,
                    "maximum": 6,
                },
                {
                    "name": "state",
                    "type": "enum",
                    "choices": ["ON", "OFF"],
                },
            ),
        )
        span = _capability(
            "analyzer.span",
            (
                CatalogOperation("set", "FREQ:SPAN {value}"),
                CatalogOperation("query", "FREQ:SPAN?", "float"),
            ),
            parameters=(
                {
                    "name": "value",
                    "type": "float",
                    "minimum": 10,
                    "maximum": 1000,
                },
            ),
        )
        mode = _capability(
            "trace.mode",
            (
                CatalogOperation("set", "TRAC:MODE {mode}"),
                CatalogOperation("query", "TRAC:MODE?", "string"),
            ),
            parameters=(
                {
                    "name": "mode",
                    "type": "enum",
                    "choices": ["WRIT", "MAXH"],
                },
            ),
        )

        built = build_safe_validation_policy(_profile(state, span, mode))

        self.assertEqual(
            built.operation_arguments["trace.state::query"],
            {"trace": 1},
        )
        self.assertEqual(
            built.operation_arguments["trace.state::set"],
            {"trace": 1, "state": "OFF"},
        )
        self.assertEqual(
            built.operation_arguments["analyzer.span::set"],
            {"value": 10},
        )
        self.assertNotIn(
            "trace.mode::set",
            built.operation_arguments,
        )
        self.assertIn(
            "complete rollback",
            built.reason_for("trace.mode::set"),
        )
        self.assertFalse(built.policy.approved_hazardous_operation_ids)
        self.assertFalse(built.policy.skipped_operation_ids)

    def test_queries_only_infer_numeric_selectors(self) -> None:
        selected = _capability(
            "measurement.selected",
            (
                CatalogOperation(
                    "query",
                    "MEAS{channel}:VAL?",
                    "float",
                ),
            ),
            parameters=(
                {
                    "name": "channel",
                    "type": "integer",
                    "minimum": 1,
                    "maximum": 4,
                },
            ),
        )
        named = _capability(
            "measurement.named",
            (
                CatalogOperation(
                    "query",
                    "SENS:{sense_function}:NPLC?",
                    "float",
                ),
            ),
            parameters=(
                {"name": "sense_function", "type": "string"},
            ),
        )

        built = build_safe_validation_policy(_profile(selected, named))

        self.assertEqual(
            built.operation_arguments["measurement.selected::query"],
            {"channel": 1},
        )
        reason = built.reason_for("measurement.named::query")
        self.assertIn("not a numeric selector", reason)
        self.assertNotIn(
            "measurement.named::query",
            built.automatic_operation_ids,
        )

    def test_blocks_all_explicitly_dangerous_write_classes(self) -> None:
        cases = (
            _capability(
                "source.power",
                (
                    CatalogOperation("set", "SOUR:POW {value}"),
                    CatalogOperation("query", "SOUR:POW?", "float"),
                ),
                risk="high",
                parameters=(
                    {
                        "name": "value",
                        "type": "float",
                        "minimum": -120,
                        "maximum": 30,
                    },
                ),
            ),
            _capability(
                "output.state",
                (
                    CatalogOperation("set", "OUTP {state}"),
                    CatalogOperation("query", "OUTP?", "boolean"),
                ),
                parameters=(
                    {"name": "state", "type": "boolean"},
                ),
            ),
            _capability(
                "trace.file",
                (
                    CatalogOperation(
                        "set",
                        'MMEM:STOR "{filename}"',
                    ),
                    CatalogOperation("query", "MMEM:CAT?", "string"),
                ),
                parameters=(
                    {"name": "filename", "type": "string"},
                ),
            ),
            _capability(
                "trace.binary",
                (
                    CatalogOperation(
                        "query",
                        "TRAC:DATA?",
                        "float_array",
                        binary=True,
                    ),
                ),
            ),
            _capability(
                "system.reset",
                (CatalogOperation("execute", "*RST"),),
            ),
        )

        built = build_safe_validation_policy(_profile(*cases))

        self.assertIn("High-risk", built.reason_for("source.power::set"))
        self.assertIn("power writes", built.reason_for("source.power::set"))
        self.assertIn(
            "Output-enable",
            built.reason_for("output.state::set"),
        )
        self.assertIn("File", built.reason_for("trace.file::set"))
        self.assertIn("Binary", built.reason_for("trace.binary::query"))
        self.assertIn("Reset", built.reason_for("system.reset::execute"))
        self.assertIn("Execute", built.reason_for("system.reset::execute"))
        for operation_id_value in built.manual_reasons:
            self.assertNotIn(
                operation_id_value,
                built.policy.approved_hazardous_operation_ids,
            )

    def test_trace_mode_side_effect_and_arbitrary_string_are_manual(
        self,
    ) -> None:
        fixed_mode = _capability(
            "trace.mode.max_hold",
            (
                CatalogOperation("set", "DISP:TRAC{trace}:MODE MAXH"),
                CatalogOperation(
                    "query",
                    "DISP:TRAC{trace}:MODE?",
                    "string",
                ),
            ),
            parameters=(
                {
                    "name": "trace",
                    "type": "integer",
                    "minimum": 1,
                    "maximum": 6,
                },
            ),
        )
        arbitrary = _capability(
            "arb.select",
            (
                CatalogOperation("set", "ARB:SEL {name}"),
                CatalogOperation("query", "ARB:SEL?", "string"),
            ),
            parameters=({"name": "name", "type": "string"},),
        )

        built = build_safe_validation_policy(
            _profile(fixed_mode, arbitrary)
        )

        self.assertEqual(
            built.operation_arguments["trace.mode.max_hold::query"],
            {"trace": 1},
        )
        self.assertNotIn(
            "trace.mode.max_hold::set",
            built.operation_arguments,
        )
        self.assertIn(
            "complete rollback",
            built.reason_for("trace.mode.max_hold::set"),
        )
        self.assertIn(
            "Arbitrary string",
            built.reason_for("arb.select::set"),
        )

    def test_real_fsv_profile_has_safe_driver_backed_probes_only(
        self,
    ) -> None:
        profile = profile_by_id("rs_fsv_fsva")
        self.assertIsNotNone(profile)
        assert profile is not None

        built = build_safe_validation_policy(profile)

        self.assertEqual(
            built.operation_arguments[
                operation_id("analyzer.frequency.center", "set")
            ],
            {"value": 1_000_000.0},
        )
        self.assertIn(
            "Execute",
            built.reason_for("measurement.initiate::execute"),
        )
        self.assertIn(
            "Reset",
            built.reason_for("system.reset::execute"),
        )
        self.assertEqual(
            built.policy.approved_hazardous_operation_ids,
            frozenset(),
        )


if __name__ == "__main__":
    unittest.main()
