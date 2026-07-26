from __future__ import annotations

import re
from dataclasses import dataclass

from .models import (
    ClassificationConfidence,
    ClassificationResult,
    DeviceCategory,
    InstrumentIdentity,
)
from .profile_catalog import match_representative_profile


def _normalized_token(value: str) -> str:
    return re.sub(r"[^A-Z0-9]+", "", value.upper())


@dataclass(frozen=True, slots=True)
class _FamilyRule:
    name: str
    category: DeviceCategory
    model_pattern: re.Pattern[str]
    manufacturers: tuple[str, ...] = ()

    def matches(self, manufacturer: str, model: str) -> bool:
        if self.manufacturers and manufacturer not in self.manufacturers:
            return False
        return bool(self.model_pattern.search(model))


_ROHDE_SCHWARZ = ("ROHDESCHWARZ", "RS")
_KEYSIGHT_FAMILY = ("KEYSIGHTTECHNOLOGIES", "AGILENTTECHNOLOGIES", "HEWLETTPACKARD", "HP")

_FAMILY_RULES = (
    _FamilyRule(
        "R&S FSV/FSW/FSP/FSL/FSU/FPS family",
        DeviceCategory.SPECTRUM_ANALYZER,
        re.compile(r"^(FSV|FSW|FSP|FSL|FSU|FPS)"),
        _ROHDE_SCHWARZ,
    ),
    _FamilyRule(
        "R&S SMA/SMB/SMC/SMW/SGS/SMM/SMP family",
        DeviceCategory.SIGNAL_GENERATOR,
        re.compile(r"^(SMA|SMB|SMC|SMW|SGS|SMM|SMP)"),
        _ROHDE_SCHWARZ,
    ),
    _FamilyRule(
        "Keysight/Agilent 33xxx waveform-generator family",
        DeviceCategory.FUNCTION_GENERATOR,
        re.compile(r"^33\d{3}"),
        _KEYSIGHT_FAMILY,
    ),
    _FamilyRule(
        "Keysight/Agilent N90xx family",
        DeviceCategory.SPECTRUM_ANALYZER,
        re.compile(r"^N90\d{2}"),
        _KEYSIGHT_FAMILY,
    ),
    _FamilyRule(
        "Keysight/Agilent N51xx family",
        DeviceCategory.SIGNAL_GENERATOR,
        re.compile(r"^N51\d{2}"),
        _KEYSIGHT_FAMILY,
    ),
    _FamilyRule(
        "Siglent SSA family",
        DeviceCategory.SPECTRUM_ANALYZER,
        re.compile(r"^SSA"),
        ("SIGLENTTECHNOLOGIES", "SIGLENT"),
    ),
    _FamilyRule(
        "Siglent SSG family",
        DeviceCategory.SIGNAL_GENERATOR,
        re.compile(r"^SSG"),
        ("SIGLENTTECHNOLOGIES", "SIGLENT"),
    ),
    _FamilyRule(
        "Rigol DSA/RSA family",
        DeviceCategory.SPECTRUM_ANALYZER,
        re.compile(r"^(DSA|RSA)"),
        ("RIGOLTECHNOLOGIES", "RIGOL"),
    ),
    _FamilyRule(
        "Rigol DSG family",
        DeviceCategory.SIGNAL_GENERATOR,
        re.compile(r"^DSG"),
        ("RIGOLTECHNOLOGIES", "RIGOL"),
    ),
    _FamilyRule(
        "Tektronix RSA family",
        DeviceCategory.SPECTRUM_ANALYZER,
        re.compile(r"^RSA"),
        ("TEKTRONIX",),
    ),
    _FamilyRule(
        "Common oscilloscope model family",
        DeviceCategory.OSCILLOSCOPE,
        re.compile(r"^(DSO|MSO|DPO|RTO|RTP)"),
    ),
    _FamilyRule(
        "Keysight/Agilent 344xx family",
        DeviceCategory.DIGITAL_MULTIMETER,
        re.compile(r"^344\d{2}[A-Z]?$"),
        _KEYSIGHT_FAMILY,
    ),
    _FamilyRule(
        "Rigol DP power-supply family",
        DeviceCategory.POWER_SUPPLY,
        re.compile(r"^DP\d"),
        ("RIGOLTECHNOLOGIES", "RIGOL"),
    ),
    _FamilyRule(
        "Siglent SPD power-supply family",
        DeviceCategory.POWER_SUPPLY,
        re.compile(r"^SPD"),
        ("SIGLENTTECHNOLOGIES", "SIGLENT"),
    ),
    _FamilyRule(
        "Keysight/Agilent E36xxx power-supply family",
        DeviceCategory.POWER_SUPPLY,
        re.compile(r"^E36\d{3}"),
        _KEYSIGHT_FAMILY,
    ),
    _FamilyRule(
        "R&S/HAMEG HMP power-supply family",
        DeviceCategory.POWER_SUPPLY,
        re.compile(r"^HMP"),
        (*_ROHDE_SCHWARZ, "HAMEG"),
    ),
    _FamilyRule(
        "Kikusui PMX power-supply family",
        DeviceCategory.POWER_SUPPLY,
        re.compile(r"^PMX"),
        ("KIKUSUI",),
    ),
    _FamilyRule(
        "Keysight/Agilent E49xx LCR family",
        DeviceCategory.LCR_METER,
        re.compile(r"^E49\d{2}"),
        _KEYSIGHT_FAMILY,
    ),
    _FamilyRule(
        "Keysight/Agilent N52xx network-analyzer family",
        DeviceCategory.NETWORK_ANALYZER,
        re.compile(r"^N52\d{2}"),
        _KEYSIGHT_FAMILY,
    ),
    _FamilyRule(
        "R&S ZN-series network-analyzer family",
        DeviceCategory.NETWORK_ANALYZER,
        re.compile(r"^(ZNA|ZNB|ZND|ZNL|ZVA|ZVB|ZVL)"),
        _ROHDE_SCHWARZ,
    ),
)


def classify_identity(identity: InstrumentIdentity) -> ClassificationResult:
    manufacturer = _normalized_token(identity.manufacturer)
    model = _normalized_token(identity.model)

    candidate_pack = match_representative_profile(identity)
    if candidate_pack is not None:
        return ClassificationResult(
            category=candidate_pack.category,
            confidence=ClassificationConfidence.EXACT_PROFILE,
            matched_rule=(
                f"후보 명령팩 {candidate_pack.profile_id}의 *IDN? 패턴과 일치"
            ),
            profile_id=candidate_pack.profile_id,
            profile_status="candidate_pack_unvalidated",
        )

    for rule in _FAMILY_RULES:
        if rule.matches(manufacturer, model):
            return ClassificationResult(
                category=rule.category,
                confidence=ClassificationConfidence.FAMILY_HEURISTIC,
                matched_rule=rule.name,
            )

    return ClassificationResult(
        category=DeviceCategory.UNKNOWN,
        confidence=ClassificationConfidence.UNKNOWN,
        matched_rule="No conservative classification rule matched",
    )
