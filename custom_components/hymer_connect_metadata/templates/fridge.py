"""Fridge templates exposing separate power and temperature controls."""

from __future__ import annotations

from typing import Any

from ..discovery import all_components, component_meta, slot_meta
from ..entity_base import HymerSelect, HymerSwitch
from ..template_specs import fridge_level_spec, fridge_power_spec, slots_match_requirements


class FridgePowerSwitch(HymerSwitch):
    """Template-owned fridge power switch."""

    def __init__(self, coordinator, entry, meta, component):
        super().__init__(coordinator, entry, meta, component)
        self._attr_unique_id = f"{entry.entry_id}_fridge_power_b{meta.bus_id}"


class FridgeLevelSelect(HymerSelect):
    """Template-owned fridge temperature select."""

    def __init__(self, coordinator, entry, meta, component, options):
        super().__init__(coordinator, entry, meta, component, options)
        self._attr_unique_id = f"{entry.entry_id}_fridge_level_b{meta.bus_id}"


class FridgePowerTemplate:
    PLATFORM = "switch"

    def build(self, coordinator, entry, observed):
        entities: list[Any] = []
        claimed: set[tuple[int, int]] = set()
        spec = fridge_power_spec()
        observed_buses = {bus_id for bus_id, _sensor_id in observed}
        for bus_id, comp in all_components().items():
            if bus_id not in observed_buses:
                continue
            if comp.kind not in spec.component_kinds:
                continue
            if not slots_match_requirements(bus_id, spec.required_slots):
                continue
            meta = slot_meta(bus_id, spec.power_slot)
            if meta is None:
                continue
            entities.append(
                FridgePowerSwitch(coordinator, entry, meta, component_meta(bus_id))
            )
            claimed.add((bus_id, spec.power_slot))
        return entities, claimed


class FridgeLevelTemplate:
    PLATFORM = "select"

    def build(self, coordinator, entry, observed):
        entities: list[Any] = []
        claimed: set[tuple[int, int]] = set()
        spec = fridge_level_spec()
        observed_buses = {bus_id for bus_id, _sensor_id in observed}
        for bus_id, comp in all_components().items():
            if bus_id not in observed_buses:
                continue
            if comp.kind not in spec.component_kinds:
                continue
            if not slots_match_requirements(bus_id, spec.required_slots):
                continue
            meta = slot_meta(bus_id, spec.level_slot)
            if meta is None or not meta.options:
                continue
            entities.append(
                FridgeLevelSelect(
                    coordinator,
                    entry,
                    meta,
                    component_meta(bus_id),
                    list(meta.options),
                )
            )
            claimed.add((bus_id, spec.level_slot))
        return entities, claimed
