"""Truma water boiler mode select — Off / ECO / Turbo on slot 5 of a
kind="truma_heater" component.
"""

from __future__ import annotations

import logging
from typing import Any

from homeassistant.components.select import SelectEntity
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from ..discovery import all_components, component_meta
from ..entity_base import _device_info_for_bus
from ..template_specs import boiler_mode_spec

_LOGGER = logging.getLogger(__name__)


class BoilerSelectTemplate:
    PLATFORM = "select"

    def build(self, coordinator, entry, observed):
        entities: list[Any] = []
        claimed: set[tuple[int, int]] = set()
        spec = boiler_mode_spec()
        for bus_id, comp in all_components().items():
            if comp.kind not in spec.component_kinds:
                continue
            if not all((bus_id, requirement.sensor_id) in observed for requirement in spec.required_slots):
                continue
            entities.append(BoilerSelect(coordinator, entry, bus_id, spec))
            for requirement in spec.required_slots:
                claimed.add((bus_id, requirement.sensor_id))
        return entities, claimed


class BoilerSelect(CoordinatorEntity, SelectEntity):
    _attr_has_entity_name = True
    _attr_icon = "mdi:water-boiler"

    def __init__(self, coordinator, entry, bus_id, spec):
        super().__init__(coordinator)
        self._bus = bus_id
        self._spec = spec
        comp = component_meta(bus_id)
        self._attr_unique_id = f"{entry.entry_id}_boiler_mode_b{bus_id}"
        self._attr_translation_key = "boiler_mode_ctrl"
        self._attr_name = "Warm Water Boiler"
        self._attr_options = list(spec.options)
        self._attr_device_info = _device_info_for_bus(entry.entry_id, bus_id, comp)
        self._optimistic: str | None = None

    def _slot(self, sid: int):
        slots = (self.coordinator.data or {}).get("signalr_slots") or {}
        return slots.get((self._bus, sid))

    def _energy_source(self) -> str:
        v = self._slot(self._spec.energy_source_slot)
        if isinstance(v, str) and v not in ("unknown", "unavailable"):
            return v
        return "Diesel"

    @property
    def current_option(self) -> str | None:
        if self._optimistic is not None:
            return self._optimistic
        v = self._slot(self._spec.mode_slot)
        if not isinstance(v, str):
            return None
        reverse = {wire: option for option, wire in self._spec.wire_map.items()}
        return reverse.get(v.upper(), "Off")

    async def async_select_option(self, option: str) -> None:
        if option not in self._spec.wire_map:
            return
        client = await self.coordinator.async_ensure_signalr_connected()
        fuel = self._energy_source()
        await client.send_multi_sensor_command([
            {"bus_id": self._bus, "sensor_id": self._spec.mode_slot, "str_value": self._spec.wire_map[option]},
            {"bus_id": self._bus, "sensor_id": self._spec.energy_source_slot, "str_value": fuel},
        ])
        self._optimistic = option
        self.async_write_ha_state()

    def _handle_coordinator_update(self) -> None:
        if self._optimistic is not None and self.current_option == self._optimistic:
            self._optimistic = None
        super()._handle_coordinator_update()
