from __future__ import annotations

import re
from contextlib import contextmanager
from dataclasses import dataclass
from enum import Enum
from threading import Event
from typing import Callable, Iterator, Protocol

from scpi_automation.identity import (
    ClassificationResult,
    InstrumentIdentity,
    classify_identity,
    parse_idn_response,
)


class VisaDiscoveryError(RuntimeError):
    """Raised when the VISA environment cannot be used."""


class DiscoveryState(str, Enum):
    IDENTIFIED = "identified"
    SKIPPED = "skipped"
    ERROR = "error"

    @property
    def label_ko(self) -> str:
        return {
            DiscoveryState.IDENTIFIED: "식별 완료",
            DiscoveryState.SKIPPED: "수동 확인",
            DiscoveryState.ERROR: "식별 실패",
        }[self]


@dataclass(frozen=True, slots=True)
class DiscoveryRecord:
    resource: str
    interface: str
    state: DiscoveryState
    identity: InstrumentIdentity | None = None
    classification: ClassificationResult | None = None
    message: str = ""


class _VisaSession(Protocol):
    timeout: int

    def query(self, command: str) -> str: ...

    def write(self, command: str) -> object: ...

    def close(self) -> None: ...


class _ResourceManager(Protocol):
    def list_resources(self) -> tuple[str, ...]: ...

    def open_resource(self, resource: str) -> _VisaSession: ...

    def close(self) -> None: ...


def resource_interface(resource: str) -> str:
    return resource.split("::", 1)[0].strip().upper() or "UNKNOWN"


def should_auto_identify(resource: str) -> bool:
    upper = resource.strip().upper()
    interface = resource_interface(upper)
    interface_family = re.sub(r"\d+$", "", interface)
    if interface_family == "ASRL":
        return False
    return upper.endswith("::INSTR") and interface_family in {
        "GPIB",
        "USB",
        "TCPIP",
        "PXI",
        "VXI",
    }


def _make_resource_manager(backend: str) -> _ResourceManager:
    try:
        import pyvisa
    except ImportError as exc:
        raise VisaDiscoveryError(
            "PyVISA가 설치되어 있지 않습니다. 지금은 아래의 IDN 직접 분류를 사용할 수 있습니다."
        ) from exc

    try:
        if backend:
            return pyvisa.ResourceManager(backend)
        return pyvisa.ResourceManager()
    except Exception as exc:
        selected = backend or "시스템 기본"
        raise VisaDiscoveryError(
            f"VISA backend를 열 수 없습니다: {selected} ({exc})"
        ) from exc


def _identify_with_manager(
    manager: _ResourceManager,
    resource: str,
    timeout_ms: int,
) -> DiscoveryRecord:
    interface = resource_interface(resource)
    session: _VisaSession | None = None
    try:
        session = manager.open_resource(resource)
        session.timeout = timeout_ms
        raw_idn = session.query("*IDN?")
        identity = parse_idn_response(raw_idn)
        classification = classify_identity(identity)
        return DiscoveryRecord(
            resource=resource,
            interface=interface,
            state=DiscoveryState.IDENTIFIED,
            identity=identity,
            classification=classification,
            message="read-only *IDN? 응답으로 분류했습니다.",
        )
    except Exception as exc:
        return DiscoveryRecord(
            resource=resource,
            interface=interface,
            state=DiscoveryState.ERROR,
            message=str(exc),
        )
    finally:
        if session is not None:
            try:
                session.close()
            except Exception:
                pass


def discover_resources(
    backend: str = "",
    timeout_ms: int = 1500,
    stop_event: Event | None = None,
    on_record: Callable[[DiscoveryRecord], None] | None = None,
) -> list[DiscoveryRecord]:
    """List VISA INSTR resources and identify conservative interface types.

    Serial resources are listed but not opened automatically because opening a
    serial port can toggle control lines or require device-specific framing.
    """

    manager = _make_resource_manager(backend)
    records: list[DiscoveryRecord] = []
    try:
        try:
            resources = tuple(manager.list_resources())
        except Exception as exc:
            raise VisaDiscoveryError(f"VISA resource 검색에 실패했습니다: {exc}") from exc

        for resource in resources:
            if stop_event is not None and stop_event.is_set():
                break

            if should_auto_identify(resource):
                record = _identify_with_manager(manager, resource, timeout_ms)
            else:
                record = DiscoveryRecord(
                    resource=resource,
                    interface=resource_interface(resource),
                    state=DiscoveryState.SKIPPED,
                    message="이 interface는 자동으로 열지 않았습니다. 주소 직접 식별을 사용하세요.",
                )

            records.append(record)
            if on_record is not None:
                on_record(record)
        return records
    finally:
        try:
            manager.close()
        except Exception:
            pass


def identify_resource(
    resource: str,
    backend: str = "",
    timeout_ms: int = 1500,
) -> DiscoveryRecord:
    resource = resource.strip()
    if not resource:
        raise VisaDiscoveryError("VISA resource 주소를 입력하세요.")

    manager = _make_resource_manager(backend)
    try:
        return _identify_with_manager(manager, resource, timeout_ms)
    finally:
        try:
            manager.close()
        except Exception:
            pass


@contextmanager
def open_resource_session(
    resource: str,
    backend: str = "",
    timeout_ms: int = 2000,
) -> Iterator[_VisaSession]:
    """Open one VISA resource for an explicitly requested control operation.

    Discovery deliberately opens a short-lived session only for ``*IDN?``.
    Hardware validation needs a longer-lived session, but callers should still
    receive deterministic cleanup of both the instrument session and resource
    manager.  This context manager is the single public entry point used by the
    validation UI.
    """

    resource = resource.strip()
    if not resource:
        raise VisaDiscoveryError("VISA resource 주소를 입력하세요.")
    if not 1 <= timeout_ms <= 600_000:
        raise VisaDiscoveryError("Timeout은 1~600000 ms 범위여야 합니다.")

    manager = _make_resource_manager(backend)
    session: _VisaSession | None = None
    try:
        try:
            session = manager.open_resource(resource)
            session.timeout = timeout_ms
        except Exception as exc:
            raise VisaDiscoveryError(
                f"VISA 장비 세션을 열지 못했습니다: {exc}"
            ) from exc
        try:
            yield session
        finally:
            try:
                session.close()
            except Exception:
                pass
    finally:
        try:
            manager.close()
        except Exception:
            pass
