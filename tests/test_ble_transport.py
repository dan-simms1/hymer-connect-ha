"""Unit tests for the BLE transport's hardware-independent logic.

Covers the (bus, sensor, value) -> ConnectedComponentValue mapping and the BLE
PIA frame reassembler. Connection/bonding/TLS need a vehicle and are not tested
here.
"""

from __future__ import annotations

import importlib
import unittest

from tests.hymer_test_support import ensure_package_paths, install_homeassistant_stubs


class BleTransportLogicTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        install_homeassistant_stubs()
        ensure_package_paths()
        cls.bt = importlib.import_module(
            "custom_components.hymer_connect_metadata.ble_transport"
        )
        cls.pia = importlib.import_module(
            "custom_components.hymer_connect_metadata.ble_pia"
        )

    def test_light_args_map_to_connected_component_value(self) -> None:
        got = self.bt._values_from_light_args(
            11, 1, bool_value=True, uint_value=None, str_value=None
        )
        want = self.pia.build_connected_component_value(
            value_id=1, component_id=11, bool_value=True
        )
        self.assertEqual(got, want)

    def test_uint_maps_to_int32(self) -> None:
        got = self.bt._values_from_light_args(
            11, 2, bool_value=None, uint_value=70, str_value=None
        )
        want = self.pia.build_connected_component_value(
            value_id=2, component_id=11, int_value=70
        )
        self.assertEqual(got, want)

    def test_sensor_dict_datatypes(self) -> None:
        cases = [
            ({"bus_id": 11, "sensor_id": 1, "bool_value": True},
             {"value_id": 1, "component_id": 11, "bool_value": True}),
            ({"bus_id": 11, "sensor_id": 2, "uint_value": 55},
             {"value_id": 2, "component_id": 11, "int_value": 55}),
            ({"bus_id": 5, "sensor_id": 3, "float_value": 21.5},
             {"value_id": 3, "component_id": 5, "float_value": 21.5}),
            ({"bus_id": 3, "sensor_id": 1, "str_value": "On"},
             {"value_id": 1, "component_id": 3, "string_value": "On"}),
        ]
        for sensor, expected_kwargs in cases:
            got = self.bt._value_from_sensor_dict(sensor)
            want = self.pia.build_connected_component_value(**expected_kwargs)
            self.assertEqual(got, want, f"mismatch for {sensor}")

    def test_frame_reassembler_handles_split_and_concatenated_frames(self) -> None:
        t = self.bt.HymerBleTransport(hass=None, scu_address="AA:BB:CC:DD:EE:FF")
        v = self.pia.build_connected_component_value(value_id=1, component_id=11, bool_value=True)
        frame, _ = self.pia.build_set_values_ble_pia_frame([v], request_id=1, timestamp=2)

        # split one frame across three chunks -> exactly one frame out
        out = []
        out += t._feed_frames(frame[:3])
        out += t._feed_frames(frame[3:9])
        out += t._feed_frames(frame[9:])
        self.assertEqual(out, [frame])

        # two frames concatenated in one chunk -> both out
        out2 = t._feed_frames(frame + frame)
        self.assertEqual(out2, [frame, frame])

    def test_empty_multi_sensor_is_noop_true(self) -> None:
        import asyncio

        t = self.bt.HymerBleTransport(hass=None, scu_address="AA:BB:CC:DD:EE:FF")
        self.assertTrue(asyncio.run(t.send_multi_sensor_command([])))


class PairOverBleOrchestrationTests(unittest.TestCase):
    """async_pair_over_ble wires cloud confirmation-token + BLE pairing together."""

    @classmethod
    def setUpClass(cls) -> None:
        install_homeassistant_stubs()
        ensure_package_paths()
        cls.bt = importlib.import_module(
            "custom_components.hymer_connect_metadata.ble_transport"
        )

    def test_pairing_returns_the_minted_tokens(self) -> None:
        import asyncio

        bt = self.bt
        calls = {}

        class FakeApi:
            async def get_confirmation_token_value(self):
                calls["confirmation"] = True
                return "fake-confirmation-token"

        class FakeResponse:
            remote_access_refresh_token = "fake-refresh-660chars"
            remote_access_token = "fake-access-661chars"
            confirmation_required = False

        class FakeTransport:
            def __init__(self, hass, address):
                calls["address"] = address
            async def start(self):
                calls["started"] = True
            async def pair_mobile(self, activation, confirmation, name):
                calls["pair"] = (activation, confirmation, name)
                return FakeResponse()
            async def stop(self):
                calls["stopped"] = True

        original = bt.HymerBleTransport
        bt.HymerBleTransport = FakeTransport
        try:
            out = asyncio.run(bt.async_pair_over_ble(
                hass=None, api=FakeApi(),
                scu_address="AA:BB:CC:DD:EE:FF",
                activation_token="fake-activation-jwt",
                mobile_device_name="home-assistant",
            ))
        finally:
            bt.HymerBleTransport = original

        self.assertEqual(out["ehg_refresh_token"], "fake-refresh-660chars")
        self.assertEqual(out["ehg_access_token"], "fake-access-661chars")
        self.assertTrue(calls["confirmation"])
        self.assertTrue(calls["started"])
        self.assertTrue(calls["stopped"])
        self.assertEqual(
            calls["pair"],
            ("fake-activation-jwt", "fake-confirmation-token", "home-assistant"),
        )

    def test_pairing_stops_transport_even_on_failure(self) -> None:
        import asyncio

        bt = self.bt
        stopped = {"v": False}

        class FakeApi:
            async def get_confirmation_token_value(self):
                return "conf"

        class FakeTransport:
            def __init__(self, hass, address):
                pass
            async def start(self):
                pass
            async def pair_mobile(self, *a):
                raise bt.BleTransportError("Timed out waiting for SCU BLE/TLS data")
            async def stop(self):
                stopped["v"] = True

        original = bt.HymerBleTransport
        bt.HymerBleTransport = FakeTransport
        try:
            with self.assertRaises(bt.BleTransportError):
                asyncio.run(bt.async_pair_over_ble(
                    hass=None, api=FakeApi(), scu_address="AA:BB:CC:DD:EE:FF",
                    activation_token="x",
                ))
        finally:
            bt.HymerBleTransport = original
        self.assertTrue(stopped["v"], "transport must be stopped even when pairing fails")


if __name__ == "__main__":
    unittest.main()
