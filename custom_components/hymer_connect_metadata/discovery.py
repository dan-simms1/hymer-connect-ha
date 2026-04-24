"""Discovery: load runtime metadata and match it against observed wire slots.

The integration talks to a vehicle SCU that emits data only for components that
are physically present.  The first full PiaResponse frame is effectively a
capability advertisement: every (bus_id, sensor_id) tuple it contains is a
sensor this van has.

This module loads the local runtime metadata files:

  - `data/sensor_labels.json`
  - `data/component_kinds.json`

and exposes a simple lookup API for platform setup code.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from functools import lru_cache
from typing import Any

from .runtime_metadata import data_path

_LOGGER = logging.getLogger(__name__)

_SENSOR_LABELS = data_path("sensor_labels.json")
_COMPONENT_KINDS = data_path("component_kinds.json")


@dataclass(frozen=True)
class SlotMeta:
    """Metadata for a single (bus_id, sensor_id) slot."""

    bus_id: int
    sensor_id: int
    label: str
    unit: str | None
    datatype: str      # "bool" | "int" | "float" | "string"
    mode: str          # "r" | "rw" | "w"
    wire_mode: str     # original protocol mode before any runtime normalization
    transform: str | None = None   # div10/div100/div1000/invert100 etc.
    control_platform: str | None = None
    options: tuple[str, ...] = ()
    min_value: float | int | None = None
    max_value: float | int | None = None
    step: float | int | None = None
    description: str | None = None
    deprecated: bool = False

    @property
    def is_writable(self) -> bool:
        return self.wire_mode in ("rw", "w")


@dataclass(frozen=True)
class ComponentMeta:
    """Metadata for a whole bus/component."""

    bus_id: int
    kind: str           # "light" | "truma_heater" | "fridge" | "chassis" | ...
    name: str           # display name
    suggested_area: str | None


@lru_cache(maxsize=1)
def _load_labels() -> dict[tuple[int, int], SlotMeta]:
    out: dict[tuple[int, int], SlotMeta] = {}
    if not _SENSOR_LABELS.exists():
        return out
    with _SENSOR_LABELS.open() as f:
        raw = json.load(f)
    for key, v in raw["slots"].items():
        bus_s, sid_s = key.split(":")
        bus, sid = int(bus_s), int(sid_s)
        out[(bus, sid)] = SlotMeta(
            bus_id=bus,
            sensor_id=sid,
            label=v["label"],
            unit=v.get("unit"),
            datatype=v["datatype"],
            mode=v["mode"],
            wire_mode=v.get("wire_mode", v["mode"]),
            transform=v.get("transform"),
            control_platform=v.get("control_platform"),
            options=tuple(v.get("options", [])),
            min_value=v.get("min"),
            max_value=v.get("max"),
            step=v.get("step"),
            description=v.get("description"),
            deprecated=bool(v.get("deprecated", False)),
        )
    return out


@lru_cache(maxsize=1)
def _load_kinds() -> dict[int, ComponentMeta]:
    out: dict[int, ComponentMeta] = {}
    if not _COMPONENT_KINDS.exists():
        return out
    with _COMPONENT_KINDS.open() as f:
        raw = json.load(f)
    for key, v in raw["components"].items():
        bus = int(key)
        out[bus] = ComponentMeta(
            bus_id=bus,
            kind=v["kind"],
            name=v["name"],
            suggested_area=v.get("suggested_area"),
        )
    return out


def slot_meta(bus_id: int, sensor_id: int) -> SlotMeta | None:
    return _load_labels().get((bus_id, sensor_id))


def component_meta(bus_id: int) -> ComponentMeta | None:
    return _load_kinds().get(bus_id)


def all_slots() -> dict[tuple[int, int], SlotMeta]:
    return _load_labels()


def all_components() -> dict[int, ComponentMeta]:
    return _load_kinds()


def invalidate_runtime_metadata_cache() -> None:
    """Clear cached runtime metadata so entry reloads see local JSON updates."""
    _load_labels.cache_clear()
    _load_kinds.cache_clear()


def warm_runtime_metadata_cache() -> None:
    """Load runtime metadata files into cache off the event loop."""
    _load_labels()
    _load_kinds()


def apply_transform(value: Any, transform: str | None) -> Any:
    """Apply a named transform to a scalar value."""
    if transform is None or not isinstance(value, (int, float)):
        return value
    if transform == "div10":
        return value / 10
    if transform == "div100":
        return value / 100
    if transform == "div1000":
        return value / 1000
    if transform == "div3600":
        return round(value / 3600, 1)
    if transform == "invert100":
        return 100 - value
    _LOGGER.warning("Unknown transform %s", transform)
    return value


def reverse_transform(value: Any, transform: str | None) -> Any:
    """Invert a transform for a write command (HA value → wire value)."""
    if transform is None or not isinstance(value, (int, float)):
        return value
    if transform == "div10": return value * 10
    if transform == "div100": return value * 100
    if transform == "div1000": return value * 1000
    if transform == "div3600": return value * 3600
    if transform == "invert100": return 100 - value
    return value
