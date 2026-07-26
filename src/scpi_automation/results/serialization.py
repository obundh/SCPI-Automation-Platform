from __future__ import annotations

from typing import Any

from scpi_automation.execution import ExecutionResult
from scpi_automation.planning import (
    GenericPlanItem,
    SignalGeneratorPlanItem,
    SpectrumPlanItem,
)
from scpi_automation.routine import (
    DelayStep,
    PlanBoundDelayStep,
    SelectedFeature,
    SelectedInstrument,
    WaitForCompletionStep,
)


RESULT_DOCUMENT_TYPE = "scpi-execution-result"


def instrument_to_dict(instrument: SelectedInstrument) -> dict[str, object]:
    return {
        "resource": instrument.resource,
        "category": instrument.category.value,
        "category_label_ko": instrument.category.label_ko,
        "manufacturer": instrument.manufacturer,
        "model": instrument.model,
        "serial": instrument.serial,
        "firmware": instrument.firmware,
        "raw_idn": instrument.raw_idn,
        "profile_id": instrument.profile_id,
        "compatibility_status": instrument.compatibility_status,
        "compatible_capability_ids": list(
            instrument.compatible_capability_ids
        ),
        "compatible_operation_ids": list(
            instrument.compatible_operation_ids
        ),
        "incompatible_operation_ids": list(
            instrument.incompatible_operation_ids
        ),
        "unresolved_operation_ids": list(
            instrument.unresolved_operation_ids
        ),
        "validation_catalog_fingerprint": (
            instrument.validation_catalog_fingerprint
        ),
        "option_state": instrument.option_state,
        "option_response": instrument.option_response,
    }


def routine_step_to_dict(
    step,
    *,
    step_index: int,
) -> dict[str, object]:
    if isinstance(step, SelectedFeature):
        return {
            "step_index": step_index,
            "type": "feature",
            "resource": step.instrument.resource,
            "feature_id": step.feature_id,
            "arguments": dict(step.arguments),
            "plan_bindings": {
                binding.parameter_name: binding.field_id
                for binding in step.plan_bindings
            },
            "result_name": step.result_name,
        }
    if isinstance(step, DelayStep):
        return {
            "step_index": step_index,
            "type": "delay",
            "seconds": step.seconds,
        }
    if isinstance(step, PlanBoundDelayStep):
        return {
            "step_index": step_index,
            "type": "plan_bound_delay",
            "resource": step.instrument.resource,
            "field_id": step.field_id,
        }
    if isinstance(step, WaitForCompletionStep):
        return {
            "step_index": step_index,
            "type": "wait_for_completion",
            "resource": step.instrument.resource,
            "timeout_seconds": step.timeout_seconds,
        }
    raise TypeError(f"지원하지 않는 루틴 단계입니다: {type(step).__name__}")


def plan_item_to_dict(item, *, plan_index: int) -> dict[str, object]:
    common = {
        "plan_index": plan_index,
        "resource": item.instrument.resource,
        "category": item.instrument.category.value,
        "category_label_ko": item.instrument.category.label_ko,
        "case_id": item.case_id,
        "case_name": item.case_name,
        "repeat_count": item.repeat_count,
    }
    if isinstance(item, SpectrumPlanItem):
        return {
            **common,
            "type": "spectrum",
            "values": {
                "center_frequency_hz": item.center_frequency_hz,
                "span_hz": item.span_hz,
                "start_frequency_hz": item.start_frequency_hz,
                "stop_frequency_hz": item.stop_frequency_hz,
                "rbw_hz": item.rbw_hz,
                "vbw_hz": item.vbw_hz,
                "reference_level_dbm": item.reference_level_dbm,
            },
        }
    if isinstance(item, SignalGeneratorPlanItem):
        return {
            **common,
            "type": "signal_generator",
            "values": {
                "frequency_hz": item.frequency_hz,
                "power_dbm": item.power_dbm,
                "dwell_seconds": item.dwell_seconds,
            },
        }
    if isinstance(item, GenericPlanItem):
        return {
            **common,
            "type": "generic",
            "method_id": item.method_id,
            "method_label_ko": item.method_label_ko,
            "assistance_notice_acknowledged": (
                item.assistance_notice_acknowledged
            ),
            "common_values": [
                {
                    "field_id": value.field_id,
                    "value": value.value,
                    "unit": value.unit,
                }
                for value in item.common_values
            ],
            "detail_values": [
                {
                    "field_id": value.field_id,
                    "value": value.value,
                    "unit": value.unit,
                }
                for value in item.detail_values
            ],
        }
    raise TypeError(f"지원하지 않는 계획 항목입니다: {type(item).__name__}")


def _parsed_value(value: Any) -> Any:
    if isinstance(value, tuple):
        return [_parsed_value(item) for item in value]
    return value


def execution_result_to_dict(result: ExecutionResult) -> dict[str, object]:
    return {
        "document_type": RESULT_DOCUMENT_TYPE,
        "schema_version": result.schema_version,
        "run_id": result.run_id,
        "started_at_utc": result.started_at_utc,
        "finished_at_utc": result.finished_at_utc,
        "duration_ms": result.duration_ms,
        "status": result.status.value,
        "status_label_ko": result.status.label_ko,
        "dry_run": result.dry_run,
        "stop_reason": result.stop_reason,
        "summary": {
            "instrument_count": len(result.instruments),
            "routine_step_count": len(result.routine_steps),
            "executed_step_count": len(
                result.executed_steps or result.routine_steps
            ),
            "plan_item_count": len(result.plan_items),
            "test_case_count": result.test_case_count,
            "uses_plan_values": result.uses_plan_values,
            "completed_step_count": sum(
                record.status in {"completed", "dry_run"}
                for record in result.step_records
            ),
            "measurement_count": len(result.measurements),
            "event_count": len(result.events),
            "error_count": result.error_count,
            "safety_action_count": len(result.safety_records),
        },
        "instruments": [
            instrument_to_dict(instrument)
            for instrument in result.instruments
        ],
        "routine": [
            routine_step_to_dict(step, step_index=index)
            for index, step in enumerate(result.routine_steps, start=1)
        ],
        "executed_routine": [
            routine_step_to_dict(step, step_index=index)
            for index, step in enumerate(
                result.executed_steps or result.routine_steps,
                start=1,
            )
        ],
        "compiled_digest": result.compiled_digest,
        "plan": [
            plan_item_to_dict(item, plan_index=index)
            for index, item in enumerate(result.plan_items, start=1)
        ],
        "steps": [
            {
                "step_index": record.step_index,
                "step_kind": record.step_kind,
                "status": record.status,
                "started_at_utc": record.started_at_utc,
                "finished_at_utc": record.finished_at_utc,
                "duration_ms": record.duration_ms,
                "resource": record.resource,
                "feature_id": record.feature_id,
                "capability_id": record.capability_id,
                "operation": record.operation,
                "command": record.command,
                "response": record.response,
                "result_name": record.result_name,
                "response_type": record.response_type,
                "measurement_id": record.measurement_id,
                "error": record.error,
                "case_id": record.case_id,
                "case_name": record.case_name,
                "case_index": record.case_index,
                "repeat_index": record.repeat_index,
                "repeat_count": record.repeat_count,
                "template_step_index": record.template_step_index,
                "applied_plan_bindings": [
                    {
                        "parameter_name": parameter_name,
                        "field_id": field_id,
                        "value": value,
                    }
                    for parameter_name, field_id, value
                    in record.applied_plan_bindings
                ],
            }
            for record in result.step_records
        ],
        "measurements": [
            {
                "measurement_id": record.measurement_id,
                "sequence": record.sequence,
                "timestamp_utc": record.timestamp_utc,
                "step_index": record.step_index,
                "resource": record.resource,
                "manufacturer": record.manufacturer,
                "model": record.model,
                "feature_id": record.feature_id,
                "capability_id": record.capability_id,
                "operation": record.operation,
                "result_name": record.result_name,
                "response_type": record.response_type,
                "raw_response": record.raw_response,
                "parsed_value": _parsed_value(record.parsed_value),
                "unit": record.unit,
                "status": record.status,
                "case_id": record.case_id,
                "case_name": record.case_name,
                "case_index": record.case_index,
                "repeat_index": record.repeat_index,
                "repeat_count": record.repeat_count,
                "template_step_index": record.template_step_index,
            }
            for record in result.measurements
        ],
        "events": [
            {
                "sequence": event.sequence,
                "timestamp_utc": event.timestamp_utc,
                "level": event.level,
                "kind": event.kind,
                "message": event.message,
                "step_index": event.step_index,
                "total_steps": event.total_steps,
                "resource": event.resource,
                "command": event.command,
                "response": event.response,
                "feature_id": event.feature_id,
                "capability_id": event.capability_id,
                "response_type": event.response_type,
                "parsed_value": _parsed_value(event.parsed_value),
                "unit": event.unit,
                "measurement_id": event.measurement_id,
                "case_id": event.case_id,
                "case_name": event.case_name,
                "case_index": event.case_index,
                "repeat_index": event.repeat_index,
                "repeat_count": event.repeat_count,
                "template_step_index": event.template_step_index,
            }
            for event in result.events
        ],
        "safety": [
            {
                "sequence": record.sequence,
                "timestamp_utc": record.timestamp_utc,
                "resource": record.resource,
                "operation_id": record.operation_id,
                "command": record.command,
                "status": record.status,
                "response": record.response,
                "message": record.message,
            }
            for record in result.safety_records
        ],
    }
