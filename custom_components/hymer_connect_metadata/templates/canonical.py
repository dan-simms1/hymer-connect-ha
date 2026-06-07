"""Canonical capability templates.

These templates create one entity per user-facing capability when the selected
vehicle exposes a matching provider slot that is not already covered by the
raw discovery metadata.  This lets the integration reconcile cross-vehicle
component families without removing the generic per-slot fallback layer.
"""

from __future__ import annotations

import asyncio
import logging
import time
from typing import Any

from homeassistant.components.binary_sensor import (
    BinarySensorDeviceClass,
    BinarySensorEntity,
)
from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorStateClass,
)
from homeassistant.components.switch import SwitchEntity
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from ..capability_resolver import (
    ResolvedCapability,
    present_candidate_slots,
    resolved_capabilities,
)
from ..entity_base import root_device_info
from ..preferences import display_unit, display_value, suggested_display_precision

_LOGGER = logging.getLogger(__name__)

_SENSOR_DEVICE_CLASS: dict[str, SensorDeviceClass] = {
    "battery": SensorDeviceClass.BATTERY,
    "current": SensorDeviceClass.CURRENT,
    "frequency": SensorDeviceClass.FREQUENCY,
    "power": SensorDeviceClass.POWER,
    "temperature": SensorDeviceClass.TEMPERATURE,
    "voltage": SensorDeviceClass.VOLTAGE,
}

_BINARY_DEVICE_CLASS: dict[str, BinarySensorDeviceClass] = {
    "battery_charging": BinarySensorDeviceClass.BATTERY_CHARGING,
    "connectivity": BinarySensorDeviceClass.CONNECTIVITY,
    "motion": BinarySensorDeviceClass.MOTION,
    "plug": BinarySensorDeviceClass.PLUG,
    "problem": BinarySensorDeviceClass.PROBLEM,
}


class CanonicalSensorTemplate:
    PLATFORM = "sensor"

    def build(self, coordinator, entry, observed):
        entities: list[Any] = []
        claimed: set[tuple[int, int]] = set()
        for capability in resolved_capabilities(observed, self.PLATFORM):
            entities.append(CanonicalSensor(coordinator, entry, capability))
            claimed.update(present_candidate_slots(capability, observed))
        return entities, claimed


class CanonicalBinarySensorTemplate:
    PLATFORM = "binary_sensor"

    def build(self, coordinator, entry, observed):
        entities: list[Any] = []
        claimed: set[tuple[int, int]] = set()
        for capability in resolved_capabilities(observed, self.PLATFORM):
            entities.append(CanonicalBinarySensor(coordinator, entry, capability))
            claimed.update(present_candidate_slots(capability, observed))
        return entities, claimed


class CanonicalSwitchTemplate:
    PLATFORM = "switch"

    def build(self, coordinator, entry, observed):
        entities: list[Any] = []
        claimed: set[tuple[int, int]] = set()
        for capability in resolved_capabilities(observed, self.PLATFORM):
            entities.append(CanonicalSwitch(coordinator, entry, capability))
            claimed.update(present_candidate_slots(capability, observed))
        return entities, claimed


class _CanonicalEntity(CoordinatorEntity):
    _attr_has_entity_name = True

    def __init__(self, coordinator, entry, capability: ResolvedCapability) -> None:
        super().__init__(coordinator)
        self._entry = entry
        self._capability = capability
        self._attr_unique_id = f"{entry.entry_id}_canonical_{capability.spec.key}"
        self._attr_translation_key = capability.spec.key
        self._attr_device_info = root_device_info(entry)
        if capability.spec.icon:
            self._attr_icon = capability.spec.icon

    def _current_capability(self) -> ResolvedCapability:
        active = getattr(self.coordinator, "active_slots", set())
        observed = self.coordinator.observed_slots
        preferred_sets = (active, observed)
        for candidates in preferred_sets:
            for candidate in self._capability.spec.candidates:
                if candidate.key not in candidates:
                    continue
                if candidate.key != self._capability.slot:
                    self._capability = ResolvedCapability(
                        spec=self._capability.spec,
                        candidate=candidate,
                    )
                return self._capability
        return self._capability

    def _raw(self) -> Any:
        capability = self._current_capability()
        slots = (self.coordinator.data or {}).get("signalr_slots") or {}
        return slots.get(capability.slot)

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        capability = self._current_capability()
        active = getattr(self.coordinator, "active_slots", set())
        observed = self.coordinator.observed_slots
        return {
            "provider_component_id": capability.component_id,
            "provider_sensor_id": capability.sensor_id,
            "provider_component_name": capability.component_name,
            "alternate_provider_slots": [
                list(candidate.key)
                for candidate in capability.spec.candidates
                if candidate.key in observed
                and candidate.key != capability.slot
            ],
            "active_alternate_provider_slots": [
                list(candidate.key)
                for candidate in capability.spec.candidates
                if candidate.key in active
                and candidate.key != capability.slot
            ],
        }


class CanonicalSensor(_CanonicalEntity, SensorEntity):
    def __init__(self, coordinator, entry, capability: ResolvedCapability) -> None:
        super().__init__(coordinator, entry, capability)
        if capability.spec.unit:
            self._attr_native_unit_of_measurement = display_unit(
                capability.spec.unit,
                entry,
            )
            precision = suggested_display_precision(capability.spec.unit)
            if precision is not None:
                self._attr_suggested_display_precision = precision
            self._attr_state_class = SensorStateClass.MEASUREMENT
        if capability.spec.sensor_device_class:
            self._attr_device_class = _SENSOR_DEVICE_CLASS[
                capability.spec.sensor_device_class
            ]

    @property
    def native_value(self) -> Any:
        return display_value(
            self._raw(),
            self._capability.spec.unit,
            self._entry,
        )


class CanonicalBinarySensor(_CanonicalEntity, BinarySensorEntity):
    def __init__(self, coordinator, entry, capability: ResolvedCapability) -> None:
        super().__init__(coordinator, entry, capability)
        if capability.spec.binary_device_class:
            self._attr_device_class = _BINARY_DEVICE_CLASS[
                capability.spec.binary_device_class
            ]

    @property
    def is_on(self) -> bool | None:
        value = self._raw()
        if value is None:
            return None
        if isinstance(value, bool):
            return value
        if isinstance(value, (int, float)):
            return bool(value)
        if isinstance(value, str):
            return value.upper() in {"ON", "TRUE", "YES", "CONNECTED"}
        return None


class CanonicalSwitch(_CanonicalEntity, SwitchEntity):
    _MAIN_SWITCH_OFF_HOLDOFF_S = 30.0
    _MAIN_SWITCH_ON_VERIFY_S = 60.0
    _OPTIMISTIC_TTL_S = 15.0

    def __init__(self, coordinator, entry, capability: ResolvedCapability) -> None:
        super().__init__(coordinator, entry, capability)
        self._optimistic: bool | None = None
        self._optimistic_set_at: float = 0.0
        self._main_switch_command_at: float = 0.0
        self._main_switch_command_expected: bool | None = None
        self._retry_count: int = 0  # caps the re-auth+retry loop

    def _raw_is_on(self) -> bool | None:
        value = self._raw()
        if value is None:
            return None
        if isinstance(value, bool):
            return value
        if isinstance(value, str):
            return value.upper() in {"ON", "TRUE", "YES"}
        return bool(value)

    def _is_main_switch(self) -> bool:
        return self._capability.spec.key == "main_switch"

    def _expire_optimistic(self) -> None:
        if self._optimistic is None:
            return
        elapsed = time.monotonic() - self._optimistic_set_at
        ttl = self._MAIN_SWITCH_OFF_HOLDOFF_S
        if not self._is_main_switch():
            ttl = self._OPTIMISTIC_TTL_S
        if elapsed >= ttl:
            self._optimistic = None

    @property
    def available(self) -> bool:
        if (
            self._capability.spec.key == "water_pump"
            and not self.coordinator.is_habitation_power_available()
        ):
            return False
        return super().available

    @property
    def is_on(self) -> bool | None:
        self._expire_optimistic()
        if self._optimistic is not None:
            return self._optimistic
        return self._raw_is_on()

    def _track_task(self, task: asyncio.Task) -> None:
        track = getattr(self.coordinator, "track_background_task", None)
        if callable(track):
            track(task)

    def _create_task(self, coro) -> asyncio.Task:
        hass = getattr(self.coordinator, "hass", None)
        if hass is not None and hasattr(hass, "async_create_task"):
            task = hass.async_create_task(coro)
        else:
            task = asyncio.create_task(coro)
        self._track_task(task)
        return task

    def _schedule_main_switch_verification(self, expected_on: bool) -> None:
        if not self._is_main_switch():
            return
        command_at = self._main_switch_command_at
        delay = (
            self._MAIN_SWITCH_ON_VERIFY_S
            if expected_on
            else self._MAIN_SWITCH_OFF_HOLDOFF_S
        )
        self._create_task(
            self._verify_main_switch_readback(expected_on, command_at, delay)
        )

    async def _verify_main_switch_readback(
        self,
        expected_on: bool,
        command_at: float,
        delay: float,
    ) -> None:
        await asyncio.sleep(delay)
        if (
            self._main_switch_command_at != command_at
            or self._main_switch_command_expected is not expected_on
        ):
            return
        actual = self._raw_is_on()
        if actual is not None and bool(actual) == expected_on:
            self._optimistic = None
            self._main_switch_command_expected = None
            self.async_write_ha_state()
            return
        if expected_on is False and self.coordinator.habitation_power_state is False:
            self._optimistic = None
            self._main_switch_command_expected = None
            self.async_write_ha_state()
            return

        self._retry_count += 1
        if self._retry_count > 1:
            _LOGGER.warning(
                "12V main switch still mismatched (%r) after %d re-auth+retry "
                "attempts — giving up (reload integration to recover)",
                actual,
                self._retry_count,
            )
            self._optimistic = None
            self._main_switch_command_expected = None
            self._retry_count = 0
            self.async_write_ha_state()
            return

        # A plain reconnect reuses the stale OAuth2 session and can leave the
        # hub->SCU command channel dead.  Force a full re-auth so the retried
        # command actually reaches the SCU.  Keep verify=True so a second
        # mismatch trips the retry cap above instead of looping forever.
        await self.coordinator.force_reauth_and_reconnect()
        await self._send(expected_on, verify=True)

    async def _send(self, on: bool, *, verify: bool = True) -> None:
        capability = self._current_capability()
        client = await self.coordinator.async_ensure_signalr_connected()
        if capability.candidate.write_style == "string_on_off":
            await client.send_light_command(
                capability.component_id,
                capability.sensor_id,
                str_value="On" if on else "Off",
            )
        else:
            await client.send_light_command(
                capability.component_id,
                capability.sensor_id,
                bool_value=on,
            )
        self._optimistic = on
        self._optimistic_set_at = time.monotonic()
        if self._is_main_switch():
            self._main_switch_command_at = self._optimistic_set_at
            self._main_switch_command_expected = on
            if verify:
                self._schedule_main_switch_verification(on)
        self.async_write_ha_state()

    async def async_turn_on(self, **kwargs):
        self._retry_count = 0  # reset cap on a new user-initiated command
        await self._send(True)

    async def async_turn_off(self, **kwargs):
        self._retry_count = 0  # reset cap on a new user-initiated command
        await self._send(False)

    def _handle_coordinator_update(self) -> None:
        self._expire_optimistic()
        if self._optimistic is not None:
            actual = self._raw_is_on()
            if (
                self._is_main_switch()
                and self._optimistic is False
            ):
                value = self._raw()
                value_is_on = (
                    value is True
                    or (isinstance(value, str) and value.upper() == "ON")
                )
                elapsed = time.monotonic() - self._optimistic_set_at
                if value_is_on and elapsed < self._MAIN_SWITCH_OFF_HOLDOFF_S:
                    super()._handle_coordinator_update()
                    return
            if actual is not None and bool(actual) == self._optimistic:
                self._optimistic = None
        super()._handle_coordinator_update()
