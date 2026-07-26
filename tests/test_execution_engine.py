from __future__ import annotations

import threading
import time
import unittest
from contextlib import contextmanager

from scpi_automation.execution import ExecutionStatus, run_execution
from scpi_automation.identity import DeviceCategory, profile_by_id
from scpi_automation.planning import SpectrumPlanItem
from scpi_automation.routine import (
    DelayStep,
    PlanArgumentBinding,
    SelectedInstrument,
    select_feature,
    wait_for_completion,
)
from scpi_automation.validation import profile_fingerprint


class FakeSession:
    def __init__(
        self,
        responses: dict[str, str],
        *,
        failing_writes: tuple[str, ...] = (),
    ) -> None:
        self.timeout = 2_000
        self.responses = dict(responses)
        self.failing_writes = set(failing_writes)
        self.queries: list[str] = []
        self.writes: list[str] = []

    def query(self, command: str) -> str:
        self.queries.append(command)
        if command not in self.responses:
            raise AssertionError(f"Unexpected query: {command}")
        return self.responses[command]

    def write(self, command: str):
        self.writes.append(command)
        if command in self.failing_writes:
            raise RuntimeError("simulated VISA write failure")
        return len(command)


class SessionFactory:
    def __init__(self, sessions: dict[str, FakeSession]) -> None:
        self.sessions = sessions
        self.opened: list[tuple[str, str, int]] = []

    @contextmanager
    def __call__(
        self,
        *,
        resource: str,
        backend: str,
        timeout_ms: int,
    ):
        self.opened.append((resource, backend, timeout_ms))
        yield self.sessions[resource]


def fsv_instrument(
    *,
    fingerprint: str | None = None,
    option_state: str = "unsupported",
    operation_ids: tuple[str, ...] = (
        "analyzer.frequency.center::set",
        "analyzer.frequency.center::query",
    ),
) -> SelectedInstrument:
    profile = profile_by_id("rs_fsv_fsva")
    assert profile is not None
    return SelectedInstrument(
        resource="TCPIP0::192.0.2.30::inst0::INSTR",
        category=DeviceCategory.SPECTRUM_ANALYZER,
        manufacturer="Rohde&Schwarz",
        model="FSV30",
        serial="12345",
        firmware="3.60",
        raw_idn="Rohde&Schwarz,FSV30,12345,3.60",
        profile_id=profile.profile_id,
        compatibility_status="hardware_validated_partial",
        compatible_capability_ids=("analyzer.frequency.center",),
        compatible_operation_ids=operation_ids,
        validation_catalog_fingerprint=(
            fingerprint
            if fingerprint is not None
            else profile_fingerprint(profile)
        ),
        option_response="K7" if option_state == "queried" else "",
        option_state=option_state,
    )


def fsl_instrument() -> SelectedInstrument:
    profile = profile_by_id("rs_fsl")
    assert profile is not None
    return SelectedInstrument(
        resource="TCPIP0::192.0.2.31::inst0::INSTR",
        category=DeviceCategory.SPECTRUM_ANALYZER,
        manufacturer="Rohde&Schwarz",
        model="FSL18",
        serial="54321",
        firmware="2.00",
        raw_idn="Rohde&Schwarz,FSL18,54321,2.00",
        profile_id=profile.profile_id,
        compatibility_status="hardware_validated_partial",
        compatible_capability_ids=("trace.read",),
        compatible_operation_ids=("trace.read::query",),
        validation_catalog_fingerprint=profile_fingerprint(profile),
        option_state="unsupported",
    )


def smb_instrument() -> SelectedInstrument:
    profile = profile_by_id("rs_smb100a")
    assert profile is not None
    return SelectedInstrument(
        resource="TCPIP0::192.0.2.40::inst0::INSTR",
        category=DeviceCategory.SIGNAL_GENERATOR,
        manufacturer="Rohde&Schwarz",
        model="SMB100A",
        serial="67890",
        firmware="4.10",
        raw_idn="Rohde&Schwarz,SMB100A,67890,4.10",
        profile_id=profile.profile_id,
        compatibility_status="hardware_validated_partial",
        compatible_capability_ids=("source.frequency", "rf.output.state"),
        compatible_operation_ids=(
            "source.frequency::set",
            "rf.output.state::set",
            "rf.output.state::query",
        ),
        validation_catalog_fingerprint=profile_fingerprint(profile),
        option_state="unsupported",
    )


class ExecutionEngineTests(unittest.TestCase):
    def test_live_plan_cases_apply_values_and_tag_measurements(self) -> None:
        instrument = fsv_instrument()
        set_center = select_feature(
            instrument,
            "spectrum_analyzer.cap.analyzer.frequency.center.set",
            plan_bindings=(
                PlanArgumentBinding("value", "center_frequency_hz"),
            ),
        )
        query_center = select_feature(
            instrument,
            "spectrum_analyzer.cap.analyzer.frequency.center.query",
            result_name="Center 확인",
        )
        plans = tuple(
            SpectrumPlanItem(
                instrument=instrument,
                center_frequency_hz=frequency,
                span_hz=100_000_000,
                rbw_hz=100_000,
                vbw_hz=100_000,
                reference_level_dbm=0,
                case_id=f"case-{index}",
                case_name=f"시험 {index:02d}",
            )
            for index, frequency in enumerate(
                (1_000_000_000, 2_000_000_000),
                start=1,
            )
        )
        session = FakeSession(
            {
                "*IDN?": instrument.raw_idn,
                "*OPT?": "K7",
                ":FREQ:CENT?": "1500000000",
            }
        )
        factory = SessionFactory({instrument.resource: session})

        result = run_execution(
            instruments=(instrument,),
            routine_steps=(set_center, query_center),
            plan_items=plans,
            dry_run=False,
            operator_confirmed=True,
            session_factory=factory,
        )

        self.assertEqual(result.status, ExecutionStatus.COMPLETED)
        self.assertEqual(
            session.writes,
            [":FREQ:CENT 1000000000", ":FREQ:CENT 2000000000"],
        )
        self.assertEqual(
            [measurement.case_id for measurement in result.measurements],
            ["case-1", "case-2"],
        )
        self.assertEqual(
            [measurement.template_step_index for measurement in result.measurements],
            [2, 2],
        )

    def test_dry_run_renders_commands_without_opening_visa(self) -> None:
        instrument = fsv_instrument()
        set_center = select_feature(
            instrument,
            "spectrum_analyzer.cap.analyzer.frequency.center.set",
            arguments={"value": "1000000000"},
        )
        factory = SessionFactory({})

        result = run_execution(
            instruments=(instrument,),
            routine_steps=(set_center, DelayStep(0.1)),
            dry_run=True,
            session_factory=factory,
        )

        self.assertEqual(result.status, ExecutionStatus.COMPLETED)
        self.assertTrue(result.dry_run)
        self.assertEqual(factory.opened, [])
        self.assertEqual(result.step_records[0].command, ":FREQ:CENT 1000000000")
        self.assertEqual(result.step_records[1].operation, "delay")

    def test_live_query_records_raw_and_typed_measurement(self) -> None:
        instrument = fsv_instrument()
        query_center = select_feature(
            instrument,
            "spectrum_analyzer.cap.analyzer.frequency.center.query",
            result_name="중심 주파수 확인",
        )
        session = FakeSession(
            {
                "*IDN?": instrument.raw_idn,
                "*OPT?": "K7",
                ":FREQ:CENT?": "1500000000",
            }
        )
        factory = SessionFactory({instrument.resource: session})

        result = run_execution(
            instruments=(instrument,),
            routine_steps=(query_center,),
            dry_run=False,
            backend="@py",
            timeout_ms=3_000,
            session_factory=factory,
            operator_confirmed=True,
        )

        self.assertEqual(result.status, ExecutionStatus.COMPLETED)
        self.assertEqual(
            factory.opened,
            [(instrument.resource, "@py", 3_000)],
        )
        self.assertEqual(session.queries, ["*IDN?", ":FREQ:CENT?"])
        self.assertEqual(len(result.measurements), 1)
        self.assertEqual(result.measurements[0].raw_response, "1500000000")
        self.assertEqual(result.measurements[0].parsed_value, 1_500_000_000.0)
        self.assertEqual(result.measurements[0].unit, "Hz")
        self.assertEqual(
            result.measurements[0].result_name,
            "중심 주파수 확인",
        )
        measurement_event = next(
            event
            for event in result.events
            if event.kind == "measurement_recorded"
        )
        self.assertEqual(measurement_event.parsed_value, 1_500_000_000.0)
        self.assertEqual(measurement_event.capability_id, "analyzer.frequency.center")
        self.assertEqual(measurement_event.unit, "Hz")

    def test_large_array_is_not_duplicated_in_persistent_event_or_step(self) -> None:
        instrument = fsl_instrument()
        read_trace = select_feature(
            instrument,
            "spectrum_analyzer.cap.trace.read.query",
            arguments={"trace": "1"},
            result_name="트레이스",
        )
        raw_trace = ",".join(str(value) for value in range(1_000))
        session = FakeSession(
            {
                "*IDN?": instrument.raw_idn,
                "TRAC1? TRACE1": raw_trace,
            }
        )
        callback_events = []

        result = run_execution(
            instruments=(instrument,),
            routine_steps=(read_trace,),
            dry_run=False,
            session_factory=SessionFactory(
                {instrument.resource: session}
            ),
            event_callback=callback_events.append,
            operator_confirmed=True,
        )

        self.assertEqual(result.status, ExecutionStatus.COMPLETED)
        self.assertEqual(len(result.measurements[0].parsed_value), 1_000)
        stored_event = next(
            event
            for event in result.events
            if event.kind == "measurement_recorded"
        )
        live_event = next(
            event
            for event in callback_events
            if event.kind == "measurement_recorded"
        )
        self.assertIsNone(stored_event.parsed_value)
        self.assertNotEqual(stored_event.response, raw_trace)
        self.assertIn(stored_event.measurement_id, stored_event.response)
        self.assertEqual(live_event.parsed_value, result.measurements[0].parsed_value)
        self.assertEqual(live_event.response, raw_trace)
        self.assertNotEqual(result.step_records[0].response, raw_trace)
        self.assertEqual(
            result.step_records[0].measurement_id,
            result.measurements[0].measurement_id,
        )

    def test_catalog_fingerprint_mismatch_blocks_before_visa(self) -> None:
        instrument = fsv_instrument(fingerprint="0" * 64)
        query_center = select_feature(
            instrument,
            "spectrum_analyzer.cap.analyzer.frequency.center.query",
        )
        factory = SessionFactory({})

        result = run_execution(
            instruments=(instrument,),
            routine_steps=(query_center,),
            dry_run=False,
            session_factory=factory,
            operator_confirmed=True,
        )

        self.assertEqual(result.status, ExecutionStatus.FAILED)
        self.assertEqual(factory.opened, [])
        self.assertIn("후보 명령팩이 바뀌었습니다", result.stop_reason)

    def test_actual_execution_requires_operator_confirmation(self) -> None:
        instrument = fsv_instrument()
        query_center = select_feature(
            instrument,
            "spectrum_analyzer.cap.analyzer.frequency.center.query",
        )
        factory = SessionFactory({})

        result = run_execution(
            instruments=(instrument,),
            routine_steps=(query_center,),
            dry_run=False,
            session_factory=factory,
        )

        self.assertEqual(result.status, ExecutionStatus.FAILED)
        self.assertEqual(factory.opened, [])
        self.assertEqual(result.instruments, (instrument,))
        self.assertIn("운영자의 명시적 승인", result.stop_reason)

    def test_live_idn_mismatch_blocks_before_routine_command(self) -> None:
        instrument = fsv_instrument()
        query_center = select_feature(
            instrument,
            "spectrum_analyzer.cap.analyzer.frequency.center.query",
        )
        session = FakeSession(
            {
                "*IDN?": "Rohde&Schwarz,FSV30,DIFFERENT,3.60",
            }
        )

        result = run_execution(
            instruments=(instrument,),
            routine_steps=(query_center,),
            dry_run=False,
            session_factory=SessionFactory(
                {instrument.resource: session}
            ),
            operator_confirmed=True,
        )

        self.assertEqual(result.status, ExecutionStatus.FAILED)
        self.assertEqual(session.queries, ["*IDN?"])
        self.assertIn("검증 당시와 다릅니다", result.stop_reason)

    def test_unqueried_option_state_blocks_actual_execution(self) -> None:
        instrument = fsv_instrument(
            option_state="unqueried",
            operation_ids=("analyzer.frequency.center::query",),
        )
        query_center = select_feature(
            instrument,
            "spectrum_analyzer.cap.analyzer.frequency.center.query",
        )
        session = FakeSession({"*IDN?": instrument.raw_idn})

        result = run_execution(
            instruments=(instrument,),
            routine_steps=(query_center,),
            dry_run=False,
            session_factory=SessionFactory(
                {instrument.resource: session}
            ),
            operator_confirmed=True,
        )

        self.assertEqual(result.status, ExecutionStatus.FAILED)
        self.assertEqual(session.queries, ["*IDN?"])
        self.assertIn("옵션 상태를 확인하지 않아", result.stop_reason)

    def test_unverified_opc_step_is_blocked_before_visa(self) -> None:
        instrument = fsv_instrument()
        factory = SessionFactory({})

        result = run_execution(
            instruments=(instrument,),
            routine_steps=(wait_for_completion(instrument, 1),),
            dry_run=False,
            session_factory=factory,
            operator_confirmed=True,
        )

        self.assertEqual(result.status, ExecutionStatus.FAILED)
        self.assertEqual(factory.opened, [])
        self.assertIn("검증된 *OPC?", result.stop_reason)

    def test_stop_interrupts_pc_delay(self) -> None:
        stop_event = threading.Event()
        holder: list = []

        def worker() -> None:
            holder.append(
                run_execution(
                    instruments=(),
                    routine_steps=(DelayStep(5),),
                    dry_run=False,
                    stop_event=stop_event,
                    operator_confirmed=True,
                )
            )

        thread = threading.Thread(target=worker)
        started = time.monotonic()
        thread.start()
        time.sleep(0.05)
        stop_event.set()
        thread.join(timeout=1)

        self.assertFalse(thread.is_alive())
        self.assertLess(time.monotonic() - started, 1)
        self.assertEqual(holder[0].status, ExecutionStatus.STOPPED)

    def test_write_failure_runs_validated_output_off_finalizer(self) -> None:
        instrument = smb_instrument()
        set_frequency = select_feature(
            instrument,
            "signal_generator.cap.source.frequency.set",
            arguments={"value": "1000000000"},
        )
        session = FakeSession(
            {
                "*IDN?": instrument.raw_idn,
                ":OUTP:STAT?": "0",
            },
            failing_writes=("SOUR:FREQ 1000000000",),
        )

        result = run_execution(
            instruments=(instrument,),
            routine_steps=(set_frequency,),
            dry_run=False,
            session_factory=SessionFactory(
                {instrument.resource: session}
            ),
            operator_confirmed=True,
        )

        self.assertEqual(result.status, ExecutionStatus.FAILED)
        self.assertEqual(
            session.writes,
            ["SOUR:FREQ 1000000000", ":OUTP:STAT 0"],
        )
        self.assertEqual(session.queries, ["*IDN?", ":OUTP:STAT?"])
        self.assertEqual(result.safety_records[0].status, "confirmed_off")
        self.assertIn("simulated VISA write failure", result.stop_reason)

    def test_successful_source_write_also_finishes_with_output_off(self) -> None:
        instrument = smb_instrument()
        set_frequency = select_feature(
            instrument,
            "signal_generator.cap.source.frequency.set",
            arguments={"value": "1000000000"},
        )
        session = FakeSession(
            {
                "*IDN?": instrument.raw_idn,
                ":OUTP:STAT?": "0",
            }
        )

        result = run_execution(
            instruments=(instrument,),
            routine_steps=(set_frequency,),
            dry_run=False,
            session_factory=SessionFactory(
                {instrument.resource: session}
            ),
            operator_confirmed=True,
        )

        self.assertEqual(result.status, ExecutionStatus.COMPLETED)
        self.assertEqual(
            session.writes,
            ["SOUR:FREQ 1000000000", ":OUTP:STAT 0"],
        )
        self.assertEqual(session.queries, ["*IDN?", ":OUTP:STAT?"])
        self.assertEqual(result.safety_records[0].status, "confirmed_off")
        self.assertEqual(result.stop_reason, "루틴 실행 및 안전 종료 완료")


if __name__ == "__main__":
    unittest.main()
