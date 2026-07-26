from __future__ import annotations

import json
from pathlib import Path
from typing import Any


SOURCE_LICENSES = {
    "TECTOS-JP/lab-visa-mcp": {
        "license_url": (
            "https://github.com/TECTOS-JP/lab-visa-mcp/blob/main/LICENSE"
        ),
        "copyright": "Copyright (c) 2026 visa-mcp contributors",
    },
    "TECTOS-JP/lab-executor-mcp": {
        "license_url": (
            "https://github.com/TECTOS-JP/lab-executor-mcp/blob/main/LICENSE"
        ),
        "copyright": "Copyright (c) 2026 TECTOS",
    },
    "armchairdeity/mcp-server-scpi": {
        "license_url": (
            "https://github.com/armchairdeity/mcp-server-scpi/blob/main/LICENSE"
        ),
        "copyright": "Copyright (c) 2026 armchairdeity",
    },
    "armchairdeity/rigol-ds1000z": {
        "license_url": (
            "https://github.com/armchairdeity/rigol-ds1000z/blob/v0.4.0/LICENSE"
        ),
        "copyright": "Copyright (c) 2022 Alexander Osborne",
    },
    "pymeasure/pymeasure": {
        "license_url": (
            "https://github.com/pymeasure/pymeasure/blob/master/LICENSE.txt"
        ),
        "copyright": "Copyright (c) 2013-2026 PyMeasure Developers",
    },
    "microsoft/Qcodes": {
        "license_url": (
            "https://github.com/microsoft/Qcodes/blob/main/LICENSE"
        ),
        "copyright": (
            "Copyright (c) 2015-2023 by Microsoft Corporation and "
            "Københavns Universitet."
        ),
    },
    "QCoDeS/Qcodes_contrib_drivers": {
        "license_url": (
            "https://github.com/QCoDeS/Qcodes_contrib_drivers/blob/main/LICENSE"
        ),
        "copyright": "Copyright (c) 2019 QCoDeS",
    },
}

FSV_DISTRIBUTION_NOTE = (
    "배포 프로파일은 MIT QCoDeS contrib FSV_3013 드라이버에서 확인한 "
    "기능만 포함한다. 제조사 매뉴얼에서만 확보했던 Trace·Marker 등 70개 "
    "바인딩은 제거했으며, 실장비에서 독립 검증한 로컬 확장으로만 다시 "
    "추가할 수 있다."
)
MANUAL_BIBLIOGRAPHY_NOTE = (
    "공식 문서의 제목·버전·적용 모델·링크만 기록한 서지 항목."
)
MANUAL_REDISTRIBUTION_POLICY = (
    "문서 원문, 명령, 표, 페이지 매핑은 저장소에 포함하지 않는다. "
    "공식 링크 사용은 재배포 허가를 뜻하지 않는다."
)


def _read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def _write_json(path: Path, payload: Any) -> None:
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def sanitize_profiles(catalog_dir: Path) -> dict[str, int]:
    removed_by_profile: dict[str, int] = {}
    for path in sorted((catalog_dir / "profiles").glob("*.json")):
        profile = _read_json(path)
        original = list(profile.get("capabilities", ()))
        retained = [
            capability
            for capability in original
            if capability.get("source_ids")
            and not capability.get("manual_references")
            and not str(
                capability.get("verification", "")
            ).startswith("manual_")
        ]
        profile.pop("manual_ids", None)
        profile["capabilities"] = retained

        verification = profile.setdefault("verification", {})
        verification.pop("manual_command_page_confirmed", None)
        verification["distribution_basis"] = "permissive_open_source"
        verification["manufacturer_manual_content_bundled"] = False
        if profile.get("profile_id") == "rs_fsv_fsva":
            verification["status"] = "source_code_confirmed"
            verification["notes_ko"] = FSV_DISTRIBUTION_NOTE

        _write_json(path, profile)
        removed_by_profile[str(profile["profile_id"])] = (
            len(original) - len(retained)
        )
    return removed_by_profile


def sanitize_sources(catalog_dir: Path) -> int:
    path = catalog_dir / "source_catalog.json"
    sources = _read_json(path)
    for source in sources:
        project = str(source.get("project", ""))
        metadata = SOURCE_LICENSES.get(project)
        if metadata is None:
            raise ValueError(f"No approved license metadata for {project!r}")
        if source.get("license") != "MIT":
            raise ValueError(f"Only verified MIT catalog sources are allowed: {project}")
        source.update(metadata)
        source["license_verified"] = True
        source["notice_file"] = "../THIRD_PARTY_NOTICES.md"
        source["redistribution_condition"] = (
            "Retain the copyright and MIT permission notice in copies or "
            "substantial portions."
        )
    _write_json(path, sources)
    return len(sources)


def sanitize_manual_bibliography(catalog_dir: Path) -> int:
    path = catalog_dir / "manual_catalog.json"
    manuals = _read_json(path)
    for manual in manuals:
        manual["notes_ko"] = MANUAL_BIBLIOGRAPHY_NOTE
        manual["redistribution_policy"] = MANUAL_REDISTRIBUTION_POLICY
    _write_json(path, manuals)
    return len(manuals)


def enforce_policy(catalog_dir: Path) -> dict[str, object]:
    bundled_extracts = sorted(
        (catalog_dir / "manual_commands").glob("*.json")
    )
    if bundled_extracts:
        raise ValueError(
            "Delete manufacturer-manual command extracts before sanitizing: "
            + ", ".join(path.name for path in bundled_extracts)
        )
    removed = sanitize_profiles(catalog_dir)
    sources = sanitize_sources(catalog_dir)
    manuals = sanitize_manual_bibliography(catalog_dir)
    return {
        "removed_capabilities": removed,
        "source_records": sources,
        "bibliography_records": manuals,
    }


def main() -> int:
    project_root = Path(__file__).resolve().parents[1]
    catalog_dir = project_root / "scpi_catalog_2026-07-25"
    result = enforce_policy(catalog_dir)
    removed = {
        profile_id: count
        for profile_id, count in result["removed_capabilities"].items()
        if count
    }
    print(
        "catalog distribution policy applied; "
        f"removed={removed or '{}'}, "
        f"sources={result['source_records']}, "
        f"bibliography={result['bibliography_records']}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
