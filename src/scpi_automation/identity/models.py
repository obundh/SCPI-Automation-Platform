from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


class IdentityParseError(ValueError):
    """Raised when an IDN response cannot be parsed safely."""


class DeviceCategory(str, Enum):
    SPECTRUM_ANALYZER = "spectrum_analyzer"
    SIGNAL_GENERATOR = "signal_generator"
    FUNCTION_GENERATOR = "function_generator"
    OSCILLOSCOPE = "oscilloscope"
    DIGITAL_MULTIMETER = "digital_multimeter"
    POWER_SUPPLY = "power_supply"
    LCR_METER = "lcr_meter"
    NETWORK_ANALYZER = "network_analyzer"
    UNKNOWN = "unknown"

    @property
    def label_ko(self) -> str:
        return {
            DeviceCategory.SPECTRUM_ANALYZER: "스펙트럼·신호 분석기",
            DeviceCategory.SIGNAL_GENERATOR: "RF 신호발생기",
            DeviceCategory.FUNCTION_GENERATOR: "함수·임의파형 발생기",
            DeviceCategory.OSCILLOSCOPE: "오실로스코프",
            DeviceCategory.DIGITAL_MULTIMETER: "디지털 멀티미터",
            DeviceCategory.POWER_SUPPLY: "전원공급기",
            DeviceCategory.LCR_METER: "LCR 미터",
            DeviceCategory.NETWORK_ANALYZER: "벡터 네트워크 분석기",
            DeviceCategory.UNKNOWN: "미분류",
        }[self]


class ClassificationConfidence(str, Enum):
    EXACT_PROFILE = "exact_profile"
    REPRESENTATIVE_CONFIRMED = "representative_confirmed"
    VALIDATED_PROFILE = "validated_profile"
    FAMILY_HEURISTIC = "family_heuristic"
    UNKNOWN = "unknown"

    @property
    def label_ko(self) -> str:
        return {
            ClassificationConfidence.EXACT_PROFILE: "기준 명령팩 일치",
            ClassificationConfidence.REPRESENTATIVE_CONFIRMED: "기준 명령팩 선택",
            ClassificationConfidence.VALIDATED_PROFILE: "실장비 기능 검증",
            ClassificationConfidence.FAMILY_HEURISTIC: "장비 분류 추정",
            ClassificationConfidence.UNKNOWN: "확인 필요",
        }[self]


@dataclass(frozen=True, slots=True)
class InstrumentIdentity:
    raw: str
    manufacturer: str
    model: str
    serial: str
    firmware: str


@dataclass(frozen=True, slots=True)
class ClassificationResult:
    category: DeviceCategory
    confidence: ClassificationConfidence
    matched_rule: str
    profile_id: str = ""
    profile_status: str = ""
    compatible_capability_ids: tuple[str, ...] = ()
    incompatible_capability_ids: tuple[str, ...] = ()
    compatible_operation_ids: tuple[str, ...] = ()
    incompatible_operation_ids: tuple[str, ...] = ()
    unresolved_operation_ids: tuple[str, ...] = ()
    validation_catalog_fingerprint: str = ""
    option_response: str = ""
    option_state: str = "unqueried"

    def __post_init__(self) -> None:
        if self.option_state not in {
            "queried",
            "unsupported",
            "unqueried",
        }:
            raise ValueError(
                "option_state must be queried, unsupported, or unqueried"
            )
        if self.option_state == "queried" and not self.option_response.strip():
            raise ValueError(
                "queried option_state requires an *OPT? response"
            )
        if (
            self.option_state != "queried"
            and self.option_response.strip()
        ):
            raise ValueError(
                "Only queried option_state may contain an *OPT? response"
            )

    @property
    def display_match(self) -> str:
        if self.profile_id:
            return f"{self.confidence.label_ko} · {self.profile_id}"
        return self.confidence.label_ko
