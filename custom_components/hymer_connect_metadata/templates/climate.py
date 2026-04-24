"""Heater climate templates.

Supported families:

- panel-style Truma Combi heaters (`kind="truma_heater"`)
- Truma Neo / Neo_E air heaters (`kind="heater_neo"`)
- Timberline zone heaters (`kind="heater"`, zone component layout)

The panel-style Truma family uses the historical slot-8 setpoint/off-sentinel
behaviour. The Neo and Timberline families use explicit enum mode slots and
conventional target/current temperature slots.
"""

from __future__ import annotations

import logging
from typing import Any

from homeassistant.components.climate import (
    ClimateEntity,
    ClimateEntityFeature,
    HVACAction,
    HVACMode,
)
from homeassistant.const import ATTR_TEMPERATURE, UnitOfTemperature
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from ..discovery import all_components, component_meta
from ..discovery import slot_meta
from ..entity_base import (
    _device_info_for_bus,
    enum_option_for_value,
    enum_wire_value_for_option,
)
from ..preferences import (
    display_temperature_step,
    display_value,
    native_value_from_display,
    temperature_display_unit,
)
from ..template_specs import modern_heater_specs, truma_panel_heater_spec

_LOGGER = logging.getLogger(__name__)

MIN_TEMP = 5.0
MAX_TEMP = 30.0
TEMP_STEP = 1.0
OFF_SETPOINT = -273.0
MODERN_MIN_TEMP = 5.0
MODERN_MAX_TEMP = 35.0
MODERN_TEMP_STEP = 1.0

_NEO_MODE_TO_HVAC: dict[str, HVACMode] = {
    "OFF": HVACMode.OFF,
    "HEATING": HVACMode.HEAT,
    "VENTILATING": HVACMode.FAN_ONLY,
}
_NEO_HVAC_TO_MODE: dict[HVACMode, str] = {
    HVACMode.OFF: "OFF",
    HVACMode.HEAT: "HEATING",
    HVACMode.FAN_ONLY: "VENTILATING",
}
_TIMBERLINE_MODE_TO_HVAC: dict[str, HVACMode] = {
    "OFF": HVACMode.OFF,
    "HEAT": HVACMode.HEAT,
    "FAN_ONLY": HVACMode.FAN_ONLY,
}
_TIMBERLINE_HVAC_TO_MODE: dict[HVACMode, str] = {
    HVACMode.OFF: "OFF",
    HVACMode.HEAT: "HEAT",
    HVACMode.FAN_ONLY: "FAN_ONLY",
}


def _temperature_value(value: Any) -> float | None:
    """Collapse the SCU's absolute-zero sentinel to unavailable."""
    if not isinstance(value, (int, float)):
        return None
    temperature = float(value)
    return temperature if temperature > OFF_SETPOINT else None


class TrumaClimateTemplate:
    PLATFORM = "climate"

    def build(self, coordinator, entry, observed):
        entities: list[Any] = []
        claimed: set[tuple[int, int]] = set()
        spec = truma_panel_heater_spec()
        for bus_id, comp in all_components().items():
            if comp.kind not in spec.component_kinds:
                continue
            if not all((bus_id, requirement.sensor_id) in observed for requirement in spec.required_slots):
                continue
            entities.append(TrumaClimate(coordinator, entry, bus_id))
            for sid in spec.claim_slots:
                if (bus_id, sid) in observed:
                    claimed.add((bus_id, sid))
        return entities, claimed


class TrumaClimate(CoordinatorEntity, ClimateEntity):
    _attr_has_entity_name = True
    _attr_temperature_unit = UnitOfTemperature.CELSIUS
    _attr_target_temperature_step = TEMP_STEP
    _attr_min_temp = MIN_TEMP
    _attr_max_temp = MAX_TEMP
    _attr_hvac_modes = [HVACMode.OFF, HVACMode.HEAT]
    _attr_supported_features = ClimateEntityFeature.TARGET_TEMPERATURE
    _attr_icon = "mdi:radiator"

    def __init__(self, coordinator, entry, bus_id):
        super().__init__(coordinator)
        self._entry = entry
        self._bus = bus_id
        comp = component_meta(bus_id)
        self._attr_unique_id = f"{entry.entry_id}_heater_b{bus_id}"
        self._attr_name = "Heater"
        self._attr_device_info = _device_info_for_bus(entry.entry_id, bus_id, comp)
        self._attr_temperature_unit = temperature_display_unit(entry)
        self._attr_target_temperature_step = display_temperature_step(TEMP_STEP)
        self._attr_min_temp = display_value(MIN_TEMP, "°C", entry)
        self._attr_max_temp = display_value(MAX_TEMP, "°C", entry)
        self._optimistic_mode: HVACMode | None = None
        self._optimistic_temp: float | None = None

    def _slot(self, sid: int):
        slots = (self.coordinator.data or {}).get("signalr_slots") or {}
        return slots.get((self._bus, sid))

    def _setpoint(self) -> float | None:
        return _temperature_value(self._slot(8))

    def _energy_source(self) -> str | None:
        for sensor_id in (6, 4):
            value = self._slot(sensor_id)
            if isinstance(value, str) and value not in {"unknown", "unavailable"}:
                return value
        return None

    def _writes_for_setpoint(self, target: float) -> list[dict[str, Any]]:
        writes: list[dict[str, Any]] = [
            {"bus_id": self._bus, "sensor_id": 8, "float_value": float(target)},
        ]
        fuel = self._energy_source()
        if fuel is not None:
            writes.append({"bus_id": self._bus, "sensor_id": 6, "str_value": fuel})
        return writes

    @property
    def hvac_mode(self) -> HVACMode:
        if self._optimistic_mode is not None:
            return self._optimistic_mode
        sp = self._setpoint()
        return HVACMode.HEAT if (sp is not None and sp > OFF_SETPOINT) else HVACMode.OFF

    @property
    def hvac_action(self) -> HVACAction:
        return HVACAction.HEATING if self.hvac_mode == HVACMode.HEAT else HVACAction.OFF

    @property
    def current_temperature(self) -> float | None:
        # No indoor air-temperature sensor on the SCU.  Returning None avoids
        # misleading readings from unrelated slots.
        return None

    @property
    def target_temperature(self) -> float | None:
        if self._optimistic_temp is not None:
            return self._optimistic_temp
        sp = self._setpoint()
        if sp is None or sp <= OFF_SETPOINT:
            return None
        return display_value(sp, "°C", self._entry)

    async def async_set_hvac_mode(self, hvac_mode: HVACMode) -> None:
        client = await self.coordinator.async_ensure_signalr_connected()
        if hvac_mode == HVACMode.HEAT:
            temp = self._optimistic_temp or display_value(
                self._setpoint() or 20.0,
                "°C",
                self._entry,
            )
            if temp <= OFF_SETPOINT:
                temp = display_value(20.0, "°C", self._entry)
            await client.send_multi_sensor_command(
                self._writes_for_setpoint(
                    float(native_value_from_display(temp, "°C", self._entry))
                )
            )
            self._optimistic_mode = HVACMode.HEAT
            self._optimistic_temp = temp
        else:
            await client.send_multi_sensor_command(
                self._writes_for_setpoint(OFF_SETPOINT)
            )
            self._optimistic_mode = HVACMode.OFF
            self._optimistic_temp = None
        self.async_write_ha_state()

    async def async_set_temperature(self, **kwargs):
        temp = kwargs.get(ATTR_TEMPERATURE)
        if temp is None:
            return
        client = await self.coordinator.async_ensure_signalr_connected()
        native_temp = float(native_value_from_display(temp, "°C", self._entry))
        await client.send_multi_sensor_command(
            self._writes_for_setpoint(native_temp)
        )
        self._optimistic_mode = HVACMode.HEAT
        self._optimistic_temp = float(temp)
        self.async_write_ha_state()

    def _handle_coordinator_update(self) -> None:
        if self._optimistic_mode is not None:
            sp = self._setpoint()
            if sp is not None:
                if self._optimistic_mode == HVACMode.OFF and sp <= OFF_SETPOINT:
                    self._optimistic_mode = None
                    self._optimistic_temp = None
                elif (
                    self._optimistic_mode == HVACMode.HEAT
                    and sp > OFF_SETPOINT
                    and self._optimistic_temp is not None
                    and abs(
                        float(display_value(sp, "°C", self._entry))
                        - self._optimistic_temp
                    ) < 0.5
                ):
                    self._optimistic_mode = None
                    self._optimistic_temp = None
        super()._handle_coordinator_update()


class ModernHeaterClimateTemplate:
    PLATFORM = "climate"

    def build(self, coordinator, entry, observed):
        entities: list[Any] = []
        claimed: set[tuple[int, int]] = set()
        specs = modern_heater_specs()
        for bus_id, comp in all_components().items():
            for spec in specs:
                if comp.kind not in spec.component_kinds:
                    continue
                if not all((bus_id, requirement.sensor_id) in observed for requirement in spec.required_slots):
                    continue
                mode_meta = slot_meta(bus_id, spec.mode_sid)
                target_meta = slot_meta(bus_id, spec.target_sid)
                if mode_meta is None or not mode_meta.options:
                    continue
                    continue
                if target_meta is None or target_meta.datatype not in {"int", "float"}:
                    continue
                if spec.variant == "heater_neo":
                    mode_to_hvac = _NEO_MODE_TO_HVAC
                    hvac_to_mode = _NEO_HVAC_TO_MODE
                elif spec.variant == "heater_zone":
                    mode_to_hvac = _TIMBERLINE_MODE_TO_HVAC
                    hvac_to_mode = _TIMBERLINE_HVAC_TO_MODE
                else:
                    continue
                entities.append(
                    ModernEnumHeaterClimate(
                        coordinator=coordinator,
                        entry=entry,
                        bus_id=bus_id,
                        name=spec.name,
                        unique_suffix=spec.unique_suffix,
                        target_sid=spec.target_sid,
                        current_sid=spec.current_sid,
                        mode_sid=spec.mode_sid,
                        fan_sid=spec.fan_sid,
                        mode_to_hvac=mode_to_hvac,
                        hvac_to_mode=hvac_to_mode,
                    )
                )
                for sid in spec.claim_slots:
                    if (bus_id, sid) in observed:
                        claimed.add((bus_id, sid))
        return entities, claimed


class ModernEnumHeaterClimate(CoordinatorEntity, ClimateEntity):
    _attr_has_entity_name = True
    _attr_temperature_unit = UnitOfTemperature.CELSIUS
    _attr_target_temperature_step = MODERN_TEMP_STEP
    _attr_min_temp = MODERN_MIN_TEMP
    _attr_max_temp = MODERN_MAX_TEMP
    _attr_icon = "mdi:radiator"

    def __init__(
        self,
        *,
        coordinator,
        entry,
        bus_id: int,
        name: str,
        unique_suffix: str,
        target_sid: int,
        current_sid: int,
        mode_sid: int,
        fan_sid: int | None,
        mode_to_hvac: dict[str, HVACMode],
        hvac_to_mode: dict[HVACMode, str],
    ) -> None:
        super().__init__(coordinator)
        self._entry = entry
        self._bus = bus_id
        self._target_sid = target_sid
        self._current_sid = current_sid
        self._mode_sid = mode_sid
        self._fan_sid = fan_sid
        self._mode_to_hvac = mode_to_hvac
        self._hvac_to_mode = hvac_to_mode
        self._mode_meta = slot_meta(bus_id, mode_sid)
        self._target_meta = slot_meta(bus_id, target_sid)
        self._fan_meta = slot_meta(bus_id, fan_sid) if fan_sid is not None else None
        comp = component_meta(bus_id)
        self._attr_unique_id = f"{entry.entry_id}_{unique_suffix}_b{bus_id}"
        self._attr_name = name
        self._attr_device_info = _device_info_for_bus(entry.entry_id, bus_id, comp)
        self._attr_temperature_unit = temperature_display_unit(entry)
        self._attr_target_temperature_step = display_temperature_step(MODERN_TEMP_STEP)
        self._attr_min_temp = display_value(MODERN_MIN_TEMP, "°C", entry)
        self._attr_max_temp = display_value(MODERN_MAX_TEMP, "°C", entry)
        self._attr_supported_features = ClimateEntityFeature.TARGET_TEMPERATURE
        self._attr_hvac_modes = self._available_hvac_modes()
        if self._fan_meta and self._fan_meta.options:
            self._attr_supported_features |= ClimateEntityFeature.FAN_MODE
            self._attr_fan_modes = list(self._fan_meta.options)
        self._optimistic_mode: str | None = None
        self._optimistic_fan_mode: str | None = None
        self._optimistic_temp: float | None = None

    def _slot(self, sid: int) -> Any:
        slots = (self.coordinator.data or {}).get("signalr_slots") or {}
        return slots.get((self._bus, sid))

    def _available_hvac_modes(self) -> list[HVACMode]:
        if self._mode_meta is None:
            return [HVACMode.OFF, HVACMode.HEAT]
        ordered: list[HVACMode] = []
        seen: set[HVACMode] = set()
        for option in self._mode_meta.options:
            hvac = self._mode_to_hvac.get(str(option))
            if hvac is None or hvac in seen:
                continue
            ordered.append(hvac)
            seen.add(hvac)
        if HVACMode.OFF not in seen:
            ordered.insert(0, HVACMode.OFF)
        return ordered or [HVACMode.OFF, HVACMode.HEAT]

    def _mode_option(self) -> str | None:
        if self._optimistic_mode is not None:
            return self._optimistic_mode
        if self._mode_meta is None:
            return None
        return enum_option_for_value(self._slot(self._mode_sid), self._mode_meta.options)

    def _fan_option(self) -> str | None:
        if self._optimistic_fan_mode is not None:
            return self._optimistic_fan_mode
        if self._fan_meta is None or self._fan_sid is None:
            return None
        return enum_option_for_value(self._slot(self._fan_sid), self._fan_meta.options)

    def _command_for_slot(self, sid: int, value: Any) -> dict[str, Any]:
        meta = slot_meta(self._bus, sid)
        command: dict[str, Any] = {"bus_id": self._bus, "sensor_id": sid}
        if meta is not None and meta.datatype == "float":
            command["float_value"] = float(value)
        elif meta is not None and meta.datatype == "string":
            command["str_value"] = str(value)
        elif meta is not None and meta.datatype == "bool":
            command["bool_value"] = bool(value)
        else:
            command["uint_value"] = int(value)
        return command

    async def _send_enum(self, sid: int, meta, option: str) -> None:
        if meta is None:
            return
        wire = enum_wire_value_for_option(
            option,
            datatype=meta.datatype,
            options=meta.options,
        )
        client = await self.coordinator.async_ensure_signalr_connected()
        await client.send_multi_sensor_command([self._command_for_slot(sid, wire)])

    @property
    def hvac_mode(self) -> HVACMode | None:
        option = self._mode_option()
        if option is None:
            return None
        return self._mode_to_hvac.get(option)

    @property
    def hvac_action(self) -> HVACAction | None:
        hvac_mode = self.hvac_mode
        if hvac_mode == HVACMode.HEAT:
            return HVACAction.HEATING
        if hvac_mode == HVACMode.FAN_ONLY:
            return HVACAction.FAN
        if hvac_mode == HVACMode.OFF:
            return HVACAction.OFF
        return HVACAction.IDLE

    @property
    def current_temperature(self) -> float | None:
        return display_value(
            _temperature_value(self._slot(self._current_sid)),
            "°C",
            self._entry,
        )

    @property
    def target_temperature(self) -> float | None:
        if self._optimistic_temp is not None:
            return self._optimistic_temp
        return display_value(
            _temperature_value(self._slot(self._target_sid)),
            "°C",
            self._entry,
        )

    @property
    def fan_mode(self) -> str | None:
        return self._fan_option()

    async def async_set_hvac_mode(self, hvac_mode: HVACMode) -> None:
        if self._mode_meta is None:
            return
        option = self._hvac_to_mode.get(hvac_mode)
        if option is None:
            return
        wire = enum_wire_value_for_option(
            option,
            datatype=self._mode_meta.datatype,
            options=self._mode_meta.options,
        )
        writes = [self._command_for_slot(self._mode_sid, wire)]
        if hvac_mode == HVACMode.HEAT and self.target_temperature is None:
            writes.append(self._command_for_slot(self._target_sid, 20))
            self._optimistic_temp = 20.0
        client = await self.coordinator.async_ensure_signalr_connected()
        await client.send_multi_sensor_command(writes)
        self._optimistic_mode = option
        self.async_write_ha_state()

    async def async_set_temperature(self, **kwargs: Any) -> None:
        temp = kwargs.get(ATTR_TEMPERATURE)
        if temp is None:
            return
        native_temp = native_value_from_display(temp, "°C", self._entry)
        writes = [self._command_for_slot(self._target_sid, native_temp)]
        if self.hvac_mode == HVACMode.OFF and self._mode_meta is not None:
            heat_option = self._hvac_to_mode.get(HVACMode.HEAT)
            if heat_option is not None:
                heat_wire = enum_wire_value_for_option(
                    heat_option,
                    datatype=self._mode_meta.datatype,
                    options=self._mode_meta.options,
                )
                writes.append(self._command_for_slot(self._mode_sid, heat_wire))
                self._optimistic_mode = heat_option
        client = await self.coordinator.async_ensure_signalr_connected()
        await client.send_multi_sensor_command(writes)
        self._optimistic_temp = float(temp)
        self.async_write_ha_state()

    async def async_set_fan_mode(self, fan_mode: str) -> None:
        if self._fan_sid is None or self._fan_meta is None:
            return
        await self._send_enum(self._fan_sid, self._fan_meta, fan_mode)
        self._optimistic_fan_mode = fan_mode
        self.async_write_ha_state()

    def _handle_coordinator_update(self) -> None:
        if self._optimistic_mode is not None and self._mode_option() == self._optimistic_mode:
            self._optimistic_mode = None
        if self._optimistic_fan_mode is not None and self._fan_option() == self._optimistic_fan_mode:
            self._optimistic_fan_mode = None
        target = self._slot(self._target_sid)
        if (
            self._optimistic_temp is not None
            and isinstance(target, (int, float))
            and abs(
                float(display_value(target, "°C", self._entry))
                - self._optimistic_temp
            ) < 0.5
        ):
            self._optimistic_temp = None
        super()._handle_coordinator_update()
