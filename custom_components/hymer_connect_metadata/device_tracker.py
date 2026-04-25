"""Device tracker platform for HYMER Connect."""

from __future__ import annotations

import logging

from homeassistant.components.device_tracker import SourceType
from homeassistant.components.device_tracker.config_entry import TrackerEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN, MANUFACTURER, VEHICLE_ENTITY_PICTURE
from .entity_base import entry_vehicle_display_name
from .coordinator import HymerConnectCoordinator
from .discovery import slot_meta

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up HYMER Connect device tracker from a config entry."""
    coordinator: HymerConnectCoordinator = hass.data[DOMAIN][entry.entry_id]
    async_add_entities([HymerDeviceTracker(coordinator, entry)])


class HymerDeviceTracker(
    CoordinatorEntity[HymerConnectCoordinator], TrackerEntity
):
    """Representation of the HYMER vehicle location."""

    _attr_has_entity_name = True
    _attr_entity_category = None
    _attr_name = "Location"
    _attr_icon = "mdi:rv-truck"
    _attr_entity_picture = VEHICLE_ENTITY_PICTURE

    def __init__(
        self,
        coordinator: HymerConnectCoordinator,
        entry: ConfigEntry,
    ) -> None:
        """Initialize the device tracker."""
        super().__init__(coordinator)
        self._attr_unique_id = f"{entry.entry_id}_device_tracker"
        self._attr_device_info = {
            "identifiers": {(DOMAIN, entry.entry_id)},
            "name": entry_vehicle_display_name(entry),
            "manufacturer": MANUFACTURER,
            "model": "Smart Interface Unit",
        }

    @property
    def source_type(self) -> SourceType:
        """Return the source type (GPS)."""
        return SourceType.GPS

    @property
    def latitude(self) -> float | None:
        """Return latitude value of the device."""
        return self._parse_coordinates()[0]

    @property
    def longitude(self) -> float | None:
        """Return longitude value of the device."""
        return self._parse_coordinates()[1]

    @property
    def extra_state_attributes(self) -> dict[str, str | float | None]:
        """Return extra attributes."""
        return {
            "altitude": self._slot_value_for_labels("gps_altitude", "altitude"),
            "heading": self._slot_value_for_labels("gps_heading", "heading"),
            "satellites": self._slot_value_for_labels("gps_satellites", "satellites"),
            "signal_quality": self._slot_value_for_labels(
                "gps_signal_quality",
                "lte_connection_quality",
            ),
        }

    def _slot_data(self) -> dict:
        """Return the slot-keyed SignalR dict safely."""
        if self.coordinator.data is None:
            return {}
        return self.coordinator.data.get("signalr_slots", {})

    def _slot_value_for_labels(self, *labels: str):
        """Return the first observed slot value whose metadata label matches."""
        slots = self._slot_data()
        label_set = set(labels)
        for bus_id, sensor_id in sorted(slots):
            meta = slot_meta(bus_id, sensor_id)
            if meta is None or meta.label not in label_set:
                continue
            return slots[(bus_id, sensor_id)]
        return None

    def _parse_coordinates(self) -> tuple[float | None, float | None]:
        """Parse lat/lon from the GPS location string 'lat,lon'."""
        gps_str = self._slot_value_for_labels("gps_location", "gps_coordinates")
        if not gps_str or not isinstance(gps_str, str):
            return (None, None)
        try:
            parts = gps_str.split(",")
            if len(parts) == 2:
                return (float(parts[0]), float(parts[1]))
        except (ValueError, IndexError):
            _LOGGER.debug("Could not parse GPS coordinates: %s", gps_str)
        return (None, None)
