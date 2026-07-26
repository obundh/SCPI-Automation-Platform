from __future__ import annotations

import math
import re
import string

from scpi_automation.routine import RoutineFeature


class CommandCompileError(ValueError):
    """Raised before VISA I/O when a command cannot be rendered safely."""


_ARBITRARY_PARAMETER_TYPES = frozenset({"string"})


def _safe_token(value: object, *, label: str) -> str:
    text = str(value).strip()
    if not text:
        raise CommandCompileError(f"{label} 값이 비어 있습니다.")
    if len(text) > 10_000:
        raise CommandCompileError(f"{label} 값이 너무 깁니다.")
    if any(character in text for character in ("\x00", "\r", "\n", ";")):
        raise CommandCompileError(
            f"{label} 값에 줄바꿈 또는 SCPI 명령 구분자가 들어 있습니다."
        )
    if any(ord(character) < 32 and character != "\t" for character in text):
        raise CommandCompileError(f"{label} 값에 제어 문자가 들어 있습니다.")
    return text


def _placeholder_names(template: str) -> tuple[str, ...]:
    names: list[str] = []
    try:
        parsed = string.Formatter().parse(template)
        for _literal, name, format_spec, conversion in parsed:
            if name is None:
                continue
            if (
                not name
                or format_spec
                or conversion
                or re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", name) is None
            ):
                raise CommandCompileError(
                    "복잡하거나 안전하지 않은 SCPI 자리표시자는 실행할 수 없습니다."
                )
            names.append(name)
    except ValueError as exc:
        raise CommandCompileError(f"SCPI 형식이 올바르지 않습니다: {exc}") from exc
    return tuple(dict.fromkeys(names))


def _normalize_argument(
    feature: RoutineFeature,
    name: str,
    raw_value: object,
) -> str:
    try:
        parameter = next(
            item for item in feature.parameters if item.name == name
        )
    except StopIteration as exc:
        raise CommandCompileError(
            f"'{name}' 인수 정의를 후보 명령팩에서 찾지 못했습니다."
        ) from exc

    text = _safe_token(raw_value, label=name)
    mapped = {key.casefold(): value for key, value in parameter.mapping}
    if text.casefold() in mapped:
        text = mapped[text.casefold()]

    if parameter.value_type in _ARBITRARY_PARAMETER_TYPES:
        raise CommandCompileError(
            f"'{name}'은 자유 문자열 인수입니다. V1 실행기는 모델 전용 "
            "어댑터가 없는 자유 문자열 명령을 전송하지 않습니다."
        )
    if parameter.value_type == "boolean":
        if text.upper() not in {"0", "1", "ON", "OFF", "TRUE", "FALSE"}:
            raise CommandCompileError(
                f"'{name}'은 SCPI ON/OFF 값이어야 합니다."
            )
        return _safe_token(text, label=name)
    if parameter.value_type == "enum":
        allowed = set(parameter.choices) | {
            value for _key, value in parameter.mapping
        }
        if allowed and text not in allowed:
            raise CommandCompileError(
                f"'{name}' 값이 후보 명령팩의 선택 범위를 벗어났습니다."
            )
        return _safe_token(text, label=name)
    if parameter.value_type == "voltage_current_time_triplets":
        parts = tuple(part.strip() for part in text.split(","))
        if len(parts) < 3 or len(parts) % 3 or len(parts) // 3 > 128:
            raise CommandCompileError(
                f"'{name}'은 최대 128개의 전압·전류·시간 묶음이어야 합니다."
            )
        try:
            numbers = tuple(float(part) for part in parts)
        except ValueError as exc:
            raise CommandCompileError(
                f"'{name}'에는 숫자만 사용할 수 있습니다."
            ) from exc
        if any(not math.isfinite(number) for number in numbers):
            raise CommandCompileError(f"'{name}'에는 유한한 숫자만 사용할 수 있습니다.")
        return ",".join(parts)

    numeric_types = {
        "float",
        "integer",
        "number",
        "number_or_auto",
        "float_or_enum",
        "integer_or_mnemonic",
        "float_or_mnemonic",
        "float_or_string",
    }
    mnemonic_allowed = set(parameter.choices)
    if parameter.value_type == "number_or_auto":
        mnemonic_allowed.add("AUTO")
    if parameter.value_type == "float_or_string":
        if re.fullmatch(r"[A-Za-z][A-Za-z0-9_.+-]*", text):
            return _safe_token(text, label=name)
    if parameter.value_type in numeric_types and text not in mnemonic_allowed:
        try:
            number = float(text)
        except ValueError as exc:
            raise CommandCompileError(f"'{name}'은 숫자여야 합니다.") from exc
        if not math.isfinite(number):
            raise CommandCompileError(f"'{name}'은 유한한 숫자여야 합니다.")
        if (
            parameter.value_type in {"integer", "integer_or_mnemonic"}
            and not number.is_integer()
        ):
            raise CommandCompileError(f"'{name}'은 정수여야 합니다.")
        if parameter.minimum is not None and number < parameter.minimum:
            raise CommandCompileError(
                f"'{name}' 값이 후보 명령팩의 최소값보다 작습니다."
            )
        if parameter.maximum is not None and number > parameter.maximum:
            raise CommandCompileError(
                f"'{name}' 값이 후보 명령팩의 최대값보다 큽니다."
            )
    return _safe_token(text, label=name)


def render_feature_command(
    feature: RoutineFeature,
    arguments: tuple[tuple[str, str], ...],
) -> str:
    """Render one already-selected feature without allowing command chaining."""

    if not feature.capability_id or not feature.operation:
        raise CommandCompileError(
            "화면 설명용 공통 기능에는 실행 가능한 SCPI 명령이 없습니다."
        )
    template = feature.scpi_preview.strip()
    if not template:
        raise CommandCompileError("후보 명령팩의 SCPI 명령이 비어 있습니다.")
    names = _placeholder_names(template)
    supplied = dict(arguments)
    if len(supplied) != len(arguments):
        raise CommandCompileError("같은 명령 인수가 두 번 들어 있습니다.")
    unknown = set(supplied) - set(names)
    missing = set(names) - set(supplied)
    if unknown:
        raise CommandCompileError(
            "등록되지 않은 명령 인수입니다: " + ", ".join(sorted(unknown))
        )
    if missing:
        raise CommandCompileError(
            "명령 실행에 필요한 값이 없습니다: " + ", ".join(sorted(missing))
        )
    normalized = {
        name: _normalize_argument(feature, name, supplied[name])
        for name in names
    }
    try:
        command = template.format_map(normalized)
    except (KeyError, ValueError) as exc:
        raise CommandCompileError(
            f"SCPI 명령을 완성하지 못했습니다: {exc}"
        ) from exc
    return _safe_token(command, label="SCPI 명령")


def parse_query_response(
    response: object,
    response_type: str,
    *,
    max_characters: int,
):
    raw = str(response).strip()
    if not raw:
        raise ValueError("장비 응답이 비어 있습니다.")
    if len(raw) > max_characters:
        raise ValueError(
            f"장비 응답이 허용 크기({max_characters}자)를 넘었습니다."
        )
    normalized_type = response_type.strip().casefold()
    if normalized_type in {"", "string"}:
        return raw
    if normalized_type == "boolean":
        token = raw.casefold()
        if token in {"1", "on", "true"}:
            return True
        if token in {"0", "off", "false"}:
            return False
        raise ValueError(f"ON/OFF 응답으로 해석할 수 없습니다: {raw!r}")
    if normalized_type == "integer":
        number = float(raw)
        if not math.isfinite(number) or not number.is_integer():
            raise ValueError(f"정수 응답으로 해석할 수 없습니다: {raw!r}")
        return int(number)
    if normalized_type in {"float", "number"}:
        number = float(raw)
        if not math.isfinite(number):
            raise ValueError(f"유한한 숫자 응답이 아닙니다: {raw!r}")
        return number
    if normalized_type == "float_or_string":
        try:
            number = float(raw)
        except ValueError:
            return raw
        if not math.isfinite(number):
            raise ValueError(f"유한한 숫자 응답이 아닙니다: {raw!r}")
        return number
    if normalized_type == "string_array":
        return tuple(part.strip() for part in raw.split(","))
    if normalized_type in {
        "array",
        "float_array",
        "float_pair",
        "float_triplet",
    }:
        try:
            values = tuple(float(part.strip()) for part in raw.split(","))
        except ValueError as exc:
            raise ValueError(f"숫자 배열 응답으로 해석할 수 없습니다: {raw!r}") from exc
        if any(not math.isfinite(value) for value in values):
            raise ValueError("배열 응답에 유한하지 않은 숫자가 있습니다.")
        expected = {
            "float_pair": 2,
            "float_triplet": 3,
        }.get(normalized_type)
        if expected is not None and len(values) != expected:
            raise ValueError(
                f"{expected}개 숫자 응답이어야 하지만 {len(values)}개입니다."
            )
        return values
    return raw

