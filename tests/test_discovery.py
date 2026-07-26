from __future__ import annotations

import threading
import unittest
from unittest.mock import patch

from scpi_automation.transport.discovery import (
    DiscoveryState,
    discover_resources,
    open_resource_session,
    resource_interface,
    should_auto_identify,
)


class FakeSession:
    def __init__(self, response: str) -> None:
        self.response = response
        self.timeout = 0
        self.commands: list[str] = []
        self.closed = False

    def query(self, command: str) -> str:
        self.commands.append(command)
        return self.response

    def close(self) -> None:
        self.closed = True


class FakeResourceManager:
    def __init__(self) -> None:
        self.resources = (
            "TCPIP0::192.0.2.10::inst0::INSTR",
            "ASRL3::INSTR",
        )
        self.session = FakeSession("Rohde&Schwarz,FSV30,123,3.50")
        self.opened: list[str] = []
        self.closed = False

    def list_resources(self) -> tuple[str, ...]:
        return self.resources

    def open_resource(self, resource: str) -> FakeSession:
        self.opened.append(resource)
        return self.session

    def close(self) -> None:
        self.closed = True


class DiscoveryPolicyTests(unittest.TestCase):
    def test_interface_is_parsed_from_resource(self) -> None:
        self.assertEqual(resource_interface("TCPIP0::host::inst0::INSTR"), "TCPIP0")

    def test_serial_is_not_opened_automatically(self) -> None:
        self.assertFalse(should_auto_identify("ASRL3::INSTR"))

    def test_standard_tcpip_instr_is_auto_identified(self) -> None:
        self.assertTrue(should_auto_identify("TCPIP0::host::inst0::INSTR"))

    def test_discovery_only_queries_idn_and_closes_resources(self) -> None:
        manager = FakeResourceManager()
        seen = []

        with patch(
            "scpi_automation.transport.discovery._make_resource_manager",
            return_value=manager,
        ):
            records = discover_resources(
                timeout_ms=800,
                stop_event=threading.Event(),
                on_record=seen.append,
            )

        self.assertEqual(len(records), 2)
        self.assertEqual(records[0].state, DiscoveryState.IDENTIFIED)
        self.assertEqual(records[1].state, DiscoveryState.SKIPPED)
        self.assertEqual(manager.opened, [manager.resources[0]])
        self.assertEqual(manager.session.commands, ["*IDN?"])
        self.assertEqual(manager.session.timeout, 800)
        self.assertTrue(manager.session.closed)
        self.assertTrue(manager.closed)
        self.assertEqual(seen, records)

    def test_explicit_control_session_sets_timeout_and_closes_everything(
        self,
    ) -> None:
        manager = FakeResourceManager()
        resource = manager.resources[0]

        with patch(
            "scpi_automation.transport.discovery._make_resource_manager",
            return_value=manager,
        ):
            with open_resource_session(
                resource,
                timeout_ms=2345,
            ) as session:
                self.assertIs(session, manager.session)
                self.assertFalse(manager.session.closed)
                self.assertEqual(session.timeout, 2345)

        self.assertTrue(manager.session.closed)
        self.assertTrue(manager.closed)

    def test_explicit_control_session_closes_after_caller_error(self) -> None:
        manager = FakeResourceManager()

        with patch(
            "scpi_automation.transport.discovery._make_resource_manager",
            return_value=manager,
        ):
            with self.assertRaisesRegex(RuntimeError, "operator abort"):
                with open_resource_session(manager.resources[0]):
                    raise RuntimeError("operator abort")

        self.assertTrue(manager.session.closed)
        self.assertTrue(manager.closed)


if __name__ == "__main__":
    unittest.main()
