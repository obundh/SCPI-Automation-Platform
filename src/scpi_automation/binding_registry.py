"""Explicit links between routine parameters and structured plan fields.

The registry is intentionally independent from both the routine and planning
models.  A binding is keyed by the normalized capability/operation/parameter
identity, never by a translated label or by parsing an SCPI command string.
"""

from __future__ import annotations

from dataclasses import dataclass
from types import MappingProxyType


@dataclass(frozen=True, slots=True)
class PlanBindingDefinition:
    field_id: str
    label_ko: str


_DEFINITIONS = MappingProxyType(
    {
        ("analyzer.frequency.center", "set", "value"): PlanBindingDefinition(
            "center_frequency_hz",
            "Center - 중심 주파수",
        ),
        ("analyzer.frequency.span", "set", "value"): PlanBindingDefinition(
            "span_hz",
            "Span - 주파수 분석 범위",
        ),
        ("analyzer.frequency.start", "set", "value"): PlanBindingDefinition(
            "start_frequency_hz",
            "Start - 시작 주파수",
        ),
        ("analyzer.frequency.stop", "set", "value"): PlanBindingDefinition(
            "stop_frequency_hz",
            "Stop - 종료 주파수",
        ),
        ("analyzer.rbw", "set", "value"): PlanBindingDefinition(
            "rbw_hz",
            "RBW - 분해능 대역폭",
        ),
        ("analyzer.vbw", "set", "value"): PlanBindingDefinition(
            "vbw_hz",
            "VBW - 비디오 대역폭",
        ),
        ("display.reference_level", "set", "value"): PlanBindingDefinition(
            "reference_level_dbm",
            "Ref. Level - 화면 기준 레벨",
        ),
        ("source.frequency", "set", "value"): PlanBindingDefinition(
            "frequency_hz",
            "Frequency - 출력 주파수",
        ),
        ("source.power", "set", "value"): PlanBindingDefinition(
            "power_dbm",
            "Power - 출력 설정값",
        ),
        ("sweep.dwell", "set", "value"): PlanBindingDefinition(
            "dwell_seconds",
            "Dwell - 주파수 유지 시간",
        ),
    }
)


def plan_binding_definition(
    capability_id: str,
    operation: str,
    parameter_name: str,
) -> PlanBindingDefinition | None:
    """Return the one approved semantic plan binding, if any."""

    return _DEFINITIONS.get(
        (
            capability_id.strip(),
            operation.strip(),
            parameter_name.strip(),
        )
    )


def plan_binding_definitions() -> tuple[
    tuple[tuple[str, str, str], PlanBindingDefinition],
    ...,
]:
    """Expose an immutable snapshot for validation tests and UI summaries."""

    return tuple(_DEFINITIONS.items())
