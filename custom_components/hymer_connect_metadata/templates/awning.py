"""Awning cover template.

Exposes awning-style components as a single CoverEntity when the vehicle emits
the standard open / close / position slots:

- slot 1: open command
- slot 2: close command
- slot 7: position

Tilt controls remain available through the raw layer if present.
"""

from __future__ import annotations

from typing import Any

from homeassistant.components.cover import CoverEntity, CoverEntityFeature
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from ..discovery import all_components, component_meta
from ..entity_base import _device_info_for_bus
from ..template_specs import awning_cover_spec


def _contains_token(value: Any, *tokens: str) -> bool:
    if not isinstance(value, str):
        return False
    text = value.upper()
    return any(token in text for token in tokens)


class AwningCoverTemplate:
    PLATFORM = "cover"

    def build(self, coordinator, entry, observed):
        entities: list[Any] = []
        claimed: set[tuple[int, int]] = set()
        spec = awning_cover_spec()
        for bus_id, comp in all_components().items():
            if comp.kind not in spec.component_kinds:
                continue
            if not any((bus_id, sid) in observed for sid in spec.trigger_slots):
                continue
            entities.append(
                HymerAwningCover(
                    coordinator,
                    entry,
                    bus_id,
                    has_position=(bus_id, spec.position_slot) in observed,
                )
            )
            for sid in spec.claim_slots:
                if (bus_id, sid) in observed:
                    claimed.add((bus_id, sid))
        return entities, claimed


class HymerAwningCover(CoordinatorEntity, CoverEntity):
    _attr_has_entity_name = True

    def __init__(self, coordinator, entry, bus_id: int, has_position: bool) -> None:
        super().__init__(coordinator)
        self._bus = bus_id
        self._has_position = has_position
        comp = component_meta(bus_id)
        self._attr_unique_id = f"{entry.entry_id}_awning_b{bus_id}"
        self._attr_name = "Awning"
        self._attr_icon = "mdi:awning-outline"
        self._attr_device_info = _device_info_for_bus(entry.entry_id, bus_id, comp)
        features = CoverEntityFeature.OPEN | CoverEntityFeature.CLOSE
        if has_position:
            features |= CoverEntityFeature.SET_POSITION
        self._attr_supported_features = features

    def _slot(self, sid: int) -> Any:
        slots = (self.coordinator.data or {}).get("signalr_slots") or {}
        return slots.get((self._bus, sid))

    @property
    def current_cover_position(self) -> int | None:
        value = self._slot(7)
        if isinstance(value, (int, float)):
            return max(0, min(100, int(value)))
        return None

    @property
    def is_closed(self) -> bool | None:
        position = self.current_cover_position
        if position is not None:
            return position <= 0
        status = self._slot(5)
        if _contains_token(status, "CLOSED", "RETRACTED"):
            return True
        if _contains_token(status, "OPEN", "DEPLOYED", "EXTENDED"):
            return False
        return None

    @property
    def is_opening(self) -> bool | None:
        direction = self._slot(8)
        status = self._slot(5)
        if _contains_token(direction, "OPEN", "EXTEND"):
            return True
        if _contains_token(status, "OPENING", "EXTENDING"):
            return True
        return False

    @property
    def is_closing(self) -> bool | None:
        direction = self._slot(8)
        status = self._slot(5)
        if _contains_token(direction, "CLOSE", "RETRACT"):
            return True
        if _contains_token(status, "CLOSING", "RETRACTING"):
            return True
        return False

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        attrs: dict[str, Any] = {}
        for sid, key in (
            (5, "status"),
            (6, "user_lock"),
            (8, "movement_direction"),
            (9, "tilt_front_status"),
            (10, "tilt_rear_status"),
        ):
            value = self._slot(sid)
            if value is not None:
                attrs[key] = value
        return attrs

    async def async_open_cover(self, **kwargs: Any) -> None:
        client = await self.coordinator.async_ensure_signalr_connected()
        await client.send_light_command(self._bus, 1, bool_value=True)
        self.async_write_ha_state()

    async def async_close_cover(self, **kwargs: Any) -> None:
        client = await self.coordinator.async_ensure_signalr_connected()
        await client.send_light_command(self._bus, 2, bool_value=True)
        self.async_write_ha_state()

    async def async_set_cover_position(self, **kwargs: Any) -> None:
        position = kwargs.get("position")
        if not isinstance(position, (int, float)):
            return
        client = await self.coordinator.async_ensure_signalr_connected()
        await client.send_light_command(
            self._bus,
            7,
            uint_value=max(0, min(100, int(position))),
        )
        self.async_write_ha_state()
