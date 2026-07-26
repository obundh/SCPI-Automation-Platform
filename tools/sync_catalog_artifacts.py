from __future__ import annotations

import argparse
import csv
import hashlib
import json
import re
import sqlite3
from collections import Counter
from datetime import datetime
from pathlib import Path
from typing import Any, Iterable


CSV_FIELDS = (
    "profile_id",
    "manufacturer",
    "model_family",
    "instrument_class",
    "capability_id",
    "label_ko",
    "category",
    "operation",
    "scpi",
    "response_type",
    "unit",
    "risk_level",
    "scope",
    "verification",
    "parameters_json",
    "source_ids",
    "note_ko",
)
PLACEHOLDER = re.compile(r"\{([A-Za-z_][A-Za-z0-9_]*)\}")
TEXT_ARTIFACT_SUFFIXES = frozenset({".csv", ".json", ".md", ".txt"})


def _write_text_lf(path: Path, text: str) -> None:
    with path.open("w", encoding="utf-8", newline="\n") as stream:
        stream.write(text)


def _normalize_catalog_text_files(catalog_dir: Path) -> None:
    """Keep checksummed text bytes identical on Windows and CI checkouts."""

    for path in sorted(catalog_dir.rglob("*")):
        if (
            path.is_file()
            and path.suffix.lower() in TEXT_ARTIFACT_SUFFIXES
            and path.name != "SHA256SUMS.txt"
            and "__pycache__" not in path.parts
        ):
            _write_text_lf(path, path.read_text(encoding="utf-8"))


def _read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def _compact(value: object) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )


def _operation_rows(profiles: Iterable[dict[str, Any]]) -> Iterable[dict[str, str]]:
    for profile in profiles:
        for capability in profile.get("capabilities", ()):
            source_ids = ",".join(capability.get("source_ids", ()))
            for name, operation in capability.get("operations", {}).items():
                yield {
                    "profile_id": profile["profile_id"],
                    "manufacturer": profile["manufacturer"],
                    "model_family": profile["model_family"],
                    "instrument_class": profile["instrument_class"],
                    "capability_id": capability["capability_id"],
                    "label_ko": capability["label_ko"],
                    "category": capability["category"],
                    "operation": name,
                    "scpi": str(operation.get("scpi", "")),
                    "response_type": str(operation.get("response_type", "")),
                    "unit": str(capability.get("unit", "")),
                    "risk_level": str(capability.get("risk_level", "")),
                    "scope": str(capability.get("scope", "")),
                    "verification": str(capability.get("verification", "")),
                    "parameters_json": _compact(
                        capability.get("parameters", ())
                    ),
                    "source_ids": source_ids,
                    "note_ko": str(capability.get("note_ko", "")),
                }


def _placeholder_errors(profiles: Iterable[dict[str, Any]]) -> list[str]:
    errors: list[str] = []
    for profile in profiles:
        for capability in profile.get("capabilities", ()):
            parameter_names = {
                str(parameter.get("name", ""))
                for parameter in capability.get("parameters", ())
            }
            for name, operation in capability.get("operations", {}).items():
                scpi = str(operation.get("scpi", ""))
                missing = set(PLACEHOLDER.findall(scpi)) - parameter_names
                if missing:
                    errors.append(
                        f"{profile['profile_id']} / "
                        f"{capability['capability_id']}::{name}: "
                        f"missing parameter definitions {sorted(missing)}"
                    )
    return errors


def _distribution_policy_errors(
    catalog_dir: Path,
    profiles: Iterable[dict[str, Any]],
    sources: Iterable[dict[str, Any]],
) -> list[str]:
    """Reject catalog content that is not safe for repository distribution."""

    errors: list[str] = []
    bundled_extracts = sorted(
        (catalog_dir / "manual_commands").glob("*.json")
    )
    if bundled_extracts:
        errors.append(
            "manufacturer-manual command extracts must not be distributed: "
            + ", ".join(path.name for path in bundled_extracts)
        )

    sources_by_id = {
        str(source.get("source_id", "")): source
        for source in sources
        if source.get("source_id")
    }
    for profile in profiles:
        profile_id = str(profile.get("profile_id", ""))
        if profile.get("manual_ids"):
            errors.append(
                f"{profile_id}: distributed profiles must not depend on "
                "manufacturer manual IDs"
            )
        for capability in profile.get("capabilities", ()):
            capability_id = str(capability.get("capability_id", ""))
            verification = str(capability.get("verification", ""))
            if capability.get("manual_references") or verification.startswith(
                "manual_"
            ):
                errors.append(
                    f"{profile_id}/{capability_id}: manual-derived binding "
                    "is not distributable"
                )
            source_ids = tuple(capability.get("source_ids", ()))
            if not source_ids:
                errors.append(
                    f"{profile_id}/{capability_id}: model-specific SCPI "
                    "binding has no redistributable source"
                )
                continue
            for source_id in source_ids:
                source = sources_by_id.get(str(source_id))
                if source is None:
                    errors.append(
                        f"{profile_id}/{capability_id}: unknown source "
                        f"{source_id!r}"
                    )
                    continue
                if (
                    source.get("license") != "MIT"
                    or source.get("license_verified") is not True
                    or not source.get("license_url")
                    or not source.get("copyright")
                ):
                    errors.append(
                        f"{profile_id}/{capability_id}: source {source_id!r} "
                        "does not have a verified MIT license record"
                    )
    return errors


def _sync_unified_catalog(catalog_dir: Path) -> tuple[dict[str, Any], str]:
    catalog_path = catalog_dir / "scpi_catalog.json"
    catalog = _read_json(catalog_path)
    original_profiles = catalog.get("profiles", ())
    ordered_ids = [
        str(profile.get("profile_id", ""))
        for profile in original_profiles
    ]
    profiles_by_id = {
        str(profile["profile_id"]): profile
        for path in sorted((catalog_dir / "profiles").glob("*.json"))
        if isinstance((profile := _read_json(path)), dict)
        and profile.get("profile_id")
    }
    ordered_profiles = [
        profiles_by_id.pop(profile_id)
        for profile_id in ordered_ids
        if profile_id in profiles_by_id
    ]
    ordered_profiles.extend(
        profiles_by_id[profile_id]
        for profile_id in sorted(profiles_by_id)
    )
    generated_at = datetime.now().astimezone().isoformat(timespec="seconds")
    catalog["generated_at"] = generated_at
    catalog["sources"] = _read_json(catalog_dir / "source_catalog.json")
    catalog["manuals"] = _read_json(catalog_dir / "manual_catalog.json")
    catalog["profiles"] = ordered_profiles

    taxonomy = catalog.setdefault("taxonomy", {})
    taxonomy["generated_at"] = generated_at
    taxonomy["categories"] = sorted(
        {
            str(capability.get("category", ""))
            for profile in ordered_profiles
            for capability in profile.get("capabilities", ())
            if capability.get("category")
        }
    )
    taxonomy["capability_ids"] = sorted(
        {
            str(capability.get("capability_id", ""))
            for profile in ordered_profiles
            for capability in profile.get("capabilities", ())
            if capability.get("capability_id")
        }
    )
    scope = catalog.setdefault("scope", {})
    scope["manual_command_candidates"] = 0
    scope["manual_command_candidate_profiles"] = 0
    scope["manufacturer_manual_content_bundled"] = False
    scope["description_ko"] = (
        "허용 라이선스가 확인된 오픈소스 드라이버 기반 기능 바인딩만 "
        "배포하는 로컬 SCPI 데이터팩. 제조사 매뉴얼 원문·명령 색인·페이지 "
        "매핑은 포함하지 않는다."
    )
    policy_errors = _distribution_policy_errors(
        catalog_dir,
        ordered_profiles,
        catalog["sources"],
    )
    if policy_errors:
        raise ValueError("\n".join(policy_errors))
    _write_text_lf(
        catalog_path,
        json.dumps(catalog, ensure_ascii=False, indent=2) + "\n",
    )
    return catalog, generated_at


def _write_csv(catalog_dir: Path, profiles: list[dict[str, Any]]) -> None:
    path = catalog_dir / "command_bindings.csv"
    with path.open("w", encoding="utf-8", newline="\n") as stream:
        writer = csv.DictWriter(
            stream,
            fieldnames=CSV_FIELDS,
            lineterminator="\n",
        )
        writer.writeheader()
        writer.writerows(_operation_rows(profiles))


def _replace_sqlite(
    catalog_dir: Path,
    catalog: dict[str, Any],
    generated_at: str,
) -> str:
    path = catalog_dir / "scpi_catalog.sqlite"
    connection = sqlite3.connect(path)
    try:
        connection.execute("PRAGMA foreign_keys=OFF")
        with connection:
            for table in (
                "operations",
                "capabilities",
                "profile_manuals",
                "profile_sources",
                "profiles",
                "manuals",
                "sources",
            ):
                connection.execute(f"DELETE FROM {table}")

            for manual in catalog.get("manuals", ()):
                connection.execute(
                    """
                    INSERT INTO manuals VALUES (
                      ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?
                    )
                    """,
                    (
                        manual["manual_id"],
                        manual["manufacturer"],
                        manual["title"],
                        manual.get("document_reference"),
                        _compact(manual.get("applies_to", ())),
                        manual["official_url"],
                        manual.get("version"),
                        manual.get("firmware"),
                        manual.get("publication_date"),
                        int(bool(manual.get("remote_commands", False))),
                        manual.get("verified_on", ""),
                        manual.get("notes_ko"),
                        manual.get("redistribution_policy"),
                    ),
                )
            for source in catalog.get("sources", ()):
                connection.execute(
                    """
                    INSERT INTO sources VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        source["source_id"],
                        source["kind"],
                        source.get("project"),
                        source.get("path"),
                        source.get("revision"),
                        source["url"],
                        source.get("license"),
                        int(bool(source.get("license_verified", False))),
                        source.get("purpose_ko"),
                    ),
                )

            manual_ids = {
                str(item["manual_id"])
                for item in catalog.get("manuals", ())
            }
            source_ids = {
                str(item["source_id"])
                for item in catalog.get("sources", ())
            }
            for profile in catalog["profiles"]:
                verification = profile.get("verification", {})
                connection.execute(
                    """
                    INSERT INTO profiles VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        profile["profile_id"],
                        profile["manufacturer"],
                        profile["model_family"],
                        _compact(profile.get("models", ())),
                        profile["instrument_class"],
                        _compact(
                            profile.get("identification", {}).get(
                                "idn_patterns",
                                (),
                            )
                        ),
                        _compact(profile.get("interfaces", ())),
                        verification.get("status", ""),
                        verification.get("notes_ko"),
                        _compact(profile.get("model_limits", {})),
                        _compact(profile.get("safe_shutdown", ())),
                    ),
                )
                for manual_id in profile.get("manual_ids", ()):
                    if manual_id in manual_ids:
                        connection.execute(
                            "INSERT INTO profile_manuals VALUES (?, ?)",
                            (profile["profile_id"], manual_id),
                        )
                for source_id in profile.get("source_ids", ()):
                    if source_id in source_ids:
                        connection.execute(
                            "INSERT INTO profile_sources VALUES (?, ?)",
                            (profile["profile_id"], source_id),
                        )

                for capability in profile.get("capabilities", ()):
                    cursor = connection.execute(
                        """
                        INSERT INTO capabilities(
                          profile_id, capability_id, label_ko, category, unit,
                          risk_level, scope, verification, parameters_json,
                          preconditions_json, alternatives_json, note_ko,
                          source_ids_json
                        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                        """,
                        (
                            profile["profile_id"],
                            capability["capability_id"],
                            capability["label_ko"],
                            capability["category"],
                            capability.get("unit"),
                            capability["risk_level"],
                            capability.get("scope"),
                            capability["verification"],
                            _compact(capability.get("parameters", ())),
                            _compact(capability.get("preconditions", ())),
                            _compact(capability.get("alternatives", ())),
                            capability.get("note_ko"),
                            _compact(capability.get("source_ids", ())),
                        ),
                    )
                    capability_pk = int(cursor.lastrowid)
                    for name, operation in capability.get(
                        "operations",
                        {},
                    ).items():
                        connection.execute(
                            """
                            INSERT INTO operations(
                              capability_pk, operation_name, scpi,
                              response_type, binary
                            ) VALUES (?, ?, ?, ?, ?)
                            """,
                            (
                                capability_pk,
                                name,
                                operation.get("scpi", ""),
                                operation.get("response_type"),
                                int(bool(operation.get("binary", False))),
                            ),
                        )

            metadata = {
                "catalog_version": str(catalog.get("catalog_version", "")),
                "generated_at": generated_at,
                "verified_on": str(catalog.get("verified_on", "")),
                "profile_count": str(len(catalog["profiles"])),
                "manual_count": str(len(catalog.get("manuals", ()))),
                "source_count": str(len(catalog.get("sources", ()))),
            }
            for key, value in metadata.items():
                connection.execute(
                    """
                    INSERT INTO metadata(key, value) VALUES (?, ?)
                    ON CONFLICT(key) DO UPDATE SET value=excluded.value
                    """,
                    (key, value),
                )
        connection.execute("PRAGMA foreign_keys=ON")
        return str(connection.execute("PRAGMA quick_check").fetchone()[0])
    finally:
        connection.close()


def _coverage_payload(
    catalog_dir: Path,
    catalog: dict[str, Any],
    generated_at: str,
    integrity: str,
) -> dict[str, Any]:
    profiles = catalog["profiles"]
    capabilities = [
        capability
        for profile in profiles
        for capability in profile.get("capabilities", ())
    ]
    operations = list(_operation_rows(profiles))
    errors = _placeholder_errors(profiles)
    profile_rows = []
    for profile in profiles:
        profile_capabilities = profile.get("capabilities", ())
        profile_rows.append(
            {
                "profile_id": profile["profile_id"],
                "manufacturer": profile["manufacturer"],
                "model_family": profile["model_family"],
                "instrument_class": profile["instrument_class"],
                "capability_count": len(profile_capabilities),
                "operation_count": sum(
                    len(item.get("operations", {}))
                    for item in profile_capabilities
                ),
                "manual_command_candidates": 0,
                "verification": profile.get("verification", {}).get(
                    "status",
                    "",
                ),
                "manual_ids": list(profile.get("manual_ids", ())),
            }
        )
    return {
        "generated_at": generated_at,
        "verified_on": catalog.get("verified_on", ""),
        "database_integrity": integrity,
        "counts": {
            "profiles": len(profiles),
            "manuals": len(catalog.get("manuals", ())),
            "sources": len(catalog.get("sources", ())),
            "capabilities": len(capabilities),
            "operations": len(operations),
            "high_risk_operations": sum(
                row["risk_level"] == "high" for row in operations
            ),
            "manual_command_candidates": 0,
        },
        "instrument_classes": dict(
            sorted(
                Counter(
                    profile["instrument_class"] for profile in profiles
                ).items()
            )
        ),
        "risk_levels": dict(
            sorted(
                Counter(
                    str(item.get("risk_level", ""))
                    for item in capabilities
                ).items()
            )
        ),
        "verification_levels": dict(
            sorted(
                Counter(
                    str(item.get("verification", ""))
                    for item in capabilities
                ).items()
            )
        ),
        "placeholder_validation": {
            "status": "ok" if not errors else "error",
            "errors": errors,
        },
        "profiles": profile_rows,
        "limitations_ko": [
            "제조사 매뉴얼 원문, 명령 색인, 페이지 매핑은 배포하지 않는다.",
            "모델별 SCPI 바인딩은 라이선스가 확인된 오픈소스 출처만 포함한다.",
            "사용자 로컬 매뉴얼 후보는 저장소 밖에서만 관리하며 자동 실행하지 않는다.",
            "위험 명령, 파일·메모리 변경, Reset·Calibration은 일괄 자동 검증하지 않는다.",
            "오픈소스 드라이버 근거는 해당 물리 장비에서의 동작 보증이 아니다.",
        ],
    }


def _write_coverage_markdown(
    path: Path,
    coverage: dict[str, Any],
) -> None:
    counts = coverage["counts"]
    lines = [
        "# Coverage Report",
        "",
        f"- Database integrity: `{coverage['database_integrity']}`",
        f"- Profiles: **{counts['profiles']}**",
        f"- Capabilities: **{counts['capabilities']}**",
        f"- Curated operations: **{counts['operations']}**",
        (
            "- Bundled manufacturer-manual command candidates: "
            f"**{counts['manual_command_candidates']}**"
        ),
        f"- High-risk operations: **{counts['high_risk_operations']}**",
        f"- Manuals: **{counts['manuals']}**",
        f"- Sources: **{counts['sources']}**",
        (
            "- SCPI template placeholder validation: "
            f"**{coverage['placeholder_validation']['status']}**"
        ),
        "",
        "## Profiles",
        "",
        "| Profile | Capabilities | Operations | Manual candidates | Verification |",
        "|---|---:|---:|---:|---|",
    ]
    for profile in coverage["profiles"]:
        lines.append(
            f"| `{profile['profile_id']}` | "
            f"{profile['capability_count']} | "
            f"{profile['operation_count']} | "
            f"{profile['manual_command_candidates']} | "
            f"{profile['verification']} |"
        )
    lines.extend(["", "## Limitations", ""])
    lines.extend(
        f"- {item}" for item in coverage.get("limitations_ko", ())
    )
    _write_text_lf(path, "\n".join(lines) + "\n")


def _write_checksums(catalog_dir: Path) -> None:
    paths = sorted(
        (
            path
            for path in catalog_dir.rglob("*")
            if path.is_file()
            and path.name != "SHA256SUMS.txt"
            and "__pycache__" not in path.parts
        ),
        key=lambda path: path.relative_to(catalog_dir).as_posix(),
    )
    lines = [
        f"{hashlib.sha256(path.read_bytes()).hexdigest()}  "
        f"{path.relative_to(catalog_dir).as_posix()}"
        for path in paths
    ]
    _write_text_lf(
        catalog_dir / "SHA256SUMS.txt",
        "\n".join(lines) + "\n",
    )


def sync_catalog(catalog_dir: Path) -> dict[str, Any]:
    _normalize_catalog_text_files(catalog_dir)
    root_notice = catalog_dir.parent / "THIRD_PARTY_NOTICES.md"
    if not root_notice.is_file():
        raise FileNotFoundError(
            f"Required third-party notice is missing: {root_notice}"
        )
    _write_text_lf(
        catalog_dir / "THIRD_PARTY_NOTICES.md",
        root_notice.read_text(encoding="utf-8"),
    )
    catalog, generated_at = _sync_unified_catalog(catalog_dir)
    profiles = catalog["profiles"]
    errors = _placeholder_errors(profiles)
    if errors:
        raise ValueError("\n".join(errors))
    _write_csv(catalog_dir, profiles)
    integrity = _replace_sqlite(catalog_dir, catalog, generated_at)
    coverage = _coverage_payload(
        catalog_dir,
        catalog,
        generated_at,
        integrity,
    )
    _write_text_lf(
        catalog_dir / "reports" / "coverage_report.json",
        json.dumps(coverage, ensure_ascii=False, indent=2) + "\n",
    )
    _write_coverage_markdown(
        catalog_dir / "reports" / "coverage_report.md",
        coverage,
    )
    _write_checksums(catalog_dir)
    return coverage


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Synchronize catalog JSON, CSV, SQLite, coverage and hashes."
    )
    parser.add_argument(
        "catalog_dir",
        nargs="?",
        type=Path,
        default=Path("scpi_catalog_2026-07-25"),
    )
    args = parser.parse_args()
    coverage = sync_catalog(args.catalog_dir.resolve())
    counts = coverage["counts"]
    print(
        f"synced {counts['profiles']} profiles, "
        f"{counts['capabilities']} capabilities, "
        f"{counts['operations']} curated operations, "
        f"{counts['manual_command_candidates']} manual candidates"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
