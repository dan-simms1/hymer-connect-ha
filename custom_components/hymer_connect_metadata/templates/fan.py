"""Fan templates for supported fan families.

Current coverage:

- MaxxFan front/rear fan power channels on component 102
- Airxcel front/rear roof fans on component 95
- bathroom fan channel on the power-system family

The more ambiguous speed / dome / airflow controls remain in the raw layer.
"""

from __future__ import annotations

from typing import Any

from homeassistant.components.fan import FanEntity, FanEntityFeature
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from ..discovery import component_meta, slot_meta
from ..entity_base import _device_info_for_bus, enum_option_for_value
from ..template_specs import FanEntitySpec, fan_entity_specs

_FAN_SPEED_FEATURE = getattr(
    FanEntityFeature,
    "SET_SPEED",
    getattr(FanEntityFeature, "SET_PERCENTAGE", 0),
)
_AIRXCEL_SPEED_TO_PERCENTAGE = {
    "LOW": 33,
    "MEDIUM": 66,
    "HIGH": 100,
}
_AIRXCEL_PERCENTAGE_TO_SPEED = [
    (34, "LOW"),
    (67, "MEDIUM"),
    (101, "HIGH"),
]
_MAXXFAN_SPEED_TO_PERCENTAGE = {
    "OFF": 0,
    "LOW": 33,
    "MEDIUM": 66,
    "HIGH": 100,
}
_MAXXFAN_PERCENTAGE_TO_SPEED = [
    (1, "OFF"),
    (34, "LOW"),
    (67, "MEDIUM"),
    (101, "HIGH"),
]


class FanTemplate:
    PLATFORM = "fan"

    def build(self, coordinator, entry, observed):
        entities: list[Any] = []
        claimed: set[tuple[int, int]] = set()
        for spec in fan_entity_specs():
            if not _fan_spec_is_observed(spec, observed):
                continue
            entity = _build_fan_entity(spec, coordinator, entry)
            if entity is None:
                continue
            entities.append(entity)
            claimed.update(slot for slot in spec.claimable_slots if slot in observed)
        return entities, claimed


def _fan_spec_is_observed(
    spec: FanEntitySpec,
    observed: set[tuple[int, int]],
) -> bool:
    required = {(spec.component_id, spec.state_sid)}
    if spec.speed_mode_sid is not None:
        required.add((spec.component_id, spec.speed_mode_sid))
    if spec.speed_sid is not None:
        required.add((spec.component_id, spec.speed_sid))
    return required.issubset(observed)


def _build_fan_entity(spec: FanEntitySpec, coordinator, entry):
    if spec.kind == "airxcel_roof_fan":
        if spec.speed_mode_sid is None or spec.speed_sid is None:
            return None
        return AirxcelRoofFan(
            coordinator,
            entry,
            spec.component_id,
            state_sid=spec.state_sid,
            speed_mode_sid=spec.speed_mode_sid,
            speed_sid=spec.speed_sid,
            name=spec.name,
        )
    if spec.kind == "maxxfan":
        if spec.speed_sid is None:
            return None
        return HymerMaxxFan(
            coordinator,
            entry,
            spec.component_id,
            state_sid=spec.state_sid,
            speed_sid=spec.speed_sid,
            name=spec.name,
            attribute_slots=spec.attributes,
        )
    if spec.kind == "simple":
        return HymerSimpleFan(
            coordinator,
            entry,
            spec.component_id,
            spec.state_sid,
            spec.name,
            attribute_slots=spec.attributes,
        )
    return None


class HymerSimpleFan(CoordinatorEntity, FanEntity):
    _attr_has_entity_name = True
    _attr_supported_features = FanEntityFeature.TURN_ON | FanEntityFeature.TURN_OFF

    def __init__(
        self,
        coordinator,
        entry,
        bus_id: int,
        state_sid: int,
        name: str,
        attribute_slots: dict[str, int] | None = None,
    ) -> None:
        super().__init__(coordinator)
        self._bus = bus_id
        self._state_sid = state_sid
        self._attribute_slots = attribute_slots or {}
        comp = component_meta(bus_id)
        suffix = name.lower().replace(" ", "_")
        self._attr_unique_id = f"{entry.entry_id}_fan_b{bus_id}_{suffix}"
        self._attr_name = name
        self._attr_device_info = _device_info_for_bus(entry.entry_id, bus_id, comp)
        self._attr_icon = "mdi:fan"

    def _slot(self, sid: int | None):
        if sid is None:
            return None
        slots = (self.coordinator.data or {}).get("signalr_slots") or {}
        return slots.get((self._bus, sid))

    @property
    def is_on(self) -> bool | None:
        value = self._slot(self._state_sid)
        if value is None:
            return None
        return bool(value)

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        attrs: dict[str, Any] = {}
        for key, sid in self._attribute_slots.items():
            value = self._slot(sid)
            if value is not None:
                attrs[key] = value
        return attrs

    async def async_turn_on(self, percentage: int | None = None, preset_mode: str | None = None, **kwargs: Any) -> None:
        client = await self.coordinator.async_ensure_signalr_connected()
        await client.send_light_command(self._bus, self._state_sid, bool_value=True)
        self.async_write_ha_state()

    async def async_turn_off(self, **kwargs: Any) -> None:
        client = await self.coordinator.async_ensure_signalr_connected()
        await client.send_light_command(self._bus, self._state_sid, bool_value=False)
        self.async_write_ha_state()


class AirxcelRoofFan(CoordinatorEntity, FanEntity):
    _attr_has_entity_name = True
    _attr_supported_features = (
        FanEntityFeature.TURN_ON
        | FanEntityFeature.TURN_OFF
        | _FAN_SPEED_FEATURE
    )

    def __init__(
        self,
        coordinator,
        entry,
        bus_id: int,
        *,
        state_sid: int,
        speed_mode_sid: int,
        speed_sid: int,
        name: str,
    ) -> None:
        super().__init__(coordinator)
        self._bus = bus_id
        self._state_sid = state_sid
        self._speed_mode_sid = speed_mode_sid
        self._speed_sid = speed_sid
        self._state_meta = slot_meta(bus_id, state_sid)
        self._speed_mode_meta = slot_meta(bus_id, speed_mode_sid)
        self._speed_meta = slot_meta(bus_id, speed_sid)
        comp = component_meta(bus_id)
        suffix = name.lower().replace(" ", "_")
        self._attr_unique_id = f"{entry.entry_id}_fan_b{bus_id}_{suffix}"
        self._attr_name = name
        self._attr_device_info = _device_info_for_bus(entry.entry_id, bus_id, comp)
        self._attr_icon = "mdi:fan"
        self._optimistic_on: bool | None = None
        self._optimistic_percentage: int | None = None

    def _slot(self, sid: int):
        slots = (self.coordinator.data or {}).get("signalr_slots") or {}
        return slots.get((self._bus, sid))

    def _live_is_on(self) -> bool | None:
        value = self._slot(self._state_sid)
        if isinstance(value, str):
            return value.upper() == "ON"
        return None

    def _live_percentage(self) -> int | None:
        if self._speed_meta is None:
            return None
        option = enum_option_for_value(self._slot(self._speed_sid), self._speed_meta.options)
        if option is None:
            return None
        return _AIRXCEL_SPEED_TO_PERCENTAGE.get(option)

    def _command(self, sid: int, value: Any) -> dict[str, Any]:
        meta = slot_meta(self._bus, sid)
        command: dict[str, Any] = {"bus_id": self._bus, "sensor_id": sid}
        if meta is not None and meta.datatype == "string":
            command["str_value"] = str(value)
        else:
            command["uint_value"] = int(value)
        return command

    def _speed_for_percentage(self, percentage: int) -> str:
        for threshold, option in _AIRXCEL_PERCENTAGE_TO_SPEED:
            if percentage < threshold:
                return option
        return "HIGH"

    @property
    def is_on(self) -> bool | None:
        if self._optimistic_on is not None:
            return self._optimistic_on
        return self._live_is_on()

    @property
    def percentage(self) -> int | None:
        if self._optimistic_percentage is not None:
            return self._optimistic_percentage
        return self._live_percentage()

    async def async_turn_on(
        self,
        percentage: int | None = None,
        preset_mode: str | None = None,
        **kwargs: Any,
    ) -> None:
        writes = [self._command(self._state_sid, "ON")]
        if percentage is not None and self._speed_mode_meta is not None and self._speed_meta is not None:
            speed = self._speed_for_percentage(percentage)
            writes.append(self._command(self._speed_mode_sid, "MANUAL"))
            writes.append(self._command(self._speed_sid, speed))
            self._optimistic_percentage = _AIRXCEL_SPEED_TO_PERCENTAGE[speed]
        client = await self.coordinator.async_ensure_signalr_connected()
        await client.send_multi_sensor_command(writes)
        self._optimistic_on = True
        self.async_write_ha_state()

    async def async_turn_off(self, **kwargs: Any) -> None:
        client = await self.coordinator.async_ensure_signalr_connected()
        await client.send_multi_sensor_command([self._command(self._state_sid, "OFF")])
        self._optimistic_on = False
        self.async_write_ha_state()

    async def async_set_percentage(self, percentage: int) -> None:
        if self._speed_mode_meta is None or self._speed_meta is None:
            return
        if percentage <= 0:
            await self.async_turn_off()
            return
        speed = self._speed_for_percentage(percentage)
        writes = [
            self._command(self._state_sid, "ON"),
            self._command(self._speed_mode_sid, "MANUAL"),
            self._command(self._speed_sid, speed),
        ]
        client = await self.coordinator.async_ensure_signalr_connected()
        await client.send_multi_sensor_command(writes)
        self._optimistic_on = True
        self._optimistic_percentage = _AIRXCEL_SPEED_TO_PERCENTAGE[speed]
        self.async_write_ha_state()

    def _handle_coordinator_update(self) -> None:
        if self._optimistic_on is not None and self._live_is_on() == self._optimistic_on:
            self._optimistic_on = None
        if (
            self._optimistic_percentage is not None
            and self._live_percentage() == self._optimistic_percentage
        ):
            self._optimistic_percentage = None
        super()._handle_coordinator_update()


class HymerMaxxFan(CoordinatorEntity, FanEntity):
    _attr_has_entity_name = True
    _attr_supported_features = (
        FanEntityFeature.TURN_ON
        | FanEntityFeature.TURN_OFF
        | _FAN_SPEED_FEATURE
    )

    def __init__(
        self,
        coordinator,
        entry,
        bus_id: int,
        *,
        state_sid: int,
        speed_sid: int,
        name: str,
        attribute_slots: dict[str, int] | None = None,
    ) -> None:
        super().__init__(coordinator)
        self._bus = bus_id
        self._state_sid = state_sid
        self._speed_sid = speed_sid
        self._speed_meta = slot_meta(bus_id, speed_sid)
        self._attribute_slots = attribute_slots or {}
        comp = component_meta(bus_id)
        suffix = name.lower().replace(" ", "_")
        self._attr_unique_id = f"{entry.entry_id}_fan_b{bus_id}_{suffix}"
        self._attr_name = name
        self._attr_device_info = _device_info_for_bus(entry.entry_id, bus_id, comp)
        self._attr_icon = "mdi:fan"
        self._optimistic_on: bool | None = None
        self._optimistic_percentage: int | None = None

    def _slot(self, sid: int | None):
        if sid is None:
            return None
        slots = (self.coordinator.data or {}).get("signalr_slots") or {}
        return slots.get((self._bus, sid))

    def _live_percentage(self) -> int | None:
        if self._speed_meta is None:
            return None
        option = enum_option_for_value(self._slot(self._speed_sid), self._speed_meta.options)
        if option is None:
            return None
        return _MAXXFAN_SPEED_TO_PERCENTAGE.get(option)

    def _speed_for_percentage(self, percentage: int) -> str:
        for threshold, option in _MAXXFAN_PERCENTAGE_TO_SPEED:
            if percentage < threshold:
                return option
        return "HIGH"

    @property
    def is_on(self) -> bool | None:
        if self._optimistic_on is not None:
            return self._optimistic_on
        value = self._slot(self._state_sid)
        if value is None:
            return None
        return bool(value)

    @property
    def percentage(self) -> int | None:
        if self._optimistic_percentage is not None:
            return self._optimistic_percentage
        return self._live_percentage()

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        attrs: dict[str, Any] = {}
        speed = enum_option_for_value(
            self._slot(self._speed_sid),
            self._speed_meta.options if self._speed_meta else (),
        )
        if speed is not None:
            attrs["speed_state"] = speed
        for key, sid in self._attribute_slots.items():
            value = self._slot(sid)
            if value is not None:
                attrs[key] = value
        return attrs

    async def async_turn_on(
        self,
        percentage: int | None = None,
        preset_mode: str | None = None,
        **kwargs: Any,
    ) -> None:
        writes = [{"bus_id": self._bus, "sensor_id": self._state_sid, "bool_value": True}]
        if percentage is not None and self._speed_meta is not None:
            speed = self._speed_for_percentage(percentage)
            writes.append({"bus_id": self._bus, "sensor_id": self._speed_sid, "str_value": speed})
            self._optimistic_percentage = _MAXXFAN_SPEED_TO_PERCENTAGE[speed]
        client = await self.coordinator.async_ensure_signalr_connected()
        await client.send_multi_sensor_command(writes)
        self._optimistic_on = True
        self.async_write_ha_state()

    async def async_turn_off(self, **kwargs: Any) -> None:
        client = await self.coordinator.async_ensure_signalr_connected()
        await client.send_multi_sensor_command([
            {"bus_id": self._bus, "sensor_id": self._state_sid, "bool_value": False},
        ])
        self._optimistic_on = False
        self._optimistic_percentage = 0
        self.async_write_ha_state()

    async def async_set_percentage(self, percentage: int) -> None:
        if self._speed_meta is None:
            return
        if percentage <= 0:
            await self.async_turn_off()
            return
        speed = self._speed_for_percentage(percentage)
        client = await self.coordinator.async_ensure_signalr_connected()
        await client.send_multi_sensor_command([
            {"bus_id": self._bus, "sensor_id": self._state_sid, "bool_value": True},
            {"bus_id": self._bus, "sensor_id": self._speed_sid, "str_value": speed},
        ])
        self._optimistic_on = True
        self._optimistic_percentage = _MAXXFAN_SPEED_TO_PERCENTAGE[speed]
        self.async_write_ha_state()

    def _handle_coordinator_update(self) -> None:
        live_on = self._slot(self._state_sid)
        if self._optimistic_on is not None and live_on is not None and bool(live_on) == self._optimistic_on:
            self._optimistic_on = None
        if (
            self._optimistic_percentage is not None
            and self._live_percentage() == self._optimistic_percentage
        ):
            self._optimistic_percentage = None
        super()._handle_coordinator_update()
