from __future__ import annotations

import math
import tkinter as tk
from typing import Any

from scpi_automation.identity import DeviceCategory


CATEGORY_DESCRIPTIONS = {
    DeviceCategory.SPECTRUM_ANALYZER: (
        "신호를 주파수별로 나누어 크기와 피크를 확인하는 장비예요."
    ),
    DeviceCategory.SIGNAL_GENERATOR: (
        "원하는 주파수와 세기의 시험 신호를 만들어 보내는 장비예요."
    ),
    DeviceCategory.FUNCTION_GENERATOR: (
        "정현파·펄스·임의파형처럼 다양한 전기 파형을 만들어 주는 장비예요."
    ),
    DeviceCategory.OSCILLOSCOPE: (
        "시간에 따라 변하는 전압 파형을 화면으로 확인하는 장비예요."
    ),
    DeviceCategory.DIGITAL_MULTIMETER: (
        "전압·전류·저항 같은 전기 값을 숫자로 측정하는 장비예요."
    ),
    DeviceCategory.POWER_SUPPLY: (
        "시험 대상에 필요한 전압과 전류를 안정적으로 공급하는 장비예요."
    ),
    DeviceCategory.LCR_METER: (
        "부품의 인덕턴스·커패시턴스·저항과 임피던스를 측정하는 장비예요."
    ),
    DeviceCategory.NETWORK_ANALYZER: (
        "주파수에 따른 반사와 전달 특성을 S-파라미터로 측정하는 장비예요."
    ),
    DeviceCategory.UNKNOWN: (
        "장비 이름표는 읽었지만 종류를 아직 확정하지 못했어요."
    ),
}


CATEGORY_COLORS = {
    DeviceCategory.SPECTRUM_ANALYZER: ("#00A86B", "#E8F8F1"),
    DeviceCategory.SIGNAL_GENERATOR: ("#3182F6", "#EDF5FF"),
    DeviceCategory.FUNCTION_GENERATOR: ("#2563EB", "#EEF4FF"),
    DeviceCategory.OSCILLOSCOPE: ("#7C3AED", "#F3EEFF"),
    DeviceCategory.DIGITAL_MULTIMETER: ("#D97706", "#FFF4E5"),
    DeviceCategory.POWER_SUPPLY: ("#DC2626", "#FDECEC"),
    DeviceCategory.LCR_METER: ("#C2410C", "#FFF2E8"),
    DeviceCategory.NETWORK_ANALYZER: ("#0891B2", "#E8F8FB"),
    DeviceCategory.UNKNOWN: ("#6B7684", "#F2F4F6"),
}


def category_description(category: DeviceCategory) -> str:
    return CATEGORY_DESCRIPTIONS.get(
        category,
        CATEGORY_DESCRIPTIONS[DeviceCategory.UNKNOWN],
    )


def category_colors(category: DeviceCategory) -> tuple[str, str]:
    return CATEGORY_COLORS.get(
        category,
        CATEGORY_COLORS[DeviceCategory.UNKNOWN],
    )


class CategoryArtwork(tk.Canvas):
    """Small responsive vector illustration for an instrument category."""

    def __init__(
        self,
        master: tk.Misc,
        category: DeviceCategory,
        *,
        width: int = 160,
        height: int = 94,
        background: str = "#F8FAFC",
        **kwargs: Any,
    ) -> None:
        super().__init__(
            master,
            width=width,
            height=height,
            background=background,
            highlightthickness=0,
            **kwargs,
        )
        self.category = category
        self.bind("<Configure>", self._redraw)

    def _rounded_rectangle(
        self,
        x1: float,
        y1: float,
        x2: float,
        y2: float,
        radius: float,
        **kwargs: Any,
    ) -> int:
        points = (
            x1 + radius,
            y1,
            x2 - radius,
            y1,
            x2,
            y1,
            x2,
            y1 + radius,
            x2,
            y2 - radius,
            x2,
            y2,
            x2 - radius,
            y2,
            x1 + radius,
            y2,
            x1,
            y2,
            x1,
            y2 - radius,
            x1,
            y1 + radius,
            x1,
            y1,
        )
        return self.create_polygon(points, smooth=True, splinesteps=24, **kwargs)

    def _redraw(self, _event: tk.Event[tk.Misc] | None = None) -> None:
        self.delete("all")
        width = max(80, self.winfo_width())
        height = max(52, self.winfo_height())
        accent, light = category_colors(self.category)

        self._rounded_rectangle(
            width * 0.02,
            height * 0.04,
            width * 0.98,
            height * 0.96,
            min(width, height) * 0.08,
            fill=light,
            outline="",
        )
        if self.category == DeviceCategory.SPECTRUM_ANALYZER:
            self._draw_spectrum(width, height, accent)
        elif self.category == DeviceCategory.SIGNAL_GENERATOR:
            self._draw_generator(width, height, accent)
        elif self.category == DeviceCategory.FUNCTION_GENERATOR:
            self._draw_function_generator(width, height, accent)
        elif self.category == DeviceCategory.OSCILLOSCOPE:
            self._draw_scope(width, height, accent)
        elif self.category == DeviceCategory.DIGITAL_MULTIMETER:
            self._draw_meter(width, height, accent)
        elif self.category == DeviceCategory.POWER_SUPPLY:
            self._draw_supply(width, height, accent)
        elif self.category == DeviceCategory.LCR_METER:
            self._draw_lcr(width, height, accent)
        elif self.category == DeviceCategory.NETWORK_ANALYZER:
            self._draw_network(width, height, accent)
        else:
            self._draw_unknown(width, height, accent)

    def _instrument_body(
        self,
        width: float,
        height: float,
        *,
        screen_ratio: float = 0.68,
    ) -> tuple[float, float, float, float]:
        x1, y1, x2, y2 = width * 0.11, height * 0.16, width * 0.89, height * 0.84
        self._rounded_rectangle(
            x1,
            y1,
            x2,
            y2,
            height * 0.06,
            fill="#26313F",
            outline="#151B24",
            width=1,
        )
        screen = (
            x1 + width * 0.045,
            y1 + height * 0.09,
            x1 + (x2 - x1) * screen_ratio,
            y2 - height * 0.09,
        )
        self.create_rectangle(*screen, fill="#0F1720", outline="#4B5563", width=1)
        knob_x = x2 - width * 0.09
        self.create_oval(
            knob_x - height * 0.09,
            height * 0.34,
            knob_x + height * 0.09,
            height * 0.52,
            fill="#D8DEE6",
            outline="#8993A1",
        )
        self.create_oval(
            knob_x - height * 0.055,
            height * 0.59,
            knob_x + height * 0.055,
            height * 0.70,
            fill="#8F9BAA",
            outline="",
        )
        return screen

    def _screen_grid(self, screen: tuple[float, float, float, float]) -> None:
        x1, y1, x2, y2 = screen
        for index in range(1, 5):
            x = x1 + (x2 - x1) * index / 5
            self.create_line(x, y1, x, y2, fill="#293444", width=1)
        for index in range(1, 4):
            y = y1 + (y2 - y1) * index / 4
            self.create_line(x1, y, x2, y, fill="#293444", width=1)

    def _draw_spectrum(self, width: float, height: float, accent: str) -> None:
        screen = self._instrument_body(width, height)
        self._screen_grid(screen)
        x1, y1, x2, y2 = screen
        points = (
            (0.00, 0.80),
            (0.11, 0.77),
            (0.18, 0.69),
            (0.27, 0.73),
            (0.36, 0.58),
            (0.44, 0.67),
            (0.53, 0.18),
            (0.59, 0.69),
            (0.72, 0.61),
            (0.82, 0.72),
            (1.00, 0.66),
        )
        coords: list[float] = []
        for px, py in points:
            coords.extend((x1 + (x2 - x1) * px, y1 + (y2 - y1) * py))
        self.create_line(*coords, fill=accent, width=max(2, int(height * 0.025)))

    def _draw_generator(self, width: float, height: float, accent: str) -> None:
        screen = self._instrument_body(width, height)
        self._screen_grid(screen)
        x1, y1, x2, y2 = screen
        coords: list[float] = []
        for index in range(41):
            position = index / 40
            x = x1 + (x2 - x1) * position
            y = (y1 + y2) / 2 - math.sin(position * math.pi * 4) * (y2 - y1) * 0.28
            coords.extend((x, y))
        self.create_line(*coords, fill=accent, width=max(2, int(height * 0.025)))
        arrow_y = height * 0.89
        self.create_line(
            width * 0.33,
            arrow_y,
            width * 0.72,
            arrow_y,
            fill=accent,
            width=max(2, int(height * 0.025)),
            arrow="last",
        )

    def _draw_function_generator(
        self,
        width: float,
        height: float,
        accent: str,
    ) -> None:
        screen = self._instrument_body(width, height, screen_ratio=0.72)
        self._screen_grid(screen)
        x1, y1, x2, y2 = screen
        middle = (y1 + y2) / 2
        coords: list[float] = []
        for index in range(31):
            position = index / 30
            x = x1 + (x2 - x1) * position
            y = middle - math.sin(position * math.pi * 4) * (y2 - y1) * 0.20
            coords.extend((x, y))
        self.create_line(*coords, fill=accent, width=max(2, int(height * 0.022)))
        square_y = y2 - (y2 - y1) * 0.16
        self.create_line(
            x1,
            square_y,
            x1 + (x2 - x1) * 0.22,
            square_y,
            x1 + (x2 - x1) * 0.22,
            square_y - (y2 - y1) * 0.18,
            x1 + (x2 - x1) * 0.45,
            square_y - (y2 - y1) * 0.18,
            fill="#F59E0B",
            width=max(1, int(height * 0.018)),
        )

    def _draw_scope(self, width: float, height: float, accent: str) -> None:
        screen = self._instrument_body(width, height, screen_ratio=0.72)
        self._screen_grid(screen)
        x1, y1, x2, y2 = screen
        points = (
            (0.00, 0.55),
            (0.15, 0.55),
            (0.20, 0.25),
            (0.27, 0.82),
            (0.34, 0.38),
            (0.42, 0.55),
            (0.59, 0.55),
            (0.64, 0.25),
            (0.71, 0.82),
            (0.78, 0.38),
            (0.86, 0.55),
            (1.00, 0.55),
        )
        coords: list[float] = []
        for px, py in points:
            coords.extend((x1 + (x2 - x1) * px, y1 + (y2 - y1) * py))
        self.create_line(*coords, fill=accent, width=max(2, int(height * 0.025)))

    def _draw_meter(self, width: float, height: float, accent: str) -> None:
        screen = self._instrument_body(width, height, screen_ratio=0.74)
        x1, y1, x2, y2 = screen
        self.create_text(
            (x1 + x2) / 2,
            (y1 + y2) / 2,
            text="12.345",
            fill=accent,
            font=("Consolas", max(8, int(height * 0.18)), "bold"),
        )

    def _draw_supply(self, width: float, height: float, accent: str) -> None:
        screen = self._instrument_body(width, height, screen_ratio=0.7)
        x1, y1, x2, y2 = screen
        self.create_text(
            (x1 + x2) / 2,
            y1 + (y2 - y1) * 0.35,
            text="12.00 V",
            fill=accent,
            font=("Consolas", max(7, int(height * 0.12)), "bold"),
        )
        self.create_text(
            (x1 + x2) / 2,
            y1 + (y2 - y1) * 0.68,
            text="0.50 A",
            fill="#F59E0B",
            font=("Consolas", max(7, int(height * 0.11))),
        )

    def _draw_lcr(self, width: float, height: float, accent: str) -> None:
        screen = self._instrument_body(width, height, screen_ratio=0.72)
        x1, y1, x2, y2 = screen
        self.create_text(
            (x1 + x2) / 2,
            y1 + (y2 - y1) * 0.34,
            text="L  C  R",
            fill=accent,
            font=("Segoe UI Semibold", max(7, int(height * 0.11))),
        )
        self.create_text(
            (x1 + x2) / 2,
            y1 + (y2 - y1) * 0.68,
            text="1.002 kΩ",
            fill="#F59E0B",
            font=("Consolas", max(7, int(height * 0.11)), "bold"),
        )

    def _draw_network(self, width: float, height: float, accent: str) -> None:
        screen = self._instrument_body(width, height, screen_ratio=0.72)
        x1, y1, x2, y2 = screen
        center_x = (x1 + x2) / 2
        center_y = (y1 + y2) / 2
        radius = min(x2 - x1, y2 - y1) * 0.38
        self.create_oval(
            center_x - radius,
            center_y - radius,
            center_x + radius,
            center_y + radius,
            outline=accent,
            width=max(1, int(height * 0.018)),
        )
        self.create_line(
            center_x - radius,
            center_y,
            center_x + radius,
            center_y,
            fill="#536172",
        )
        for factor in (0.35, 0.65):
            inner = radius * factor
            self.create_arc(
                center_x - radius,
                center_y - inner,
                center_x + radius,
                center_y + inner,
                start=200,
                extent=320,
                style="arc",
                outline=accent,
            )

    def _draw_unknown(self, width: float, height: float, accent: str) -> None:
        screen = self._instrument_body(width, height, screen_ratio=0.72)
        x1, y1, x2, y2 = screen
        self.create_text(
            (x1 + x2) / 2,
            (y1 + y2) / 2,
            text="?",
            fill=accent,
            font=("Segoe UI Semibold", max(12, int(height * 0.28))),
        )


class TimelineConnector(tk.Canvas):
    def __init__(
        self,
        master: tk.Misc,
        *,
        color: str,
        last: bool,
        width: int = 28,
        height: int = 54,
        background: str = "#FFFFFF",
    ) -> None:
        super().__init__(
            master,
            width=width,
            height=height,
            background=background,
            highlightthickness=0,
        )
        self.color = color
        self.last = last
        self.bind("<Configure>", self._redraw)

    def _redraw(self, _event: tk.Event[tk.Misc] | None = None) -> None:
        self.delete("all")
        width = self.winfo_width()
        height = self.winfo_height()
        center_x = width * 0.5
        dot_y = min(height * 0.38, 22)
        self.create_line(center_x, 0, center_x, dot_y, fill=self.color, width=2)
        if not self.last:
            self.create_line(center_x, dot_y, center_x, height, fill=self.color, width=2)
        self.create_line(center_x, dot_y, width, dot_y, fill=self.color, width=2)
        radius = max(3, min(5, width * 0.15))
        self.create_oval(
            center_x - radius,
            dot_y - radius,
            center_x + radius,
            dot_y + radius,
            fill=self.color,
            outline="#FFFFFF",
            width=2,
        )
