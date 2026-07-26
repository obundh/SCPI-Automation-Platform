from __future__ import annotations

import math
from collections.abc import Iterable, Mapping

from scpi_automation.routine import RoutineFeature, RoutineParameter


_PARAMETER_LABELS = {
    "value": "설정값",
    "state": "상태",
    "channel": "채널",
    "marker": "마커",
    "trace": "트레이스",
    "port": "포트",
    "mode": "동작 방식",
    "source": "소스",
    "format": "데이터 형식",
    "function": "측정 기능",
    "range": "측정 범위",
    "count": "개수",
    "start": "시작값",
    "stop": "끝값",
    "step": "간격",
}


def friendly_parameter_label(name: str) -> str:
    normalized = str(name).strip()
    return _PARAMETER_LABELS.get(
        normalized,
        normalized.replace("_", " ").strip().title() or "입력값",
    )


def _number(value: object) -> float | None:
    if isinstance(value, bool):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _compact(number: float) -> str:
    if number == 0:
        return "0"
    absolute = abs(number)
    if absolute >= 1e12 or absolute < 1e-6:
        return f"{number:.6g}"
    return f"{number:.9g}"


def format_engineering_value(
    value: object,
    unit: str = "",
    *,
    include_unit: bool = True,
) -> str:
    """Format one stored base-unit value for an operator-facing screen."""

    text, display_unit = engineering_value_parts(value, unit)
    if not include_unit or not display_unit:
        return text
    return f"{text} {display_unit}"


def engineering_value_parts(
    value: object,
    unit: str = "",
) -> tuple[str, str]:
    """Return a readable number and the matching displayed unit separately."""

    if isinstance(value, bool):
        return ("켜짐 (ON)" if value else "꺼짐 (OFF)"), ""
    number = _number(value)
    normalized_unit = str(unit or "").strip()
    if number is None:
        text = "" if value is None else str(value)
        return text, normalized_unit

    absolute = abs(number)
    display_number = number
    display_unit = normalized_unit
    if normalized_unit.casefold() == "hz":
        for threshold, suffix in (
            (1e9, "GHz"),
            (1e6, "MHz"),
            (1e3, "kHz"),
        ):
            if absolute >= threshold:
                display_number = number / threshold
                display_unit = suffix
                break
    elif normalized_unit.casefold() == "s":
        if absolute != 0 and absolute < 1e-6:
            display_number = number * 1e9
            display_unit = "ns"
        elif absolute != 0 and absolute < 1e-3:
            display_number = number * 1e6
            display_unit = "µs"
        elif absolute != 0 and absolute < 1:
            display_number = number * 1e3
            display_unit = "ms"
        elif absolute >= 60 and number % 60 == 0:
            display_number = number / 60
            display_unit = "min"
    elif normalized_unit in {"V", "A", "W", "Ohm", "Ω"}:
        base_unit = "Ω" if normalized_unit in {"Ohm", "Ω"} else normalized_unit
        if absolute != 0 and absolute < 1e-6:
            display_number = number * 1e9
            display_unit = f"n{base_unit}"
        elif absolute != 0 and absolute < 1e-3:
            display_number = number * 1e6
            display_unit = f"µ{base_unit}"
        elif absolute != 0 and absolute < 1:
            display_number = number * 1e3
            display_unit = f"m{base_unit}"
        elif absolute >= 1e6:
            display_number = number / 1e6
            display_unit = f"M{base_unit}"
        elif absolute >= 1e3:
            display_number = number / 1e3
            display_unit = f"k{base_unit}"

    text = _compact(display_number)
    return text, display_unit


def format_display_value(value: object, unit: str = "") -> str:
    if isinstance(value, (tuple, list)):
        values = tuple(value)
        if not values:
            return "값 없음"
        if len(values) <= 4:
            return ", ".join(
                format_engineering_value(item, unit) for item in values
            )
        numeric = tuple(
            number
            for item in values
            if (number := _number(item)) is not None
        )
        if len(numeric) == len(values):
            return (
                f"{len(values)}개 값 · 최소 "
                f"{format_engineering_value(min(numeric), unit)} · 최대 "
                f"{format_engineering_value(max(numeric), unit)}"
            )
        return f"{len(values)}개 값"
    return format_engineering_value(value, unit)


def format_parameter_value(
    parameter: RoutineParameter | None,
    value: object,
) -> str:
    if parameter is None:
        return str(value)
    text = str(value).strip()
    if parameter.name == "state":
        if text.casefold() in {"false", "off", "0"}:
            return "끄기 (OFF)"
        if text.casefold() in {"true", "on", "1"}:
            return "켜기 (ON)"
    if parameter.unit and _number(text) is not None:
        return format_engineering_value(text, parameter.unit)
    return text


def format_feature_arguments(
    feature: RoutineFeature,
    arguments: Mapping[str, object] | Iterable[tuple[str, object]],
) -> str:
    values = dict(arguments)
    parameters = {parameter.name: parameter for parameter in feature.parameters}
    return " · ".join(
        f"{friendly_parameter_label(name)} "
        f"{format_parameter_value(parameters.get(name), value)}"
        for name, value in values.items()
    )
