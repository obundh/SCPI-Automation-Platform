from __future__ import annotations

import math
from types import MappingProxyType
from typing import Iterable, Mapping

from scpi_automation.binding_registry import plan_binding_definition
from scpi_automation.identity import (
    CatalogCapability,
    DeviceCategory,
    catalog_profiles,
    profile_by_id,
)
from scpi_automation.validation import (
    LocalExtensionRegistry,
    OPTION_STATE_QUERIED,
    OPTION_STATE_UNSUPPORTED,
    PromotedLocalExtension,
    load_local_extension_registry,
)

from .models import (
    FeatureRisk,
    FeatureVerification,
    PlanArgumentBinding,
    RoutineFeature,
    RoutineParameter,
    SelectedFeature,
    SelectedInstrument,
)


def _feature(
    category: DeviceCategory,
    slug: str,
    display_name: str,
    description: str,
    risk: FeatureRisk = FeatureRisk.CAUTION,
) -> RoutineFeature:
    return RoutineFeature(
        feature_id=f"{category.value}.{slug}",
        category=category,
        display_name=display_name,
        description=description,
        risk=risk,
        verification=FeatureVerification.PROFILE_REQUIRED,
    )


_BASE_FEATURES_BY_CATEGORY = MappingProxyType(
    {
        DeviceCategory.SPECTRUM_ANALYZER: (
            _feature(
                DeviceCategory.SPECTRUM_ANALYZER,
                "set_center_frequency",
                "Center Frequency - 중심 주파수 설정",
                "분석 화면의 가운데에 놓을 주파수를 정해요.",
            ),
            _feature(
                DeviceCategory.SPECTRUM_ANALYZER,
                "set_span",
                "Span - 주파수 분석 범위 설정",
                "화면에 한 번에 보여 줄 주파수 폭을 정해요.",
            ),
            _feature(
                DeviceCategory.SPECTRUM_ANALYZER,
                "set_rbw",
                "RBW - 분해능 대역폭 설정",
                "서로 가까운 신호를 구분하는 분석 폭을 정해요.",
            ),
            _feature(
                DeviceCategory.SPECTRUM_ANALYZER,
                "single_sweep",
                "Single Sweep - 한 번 측정",
                "현재 설정으로 스펙트럼을 한 번 측정해요.",
                FeatureRisk.SAFE,
            ),
            _feature(
                DeviceCategory.SPECTRUM_ANALYZER,
                "peak_search",
                "Peak Search - 가장 높은 신호 찾기",
                "가장 높은 신호 위치로 마커를 옮겨요.",
                FeatureRisk.SAFE,
            ),
            _feature(
                DeviceCategory.SPECTRUM_ANALYZER,
                "read_marker",
                "Marker Read - 마커 값 읽기",
                "마커의 주파수와 신호 크기를 결과로 가져와요.",
                FeatureRisk.SAFE,
            ),
        ),
        DeviceCategory.SIGNAL_GENERATOR: (
            _feature(
                DeviceCategory.SIGNAL_GENERATOR,
                "set_frequency",
                "Frequency - 출력 주파수 설정",
                "RF 신호의 주파수를 정해요.",
            ),
            _feature(
                DeviceCategory.SIGNAL_GENERATOR,
                "set_power",
                "Power Level - 출력 세기 설정",
                "시험 대상에 들어갈 RF 신호의 세기를 정해요.",
                FeatureRisk.HAZARDOUS,
            ),
            _feature(
                DeviceCategory.SIGNAL_GENERATOR,
                "output_on",
                "RF Output ON - RF 출력 켜기",
                "설정한 RF 신호가 출력 단자로 나오게 해요.",
                FeatureRisk.HAZARDOUS,
            ),
            _feature(
                DeviceCategory.SIGNAL_GENERATOR,
                "output_off",
                "RF Output OFF - RF 출력 끄기",
                "RF 신호 출력을 멈춰요.",
                FeatureRisk.SAFE,
            ),
            _feature(
                DeviceCategory.SIGNAL_GENERATOR,
                "read_settings",
                "Readback - 현재 설정 읽기",
                "현재 주파수와 출력 세기를 읽어요.",
                FeatureRisk.SAFE,
            ),
        ),
        DeviceCategory.FUNCTION_GENERATOR: (
            _feature(
                DeviceCategory.FUNCTION_GENERATOR,
                "set_waveform",
                "Waveform - 파형 종류 설정",
                "정현파·구형파·펄스·임의파형 중 하나를 선택해요.",
            ),
            _feature(
                DeviceCategory.FUNCTION_GENERATOR,
                "set_frequency",
                "Frequency - 파형 주파수 설정",
                "선택 채널의 파형 주파수를 정해요.",
            ),
            _feature(
                DeviceCategory.FUNCTION_GENERATOR,
                "set_amplitude",
                "Amplitude - 파형 진폭 설정",
                "시험 대상에 인가할 파형 진폭을 정해요.",
                FeatureRisk.HAZARDOUS,
            ),
            _feature(
                DeviceCategory.FUNCTION_GENERATOR,
                "output_off",
                "Output OFF - 채널 출력 끄기",
                "선택 채널의 출력을 안전하게 꺼요.",
                FeatureRisk.SAFE,
            ),
        ),
        DeviceCategory.OSCILLOSCOPE: (
            _feature(
                DeviceCategory.OSCILLOSCOPE,
                "set_time_scale",
                "Time/Div - 시간 축 설정",
                "화면 한 칸이 나타내는 시간을 정해요.",
            ),
            _feature(
                DeviceCategory.OSCILLOSCOPE,
                "set_voltage_scale",
                "Volts/Div - 전압 축 설정",
                "선택 채널의 전압 축을 정해요.",
            ),
            _feature(
                DeviceCategory.OSCILLOSCOPE,
                "set_trigger_level",
                "Trigger Level - 트리거 기준 설정",
                "파형을 잡기 시작할 기준 전압을 정해요.",
            ),
            _feature(
                DeviceCategory.OSCILLOSCOPE,
                "single_acquisition",
                "Single Acquisition - 파형 한 번 잡기",
                "조건에 맞는 파형을 한 번 잡고 멈춰요.",
                FeatureRisk.SAFE,
            ),
            _feature(
                DeviceCategory.OSCILLOSCOPE,
                "read_measurement",
                "Measurement - 측정값 읽기",
                "파형 또는 계산된 값을 결과로 가져와요.",
                FeatureRisk.SAFE,
            ),
        ),
        DeviceCategory.DIGITAL_MULTIMETER: (
            _feature(
                DeviceCategory.DIGITAL_MULTIMETER,
                "set_measurement_mode",
                "Function - 측정 종류 선택",
                "전압·전류·저항 중 무엇을 측정할지 정해요.",
            ),
            _feature(
                DeviceCategory.DIGITAL_MULTIMETER,
                "set_range",
                "Range - 측정 범위 설정",
                "예상되는 값에 맞춰 측정 범위를 정해요.",
            ),
            _feature(
                DeviceCategory.DIGITAL_MULTIMETER,
                "read_value",
                "Read - 측정값 읽기",
                "멀티미터의 측정값을 결과로 가져와요.",
                FeatureRisk.SAFE,
            ),
        ),
        DeviceCategory.POWER_SUPPLY: (
            _feature(
                DeviceCategory.POWER_SUPPLY,
                "set_voltage",
                "Voltage - 출력 전압 설정",
                "시험 대상에 공급할 전압을 정해요.",
                FeatureRisk.HAZARDOUS,
            ),
            _feature(
                DeviceCategory.POWER_SUPPLY,
                "set_current_limit",
                "Current Limit - 전류 제한 설정",
                "흐를 수 있는 최대 전류를 정해요.",
                FeatureRisk.HAZARDOUS,
            ),
            _feature(
                DeviceCategory.POWER_SUPPLY,
                "output_on",
                "Output ON - 전원 출력 켜기",
                "출력 단자에 전압을 공급해요.",
                FeatureRisk.HAZARDOUS,
            ),
            _feature(
                DeviceCategory.POWER_SUPPLY,
                "output_off",
                "Output OFF - 전원 출력 끄기",
                "출력 단자의 전원 공급을 멈춰요.",
                FeatureRisk.SAFE,
            ),
            _feature(
                DeviceCategory.POWER_SUPPLY,
                "read_output",
                "Output Readback - 출력 상태 읽기",
                "실제 전압과 전류를 결과로 가져와요.",
                FeatureRisk.SAFE,
            ),
        ),
        DeviceCategory.LCR_METER: (
            _feature(
                DeviceCategory.LCR_METER,
                "set_frequency",
                "Frequency - 측정 주파수 설정",
                "부품을 측정할 AC 시험 주파수를 정해요.",
            ),
            _feature(
                DeviceCategory.LCR_METER,
                "set_function",
                "Function - 임피던스 측정 함수 선택",
                "Cp-D, Ls-Q, R-X 등 결과 조합을 선택해요.",
            ),
            _feature(
                DeviceCategory.LCR_METER,
                "read_impedance",
                "Impedance Read - 임피던스 결과 읽기",
                "선택 함수의 측정 결과와 상태를 가져와요.",
                FeatureRisk.SAFE,
            ),
        ),
        DeviceCategory.NETWORK_ANALYZER: (
            _feature(
                DeviceCategory.NETWORK_ANALYZER,
                "set_frequency_range",
                "Frequency Sweep - 주파수 범위 설정",
                "S-파라미터를 측정할 시작·종료 주파수를 정해요.",
            ),
            _feature(
                DeviceCategory.NETWORK_ANALYZER,
                "set_if_bandwidth",
                "IFBW - IF 대역폭 설정",
                "측정 노이즈와 속도를 결정하는 IF 대역폭을 정해요.",
            ),
            _feature(
                DeviceCategory.NETWORK_ANALYZER,
                "read_trace",
                "Trace Read - S-파라미터 Trace 읽기",
                "활성 Trace의 포맷된 데이터를 가져와요.",
                FeatureRisk.SAFE,
            ),
        ),
        DeviceCategory.UNKNOWN: (
            _feature(
                DeviceCategory.UNKNOWN,
                "review_identity",
                "IDN - 장비 정보 다시 확인",
                "기준 명령팩을 선택하기 전에 제조사와 모델 정보를 확인해요.",
                FeatureRisk.SAFE,
            ),
        ),
    }
)


_ENGLISH_NAMES = {
    "analyzer.frequency.center": "Center Frequency",
    "analyzer.frequency.span": "Span",
    "analyzer.frequency.start": "Start Frequency",
    "analyzer.frequency.stop": "Stop Frequency",
    "analyzer.frequency.cw": "CW Frequency",
    "analyzer.input.attenuation": "Input Attenuation",
    "analyzer.rbw": "RBW",
    "analyzer.vbw": "VBW",
    "display.reference_level": "Reference Level",
    "marker.amplitude": "Marker Amplitude",
    "marker.frequency": "Marker Frequency",
    "marker.peak_search": "Peak Search",
    "marker.next_peak": "Next Peak",
    "marker.state": "Marker State",
    "trace.read": "Trace Data",
    "trace.format": "Trace Format",
    "trace.mode": "Trace Mode",
    "trace.data.formatted": "Formatted Trace Data",
    "waveform.data": "Waveform Data",
    "waveform.preamble": "Waveform Preamble",
    "waveform.frequency": "Waveform Frequency",
    "waveform.amplitude": "Amplitude",
    "waveform.shape": "Waveform",
    "rf.output.state": "RF Output",
    "output.state": "Output",
    "output.master_state": "Master Output",
    "channel.output.state": "Channel Output",
    "source.frequency": "RF Frequency",
    "source.power": "Source Power",
    "source.voltage": "Voltage",
    "source.current": "Current",
    "channel.voltage.setpoint": "Channel Voltage",
    "channel.current.limit": "Current Limit",
    "measurement.read": "Measurement Read",
    "measurement.fetch": "Measurement Fetch",
    "measurement.voltage": "Voltage Read",
    "measurement.current": "Current Read",
    "measurement.impedance.formatted": "Formatted Impedance",
    "measurement.impedance.corrected": "Corrected Impedance",
    "lcr.frequency": "Measurement Frequency",
    "lcr.measurement.function": "LCR Function",
    "vna.if_bandwidth": "IF Bandwidth",
}

_KOREAN_NAMES = {
    "analyzer.frequency.span": "주파수 분석 범위",
    "analyzer.rbw": "분해능 대역폭",
    "analyzer.vbw": "비디오 대역폭",
    "trace.active": "활성 트레이스",
    "trace.catalog": "트레이스 목록",
    "trace.data.formatted": "포맷된 트레이스 데이터",
    "trace.format": "트레이스 형식",
    "trace.mode": "트레이스 모드",
    "trace.read": "트레이스 데이터",
    "trace.select": "트레이스 선택",
}


def _english_name(capability_id: str) -> str:
    if capability_id in _ENGLISH_NAMES:
        return _ENGLISH_NAMES[capability_id]
    replacements = {
        "rbw": "RBW",
        "vbw": "VBW",
        "rf": "RF",
        "dc": "DC",
        "ac": "AC",
        "lcr": "LCR",
        "vna": "VNA",
        "nplc": "NPLC",
        "arb": "ARB",
        "cw": "CW",
    }
    words = capability_id.replace("_", " ").replace(".", " ").split()
    return " ".join(
        replacements.get(word.lower(), word.capitalize())
        for word in words
    )


def _parameter(value: Mapping[str, object]) -> RoutineParameter:
    mapping = value.get("mapping", {})
    mapping_items = (
        tuple((str(key), str(item)) for key, item in mapping.items())
        if isinstance(mapping, dict)
        else ()
    )
    return RoutineParameter(
        name=str(value.get("name", "")),
        value_type=str(value.get("type", "string")),
        unit=str(value.get("unit", "")),
        minimum=(
            value.get("minimum")
            if isinstance(value.get("minimum"), (int, float))
            else None
        ),
        maximum=(
            value.get("maximum")
            if isinstance(value.get("maximum"), (int, float))
            else None
        ),
        choices=tuple(str(item) for item in value.get("choices", ())),
        mapping=mapping_items,
        note_ko=str(value.get("note_ko", "")),
    )


def _operation_parameters(
    capability: CatalogCapability,
    scpi: str,
) -> tuple[RoutineParameter, ...]:
    """Return only parameters referenced by this operation's SCPI template."""

    return tuple(
        _parameter(parameter)
        for parameter in capability.parameters
        if (
            (name := str(parameter.get("name", "")))
            and f"{{{name}}}" in scpi
        )
    )


def _feature_verification(value: str) -> FeatureVerification:
    if value == "hardware_verified_by_catalog_owner":
        return FeatureVerification.VERIFIED
    if value == "live_hardware_source_confirmed":
        return FeatureVerification.BENCH_OBSERVED
    return FeatureVerification.PROFILE_REQUIRED


def _operation_display(
    capability: CatalogCapability,
    operation: str,
) -> str:
    english = _english_name(capability.capability_id)
    label = _KOREAN_NAMES.get(
        capability.capability_id,
        capability.label_ko or english,
    )
    if capability.capability_id.startswith("trace.") and "트레이스" not in label:
        label = label.replace("Trace", "트레이스")
        if "트레이스" not in label:
            label = f"트레이스 {label}"
    if operation == "set":
        if "설정" not in label and "ON/OFF" not in label:
            label = f"{label} 설정"
        return f"{english} - {label}"
    if operation == "query":
        if "읽기" not in label and "조회" not in label:
            label = f"{label} 읽기"
        return f"{english} Read - {label}"
    return f"{english} - {label}"


def _operation_description(
    capability: CatalogCapability,
    operation: str,
    parameters: tuple[RoutineParameter, ...],
) -> str:
    operation_text = {
        "set": "장비 설정을 변경하는 기능이에요.",
        "query": "장비의 현재 값이나 측정 결과를 읽는 기능이에요.",
        "execute": "장비에서 해당 동작을 실행하는 기능이에요.",
    }.get(operation, "후보 명령팩에 등록된 기능이에요.")
    parameter_names = ", ".join(
        parameter.name
        for parameter in parameters
    )
    pieces = [operation_text]
    if parameter_names:
        pieces.append(f"입력값: {parameter_names}.")
    if capability.note_ko:
        pieces.append(capability.note_ko)
    return " ".join(pieces)


def _dynamic_feature_id(
    category: DeviceCategory,
    capability_id: str,
    operation: str,
) -> str:
    return f"{category.value}.cap.{capability_id}.{operation}"


def _build_dynamic_catalog() -> tuple[
    dict[str, RoutineFeature],
    dict[str, tuple[RoutineFeature, ...]],
]:
    features_by_id: dict[str, RoutineFeature] = {}
    features_by_profile: dict[str, tuple[RoutineFeature, ...]] = {}
    risk_map = {
        "low": FeatureRisk.SAFE,
        "medium": FeatureRisk.CAUTION,
        "high": FeatureRisk.HAZARDOUS,
    }

    for profile in catalog_profiles():
        profile_features: list[RoutineFeature] = []
        for capability in profile.capabilities:
            for operation in capability.operations:
                feature_id = _dynamic_feature_id(
                    profile.category,
                    capability.capability_id,
                    operation.name,
                )
                parameters = _operation_parameters(
                    capability,
                    operation.scpi,
                )
                profile_feature = RoutineFeature(
                    feature_id=feature_id,
                    category=profile.category,
                    display_name=_operation_display(
                        capability,
                        operation.name,
                    ),
                    description=_operation_description(
                        capability,
                        operation.name,
                        parameters,
                    ),
                    risk=risk_map.get(
                        capability.risk_level,
                        FeatureRisk.CAUTION,
                    ),
                    verification=_feature_verification(
                        capability.verification,
                    ),
                    capability_id=capability.capability_id,
                    operation=operation.name,
                    group=capability.group,
                    scpi_preview=operation.scpi,
                    response_type=operation.response_type,
                    parameters=parameters,
                    profile_ids=(profile.profile_id,),
                )
                profile_features.append(profile_feature)
                existing = features_by_id.get(feature_id)
                if existing is None:
                    features_by_id[feature_id] = profile_feature
                elif profile.profile_id not in existing.profile_ids:
                    features_by_id[feature_id] = RoutineFeature(
                        feature_id=existing.feature_id,
                        category=existing.category,
                        display_name=existing.display_name,
                        description=existing.description,
                        risk=existing.risk,
                        verification=existing.verification,
                        capability_id=existing.capability_id,
                        operation=existing.operation,
                        group=existing.group,
                        scpi_preview=existing.scpi_preview,
                        response_type=existing.response_type,
                        parameters=existing.parameters,
                        profile_ids=existing.profile_ids + (profile.profile_id,),
                    )
        features_by_profile[profile.profile_id] = tuple(profile_features)

    return features_by_id, features_by_profile


_DYNAMIC_FEATURES_BY_ID, _DYNAMIC_FEATURES_BY_PROFILE = (
    _build_dynamic_catalog()
)
_DYNAMIC_FEATURES_BY_PROFILE_AND_ID = {
    profile_id: MappingProxyType(
        {feature.feature_id: feature for feature in features}
    )
    for profile_id, features in _DYNAMIC_FEATURES_BY_PROFILE.items()
}
_BASE_FEATURES_BY_ID = {
    feature.feature_id: feature
    for category_features in _BASE_FEATURES_BY_CATEGORY.values()
    for feature in category_features
}
_FEATURES_BY_ID = MappingProxyType(
    {**_BASE_FEATURES_BY_ID, **_DYNAMIC_FEATURES_BY_ID}
)


def _load_extensions_fail_closed() -> LocalExtensionRegistry:
    """Do not expose local commands when their evidence registry is unreadable."""

    try:
        return load_local_extension_registry()
    except (OSError, ValueError):
        return LocalExtensionRegistry()


def _local_record_features(
    record: PromotedLocalExtension,
) -> tuple[RoutineFeature, ...]:
    definition = record.definition
    capability = definition.as_capability()
    risk_map = {
        "low": FeatureRisk.SAFE,
        "medium": FeatureRisk.CAUTION,
        "high": FeatureRisk.HAZARDOUS,
        "hazardous": FeatureRisk.HAZARDOUS,
        "critical": FeatureRisk.HAZARDOUS,
    }
    return tuple(
        RoutineFeature(
            feature_id=_dynamic_feature_id(
                definition.category,
                capability.capability_id,
                operation.name,
            ),
            category=definition.category,
            display_name={
                "query": f"{definition.label_ko} Read - 값 읽기",
                "set": f"{definition.label_ko} Set - 설정",
                "execute": f"{definition.label_ko} Run - 실행",
            }.get(operation.name, definition.label_ko),
            description=(
                _operation_description(
                    capability,
                    operation.name,
                    _operation_parameters(capability, operation.scpi),
                )
                + f" Source: {definition.manual_title} p.{definition.manual_page}."
            ),
            risk=risk_map.get(
                definition.risk_level,
                FeatureRisk.HAZARDOUS,
            ),
            verification=FeatureVerification.VERIFIED,
            capability_id=capability.capability_id,
            operation=operation.name,
            group=capability.group,
            scpi_preview=operation.scpi,
            response_type=operation.response_type,
            parameters=_operation_parameters(capability, operation.scpi),
            profile_ids=(definition.source_profile_id,),
        )
        for operation in capability.operations
        if (
            f"{capability.capability_id}::{operation.name}"
            in record.compatible_operation_ids
        )
    )


def local_extension_features_for(
    profile_id: str,
    compatible_operation_ids: Iterable[str] = (),
    *,
    registry: LocalExtensionRegistry | None = None,
) -> tuple[RoutineFeature, ...]:
    """Return only identity-validation-approved local extension operations."""

    allowed = frozenset(compatible_operation_ids)
    if not allowed:
        return ()
    selected_registry = registry or _load_extensions_fail_closed()
    return tuple(
        feature
        for record in selected_registry.for_profile(profile_id)
        for feature in _local_record_features(record)
        if (
            f"{feature.capability_id}::{feature.operation}"
            in allowed
        )
    )


def _local_extension_feature_by_id(
    feature_id: str,
    profile_id: str = "",
) -> RoutineFeature | None:
    registry = _load_extensions_fail_closed()
    return next(
        (
            feature
            for record in registry.records
            if (
                not profile_id
                or record.definition.source_profile_id == profile_id
            )
            for feature in _local_record_features(record)
            if feature.feature_id == feature_id
        ),
        None,
    )


def features_for(
    category: DeviceCategory,
    profile_id: str = "",
    compatible_capability_ids: Iterable[str] = (),
    compatibility_status: str = "",
    compatible_operation_ids: Iterable[str] = (),
) -> tuple[RoutineFeature, ...]:
    """Return model-specific operations, or the category fallback catalog."""

    profile = profile_by_id(profile_id) if profile_id else None
    if profile is not None and profile.category is category:
        features = _DYNAMIC_FEATURES_BY_PROFILE.get(profile.profile_id, ())
        allowed = frozenset(compatible_capability_ids)
        allowed_operations = frozenset(compatible_operation_ids)
        if allowed_operations:
            features = tuple(
                feature
                for feature in features
                if (
                    f"{feature.capability_id}::{feature.operation}"
                    in allowed_operations
                )
            )
            features += local_extension_features_for(
                profile.profile_id,
                allowed_operations,
            )
        elif compatibility_status == "demo_catalog_preview":
            return features
        elif compatibility_status in {
            "candidate_pack_unvalidated",
            "user_compatible",
            "hardware_validated",
            "hardware_validated_partial",
        } or allowed:
            # Legacy capability-level approval and IDN pattern matches do not
            # prove that a particular set/query/execute operation works.
            # Only an operation-level PASS allowlist may unlock routine items.
            features = ()
        return features
    return _BASE_FEATURES_BY_CATEGORY[category]


def feature_by_id(
    feature_id: str,
    profile_id: str = "",
) -> RoutineFeature:
    if profile_id:
        profile_features = _DYNAMIC_FEATURES_BY_PROFILE_AND_ID.get(profile_id)
        if profile_features is not None:
            profile_feature = profile_features.get(feature_id)
            if profile_feature is not None:
                return profile_feature
    try:
        return _FEATURES_BY_ID[feature_id]
    except KeyError as exc:
        local_feature = _local_extension_feature_by_id(
            feature_id,
            profile_id,
        )
        if local_feature is not None:
            return local_feature
        raise KeyError(f"등록되지 않은 루틴 기능입니다: {feature_id}") from exc


def _normalize_arguments(
    feature: RoutineFeature,
    arguments: Mapping[str, object] | Iterable[tuple[str, object]],
    *,
    allowed_missing: Iterable[str] = (),
) -> tuple[tuple[str, str], ...]:
    items = arguments.items() if isinstance(arguments, Mapping) else arguments
    normalized = tuple((str(name), str(value).strip()) for name, value in items)
    by_name = {name: value for name, value in normalized}
    expected = {parameter.name for parameter in feature.parameters}
    unknown = set(by_name) - expected
    if unknown:
        raise ValueError(
            "등록되지 않은 기능 인수입니다: " + ", ".join(sorted(unknown))
        )
    allowed_missing_names = frozenset(allowed_missing)
    unknown_missing = allowed_missing_names - expected
    if unknown_missing:
        raise ValueError(
            "계획값을 연결할 수 없는 기능 인수입니다: "
            + ", ".join(sorted(unknown_missing))
        )
    missing = expected - set(by_name) - allowed_missing_names
    if missing:
        raise ValueError(
            "기능에 필요한 값을 입력해 주세요: " + ", ".join(sorted(missing))
        )
    for parameter in feature.parameters:
        if parameter.name not in by_name:
            continue
        value = by_name[parameter.name]
        if not value:
            raise ValueError(f"{parameter.name} 값을 입력해 주세요.")
        mapped_values = tuple(key for key, _mapped in parameter.mapping)
        if mapped_values and value not in mapped_values:
            raise ValueError(
                f"{parameter.name}은(는) 다음 중 하나여야 합니다: "
                + ", ".join(mapped_values)
            )
        if parameter.value_type == "boolean" and not mapped_values:
            boolean_values = ("false", "true")
            if value not in boolean_values:
                raise ValueError(
                    f"{parameter.name}은(는) 다음 중 하나여야 합니다: "
                    + ", ".join(boolean_values)
                )
        if parameter.value_type == "enum":
            if value not in parameter.choices:
                raise ValueError(
                    f"{parameter.name}은(는) 다음 중 하나여야 합니다: "
                    + ", ".join(parameter.choices)
                )
            continue
        if parameter.value_type == "float_or_enum":
            if value in parameter.choices:
                continue
            try:
                number = float(value)
            except ValueError as exc:
                raise ValueError(
                    f"{parameter.name}은(는) 유한한 숫자여야 합니다."
                ) from exc
            if not math.isfinite(number):
                raise ValueError(
                    f"{parameter.name}은(는) 유한한 숫자여야 합니다."
                )
            continue
        if parameter.value_type == "float_or_string":
            if value.upper() in {"INF", "MIN", "MAX", "DEF"}:
                continue
            try:
                number = float(value)
            except ValueError as exc:
                raise ValueError(
                    f"{parameter.name}은(는) 숫자 또는 "
                    "INF/MIN/MAX/DEF여야 합니다."
                ) from exc
            if not math.isfinite(number):
                raise ValueError(
                    f"{parameter.name}은(는) 유한한 숫자 또는 "
                    "INF/MIN/MAX/DEF여야 합니다."
                )
            continue
        if parameter.value_type == "voltage_current_time_triplets":
            values = tuple(part.strip() for part in value.split(","))
            if (
                len(values) < 3
                or len(values) % 3
                or len(values) // 3 > 128
                or any(not part for part in values)
            ):
                raise ValueError(
                    f"{parameter.name}은(는) 전압, 전류, 체류시간을 "
                    "3개 단위로 최대 128점까지 입력해야 합니다."
                )
            try:
                numbers = tuple(float(part) for part in values)
            except ValueError as exc:
                raise ValueError(
                    f"{parameter.name}에는 숫자만 입력해야 합니다."
                ) from exc
            if any(not math.isfinite(number) for number in numbers):
                raise ValueError(
                    f"{parameter.name}에는 유한한 숫자만 입력해야 합니다."
                )
            if any(
                not 0.06 <= dwell <= 10
                for dwell in numbers[2::3]
            ):
                raise ValueError(
                    f"{parameter.name}의 체류시간은 "
                    "0.06초 이상 10초 이하여야 합니다."
                )
            continue

        numeric_kind = {
            "integer": "integer",
            "float": "float",
            "number": "float",
            "number_or_auto": "float",
            "integer_or_mnemonic": "integer",
            "float_or_mnemonic": "float",
        }.get(parameter.value_type)
        if numeric_kind is not None:
            if (
                parameter.value_type == "number_or_auto"
                and value.upper() == "AUTO"
            ):
                continue
            if (
                parameter.value_type
                in {"integer_or_mnemonic", "float_or_mnemonic"}
                and value in parameter.choices
            ):
                continue
            try:
                number = float(value)
            except ValueError as exc:
                raise ValueError(f"{parameter.name}은(는) 숫자여야 합니다.") from exc
            if not math.isfinite(number):
                raise ValueError(f"{parameter.name}은(는) 유한한 숫자여야 합니다.")
            if numeric_kind == "integer" and not number.is_integer():
                raise ValueError(f"{parameter.name}은(는) 정수여야 합니다.")
            if parameter.minimum is not None and number < parameter.minimum:
                raise ValueError(
                    f"{parameter.name}은(는) {parameter.minimum} 이상이어야 합니다."
                )
            if parameter.maximum is not None and number > parameter.maximum:
                raise ValueError(
                    f"{parameter.name}은(는) {parameter.maximum} 이하여야 합니다."
                )
    return normalized


def select_feature(
    instrument: SelectedInstrument,
    feature_id: str,
    *,
    arguments: Mapping[str, object] | Iterable[tuple[str, object]] = (),
    plan_bindings: Iterable[
        PlanArgumentBinding | tuple[str, str]
    ] = (),
    result_name: str = "",
) -> SelectedFeature:
    profile = profile_by_id(instrument.profile_id) if instrument.profile_id else None
    feature = feature_by_id(feature_id, instrument.profile_id)
    local_record: PromotedLocalExtension | None = None
    if feature.capability_id.startswith("local."):
        local_operation_id = (
            f"{feature.capability_id}::{feature.operation}"
        )
        local_record = _load_extensions_fail_closed().by_operation_id(
            local_operation_id
        )
        if local_record is None:
            raise ValueError(
                "로컬 기능의 실장비 검증 증거를 찾을 수 없습니다."
            )
        definition = local_record.definition
        expected_identity = tuple(
            value.strip().casefold()
            for value in (
                definition.identity_raw,
                definition.identity_manufacturer,
                definition.identity_model,
                definition.identity_serial,
                definition.identity_firmware,
            )
        )
        actual_identity = tuple(
            value.strip().casefold()
            for value in (
                instrument.raw_idn,
                instrument.manufacturer,
                instrument.model,
                instrument.serial,
                instrument.firmware,
            )
        )
        options_match = False
        if definition.identity_options_state == OPTION_STATE_QUERIED:
            options_match = (
                instrument.option_state == OPTION_STATE_QUERIED
                and bool(instrument.option_response.strip())
                and (
                definition.identity_options.strip().casefold()
                == instrument.option_response.strip().casefold()
                )
            )
        elif definition.identity_options_state == OPTION_STATE_UNSUPPORTED:
            profile_has_option_query = bool(
                profile is not None
                and any(
                    operation.name == "query"
                    and "".join(operation.scpi.split()).upper() == "*OPT?"
                    for capability in profile.capabilities
                    for operation in capability.operations
                )
            )
            options_match = (
                instrument.option_state == OPTION_STATE_UNSUPPORTED
                and
                not profile_has_option_query
                and not instrument.option_response.strip()
            )
        if (
            definition.source_profile_id != instrument.profile_id
            or expected_identity != actual_identity
            or not options_match
        ):
            raise ValueError(
                "이 로컬 기능은 다른 제조사·모델·시리얼·펌웨어·옵션에서 "
                "검증된 기록입니다."
            )
    if feature.category is not instrument.category:
        raise ValueError(
            f"'{feature.display_name}' 기능은 "
            f"{instrument.category.label_ko}에서 사용할 수 없습니다."
        )
    if (
        profile is not None
        and feature.capability_id
        and instrument.profile_id not in feature.profile_ids
    ):
        raise ValueError("이 후보 명령팩에 등록되지 않은 기능입니다.")
    if (
        not feature.capability_id
        and instrument.compatibility_status
        not in {"", "demo_catalog_preview"}
    ):
        raise ValueError(
            "이 공통 기능 카드는 화면 설명·데모용이며 실행 명령이 아닙니다. "
            "현재 장비에서 operation별 검증을 통과한 모델 기능을 선택해 주세요."
        )
    if (
        instrument.compatible_operation_ids
        and feature.capability_id
        and (
            f"{feature.capability_id}::{feature.operation}"
            not in instrument.compatible_operation_ids
        )
    ):
        raise ValueError("실장비에서 검증되지 않은 명령은 루틴에 추가할 수 없습니다.")
    if (
        not instrument.compatible_operation_ids
        and instrument.compatibility_status
        in {
            "candidate_pack_unvalidated",
            "user_compatible",
            "hardware_validated",
            "hardware_validated_partial",
        }
        and feature.capability_id
    ):
        raise ValueError(
            "operation별 실장비 검증을 통과하지 않은 기능은 루틴에 추가할 수 없습니다."
        )
    normalized_bindings: list[PlanArgumentBinding] = []
    for raw_binding in plan_bindings:
        if isinstance(raw_binding, PlanArgumentBinding):
            binding = raw_binding
        else:
            try:
                parameter_name, field_id = raw_binding
            except (TypeError, ValueError) as exc:
                raise ValueError(
                    "계획값 연결은 (기능 인수, 계획 필드) 형식이어야 합니다."
                ) from exc
            binding = PlanArgumentBinding(
                parameter_name=str(parameter_name),
                field_id=str(field_id),
            )
        definition = plan_binding_definition(
            feature.capability_id,
            feature.operation,
            binding.parameter_name,
        )
        if definition is None:
            raise ValueError(
                f"{feature.display_name}의 {binding.parameter_name} 인수는 "
                "시험 계획값과 연결하도록 검토된 항목이 아닙니다."
            )
        if definition.field_id != binding.field_id:
            raise ValueError(
                f"{feature.display_name}의 {binding.parameter_name}에는 "
                f"{definition.field_id} 계획값만 연결할 수 있습니다."
            )
        normalized_bindings.append(binding)
    binding_names = tuple(
        binding.parameter_name for binding in normalized_bindings
    )
    if len(set(binding_names)) != len(binding_names):
        raise ValueError("같은 기능 인수에 계획값을 두 번 연결할 수 없습니다.")
    normalized_arguments = _normalize_arguments(
        feature,
        arguments,
        allowed_missing=binding_names,
    )
    argument_names = {name for name, _value in normalized_arguments}
    overlap = argument_names.intersection(binding_names)
    if overlap:
        raise ValueError(
            "고정값과 계획값을 함께 지정한 기능 인수입니다: "
            + ", ".join(sorted(overlap))
        )
    if local_record is not None:
        operation_id_value = (
            f"{feature.capability_id}::{feature.operation}"
        )
        validated_arguments = local_record.definition.operation_arguments.get(
            operation_id_value,
            {},
        )
        expected_arguments = _normalize_arguments(
            feature,
            validated_arguments,
        )
        if dict(normalized_arguments) != dict(expected_arguments):
            expected_text = (
                ", ".join(
                    f"{name}={value}"
                    for name, value in expected_arguments
                )
                or "입력값 없음"
            )
            raise ValueError(
                "이 로컬 기능은 아직 한 가지 시험 인수 조합만 검증됐습니다. "
                f"현재 허용값: {expected_text}. 다른 값·채널·Trace·선택지는 "
                "별도 기능 후보로 다시 검증해 주세요."
            )
    _enforce_validated_model_constraints(
        instrument,
        profile,
        feature,
        normalized_arguments,
    )
    return SelectedFeature(
        instrument=instrument,
        feature_id=feature.feature_id,
        arguments=normalized_arguments,
        plan_bindings=tuple(normalized_bindings),
        result_name=result_name.strip(),
    )


_SELECTOR_PARAMETER_NAMES = frozenset(
    {"channel", "trace", "marker", "port", "window", "input"}
)
_NUMERIC_PARAMETER_TYPES = frozenset(
    {
        "integer",
        "float",
        "number",
        "number_or_auto",
        "integer_or_mnemonic",
        "float_or_mnemonic",
    }
)
_FSV_FREQUENCY_MAXIMUM_BY_MODEL = MappingProxyType(
    {
        "FSV3": 3_000_000_000.0,
        "FSV7": 7_000_000_000.0,
        "FSV13": 13_600_000_000.0,
        "FSV30": 30_000_000_000.0,
        "FSV40": 40_000_000_000.0,
        "FSVA4": 4_000_000_000.0,
        "FSVA7": 7_000_000_000.0,
        "FSVA13": 13_600_000_000.0,
        "FSVA30": 30_000_000_000.0,
        "FSVA40": 40_000_000_000.0,
    }
)
_FSV_ABSOLUTE_FREQUENCY_CAPABILITIES = frozenset(
    {
        "analyzer.frequency.center",
        "analyzer.frequency.start",
        "analyzer.frequency.stop",
        "marker.x",
        "marker.search_limits.left",
        "marker.search_limits.right",
    }
)


def _enforce_validated_model_constraints(
    instrument: SelectedInstrument,
    profile,
    feature: RoutineFeature,
    arguments: tuple[tuple[str, str], ...],
) -> None:
    """Keep one probe PASS from granting unverified model-wide ranges."""

    if (
        profile is None
        or instrument.compatibility_status
        not in {"hardware_validated", "hardware_validated_partial"}
        or feature.operation != "set"
    ):
        return
    normalized_model = instrument.model.strip().upper()
    declared_models = {model.strip().upper() for model in profile.models}
    numeric_value_parameters = tuple(
        parameter
        for parameter in feature.parameters
        if parameter.name not in _SELECTOR_PARAMETER_NAMES
        and parameter.value_type in _NUMERIC_PARAMETER_TYPES
    )
    if normalized_model not in declared_models and numeric_value_parameters:
        if (
            feature.capability_id.startswith("local.")
            and all(
                parameter.minimum is not None
                and parameter.maximum is not None
                for parameter in numeric_value_parameters
            )
        ):
            # Identity-bound local extensions may carry model-manual limits.
            # The exact instrument/firmware/options evidence was checked above;
            # both bounds remain mandatory so one successful probe value never
            # opens an unbounded numeric control.
            return
        raise ValueError(
            "후보 명령은 통과했지만 이 모델의 수치 허용 범위가 아직 등록되지 "
            "않았습니다. 모델 매뉴얼의 범위를 프로파일에 먼저 등록해 주세요."
        )
    if (
        profile.profile_id != "rs_fsv_fsva"
        or feature.capability_id
        not in _FSV_ABSOLUTE_FREQUENCY_CAPABILITIES
    ):
        return
    maximum = _FSV_FREQUENCY_MAXIMUM_BY_MODEL.get(normalized_model)
    if maximum is None:
        return
    values = dict(arguments)
    if "value" not in values:
        return
    try:
        requested = float(values["value"])
    except ValueError:
        return
    if requested > maximum:
        raise ValueError(
            f"{instrument.model}의 등록된 주파수 상한은 {maximum:.0f} Hz입니다."
        )
