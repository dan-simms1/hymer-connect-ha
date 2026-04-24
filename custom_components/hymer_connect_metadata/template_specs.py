"""Load tracked rich-template metadata."""

from __future__ import annotations

import json
from dataclasses import dataclass
from functools import lru_cache
from typing import Any

from .discovery import slot_meta
from .runtime_metadata import spec_path

_TEMPLATE_SPECS_PATH = spec_path("template_specs.json")


@dataclass(frozen=True)
class SlotRequirement:
    """One slot requirement used by a template layout."""

    sensor_id: int
    label: str | None = None
    datatype: str | None = None


@dataclass(frozen=True)
class SimpleLightSpec:
    """Metadata for the simple light aggregation template."""

    required_slots: tuple[SlotRequirement, ...]
    optional_slots: tuple[SlotRequirement, ...]
    fallback_name: str


@dataclass(frozen=True)
class NamedLightChannelSpec:
    """Metadata for named light channel aggregation."""

    accepted_exact_labels: tuple[str, ...]
    accepted_label_suffixes: tuple[str, ...]
    brightness_suffix: str
    ignored_prefixes: tuple[str, ...]
    ignored_suffixes: tuple[str, ...]


@dataclass(frozen=True)
class AwningCoverSpec:
    """Metadata for the awning cover template."""

    component_kinds: tuple[str, ...]
    trigger_slots: tuple[int, ...]
    claim_slots: tuple[int, ...]
    position_slot: int


@dataclass(frozen=True)
class FridgePowerSpec:
    """Metadata for the fridge power switch template."""

    component_kinds: tuple[str, ...]
    required_slots: tuple[SlotRequirement, ...]
    power_slot: int


@dataclass(frozen=True)
class FridgeLevelSpec:
    """Metadata for the fridge level select template."""

    component_kinds: tuple[str, ...]
    required_slots: tuple[SlotRequirement, ...]
    level_slot: int


@dataclass(frozen=True)
class AirConditionerSingleZoneSpec:
    """Metadata for the standard single-zone air-conditioner template."""

    component_kinds: tuple[str, ...]
    required_slots: tuple[SlotRequirement, ...]
    claim_slots: tuple[int, ...]
    target_sid: int
    current_sid: int
    mode_sid: int
    fan_sid: int
    name: str


@dataclass(frozen=True)
class AirxcelZoneSpec:
    """Metadata for one Airxcel climate zone."""

    zone: str
    component_kinds: tuple[str, ...]
    required_slots: tuple[SlotRequirement, ...]
    claim_slots: tuple[int, ...]
    mode_sid: int
    fan_mode_sid: int
    fan_speed_sid: int
    heat_target_sid: int
    cool_target_sid: int
    ambient_sid: int
    name: str


@dataclass(frozen=True)
class TrumaPanelHeaterSpec:
    """Metadata for the panel-style Truma heater climate template."""

    component_kinds: tuple[str, ...]
    required_slots: tuple[SlotRequirement, ...]
    claim_slots: tuple[int, ...]
    setpoint_sid: int
    name: str


@dataclass(frozen=True)
class ModernHeaterSpec:
    """Metadata for the modern enum-based heater climate templates."""

    variant: str
    component_kinds: tuple[str, ...]
    required_slots: tuple[SlotRequirement, ...]
    claim_slots: tuple[int, ...]
    name: str
    unique_suffix: str
    target_sid: int
    current_sid: int
    mode_sid: int
    fan_sid: int | None


@dataclass(frozen=True)
class BoilerModeSpec:
    """Metadata for the Truma boiler-mode select template."""

    component_kinds: tuple[str, ...]
    required_slots: tuple[SlotRequirement, ...]
    options: tuple[str, ...]
    wire_values: tuple[tuple[str, str], ...]
    mode_slot: int
    energy_source_slot: int

    @property
    def wire_map(self) -> dict[str, str]:
        return dict(self.wire_values)


@dataclass(frozen=True)
class SlotWriteSpec:
    """One metadata-defined write used by a rich template."""

    sensor_id: int
    value_type: str
    value: Any


@dataclass(frozen=True)
class HeaterEnergySpec:
    """Metadata for the Truma heater-energy select template."""

    component_kinds: tuple[str, ...]
    required_slots: tuple[SlotRequirement, ...]
    options: tuple[str, ...]
    mode_slot: int
    mirror_slot: int
    power_slot: int
    option_writes: tuple[tuple[str, tuple[SlotWriteSpec, ...]], ...]

    @property
    def writes(self) -> dict[str, tuple[SlotWriteSpec, ...]]:
        return dict(self.option_writes)


@dataclass(frozen=True)
class FanEntitySpec:
    """Metadata for one aggregated fan entity."""

    kind: str
    component_id: int
    name: str
    state_sid: int
    speed_mode_sid: int | None = None
    speed_sid: int | None = None
    attribute_slots: tuple[tuple[str, int], ...] = ()

    @property
    def claimable_slots(self) -> tuple[tuple[int, int], ...]:
        slots: list[tuple[int, int]] = [(self.component_id, self.state_sid)]
        if self.speed_mode_sid is not None:
            slots.append((self.component_id, self.speed_mode_sid))
        if self.speed_sid is not None:
            slots.append((self.component_id, self.speed_sid))
        slots.extend((self.component_id, sid) for _label, sid in self.attribute_slots)
        return tuple(slots)

    @property
    def attributes(self) -> dict[str, int]:
        return dict(self.attribute_slots)


def _require_dict(payload: Any, key: str) -> dict[str, Any]:
    value = payload.get(key)
    if not isinstance(value, dict):
        raise ValueError(f"template_specs.json must define object '{key}'")
    return value


def _require_list(payload: dict[str, Any], key: str) -> list[Any]:
    value = payload.get(key)
    if not isinstance(value, list):
        raise ValueError(f"template_specs.json entry missing list '{key}'")
    return value


def _require_string(payload: dict[str, Any], key: str) -> str:
    value = payload.get(key)
    if not isinstance(value, str) or not value:
        raise ValueError(f"template_specs.json entry missing valid '{key}'")
    return value


def _optional_int(payload: dict[str, Any], key: str) -> int | None:
    value = payload.get(key)
    if value is None:
        return None
    if not isinstance(value, int):
        raise ValueError(f"template_specs.json entry has invalid '{key}'")
    return value


def _string_list(payload: dict[str, Any], key: str) -> tuple[str, ...]:
    values = _require_list(payload, key)
    rendered: list[str] = []
    for value in values:
        if not isinstance(value, str) or not value:
            raise ValueError(f"template_specs.json entry has invalid string in '{key}'")
        rendered.append(value)
    return tuple(rendered)


def _slot_requirement(payload: dict[str, Any]) -> SlotRequirement:
    return SlotRequirement(
        sensor_id=int(payload["sensor_id"]),
        label=payload.get("label"),
        datatype=payload.get("datatype"),
    )


def _int_tuple(payload: dict[str, Any], key: str) -> tuple[int, ...]:
    values = _require_list(payload, key)
    rendered: list[int] = []
    for value in values:
        if not isinstance(value, int):
            raise ValueError(f"template_specs.json entry has invalid integer in '{key}'")
        rendered.append(value)
    return tuple(rendered)


def _write_specs(payload: dict[str, Any], key: str) -> tuple[tuple[str, tuple[SlotWriteSpec, ...]], ...]:
    value = payload.get(key)
    if not isinstance(value, dict):
        raise ValueError(f"template_specs.json entry missing object '{key}'")
    rendered: list[tuple[str, tuple[SlotWriteSpec, ...]]] = []
    for option, writes_raw in value.items():
        if not isinstance(option, str):
            raise ValueError(f"template_specs.json write option in '{key}' must be a string")
        if not isinstance(writes_raw, list):
            raise ValueError(f"template_specs.json writes for '{option}' must be a list")
        writes: list[SlotWriteSpec] = []
        for item in writes_raw:
            if not isinstance(item, dict):
                raise ValueError("template_specs.json write spec must be an object")
            value_type = _require_string(item, "type")
            if value_type not in {"str", "uint"}:
                raise ValueError(f"template_specs.json write spec has unsupported type '{value_type}'")
            writes.append(
                SlotWriteSpec(
                    sensor_id=int(item["sensor_id"]),
                    value_type=value_type,
                    value=item.get("value"),
                )
            )
        rendered.append((option, tuple(writes)))
    return tuple(rendered)


@lru_cache(maxsize=1)
def _payload() -> dict[str, Any]:
    return json.loads(_TEMPLATE_SPECS_PATH.read_text())


@lru_cache(maxsize=1)
def simple_light_spec() -> SimpleLightSpec:
    payload = _require_dict(_require_dict(_payload(), "light"), "simple_component")
    return SimpleLightSpec(
        required_slots=tuple(_slot_requirement(item) for item in _require_list(payload, "required_slots")),
        optional_slots=tuple(_slot_requirement(item) for item in _require_list(payload, "optional_slots")),
        fallback_name=_require_string(payload, "fallback_name"),
    )


@lru_cache(maxsize=1)
def named_light_channel_spec() -> NamedLightChannelSpec:
    payload = _require_dict(_require_dict(_payload(), "light"), "named_channels")
    return NamedLightChannelSpec(
        accepted_exact_labels=_string_list(payload, "accepted_exact_labels"),
        accepted_label_suffixes=_string_list(payload, "accepted_label_suffixes"),
        brightness_suffix=_require_string(payload, "brightness_suffix"),
        ignored_prefixes=_string_list(payload, "ignored_prefixes"),
        ignored_suffixes=_string_list(payload, "ignored_suffixes"),
    )


@lru_cache(maxsize=1)
def awning_cover_spec() -> AwningCoverSpec:
    payload = _require_dict(_require_dict(_payload(), "cover"), "awning")
    return AwningCoverSpec(
        component_kinds=_string_list(payload, "component_kinds"),
        trigger_slots=_int_tuple(payload, "trigger_slots"),
        claim_slots=_int_tuple(payload, "claim_slots"),
        position_slot=int(payload["position_slot"]),
    )


@lru_cache(maxsize=1)
def fridge_power_spec() -> FridgePowerSpec:
    payload = _require_dict(_require_dict(_payload(), "switch"), "fridge_power")
    return FridgePowerSpec(
        component_kinds=_string_list(payload, "component_kinds"),
        required_slots=tuple(
            _slot_requirement(item) for item in _require_list(payload, "required_slots")
        ),
        power_slot=int(payload["power_slot"]),
    )


@lru_cache(maxsize=1)
def fridge_level_spec() -> FridgeLevelSpec:
    payload = _require_dict(_require_dict(_payload(), "select"), "fridge_level")
    return FridgeLevelSpec(
        component_kinds=_string_list(payload, "component_kinds"),
        required_slots=tuple(
            _slot_requirement(item) for item in _require_list(payload, "required_slots")
        ),
        level_slot=int(payload["level_slot"]),
    )


@lru_cache(maxsize=1)
def air_conditioner_single_zone_spec() -> AirConditionerSingleZoneSpec:
    payload = _require_dict(_require_dict(_payload(), "climate"), "air_conditioner_single_zone")
    return AirConditionerSingleZoneSpec(
        component_kinds=_string_list(payload, "component_kinds"),
        required_slots=tuple(_slot_requirement(item) for item in _require_list(payload, "required_slots")),
        claim_slots=_int_tuple(payload, "claim_slots"),
        target_sid=int(payload["target_sid"]),
        current_sid=int(payload["current_sid"]),
        mode_sid=int(payload["mode_sid"]),
        fan_sid=int(payload["fan_sid"]),
        name=_require_string(payload, "name"),
    )


@lru_cache(maxsize=1)
def airxcel_zone_specs() -> tuple[AirxcelZoneSpec, ...]:
    payload = _require_dict(_payload(), "climate")
    specs: list[AirxcelZoneSpec] = []
    for item in _require_list(payload, "airxcel_zones"):
        if not isinstance(item, dict):
            raise ValueError("template_specs.json airxcel_zones entry must be an object")
        specs.append(
            AirxcelZoneSpec(
                zone=_require_string(item, "zone"),
                component_kinds=_string_list(item, "component_kinds"),
                required_slots=tuple(_slot_requirement(req) for req in _require_list(item, "required_slots")),
                claim_slots=_int_tuple(item, "claim_slots"),
                mode_sid=int(item["mode_sid"]),
                fan_mode_sid=int(item["fan_mode_sid"]),
                fan_speed_sid=int(item["fan_speed_sid"]),
                heat_target_sid=int(item["heat_target_sid"]),
                cool_target_sid=int(item["cool_target_sid"]),
                ambient_sid=int(item["ambient_sid"]),
                name=_require_string(item, "name"),
            )
        )
    return tuple(specs)


@lru_cache(maxsize=1)
def truma_panel_heater_spec() -> TrumaPanelHeaterSpec:
    payload = _require_dict(_require_dict(_payload(), "climate"), "truma_panel_heater")
    return TrumaPanelHeaterSpec(
        component_kinds=_string_list(payload, "component_kinds"),
        required_slots=tuple(_slot_requirement(item) for item in _require_list(payload, "required_slots")),
        claim_slots=_int_tuple(payload, "claim_slots"),
        setpoint_sid=int(payload["setpoint_sid"]),
        name=_require_string(payload, "name"),
    )


@lru_cache(maxsize=1)
def modern_heater_specs() -> tuple[ModernHeaterSpec, ...]:
    payload = _require_dict(_payload(), "climate")
    specs: list[ModernHeaterSpec] = []
    for item in _require_list(payload, "modern_heaters"):
        if not isinstance(item, dict):
            raise ValueError("template_specs.json modern_heaters entry must be an object")
        specs.append(
            ModernHeaterSpec(
                variant=_require_string(item, "variant"),
                component_kinds=_string_list(item, "component_kinds"),
                required_slots=tuple(_slot_requirement(req) for req in _require_list(item, "required_slots")),
                claim_slots=_int_tuple(item, "claim_slots"),
                name=_require_string(item, "name"),
                unique_suffix=_require_string(item, "unique_suffix"),
                target_sid=int(item["target_sid"]),
                current_sid=int(item["current_sid"]),
                mode_sid=int(item["mode_sid"]),
                fan_sid=_optional_int(item, "fan_sid"),
            )
        )
    return tuple(specs)


@lru_cache(maxsize=1)
def boiler_mode_spec() -> BoilerModeSpec:
    payload = _require_dict(_require_dict(_payload(), "select"), "boiler_mode")
    wire_values_raw = payload.get("wire_values")
    if not isinstance(wire_values_raw, dict):
        raise ValueError("template_specs.json boiler_mode must define wire_values")
    wire_values: list[tuple[str, str]] = []
    for option, wire_value in wire_values_raw.items():
        if not isinstance(option, str) or not isinstance(wire_value, str):
            raise ValueError("template_specs.json boiler_mode wire_values must map strings to strings")
        wire_values.append((option, wire_value))
    return BoilerModeSpec(
        component_kinds=_string_list(payload, "component_kinds"),
        required_slots=tuple(_slot_requirement(item) for item in _require_list(payload, "required_slots")),
        options=_string_list(payload, "options"),
        wire_values=tuple(wire_values),
        mode_slot=int(payload["mode_slot"]),
        energy_source_slot=int(payload["energy_source_slot"]),
    )


@lru_cache(maxsize=1)
def heater_energy_spec() -> HeaterEnergySpec:
    payload = _require_dict(_require_dict(_payload(), "select"), "heater_energy")
    return HeaterEnergySpec(
        component_kinds=_string_list(payload, "component_kinds"),
        required_slots=tuple(_slot_requirement(item) for item in _require_list(payload, "required_slots")),
        options=_string_list(payload, "options"),
        mode_slot=int(payload["mode_slot"]),
        mirror_slot=int(payload["mirror_slot"]),
        power_slot=int(payload["power_slot"]),
        option_writes=_write_specs(payload, "writes"),
    )


@lru_cache(maxsize=1)
def fan_entity_specs() -> tuple[FanEntitySpec, ...]:
    payload = _require_dict(_payload(), "fan")
    specs: list[FanEntitySpec] = []
    for item in _require_list(payload, "entities"):
        if not isinstance(item, dict):
            raise ValueError("template_specs.json fan entity must be an object")
        attribute_slots_raw = item.get("attribute_slots", {})
        if not isinstance(attribute_slots_raw, dict):
            raise ValueError("template_specs.json fan attribute_slots must be an object")
        attribute_slots: list[tuple[str, int]] = []
        for label, sid in attribute_slots_raw.items():
            if not isinstance(label, str) or not isinstance(sid, int):
                raise ValueError(
                    "template_specs.json fan attribute_slots must map strings to integers"
                )
            attribute_slots.append((label, sid))
        specs.append(
            FanEntitySpec(
                kind=_require_string(item, "kind"),
                component_id=int(item["component_id"]),
                name=_require_string(item, "name"),
                state_sid=int(item["state_sid"]),
                speed_mode_sid=_optional_int(item, "speed_mode_sid"),
                speed_sid=_optional_int(item, "speed_sid"),
                attribute_slots=tuple(attribute_slots),
            )
        )
    return tuple(specs)


def slot_matches_requirement(component_id: int, requirement: SlotRequirement) -> bool:
    """Return True when a slot matches the metadata requirement."""
    meta = slot_meta(component_id, requirement.sensor_id)
    if meta is None:
        return False
    if requirement.label is not None and meta.label != requirement.label:
        return False
    if requirement.datatype is not None and meta.datatype != requirement.datatype:
        return False
    return True


def slots_match_requirements(
    component_id: int,
    requirements: tuple[SlotRequirement, ...],
) -> bool:
    """Return True when every required slot matches."""
    return all(slot_matches_requirement(component_id, requirement) for requirement in requirements)


def _record_value(record: Any, key: str) -> Any:
    if isinstance(record, dict):
        return record.get(key)
    return getattr(record, key, None)


def _slot_record_matches(
    record: Any | None,
    requirement: SlotRequirement,
) -> bool:
    if record is None:
        return False
    if requirement.label is not None and _record_value(record, "label") != requirement.label:
        return False
    if (
        requirement.datatype is not None
        and _record_value(record, "datatype") != requirement.datatype
    ):
        return False
    return True


def _slots_match_component_requirements(
    slots_for_component: dict[int, Any],
    requirements: tuple[SlotRequirement, ...],
) -> bool:
    return all(
        _slot_record_matches(slots_for_component.get(requirement.sensor_id), requirement)
        for requirement in requirements
    )


def _claim(
    claims: dict[int, str],
    slots_for_component: dict[int, Any],
    sensor_ids: tuple[int, ...],
    tag: str,
) -> None:
    for sensor_id in sensor_ids:
        if sensor_id in slots_for_component:
            claims[sensor_id] = tag


def _is_named_light_label(label: str, spec: NamedLightChannelSpec) -> bool:
    return label in spec.accepted_exact_labels or any(
        label.endswith(suffix) for suffix in spec.accepted_label_suffixes
    )


def rich_template_claims(
    component_id: int,
    component_kind: str | None,
    slots_for_component: dict[int, Any],
) -> dict[int, str]:
    """Return rich-template support-class claims for one component."""
    claims: dict[int, str] = {}

    simple_spec = simple_light_spec()
    simple_light_present = _slots_match_component_requirements(
        slots_for_component,
        simple_spec.required_slots,
    )
    if simple_light_present:
        _claim(
            claims,
            slots_for_component,
            tuple(requirement.sensor_id for requirement in simple_spec.required_slots),
            "light",
        )
        optional_present = tuple(
            requirement.sensor_id
            for requirement in simple_spec.optional_slots
            if _slot_record_matches(slots_for_component.get(requirement.sensor_id), requirement)
        )
        _claim(claims, slots_for_component, optional_present, "light")

    named_light_spec = named_light_channel_spec()
    brightness_by_label: dict[str, int] = {}
    for sensor_id, record in slots_for_component.items():
        label = str(_record_value(record, "label") or "")
        if not label.endswith(named_light_spec.brightness_suffix):
            continue
        if _record_value(record, "datatype") != "int":
            continue
        base = label.removesuffix(named_light_spec.brightness_suffix)
        if _is_named_light_label(base, named_light_spec):
            brightness_by_label[base] = sensor_id

    for sensor_id, record in slots_for_component.items():
        label = str(_record_value(record, "label") or "")
        mode = _record_value(record, "mode")
        datatype = _record_value(record, "datatype")
        unit = _record_value(record, "unit")
        if mode not in {"rw", "w"}:
            continue
        if any(label.startswith(prefix) for prefix in named_light_spec.ignored_prefixes):
            continue
        if label.endswith(named_light_spec.brightness_suffix):
            continue
        if any(label.endswith(suffix) for suffix in named_light_spec.ignored_suffixes):
            continue
        if not _is_named_light_label(label, named_light_spec):
            continue
        if sensor_id in {1, 2, 3} and simple_light_present:
            continue
        if datatype == "bool":
            claims[sensor_id] = "light"
            brightness_sid = brightness_by_label.get(label)
            if brightness_sid is not None and brightness_sid in slots_for_component:
                claims[brightness_sid] = "light"
        elif datatype == "int" and unit == "%":
            claims[sensor_id] = "light"

    awning_spec = awning_cover_spec()
    if (
        component_kind in awning_spec.component_kinds
        and any(sid in slots_for_component for sid in awning_spec.trigger_slots)
    ):
        _claim(claims, slots_for_component, awning_spec.claim_slots, "awning")

    fridge_power = fridge_power_spec()
    if (
        component_kind in fridge_power.component_kinds
        and _slots_match_component_requirements(
            slots_for_component,
            fridge_power.required_slots,
        )
    ):
        _claim(
            claims,
            slots_for_component,
            (fridge_power.power_slot,),
            "fridge_power",
        )

    fridge_level = fridge_level_spec()
    if (
        component_kind in fridge_level.component_kinds
        and _slots_match_component_requirements(
            slots_for_component,
            fridge_level.required_slots,
        )
    ):
        _claim(
            claims,
            slots_for_component,
            (fridge_level.level_slot,),
            "fridge_level",
        )

    single_zone_spec = air_conditioner_single_zone_spec()
    if (
        component_kind in single_zone_spec.component_kinds
        and _slots_match_component_requirements(
            slots_for_component,
            single_zone_spec.required_slots,
        )
    ):
        _claim(claims, slots_for_component, single_zone_spec.claim_slots, "air_conditioner")

    for zone_spec in airxcel_zone_specs():
        if component_kind not in zone_spec.component_kinds:
            continue
        if not _slots_match_component_requirements(slots_for_component, zone_spec.required_slots):
            continue
        _claim(claims, slots_for_component, zone_spec.claim_slots, "air_conditioner")

    truma_spec = truma_panel_heater_spec()
    if (
        component_kind in truma_spec.component_kinds
        and _slots_match_component_requirements(slots_for_component, truma_spec.required_slots)
    ):
        _claim(claims, slots_for_component, truma_spec.claim_slots, "climate")

    for heater_spec in modern_heater_specs():
        if component_kind not in heater_spec.component_kinds:
            continue
        if not _slots_match_component_requirements(
            slots_for_component,
            heater_spec.required_slots,
        ):
            continue
        _claim(claims, slots_for_component, heater_spec.claim_slots, "climate")

    boiler_spec = boiler_mode_spec()
    if (
        component_kind in boiler_spec.component_kinds
        and _slots_match_component_requirements(slots_for_component, boiler_spec.required_slots)
    ):
        _claim(
            claims,
            slots_for_component,
            tuple(requirement.sensor_id for requirement in boiler_spec.required_slots),
            "boiler",
        )

    heater_spec = heater_energy_spec()
    if (
        component_kind in heater_spec.component_kinds
        and _slots_match_component_requirements(slots_for_component, heater_spec.required_slots)
    ):
        _claim(
            claims,
            slots_for_component,
            tuple(requirement.sensor_id for requirement in heater_spec.required_slots),
            "heater_energy",
        )

    for fan_spec in fan_entity_specs():
        if fan_spec.component_id != component_id:
            continue
        required: tuple[int, ...]
        required_items: list[int] = [fan_spec.state_sid]
        if fan_spec.speed_mode_sid is not None:
            required_items.append(fan_spec.speed_mode_sid)
        if fan_spec.speed_sid is not None:
            required_items.append(fan_spec.speed_sid)
        required = tuple(required_items)
        if not all(sensor_id in slots_for_component for sensor_id in required):
            continue
        _claim(
            claims,
            slots_for_component,
            tuple(sensor_id for _cid, sensor_id in fan_spec.claimable_slots),
            "fan",
        )

    return claims


def invalidate_template_spec_cache() -> None:
    """Clear cached template metadata so entry reloads pick up spec updates."""
    _payload.cache_clear()
    simple_light_spec.cache_clear()
    named_light_channel_spec.cache_clear()
    awning_cover_spec.cache_clear()
    fridge_power_spec.cache_clear()
    fridge_level_spec.cache_clear()
    air_conditioner_single_zone_spec.cache_clear()
    airxcel_zone_specs.cache_clear()
    truma_panel_heater_spec.cache_clear()
    modern_heater_specs.cache_clear()
    boiler_mode_spec.cache_clear()
    heater_energy_spec.cache_clear()
    fan_entity_specs.cache_clear()


def warm_template_spec_cache() -> None:
    """Load tracked template specs into cache off the event loop."""
    _payload()
    simple_light_spec()
    named_light_channel_spec()
    awning_cover_spec()
    fridge_power_spec()
    fridge_level_spec()
    air_conditioner_single_zone_spec()
    airxcel_zone_specs()
    truma_panel_heater_spec()
    modern_heater_specs()
    boiler_mode_spec()
    heater_energy_spec()
    fan_entity_specs()
