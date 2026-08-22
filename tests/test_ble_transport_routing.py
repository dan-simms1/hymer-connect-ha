"""Coordinator transport-selection routing (no hardware, no cloud)."""

from __future__ import annotations

import importlib
from types import SimpleNamespace
import unittest

from tests.hymer_test_support import ensure_package_paths, install_homeassistant_stubs


class TransportOrderTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        import sys

        install_homeassistant_stubs()
        ensure_package_paths()
        # install_homeassistant_stubs() registers a *stub* coordinator module;
        # drop it so we import the real one (same trick the coordinator tests use).
        sys.modules.pop("custom_components.hymer_connect_metadata.coordinator", None)
        cls.coord = importlib.import_module(
            "custom_components.hymer_connect_metadata.coordinator"
        )
        cls.const = importlib.import_module(
            "custom_components.hymer_connect_metadata.const"
        )

    def _order(self, method: str, options: dict) -> list[str]:
        Coordinator = self.coord.HymerConnectCoordinator
        fake = SimpleNamespace(
            config_entry=SimpleNamespace(options=options),
            _BLE_ROUTABLE=Coordinator._BLE_ROUTABLE,
        )
        return Coordinator._transport_order(fake, method)

    def test_ble_disabled_is_cloud_only(self) -> None:
        self.assertEqual(self._order("send_light_command", {}), ["cloud"])

    def test_ble_enabled_without_address_is_cloud_only(self) -> None:
        opts = {self.const.CONF_BLE_ENABLED: True}
        self.assertEqual(self._order("send_light_command", opts), ["cloud"])

    def test_fallback_mode_tries_cloud_then_ble(self) -> None:
        opts = {
            self.const.CONF_BLE_ENABLED: True,
            self.const.CONF_BLE_ADDRESS: "AA:BB:CC:DD:EE:FF",
            self.const.CONF_BLE_MODE: self.const.BLE_MODE_FALLBACK,
        }
        self.assertEqual(self._order("send_light_command", opts), ["cloud", "ble"])

    def test_primary_mode_tries_ble_then_cloud(self) -> None:
        opts = {
            self.const.CONF_BLE_ENABLED: True,
            self.const.CONF_BLE_ADDRESS: "AA:BB:CC:DD:EE:FF",
            self.const.CONF_BLE_MODE: self.const.BLE_MODE_PRIMARY,
        }
        self.assertEqual(self._order("send_light_command", opts), ["ble", "cloud"])

    def test_non_ble_routable_command_is_cloud_only_even_with_ble_on(self) -> None:
        opts = {
            self.const.CONF_BLE_ENABLED: True,
            self.const.CONF_BLE_ADDRESS: "AA:BB:CC:DD:EE:FF",
            self.const.CONF_BLE_MODE: self.const.BLE_MODE_PRIMARY,
        }
        # raw PIA requests / restart / slot actions stay on the cloud
        self.assertEqual(self._order("send_pia_request", opts), ["cloud"])
        self.assertEqual(self._order("send_restart_system_command", opts), ["cloud"])
        self.assertEqual(self._order("send_multi_sensor_command", opts), ["ble", "cloud"])


if __name__ == "__main__":
    unittest.main()
