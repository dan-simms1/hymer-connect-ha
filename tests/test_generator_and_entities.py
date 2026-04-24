from __future__ import annotations

import asyncio
import base64
import importlib
import json
import sys
import time
import types
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from unittest import mock
from zipfile import ZipFile

from tests.hymer_test_support import ensure_package_paths, install_homeassistant_stubs


class GeneratorAndEntityTests(unittest.TestCase):
    def test_signed_range_normalization(self) -> None:
        from scripts.generate_cleanroom_registry import _normalize_signed_32

        self.assertEqual(_normalize_signed_32(4294967023), -273)
        self.assertEqual(_normalize_signed_32(30), 30)

    def test_lock_binary_sensor_truth_table(self) -> None:
        install_homeassistant_stubs()
        entity_base = importlib.import_module("custom_components.hymer_connect_metadata.entity_base")
        cls = entity_base.HymerBinarySensor

        locked = cls.__new__(cls)
        locked._attr_device_class = entity_base.BinarySensorDeviceClass.LOCK
        locked._value = lambda: "LOCKED"
        self.assertFalse(locked.is_on)

        unlocked = cls.__new__(cls)
        unlocked._attr_device_class = entity_base.BinarySensorDeviceClass.LOCK
        unlocked._value = lambda: "UNLOCKED"
        self.assertTrue(unlocked.is_on)

    def test_binary_sensor_device_class_uses_label_words_not_substrings(self) -> None:
        install_homeassistant_stubs()
        entity_base = importlib.import_module("custom_components.hymer_connect_metadata.entity_base")

        self.assertIsNone(
            entity_base._binary_sensor_device_class_for_label(
                "bedroom_locker_accent_light"
            )
        )
        self.assertEqual(
            entity_base._binary_sensor_device_class_for_label(
                "central_locking_status"
            ),
            entity_base.BinarySensorDeviceClass.LOCK,
        )

    def test_raw_slot_policy_moves_raw_internal_slots_to_diagnostic(self) -> None:
        install_homeassistant_stubs()
        entity_base = importlib.import_module("custom_components.hymer_connect_metadata.entity_base")
        preferences = importlib.import_module("custom_components.hymer_connect_metadata.preferences")

        battery_type = type(
            "Meta",
            (),
            {"label": "battery_type", "deprecated": False, "wire_mode": "rw", "is_writable": True},
        )()
        self.assertEqual(
            entity_base.slot_entity_category(battery_type),
            entity_base.EntityCategory.DIAGNOSTIC,
        )
        self.assertFalse(entity_base.slot_entity_hidden_by_default(battery_type))

        user_active = type(
            "Meta",
            (),
            {"label": "user_active", "deprecated": False, "wire_mode": "rw", "is_writable": True},
        )()
        self.assertEqual(
            entity_base.slot_entity_category(user_active),
            entity_base.EntityCategory.DIAGNOSTIC,
        )
        self.assertFalse(entity_base.slot_entity_hidden_by_default(user_active))
        self.assertEqual(
            entity_base.slot_entity_name_override(
                type(
                    "Meta",
                    (),
                    {"label": "night_mode", "deprecated": False, "wire_mode": "rw", "is_writable": True},
                )()
            ),
            "Silent Mode",
        )
        self.assertEqual(
            entity_base.slot_entity_name_override(
                type(
                    "Meta",
                    (),
                    {"label": "brightness", "deprecated": False, "wire_mode": "rw", "is_writable": True},
                )(),
                type("Component", (), {"kind": "light", "name": "Communal"})(),
            ),
            "Communal Brightness",
        )
        self.assertEqual(
            entity_base.slot_entity_name_override(
                type(
                    "Meta",
                    (),
                    {"label": "night_mode", "deprecated": False, "wire_mode": "rw", "is_writable": True},
                )(),
                type("Component", (), {"kind": "fridge", "name": "Fridge Module 34"})(),
            ),
            "Fridge Silent Mode",
        )

        freezer_level = type(
            "Meta",
            (),
            {"label": "freezer_level", "deprecated": True, "wire_mode": "rw", "is_writable": True},
        )()
        self.assertEqual(
            entity_base.slot_entity_category(freezer_level),
            entity_base.EntityCategory.DIAGNOSTIC,
        )
        self.assertFalse(entity_base.slot_entity_hidden_by_default(freezer_level))

        combi_error = type(
            "Meta",
            (),
            {"label": "combi_error", "deprecated": False, "wire_mode": "r", "is_writable": False},
        )()
        self.assertIsNone(entity_base.slot_entity_category(combi_error))
        self.assertFalse(entity_base.slot_entity_hidden_by_default(combi_error))
        self.assertEqual(
            entity_base.slot_entity_name_override(
                combi_error,
                type("Component", (), {"kind": "truma_heater", "name": "Heater Module 58"})(),
            ),
            "Heater Error",
        )

        fridge_voltage = type(
            "Meta",
            (),
            {"label": "dcvoltage", "deprecated": False, "wire_mode": "r", "is_writable": False},
        )()
        self.assertEqual(
            entity_base.slot_entity_name_override(
                fridge_voltage,
                type("Component", (), {"kind": "fridge", "name": "Fridge Module 34"})(),
            ),
            "Fridge DC Voltage",
        )

        fridge_warning = type(
            "Meta",
            (),
            {
                "label": "warning_error_information",
                "deprecated": False,
                "wire_mode": "r",
                "is_writable": False,
            },
        )()
        self.assertEqual(
            entity_base.slot_entity_name_override(
                fridge_warning,
                type("Component", (), {"kind": "fridge", "name": "Fridge Module 34"})(),
            ),
            "Fridge Warning/Error",
        )

        device_failure = type(
            "Meta",
            (),
            {
                "label": "device_failure",
                "deprecated": False,
                "wire_mode": "r",
                "is_writable": False,
            },
        )()
        self.assertEqual(
            entity_base.slot_entity_name_override(
                device_failure,
                type("Component", (), {"kind": "bms", "name": "Battery Monitor 99"})(),
            ),
            "Battery Monitor Failure",
        )

        panel_busy = type(
            "Meta",
            (),
            {"label": "panel_busy", "deprecated": False, "wire_mode": "r", "is_writable": False},
        )()
        self.assertIsNone(entity_base.slot_entity_category(panel_busy))
        self.assertEqual(
            entity_base.slot_entity_name_override(
                panel_busy,
                type("Component", (), {"kind": "truma_heater", "name": "Heater Module 58"})(),
            ),
            "Heater Panel Busy",
        )

        lighting_module_signal = type(
            "Meta",
            (),
            {
                "label": "lighting_module_all_off",
                "deprecated": False,
                "wire_mode": "rw",
                "is_writable": True,
            },
        )()
        self.assertEqual(
            entity_base.slot_entity_category(lighting_module_signal),
            entity_base.EntityCategory.DIAGNOSTIC,
        )
        self.assertFalse(
            entity_base.slot_entity_hidden_by_default(lighting_module_signal)
        )

        water_pump = type(
            "Meta",
            (),
            {"label": "water_pump", "deprecated": False, "wire_mode": "rw", "is_writable": True},
        )()
        self.assertIsNone(entity_base.slot_entity_category(water_pump))
        self.assertFalse(entity_base.slot_entity_hidden_by_default(water_pump))

        wake_up_chassis = type(
            "Meta",
            (),
            {"label": "wake_up_chassis", "deprecated": False, "wire_mode": "w", "is_writable": True},
        )()
        self.assertIsNone(entity_base.slot_entity_category(wake_up_chassis))

        entry = SimpleNamespace(options={})
        self.assertTrue(
            entity_base.slot_entity_disabled_by_default(battery_type, entry)
        )
        self.assertFalse(
            entity_base.slot_entity_disabled_by_default(wake_up_chassis, entry)
        )
        debug_entry = SimpleNamespace(
            options={"show_debug_diagnostics": True}
        )
        self.assertFalse(
            entity_base.slot_entity_disabled_by_default(battery_type, debug_entry)
        )
        self.assertFalse(
            preferences.admin_actions_enabled(SimpleNamespace(options={}))
        )
        self.assertTrue(
            preferences.admin_actions_enabled(
                SimpleNamespace(options={"show_admin_actions": True})
            )
        )

        self.assertEqual(preferences.display_value(10.0, "km", SimpleNamespace(options={})), 10.0)
        self.assertAlmostEqual(
            preferences.display_value(
                10.0,
                "km",
                SimpleNamespace(options={"use_miles": True}),
            ),
            6.21371192237334,
        )
        self.assertAlmostEqual(
            preferences.native_value_from_display(
                68.0,
                "°C",
                SimpleNamespace(options={"use_fahrenheit": True}),
            ),
            20.0,
        )
        self.assertEqual(
            entity_base._humanise("connected_btdevices"), "Connected BT Devices"
        )
        self.assertEqual(entity_base._humanise("dplus"), "D+ Signal")
        self.assertEqual(entity_base._humanise("d_plus_state"), "D+ Signal")
        self.assertEqual(entity_base._humanise("eblover_temperature"), "EBL Over Temperature")
        self.assertEqual(
            entity_base._humanise("outside_temp_calib_failure"),
            "Outside Temperature Calibration Failure",
        )
        self.assertEqual(
            entity_base._humanise("outside_temp_sensor_failure"),
            "Outside Temperature Sensor Failure",
        )
        self.assertEqual(
            entity_base._humanise("paired_btdevices"), "Paired BT Devices"
        )
        self.assertEqual(entity_base._humanise("solar_aes_active"), "Solar AES Active")

        integration = importlib.import_module("custom_components.hymer_connect_metadata.__init__")
        named_entry = SimpleNamespace(entry_id="entry-1", options={})
        self.assertEqual(
            integration._named_entity_policy_for_unique_id(
                named_entry, "entry-1_canonical_battery_soc"
            ),
            {"original_name": "Living Battery State Of Charge"},
        )
        self.assertEqual(
            integration._named_entity_policy_for_unique_id(
                named_entry, "entry-1_canonical_starter_battery_voltage"
            ),
            {"original_name": "Vehicle Battery Voltage"},
        )
        self.assertEqual(
            integration._named_entity_policy_for_unique_id(
                named_entry, "entry-1_canonical_lte_connection_state"
            ),
            {"original_name": "LTE Connection State"},
        )
        self.assertEqual(
            integration._named_entity_policy_for_unique_id(
                named_entry, "entry-1_canonical_solar_aes_active"
            ),
            {"original_name": "Solar AES Active"},
        )

    def test_schedule_coordinator_shutdown_uses_thread_safe_create_task(self) -> None:
        install_homeassistant_stubs()
        integration = importlib.import_module("custom_components.hymer_connect_metadata.__init__")

        captured: list[object] = []

        class Hass:
            def create_task(self, coro):
                captured.append(coro)
                coro.close()
                return None

        class Coordinator:
            async def async_prepare_for_shutdown(self):
                return None

        integration._schedule_coordinator_shutdown(Hass(), Coordinator())

        self.assertEqual(len(captured), 1)

    def test_opn_truthiness_is_door_specific(self) -> None:
        install_homeassistant_stubs()
        entity_base = importlib.import_module("custom_components.hymer_connect_metadata.entity_base")
        cls = entity_base.HymerBinarySensor

        door = cls.__new__(cls)
        door._attr_device_class = entity_base.BinarySensorDeviceClass.DOOR
        door._value = lambda: "OPN"
        self.assertTrue(door.is_on)

        generic = cls.__new__(cls)
        generic._attr_device_class = None
        generic._value = lambda: "OPN"
        self.assertIsNone(generic.is_on)

    def test_pending_setup_slots_trigger_reload_once_setup_completes(self) -> None:
        install_homeassistant_stubs()
        import sys

        sys.modules.pop("custom_components.hymer_connect_metadata.coordinator", None)
        ensure_package_paths()
        coordinator_mod = importlib.import_module("custom_components.hymer_connect_metadata.coordinator")
        cls = coordinator_mod.HymerConnectCoordinator

        coordinator = cls.__new__(cls)
        coordinator._slot_data = {(1, 1): 1}
        coordinator._slot_last_seen = {(1, 1): time.monotonic()}
        coordinator._entry_setup_complete = False
        coordinator._setup_slot_baseline = {(1, 1)}
        coordinator._pending_setup_slots = set()
        coordinator.data = {}
        reloaded: list[set[tuple[int, int]]] = []
        coordinator._schedule_capability_reload = lambda slots: reloaded.append(set(slots))
        coordinator.async_set_updated_data = lambda data: setattr(coordinator, "data", data)

        coordinator._on_signalr_update({(1, 1): 1, (1, 2): 2})
        self.assertEqual(coordinator._pending_setup_slots, {(1, 2)})

        coordinator.mark_entry_setup_complete()
        self.assertEqual(reloaded, [{(1, 2)}])

    def test_coordinator_tracks_active_and_stale_slots_separately(self) -> None:
        install_homeassistant_stubs()
        import sys

        sys.modules.pop("custom_components.hymer_connect_metadata.coordinator", None)
        ensure_package_paths()
        coordinator_mod = importlib.import_module("custom_components.hymer_connect_metadata.coordinator")
        cls = coordinator_mod.HymerConnectCoordinator

        coordinator = cls.__new__(cls)
        coordinator._slot_data = {(1, 1): 1, (1, 2): 2}
        coordinator._slot_last_seen = {
            (1, 1): time.monotonic(),
            (1, 2): time.monotonic() - (coordinator_mod._ACTIVE_SLOT_WINDOW_S + 1),
        }

        self.assertEqual(coordinator.active_slots, {(1, 1)})
        self.assertEqual(coordinator.stale_slots, {(1, 2)})

    def test_mark_slots_recent_refreshes_existing_slot_activity(self) -> None:
        install_homeassistant_stubs()
        import sys

        sys.modules.pop("custom_components.hymer_connect_metadata.coordinator", None)
        ensure_package_paths()
        coordinator_mod = importlib.import_module("custom_components.hymer_connect_metadata.coordinator")
        cls = coordinator_mod.HymerConnectCoordinator

        coordinator = cls.__new__(cls)
        stale = time.monotonic() - (coordinator_mod._ACTIVE_SLOT_WINDOW_S + 1)
        coordinator._slot_data = {(1, 1): 1, (1, 2): 2}
        coordinator._slot_last_seen = {(1, 1): stale, (1, 2): stale}

        coordinator.mark_slots_recent()

        self.assertEqual(coordinator.active_slots, {(1, 1), (1, 2)})
        self.assertEqual(coordinator.stale_slots, set())

    def test_async_update_data_marks_known_slots_recent_without_poll_refresh(self) -> None:
        install_homeassistant_stubs()
        import sys

        sys.modules.pop("custom_components.hymer_connect_metadata.coordinator", None)
        ensure_package_paths()
        coordinator_mod = importlib.import_module("custom_components.hymer_connect_metadata.coordinator")
        cls = coordinator_mod.HymerConnectCoordinator

        class SignalR:
            connected = True
            needs_reconnect = False

        coordinator = cls.__new__(cls)
        stale = time.monotonic() - (coordinator_mod._ACTIVE_SLOT_WINDOW_S + 1)
        coordinator._cached_rest_data = {"vehicle": {}}
        coordinator._last_rest_metadata_refresh = time.monotonic()
        coordinator._vehicle_urn = ""
        coordinator._scu_urn = ""
        coordinator._vehicle_id = None
        coordinator._vin = ""
        coordinator._slot_data = {(1, 1): 1}
        coordinator._slot_last_seen = {(1, 1): stale}
        coordinator._signalr = SignalR()
        coordinator._last_reconnect_attempt = 0.0
        coordinator._reconnect_backoff = coordinator_mod._INITIAL_BACKOFF
        coordinator.config_entry = type(
            "Entry",
            (),
            {"data": {}, "unique_id": "entry-1", "entry_id": "entry-1"},
        )()
        coordinator.api = type("Api", (), {})()
        coordinator.hass = type(
            "Hass",
            (),
            {"config_entries": type("Cfg", (), {"async_entries": lambda self, domain: []})()},
        )()

        import asyncio

        data = asyncio.run(coordinator._async_update_data())

        self.assertEqual(data["signalr_slots"], {(1, 1): 1})
        self.assertEqual(coordinator.active_slots, set())

    def test_coordinator_send_retries_once_after_failed_signalr_command(self) -> None:
        install_homeassistant_stubs()
        import sys

        sys.modules.pop("custom_components.hymer_connect_metadata.coordinator", None)
        ensure_package_paths()
        coordinator_mod = importlib.import_module("custom_components.hymer_connect_metadata.coordinator")
        cls = coordinator_mod.HymerConnectCoordinator

        class Client:
            def __init__(self, ok: bool) -> None:
                self._connected = True
                self.needs_reconnect = False
                self.ok = ok
                self.calls = 0

            @property
            def connected(self) -> bool:
                return self._connected

            def mark_disconnected(self) -> None:
                self._connected = False

            async def send_light_command(self, *args, **kwargs):
                del args, kwargs
                self.calls += 1
                return self.ok

        first = Client(False)
        second = Client(True)
        coordinator = cls.__new__(cls)
        coordinator._signalr = first
        coordinator.config_entry = type("Entry", (), {"title": "Test Van"})()

        async def start_signalr() -> None:
            coordinator._signalr = second

        coordinator.start_signalr = start_signalr

        import asyncio

        asyncio.run(
            coordinator.async_send_light_command(3, 1, bool_value=True)
        )

        self.assertEqual(first.calls, 1)
        self.assertEqual(second.calls, 1)

    def test_signalr_connection_lost_is_ignored_when_refresh_is_suppressed(self) -> None:
        install_homeassistant_stubs()
        import sys

        sys.modules.pop("custom_components.hymer_connect_metadata.coordinator", None)
        ensure_package_paths()
        coordinator_mod = importlib.import_module("custom_components.hymer_connect_metadata.coordinator")
        cls = coordinator_mod.HymerConnectCoordinator

        created: list[object] = []
        coordinator = cls.__new__(cls)
        coordinator._suppress_connection_lost_refresh = True
        coordinator._reconnect_task = None
        coordinator._reconnect_backoff = 123
        coordinator._last_reconnect_attempt = 456.0
        coordinator.config_entry = type("Entry", (), {"title": "Test Van"})()
        coordinator.hass = type(
            "Hass",
            (),
            {"async_create_task": lambda self, coro: created.append(coro)},
        )()

        coordinator._on_signalr_connection_lost()

        self.assertEqual(created, [])
        self.assertEqual(coordinator._reconnect_backoff, 123)
        self.assertEqual(coordinator._last_reconnect_attempt, 456.0)

    def test_stop_signalr_suppresses_refresh_callback_from_intentional_stop(self) -> None:
        install_homeassistant_stubs()
        import sys

        sys.modules.pop("custom_components.hymer_connect_metadata.coordinator", None)
        ensure_package_paths()
        coordinator_mod = importlib.import_module("custom_components.hymer_connect_metadata.coordinator")
        cls = coordinator_mod.HymerConnectCoordinator

        class SignalR:
            async def stop(self_nonlocal) -> None:
                coordinator._on_signalr_connection_lost()

        created: list[object] = []
        coordinator = cls.__new__(cls)
        coordinator._capability_reload_task = None
        coordinator._capability_reload_slots = set()
        coordinator._reconnect_task = None
        coordinator._suppress_connection_lost_refresh = False
        coordinator._signalr = SignalR()
        coordinator.config_entry = type("Entry", (), {"title": "Test Van"})()
        coordinator.hass = type(
            "Hass",
            (),
            {"async_create_task": lambda self, coro: created.append(coro)},
        )()

        import asyncio

        asyncio.run(coordinator.stop_signalr())

        self.assertEqual(created, [])
        self.assertTrue(coordinator._suppress_connection_lost_refresh)
        self.assertIsNone(coordinator._signalr)

    def test_signalr_connection_lost_is_ignored_during_shutdown(self) -> None:
        install_homeassistant_stubs()
        import sys

        sys.modules.pop("custom_components.hymer_connect_metadata.coordinator", None)
        ensure_package_paths()
        coordinator_mod = importlib.import_module("custom_components.hymer_connect_metadata.coordinator")
        cls = coordinator_mod.HymerConnectCoordinator

        created: list[object] = []
        coordinator = cls.__new__(cls)
        coordinator._shutting_down = True
        coordinator._suppress_connection_lost_refresh = False
        coordinator._reconnect_task = None
        coordinator.config_entry = type("Entry", (), {"title": "Test Van"})()
        coordinator.hass = type(
            "Hass",
            (),
            {"async_create_task": lambda self, coro: created.append(coro)},
        )()

        coordinator._on_signalr_connection_lost()

        self.assertEqual(created, [])

    def test_async_prepare_for_shutdown_cancels_background_work(self) -> None:
        install_homeassistant_stubs()
        import sys

        sys.modules.pop("custom_components.hymer_connect_metadata.coordinator", None)
        ensure_package_paths()
        coordinator_mod = importlib.import_module("custom_components.hymer_connect_metadata.coordinator")
        cls = coordinator_mod.HymerConnectCoordinator

        class SignalR:
            def __init__(self) -> None:
                self.stop_calls = 0

            async def stop(self_nonlocal) -> None:
                self_nonlocal.stop_calls += 1

        import asyncio

        async def run_test() -> None:
            signalr = SignalR()
            coordinator = cls.__new__(cls)
            coordinator._background_tasks = set()
            coordinator._capability_reload_task = None
            coordinator._capability_reload_slots = set()
            coordinator._reconnect_task = asyncio.create_task(asyncio.sleep(60))
            coordinator._suppress_connection_lost_refresh = False
            coordinator._shutting_down = False
            coordinator._signalr = signalr
            coordinator.config_entry = type("Entry", (), {"title": "Test Van"})()

            task = coordinator.track_background_task(asyncio.create_task(asyncio.sleep(60)))
            await coordinator.async_prepare_for_shutdown()

            self.assertTrue(coordinator._shutting_down)
            self.assertTrue(coordinator._suppress_connection_lost_refresh)
            self.assertTrue(task.cancelled())
            self.assertIsNone(coordinator._signalr)
            self.assertEqual(signalr.stop_calls, 1)
            self.assertIsNone(coordinator._reconnect_task)

        asyncio.run(run_test())

    def test_coordinator_habitation_power_state_uses_main_switch_slots(self) -> None:
        install_homeassistant_stubs()
        import sys

        sys.modules.pop("custom_components.hymer_connect_metadata.coordinator", None)
        ensure_package_paths()
        capability_resolver = importlib.import_module(
            "custom_components.hymer_connect_metadata.capability_resolver"
        )
        coordinator_mod = importlib.import_module("custom_components.hymer_connect_metadata.coordinator")
        cls = coordinator_mod.HymerConnectCoordinator
        slot = next(iter(capability_resolver.main_switch_slots()))

        coordinator = cls.__new__(cls)
        coordinator._slot_data = {slot: "Off"}
        coordinator.data = {"signalr_slots": {slot: "Off"}}
        self.assertFalse(coordinator.is_habitation_power_available())

        coordinator.data = {"signalr_slots": {slot: "On"}}
        self.assertTrue(coordinator.is_habitation_power_available())

        coordinator._slot_data = {}
        coordinator.data = {"signalr_slots": {}}
        self.assertTrue(coordinator.is_habitation_power_available())

    def test_coordinator_background_tasks_cancel_cleanly(self) -> None:
        install_homeassistant_stubs()
        import sys

        sys.modules.pop("custom_components.hymer_connect_metadata.coordinator", None)
        ensure_package_paths()
        coordinator_mod = importlib.import_module("custom_components.hymer_connect_metadata.coordinator")
        cls = coordinator_mod.HymerConnectCoordinator

        coordinator = cls.__new__(cls)
        coordinator._background_tasks = set()
        coordinator.config_entry = type("Entry", (), {"title": "Test Van"})()

        import asyncio

        async def run_test() -> None:
            task = coordinator.track_background_task(asyncio.create_task(asyncio.sleep(60)))
            await coordinator.async_cancel_background_tasks()
            self.assertTrue(task.cancelled())
            self.assertEqual(coordinator._background_tasks, set())

        asyncio.run(run_test())

    def test_extract_request_id_and_decode_transport_response(self) -> None:
        ensure_package_paths()
        pia_decoder = importlib.import_module("custom_components.hymer_connect_metadata.pia_decoder")

        request_payload = pia_decoder.build_light_command(12, 1, bool_value=True)
        self.assertIsInstance(
            pia_decoder.extract_request_id_from_payload(request_payload),
            int,
        )

        synthetic_slots = b"".join(
            [
                pia_decoder._encode_bytes_field(
                    1,
                    pia_decoder._encode_varint_field(1, 7)
                    + pia_decoder._encode_varint_field(2, 1)
                    + pia_decoder._encode_varint_field(3, 3557900),
                ),
                pia_decoder._encode_bytes_field(
                    2,
                    pia_decoder._encode_varint_field(1, 4)
                    + pia_decoder._encode_varint_field(2, 3)
                    + pia_decoder._encode_str_field(4, "Standby"),
                ),
            ]
        )
        response_payload = base64.b64encode(
            pia_decoder._encode_varint_field(1, 39747)
            + pia_decoder._encode_varint_field(2, pia_decoder.STATUS_SUCCESS)
            + pia_decoder._encode_varint_field(3, 1765109862)
            + pia_decoder._encode_bytes_field(5, synthetic_slots)
        ).decode("ascii")
        decoded = pia_decoder.decode_transport_response(response_payload)
        self.assertIsNotNone(decoded)
        self.assertEqual(decoded["request_id"], 39747)
        self.assertEqual(decoded["status"], pia_decoder.STATUS_SUCCESS)
        slots = pia_decoder.decode_pia_slots_bytes(decoded["payload"])
        self.assertEqual(slots[(1, 7)], 3557900)
        self.assertEqual(slots[(3, 4)], "Standby")

        compact_response_payload = base64.b64encode(
            pia_decoder._encode_varint_field(1, 604980)
            + pia_decoder._encode_varint_field(2, pia_decoder.STATUS_SUCCESS)
            + pia_decoder._encode_bytes_field(
                19,
                pia_decoder._encode_varint_field(3, 1765109862890),
            )
        ).decode("ascii")
        compact = pia_decoder.decode_transport_response(compact_response_payload)
        self.assertIsNotNone(compact)
        self.assertEqual(compact["request_id"], 604980)
        self.assertEqual(compact["status"], pia_decoder.STATUS_SUCCESS)
        self.assertEqual(
            pia_decoder._decode_protobuf(compact["payload"]),
            [(3, 0, 1765109862890)],
        )

    def test_signalr_stop_cancels_token_refresh_task(self) -> None:
        ensure_package_paths()
        signalr_mod = importlib.import_module("custom_components.hymer_connect_metadata.signalr_client")
        cls = signalr_mod.HymerSignalRClient

        import asyncio

        async def run_test() -> None:
            client = cls(
                api=object(),
                session=object(),
                vehicle_urn="vehicle",
                scu_urn="scu",
            )
            client._token_refresh_task = asyncio.create_task(asyncio.sleep(60))
            await client.stop()
            self.assertIsNone(client._token_refresh_task)

        asyncio.run(run_test())

    def test_restart_system_request_matches_app_command_shape(self) -> None:
        ensure_package_paths()
        pia_decoder = importlib.import_module("custom_components.hymer_connect_metadata.pia_decoder")

        payload = pia_decoder.build_restart_system_request(cold=True)
        request_id = pia_decoder.extract_request_id_from_payload(payload)
        self.assertIsInstance(request_id, int)
        self.assertGreater(request_id, 0)

        raw = base64.b64decode(payload)
        fields = pia_decoder._decode_protobuf(raw)
        self.assertEqual(
            [(fn, wt) for fn, wt, _ in fields[:4]],
            [(1, 0), (2, 2), (3, 0), (9, 2)],
        )
        version = next(value for fn, wt, value in fields if fn == 2 and wt == 2)
        self.assertEqual(version.decode("utf-8"), "v0.32.0")

        command = next(value for fn, wt, value in fields if fn == 9 and wt == 2)
        command_fields = pia_decoder._decode_protobuf(command)
        self.assertEqual([(fn, wt) for fn, wt, _ in command_fields], [(2, 2)])

        restart = command_fields[0][2]
        self.assertEqual(pia_decoder._decode_protobuf(restart), [(1, 0, 1)])

    def test_signalr_restart_system_command_uses_generic_request_envelope(self) -> None:
        ensure_package_paths()
        signalr_mod = importlib.import_module("custom_components.hymer_connect_metadata.signalr_client")
        pia_decoder = importlib.import_module("custom_components.hymer_connect_metadata.pia_decoder")
        cls = signalr_mod.HymerSignalRClient

        import asyncio

        async def run_test() -> None:
            client = cls(
                api=object(),
                session=object(),
                vehicle_urn="vehicle",
                scu_urn="scu",
            )
            seen: dict[str, str] = {}

            async def fake_send(payload: str) -> bool:
                seen["payload"] = payload
                return True

            client.send_pia_request = fake_send
            ok = await client.send_restart_system_command()
            self.assertTrue(ok)
            self.assertIn("payload", seen)
            raw = base64.b64decode(seen["payload"])
            fields = pia_decoder._decode_protobuf(raw)
            self.assertTrue(any(fn == 9 and wt == 2 for fn, wt, _ in fields))

        asyncio.run(run_test())

    def test_retry_waiting_requests_replays_requests_queued_during_drain(self) -> None:
        ensure_package_paths()
        signalr_mod = importlib.import_module("custom_components.hymer_connect_metadata.signalr_client")
        cls = signalr_mod.HymerSignalRClient
        pending_cls = signalr_mod._PendingPiaRequest

        import asyncio

        async def run_test() -> None:
            client = cls(
                api=object(),
                session=object(),
                vehicle_urn="vehicle",
                scu_urn="scu",
            )
            loop = asyncio.get_running_loop()
            client._pending_requests = {
                1: pending_cls("payload-1", loop.create_future(), sent=False),
                2: pending_cls("payload-2", loop.create_future(), sent=False),
            }
            client._waiting_request_ids = [1]
            sent: list[int] = []

            async def fake_send(request_id: int, payload: str) -> None:
                self.assertEqual(
                    payload,
                    client._pending_requests[request_id].payload,
                )
                sent.append(request_id)
                if request_id == 1 and 2 not in client._waiting_request_ids:
                    client._waiting_request_ids.append(2)
                await asyncio.sleep(0)

            client._send_request_payload = fake_send
            await client._retry_waiting_requests()

            self.assertEqual(sent, [1, 2])
            self.assertEqual(client._waiting_request_ids, [])

        asyncio.run(run_test())

    def test_generated_subscription_requests_are_transport_decodable(self) -> None:
        ensure_package_paths()
        pia_decoder = importlib.import_module("custom_components.hymer_connect_metadata.pia_decoder")

        requests = pia_decoder.build_subscription_requests()

        self.assertEqual(len(requests), 7)
        for payload in requests:
            request_id = pia_decoder.extract_request_id_from_payload(payload)
            self.assertIsInstance(request_id, int)
            self.assertGreater(request_id, 0)

        second_raw = base64.b64decode(requests[1])
        second_outer = pia_decoder._decode_protobuf(second_raw)
        second_inner = pia_decoder._decode_protobuf(second_outer[0][2])
        topic_payload = next(
            value for fn, wt, value in second_inner if fn == 4 and wt == 2
        )
        catalog = pia_decoder._decode_protobuf(topic_payload)
        self.assertEqual([(fn, wt) for fn, wt, _ in catalog], [(3, 2)])
        entries = pia_decoder._decode_protobuf(catalog[0][2])
        self.assertEqual(len(entries), 131)

    def test_initial_subscription_failure_raises(self) -> None:
        ensure_package_paths()
        signalr_mod = importlib.import_module("custom_components.hymer_connect_metadata.signalr_client")
        cls = signalr_mod.HymerSignalRClient

        import asyncio

        async def run_test() -> None:
            client = cls(
                api=object(),
                session=object(),
                vehicle_urn="vehicle",
                scu_urn="scu",
            )
            calls = 0

            async def fake_send(_: str) -> bool:
                nonlocal calls
                calls += 1
                return calls < 4

            client.send_pia_request = fake_send
            with self.assertRaises(signalr_mod.HymerConnectApiError):
                await client._send_initial_subscriptions()
            self.assertEqual(calls, 4)

        asyncio.run(run_test())

    def test_start_signalr_serializes_concurrent_starts(self) -> None:
        install_homeassistant_stubs()
        import sys
        import asyncio

        sys.modules.pop("custom_components.hymer_connect_metadata.coordinator", None)
        ensure_package_paths()
        coordinator_mod = importlib.import_module("custom_components.hymer_connect_metadata.coordinator")
        cls = coordinator_mod.HymerConnectCoordinator

        original_client_cls = coordinator_mod.HymerSignalRClient
        created: list[object] = []

        class FakeClient:
            def __init__(self, **kwargs) -> None:
                del kwargs
                created.append(self)
                self._connected = False

            @property
            def connected(self) -> bool:
                return self._connected

            async def start(self) -> None:
                await asyncio.sleep(0)
                self._connected = True

            async def stop(self) -> None:
                self._connected = False

        coordinator_mod.HymerSignalRClient = FakeClient
        try:
            coordinator = cls.__new__(cls)
            coordinator.api = object()
            coordinator._session = object()
            coordinator._vehicle_urn = "vehicle"
            coordinator._scu_urn = "scu"
            coordinator._ehg_refresh_token = ""
            coordinator._signalr = None
            coordinator._reconnect_backoff = 1
            coordinator._consecutive_failures = 0
            coordinator._capability_reload_task = None
            coordinator._capability_reload_slots = set()
            coordinator._reconnect_task = None
            coordinator._suppress_connection_lost_refresh = False
            coordinator.config_entry = type("Entry", (), {"title": "Test Van"})()

            async def run_test() -> None:
                await asyncio.gather(
                    coordinator.start_signalr(),
                    coordinator.start_signalr(),
                )

            asyncio.run(run_test())

            self.assertEqual(len(created), 1)
            self.assertIs(coordinator._signalr, created[0])
            self.assertTrue(coordinator._signalr.connected)
        finally:
            coordinator_mod.HymerSignalRClient = original_client_cls

    def test_water_pump_switches_become_unavailable_when_12v_is_off(self) -> None:
        install_homeassistant_stubs()
        entity_base = importlib.import_module("custom_components.hymer_connect_metadata.entity_base")
        canonical = importlib.import_module("custom_components.hymer_connect_metadata.templates.canonical")

        raw = entity_base.HymerSwitch.__new__(entity_base.HymerSwitch)
        raw._meta = type("Meta", (), {"label": "water_pump"})()
        raw.coordinator = type(
            "Coordinator",
            (),
            {"is_habitation_power_available": lambda self: False},
        )()
        self.assertFalse(raw.available)

        switch = canonical.CanonicalSwitch.__new__(canonical.CanonicalSwitch)
        switch._capability = type(
            "Capability",
            (),
            {"spec": type("Spec", (), {"key": "water_pump"})()},
        )()
        switch.coordinator = type(
            "Coordinator",
            (),
            {"is_habitation_power_available": lambda self: False},
        )()
        self.assertFalse(switch.available)

    def test_write_only_switch_optimistic_state_expires(self) -> None:
        install_homeassistant_stubs()
        entity_base = importlib.import_module("custom_components.hymer_connect_metadata.entity_base")
        cls = entity_base.HymerSwitch

        switch = cls.__new__(cls)
        switch._meta = type("Meta", (), {"wire_mode": "w", "datatype": "bool"})()
        switch._bus = 107
        switch._sid = 1
        switch._optimistic = True
        switch._optimistic_set_at = time.monotonic() - (cls._OPTIMISTIC_TTL_S + 1)
        switch._raw_is_on = lambda: None

        self.assertIsNone(switch.is_on)
        self.assertIsNone(switch._optimistic)

    def test_rw_switch_optimistic_state_expires(self) -> None:
        install_homeassistant_stubs()
        entity_base = importlib.import_module("custom_components.hymer_connect_metadata.entity_base")
        cls = entity_base.HymerSwitch

        switch = cls.__new__(cls)
        switch._meta = type("Meta", (), {"wire_mode": "rw", "datatype": "bool"})()
        switch._bus = 98
        switch._sid = 10
        switch._optimistic = True
        switch._optimistic_set_at = time.monotonic() - (cls._OPTIMISTIC_TTL_S + 1)
        switch._raw_is_on = lambda: False

        self.assertFalse(switch.is_on)
        self.assertIsNone(switch._optimistic)

    def test_number_select_and_text_optimistic_state_expires(self) -> None:
        install_homeassistant_stubs()
        entity_base = importlib.import_module("custom_components.hymer_connect_metadata.entity_base")

        number = entity_base.HymerNumber.__new__(entity_base.HymerNumber)
        number._bus = 6
        number._sid = 8
        number._optimistic = 22.0
        number._optimistic_set_at = (
            time.monotonic() - (entity_base.HymerNumber._OPTIMISTIC_TTL_S + 1)
        )
        number._value = lambda: None
        self.assertIsNone(number.native_value)
        self.assertIsNone(number._optimistic)

        select = entity_base.HymerSelect.__new__(entity_base.HymerSelect)
        select._bus = 6
        select._sid = 4
        select._optimistic = "Diesel"
        select._optimistic_set_at = (
            time.monotonic() - (entity_base.HymerSelect._OPTIMISTIC_TTL_S + 1)
        )
        select._attr_options = ["Diesel"]
        select._value = lambda: None
        self.assertIsNone(select.current_option)
        self.assertIsNone(select._optimistic)

        text = entity_base.HymerText.__new__(entity_base.HymerText)
        text._bus = 58
        text._sid = 1
        text._optimistic = "07:30"
        text._optimistic_set_at = (
            time.monotonic() - (entity_base.HymerText._OPTIMISTIC_TTL_S + 1)
        )
        text._value = lambda: None
        self.assertIsNone(text.native_value)
        self.assertIsNone(text._optimistic)

    def test_canonical_switch_optimistic_state_expires(self) -> None:
        install_homeassistant_stubs()
        canonical = importlib.import_module("custom_components.hymer_connect_metadata.templates.canonical")

        switch = canonical.CanonicalSwitch.__new__(canonical.CanonicalSwitch)
        switch._capability = type(
            "Capability",
            (),
            {"spec": type("Spec", (), {"key": "water_pump"})()},
        )()
        switch._optimistic = True
        switch._optimistic_set_at = (
            time.monotonic() - (canonical.CanonicalSwitch._OPTIMISTIC_TTL_S + 1)
        )
        switch._raw_is_on = lambda: None

        self.assertIsNone(switch.is_on)
        self.assertIsNone(switch._optimistic)

    def test_canonical_entity_rebinds_to_higher_preference_provider(self) -> None:
        install_homeassistant_stubs()
        canonical = importlib.import_module("custom_components.hymer_connect_metadata.templates.canonical")
        capability_resolver = importlib.import_module(
            "custom_components.hymer_connect_metadata.capability_resolver"
        )

        coordinator = type(
            "Coordinator",
            (),
            {
                "observed_slots": {(2, 8)},
                "active_slots": {(2, 8)},
                "data": {"signalr_slots": {(2, 8): 55}},
            },
        )()
        entry = type(
            "Entry",
            (),
            {"entry_id": "entry-1", "data": {}, "title": "Test Van"},
        )()
        capability = capability_resolver.resolved_capabilities({(2, 8)}, "sensor")[0]
        entity = canonical.CanonicalSensor(coordinator, entry, capability)

        self.assertEqual(entity.native_value, 55)
        self.assertEqual(
            entity.extra_state_attributes["provider_component_id"],
            2,
        )

        coordinator.observed_slots = {(2, 8), (3, 8)}
        coordinator.active_slots = {(2, 8), (3, 8)}
        coordinator.data = {"signalr_slots": {(2, 8): 55, (3, 8): 61}}

        self.assertEqual(entity.native_value, 61)
        self.assertEqual(
            entity.extra_state_attributes["provider_component_id"],
            3,
        )

    def test_canonical_entity_prefers_active_alternate_over_stale_preferred(self) -> None:
        install_homeassistant_stubs()
        canonical = importlib.import_module("custom_components.hymer_connect_metadata.templates.canonical")
        capability_resolver = importlib.import_module(
            "custom_components.hymer_connect_metadata.capability_resolver"
        )

        coordinator = type(
            "Coordinator",
            (),
            {
                "observed_slots": {(2, 8), (3, 8)},
                "active_slots": {(3, 8)},
                "data": {"signalr_slots": {(2, 8): 55, (3, 8): 61}},
            },
        )()
        entry = type(
            "Entry",
            (),
            {"entry_id": "entry-1", "data": {}, "title": "Test Van"},
        )()
        capability = capability_resolver.resolved_capabilities({(2, 8), (3, 8)}, "sensor")[0]
        entity = canonical.CanonicalSensor(coordinator, entry, capability)

        self.assertEqual(entity.native_value, 61)
        attrs = entity.extra_state_attributes
        self.assertEqual(attrs["provider_component_id"], 3)
        self.assertEqual(attrs["active_alternate_provider_slots"], [])
        self.assertEqual(attrs["alternate_provider_slots"], [[2, 8]])

    def test_coordinator_refreshes_platforms_for_pending_setup_slots(self) -> None:
        install_homeassistant_stubs()
        import sys

        sys.modules.pop("custom_components.hymer_connect_metadata.coordinator", None)
        ensure_package_paths()
        coordinator_mod = importlib.import_module("custom_components.hymer_connect_metadata.coordinator")
        cls = coordinator_mod.HymerConnectCoordinator

        coordinator = cls.__new__(cls)
        coordinator._slot_data = {(1, 1): 1}
        coordinator._slot_last_seen = {(1, 1): time.monotonic()}
        coordinator._entry_setup_complete = False
        coordinator._setup_slot_baseline = {(1, 1)}
        coordinator._pending_setup_slots = set()
        coordinator._capability_reload_slots = set()
        coordinator._capability_reload_task = None
        coordinator._platform_refresh_callbacks = {}
        coordinator._platform_discovery_profile = {}
        coordinator.data = {}
        refreshed: list[set[tuple[int, int]]] = []
        coordinator._schedule_capability_reload = lambda slots: refreshed.append(set(slots))
        coordinator.async_set_updated_data = lambda data: setattr(coordinator, "data", data)

        coordinator._on_signalr_update({(1, 1): 1, (1, 2): 2})
        self.assertEqual(coordinator._pending_setup_slots, {(1, 2)})

        coordinator.mark_entry_setup_complete()
        self.assertEqual(refreshed, [{(1, 2)}])

    def test_platform_setup_records_template_and_generic_profile(self) -> None:
        install_homeassistant_stubs()
        import sys

        sys.modules.pop("custom_components.hymer_connect_metadata.platform_setup", None)
        ensure_package_paths()
        platform_setup = importlib.import_module("custom_components.hymer_connect_metadata.platform_setup")
        const = importlib.import_module("custom_components.hymer_connect_metadata.const")
        entity_base = importlib.import_module("custom_components.hymer_connect_metadata.entity_base")

        class FakeTemplate:
            PLATFORM = "sensor"

            def build(self, coordinator, entry, observed):
                entity = type("TemplateEntity", (), {"_attr_unique_id": f"{entry.entry_id}_template"})()
                return ([entity], {(1, 1)})

        original_templates_for_platform = platform_setup.templates_for_platform
        platform_setup.templates_for_platform = (
            lambda platform: [FakeTemplate()] if platform == "sensor" else []
        )
        try:
            class Coordinator:
                def __init__(self) -> None:
                    self.observed_slots = {(1, 1), (1, 5)}
                    self.data = {"signalr_slots": {(1, 1): 1000, (1, 5): 50}}
                    self._platform_discovery_profile = {}
                    self.refresh_callbacks = {}

                async def wait_for_first_frame(self, timeout=30.0):
                    return True

                def set_platform_discovery_profile(self, platform, profile):
                    self._platform_discovery_profile[platform] = profile

                def register_platform_refresh(self, platform, callback):
                    self.refresh_callbacks[platform] = callback

            coordinator = Coordinator()
            entry = type("Entry", (), {"entry_id": "entry-1", "title": "Test Van", "data": {}})()
            hass = type("Hass", (), {"data": {const.DOMAIN: {entry.entry_id: coordinator}}})()
            collected = []

            async def run() -> None:
                await platform_setup.setup_platform(
                    hass,
                    entry,
                    lambda entities: collected.extend(entities),
                    "sensor",
                )

            import asyncio

            asyncio.run(run())

            self.assertEqual(len(collected), 2)
            self.assertTrue(any(isinstance(entity, entity_base.HymerSensor) for entity in collected))
            profile = coordinator._platform_discovery_profile["sensor"]
            self.assertEqual(profile["generic_entity_count"], 1)
            self.assertEqual(profile["claimed_slot_count"], 1)
            self.assertEqual(profile["template_summaries"][0]["claimed_slots"], [[1, 1]])
            self.assertIn("sensor", coordinator.refresh_callbacks)
        finally:
            platform_setup.templates_for_platform = original_templates_for_platform

    def test_platform_setup_keeps_diagnostic_raw_slots_visible(self) -> None:
        install_homeassistant_stubs()
        import sys

        sys.modules.pop("custom_components.hymer_connect_metadata.platform_setup", None)
        ensure_package_paths()
        discovery = importlib.import_module("custom_components.hymer_connect_metadata.discovery")
        entity_base = importlib.import_module("custom_components.hymer_connect_metadata.entity_base")
        platform_setup = importlib.import_module("custom_components.hymer_connect_metadata.platform_setup")

        hidden_meta = discovery.SlotMeta(
            bus_id=30,
            sensor_id=9,
            label="user_active",
            unit=None,
            datatype="bool",
            mode="rw",
            wire_mode="rw",
        )
        original_all_slots = platform_setup.all_slots
        original_component_meta = platform_setup.component_meta
        original_templates_for_platform = platform_setup.templates_for_platform
        platform_setup.all_slots = lambda: {(30, 9): hidden_meta}
        platform_setup.component_meta = lambda _bus_id: None
        platform_setup.templates_for_platform = lambda _platform: []
        try:
            coordinator = type(
                "Coordinator",
                (),
                {"data": {"signalr_slots": {(30, 9): True}}},
            )()
            entry = type("Entry", (), {"entry_id": "entry-1"})()

            entities, profile = platform_setup._discover_platform_entities(
                coordinator,
                entry,
                "switch",
                {(30, 9)},
            )

            self.assertEqual(len(entities), 1)
            self.assertEqual(profile["skipped_hidden_raw_slot"], 0)
            self.assertEqual(profile["generic_entity_count"], 1)
            self.assertEqual(
                getattr(entities[0], "_attr_entity_category", None),
                entity_base.EntityCategory.DIAGNOSTIC,
            )
        finally:
            platform_setup.all_slots = original_all_slots
            platform_setup.component_meta = original_component_meta
            platform_setup.templates_for_platform = original_templates_for_platform

    def test_platform_refresh_adds_new_entities_without_entry_reload(self) -> None:
        install_homeassistant_stubs()
        import sys

        sys.modules.pop("custom_components.hymer_connect_metadata.platform_setup", None)
        ensure_package_paths()
        platform_setup = importlib.import_module("custom_components.hymer_connect_metadata.platform_setup")
        const = importlib.import_module("custom_components.hymer_connect_metadata.const")
        entity_base = importlib.import_module("custom_components.hymer_connect_metadata.entity_base")

        class FakeTemplate:
            PLATFORM = "sensor"

            def build(self, coordinator, entry, observed):
                if (1, 1) not in observed:
                    return ([], set())
                entity = type("TemplateEntity", (), {"_attr_unique_id": f"{entry.entry_id}_template"})()
                return ([entity], {(1, 1)})

        original_templates_for_platform = platform_setup.templates_for_platform
        platform_setup.templates_for_platform = (
            lambda platform: [FakeTemplate()] if platform == "sensor" else []
        )
        try:
            class Coordinator:
                def __init__(self) -> None:
                    self.observed_slots = {(1, 1)}
                    self.data = {"signalr_slots": {(1, 1): 1000}}
                    self._platform_discovery_profile = {}
                    self.refresh_callbacks = {}

                async def wait_for_first_frame(self, timeout=30.0):
                    return True

                def set_platform_discovery_profile(self, platform, profile):
                    self._platform_discovery_profile[platform] = profile

                def register_platform_refresh(self, platform, callback):
                    self.refresh_callbacks[platform] = callback

            coordinator = Coordinator()
            entry = type("Entry", (), {"entry_id": "entry-1", "title": "Test Van", "data": {}})()
            hass = type("Hass", (), {"data": {const.DOMAIN: {entry.entry_id: coordinator}}})()
            collected = []

            async def run() -> None:
                await platform_setup.setup_platform(
                    hass,
                    entry,
                    lambda entities: collected.extend(entities),
                    "sensor",
                )
                coordinator.observed_slots = {(1, 1), (1, 5)}
                coordinator.data = {"signalr_slots": {(1, 1): 1000, (1, 5): 50}}
                await coordinator.refresh_callbacks["sensor"]({(1, 5)})

            import asyncio

            asyncio.run(run())

            self.assertEqual(len(collected), 2)
            self.assertTrue(any(isinstance(entity, entity_base.HymerSensor) for entity in collected))
            profile = coordinator._platform_discovery_profile["sensor"]
            self.assertEqual(profile["entity_count"], 2)
            self.assertEqual(profile["generic_entity_count"], 1)
        finally:
            platform_setup.templates_for_platform = original_templates_for_platform

    def test_platform_refresh_skips_irrelevant_new_slots(self) -> None:
        install_homeassistant_stubs()
        import sys

        sys.modules.pop("custom_components.hymer_connect_metadata.platform_setup", None)
        ensure_package_paths()
        platform_setup = importlib.import_module("custom_components.hymer_connect_metadata.platform_setup")
        const = importlib.import_module("custom_components.hymer_connect_metadata.const")

        original_discover = platform_setup._discover_platform_entities

        calls: list[set[tuple[int, int]]] = []

        def fake_discover(coordinator, entry, platform, observed):
            calls.append(set(observed))
            return ([], {"entity_count": 0, "generic_entity_count": 0, "template_summaries": []})

        platform_setup._discover_platform_entities = fake_discover
        try:
            class Coordinator:
                def __init__(self) -> None:
                    self.observed_slots = {(3, 8)}
                    self.data = {"signalr_slots": {(3, 8): 42}}
                    self._platform_discovery_profile = {}
                    self.refresh_callbacks = {}

                async def wait_for_first_frame(self, timeout=30.0):
                    return True

                def set_platform_discovery_profile(self, platform, profile):
                    self._platform_discovery_profile[platform] = profile

                def register_platform_refresh(self, platform, callback):
                    self.refresh_callbacks[platform] = callback

            coordinator = Coordinator()
            entry = type("Entry", (), {"entry_id": "entry-1", "title": "Test Van", "data": {}})()
            hass = type("Hass", (), {"data": {const.DOMAIN: {entry.entry_id: coordinator}}})()

            async def run() -> None:
                await platform_setup.setup_platform(
                    hass,
                    entry,
                    lambda entities: None,
                    "sensor",
                )
                self.assertEqual(len(calls), 1)
                await coordinator.refresh_callbacks["sensor"]({(107, 1)})

            import asyncio

            asyncio.run(run())

            self.assertEqual(len(calls), 1)
        finally:
            platform_setup._discover_platform_entities = original_discover

    def test_platform_setup_keeps_light_brightness_numbers_but_skips_switch_duplicates(self) -> None:
        install_homeassistant_stubs()
        import sys

        sys.modules.pop("custom_components.hymer_connect_metadata.platform_setup", None)
        ensure_package_paths()
        platform_setup = importlib.import_module("custom_components.hymer_connect_metadata.platform_setup")
        const = importlib.import_module("custom_components.hymer_connect_metadata.const")
        entity_base = importlib.import_module("custom_components.hymer_connect_metadata.entity_base")

        class Coordinator:
            def __init__(self) -> None:
                self.observed_slots = {(12, 1), (12, 2), (12, 3)}
                self.data = {"signalr_slots": {(12, 1): False, (12, 2): 50, (12, 3): 25}}
                self._platform_discovery_profile = {}
                self.refresh_callbacks = {}

            async def wait_for_first_frame(self, timeout=30.0):
                return True

            def set_platform_discovery_profile(self, platform, profile):
                self._platform_discovery_profile[platform] = profile

            def register_platform_refresh(self, platform, callback):
                self.refresh_callbacks[platform] = callback

        entry = type("Entry", (), {"entry_id": "entry-1", "title": "Test Van", "data": {}})()

        async def run(platform: str):
            coordinator = Coordinator()
            hass = type("Hass", (), {"data": {const.DOMAIN: {entry.entry_id: coordinator}}})()
            collected = []
            await platform_setup.setup_platform(
                hass,
                entry,
                lambda entities: collected.extend(entities),
                platform,
            )
            return collected, coordinator._platform_discovery_profile[platform]

        import asyncio

        switch_entities, switch_profile = asyncio.run(run("switch"))
        number_entities, number_profile = asyncio.run(run("number"))

        self.assertEqual(switch_entities, [])
        self.assertEqual(len(number_entities), 1)
        self.assertIsInstance(number_entities[0], entity_base.HymerNumber)
        self.assertEqual(switch_profile["generic_entity_count"], 0)
        self.assertEqual(number_profile["generic_entity_count"], 1)
        self.assertEqual(switch_profile["skipped_rich_template_claim"], 1)
        self.assertEqual(number_profile["skipped_rich_template_claim"], 1)

    def test_light_brightness_generic_name_uses_component_name(self) -> None:
        install_homeassistant_stubs()
        entity_base = importlib.import_module("custom_components.hymer_connect_metadata.entity_base")
        discovery = importlib.import_module("custom_components.hymer_connect_metadata.discovery")

        component = discovery.component_meta(12)
        meta = discovery.slot_meta(12, 2)
        entity = entity_base.HymerNumber(
            coordinator=type("Coordinator", (), {"data": {"signalr_slots": {(12, 2): 42}}})(),
            entry=type("Entry", (), {"entry_id": "entry-1"})(),
            meta=meta,
            component=component,
        )

        self.assertEqual(entity._attr_name, "Lighting Zone Brightness")

    def test_button_press_uses_slot_action_validation(self) -> None:
        install_homeassistant_stubs()
        entity_base = importlib.import_module("custom_components.hymer_connect_metadata.entity_base")
        sent: list[list[dict[str, object]]] = []

        class Client:
            async def send_multi_sensor_command(self, sensors):
                sent.append(list(sensors))

        async def ensure_client():
            return Client()

        button = entity_base.HymerButton.__new__(entity_base.HymerButton)
        button._meta = type("Meta", (), {"datatype": "bool"})()
        button._bus = 107
        button._sid = 1
        button._ensure_client = ensure_client

        import asyncio

        asyncio.run(button.async_press())
        self.assertEqual(
            sent,
            [[{"bus_id": 107, "sensor_id": 1, "bool_value": True}]],
        )

        invalid = entity_base.HymerButton.__new__(entity_base.HymerButton)
        invalid._meta = type("Meta", (), {"datatype": "bool"})()
        invalid._bus = 3
        invalid._sid = 8
        invalid._ensure_client = ensure_client

        with self.assertRaises(entity_base.HomeAssistantError):
            asyncio.run(invalid.async_press())

    def test_button_platform_registers_restart_button_before_generic_setup(self) -> None:
        install_homeassistant_stubs()
        button_platform = importlib.import_module("custom_components.hymer_connect_metadata.button")
        entity_base = importlib.import_module("custom_components.hymer_connect_metadata.entity_base")

        class Coordinator:
            async def async_send_restart_system_command(self, *, cold: bool = True) -> bool:
                return cold

        class Entry:
            entry_id = "entry-1"
            title = "HYMER Connect Metadata (HYMER)"
            data = {"vehicle_model": "Smart Interface Unit"}
            options = {}

        class Hass:
            data = {"hymer_connect_metadata": {"entry-1": Coordinator()}}

        setup_calls: list[int] = []
        added_batches: list[list[object]] = []

        async def fake_setup_platform(hass, entry, async_add_entities, platform):
            self.assertEqual(platform, "button")
            setup_calls.append(len(added_batches))

        def add_entities(entities):
            added_batches.append(list(entities))

        import asyncio

        with mock.patch.object(button_platform, "setup_platform", fake_setup_platform):
            asyncio.run(button_platform.async_setup_entry(Hass(), Entry(), add_entities))

        self.assertEqual(setup_calls, [1])
        self.assertEqual(len(added_batches), 1)
        self.assertEqual(
            [getattr(entity, "_attr_unique_id", None) for entity in added_batches[0]],
            ["entry-1_restart_system"],
        )
        restart_button = added_batches[0][0]
        self.assertEqual(
            getattr(restart_button, "_attr_entity_category", None),
            entity_base.EntityCategory.CONFIG,
        )
        self.assertFalse(
            getattr(restart_button, "_attr_entity_registry_enabled_default", True)
        )

    def test_named_entity_policy_gates_restart_button_behind_admin_actions(self) -> None:
        install_homeassistant_stubs()
        init_mod = importlib.import_module("custom_components.hymer_connect_metadata.__init__")

        disabled = init_mod._named_entity_policy_for_unique_id(
            SimpleNamespace(entry_id="entry-1", options={}),
            "entry-1_restart_system",
        )
        self.assertEqual(disabled["original_name"], "Restart System")
        self.assertEqual(disabled["entity_category"], init_mod.EntityCategory.CONFIG)
        self.assertEqual(
            disabled["disabled_by"],
            init_mod.er.RegistryEntryDisabler.INTEGRATION,
        )

        enabled = init_mod._named_entity_policy_for_unique_id(
            SimpleNamespace(entry_id="entry-1", options={"show_admin_actions": True}),
            "entry-1_restart_system",
        )
        self.assertIsNone(enabled["disabled_by"])

    def test_runtime_metadata_preflight_reports_missing_local_pack(self) -> None:
        ensure_package_paths()
        runtime_metadata = importlib.import_module(
            "custom_components.hymer_connect_metadata.runtime_metadata"
        )

        with TemporaryDirectory() as data_dir, TemporaryDirectory() as specs_dir:
            data_path = Path(data_dir)
            specs_path = Path(specs_dir)
            (specs_path / "provider_specs.json").write_text("{}")
            (specs_path / "template_specs.json").write_text("{}")

            with mock.patch.object(runtime_metadata, "DATA_DIR", data_path), mock.patch.object(
                runtime_metadata, "SPECS_DIR", specs_path
            ):
                missing = runtime_metadata.missing_runtime_data_files()
                self.assertIn("sensor_labels.json", missing)
                with self.assertRaises(runtime_metadata.RuntimeMetadataMissingError) as ctx:
                    runtime_metadata.ensure_runtime_metadata_present()
                self.assertIn(
                    "python3 scripts/prepare_runtime_metadata.py --apk-url <apk-url> --zip-out hymer_connect_metadata_runtime_metadata.zip",
                    str(ctx.exception),
                )
                self.assertIn("data/sensor_labels.json", ctx.exception.missing_files)
                self.assertIn("data/oauth_client.json", ctx.exception.missing_files)

    def test_runtime_metadata_loads_local_oauth_header(self) -> None:
        ensure_package_paths()
        runtime_metadata = importlib.import_module(
            "custom_components.hymer_connect_metadata.runtime_metadata"
        )

        header = runtime_metadata.load_oauth_basic_auth_header()

        self.assertEqual(
            header,
            "Basic dGVzdC1jbGllbnQ6c3ludGhldGljLXNlY3JldA==",
        )

    def test_missing_runtime_metadata_repair_issue_uses_prepare_command(self) -> None:
        install_homeassistant_stubs()
        repairs = importlib.import_module("custom_components.hymer_connect_metadata.repairs")
        issue_registry = importlib.import_module("homeassistant.helpers.issue_registry")

        issue_registry.created_issues.clear()
        issue_registry.deleted_issues.clear()

        hass = object()
        repairs.async_create_missing_runtime_metadata_issue(
            hass,
            ("data/sensor_labels.json", "data/component_kinds.json"),
            "python3 scripts/prepare_runtime_metadata.py --apk-url <apk-url> --ha-config-dir /config",
        )
        repairs.async_delete_missing_runtime_metadata_issue(hass)

        self.assertEqual(len(issue_registry.created_issues), 1)
        created = issue_registry.created_issues[0]
        self.assertEqual(created["issue_id"], "missing_runtime_metadata")
        self.assertEqual(created["translation_key"], "missing_runtime_metadata")
        self.assertEqual(
            created["translation_placeholders"]["missing_files"],
            "data/sensor_labels.json, data/component_kinds.json",
        )
        self.assertIn(
            "--ha-config-dir /config",
            created["translation_placeholders"]["prepare_command"],
        )
        self.assertEqual(
            issue_registry.deleted_issues,
            [
                {
                    "hass": hass,
                    "domain": "hymer_connect_metadata",
                    "issue_id": "missing_runtime_metadata",
                }
            ],
        )

    def test_generate_overlay_from_expanded_bundle_snippet(self) -> None:
        from scripts.generate_cleanroom_registry import generate_overlay_from_bundle

        bundle_text = "\n".join(
            [
                "r1 = {'componentId': 1, 'id': 1, 'name': 'Mileage', 'mode': 'r', 'datatype': 'int', 'unit': 'm'};",
                "r2 = {'min': 0, 'max': 2147483647, 'resolution': 5};",
                "r1['range'] = r2;",
                "r3 = {'id': 1, 'name': 'VehicleSignal', 'capabilities': null, 'settings': null};",
                "r4 = {'componentId': 24, 'id': 1, 'name': 'On', 'mode': 'rw', 'datatype': 'bool'};",
                "r5 = {'componentId': 24, 'id': 2, 'name': 'Brightness', 'mode': 'rw', 'datatype': 'int', 'unit': '%'};",
                "r6 = {'id': 24, 'name': 'LightGroup01', 'capabilities': null, 'settings': null};",
                "r7 = {'LIGHT_GROUP_1': 'Communal'};",
                "r8 = {'id': 1, 'version': 0, 'name': 'SCENARIOS.ARRIVAL.TITLE', 'description': 'SCENARIOS.ARRIVAL.DESCRIPTION', 'icon': 'arrival'};",
                "r9 = new Array(1);",
                "r10 = {'componentId': 1, 'valueId': 1, 'value': true};",
                "r9[0] = r10;",
                "r8['components'] = r9;",
                "r11 = {'Integrated': 0, 0: 'Integrated'};",
                "r12 = new Array(1);",
                "r13 = {'key': 'HY_TEST_VAN', 'modelName': 'Test Van', 'group': 0};",
                "r12[0] = r13;",
                "r14 = {};",
                "r14['VehicleGroupVariant'] = r11;",
                "r14['VEHICLES'] = r12;",
            ]
        )

        with TemporaryDirectory() as tmpdir:
            bundle_path = Path(tmpdir) / "bundle.js"
            bundle_path.write_text(bundle_text)
            outputs = generate_overlay_from_bundle(
                bundle_path,
                Path("custom_components/hymer_connect_metadata/pia_decoder.py"),
                Path("custom_components/hymer_connect_metadata/specs/provider_specs.json"),
                Path("custom_components/hymer_connect_metadata/specs/template_specs.json"),
            )

        generated_components = outputs[0]
        generated_slots = outputs[1]
        generated_vehicles = outputs[3]
        generated_scenarios = outputs[4]

        self.assertEqual(
            generated_components["components"]["1"]["name"],
            "Chassis Signals",
        )
        self.assertEqual(
            generated_components["components"]["24"]["name"],
            "Communal",
        )
        self.assertEqual(
            generated_components["components"]["24"]["source_name"],
            "LightGroup01",
        )
        self.assertEqual(generated_slots["slots"]["1:1"]["label"], "odometer")
        self.assertEqual(generated_vehicles["models"]["HY_TEST_VAN"]["group"], "Integrated")
        self.assertEqual(generated_scenarios["entries"][0]["key"], "ARRIVAL")

    def test_prepare_runtime_metadata_extracts_local_oauth_client_header(self) -> None:
        from scripts import prepare_runtime_metadata

        username = "test-client"
        password = "synthetic-secret"
        bundle_text = (
            "{'CLIENT_USERNAME': '"
            + base64.b64encode(username.encode("utf-8")).decode("ascii")
            + "', 'CLIENT_PASSWORD': '"
            + base64.b64encode(password.encode("utf-8")).decode("ascii")
            + "'}"
        )

        with TemporaryDirectory() as tmpdir:
            bundle_path = Path(tmpdir) / "bundle.js"
            bundle_path.write_text(bundle_text)
            payload = prepare_runtime_metadata._extract_oauth_client_payload(bundle_path)

        self.assertEqual(
            payload["authorization_header"],
            "Basic "
            + base64.b64encode(f"{username}:{password}".encode("utf-8")).decode(
                "ascii"
            ),
        )

    def test_prepare_runtime_metadata_resolves_home_assistant_config_target(self) -> None:
        from scripts import prepare_runtime_metadata

        with TemporaryDirectory() as tmpdir:
            config_dir = Path(tmpdir)
            args = prepare_runtime_metadata.build_parser().parse_args(
                ["--bundle-js", "bundle.js", "--ha-config-dir", str(config_dir)]
            )

            self.assertEqual(
                prepare_runtime_metadata._resolve_output_dir(args),
                config_dir.resolve()
                / "custom_components"
                / "hymer_connect_metadata"
                / "data",
            )

    def test_prepare_runtime_metadata_zip_preserves_home_assistant_layout(self) -> None:
        from scripts import prepare_runtime_metadata

        with TemporaryDirectory() as tmpdir:
            zip_path = Path(tmpdir) / "metadata.zip"
            prepare_runtime_metadata._write_zip(
                zip_path,
                {
                    "component_kinds.json": {"components": {}, "_comment": "test"},
                    "oauth_client.json": {
                        "authorization_header": "Basic dGVzdDp0ZXN0",
                        "_comment": "synthetic",
                    },
                },
            )

            with ZipFile(zip_path) as archive:
                self.assertEqual(
                    set(archive.namelist()),
                    {
                        "README.txt",
                        "custom_components/hymer_connect_metadata/data/component_kinds.json",
                        "custom_components/hymer_connect_metadata/data/oauth_client.json",
                    },
                )
                payload = json.loads(
                    archive.read(
                        "custom_components/hymer_connect_metadata/data/component_kinds.json"
                    ).decode("utf-8")
                )
                self.assertEqual(payload["components"], {})

    def test_config_flow_surfaces_missing_runtime_metadata_before_login(self) -> None:
        install_homeassistant_stubs()
        runtime_metadata = importlib.import_module(
            "custom_components.hymer_connect_metadata.runtime_metadata"
        )
        voluptuous = types.ModuleType("voluptuous")
        voluptuous.Schema = lambda value: value
        voluptuous.Required = lambda key, default=None: key
        voluptuous.Optional = lambda key, default=None: key
        voluptuous.In = lambda choices: choices
        with mock.patch.dict(sys.modules, {"voluptuous": voluptuous}):
            config_flow = importlib.import_module(
                "custom_components.hymer_connect_metadata.config_flow"
            )

            flow = config_flow.HymerConnectConfigFlow()
            flow.hass = object()

            async def _run() -> dict[str, object]:
                with mock.patch.object(
                    flow,
                    "_async_authenticate_api",
                    side_effect=runtime_metadata.RuntimeMetadataMissingError(
                        ("data/oauth_client.json",),
                        "python3 scripts/prepare_runtime_metadata.py --apk-url <apk-url>",
                    ),
                ):
                    return await flow.async_step_user(
                        {
                            "brand": "hymer",
                            "username": "user@example.com",
                            "password": "secret",
                        }
                    )

            result = asyncio.run(_run())

        self.assertEqual(result["step_id"], "user")
        self.assertEqual(result["errors"]["base"], "missing_runtime_metadata")

    def test_fixture_slot_metadata_promotes_only_matching_legacy_transforms(self) -> None:
        runtime_metadata = importlib.import_module(
            "custom_components.hymer_connect_metadata.runtime_metadata"
        )
        slots = json.loads(runtime_metadata.data_path("sensor_labels.json").read_text())["slots"]

        self.assertEqual(slots["1:1"]["label"], "odometer")
        self.assertEqual(slots["1:1"]["transform"], "div1000")
        self.assertEqual(slots["1:1"]["unit"], "km")

        self.assertEqual(slots["1:5"]["label"], "distance_to_service")
        self.assertNotIn("transform", slots["1:5"])

        self.assertEqual(slots["1:2"]["label"], "fuel_level")
        self.assertEqual(slots["1:2"]["transform"], "invert100")

        self.assertEqual(slots["1:7"]["label"], "adblue_remaining_distance")
        self.assertEqual(slots["1:7"]["transform"], "div1000")

        self.assertEqual(slots["34:3"]["control_platform"], "select")
        self.assertEqual(slots["34:3"]["options"], ["1", "2", "3", "4", "5"])

        self.assertEqual(slots["34:7"]["label"], "dcvoltage")
        self.assertNotIn("transform", slots["34:7"])


if __name__ == "__main__":
    unittest.main()
