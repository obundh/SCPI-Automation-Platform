from __future__ import annotations

import json
import re
import sys
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any

from .models import DeviceCategory, InstrumentIdentity


_CLASS_TO_CATEGORY = {
    "dc_power_supply": DeviceCategory.POWER_SUPPLY,
    "digital_multimeter": DeviceCategory.DIGITAL_MULTIMETER,
    "digital_oscilloscope": DeviceCategory.OSCILLOSCOPE,
    "function_arbitrary_waveform_generator": DeviceCategory.FUNCTION_GENERATOR,
    "lcr_meter": DeviceCategory.LCR_METER,
    "rf_signal_generator": DeviceCategory.SIGNAL_GENERATOR,
    "signal_and_spectrum_analyzer": DeviceCategory.SPECTRUM_ANALYZER,
    "spectrum_analyzer": DeviceCategory.SPECTRUM_ANALYZER,
    "vector_network_analyzer": DeviceCategory.NETWORK_ANALYZER,
}


@dataclass(frozen=True, slots=True)
class CatalogOperation:
    name: str
    scpi: str
    response_type: str = ""
    binary: bool = False


@dataclass(frozen=True, slots=True)
class CatalogCapability:
    capability_id: str
    label_ko: str
    group: str
    risk_level: str
    verification: str
    operations: tuple[CatalogOperation, ...]
    unit: str = ""
    note_ko: str = ""
    parameters: tuple[dict[str, Any], ...] = ()


@dataclass(frozen=True, slots=True)
class InstrumentProfile:
    profile_id: str
    manufacturer: str
    model_family: str
    models: tuple[str, ...]
    instrument_class: str
    category: DeviceCategory
    idn_patterns: tuple[str, ...]
    verification_status: str
    hardware_verified: bool
    capabilities: tuple[CatalogCapability, ...]

    @property
    def display_name(self) -> str:
        return f"{self.manufacturer} {self.model_family}".strip()


def _catalog_candidates() -> tuple[Path, ...]:
    package_file = Path(__file__).resolve()
    project_root = package_file.parents[3]
    candidates = [
        project_root / "scpi_catalog_2026-07-25" / "scpi_catalog.json",
        Path(sys.prefix)
        / "scpi_catalog_2026-07-25"
        / "scpi_catalog.json",
    ]
    bundle_root = getattr(sys, "_MEIPASS", None)
    if bundle_root:
        candidates.insert(
            0,
            Path(bundle_root) / "scpi_catalog_2026-07-25" / "scpi_catalog.json",
        )
    return tuple(candidates)


def catalog_path() -> Path:
    for candidate in _catalog_candidates():
        if candidate.is_file():
            return candidate
    searched = "\n".join(str(path) for path in _catalog_candidates())
    raise FileNotFoundError(f"SCPI 카탈로그를 찾지 못했습니다.\n{searched}")


def _operation(name: str, value: object) -> CatalogOperation:
    item = value if isinstance(value, dict) else {}
    return CatalogOperation(
        name=name,
        scpi=str(item.get("scpi", "")),
        response_type=str(item.get("response_type", "")),
        binary=bool(item.get("binary", False)),
    )


def _capability(value: object) -> CatalogCapability:
    item = value if isinstance(value, dict) else {}
    raw_operations = item.get("operations", {})
    operation_items = (
        raw_operations.items() if isinstance(raw_operations, dict) else ()
    )
    raw_parameters = item.get("parameters", ())
    parameters = tuple(
        dict(parameter)
        for parameter in raw_parameters
        if isinstance(parameter, dict)
    )
    return CatalogCapability(
        capability_id=str(item.get("capability_id", "")),
        label_ko=str(item.get("label_ko", "")),
        group=str(item.get("category", "기타")),
        risk_level=str(item.get("risk_level", "medium")),
        verification=str(item.get("verification", "profile_required")),
        operations=tuple(
            _operation(name, operation)
            for name, operation in operation_items
        ),
        unit=str(item.get("unit", "")),
        note_ko=str(item.get("note_ko", "")),
        parameters=parameters,
    )


def _profile(value: object) -> InstrumentProfile | None:
    item = value if isinstance(value, dict) else {}
    instrument_class = str(item.get("instrument_class", ""))
    category = _CLASS_TO_CATEGORY.get(instrument_class)
    if category is None:
        return None
    identification = item.get("identification", {})
    if not isinstance(identification, dict):
        identification = {}
    verification = item.get("verification", {})
    if not isinstance(verification, dict):
        verification = {}
    raw_capabilities = item.get("capabilities", ())
    return InstrumentProfile(
        profile_id=str(item.get("profile_id", "")),
        manufacturer=str(item.get("manufacturer", "")),
        model_family=str(item.get("model_family", "")),
        models=tuple(str(model) for model in item.get("models", ())),
        instrument_class=instrument_class,
        category=category,
        idn_patterns=tuple(
            str(pattern)
            for pattern in identification.get("idn_patterns", ())
        ),
        verification_status=str(verification.get("status", "")),
        hardware_verified=bool(
            verification.get("hardware_verified_by_catalog_owner", False)
        ),
        capabilities=tuple(
            _capability(capability)
            for capability in raw_capabilities
            if isinstance(capability, dict)
        ),
    )


@lru_cache(maxsize=1)
def catalog_profiles() -> tuple[InstrumentProfile, ...]:
    payload = json.loads(catalog_path().read_text(encoding="utf-8"))
    return tuple(
        profile
        for raw_profile in payload.get("profiles", ())
        if (profile := _profile(raw_profile)) is not None
    )


@lru_cache(maxsize=1)
def _profiles_by_id() -> dict[str, InstrumentProfile]:
    return {profile.profile_id: profile for profile in catalog_profiles()}


def profile_by_id(profile_id: str) -> InstrumentProfile | None:
    return _profiles_by_id().get(profile_id)


def representative_profiles(
    category: DeviceCategory | None = None,
) -> tuple[InstrumentProfile, ...]:
    profiles = catalog_profiles()
    if category is None:
        return profiles
    return tuple(profile for profile in profiles if profile.category is category)


def match_representative_profile(
    identity: InstrumentIdentity,
) -> InstrumentProfile | None:
    raw_idn = identity.raw.strip()
    for profile in catalog_profiles():
        for pattern in profile.idn_patterns:
            try:
                if re.fullmatch(pattern, raw_idn, flags=re.IGNORECASE):
                    return profile
            except re.error:
                continue
    return None


def recommended_profile(
    category: DeviceCategory,
    identity: InstrumentIdentity | None = None,
) -> InstrumentProfile | None:
    profiles = representative_profiles(category)
    if not profiles:
        return None
    if identity is None:
        return profiles[0]

    manufacturer_token = re.sub(
        r"[^A-Z0-9]+",
        "",
        identity.manufacturer.upper(),
    )
    model_token = re.sub(r"[^A-Z0-9]+", "", identity.model.upper())

    def score(profile: InstrumentProfile) -> tuple[int, int]:
        profile_manufacturer = re.sub(
            r"[^A-Z0-9]+",
            "",
            profile.manufacturer.upper(),
        )
        same_manufacturer = int(
            manufacturer_token
            and (
                manufacturer_token in profile_manufacturer
                or profile_manufacturer in manufacturer_token
            )
        )
        family_token = re.sub(
            r"[^A-Z0-9]+",
            "",
            profile.model_family.upper(),
        )
        prefix_length = 0
        for left, right in zip(model_token, family_token):
            if left != right:
                break
            prefix_length += 1
        return same_manufacturer, prefix_length

    return max(profiles, key=score)
