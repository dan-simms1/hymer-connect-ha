"""Truma heater energy-source select.

Supports the five panel-style energy modes:

- Diesel
- Mix 900W
- Mix 1800W
- Electric 900W
- Electric 1800W

Writes slots 4 + 6 as a pair, plus slot 9 for the electric power limit when
the selected mode uses shore power.

Requires shore power for "Electric" — the SCU rejects otherwise.
"""

from __future__ import annotations

import logging
from typing import Any

from homeassistant.components.select import SelectEntity
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from ..discovery import all_components, component_meta
from ..entity_base import _device_info_for_bus
from ..template_specs import heater_energy_spec

_LOGGER = logging.getLogger(__name__)


class HeaterEnergyTemplate:
    PLATFORM = "select"

    def build(self, coordinator, entry, observed):
        entities: list[Any] = []
        claimed: set[tuple[int, int]] = set()
        spec = heater_energy_spec()
        for bus_id, comp in all_components().items():
            if comp.kind not in spec.component_kinds:
                continue
            if not all((bus_id, requirement.sensor_id) in observed for requirement in spec.required_slots):
                continue
            entities.append(HeaterEnergySelect(coordinator, entry, bus_id, spec))
            for requirement in spec.required_slots:
                claimed.add((bus_id, requirement.sensor_id))
            # Power-limit readback remains generic; metadata only defines the
            # send semantics for the rich aggregated select.
        return entities, claimed


class HeaterEnergySelect(CoordinatorEntity, SelectEntity):
    _attr_has_entity_name = True
    _attr_icon = "mdi:gas-station"

    def __init__(self, coordinator, entry, bus_id, spec):
        super().__init__(coordinator)
        self._bus = bus_id
        self._spec = spec
        comp = component_meta(bus_id)
        self._attr_unique_id = f"{entry.entry_id}_heater_energy_b{bus_id}"
        self._attr_translation_key = "heater_energy_ctrl"
        self._attr_name = "Heater Energy Source"
        self._attr_options = list(spec.options)
        self._attr_device_info = _device_info_for_bus(entry.entry_id, bus_id, comp)
        self._optimistic: str | None = None

    def _slot(self, sid: int):
        slots = (self.coordinator.data or {}).get("signalr_slots") or {}
        return slots.get((self._bus, sid))

    @property
    def current_option(self) -> str | None:
        if self._optimistic is not None:
            return self._optimistic
        v = self._slot(self._spec.mode_slot)
        power = self._slot(self._spec.power_slot)
        if v == "Diesel":
            return "Diesel"
        if v in {"Electricity", "Electric"}:
            if power == 1800:
                return "Electric 1800W"
            return "Electric 900W"
        if v == "Both":
            if power == 1800:
                return "Mix 1800W"
            return "Mix 900W"
        return None

    async def async_select_option(self, option: str) -> None:
        option_writes = self._spec.writes.get(option)
        if option_writes is None:
            return
        client = await self.coordinator.async_ensure_signalr_connected()
        writes: list[dict[str, Any]] = []
        for write in option_writes:
            payload = {
                "bus_id": self._bus,
                "sensor_id": write.sensor_id,
            }
            if write.value_type == "str":
                payload["str_value"] = str(write.value)
            else:
                payload["uint_value"] = int(write.value)
            writes.append(payload)
        await client.send_multi_sensor_command(writes)
        self._optimistic = option
        self.async_write_ha_state()

    def _handle_coordinator_update(self) -> None:
        if self._optimistic is not None and self.current_option == self._optimistic:
            self._optimistic = None
        super()._handle_coordinator_update()
