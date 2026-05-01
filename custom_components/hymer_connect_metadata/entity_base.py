"""Generic entity bases that consume discovery metadata.

Every entity here takes a (bus_id, sensor_id) pair at construction and reads
its value from the coordinator's slot_data.  Platform setup picks the right
class based on the slot's datatype + mode.
"""

from __future__ import annotations

import logging
import time
from typing import Any

from homeassistant.components.binary_sensor import (
    BinarySensorDeviceClass,
    BinarySensorEntity,
)
from homeassistant.components.button import ButtonEntity
from homeassistant.components.number import NumberEntity
from homeassistant.components.select import SelectEntity
from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorStateClass,
)
from homeassistant.components.switch import SwitchDeviceClass, SwitchEntity
from homeassistant.components.text import TextEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.entity import EntityCategory
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import (
    CONF_VEHICLE_MODEL,
    CONF_VEHICLE_MODEL_GROUP,
    CONF_VEHICLE_NAME,
    CONF_VIN,
    DOMAIN,
    MANUFACTURER,
)
from .coordinator import HymerConnectCoordinator
from .discovery import ComponentMeta, SlotMeta, apply_transform, reverse_transform
from .preferences import (
    debug_diagnostics_enabled,
    display_unit,
    display_value,
    native_value_from_display,
    use_miles,
)
from .slot_actions import SlotActionError, serialize_slot_action

_LOGGER = logging.getLogger(__name__)

_DIAGNOSTIC_RAW_LABELS = {
    "activate_tank_refill_interval",
    "battery_type",
    "freezer_level",
    "heater_air_mode",
    "lighting_module_12v_supply",
    "lighting_module_all_off",
    "lighting_module_d_plus",
    "living_battery_capacity",
    "response_error",
    "test_signal_write",
    "update_tank_level_immediately",
    "user_active",
}


# -- Per-unit SensorDeviceClass mapping --------------------------------------

_UNIT_TO_DEVICE_CLASS: dict[str | None, SensorDeviceClass | None] = {
    "V": SensorDeviceClass.VOLTAGE,
    "A": SensorDeviceClass.CURRENT,
    "W": SensorDeviceClass.POWER,
    "kW": SensorDeviceClass.POWER,
    "mV": SensorDeviceClass.VOLTAGE,
    "Ah": None,  # no native device_class for Ah in HA
    "km": SensorDeviceClass.DISTANCE,
    "m": SensorDeviceClass.DISTANCE,
    "°C": SensorDeviceClass.TEMPERATURE,
    "°F": SensorDeviceClass.TEMPERATURE,
    "Hz": SensorDeviceClass.FREQUENCY,
    "bar": SensorDeviceClass.PRESSURE,
    "psi": SensorDeviceClass.PRESSURE,
    "%": None,  # ambiguous (battery, fuel, brightness); set per-label if needed
    "s": SensorDeviceClass.DURATION,
    "min": SensorDeviceClass.DURATION,
    "d": SensorDeviceClass.DURATION,
    "h": SensorDeviceClass.DURATION,
}

_HUMAN_LABEL_OVERRIDES: dict[str, str] = {
    "ad_blue_level": "AdBlue Level",
    "adblue_level": "AdBlue Level",
    "adblue_remaining_distance": "AdBlue Remaining Distance",
    "battery_keeper_active": "Starter Battery Maintainer Active",
    "battery_soc": "Battery SOC",
    "battery_switch_active": "Vehicle Battery Switch Active",
    "bms_state_of_health": "BMS State Of Health",
    "connected_btdevices": "Connected BT Devices",
    "d_plus_state": "D+ Signal",
    "dplus": "D+ Signal",
    "dplus_simulated": "D+ Simulated",
    "dplus_status": "D+ Status",
    "distance_to_service": "Distance to Service",
    "door_state_driver": "Driver Door State Code",
    "door_state_entrance_door": "Entrance Door State Code",
    "ebl_over_temperature": "EBL Over Temperature",
    "eblover_temperature": "EBL Over Temperature",
    "ebloutdoor_temp_sensor": "EBL Outdoor Temperature",
    "gps_location": "GPS Location",
    "gps_coordinates": "GPS Coordinates",
    "ignition_status": "Ignition Status Code",
    "language_setting": "Language Setting Code",
    "lighting_module_12v_supply": "Lighting Module 12V Supply",
    "lte_connection_quality": "LTE Connection Quality",
    "lte_connection_state": "LTE Connection State",
    "outside_temp_calib_failure": "Outside Temperature Calibration Failure",
    "outside_temp_sensor_failure": "Outside Temperature Sensor Failure",
    "paired_btdevices": "Paired BT Devices",
    "scu_internal_time": "SCU Internal Time",
    "scu_voltage": "SCU Voltage",
    "solar_aes_active": "Solar AES Active",
    "standheizung": "Parking Heater",
    "standheizung_available": "Parking Heater Available",
    "vehicle_brand": "Vehicle Brand Code",
    "vehicle_type": "Vehicle Type Code",
    "vin": "VIN",
}

_HUMAN_WORD_OVERRIDES: dict[str, str] = {
    "adblue": "AdBlue",
    "aes": "AES",
    "bms": "BMS",
    "can": "CAN",
    "dpf": "DPF",
    "ebl": "EBL",
    "ehg": "EHG",
    "gps": "GPS",
    "lte": "LTE",
    "rpm": "RPM",
    "btdevices": "BT Devices",
    "calib": "Calibration",
    "scu": "SCU",
    "soc": "SOC",
    "vin": "VIN",
}


def _label_words(label: str) -> set[str]:
    return {word for word in label.split("_") if word}


def _default_device_class(meta: SlotMeta, entry: Any | None = None) -> SensorDeviceClass | None:
    # battery_soc, bms_state_of_health etc. — use BATTERY
    if meta.unit == "%" and "soc" in meta.label:
        return SensorDeviceClass.BATTERY
    if meta.unit == "km" and entry is not None and use_miles(entry):
        return None
    return _UNIT_TO_DEVICE_CLASS.get(meta.unit)


def _binary_sensor_device_class_for_label(label: str):
    words = _label_words(label)
    if "door" in words:
        return BinarySensorDeviceClass.DOOR
    if "lock" in words or "locking" in words:
        return BinarySensorDeviceClass.LOCK
    if "connected" in words or "connection" in words:
        return BinarySensorDeviceClass.CONNECTIVITY
    if words.intersection({"failure", "error", "warning"}):
        return BinarySensorDeviceClass.PROBLEM
    if {"charge", "detected"} <= words or "charging" in words:
        return BinarySensorDeviceClass.BATTERY_CHARGING
    if "shoreline" in words or "plug" in words:
        return BinarySensorDeviceClass.PLUG
    if "movement" in words or "motion" in words:
        return BinarySensorDeviceClass.MOTION
    if {"engine", "running"} <= words:
        return BinarySensorDeviceClass.RUNNING
    return None


def _device_info_for_bus(
    entry_id: str, bus_id: int, comp: ComponentMeta | None
) -> dict[str, Any]:
    """Attach slot-backed entities to the root vehicle device."""
    del bus_id, comp
    return {
        "identifiers": {(DOMAIN, entry_id)},
        "manufacturer": MANUFACTURER,
    }


def entry_vehicle_display_name(entry: ConfigEntry) -> str:
    """Return a stable per-vehicle display name for the HA device."""
    data = getattr(entry, "data", {}) or {}
    model = (
        data.get(CONF_VEHICLE_MODEL)
        or data.get(CONF_VEHICLE_MODEL_GROUP)
        or data.get(CONF_VEHICLE_NAME)
    )
    vin = str(data.get(CONF_VIN) or "").strip().upper()
    if model and vin:
        return f"{model} ({vin[-6:]})"
    if model:
        return str(model)
    title = getattr(entry, "title", "")
    if title:
        return str(title)
    if vin:
        return vin[-6:]
    return "HYMER"


def root_device_info(entry: ConfigEntry) -> dict[str, Any]:
    """Build device_info for the vehicle/root integration device."""
    model = entry.data.get(CONF_VEHICLE_MODEL) or "Smart Interface Unit"
    return {
        "identifiers": {(DOMAIN, entry.entry_id)},
        "name": entry_vehicle_display_name(entry),
        "manufacturer": MANUFACTURER,
        "model": model,
    }


# -- Base: shared slot-aware boilerplate -------------------------------------


class _HymerSlotEntity(CoordinatorEntity[HymerConnectCoordinator]):
    """Base mixin for entities backed by a single (bus_id, sensor_id) slot."""

    _attr_has_entity_name = True

    def __init__(
        self,
        coordinator: HymerConnectCoordinator,
        entry: ConfigEntry,
        meta: SlotMeta,
        component: ComponentMeta | None,
    ) -> None:
        super().__init__(coordinator)
        self._entry = entry
        self._meta = meta
        self._component = component
        self._bus = meta.bus_id
        self._sid = meta.sensor_id
        self._attr_unique_id = (
            f"{entry.entry_id}_b{meta.bus_id}_s{meta.sensor_id}"
        )
        self._attr_translation_key = meta.label
        self._attr_name = _slot_entity_name(meta, component)
        self._attr_device_info = _device_info_for_bus(
            entry.entry_id, meta.bus_id, component
        )
        category = slot_entity_category(meta)
        if category is not None:
            self._attr_entity_category = category
        if slot_entity_hidden_by_default(meta) or slot_entity_disabled_by_default(
            meta, entry
        ):
            self._attr_entity_registry_enabled_default = False

    def _raw(self) -> Any:
        slots = (self.coordinator.data or {}).get("signalr_slots") or {}
        return slots.get((self._bus, self._sid))

    def _value(self) -> Any:
        return apply_transform(self._raw(), self._meta.transform)

    async def _ensure_client(self):
        """Return a connected SignalR client, reconnecting if needed."""
        return await self.coordinator.async_ensure_signalr_connected()


def _humanise(label: str) -> str:
    """Turn snake_case → Title Case (used when translations aren't loaded)."""
    if label in _HUMAN_LABEL_OVERRIDES:
        return _HUMAN_LABEL_OVERRIDES[label]
    rendered: list[str] = []
    for word in label.split("_"):
        rendered.append(_HUMAN_WORD_OVERRIDES.get(word, word.capitalize()))
    return " ".join(rendered)


def _slot_entity_name(meta: SlotMeta, component: ComponentMeta | None) -> str:
    """Render a clearer fallback name for generic per-slot entities."""
    if meta.label == "device_failure":
        if component is not None and component.kind == "bms":
            return "Battery Monitor Failure"
        if component is not None:
            return f"{component.name} Failure"
        return "Device Failure"
    if meta.label == "device_failure_status":
        if component is not None and component.kind == "bms":
            return "Battery Monitor Failure Status"
        if component is not None:
            return f"{component.name} Failure Status"
        return "Device Failure Status"
    if meta.label == "response_error":
        if component is not None and component.kind == "truma_heater":
            return "Heater Response Error"
        if component is not None:
            return f"{component.name} Response Error"
        return "Response Error"
    if meta.label == "combi_error":
        if component is not None and component.kind == "truma_heater":
            return "Heater Error"
        if component is not None:
            return f"{component.name} Error"
        return "Combi Error"
    if meta.label == "panel_busy":
        if component is not None and component.kind == "truma_heater":
            return "Heater Panel Busy"
        if component is not None:
            return f"{component.name} Panel Busy"
        return "Panel Busy"
    if meta.label == "error":
        if component is not None:
            return f"{component.name} Error"
        return "Error"
    if meta.label == "night_mode" and component is not None and component.kind == "fridge":
        return "Fridge Silent Mode"
    if meta.label == "night_mode":
        return "Silent Mode"
    if component is not None and component.kind == "truma_heater":
        if meta.label == "power_limit":
            return "Heater Electric Power Limit"
    if component is not None and component.kind == "fridge":
        if meta.label in {
            "warning_error_information",
            "error_warning_information",
            "error_information",
        }:
            return "Fridge Warning/Error"
        if meta.label == "dcvoltage":
            return "Fridge DC Voltage"
        if meta.label == "fridge_power":
            return "Fridge"
        if meta.label == "fridge_level":
            return "Fridge Temperature Level"
        if meta.label == "door_open":
            return "Fridge Door"
    if component is not None and component.kind == "light":
        if meta.label == "brightness":
            return f"{component.name} Brightness"
        if meta.label == "color_temp":
            return f"{component.name} Color Temperature"
        if meta.label == "on_off":
            return component.name
    return _humanise(meta.label)


def slot_entity_name_override(
    meta: SlotMeta,
    component: ComponentMeta | None = None,
) -> str | None:
    """Return a registry-level display-name override for raw entities."""
    if meta.label in _HUMAN_LABEL_OVERRIDES:
        return _slot_entity_name(meta, component)
    if meta.label in {
        "combi_error",
        "dcvoltage",
        "device_failure",
        "device_failure_status",
        "error",
        "error_information",
        "power_limit",
        "error_warning_information",
        "panel_busy",
        "response_error",
        "warning_error_information",
    }:
        return _slot_entity_name(meta, component)
    if meta.label == "night_mode" and component is not None and component.kind == "fridge":
        return "Fridge Silent Mode"
    if meta.label == "night_mode":
        return "Silent Mode"
    if component is not None and component.kind == "light":
        return _slot_entity_name(meta, component)
    if component is not None and component.kind == "fridge":
        return _slot_entity_name(meta, component)
    return None


def slot_entity_category(meta: SlotMeta) -> EntityCategory | None:
    """Return the HA entity category for a raw slot-backed entity."""
    if meta.label in _DIAGNOSTIC_RAW_LABELS:
        return EntityCategory.DIAGNOSTIC
    if meta.deprecated:
        return (
            EntityCategory.CONFIG
            if meta.is_writable
            else EntityCategory.DIAGNOSTIC
        )
    return None


def slot_entity_hidden_by_default(meta: SlotMeta) -> bool:
    """Return whether a raw slot-backed entity should default to disabled."""
    return meta.deprecated and meta.label not in _DIAGNOSTIC_RAW_LABELS


def slot_entity_disabled_by_default(meta: SlotMeta, entry: ConfigEntry) -> bool:
    """Return whether a raw slot-backed entity should be integration-disabled."""
    return (
        slot_entity_category(meta) == EntityCategory.DIAGNOSTIC
        and not debug_diagnostics_enabled(entry)
    )


def enum_option_for_value(
    value: Any,
    options: list[str] | tuple[str, ...],
) -> str | None:
    """Map a raw enum wire value onto an option label."""
    numeric_options: dict[int, str] = {}
    for option in options:
        try:
            numeric_options[int(str(option))] = str(option)
        except (TypeError, ValueError):
            numeric_options = {}
            break
    if isinstance(value, str):
        return value if value in options else None
    if isinstance(value, (int, float)):
        if numeric_options:
            return numeric_options.get(int(value))
        index = int(value)
        if 0 <= index < len(options):
            return str(options[index])
    return None


def enum_wire_value_for_option(
    option: str,
    *,
    datatype: str,
    options: list[str] | tuple[str, ...],
) -> str | int:
    """Map an option label back to the wire representation."""
    if datatype == "string":
        return option
    if datatype == "int":
        try:
            numeric_options = [int(str(item)) for item in options]
        except (TypeError, ValueError):
            numeric_options = []
        if numeric_options and option in {str(item) for item in options}:
            return int(option)
        try:
            return list(options).index(option)
        except ValueError as err:
            raise ValueError(f"Unknown option {option!r}") from err
    raise ValueError(f"Unsupported enum datatype {datatype!r}")


# -- Concrete classes --------------------------------------------------------


class HymerSensor(_HymerSlotEntity, SensorEntity):
    """Generic read-only numeric/string sensor."""

    def __init__(self, coordinator, entry, meta, component):
        super().__init__(coordinator, entry, meta, component)
        if meta.unit:
            self._attr_native_unit_of_measurement = display_unit(meta.unit, entry)
        dc = _default_device_class(meta, entry)
        if dc is not None:
            self._attr_device_class = dc
        if meta.datatype in ("int", "float") and meta.unit:
            # Totals look like Ah remaining / km-to-service etc. — measurement.
            self._attr_state_class = SensorStateClass.MEASUREMENT

    @property
    def native_value(self) -> Any:
        v = self._value()
        # Sentinel filter — decoded layer removes most, but keep a safety net.
        if isinstance(v, float) and v <= -273:
            return None
        return display_value(v, self._meta.unit, self._entry)


class HymerBinarySensor(_HymerSlotEntity, BinarySensorEntity):
    """Generic read-only boolean / string → on/off sensor."""

    _STRING_TRUE_VALUES = {"ON", "TRUE", "YES"}
    _STRING_FALSE_VALUES = {"OFF", "FALSE", "NO"}
    _DOOR_TRUE_VALUES = {"OPEN", "OPN"}
    _DOOR_FALSE_VALUES = {"CLOSED", "CLS"}
    _CONNECTIVITY_TRUE_VALUES = {"CONNECTED", "ONLINE", "AVAILABLE"}
    _CONNECTIVITY_FALSE_VALUES = {"DISCONNECTED", "OFFLINE", "UNAVAILABLE"}

    def __init__(self, coordinator, entry, meta, component):
        super().__init__(coordinator, entry, meta, component)
        device_class = _binary_sensor_device_class_for_label(meta.label)
        if device_class is not None:
            self._attr_device_class = device_class

    @property
    def is_on(self) -> bool | None:
        v = self._value()
        if v is None:
            return None
        if isinstance(v, bool):
            return v
        if isinstance(v, (int, float)):
            return bool(v)
        if isinstance(v, str):
            u = v.upper()
            if u in self._STRING_TRUE_VALUES:
                return True
            if u in self._STRING_FALSE_VALUES:
                return False
            if self._attr_device_class == BinarySensorDeviceClass.DOOR:
                if u in self._DOOR_TRUE_VALUES:
                    return True
                if u in self._DOOR_FALSE_VALUES:
                    return False
            if self._attr_device_class == BinarySensorDeviceClass.LOCK:
                if "UNLOCKED" in u or "INTERNAL" in u:
                    return True
                if "LOCKED" in u:
                    return False
            if self._attr_device_class in {
                BinarySensorDeviceClass.CONNECTIVITY,
                BinarySensorDeviceClass.PLUG,
            }:
                if u in self._CONNECTIVITY_TRUE_VALUES:
                    return True
                if u in self._CONNECTIVITY_FALSE_VALUES:
                    return False
        return None


class HymerSwitch(_HymerSlotEntity, SwitchEntity):
    """Generic writable bool / "On"/"Off" string switch.

    Inherits the v2.9.9 12V-main-switch bounce-back holdoff: after commanding
    OFF on the 12V main switch (bus 3, sid 1), the SCU briefly disconnects
    and pushes a stale cached "On" readback ~5 s later; we hold the
    optimistic OFF for 30 s to ride through the bounce.
    """

    _attr_device_class = SwitchDeviceClass.SWITCH

    # 12V main switch: bus 3, sid 1.  Use a tuple so per-vehicle overlays
    # could override this in the future.
    _MAIN_SWITCH_SLOT: tuple[int, int] = (3, 1)
    _MAIN_SWITCH_OFF_HOLDOFF_S: float = 30.0
    _OPTIMISTIC_TTL_S: float = 15.0

    def __init__(self, coordinator, entry, meta, component):
        super().__init__(coordinator, entry, meta, component)
        self._optimistic: bool | None = None
        self._optimistic_set_at: float = 0.0

    def _raw_is_on(self) -> bool | None:
        v = self._value()
        if v is None:
            return None
        if isinstance(v, bool):
            return v
        if isinstance(v, str):
            return v.upper() in ("ON", "TRUE", "YES")
        return bool(v)

    def _expire_optimistic(self) -> None:
        if self._optimistic is None:
            return
        elapsed = time.monotonic() - self._optimistic_set_at
        ttl = self._OPTIMISTIC_TTL_S
        if (
            (self._bus, self._sid) == self._MAIN_SWITCH_SLOT
            and self._optimistic is False
        ):
            ttl = self._MAIN_SWITCH_OFF_HOLDOFF_S
        if elapsed >= ttl:
            _LOGGER.debug(
                "Expiring optimistic state for switch %s after %.1fs",
                (self._bus, self._sid),
                elapsed,
            )
            self._optimistic = None

    @property
    def available(self) -> bool:
        if self._meta.label == "water_pump" and not self.coordinator.is_habitation_power_available():
            return False
        return super().available

    @property
    def is_on(self) -> bool | None:
        self._expire_optimistic()
        if self._optimistic is not None:
            return self._optimistic
        return self._raw_is_on()

    async def _send(self, on: bool) -> None:
        client = await self._ensure_client()
        if self._meta.datatype == "string":
            # Main-switch style: "On"/"Off" strings
            await client.send_light_command(
                self._bus, self._sid, str_value="On" if on else "Off"
            )
        else:
            await client.send_light_command(
                self._bus, self._sid, bool_value=on
            )
        self._optimistic = on
        self._optimistic_set_at = time.monotonic()
        self.async_write_ha_state()

    async def async_turn_on(self, **kwargs):
        await self._send(True)

    async def async_turn_off(self, **kwargs):
        await self._send(False)

    def _handle_coordinator_update(self) -> None:
        self._expire_optimistic()
        if self._optimistic is not None:
            actual = self._raw_is_on()
            # 12V main switch OFF holdoff: ignore stale "On" readback
            # during the SCU reconnection bounce.
            if (
                self._optimistic is False
                and (self._bus, self._sid) == self._MAIN_SWITCH_SLOT
            ):
                v = self._value()
                v_is_on = (
                    v is True
                    or (isinstance(v, str) and v.upper() == "ON")
                )
                elapsed = time.monotonic() - self._optimistic_set_at
                if v_is_on and elapsed < self._MAIN_SWITCH_OFF_HOLDOFF_S:
                    _LOGGER.debug(
                        "12V switch: ignoring stale 'On' readback %.1fs after OFF (holdoff %ds)",
                        elapsed, int(self._MAIN_SWITCH_OFF_HOLDOFF_S),
                    )
                    super()._handle_coordinator_update()
                    return
            if actual is not None and bool(actual) == self._optimistic:
                self._optimistic = None
        super()._handle_coordinator_update()


class HymerNumber(_HymerSlotEntity, NumberEntity):
    """Generic writable numeric (int/float) control."""

    _OPTIMISTIC_TTL_S: float = 15.0

    def __init__(self, coordinator, entry, meta, component):
        super().__init__(coordinator, entry, meta, component)
        if meta.unit:
            self._attr_native_unit_of_measurement = display_unit(meta.unit, entry)
        if meta.min_value is not None:
            self._attr_native_min_value = float(
                display_value(meta.min_value, meta.unit, entry)
            )
        if meta.max_value is not None:
            self._attr_native_max_value = float(
                display_value(meta.max_value, meta.unit, entry)
            )
        if meta.step is not None:
            self._attr_native_step = float(
                display_value(meta.step, meta.unit, entry)
            )
        # Reasonable default ranges when runtime metadata does not provide one.
        if (
            meta.unit == "%"
            and meta.min_value is None
            and meta.max_value is None
            and meta.step is None
        ):
            self._attr_native_min_value = 0
            self._attr_native_max_value = 100
            self._attr_native_step = 1
        self._optimistic: float | None = None
        self._optimistic_set_at: float = 0.0

    def _expire_optimistic(self) -> None:
        if self._optimistic is None:
            return
        elapsed = time.monotonic() - self._optimistic_set_at
        if elapsed >= self._OPTIMISTIC_TTL_S:
            _LOGGER.debug(
                "Expiring optimistic numeric state for %s after %.1fs",
                (self._bus, self._sid),
                elapsed,
            )
            self._optimistic = None

    @property
    def native_value(self) -> float | None:
        self._expire_optimistic()
        if self._optimistic is not None:
            return self._optimistic
        v = self._value()
        if v is None:
            return None
        if not isinstance(v, (int, float)):
            return None
        return float(display_value(v, self._meta.unit, self._entry))

    async def async_set_native_value(self, value: float) -> None:
        client = await self._ensure_client()
        native_value = native_value_from_display(
            value,
            self._meta.unit,
            self._entry,
        )
        wire = reverse_transform(native_value, self._meta.transform)
        if self._meta.datatype == "float":
            await client.send_multi_sensor_command([
                {"bus_id": self._bus, "sensor_id": self._sid,
                 "float_value": float(wire)},
            ])
        else:
            await client.send_light_command(
                self._bus, self._sid, uint_value=int(round(float(wire)))
            )
        self._optimistic = value
        self._optimistic_set_at = time.monotonic()
        self.async_write_ha_state()

    def _handle_coordinator_update(self) -> None:
        self._expire_optimistic()
        if self._optimistic is not None:
            actual = self._value()
            if isinstance(actual, (int, float)) and abs(float(actual) - self._optimistic) < 1e-6:
                self._optimistic = None
        super()._handle_coordinator_update()


class HymerSelect(_HymerSlotEntity, SelectEntity):
    """Generic writable string enum — requires an 'options' set provided by
    the discovery layer or a template override.
    """

    _OPTIMISTIC_TTL_S: float = 15.0

    def __init__(self, coordinator, entry, meta, component, options: list[str]):
        super().__init__(coordinator, entry, meta, component)
        self._attr_options = options
        self._optimistic: str | None = None
        self._optimistic_set_at: float = 0.0

    def _expire_optimistic(self) -> None:
        if self._optimistic is None:
            return
        elapsed = time.monotonic() - self._optimistic_set_at
        if elapsed >= self._OPTIMISTIC_TTL_S:
            _LOGGER.debug(
                "Expiring optimistic select state for %s after %.1fs",
                (self._bus, self._sid),
                elapsed,
            )
            self._optimistic = None

    @property
    def current_option(self) -> str | None:
        self._expire_optimistic()
        if self._optimistic is not None:
            return self._optimistic
        return enum_option_for_value(self._value(), self._attr_options)

    async def async_select_option(self, option: str) -> None:
        client = await self._ensure_client()
        wire = enum_wire_value_for_option(
            option,
            datatype=self._meta.datatype,
            options=self._attr_options,
        )
        if isinstance(wire, str):
            await client.send_light_command(self._bus, self._sid, str_value=wire)
        else:
            await client.send_light_command(self._bus, self._sid, uint_value=wire)
        self._optimistic = option
        self._optimistic_set_at = time.monotonic()
        self.async_write_ha_state()

    def _handle_coordinator_update(self) -> None:
        self._expire_optimistic()
        if self._optimistic is not None:
            actual = enum_option_for_value(self._value(), self._attr_options)
            if actual == self._optimistic:
                self._optimistic = None
        super()._handle_coordinator_update()


class HymerText(_HymerSlotEntity, TextEntity):
    """Generic writable text value for validated free-form string slots."""

    _OPTIMISTIC_TTL_S: float = 15.0

    def __init__(self, coordinator, entry, meta, component):
        super().__init__(coordinator, entry, meta, component)
        self._optimistic: str | None = None
        self._optimistic_set_at: float = 0.0

    def _expire_optimistic(self) -> None:
        if self._optimistic is None:
            return
        elapsed = time.monotonic() - self._optimistic_set_at
        if elapsed >= self._OPTIMISTIC_TTL_S:
            _LOGGER.debug(
                "Expiring optimistic text state for %s after %.1fs",
                (self._bus, self._sid),
                elapsed,
            )
            self._optimistic = None

    @property
    def native_value(self) -> str | None:
        self._expire_optimistic()
        if self._optimistic is not None:
            return self._optimistic
        value = self._value()
        return value if isinstance(value, str) else None

    async def async_set_value(self, value: str) -> None:
        client = await self._ensure_client()
        await client.send_light_command(self._bus, self._sid, str_value=value)
        self._optimistic = value
        self._optimistic_set_at = time.monotonic()
        self.async_write_ha_state()

    def _handle_coordinator_update(self) -> None:
        self._expire_optimistic()
        if self._optimistic is not None:
            actual = self._value()
            if isinstance(actual, str) and actual == self._optimistic:
                self._optimistic = None
        super()._handle_coordinator_update()


class HymerButton(_HymerSlotEntity, ButtonEntity):
    """Generic momentary action button for validated write-only slots."""

    async def async_press(self) -> None:
        client = await self._ensure_client()
        default_value: bool | int | float
        if self._meta.datatype == "bool":
            default_value = True
        elif self._meta.datatype == "int":
            default_value = 1
        elif self._meta.datatype == "float":
            default_value = 1.0
        else:
            raise ValueError(
                f"Unsupported button datatype {self._meta.datatype!r} for "
                f"slot {(self._bus, self._sid)}"
            )

        try:
            sensor = serialize_slot_action(
                {
                    "component_id": self._bus,
                    "sensor_id": self._sid,
                    "value": default_value,
                }
            )
        except SlotActionError as err:
            raise HomeAssistantError(str(err)) from err

        await client.send_multi_sensor_command([sensor])


class HymerRootActionButton(CoordinatorEntity[HymerConnectCoordinator], ButtonEntity):
    """Root-device action button for app-level commands without slot backing."""

    _attr_has_entity_name = True

    def __init__(
        self,
        coordinator: HymerConnectCoordinator,
        entry: ConfigEntry,
        *,
        key: str,
        name: str,
        press_action: Any,
        icon: str | None = None,
        entity_category: EntityCategory | None = None,
        enabled_default: bool = True,
    ) -> None:
        super().__init__(coordinator)
        self._press_action = press_action
        self._attr_unique_id = f"{entry.entry_id}_{key}"
        self._attr_name = name
        self._attr_device_info = root_device_info(entry)
        if icon is not None:
            self._attr_icon = icon
        if entity_category is not None:
            self._attr_entity_category = entity_category
        if not enabled_default:
            self._attr_entity_registry_enabled_default = False

    @property
    def available(self) -> bool:
        return bool(self.coordinator.scu_urn)

    async def async_press(self) -> None:
        await self._press_action()
