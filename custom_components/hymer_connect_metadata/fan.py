"""Fan platform for aggregated fan-style entities."""

from __future__ import annotations

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import DOMAIN
from .templates import templates_for_platform


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up template-backed fan entities for a config entry."""
    coordinator = hass.data[DOMAIN][entry.entry_id]
    await coordinator.wait_for_first_frame(timeout=30.0)
    observed: set[tuple[int, int]] = coordinator.observed_slots

    entities = []
    for template in templates_for_platform("fan"):
        tpl_entities, _claimed = template.build(coordinator, entry, observed)
        entities.extend(tpl_entities)

    if entities:
        async_add_entities(entities)
