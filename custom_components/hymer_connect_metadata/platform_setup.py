"""Shared platform-setup dispatcher.

Every HA platform (sensor, binary_sensor, switch, number, select, climate,
light) calls through here with its own platform key.  We:

  1. Wait for the first SignalR frame so we know which slots exist.
  2. Let each matching template claim the slots it owns and return its
     rich entities.
  3. For every remaining slot that maps to this platform's entity kind,
     create a default generic entity.

This way each platform file is ~20 lines.
"""

from __future__ import annotations

import logging
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .capability_resolver import all_capability_specs
from .const import DOMAIN
from .coordinator import HymerConnectCoordinator
from .discovery import SlotMeta, all_slots, component_meta
from .entity_base import (
    HymerBinarySensor,
    HymerButton,
    HymerNumber,
    HymerSelect,
    HymerSensor,
    HymerSwitch,
    HymerText,
    slot_entity_hidden_by_default,
)
from .template_specs import rich_template_claims
from .templates import templates_for_platform

_LOGGER = logging.getLogger(__name__)
_RICH_TEMPLATE_PLATFORM_TAGS = {
    "air_conditioner": "climate",
    "awning": "cover",
    "boiler": "select",
    "climate": "climate",
    "fan": "fan",
    "fridge_level": "select",
    "fridge_power": "switch",
    "heater_energy": "select",
    "light": "light",
}


def _should_keep_generic_slot_for_rich_template(
    platform: str,
    meta: SlotMeta,
    rich_platform: str | None,
) -> bool:
    """Allow selected generic controls to coexist with rich entities.

    Home Assistant's device page does not expose light brightness controls as
    cleanly as a standalone NumberEntity, so we keep raw brightness numbers for
    light components while still suppressing duplicate on/off switches.
    """
    return (
        platform == "number"
        and rich_platform == "light"
        and meta.label == "brightness"
    )


def _rich_template_platform_claims(
    observed: set[tuple[int, int]],
) -> dict[tuple[int, int], str]:
    """Map observed slots claimed by rich templates onto their owning platform."""
    claims: dict[tuple[int, int], str] = {}
    slots = all_slots()
    for component_id in sorted({bus_id for bus_id, _sensor_id in observed}):
        component = component_meta(component_id)
        slots_for_component = {
            sensor_id: meta
            for (bus_id, sensor_id), meta in slots.items()
            if bus_id == component_id
        }
        for sensor_id, tag in rich_template_claims(
            component_id,
            component.kind if component else None,
            slots_for_component,
        ).items():
            platform = _RICH_TEMPLATE_PLATFORM_TAGS.get(tag)
            if platform is None:
                continue
            slot = (component_id, sensor_id)
            if slot in observed:
                claims[slot] = platform
    return claims


def _platform_for_slot(meta: SlotMeta) -> str | None:
    """Return the default HA platform key for a slot based on datatype+mode."""
    if meta.control_platform is not None:
        return meta.control_platform
    if meta.mode == "r":
        return "binary_sensor" if meta.datatype == "bool" else "sensor"
    if meta.mode in ("rw", "w"):
        if meta.datatype == "bool":
            return "switch"
        if meta.datatype in ("int", "float"):
            # Writable numerics → Number (unless a template claims them
            # for a SelectEntity like fridge level).
            return "number"
        if meta.datatype == "string":
            # String writes need explicit control metadata. Otherwise we
            # skip them rather than guessing between switch/select.
            return None
    return None


def _default_class_for(platform: str, meta: SlotMeta):
    if platform == "sensor":
        return HymerSensor
    if platform == "binary_sensor":
        return HymerBinarySensor
    if platform == "switch":
        return HymerSwitch
    if platform == "button":
        return HymerButton
    if platform == "number":
        return HymerNumber
    if platform == "select":
        return HymerSelect
    if platform == "text":
        return HymerText
    return None


def _template_name(template: Any) -> str:
    return template.__class__.__name__


def _entity_unique_id(entity: Any) -> str | None:
    return getattr(entity, "unique_id", None) or getattr(entity, "_attr_unique_id", None)


def _new_slots_might_affect_platform(
    platform: str,
    new_slots: set[tuple[int, int]],
) -> bool:
    if not new_slots:
        return False

    slots = all_slots()
    for slot in new_slots:
        meta = slots.get(slot)
        if meta is not None and _platform_for_slot(meta) == platform:
            return True

    if platform in {"sensor", "binary_sensor", "switch"}:
        for spec in all_capability_specs():
            if spec.platform != platform:
                continue
            if any(candidate.key in new_slots for candidate in spec.candidates):
                return True

    touched_components = {component_id for component_id, _sensor_id in new_slots}
    for component_id in touched_components:
        component = component_meta(component_id)
        slots_for_component = {
            sensor_id: meta
            for (bus_id, sensor_id), meta in slots.items()
            if bus_id == component_id
        }
        claims = rich_template_claims(
            component_id,
            component.kind if component else None,
            slots_for_component,
        )
        if any(
            _RICH_TEMPLATE_PLATFORM_TAGS.get(tag) == platform
            for tag in claims.values()
        ):
            return True
    return False


def _discover_platform_entities(
    coordinator: HymerConnectCoordinator,
    entry: ConfigEntry,
    platform: str,
    observed: set[tuple[int, int]],
) -> tuple[list[Any], dict[str, Any]]:
    entities: list[Any] = []
    claimed: set[tuple[int, int]] = set()
    template_summaries: list[dict[str, Any]] = []
    generic_created = 0
    skipped_unknown = 0
    skipped_platform_mismatch = 0
    skipped_missing_class = 0
    skipped_missing_options = 0
    skipped_rich_template_claim = 0
    skipped_hidden_raw_slot = 0
    rich_template_platform_claims = _rich_template_platform_claims(observed)

    # 1. Templates for this platform claim their slot groups first.
    for template in templates_for_platform(platform):
        tpl_entities, tpl_claimed = template.build(coordinator, entry, observed)
        entities.extend(tpl_entities)
        claimed.update(tpl_claimed)
        template_summaries.append(
            {
                "template": _template_name(template),
                "entity_count": len(tpl_entities),
                "claimed_slot_count": len(tpl_claimed),
                "claimed_slots": [list(slot) for slot in sorted(tpl_claimed)],
            }
        )

    # 2. Generic per-slot entities for anything un-claimed on this platform.
    for key in sorted(observed - claimed):
        meta = all_slots().get(key)
        if meta is None:
            skipped_unknown += 1
            continue  # unknown slot, fall back silently
        if _platform_for_slot(meta) != platform:
            skipped_platform_mismatch += 1
            continue
        if slot_entity_hidden_by_default(meta):
            skipped_hidden_raw_slot += 1
            continue
        rich_platform = rich_template_platform_claims.get(key)
        if rich_platform is not None and not _should_keep_generic_slot_for_rich_template(
            platform,
            meta,
            rich_platform,
        ):
            skipped_rich_template_claim += 1
            continue
        comp = component_meta(key[0])
        cls = _default_class_for(platform, meta)
        if cls is None:
            skipped_missing_class += 1
            continue
        if cls is HymerSelect:
            if not meta.options:
                skipped_missing_options += 1
                continue
            entities.append(cls(coordinator, entry, meta, comp, list(meta.options)))
            generic_created += 1
            continue
        entities.append(cls(coordinator, entry, meta, comp))
        generic_created += 1

    profile = {
        "observed_slot_count": len(observed),
        "claimed_slot_count": len(claimed),
        "entity_count": len(entities),
        "generic_entity_count": generic_created,
        "template_summaries": template_summaries,
        "skipped_unknown": skipped_unknown,
        "skipped_platform_mismatch": skipped_platform_mismatch,
        "skipped_missing_class": skipped_missing_class,
        "skipped_missing_options": skipped_missing_options,
        "skipped_rich_template_claim": skipped_rich_template_claim,
        "skipped_hidden_raw_slot": skipped_hidden_raw_slot,
    }
    return entities, profile


async def setup_platform(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
    platform: str,
) -> None:
    """Generic async_setup_entry shared by every HYMER Connect platform."""
    coordinator: HymerConnectCoordinator = hass.data[DOMAIN][entry.entry_id]

    # Wait for the SCU's first data frame so we know what exists. If the
    # van is offline we fall through with observed={} and register a dynamic
    # refresh callback so later frames can add entities without a full reload.
    await coordinator.wait_for_first_frame(timeout=30.0)
    observed: set[tuple[int, int]] = coordinator.observed_slots

    tracked_unique_ids: set[str] = set()

    def _new_entities(entities: list[Any]) -> list[Any]:
        additions: list[Any] = []
        for entity in entities:
            unique_id = _entity_unique_id(entity)
            if unique_id is not None and unique_id in tracked_unique_ids:
                continue
            if unique_id is not None:
                tracked_unique_ids.add(unique_id)
            additions.append(entity)
        return additions

    entities, profile = _discover_platform_entities(coordinator, entry, platform, observed)
    coordinator.set_platform_discovery_profile(platform, profile)
    initial_entities = _new_entities(entities)

    async def _refresh_platform(new_slots: set[tuple[int, int]]) -> None:
        if not _new_slots_might_affect_platform(platform, new_slots):
            _LOGGER.debug(
                "Skipping %s refresh; %d new slots cannot affect that platform",
                platform,
                len(new_slots),
            )
            return
        refreshed_entities, refreshed_profile = _discover_platform_entities(
            coordinator,
            entry,
            platform,
            coordinator.observed_slots,
        )
        coordinator.set_platform_discovery_profile(platform, refreshed_profile)
        additions = _new_entities(refreshed_entities)
        if additions:
            _LOGGER.info(
                "Discovered %d new %s entities from %d observed slots",
                len(additions), platform, len(coordinator.observed_slots),
            )
            async_add_entities(additions)

    coordinator.register_platform_refresh(platform, _refresh_platform)

    if initial_entities:
        _LOGGER.info(
            "Discovered %d %s entities from %d observed slots",
            len(initial_entities), platform, len(observed),
        )
        if profile["template_summaries"]:
            _LOGGER.debug(
                "Platform %s template claims: %s",
                platform,
                [
                    (
                        summary["template"],
                        summary["entity_count"],
                        summary["claimed_slot_count"],
                    )
                    for summary in profile["template_summaries"]
                ],
            )
        async_add_entities(initial_entities)
    else:
        _LOGGER.debug("No %s entities matched %d observed slots", platform, len(observed))
