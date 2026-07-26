from __future__ import annotations

import math
import re


MAX_FREQUENCY_POINTS = 500


def _positive_frequency(value: float, field_name: str) -> float:
    if isinstance(value, bool):
        raise ValueError(f"{field_name}은(는) 0보다 큰 숫자여야 합니다.")
    try:
        normalized = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{field_name}은(는) 숫자여야 합니다.") from exc
    if not math.isfinite(normalized) or normalized <= 0:
        raise ValueError(f"{field_name}은(는) 0보다 큰 숫자여야 합니다.")
    return normalized


def parse_frequency_list(
    text: str,
    unit_factor: float,
    *,
    max_points: int = MAX_FREQUENCY_POINTS,
) -> tuple[float, ...]:
    """Parse comma, semicolon, or newline-separated values into Hz."""

    if not isinstance(text, str):
        raise TypeError("주파수 목록은 문자열이어야 합니다.")
    factor = _positive_frequency(unit_factor, "주파수 단위 배율")
    if isinstance(max_points, bool) or not isinstance(max_points, int):
        raise TypeError("최대 주파수 개수는 정수여야 합니다.")
    if max_points <= 0:
        raise ValueError("최대 주파수 개수는 1개 이상이어야 합니다.")

    tokens = [
        token.strip()
        for token in re.split(r"[,;\r\n]+", text)
        if token.strip()
    ]
    if not tokens:
        raise ValueError("주파수를 한 개 이상 입력해 주세요.")
    if len(tokens) > max_points:
        raise ValueError(
            f"한 번에 추가할 수 있는 주파수는 최대 {max_points}개예요."
        )

    values: list[float] = []
    for index, token in enumerate(tokens, start=1):
        try:
            number = float(token.replace("_", "").replace(" ", ""))
        except ValueError as exc:
            raise ValueError(
                f"{index}번째 주파수 ‘{token}’을 숫자로 입력해 주세요."
            ) from exc
        values.append(
            _positive_frequency(number * factor, f"{index}번째 주파수")
        )
    return tuple(values)


def generate_frequency_series(
    start_hz: float,
    stop_hz: float,
    step_hz: float,
    *,
    max_points: int = MAX_FREQUENCY_POINTS,
) -> tuple[float, ...]:
    """Generate an inclusive ascending frequency series."""

    start = _positive_frequency(start_hz, "시작 주파수")
    stop = _positive_frequency(stop_hz, "끝 주파수")
    step = _positive_frequency(step_hz, "주파수 간격")
    if isinstance(max_points, bool) or not isinstance(max_points, int):
        raise TypeError("최대 주파수 개수는 정수여야 합니다.")
    if max_points <= 0:
        raise ValueError("최대 주파수 개수는 1개 이상이어야 합니다.")
    if stop < start:
        raise ValueError("끝 주파수는 시작 주파수보다 크거나 같아야 합니다.")
    if start == stop:
        return (start,)

    step_count = (stop - start) / step
    nearest_step_count = round(step_count)
    if not math.isclose(
        step_count,
        nearest_step_count,
        rel_tol=1e-10,
        abs_tol=1e-10,
    ):
        last_frequency = start + (math.floor(step_count) * step)
        raise ValueError(
            "끝 주파수까지 간격이 정확히 맞지 않아요. "
            f"현재 마지막 주파수는 {last_frequency:g} Hz예요."
        )

    point_count = int(nearest_step_count) + 1
    if point_count > max_points:
        raise ValueError(
            f"생성되는 주파수가 {point_count}개예요. "
            f"한 번에는 최대 {max_points}개까지 추가할 수 있어요."
        )

    values = [start + (index * step) for index in range(point_count)]
    values[-1] = stop
    return tuple(values)
