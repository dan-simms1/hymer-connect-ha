"""Light templates.

Supports:

- classic light-zone families with slots 1/2/3
- generic simple dimmer modules that expose the same on/off + brightness shape
- named auxiliary light channels on other component families such as the
  power-system lighting controller and climate-module lamp channels
"""

from __future__ import annotations

import logging
from typing import Any

from homeassistant.components.light import (
    ATTR_BRIGHTNESS,
    ATTR_COLOR_TEMP_KELVIN,
    ColorMode,
    LightEntity,
)
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from ..discovery import all_components, component_meta, slot_meta
from ..entity_base import _device_info_for_bus
from ..template_specs import (
    named_light_channel_spec,
    simple_light_spec,
    slot_matches_requirement,
    slots_match_requirements,
)

_LOGGER = logging.getLogger(__name__)


class LightTemplate:
    PLATFORM = "light"

    def build(self, coordinator, entry, observed):
        entities: list[Any] = []
        claimed: set[tuple[int, int]] = set()
        simple_spec = simple_light_spec()
        for bus_id, comp in all_components().items():
            if not _is_simple_light_component(bus_id):
                continue
            if (bus_id, 1) not in observed:
                continue
            has_brightness = (bus_id, 2) in observed
            has_color = any(
                (bus_id, requirement.sensor_id) in observed
                and slot_matches_requirement(bus_id, requirement)
                for requirement in simple_spec.optional_slots
            )
            entities.append(
                HymerLight(
                    coordinator,
                    entry,
                    bus_id,
                    has_brightness,
                    has_color,
                    name_override=None if comp.kind == "light" else simple_spec.fallback_name,
                )
            )
            claimed.add((bus_id, 1))
            if has_brightness:
                claimed.add((bus_id, 2))
            if has_color:
                claimed.add((bus_id, 3))

        for bus_id, slots_for_bus in _named_light_channels(observed).items():
            for channel in slots_for_bus:
                entities.append(HymerNamedLight(coordinator, entry, bus_id, channel))
                claimed.add((bus_id, channel["state_sid"]))
                if channel.get("brightness_sid") is not None:
                    claimed.add((bus_id, int(channel["brightness_sid"])))
        return entities, claimed


def _is_simple_light_component(bus_id: int) -> bool:
    return slots_match_requirements(bus_id, simple_light_spec().required_slots)


def _is_light_label(label: str) -> bool:
    spec = named_light_channel_spec()
    return label in spec.accepted_exact_labels or any(
        label.endswith(suffix) for suffix in spec.accepted_label_suffixes
    )


def _humanise_label(label: str) -> str:
    return " ".join(part.capitalize() for part in label.split("_"))


def _named_light_channels(
    observed: set[tuple[int, int]],
) -> dict[int, list[dict[str, Any]]]:
    spec = named_light_channel_spec()
    channels: dict[int, list[dict[str, Any]]] = {}
    for bus_id in sorted({bus for bus, _sid in observed}):
        state_by_label: dict[str, tuple[int, Any]] = {}
        brightness_by_label: dict[str, int] = {}
        for _bus_id, sid in sorted(slot for slot in observed if slot[0] == bus_id):
            meta = slot_meta(bus_id, sid)
            if meta is None or meta.mode not in {"rw", "w"}:
                continue
            label = meta.label
            if any(label.startswith(prefix) for prefix in spec.ignored_prefixes):
                continue
            if label.endswith(spec.brightness_suffix):
                base = label.removesuffix(spec.brightness_suffix)
                if _is_light_label(base) and meta.datatype == "int":
                    brightness_by_label[base] = sid
                continue
            if any(label.endswith(suffix) for suffix in spec.ignored_suffixes):
                continue
            if not _is_light_label(label):
                continue
            state_by_label[label] = (sid, meta)

        bus_channels: list[dict[str, Any]] = []
        for label, (sid, meta) in state_by_label.items():
            if sid in {1, 2, 3} and _is_simple_light_component(bus_id):
                continue
            brightness_sid = brightness_by_label.get(label)
            if meta.datatype == "bool":
                bus_channels.append(
                    {
                        "label": label,
                        "state_sid": sid,
                        "brightness_sid": brightness_sid,
                        "direct_dimmer": False,
                    }
                )
                continue
            if meta.datatype == "int" and meta.unit == "%":
                bus_channels.append(
                    {
                        "label": label,
                        "state_sid": sid,
                        "brightness_sid": None,
                        "direct_dimmer": True,
                    }
                )
        if bus_channels:
            channels[bus_id] = bus_channels
    return channels


class HymerLight(CoordinatorEntity, LightEntity):
    _attr_has_entity_name = True

    def __init__(
        self,
        coordinator,
        entry,
        bus_id,
        has_brightness,
        has_color,
        name_override: str | None = None,
    ):
        super().__init__(coordinator)
        self._bus = bus_id
        self._has_brightness = has_brightness
        self._has_color = has_color
        comp = component_meta(bus_id)
        self._attr_unique_id = f"{entry.entry_id}_light_b{bus_id}"
        self._attr_name = name_override or (comp.name if comp else f"Light {bus_id}")
        self._attr_device_info = _device_info_for_bus(entry.entry_id, bus_id, comp)
        self._attr_icon = "mdi:ceiling-light"
        if has_color:
            self._attr_supported_color_modes = {ColorMode.COLOR_TEMP}
            self._attr_color_mode = ColorMode.COLOR_TEMP
            self._attr_min_color_temp_kelvin = 2700
            self._attr_max_color_temp_kelvin = 6500
        elif has_brightness:
            self._attr_supported_color_modes = {ColorMode.BRIGHTNESS}
            self._attr_color_mode = ColorMode.BRIGHTNESS
        else:
            self._attr_supported_color_modes = {ColorMode.ONOFF}
            self._attr_color_mode = ColorMode.ONOFF

    def _slot(self, sid: int):
        slots = (self.coordinator.data or {}).get("signalr_slots") or {}
        return slots.get((self._bus, sid))

    @property
    def available(self) -> bool:
        if not self.coordinator.is_habitation_power_available():
            return False
        return super().available

    @property
    def is_on(self) -> bool | None:
        v = self._slot(1)
        return bool(v) if v is not None else None

    @property
    def brightness(self) -> int | None:
        if not self._has_brightness:
            return None
        pct = self._slot(2)
        if isinstance(pct, (int, float)):
            return int(pct * 255 / 100)
        return None

    @property
    def color_temp_kelvin(self) -> int | None:
        if not self._has_color:
            return None
        pct = self._slot(3)
        if not isinstance(pct, (int, float)):
            return None
        # Map 0-100 onto 2700-6500 K (warm → cool)
        return int(2700 + (pct / 100.0) * (6500 - 2700))

    async def async_turn_on(self, **kwargs):
        client = await self.coordinator.async_ensure_signalr_connected()
        if ATTR_BRIGHTNESS in kwargs and self._has_brightness:
            pct = max(0, min(100, int(kwargs[ATTR_BRIGHTNESS] * 100 / 255)))
            await client.send_light_command(self._bus, 2, uint_value=pct)
        if ATTR_COLOR_TEMP_KELVIN in kwargs and self._has_color:
            kelvin = kwargs[ATTR_COLOR_TEMP_KELVIN]
            pct = max(0, min(100, int((kelvin - 2700) * 100 / (6500 - 2700))))
            await client.send_light_command(self._bus, 3, uint_value=pct)
        await client.send_light_command(self._bus, 1, bool_value=True)
        self.async_write_ha_state()

    async def async_turn_off(self, **kwargs):
        client = await self.coordinator.async_ensure_signalr_connected()
        await client.send_light_command(self._bus, 1, bool_value=False)
        self.async_write_ha_state()


class HymerNamedLight(CoordinatorEntity, LightEntity):
    _attr_has_entity_name = True

    def __init__(self, coordinator, entry, bus_id: int, channel: dict[str, Any]):
        super().__init__(coordinator)
        self._bus = bus_id
        self._label = str(channel["label"])
        self._state_sid = int(channel["state_sid"])
        self._brightness_sid = channel.get("brightness_sid")
        self._direct_dimmer = bool(channel.get("direct_dimmer"))
        comp = component_meta(bus_id)
        self._attr_unique_id = f"{entry.entry_id}_light_b{bus_id}_{self._label}"
        self._attr_name = _humanise_label(self._label)
        self._attr_device_info = _device_info_for_bus(entry.entry_id, bus_id, comp)
        self._attr_icon = "mdi:ceiling-light"
        if self._direct_dimmer or self._brightness_sid is not None:
            self._attr_supported_color_modes = {ColorMode.BRIGHTNESS}
            self._attr_color_mode = ColorMode.BRIGHTNESS
        else:
            self._attr_supported_color_modes = {ColorMode.ONOFF}
            self._attr_color_mode = ColorMode.ONOFF

    def _slot(self, sid: int | None):
        if sid is None:
            return None
        slots = (self.coordinator.data or {}).get("signalr_slots") or {}
        return slots.get((self._bus, sid))

    @property
    def available(self) -> bool:
        if not self.coordinator.is_habitation_power_available():
            return False
        return super().available

    def _brightness_pct(self) -> int | None:
        sid = self._state_sid if self._direct_dimmer else self._brightness_sid
        value = self._slot(sid)
        if isinstance(value, (int, float)):
            return max(0, min(100, int(value)))
        return None

    @property
    def is_on(self) -> bool | None:
        if self._direct_dimmer:
            pct = self._brightness_pct()
            return pct is not None and pct > 0
        value = self._slot(self._state_sid)
        if value is None:
            return None
        return bool(value)

    @property
    def brightness(self) -> int | None:
        pct = self._brightness_pct()
        if pct is None:
            return None
        return int(pct * 255 / 100)

    async def async_turn_on(self, **kwargs):
        client = await self.coordinator.async_ensure_signalr_connected()
        brightness = kwargs.get(ATTR_BRIGHTNESS)
        if self._direct_dimmer:
            current_pct = self._brightness_pct()
            pct = (
                max(0, min(100, int(brightness * 100 / 255)))
                if brightness is not None
                else (current_pct if current_pct and current_pct > 0 else 100)
            )
            await client.send_light_command(self._bus, self._state_sid, uint_value=pct)
            self.async_write_ha_state()
            return

        if self._brightness_sid is not None and brightness is not None:
            pct = max(0, min(100, int(brightness * 100 / 255)))
            await client.send_light_command(self._bus, int(self._brightness_sid), uint_value=pct)
        await client.send_light_command(self._bus, self._state_sid, bool_value=True)
        self.async_write_ha_state()

    async def async_turn_off(self, **kwargs):
        client = await self.coordinator.async_ensure_signalr_connected()
        if self._direct_dimmer:
            await client.send_light_command(self._bus, self._state_sid, uint_value=0)
        else:
            await client.send_light_command(self._bus, self._state_sid, bool_value=False)
        self.async_write_ha_state()
