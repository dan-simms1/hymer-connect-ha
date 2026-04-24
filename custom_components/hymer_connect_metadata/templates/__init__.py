"""Component-family templates.

Each template inspects the set of observed (bus_id, sensor_id) slots and,
if it recognises a component family, emits one or more aggregated HA
entities (LightEntity, ClimateEntity, CoverEntity, FanEntity, SelectEntity)
that span several slots. Templates *claim* slots so the generic discovery
layer won't emit duplicate per-slot entities for the same hardware.
"""

from __future__ import annotations

from typing import Any, Protocol

from homeassistant.config_entries import ConfigEntry


class Template(Protocol):
    """Template protocol — inspect observed slots, build entities."""

    PLATFORM: str  # "light" | "climate" | "cover" | "fan" | "select" | ...

    def build(
        self,
        coordinator: Any,
        entry: ConfigEntry,
        observed: set[tuple[int, int]],
    ) -> tuple[list[Any], set[tuple[int, int]]]:
        """Return (entities, claimed_slots).

        `entities` is a list of HA entity instances to add on this platform.
        `claimed_slots` is the set of (bus_id, sensor_id) pairs this template
        has consumed — the generic layer will skip them.
        """
        ...


def _all_templates():
    # Imported lazily to avoid circular imports during HA startup
    from . import (
        air_conditioner,
        awning,
        boiler,
        canonical,
        climate,
        fan,
        fridge,
        heater_energy,
        light,
    )
    return (
        canonical.CanonicalSensorTemplate(),
        canonical.CanonicalBinarySensorTemplate(),
        canonical.CanonicalSwitchTemplate(),
        awning.AwningCoverTemplate(),
        fan.FanTemplate(),
        light.LightTemplate(),
        fridge.FridgePowerTemplate(),
        fridge.FridgeLevelTemplate(),
        air_conditioner.AirConditionerClimateTemplate(),
        climate.TrumaClimateTemplate(),
        climate.ModernHeaterClimateTemplate(),
        boiler.BoilerSelectTemplate(),
        heater_energy.HeaterEnergyTemplate(),
    )


def templates_for_platform(platform: str) -> list[Template]:
    return [t for t in _all_templates() if t.PLATFORM == platform]
