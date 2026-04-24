"""Sensor platform — plus the REST-based vehicle metadata entities.

Dynamic sensors are discovered from observed SCU slots; see platform_setup.py.
The three REST-backed entities below (vehicle model / year / VIN) are static
and live outside the discovery flow.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from homeassistant.components.sensor import SensorEntity, SensorEntityDescription
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN
from .coordinator import HymerConnectCoordinator
from .entity_base import root_device_info
from .platform_setup import setup_platform


@dataclass(frozen=True, kw_only=True)
class _RestDescription(SensorEntityDescription):
    value_path: str


_REST_SENSORS: tuple[_RestDescription, ...] = (
    _RestDescription(key="vehicle_model", translation_key="vehicle_model",
                     value_path="model", icon="mdi:rv-truck"),
    _RestDescription(key="vehicle_model_year", translation_key="vehicle_model_year",
                     value_path="model_year", icon="mdi:calendar"),
    _RestDescription(key="vehicle_vin", translation_key="vehicle_vin",
                     value_path="vin", icon="mdi:identifier"),
)


def _resolve_path(data: dict[str, Any], path: str) -> Any:
    """Dot-separated path resolver, retained for compatibility with other files."""
    cur: Any = data
    for key in path.split("."):
        if isinstance(cur, dict):
            cur = cur.get(key)
        else:
            return None
        if cur is None:
            return None
    return cur


class _RestSensor(CoordinatorEntity[HymerConnectCoordinator], SensorEntity):
    _attr_has_entity_name = True

    def __init__(self, coordinator, desc: _RestDescription, entry: ConfigEntry):
        super().__init__(coordinator)
        self.entity_description = desc
        self._attr_unique_id = f"{entry.entry_id}_{desc.key}"
        self._attr_device_info = root_device_info(entry)

    @property
    def native_value(self):
        if self.coordinator.data is None:
            return None
        return _resolve_path(self.coordinator.data, self.entity_description.value_path)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    coordinator: HymerConnectCoordinator = hass.data[DOMAIN][entry.entry_id]
    # REST-metadata entities: always create, no discovery needed.
    async_add_entities(_RestSensor(coordinator, d, entry) for d in _REST_SENSORS)
    # Discovery-driven entities
    await setup_platform(hass, entry, async_add_entities, platform="sensor")
