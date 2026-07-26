"""Read-only access to user-local manual command candidates.

Manufacturer manuals and command indexes are not bundled with the application.
Users may create a private catalog from a manual they are authorized to use and
store it under the local application-data directory.  An entry is only a
*candidate* for later instrument validation; loading it must never cause SCPI
traffic.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable


MANUAL_CATALOG_SCHEMA_VERSION = 1
PROBE_POLICIES = frozenset(
    {
        "query_explicit",
        "query_probe",
        "query_limited",
        "manual_only",
    }
)


class ManualCatalogError(ValueError):
    """Raised when a manual-command catalog is structurally invalid."""


@dataclass(frozen=True, slots=True)
class ManualSource:
    """Source manual metadata shared by all candidates in one catalog."""

    manual_id: str
    title: str
    document_reference: str
    version: str
    firmware: str
    source_url: str
    index_pdf_pages: tuple[int, ...]


@dataclass(frozen=True, slots=True)
class ManualCommandCandidate:
    """One manual-index command awaiting live-instrument validation."""

    profile_id: str
    command_id: str
    command_pattern: str
    command_group: str
    manual_page: int
    query_scpi_candidate: str
    query_support: str
    write_support: str
    probe_policy: str
    verification: str
    source: ManualSource

    @property
    def query_probe(self) -> str:
        """Short alias used by validation code and UI layers."""

        return self.query_scpi_candidate

    @property
    def source_url(self) -> str:
        return self.source.source_url

    @property
    def manual_id(self) -> str:
        return self.source.manual_id


@dataclass(frozen=True, slots=True)
class ManualCommandCatalog:
    """Candidates extracted from one manual and mapped to one profile."""

    schema_version: int
    profile_id: str
    source: ManualSource
    extraction_method: str
    extraction_notes: str
    commands: tuple[ManualCommandCandidate, ...]

    @property
    def command_count(self) -> int:
        return len(self.commands)

    @property
    def groups(self) -> tuple[str, ...]:
        return tuple(sorted({item.command_group for item in self.commands}))

    @property
    def counts_by_group(self) -> dict[str, int]:
        counts: dict[str, int] = {}
        for item in self.commands:
            counts[item.command_group] = counts.get(item.command_group, 0) + 1
        return dict(sorted(counts.items()))

    @property
    def counts_by_probe_policy(self) -> dict[str, int]:
        counts: dict[str, int] = {}
        for item in self.commands:
            counts[item.probe_policy] = counts.get(item.probe_policy, 0) + 1
        return dict(sorted(counts.items()))

    def for_group(self, group: str) -> tuple[ManualCommandCandidate, ...]:
        """Return commands in ``group`` using a case-insensitive match."""

        wanted = group.strip().casefold()
        return tuple(
            item
            for item in self.commands
            if item.command_group.casefold() == wanted
        )

    def search(
        self,
        text: str = "",
        *,
        group: str | None = None,
        probe_policy: str | None = None,
    ) -> tuple[ManualCommandCandidate, ...]:
        """Search IDs, manual patterns, groups, and generated query probes."""

        needle = text.strip().casefold()
        wanted_group = group.strip().casefold() if group is not None else None
        wanted_policy = (
            probe_policy.strip().casefold()
            if probe_policy is not None
            else None
        )
        return tuple(
            item
            for item in self.commands
            if (
                wanted_group is None
                or item.command_group.casefold() == wanted_group
            )
            and (
                wanted_policy is None
                or item.probe_policy.casefold() == wanted_policy
            )
            and (
                not needle
                or needle
                in " ".join(
                    (
                        item.command_id,
                        item.command_pattern,
                        item.command_group,
                        item.query_scpi_candidate,
                    )
                ).casefold()
            )
        )

    def count(
        self,
        text: str = "",
        *,
        group: str | None = None,
        probe_policy: str | None = None,
    ) -> int:
        return len(
            self.search(
                text,
                group=group,
                probe_policy=probe_policy,
            )
        )


@dataclass(frozen=True, slots=True)
class ManualCommandCatalogIndex:
    """Collection of base and option-manual catalogs."""

    catalogs: tuple[ManualCommandCatalog, ...]
    load_errors: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        catalog_keys = [
            (item.profile_id, item.source.manual_id)
            for item in self.catalogs
        ]
        duplicates = sorted(
            key
            for key in set(catalog_keys)
            if catalog_keys.count(key) > 1
        )
        if duplicates:
            raise ManualCatalogError(
                "Duplicate manual-command profile/manual IDs: "
                + ", ".join(
                    f"{profile_id}/{manual_id}"
                    for profile_id, manual_id in duplicates
                )
            )

    @property
    def profile_ids(self) -> tuple[str, ...]:
        return tuple(dict.fromkeys(item.profile_id for item in self.catalogs))

    @property
    def command_count(self) -> int:
        return sum(item.command_count for item in self.catalogs)

    def for_profile(self, profile_id: str) -> ManualCommandCatalog | None:
        """Return the first catalog; use ``catalogs_for_profile`` for all."""

        catalogs = self.catalogs_for_profile(profile_id)
        return catalogs[0] if catalogs else None

    def catalogs_for_profile(
        self,
        profile_id: str,
    ) -> tuple[ManualCommandCatalog, ...]:
        wanted = profile_id.strip()
        return tuple(
            catalog
            for catalog in self.catalogs
            if catalog.profile_id == wanted
        )

    def require_profile(self, profile_id: str) -> ManualCommandCatalog:
        catalog = self.for_profile(profile_id)
        if catalog is None:
            raise KeyError(
                f"No manual-command catalog for profile {profile_id!r}"
            )
        return catalog

    def search(
        self,
        text: str = "",
        *,
        profile_id: str | None = None,
        group: str | None = None,
        probe_policy: str | None = None,
    ) -> tuple[ManualCommandCandidate, ...]:
        catalogs: Iterable[ManualCommandCatalog]
        if profile_id is None:
            catalogs = self.catalogs
        else:
            catalogs = self.catalogs_for_profile(profile_id)
        return tuple(
            command
            for catalog in catalogs
            for command in catalog.search(
                text,
                group=group,
                probe_policy=probe_policy,
            )
        )


def _manual_directory_candidates() -> tuple[Path, ...]:
    candidates: list[Path] = []
    configured = os.environ.get("SCPI_AUTOMATION_MANUAL_CATALOG", "").strip()
    if configured:
        candidates.append(Path(configured).expanduser())

    local_app_data = os.environ.get("LOCALAPPDATA", "").strip()
    if local_app_data:
        candidates.append(
            Path(local_app_data)
            / "SCPI-Automation-Platform"
            / "manual_commands"
        )
    else:
        candidates.append(
            Path.home()
            / ".scpi-automation-platform"
            / "manual_commands"
        )
    return tuple(dict.fromkeys(candidates))


def manual_command_catalog_directory() -> Path:
    """Return the user-local manual-command directory.

    The path may not exist yet.  This is intentional: an empty local catalog is
    a supported state and the application never falls back to redistributed
    manufacturer-manual extracts.
    """

    for candidate in _manual_directory_candidates():
        if candidate.is_dir():
            return candidate
    return _manual_directory_candidates()[0]


def _error(path: Path, location: str, message: str) -> ManualCatalogError:
    return ManualCatalogError(f"{path}: {location}: {message}")


def _object(
    value: object,
    *,
    path: Path,
    location: str,
) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise _error(path, location, "expected an object")
    return value


def _string(
    item: dict[str, Any],
    key: str,
    *,
    path: Path,
    location: str,
    allow_empty: bool = False,
) -> str:
    value = item.get(key)
    if not isinstance(value, str):
        raise _error(path, f"{location}.{key}", "expected a string")
    if not allow_empty and not value.strip():
        raise _error(path, f"{location}.{key}", "must not be empty")
    return value


def _positive_int(
    value: object,
    *,
    path: Path,
    location: str,
) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        raise _error(path, location, "expected a positive integer")
    return value


def _source(value: object, *, path: Path) -> ManualSource:
    item = _object(value, path=path, location="manual")
    raw_pages = item.get("index_pdf_pages")
    if not isinstance(raw_pages, list):
        raise _error(
            path,
            "manual.index_pdf_pages",
            "expected an array of positive integers",
        )
    pages = tuple(
        _positive_int(
            page,
            path=path,
            location=f"manual.index_pdf_pages[{index}]",
        )
        for index, page in enumerate(raw_pages)
    )
    return ManualSource(
        manual_id=_string(item, "manual_id", path=path, location="manual"),
        title=_string(item, "title", path=path, location="manual"),
        document_reference=_string(
            item,
            "document_reference",
            path=path,
            location="manual",
            allow_empty=True,
        ),
        version=_string(
            item,
            "version",
            path=path,
            location="manual",
            allow_empty=True,
        ),
        firmware=_string(
            item,
            "firmware",
            path=path,
            location="manual",
            allow_empty=True,
        ),
        source_url=_string(
            item,
            "source_url",
            path=path,
            location="manual",
        ),
        index_pdf_pages=pages,
    )


def _command(
    value: object,
    *,
    profile_id: str,
    source: ManualSource,
    path: Path,
    index: int,
) -> ManualCommandCandidate:
    location = f"commands[{index}]"
    item = _object(value, path=path, location=location)
    probe_policy = _string(
        item,
        "probe_policy",
        path=path,
        location=location,
    )
    if probe_policy not in PROBE_POLICIES:
        raise _error(
            path,
            f"{location}.probe_policy",
            f"unknown policy {probe_policy!r}",
        )
    return ManualCommandCandidate(
        profile_id=profile_id,
        command_id=_string(
            item,
            "command_id",
            path=path,
            location=location,
        ),
        command_pattern=_string(
            item,
            "command_pattern",
            path=path,
            location=location,
        ),
        command_group=_string(
            item,
            "command_group",
            path=path,
            location=location,
        ),
        manual_page=_positive_int(
            item.get("manual_page"),
            path=path,
            location=f"{location}.manual_page",
        ),
        query_scpi_candidate=_string(
            item,
            "query_scpi_candidate",
            path=path,
            location=location,
        ),
        query_support=_string(
            item,
            "query_support",
            path=path,
            location=location,
        ),
        write_support=_string(
            item,
            "write_support",
            path=path,
            location=location,
        ),
        probe_policy=probe_policy,
        verification=_string(
            item,
            "verification",
            path=path,
            location=location,
        ),
        source=source,
    )


def load_manual_command_catalog(
    path: str | Path,
    *,
    expected_profile_id: str | None = None,
) -> ManualCommandCatalog:
    """Load and validate one manual-command candidate JSON file."""

    source_path = Path(path)
    try:
        payload = json.loads(source_path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        raise
    except (OSError, UnicodeError) as exc:
        raise ManualCatalogError(
            f"{source_path}: could not read catalog: {exc}"
        ) from exc
    except json.JSONDecodeError as exc:
        raise ManualCatalogError(
            f"{source_path}: invalid JSON at line {exc.lineno}, "
            f"column {exc.colno}: {exc.msg}"
        ) from exc

    root = _object(payload, path=source_path, location="$")
    schema_version = root.get("schema_version")
    if (
        isinstance(schema_version, bool)
        or schema_version != MANUAL_CATALOG_SCHEMA_VERSION
    ):
        raise _error(
            source_path,
            "schema_version",
            "expected "
            f"{MANUAL_CATALOG_SCHEMA_VERSION}, got {schema_version!r}",
        )
    profile_id = _string(
        root,
        "profile_id",
        path=source_path,
        location="$",
    )
    if expected_profile_id is not None and profile_id != expected_profile_id:
        raise _error(
            source_path,
            "profile_id",
            f"expected {expected_profile_id!r}, got {profile_id!r}",
        )

    source = _source(root.get("manual"), path=source_path)
    extraction = _object(
        root.get("extraction"),
        path=source_path,
        location="extraction",
    )
    extraction_method = _string(
        extraction,
        "method",
        path=source_path,
        location="extraction",
    )
    extraction_notes = _string(
        extraction,
        "notes",
        path=source_path,
        location="extraction",
        allow_empty=True,
    )
    declared_count = extraction.get("command_count")
    if isinstance(declared_count, bool) or not isinstance(
        declared_count, int
    ):
        raise _error(
            source_path,
            "extraction.command_count",
            "expected a non-negative integer",
        )

    raw_commands = root.get("commands")
    if not isinstance(raw_commands, list):
        raise _error(source_path, "commands", "expected an array")
    commands = tuple(
        _command(
            value,
            profile_id=profile_id,
            source=source,
            path=source_path,
            index=index,
        )
        for index, value in enumerate(raw_commands)
    )
    if declared_count != len(commands):
        raise _error(
            source_path,
            "extraction.command_count",
            f"declares {declared_count}, but commands has {len(commands)}",
        )

    command_ids = [item.command_id for item in commands]
    duplicates = sorted(
        command_id
        for command_id in set(command_ids)
        if command_ids.count(command_id) > 1
    )
    if duplicates:
        raise _error(
            source_path,
            "commands",
            "duplicate command IDs: " + ", ".join(duplicates),
        )

    return ManualCommandCatalog(
        schema_version=schema_version,
        profile_id=profile_id,
        source=source,
        extraction_method=extraction_method,
        extraction_notes=extraction_notes,
        commands=commands,
    )


def load_manual_command_catalogs(
    directory: str | Path | None = None,
    *,
    strict: bool = True,
) -> ManualCommandCatalogIndex:
    """Load every ``*.json`` catalog and index it by root ``profile_id``."""

    source_directory = (
        manual_command_catalog_directory()
        if directory is None
        else Path(directory)
    )
    if not source_directory.is_dir() and directory is None:
        return ManualCommandCatalogIndex(())
    if not source_directory.is_dir():
        raise FileNotFoundError(
            f"Manual-command catalog directory does not exist: "
            f"{source_directory}"
        )
    paths = sorted(source_directory.glob("*.json"))
    catalogs: list[ManualCommandCatalog] = []
    errors: list[str] = []
    for path in paths:
        try:
            catalogs.append(load_manual_command_catalog(path))
        except (OSError, ValueError) as exc:
            if strict:
                raise
            errors.append(str(exc))
    return ManualCommandCatalogIndex(
        tuple(catalogs),
        tuple(errors),
    )
