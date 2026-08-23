"""Adversarial tests from the 1.1.0 Codex review — concurrency, lifecycle, cap."""

from __future__ import annotations

import asyncio
import importlib
import sys
import types
import unittest
from types import MethodType, SimpleNamespace
from unittest import mock

from tests.hymer_test_support import ensure_package_paths, install_homeassistant_stubs


class BleAdversarialTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        install_homeassistant_stubs()
        ensure_package_paths()
        cls.bt = importlib.import_module(
            "custom_components.hymer_connect_metadata.ble_transport"
        )
        cls.const = importlib.import_module(
            "custom_components.hymer_connect_metadata.const"
        )
        sys.modules.pop("custom_components.hymer_connect_metadata.coordinator", None)
        cls.coord = importlib.import_module(
            "custom_components.hymer_connect_metadata.coordinator"
        )

    def test_concurrent_ble_requests_are_serialized(self) -> None:
        """The session lock must stop two commands interleaving on one TLS stream."""
        bt = self.bt

        async def run_test() -> int:
            t = bt.HymerBleTransport(None, "AA:BB:CC:DD:EE:FF")
            t._client = SimpleNamespace(is_connected=True)
            t._tls = SimpleNamespace(handshake_complete=True)
            active = 0
            peak = 0

            async def fake_send(_frame, request_id):
                nonlocal active, peak
                active += 1
                peak = max(peak, active)
                await asyncio.sleep(0.01)
                active -= 1
                return SimpleNamespace(request_id=request_id, status=1, succeeded=True)

            t._send_frame_await_response = fake_send
            await asyncio.gather(
                t.send_light_command(11, 1, bool_value=True),
                t.send_light_command(12, 1, bool_value=False),
            )
            return peak

        self.assertEqual(asyncio.run(run_test()), 1)

    def test_failed_start_disconnects_partial_transport(self) -> None:
        """A start() failure must not leak a half-open transport."""
        bt = self.bt
        stopped = {"v": False}

        class FakeTransport:
            connected = False

            def __init__(self, _hass, _address):
                pass

            async def start(self):
                raise bt.BleTransportError("TLS failed")

            async def stop(self):
                stopped["v"] = True

        fake = SimpleNamespace(
            _ble=None,
            _ble_start_lock=None,
            hass=None,
            config_entry=SimpleNamespace(
                title="Van",
                options={self.const.CONF_BLE_ADDRESS: "AA:BB:CC:DD:EE:FF"},
            ),
        )

        async def stop_ble():
            fake._ble = None

        fake._stop_ble = stop_ble
        original = bt.HymerBleTransport
        bt.HymerBleTransport = FakeTransport
        try:
            result = asyncio.run(
                self.coord.HymerConnectCoordinator._ensure_ble(fake)
            )
        finally:
            bt.HymerBleTransport = original
        self.assertIsNone(result)
        self.assertTrue(stopped["v"], "partial transport must be stopped")

    def test_signalr_restart_does_not_stop_healthy_ble(self) -> None:
        """SignalR teardown (runs on every reconnect) must leave BLE alone."""
        ble_stopped = {"v": False}

        async def stop_ble():
            ble_stopped["v"] = True

        fake = SimpleNamespace(
            _capability_reload_task=None,
            _capability_reload_slots=set(),
            _signalr=None,
            _suppress_connection_lost_refresh=False,
            _cancel_reconnect_task=lambda: None,
            _stop_ble=stop_ble,
        )
        asyncio.run(self.coord.HymerConnectCoordinator._stop_signalr_locked(fake))
        self.assertFalse(ble_stopped["v"])

    def test_pairing_rejects_explicit_error_status(self) -> None:
        """A response with an error status must not be accepted as a minted token."""
        bt = self.bt
        stopped = {"v": False}

        class FakeApi:
            async def get_confirmation_token_value(self):
                return "confirmation"

        class FakeResponse:
            status = 5  # ACCESS_DENIED
            remote_access_refresh_token = "must-not-be-accepted"
            remote_access_token = "must-not-be-accepted"
            confirmation_required = False

        class FakeTransport:
            def __init__(self, _hass, _address):
                pass

            async def start(self):
                pass

            async def pair_mobile(self, *_args):
                return FakeResponse()

            async def stop(self):
                stopped["v"] = True

        original = bt.HymerBleTransport
        bt.HymerBleTransport = FakeTransport
        try:
            with self.assertRaises(bt.BleTransportError):
                asyncio.run(bt.async_pair_over_ble(
                    None, FakeApi(), "AA:BB:CC:DD:EE:FF", "owner-activation-token",
                ))
        finally:
            bt.HymerBleTransport = original
        self.assertTrue(stopped["v"], "transport must be stopped even when rejected")

    def test_oversized_frame_length_is_rejected(self) -> None:
        bt = self.bt
        t = bt.HymerBleTransport(None, "AA:BB:CC:DD:EE:FF")
        oversized = (
            bt.ble_pia.BLE_PIA_MAGIC
            + (2 ** 31).to_bytes(4, "big")
            + b"\x00\x00\x00\x00"
        )
        with self.assertRaises(bt.BleTransportError):
            t._feed_frames(oversized)


    def test_reconfigure_rejects_wrong_vehicle_from_raw_urn(self) -> None:
        """byToken returns raw EHG fields; a different urn must fail closed."""
        try:
            config_flow = importlib.import_module(
                "custom_components.hymer_connect_metadata.config_flow"
            )
        except ImportError as err:
            self.skipTest(f"config_flow deps unavailable in this env: {err}")
        bt = self.bt
        const = self.const
        paired = {"v": False}

        class FakeApi:
            async def get_vehicle_by_token(self, _token):
                return {"urn": "urn:ehg:vehicle:wrong"}

        entry = SimpleNamespace(
            unique_id="urn:ehg:vehicle:expected",
            data={
                const.CONF_BRAND: "hymer",
                "username": "user@example.com",
                "password": "secret",
                const.CONF_VEHICLE_URN: "urn:ehg:vehicle:expected",
            },
        )
        flow = config_flow.HymerConnectConfigFlow()
        flow.hass = None
        flow._get_reconfigure_entry = lambda: entry

        async def authenticate(*_a):
            return FakeApi(), {"access_token": "a", "refresh_token": "r"}

        async def resolve(*_a):
            return {"vehicle_urn": "urn:ehg:vehicle:expected"}

        async def prepare(*_a):
            return None

        async def fake_pair(*_a, **_k):
            paired["v"] = True
            return {"ehg_refresh_token": "must-not-be-stored"}

        flow._async_authenticate_api = authenticate
        flow._async_resolve_entry_vehicle = resolve
        flow._async_prepare_entry_identity = prepare
        flow.async_update_reload_and_abort = lambda *_a, **_k: {"type": "updated"}
        flow.async_show_form = lambda **kw: {"type": "form", **kw}

        selector_mod = types.ModuleType("homeassistant.helpers.selector")
        selector_mod.TextSelectorType = SimpleNamespace(PASSWORD="password")
        selector_mod.TextSelectorConfig = lambda **_k: None
        selector_mod.TextSelector = lambda _c: str
        helpers = sys.modules["homeassistant.helpers"]

        with (
            mock.patch.object(bt, "async_pair_over_ble", fake_pair),
            mock.patch.dict(sys.modules, {"homeassistant.helpers.selector": selector_mod}),
            mock.patch.object(helpers, "selector", selector_mod, create=True),
        ):
            result = asyncio.run(flow.async_step_reconfigure({
                const.CONF_EHG_REFRESH_TOKEN: "",
                const.CONF_QR_TOKEN: "qr-token",
                const.CONF_BLE_ADDRESS: "AA:BB:CC:DD:EE:FF",
            }))

        self.assertFalse(paired["v"])
        self.assertEqual(result["errors"]["base"], "qr_token_wrong_vehicle")

    def test_reconfigure_binds_to_stored_urn_not_resolver_fallback(self) -> None:
        """Resolver may fall back to a different SCU; binding must use stored urn.

        Entry is bound to vehicle A, but A has vanished from the account and
        resolve_vehicle_selection()'s single-vehicle fallback now returns a
        DIFFERENT vehicle B. A QR for B must be refused (vehicle_not_found):
        the resolved vehicle no longer matches the entry, so we refuse before
        pairing AND before the success path could migrate identity/metadata to
        B. The real resolver runs here (it is NOT mocked to the expected one).
        """
        try:
            config_flow = importlib.import_module(
                "custom_components.hymer_connect_metadata.config_flow"
            )
        except ImportError as err:
            self.skipTest(f"config_flow deps unavailable in this env: {err}")
        bt = self.bt
        const = self.const
        paired = {"v": False}

        class FakeApi:
            async def resolve_vehicle_selection(self, **_kwargs):
                # Stored urn A is gone; fall back to the only vehicle, B.
                return {"vehicle_urn": "urn:ehg:vehicle:B-fallback"}

            async def get_vehicle_by_token(self, _token):
                return {"urn": "urn:ehg:vehicle:B-fallback"}

        entry = SimpleNamespace(
            unique_id="urn:ehg:vehicle:A-stored",
            data={
                const.CONF_BRAND: "hymer",
                "username": "user@example.com",
                "password": "secret",
                const.CONF_VEHICLE_URN: "urn:ehg:vehicle:A-stored",
            },
        )
        flow = config_flow.HymerConnectConfigFlow()
        flow.hass = None
        flow._get_reconfigure_entry = lambda: entry

        async def authenticate(*_a):
            return FakeApi(), {"access_token": "a", "refresh_token": "r"}

        async def prepare(*_a):
            return None

        async def fake_pair(*_a, **_k):
            paired["v"] = True
            return {"ehg_refresh_token": "must-not-be-stored"}

        flow._async_authenticate_api = authenticate
        # _async_resolve_entry_vehicle is deliberately NOT overridden: the real
        # method runs and returns B via the fake resolver fallback above.
        flow._async_prepare_entry_identity = prepare
        flow.async_update_reload_and_abort = lambda *_a, **_k: {"type": "updated"}
        flow.async_show_form = lambda **kw: {"type": "form", **kw}

        selector_mod = types.ModuleType("homeassistant.helpers.selector")
        selector_mod.TextSelectorType = SimpleNamespace(PASSWORD="password")
        selector_mod.TextSelectorConfig = lambda **_k: None
        selector_mod.TextSelector = lambda _c: str
        helpers = sys.modules["homeassistant.helpers"]

        with (
            mock.patch.object(bt, "async_pair_over_ble", fake_pair),
            mock.patch.dict(sys.modules, {"homeassistant.helpers.selector": selector_mod}),
            mock.patch.object(helpers, "selector", selector_mod, create=True),
        ):
            result = asyncio.run(flow.async_step_reconfigure({
                const.CONF_EHG_REFRESH_TOKEN: "",
                const.CONF_QR_TOKEN: "qr-token",
                const.CONF_BLE_ADDRESS: "AA:BB:CC:DD:EE:FF",
            }))

        self.assertFalse(
            paired["v"], "must not pair against a resolver fallback vehicle"
        )
        self.assertEqual(result["errors"]["base"], "vehicle_not_found")

    def test_reconfigure_refuses_when_stored_vehicle_absent_even_with_matching_qr(
        self,
    ) -> None:
        """Even a VALID QR for the stored vehicle must not pair when discovery
        cannot confirm that vehicle.

        Entry stores A; discovery lost A and the single-vehicle fallback returns
        B; the QR is genuinely for A. Round 6's stored-urn check alone would let
        this pair (stored A == token A) and THEN abort/migrate onto B, minting
        A's token as a side effect. The resolved-vehicle guard must refuse first.
        """
        try:
            config_flow = importlib.import_module(
                "custom_components.hymer_connect_metadata.config_flow"
            )
        except ImportError as err:
            self.skipTest(f"config_flow deps unavailable in this env: {err}")
        bt = self.bt
        const = self.const
        paired = {"v": False}

        class FakeApi:
            async def resolve_vehicle_selection(self, **_kwargs):
                return {"vehicle_urn": "urn:ehg:vehicle:B-fallback"}

            async def get_vehicle_by_token(self, _token):
                # A valid owner QR for the entry's ACTUAL vehicle, A.
                return {"urn": "urn:ehg:vehicle:A-stored"}

        entry = SimpleNamespace(
            unique_id="urn:ehg:vehicle:A-stored",
            data={
                const.CONF_BRAND: "hymer",
                "username": "user@example.com",
                "password": "secret",
                const.CONF_VEHICLE_URN: "urn:ehg:vehicle:A-stored",
            },
        )
        flow = config_flow.HymerConnectConfigFlow()
        flow.hass = None
        flow._get_reconfigure_entry = lambda: entry

        async def authenticate(*_a):
            return FakeApi(), {"access_token": "a", "refresh_token": "r"}

        async def prepare(*_a):
            return None

        async def fake_pair(*_a, **_k):
            paired["v"] = True
            return {"ehg_refresh_token": "must-not-be-stored"}

        flow._async_authenticate_api = authenticate
        flow._async_prepare_entry_identity = prepare
        flow.async_update_reload_and_abort = lambda *_a, **_k: {"type": "updated"}
        flow.async_show_form = lambda **kw: {"type": "form", **kw}

        selector_mod = types.ModuleType("homeassistant.helpers.selector")
        selector_mod.TextSelectorType = SimpleNamespace(PASSWORD="password")
        selector_mod.TextSelectorConfig = lambda **_k: None
        selector_mod.TextSelector = lambda _c: str
        helpers = sys.modules["homeassistant.helpers"]

        with (
            mock.patch.object(bt, "async_pair_over_ble", fake_pair),
            mock.patch.dict(sys.modules, {"homeassistant.helpers.selector": selector_mod}),
            mock.patch.object(helpers, "selector", selector_mod, create=True),
        ):
            result = asyncio.run(flow.async_step_reconfigure({
                const.CONF_EHG_REFRESH_TOKEN: "",
                const.CONF_QR_TOKEN: "qr-token",
                const.CONF_BLE_ADDRESS: "AA:BB:CC:DD:EE:FF",
            }))

        self.assertFalse(
            paired["v"], "must not pair when discovery cannot confirm the vehicle"
        )
        self.assertEqual(result["errors"]["base"], "vehicle_not_found")

    def test_qr_lookup_transient_error_is_not_reported_as_bad_token(self) -> None:
        """A 5xx/connection failure on byToken is cannot_connect, not invalid_qr."""
        try:
            config_flow = importlib.import_module(
                "custom_components.hymer_connect_metadata.config_flow"
            )
        except ImportError as err:
            self.skipTest(f"config_flow deps unavailable in this env: {err}")
        api_mod = importlib.import_module(
            "custom_components.hymer_connect_metadata.api"
        )
        const = self.const

        def run(exc):
            class FakeApi:
                async def resolve_vehicle_selection(self, **_kwargs):
                    return {"vehicle_urn": "urn:ehg:vehicle:A-stored"}

                async def get_vehicle_by_token(self, _token):
                    raise exc

            entry = SimpleNamespace(
                unique_id="urn:ehg:vehicle:A-stored",
                data={
                    const.CONF_BRAND: "hymer",
                    "username": "user@example.com",
                    "password": "secret",
                    const.CONF_VEHICLE_URN: "urn:ehg:vehicle:A-stored",
                },
            )
            flow = config_flow.HymerConnectConfigFlow()
            flow.hass = None
            flow._get_reconfigure_entry = lambda: entry

            async def authenticate(*_a):
                return FakeApi(), {"access_token": "a", "refresh_token": "r"}

            flow._async_authenticate_api = authenticate
            flow.async_show_form = lambda **kw: {"type": "form", **kw}

            selector_mod = types.ModuleType("homeassistant.helpers.selector")
            selector_mod.TextSelectorType = SimpleNamespace(PASSWORD="password")
            selector_mod.TextSelectorConfig = lambda **_k: None
            selector_mod.TextSelector = lambda _c: str
            helpers = sys.modules["homeassistant.helpers"]

            with (
                mock.patch.dict(
                    sys.modules, {"homeassistant.helpers.selector": selector_mod}
                ),
                mock.patch.object(helpers, "selector", selector_mod, create=True),
            ):
                return asyncio.run(flow.async_step_reconfigure({
                    const.CONF_EHG_REFRESH_TOKEN: "",
                    const.CONF_QR_TOKEN: "qr-token",
                    const.CONF_BLE_ADDRESS: "AA:BB:CC:DD:EE:FF",
                }))

        transient = run(api_mod.HymerConnectApiError("upstream down", status=503))
        self.assertEqual(transient["errors"]["base"], "cannot_connect")
        rejected = run(api_mod.HymerConnectApiError("bad token", status=404))
        self.assertEqual(rejected["errors"]["base"], "invalid_qr_token")
        rate_limited = run(
            api_mod.HymerConnectApiError("too many requests", status=429)
        )
        self.assertEqual(rate_limited["errors"]["base"], "cannot_connect")
        # A malformed 200 body (JSONDecodeError/ValueError) is NOT a bad QR.
        malformed = run(ValueError("Expecting value: line 1 column 1 (char 0)"))
        self.assertEqual(malformed["errors"]["base"], "unknown")

    def test_reconfigure_aborts_before_pairing_on_identity_collision(self) -> None:
        """A unique-id collision must ABORT before pairing mints a token.

        All URN gates pass (stored == resolved == token), but preparing the
        entry identity would collide with another entry. That must abort the
        flow BEFORE async_pair_over_ble, not after the SCU has minted a token.
        """
        try:
            config_flow = importlib.import_module(
                "custom_components.hymer_connect_metadata.config_flow"
            )
        except ImportError as err:
            self.skipTest(f"config_flow deps unavailable in this env: {err}")
        bt = self.bt
        const = self.const
        paired = {"v": False}

        class FakeApi:
            async def resolve_vehicle_selection(self, **_kwargs):
                return {"vehicle_urn": "urn:ehg:vehicle:A-stored"}

            async def get_vehicle_by_token(self, _token):
                return {"urn": "urn:ehg:vehicle:A-stored"}

        entry = SimpleNamespace(
            unique_id="user@example.com",  # legacy account unique id
            data={
                const.CONF_BRAND: "hymer",
                "username": "user@example.com",
                "password": "secret",
                const.CONF_VEHICLE_URN: "urn:ehg:vehicle:A-stored",
            },
        )
        flow = config_flow.HymerConnectConfigFlow()
        flow.hass = None
        flow._get_reconfigure_entry = lambda: entry

        async def authenticate(*_a):
            return FakeApi(), {"access_token": "a", "refresh_token": "r"}

        async def fake_pair(*_a, **_k):
            paired["v"] = True
            return {"ehg_refresh_token": "must-not-be-minted"}

        async def preflight_collides(_entry, _uid):
            # Another entry already owns this vehicle's unique id.
            raise config_flow.AbortFlow("already_configured")

        flow._async_authenticate_api = authenticate
        flow._async_preflight_entry_identity = preflight_collides
        flow._vehicle_unique_id = lambda _vehicle, **_k: "urn:ehg:vehicle:A-stored"
        flow.async_show_form = lambda **kw: {"type": "form", **kw}

        selector_mod = types.ModuleType("homeassistant.helpers.selector")
        selector_mod.TextSelectorType = SimpleNamespace(PASSWORD="password")
        selector_mod.TextSelectorConfig = lambda **_k: None
        selector_mod.TextSelector = lambda _c: str
        helpers = sys.modules["homeassistant.helpers"]

        with (
            mock.patch.object(bt, "async_pair_over_ble", fake_pair),
            mock.patch.dict(sys.modules, {"homeassistant.helpers.selector": selector_mod}),
            mock.patch.object(helpers, "selector", selector_mod, create=True),
            self.assertRaises(config_flow.AbortFlow),
        ):
            asyncio.run(flow.async_step_reconfigure({
                const.CONF_EHG_REFRESH_TOKEN: "",
                const.CONF_QR_TOKEN: "qr-token",
                const.CONF_BLE_ADDRESS: "AA:BB:CC:DD:EE:FF",
            }))

        self.assertFalse(paired["v"], "must abort BEFORE minting a token")

    def test_generic_cloud_failure_still_falls_back_to_ble(self) -> None:
        """Fallback must apply to any cloud failure, not one exception class."""
        Coordinator = self.coord.HymerConnectCoordinator
        calls = []

        class FakeCoordinator:
            config_entry = SimpleNamespace(title="Van")

            def _transport_order(self, _method):
                return ["cloud", "ble"]

            async def async_ensure_signalr_healthy(self):
                raise RuntimeError("malformed negotiate response")

            async def _try_ble_send(self, method, *_a, **_k):
                calls.append(method)
                return True

        fake = FakeCoordinator()
        fake._try_cloud_send = MethodType(Coordinator._try_cloud_send, fake)
        fake._send_with_retry = MethodType(Coordinator._send_with_retry, fake)
        asyncio.run(fake._send_with_retry("send_light_command", 11, 1))
        self.assertEqual(calls, ["send_light_command"])

    def test_pair_mobile_never_confirms_error_status(self) -> None:
        """The real pair_mobile must not confirm a rejected pairing."""
        bt = self.bt
        transport = bt.HymerBleTransport(None, "AA:BB:CC:DD:EE:FF")
        transport._tls = SimpleNamespace(handshake_complete=True)
        confirmations = []

        async def rejected(_frame, _request_id):
            return SimpleNamespace(status=5)

        async def record(frame):
            confirmations.append(frame)

        transport._send_pair_and_await = rejected
        transport._write_plaintext = record

        with self.assertRaises(bt.BleTransportError):
            asyncio.run(transport.pair_mobile("activation", "confirmation", "ha"))
        self.assertEqual(confirmations, [])


    def test_cancelled_real_start_disconnects_partial_client(self) -> None:
        """Cancellation during startup must disconnect the partial GATT client."""
        bt = self.bt

        async def run_test() -> bool:
            transport = bt.HymerBleTransport(None, "AA:BB:CC:DD:EE:FF")
            connected = asyncio.Event()
            blocked = asyncio.Event()
            disconnected = {"v": False}

            async def disconnect():
                disconnected["v"] = True

            async def connect():
                transport._client = SimpleNamespace(is_connected=True, disconnect=disconnect)
                connected.set()

            async def wait_forever():
                await blocked.wait()

            transport._connect = connect
            transport._ensure_bonded = wait_forever

            task = asyncio.create_task(transport.start())
            await connected.wait()
            task.cancel()
            with self.assertRaises(asyncio.CancelledError):
                await task
            return disconnected["v"]

        self.assertTrue(asyncio.run(run_test()))

    def test_shutdown_quiesces_inflight_ble_start(self) -> None:
        """Shutdown must cancel and await an unpublished in-flight BLE start."""
        bt = self.bt
        Coordinator = self.coord.HymerConnectCoordinator
        started = asyncio.Event()
        release = asyncio.Event()

        class FakeTransport:
            connected = False

            def __init__(self, _hass, _address):
                pass

            async def start(self):
                started.set()
                await release.wait()

            async def stop(self):
                pass

        fake = SimpleNamespace(
            _ble=None, _ble_start_lock=None, _ble_start_task=None,
            _shutting_down=False, _suppress_connection_lost_refresh=False,
            _reconnect_task=None, hass=None,
            config_entry=SimpleNamespace(
                title="Van",
                options={self.const.CONF_BLE_ADDRESS: "AA:BB:CC:DD:EE:FF"},
            ),
            _cancel_reconnect_task=lambda: None,
        )
        fake.mark_shutting_down = MethodType(Coordinator.mark_shutting_down, fake)
        fake._stop_ble = MethodType(Coordinator._stop_ble, fake)

        async def noop():
            pass

        fake.async_cancel_background_tasks = noop
        fake.stop_signalr = noop

        async def run_test():
            connect_task = asyncio.create_task(Coordinator._ensure_ble(fake))
            await started.wait()
            try:
                await Coordinator.async_prepare_for_shutdown(fake)
                self.assertTrue(
                    connect_task.done(),
                    "shutdown returned while BLE startup was still active",
                )
            finally:
                release.set()
                await asyncio.gather(connect_task, return_exceptions=True)

        with mock.patch.object(bt, "HymerBleTransport", FakeTransport):
            asyncio.run(run_test())

    def test_resolve_device_path_finds_non_default_adapter(self) -> None:
        """ObjectManager discovery must select hci1, not reconstruct hci0."""
        transport = self.bt.HymerBleTransport(None, "AA:BB:CC:DD:EE:FF", adapter="hci0")
        manager = SimpleNamespace(
            call_get_managed_objects=mock.AsyncMock(return_value={
                "/org/bluez/hci1/dev_AA_BB_CC_DD_EE_FF": {"org.bluez.Device1": {}},
            })
        )
        proxy = SimpleNamespace(get_interface=mock.Mock(return_value=manager))
        bus = SimpleNamespace(
            introspect=mock.AsyncMock(return_value=object()),
            get_proxy_object=mock.Mock(return_value=proxy),
        )
        path = asyncio.run(transport._resolve_device_path(bus))
        self.assertEqual(path, "/org/bluez/hci1/dev_AA_BB_CC_DD_EE_FF")

    def test_english_translations_cover_new_ble_strings(self) -> None:
        """en.json must contain every key strings.json declares for the flow."""
        import json
        from pathlib import Path

        comp = Path(__file__).resolve().parents[1] / "custom_components" / "hymer_connect_metadata"
        strings = json.loads((comp / "strings.json").read_text())
        english = json.loads((comp / "translations" / "en.json").read_text())
        pairs = (
            (strings["config"]["step"]["reconfigure"]["data"],
             english["config"]["step"]["reconfigure"]["data"]),
            (strings["config"]["error"], english["config"]["error"]),
            (strings["options"]["step"]["init"]["data"],
             english["options"]["step"]["init"]["data"]),
        )
        for source, translated in pairs:
            self.assertLessEqual(set(source), set(translated))


    def test_cancel_during_raw_connect_disconnects_partial_client(self) -> None:
        """A client connected before BleakClient.connect returns must not leak."""
        bt = self.bt

        async def run_test() -> bool:
            entered = asyncio.Event()
            disconnected = {"v": False}

            class FakeBleakClient:
                def __init__(self, *_a, **_k):
                    self.is_connected = False

                async def connect(self):
                    self.is_connected = True
                    entered.set()
                    await asyncio.Event().wait()

                async def disconnect(self):
                    disconnected["v"] = True
                    self.is_connected = False

            bleak_module = types.ModuleType("bleak")
            bleak_module.BleakClient = FakeBleakClient
            with mock.patch.dict(sys.modules, {"bleak": bleak_module}):
                transport = bt.HymerBleTransport(None, "AA:BB:CC:DD:EE:FF")
                task = asyncio.create_task(transport.start())
                await asyncio.wait_for(entered.wait(), timeout=1)
                task.cancel()
                with self.assertRaises(asyncio.CancelledError):
                    await task
            return disconnected["v"]

        self.assertTrue(asyncio.run(run_test()))

    def test_shutdown_blocks_start_waiting_in_prestart_cleanup(self) -> None:
        """Shutdown must prevent a start not yet registered in _ble_start_task."""
        bt = self.bt
        Coordinator = self.coord.HymerConnectCoordinator
        old_stopping = asyncio.Event()
        release_old = asyncio.Event()
        new_started = asyncio.Event()
        release_new = asyncio.Event()

        class OldTransport:
            connected = False

            async def stop(self):
                old_stopping.set()
                await release_old.wait()

        class NewTransport:
            connected = False

            def __init__(self, _hass, _address):
                pass

            async def start(self):
                new_started.set()
                await release_new.wait()

            async def stop(self):
                pass

        fake = SimpleNamespace(
            _ble=OldTransport(), _ble_start_lock=None, _ble_start_task=None,
            _shutting_down=False, _suppress_connection_lost_refresh=False,
            _reconnect_task=None, hass=None,
            config_entry=SimpleNamespace(
                title="Van", options={self.const.CONF_BLE_ADDRESS: "AA:BB:CC:DD:EE:FF"}),
            _cancel_reconnect_task=lambda: None,
        )
        fake.mark_shutting_down = MethodType(Coordinator.mark_shutting_down, fake)
        fake._stop_ble = MethodType(Coordinator._stop_ble, fake)

        async def noop():
            pass

        fake.async_cancel_background_tasks = noop
        fake.stop_signalr = noop

        async def run_test() -> bool:
            ensure_task = asyncio.create_task(Coordinator._ensure_ble(fake))
            await old_stopping.wait()
            await Coordinator.async_prepare_for_shutdown(fake)
            release_old.set()
            started_after_shutdown = False
            try:
                await asyncio.wait_for(new_started.wait(), timeout=0.1)
                started_after_shutdown = True
            except asyncio.TimeoutError:
                pass
            finally:
                release_new.set()
                await asyncio.gather(ensure_task, return_exceptions=True)
            return started_after_shutdown

        with mock.patch.object(bt, "HymerBleTransport", NewTransport):
            self.assertFalse(asyncio.run(run_test()))

    def test_shutdown_guard_precedes_connected_fast_path(self) -> None:
        """No transport may be handed out once shutdown has begun."""
        fake = SimpleNamespace(
            _ble=SimpleNamespace(connected=True), _ble_start_lock=None,
            _shutting_down=True,
        )
        result = asyncio.run(self.coord.HymerConnectCoordinator._ensure_ble(fake))
        self.assertIsNone(result)

    def test_english_reconfigure_description_matches_source(self) -> None:
        """English runtime text must include the new pairing instructions."""
        import json
        from pathlib import Path

        comp = Path(__file__).resolve().parents[1] / "custom_components" / "hymer_connect_metadata"
        strings = json.loads((comp / "strings.json").read_text())
        english = json.loads((comp / "translations" / "en.json").read_text())
        self.assertEqual(
            strings["config"]["step"]["reconfigure"]["description"],
            english["config"]["step"]["reconfigure"]["description"],
        )

    def test_shutdown_is_monotonic_across_inflight_reauth(self) -> None:
        """Unload during a reauth must not resurrect SignalR (shutdown is final)."""
        coord = self.coord
        started = {"v": False}

        async def run_test():
            fake = SimpleNamespace(
                config_entry=SimpleNamespace(
                    title="van",
                    data={"username": "user@example.com", "password": "secret"},
                ),
                _shutting_down=False,
                _suppress_connection_lost_refresh=True,
                _last_reconnect_attempt=1.0,
                _reconnect_backoff=99.0,
            )

            async def stop_signalr():
                pass

            async def start_signalr():
                started["v"] = True

            class FakeApi:
                async def authenticate(self, _u, _p):
                    # Unload begins WHILE we are still re-authenticating.
                    fake._shutting_down = True

                async def _refresh_access_token(self):
                    pass

            fake.api = FakeApi()
            fake.stop_signalr = stop_signalr
            fake.start_signalr = start_signalr
            await coord.HymerConnectCoordinator.force_reauth_and_reconnect(fake)
            return fake._shutting_down, started["v"]

        shutting_down, start_called = asyncio.run(run_test())
        self.assertTrue(shutting_down, "shutdown flag must stay set — it is terminal")
        self.assertFalse(start_called, "must not start SignalR once shutdown began")

    def test_raw_connect_exception_disconnects_partial_client(self) -> None:
        """If raw BleakClient.connect() RAISES, the partial client is disconnected."""
        bt = self.bt

        async def run_test() -> bool:
            disconnected = {"v": False}

            class FakeBleakClient:
                def __init__(self, *_a, **_k):
                    self.is_connected = False

                async def connect(self):
                    self.is_connected = True
                    raise RuntimeError("connect blew up mid-handshake")

                async def disconnect(self):
                    disconnected["v"] = True
                    self.is_connected = False

            bleak_module = types.ModuleType("bleak")
            bleak_module.BleakClient = FakeBleakClient
            with mock.patch.dict(sys.modules, {"bleak": bleak_module}):
                transport = bt.HymerBleTransport(None, "AA:BB:CC:DD:EE:FF")
                with self.assertRaises(RuntimeError):
                    await transport.start()
            return disconnected["v"]

        self.assertTrue(asyncio.run(run_test()))

    def test_every_locale_reconfigure_description_matches_source(self) -> None:
        """Every shipped locale must carry the reconfigure pairing instructions."""
        import json
        from pathlib import Path

        comp = (
            Path(__file__).resolve().parents[1]
            / "custom_components"
            / "hymer_connect_metadata"
        )
        expected = json.loads((comp / "strings.json").read_text())[
            "config"
        ]["step"]["reconfigure"]["description"]
        translations = sorted((comp / "translations").glob("*.json"))
        self.assertTrue(translations, "no translation files found")
        for path in translations:
            data = json.loads(path.read_text())
            desc = data["config"]["step"]["reconfigure"]["description"]
            self.assertEqual(
                desc, expected, f"{path.name} reconfigure description drifted"
            )


if __name__ == "__main__":
    unittest.main()
