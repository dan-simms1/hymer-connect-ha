"""Tests for SCU BLE-address discovery in the config flow."""

from __future__ import annotations

import importlib
import sys
import types
import unittest

from tests.hymer_test_support import ensure_package_paths, install_homeassistant_stubs


def _info(address, name="", service_uuids=None, rssi=None):
    return types.SimpleNamespace(
        address=address,
        name=name,
        service_uuids=list(service_uuids or []),
        rssi=rssi,
    )


class BleDiscoveryTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        install_homeassistant_stubs()
        ensure_package_paths()
        try:
            cls.cf = importlib.import_module(
                "custom_components.hymer_connect_metadata.config_flow"
            )
        except Exception as err:  # noqa: BLE001 - deps (voluptuous/HA) not in this env
            raise unittest.SkipTest(f"config_flow deps unavailable: {err}")

    def setUp(self) -> None:
        self._saved = {
            k: sys.modules.get(k)
            for k in (
                "homeassistant.components.bluetooth",
                "homeassistant.helpers.selector",
            )
        }

    def tearDown(self) -> None:
        for key, value in self._saved.items():
            if value is None:
                sys.modules.pop(key, None)
            else:
                sys.modules[key] = value

    def _fake_bluetooth(self, infos) -> None:
        mod = types.ModuleType("homeassistant.components.bluetooth")
        mod.async_discovered_service_info = lambda hass, connectable=True: infos
        sys.modules["homeassistant.components.bluetooth"] = mod

    def _fake_selector(self) -> types.ModuleType:
        sel = types.ModuleType("homeassistant.helpers.selector")

        class SelectSelector:
            def __init__(self, config):
                self.config = config

        sel.SelectSelector = SelectSelector
        sel.SelectSelectorConfig = lambda **kwargs: kwargs
        sel.SelectSelectorMode = types.SimpleNamespace(DROPDOWN="dropdown")
        sys.modules["homeassistant.helpers.selector"] = sel
        return sel

    def test_matches_scu_by_name_and_uuid_and_dedups(self) -> None:
        self._fake_bluetooth(
            [
                _info("AA:BB:CC:DD:EE:01", name="HYMER 00012345", rssi=-55),
                _info(
                    "AA:BB:CC:DD:EE:02",
                    name="Unnamed",
                    service_uuids=["fff40001-13c9-42f3-9d46-e1d1aa2a7232"],
                ),
                _info(
                    "AA:BB:CC:DD:EE:03",
                    name="Someone's Watch",
                    service_uuids=["0000180f-0000-1000-8000-00805f9b34fb"],
                ),
                _info("AA:BB:CC:DD:EE:01", name="HYMER duplicate"),
            ]
        )
        options = self.cf._discovered_scu_options(object())
        values = [o["value"] for o in options]
        self.assertEqual(values, ["AA:BB:CC:DD:EE:01", "AA:BB:CC:DD:EE:02"])  # matched + deduped
        self.assertIn("-55 dBm", options[0]["label"])

    def test_no_bluetooth_module_returns_empty(self) -> None:
        sys.modules.pop("homeassistant.components.bluetooth", None)
        self.assertEqual(self.cf._discovered_scu_options(object()), [])

    def test_field_is_plain_text_when_no_devices(self) -> None:
        self._fake_bluetooth([])
        self.assertIs(self.cf._ble_address_field(object(), ""), str)

    def test_field_is_dropdown_when_devices_found(self) -> None:
        self._fake_bluetooth([_info("AA:BB:CC:DD:EE:01", name="HYMER 001")])
        sel = self._fake_selector()
        field = self.cf._ble_address_field(object(), "")
        self.assertIsInstance(field, sel.SelectSelector)
        self.assertTrue(field.config["custom_value"])  # manual MAC entry still allowed
        self.assertTrue(
            any(o["value"] == "AA:BB:CC:DD:EE:01" for o in field.config["options"])
        )

    def test_current_default_is_added_as_an_option(self) -> None:
        self._fake_bluetooth([_info("AA:BB:CC:DD:EE:01", name="HYMER 001")])
        sel = self._fake_selector()
        field = self.cf._ble_address_field(object(), "AA:BB:CC:DD:EE:99")
        values = [o["value"] for o in field.config["options"]]
        self.assertIn("AA:BB:CC:DD:EE:99", values)  # keeps a previously-saved address selectable


if __name__ == "__main__":
    unittest.main()
