"""Button platform — entities are discovered from control metadata."""

from __future__ import annotations

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity import EntityCategory
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import DOMAIN
from .entity_base import HymerRootActionButton
from .platform_setup import setup_platform
from .preferences import admin_actions_enabled


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    coordinator = hass.data[DOMAIN][entry.entry_id]
    async_add_entities(
        [
            HymerRootActionButton(
                coordinator,
                entry,
                key="restart_system",
                name="Restart System",
                icon="mdi:restart-alert",
                entity_category=EntityCategory.CONFIG,
                enabled_default=admin_actions_enabled(entry),
                press_action=coordinator.async_send_restart_system_command,
            )
        ]
    )

    await setup_platform(hass, entry, async_add_entities, platform="button")
