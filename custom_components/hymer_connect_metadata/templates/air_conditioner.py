"""Air-conditioner climate templates.

Supported families:

- standard single-zone climate modules:
  - slot 1: target room temperature
  - slot 2: actual room temperature
  - slot 3: HVAC mode
  - slot 4: fan mode
- Airxcel front/rear climate zones:
  - front mode/fan/speeds on slots 1-3, temperatures on 4/5, ambient on 14
  - rear mode/fan/speeds on slots 17-19, temperatures on 20/21, ambient on 30

The Airxcel family uses separate heat/cool target temperatures, so the climate
entity exposes a normal target temperature in heat/cool modes and a target
temperature range when the controller is in auto heat/cool mode.
"""

from __future__ import annotations

from typing import Any

from homeassistant.components.climate import (
    ClimateEntity,
    ClimateEntityFeature,
    HVACAction,
    HVACMode,
)
from homeassistant.const import ATTR_TEMPERATURE, UnitOfTemperature
from homeassistant.helpers.update_coordinator import CoordinatorEntity

try:
    from homeassistant.components.climate.const import (
        ATTR_TARGET_TEMP_HIGH,
        ATTR_TARGET_TEMP_LOW,
    )
except ImportError:  # pragma: no cover - older HA cores exported these globally
    from homeassistant.const import ATTR_TARGET_TEMP_HIGH, ATTR_TARGET_TEMP_LOW

from ..discovery import all_components, component_meta, slot_meta
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
from ..template_specs import (
    air_conditioner_single_zone_spec,
    airxcel_zone_specs,
    slots_match_requirements,
)

_MODE_TO_HVAC: dict[str, HVACMode] = {
    "OFF": HVACMode.OFF,
    "FAN": HVACMode.FAN_ONLY,
    "VENTILATION": HVACMode.FAN_ONLY,
    "COOL": HVACMode.COOL,
    "HEAT": HVACMode.HEAT,
    "DEHUMIDIFY": HVACMode.DRY,
    "AUTO": HVACMode.AUTO,
}

_HVAC_TO_MODE: dict[HVACMode, str] = {
    HVACMode.OFF: "OFF",
    HVACMode.FAN_ONLY: "FAN",
    HVACMode.COOL: "COOL",
    HVACMode.HEAT: "HEAT",
    HVACMode.DRY: "DEHUMIDIFY",
    HVACMode.AUTO: "AUTO",
}
_HEAT_COOL_MODE = getattr(HVACMode, "HEAT_COOL", HVACMode.AUTO)
_TARGET_RANGE_FEATURE = getattr(ClimateEntityFeature, "TARGET_TEMPERATURE_RANGE", 0)

_AIRXCEL_MODE_TO_HVAC: dict[str, HVACMode] = {
    "OFF": HVACMode.OFF,
    "COOL": HVACMode.COOL,
    "HEAT": HVACMode.HEAT,
    "AUTO_HEAT_COOL": _HEAT_COOL_MODE,
    "FAN_ONLY": HVACMode.FAN_ONLY,
    "AUX_HEAT": HVACMode.HEAT,
}
_AIRXCEL_HVAC_TO_MODE: dict[HVACMode, str] = {
    HVACMode.OFF: "OFF",
    HVACMode.COOL: "COOL",
    HVACMode.HEAT: "HEAT",
    _HEAT_COOL_MODE: "AUTO_HEAT_COOL",
    HVACMode.FAN_ONLY: "FAN_ONLY",
}
_AIRXCEL_FAN_MODES = ["AUTO", "LOW", "MED", "HIGH"]


def _temperature_value(value: Any) -> float | None:
    """Collapse the SCU's absolute-zero sentinel to unavailable."""
    if not isinstance(value, (int, float)):
        return None
    temperature = float(value)
    return temperature if temperature > -273.0 else None


def _mode_to_hvac(mode: str | None) -> HVACMode | None:
    if mode is None:
        return None
    return _MODE_TO_HVAC.get(mode.upper())


def _fan_modes_for_slot(bus_id: int) -> list[str]:
    meta = slot_meta(bus_id, 4)
    return list(meta.options) if meta else []


def _hvac_modes_for_slot(bus_id: int) -> list[HVACMode]:
    meta = slot_meta(bus_id, 3)
    if meta is None:
        return [HVACMode.OFF, HVACMode.COOL]
    ordered: list[HVACMode] = []
    seen: set[HVACMode] = set()
    for option in meta.options:
        hvac = _mode_to_hvac(str(option))
        if hvac is None or hvac in seen:
            continue
        ordered.append(hvac)
        seen.add(hvac)
    if HVACMode.OFF not in seen:
        ordered.insert(0, HVACMode.OFF)
    return ordered or [HVACMode.OFF, HVACMode.COOL]


class AirConditionerClimateTemplate:
    PLATFORM = "climate"

    def build(self, coordinator, entry, observed):
        entities: list[Any] = []
        claimed: set[tuple[int, int]] = set()
        single_zone_spec = air_conditioner_single_zone_spec()
        zone_specs = airxcel_zone_specs()
        for bus_id, comp in all_components().items():
            if comp.kind not in single_zone_spec.component_kinds:
                continue
            matched_zone = False
            for zone_spec in zone_specs:
                if comp.kind not in zone_spec.component_kinds:
                    continue
                if not all((bus_id, req.sensor_id) in observed for req in zone_spec.required_slots):
                    continue
                if not slots_match_requirements(bus_id, zone_spec.required_slots):
                    continue
                entities.append(AirxcelZoneClimate(coordinator, entry, bus_id, zone_spec=zone_spec))
                claimed.update((bus_id, sid) for sid in zone_spec.claim_slots if (bus_id, sid) in observed)
                matched_zone = True
            if matched_zone:
                continue
            if not all((bus_id, requirement.sensor_id) in observed for requirement in single_zone_spec.required_slots):
                continue
            if not slots_match_requirements(bus_id, single_zone_spec.required_slots):
                continue
            mode_meta = slot_meta(bus_id, single_zone_spec.mode_sid)
            if mode_meta is None or mode_meta.datatype not in {"string", "int"}:
                continue
            entities.append(AirConditionerClimate(coordinator, entry, bus_id, spec_name=single_zone_spec.name))
            for sid in single_zone_spec.claim_slots:
                if (bus_id, sid) in observed:
                    claimed.add((bus_id, sid))
        return entities, claimed


class AirConditionerClimate(CoordinatorEntity, ClimateEntity):
    _attr_has_entity_name = True
    _attr_temperature_unit = UnitOfTemperature.CELSIUS
    _attr_target_temperature_step = 1.0
    _attr_min_temp = 16.0
    _attr_max_temp = 32.0
    _attr_icon = "mdi:air-conditioner"

    def __init__(self, coordinator, entry, bus_id: int, spec_name: str) -> None:
        super().__init__(coordinator)
        self._entry = entry
        self._bus = bus_id
        comp = component_meta(bus_id)
        self._mode_meta = slot_meta(bus_id, 3)
        self._fan_meta = slot_meta(bus_id, 4)
        self._attr_unique_id = f"{entry.entry_id}_aircon_b{bus_id}"
        self._attr_name = spec_name
        self._attr_device_info = _device_info_for_bus(entry.entry_id, bus_id, comp)
        self._attr_temperature_unit = temperature_display_unit(entry)
        self._attr_target_temperature_step = display_temperature_step(1.0)
        self._attr_min_temp = display_value(16.0, "°C", entry)
        self._attr_max_temp = display_value(32.0, "°C", entry)
        self._attr_supported_features = ClimateEntityFeature.TARGET_TEMPERATURE
        if self._fan_meta and self._fan_meta.options:
            self._attr_supported_features |= ClimateEntityFeature.FAN_MODE
            self._attr_fan_modes = _fan_modes_for_slot(bus_id)
        self._attr_hvac_modes = _hvac_modes_for_slot(bus_id)
        self._optimistic_mode: str | None = None
        self._optimistic_fan_mode: str | None = None
        self._optimistic_temp: float | None = None

    def _slot(self, sid: int) -> Any:
        slots = (self.coordinator.data or {}).get("signalr_slots") or {}
        return slots.get((self._bus, sid))

    def _mode_option(self) -> str | None:
        if self._optimistic_mode is not None:
            return self._optimistic_mode
        if self._mode_meta is None:
            return None
        return enum_option_for_value(self._slot(3), self._mode_meta.options)

    @property
    def hvac_mode(self) -> HVACMode | None:
        return _mode_to_hvac(self._mode_option())

    @property
    def hvac_action(self) -> HVACAction | None:
        hvac_mode = self.hvac_mode
        if hvac_mode == HVACMode.COOL:
            return HVACAction.COOLING
        if hvac_mode == HVACMode.HEAT:
            return HVACAction.HEATING
        if hvac_mode == HVACMode.DRY:
            return HVACAction.DRYING
        if hvac_mode == HVACMode.FAN_ONLY:
            return HVACAction.FAN
        if hvac_mode == HVACMode.OFF:
            return HVACAction.OFF
        return HVACAction.IDLE

    @property
    def current_temperature(self) -> float | None:
        return display_value(_temperature_value(self._slot(2)), "°C", self._entry)

    @property
    def target_temperature(self) -> float | None:
        if self._optimistic_temp is not None:
            return self._optimistic_temp
        return display_value(_temperature_value(self._slot(1)), "°C", self._entry)

    @property
    def fan_mode(self) -> str | None:
        if self._optimistic_fan_mode is not None:
            return self._optimistic_fan_mode
        if self._fan_meta is None:
            return None
        return enum_option_for_value(self._slot(4), self._fan_meta.options)

    async def async_set_hvac_mode(self, hvac_mode: HVACMode) -> None:
        if self._mode_meta is None:
            return
        mode_option = _HVAC_TO_MODE.get(hvac_mode)
        if mode_option is None:
            return
        wire = enum_wire_value_for_option(
            mode_option,
            datatype=self._mode_meta.datatype,
            options=self._mode_meta.options,
        )
        client = await self.coordinator.async_ensure_signalr_connected()
        if isinstance(wire, str):
            await client.send_light_command(self._bus, 3, str_value=wire)
        else:
            await client.send_light_command(self._bus, 3, uint_value=wire)
        self._optimistic_mode = mode_option
        self.async_write_ha_state()

    async def async_set_temperature(self, **kwargs: Any) -> None:
        temp = kwargs.get(ATTR_TEMPERATURE)
        if temp is None:
            return
        client = await self.coordinator.async_ensure_signalr_connected()
        native_temp = float(native_value_from_display(temp, "°C", self._entry))
        await client.send_multi_sensor_command([
            {"bus_id": self._bus, "sensor_id": 1, "float_value": native_temp},
        ])
        self._optimistic_temp = float(temp)
        self.async_write_ha_state()

    async def async_set_fan_mode(self, fan_mode: str) -> None:
        if self._fan_meta is None:
            return
        wire = enum_wire_value_for_option(
            fan_mode,
            datatype=self._fan_meta.datatype,
            options=self._fan_meta.options,
        )
        client = await self.coordinator.async_ensure_signalr_connected()
        if isinstance(wire, str):
            await client.send_light_command(self._bus, 4, str_value=wire)
        else:
            await client.send_light_command(self._bus, 4, uint_value=wire)
        self._optimistic_fan_mode = fan_mode
        self.async_write_ha_state()

    def _handle_coordinator_update(self) -> None:
        mode_option = self._mode_option() if self._optimistic_mode is None else (
            enum_option_for_value(self._slot(3), self._mode_meta.options)
            if self._mode_meta is not None else None
        )
        if self._optimistic_mode is not None and mode_option == self._optimistic_mode:
            self._optimistic_mode = None

        fan_mode = (
            enum_option_for_value(self._slot(4), self._fan_meta.options)
            if self._fan_meta is not None else None
        )
        if self._optimistic_fan_mode is not None and fan_mode == self._optimistic_fan_mode:
            self._optimistic_fan_mode = None

        target = self._slot(1)
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


class AirxcelZoneClimate(CoordinatorEntity, ClimateEntity):
    _attr_has_entity_name = True
    _attr_temperature_unit = UnitOfTemperature.CELSIUS
    _attr_target_temperature_step = 1.0
    _attr_min_temp = 10.0
    _attr_max_temp = 35.0
    _attr_icon = "mdi:air-conditioner"

    def __init__(self, coordinator, entry, bus_id: int, *, zone_spec) -> None:
        super().__init__(coordinator)
        self._entry = entry
        self._bus = bus_id
        self._zone = zone_spec.zone
        self._mode_sid = zone_spec.mode_sid
        self._fan_mode_sid = zone_spec.fan_mode_sid
        self._fan_speed_sid = zone_spec.fan_speed_sid
        self._heat_sid = zone_spec.heat_target_sid
        self._cool_sid = zone_spec.cool_target_sid
        self._ambient_sid = zone_spec.ambient_sid
        comp = component_meta(bus_id)
        self._mode_meta = slot_meta(bus_id, self._mode_sid)
        self._fan_mode_meta = slot_meta(bus_id, self._fan_mode_sid)
        self._fan_speed_meta = slot_meta(bus_id, self._fan_speed_sid)
        self._attr_unique_id = f"{entry.entry_id}_airxcel_{zone_spec.zone}_b{bus_id}"
        self._attr_name = zone_spec.name
        self._attr_device_info = _device_info_for_bus(entry.entry_id, bus_id, comp)
        self._attr_temperature_unit = temperature_display_unit(entry)
        self._attr_target_temperature_step = display_temperature_step(1.0)
        self._attr_min_temp = display_value(10.0, "°C", entry)
        self._attr_max_temp = display_value(35.0, "°C", entry)
        self._attr_supported_features = (
            ClimateEntityFeature.TARGET_TEMPERATURE
            | ClimateEntityFeature.FAN_MODE
            | _TARGET_RANGE_FEATURE
        )
        self._attr_hvac_modes = self._available_hvac_modes()
        self._attr_fan_modes = _AIRXCEL_FAN_MODES
        self._optimistic_mode: str | None = None
        self._optimistic_fan_mode: str | None = None
        self._optimistic_heat: float | None = None
        self._optimistic_cool: float | None = None

    def _slot(self, sid: int) -> Any:
        slots = (self.coordinator.data or {}).get("signalr_slots") or {}
        return slots.get((self._bus, sid))

    def _available_hvac_modes(self) -> list[HVACMode]:
        if self._mode_meta is None:
            return [HVACMode.OFF, HVACMode.COOL, HVACMode.HEAT]
        ordered: list[HVACMode] = []
        seen: set[HVACMode] = set()
        for option in self._mode_meta.options:
            hvac = _AIRXCEL_MODE_TO_HVAC.get(str(option))
            if hvac is None or hvac in seen:
                continue
            ordered.append(hvac)
            seen.add(hvac)
        if HVACMode.OFF not in seen:
            ordered.insert(0, HVACMode.OFF)
        return ordered or [HVACMode.OFF, HVACMode.COOL, HVACMode.HEAT]

    def _mode_option(self) -> str | None:
        if self._optimistic_mode is not None:
            return self._optimistic_mode
        return self._live_mode_option()

    def _live_mode_option(self) -> str | None:
        if self._mode_meta is None:
            return None
        return enum_option_for_value(self._slot(self._mode_sid), self._mode_meta.options)

    def _heat_target(self) -> float | None:
        if self._optimistic_heat is not None:
            return self._optimistic_heat
        return display_value(
            _temperature_value(self._slot(self._heat_sid)),
            "°C",
            self._entry,
        )

    def _cool_target(self) -> float | None:
        if self._optimistic_cool is not None:
            return self._optimistic_cool
        return display_value(
            _temperature_value(self._slot(self._cool_sid)),
            "°C",
            self._entry,
        )

    def _raw_fan_mode_option(self) -> str | None:
        if self._fan_mode_meta is None:
            return None
        return enum_option_for_value(self._slot(self._fan_mode_sid), self._fan_mode_meta.options)

    def _raw_fan_speed_option(self) -> str | None:
        if self._fan_speed_meta is None:
            return None
        return enum_option_for_value(self._slot(self._fan_speed_sid), self._fan_speed_meta.options)

    def _command_for_sid(self, sid: int, value: Any) -> dict[str, Any]:
        meta = slot_meta(self._bus, sid)
        command: dict[str, Any] = {"bus_id": self._bus, "sensor_id": sid}
        if meta is not None and meta.datatype == "float":
            command["float_value"] = float(value)
        elif meta is not None and meta.datatype == "string":
            command["str_value"] = str(value)
        else:
            command["uint_value"] = int(value)
        return command

    def _mode_wire(self, option: str) -> Any:
        return enum_wire_value_for_option(
            option,
            datatype=self._mode_meta.datatype,
            options=self._mode_meta.options,
        )

    @property
    def hvac_mode(self) -> HVACMode | None:
        option = self._mode_option()
        if option is None:
            return None
        return _AIRXCEL_MODE_TO_HVAC.get(option)

    @property
    def hvac_action(self) -> HVACAction | None:
        mode = self.hvac_mode
        if mode == HVACMode.COOL:
            return HVACAction.COOLING
        if mode == HVACMode.HEAT:
            return HVACAction.HEATING
        if mode == HVACMode.FAN_ONLY:
            return HVACAction.FAN
        if mode == HVACMode.OFF:
            return HVACAction.OFF
        return HVACAction.IDLE

    @property
    def current_temperature(self) -> float | None:
        return display_value(
            _temperature_value(self._slot(self._ambient_sid)),
            "°C",
            self._entry,
        )

    @property
    def target_temperature(self) -> float | None:
        mode = self.hvac_mode
        if mode == HVACMode.HEAT:
            return self._heat_target()
        if mode in {HVACMode.COOL, HVACMode.FAN_ONLY, HVACMode.OFF}:
            return self._cool_target()
        if mode == _HEAT_COOL_MODE:
            return None
        return self._cool_target()

    @property
    def target_temperature_low(self) -> float | None:
        return self._heat_target()

    @property
    def target_temperature_high(self) -> float | None:
        return self._cool_target()

    @property
    def fan_mode(self) -> str | None:
        if self._optimistic_fan_mode is not None:
            return self._optimistic_fan_mode
        return self._live_fan_mode()

    def _live_fan_mode(self) -> str | None:
        raw_mode = self._raw_fan_mode_option()
        if raw_mode == "AUTO":
            return "AUTO"
        return self._raw_fan_speed_option()

    async def async_set_hvac_mode(self, hvac_mode: HVACMode) -> None:
        if self._mode_meta is None:
            return
        mode_option = _AIRXCEL_HVAC_TO_MODE.get(hvac_mode)
        if mode_option is None:
            return
        client = await self.coordinator.async_ensure_signalr_connected()
        await client.send_multi_sensor_command([
            self._command_for_sid(self._mode_sid, self._mode_wire(mode_option)),
        ])
        self._optimistic_mode = mode_option
        self.async_write_ha_state()

    async def async_set_temperature(self, **kwargs: Any) -> None:
        writes: list[dict[str, Any]] = []
        target_low = kwargs.get(ATTR_TARGET_TEMP_LOW)
        target_high = kwargs.get(ATTR_TARGET_TEMP_HIGH)
        target = kwargs.get(ATTR_TEMPERATURE)

        if target_low is not None:
            writes.append(
                self._command_for_sid(
                    self._heat_sid,
                    native_value_from_display(target_low, "°C", self._entry),
                )
            )
            self._optimistic_heat = float(target_low)
        if target_high is not None:
            writes.append(
                self._command_for_sid(
                    self._cool_sid,
                    native_value_from_display(target_high, "°C", self._entry),
                )
            )
            self._optimistic_cool = float(target_high)

        if target is not None:
            if self.hvac_mode == HVACMode.HEAT:
                writes.append(
                    self._command_for_sid(
                        self._heat_sid,
                        native_value_from_display(target, "°C", self._entry),
                    )
                )
                self._optimistic_heat = float(target)
            else:
                writes.append(
                    self._command_for_sid(
                        self._cool_sid,
                        native_value_from_display(target, "°C", self._entry),
                    )
                )
                self._optimistic_cool = float(target)

        if not writes:
            return
        client = await self.coordinator.async_ensure_signalr_connected()
        await client.send_multi_sensor_command(writes)
        self.async_write_ha_state()

    async def async_set_fan_mode(self, fan_mode: str) -> None:
        if self._fan_mode_meta is None:
            return
        writes: list[dict[str, Any]]
        if fan_mode == "AUTO":
            wire = enum_wire_value_for_option(
                "AUTO",
                datatype=self._fan_mode_meta.datatype,
                options=self._fan_mode_meta.options,
            )
            writes = [self._command_for_sid(self._fan_mode_sid, wire)]
        else:
            if self._fan_speed_meta is None or fan_mode not in self._fan_speed_meta.options:
                return
            mode_wire = enum_wire_value_for_option(
                "ON",
                datatype=self._fan_mode_meta.datatype,
                options=self._fan_mode_meta.options,
            )
            speed_wire = enum_wire_value_for_option(
                fan_mode,
                datatype=self._fan_speed_meta.datatype,
                options=self._fan_speed_meta.options,
            )
            writes = [
                self._command_for_sid(self._fan_mode_sid, mode_wire),
                self._command_for_sid(self._fan_speed_sid, speed_wire),
            ]
        client = await self.coordinator.async_ensure_signalr_connected()
        await client.send_multi_sensor_command(writes)
        self._optimistic_fan_mode = fan_mode
        self.async_write_ha_state()

    def _handle_coordinator_update(self) -> None:
        if self._optimistic_mode is not None and self._live_mode_option() == self._optimistic_mode:
            self._optimistic_mode = None
        if self._optimistic_fan_mode is not None and self._live_fan_mode() == self._optimistic_fan_mode:
            self._optimistic_fan_mode = None

        heat_target = self._slot(self._heat_sid)
        if (
            self._optimistic_heat is not None
            and isinstance(heat_target, (int, float))
            and abs(
                float(display_value(heat_target, "°C", self._entry))
                - self._optimistic_heat
            ) < 0.5
        ):
            self._optimistic_heat = None

        cool_target = self._slot(self._cool_sid)
        if (
            self._optimistic_cool is not None
            and isinstance(cool_target, (int, float))
            and abs(
                float(display_value(cool_target, "°C", self._entry))
                - self._optimistic_cool
            ) < 0.5
        ):
            self._optimistic_cool = None
        super()._handle_coordinator_update()
