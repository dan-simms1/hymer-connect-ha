"""Generate an app-style Home Assistant dashboard from resolved HYMER entities.

This module intentionally builds the dashboard from the integration's own
canonical capabilities, rich templates, and selected raw fallback entities.
It does not ship a fixed dashboard pack for one vehicle model.
"""

from __future__ import annotations

from dataclasses import dataclass
import json
from pathlib import Path
import re
from typing import Any, Iterable

import yaml

from .discovery import component_meta, slot_meta

APP_TAB_TITLES: dict[str, str] = {
    "dashboard": "Dashboard",
    "info": "Info",
    "water": "Water",
    "light": "Light",
    "energy": "Energy",
    "climate": "Climate",
    "components": "Components",
    "scenarios": "Scenarios",
}

APP_TAB_ICONS: dict[str, str] = {
    "dashboard": "mdi:view-dashboard-outline",
    "info": "mdi:information-outline",
    "water": "mdi:water-outline",
    "light": "mdi:lightbulb-group-outline",
    "energy": "mdi:flash-outline",
    "climate": "mdi:thermometer-lines",
    "components": "mdi:fridge-outline",
    "scenarios": "mdi:play-box-outline",
}

APP_TAB_ORDER: tuple[str, ...] = (
    "dashboard",
    "info",
    "water",
    "light",
    "energy",
    "climate",
    "components",
    "scenarios",
)

SECTION_ORDER: dict[str, tuple[str, ...]] = {
    "info": (
        "Connectivity",
        "Location",
        "Doors",
        "Chassis Information",
        "Basic Data",
    ),
    "water": (
        "Controls",
        "Water Levels",
        "Water Tanks",
        "Toilet",
    ),
    "light": (
        "Communal Lights",
        "Personal Lights",
        "Outside Lights",
        "Other Lights",
    ),
    "energy": (
        "Controls",
        "Electricity",
        "Solar Panel",
        "Gas",
    ),
    "climate": (
        "Heater",
        "Air Conditioning",
        "Warm Water",
        "Fans",
        "Other Climate Controls",
    ),
    "components": (
        "Fridge",
        "Awning",
        "Other Components",
    ),
    "scenarios": ("Scenes",),
}

CANONICAL_LAYOUT: dict[str, tuple[str, str, int]] = {
    "fresh_water_level": ("water", "Water Levels", 10),
    "waste_water_level": ("water", "Water Levels", 20),
    "black_water_level": ("water", "Water Levels", 30),
    "grey_cartridge_level": ("water", "Toilet", 10),
    "flush_cartridge_level": ("water", "Toilet", 20),
    "black_cartridge_level": ("water", "Toilet", 30),
    "flush_tank_available": ("water", "Toilet", 40),
    "tank_unit_available": ("water", "Toilet", 50),
    "cartridge_unit_available": ("water", "Toilet", 60),
    "pulsing_flush_status": ("water", "Toilet", 70),
    "reuse_grey_water_status": ("water", "Toilet", 80),
    "living_battery_voltage": ("energy", "Electricity", 10),
    "living_battery_current": ("energy", "Electricity", 20),
    "battery_soc": ("energy", "Electricity", 30),
    "battery_temperature": ("energy", "Electricity", 40),
    "battery_state_of_health": ("energy", "Electricity", 50),
    "battery_time_remaining": ("energy", "Electricity", 60),
    "battery_capacity_remaining": ("energy", "Electricity", 70),
    "battery_relative_capacity": ("energy", "Electricity", 80),
    "available_capacity": ("energy", "Electricity", 90),
    "dischargeable_capacity": ("energy", "Electricity", 100),
    "nominal_capacity": ("energy", "Electricity", 110),
    "battery_charge_detected": ("energy", "Electricity", 120),
    "charger_current": ("energy", "Electricity", 130),
    "starter_battery_voltage": ("energy", "Electricity", 140),
    "shoreline_connected": ("energy", "Electricity", 150),
    "solar_voltage": ("energy", "Solar Panel", 10),
    "solar_current": ("energy", "Solar Panel", 20),
    "solar_panel_power": ("energy", "Solar Panel", 30),
    "solar_active": ("energy", "Solar Panel", 40),
    "solar_reduced_power": ("energy", "Solar Panel", 50),
    "solar_aes_active": ("energy", "Solar Panel", 60),
    "lpg_level": ("energy", "Gas", 10),
    "second_lpg_level": ("energy", "Gas", 20),
    "water_pump": ("water", "Controls", 10),
    "main_switch": ("energy", "Controls", 10),
    "inverter_enabled": ("energy", "Controls", 20),
    "charger_enabled": ("energy", "Controls", 30),
    "charger_state": ("energy", "Electricity", 160),
    "charge_voltage": ("energy", "Electricity", 170),
    "charger_input_voltage": ("energy", "Electricity", 180),
    "charger_input_current": ("energy", "Electricity", 190),
    "charger_input_frequency": ("energy", "Electricity", 200),
    "inverter_state": ("energy", "Electricity", 210),
    "inverter_l_1_voltage": ("energy", "Electricity", 220),
    "inverter_l_1_current": ("energy", "Electricity", 230),
    "inverter_l_1_frequency": ("energy", "Electricity", 240),
    "inverter_l_2_voltage": ("energy", "Electricity", 250),
    "inverter_l_2_current": ("energy", "Electricity", 260),
    "inverter_l_2_frequency": ("energy", "Electricity", 270),
    "scu_voltage": ("energy", "Electricity", 280),
    "lte_connection_state": ("info", "Connectivity", 10),
    "vehicle_movement": ("info", "Connectivity", 20),
    "battery_cutoff_switch": ("energy", "Controls", 40),
}

RAW_LABEL_LAYOUT: dict[str, tuple[str, str, int]] = {
    "odometer": ("info", "Chassis Information", 10),
    "mileage": ("info", "Chassis Information", 10),
    "distance_to_service": ("info", "Chassis Information", 20),
    "fuel_level": ("info", "Chassis Information", 30),
    "adblue_remaining_distance": ("info", "Chassis Information", 40),
    "outside_temperature": ("info", "Chassis Information", 50),
    "wiping_water": ("info", "Chassis Information", 60),
    "signal_quality": ("info", "Connectivity", 30),
    "gps_signal_quality": ("info", "Connectivity", 40),
    "central_locking": ("info", "Doors", 10),
    "central_locking_status": ("info", "Doors", 10),
    "fresh_water": ("water", "Water Tanks", 10),
    "grey_water": ("water", "Water Tanks", 20),
    "waste_water": ("water", "Water Tanks", 20),
    "black_water": ("water", "Water Tanks", 30),
    "power_limit": ("climate", "Heater", 60),
    "solar_energy": ("energy", "Solar Panel", 35),
    "vehicle_model": ("info", "Basic Data", 10),
    "vehicle_model_year": ("info", "Basic Data", 20),
    "vehicle_vin": ("info", "Basic Data", 30),
    "vin": ("info", "Basic Data", 30),
}

FRIDGE_LABEL_LAYOUT: dict[str, tuple[str, str, int]] = {
    "fridge_power": ("components", "Fridge", 10),
    "fridge_level": ("components", "Fridge", 20),
    "door_open": ("components", "Fridge", 30),
    "night_mode": ("components", "Fridge", 40),
    "dcvoltage": ("components", "Fridge", 50),
    "warning_error_information": ("components", "Fridge", 60),
    "error_warning_information": ("components", "Fridge", 70),
    "error_information": ("components", "Fridge", 80),
}

LIGHT_BUCKET_ORDER: tuple[str, ...] = ("communal", "personal", "outside", "other")
LIGHT_BUCKET_SECTION: dict[str, str] = {
    "communal": "Communal Lights",
    "personal": "Personal Lights",
    "outside": "Outside Lights",
    "other": "Other Lights",
}

LIGHT_OUTSIDE_KEYWORDS = (
    "outside",
    "outdoor",
    "awning",
    "garage",
    "porch",
)

LIGHT_PERSONAL_KEYWORDS = (
    "bed",
    "bedroom",
    "night",
    "reading",
    "personal",
    "bathroom",
    "washroom",
    "toilet",
)

LIGHT_COMMUNAL_KEYWORDS = (
    "living",
    "lounge",
    "kitchen",
    "dining",
    "seating",
    "communal",
    "main",
)

SUMMARY_PRIORITY: tuple[str, ...] = (
    "battery_soc",
    "living_battery_voltage",
    "starter_battery_voltage",
    "shoreline_connected",
    "fresh_water_level",
    "waste_water_level",
    "fuel_level",
)

OVERVIEW_GAUGE_KEYS: tuple[str, ...] = (
    "battery_soc",
    "fresh_water_level",
    "waste_water_level",
    "lpg_level",
    "fuel_level",
)

ENERGY_GAUGE_KEYS: tuple[str, ...] = (
    "battery_soc",
    "battery_state_of_health",
    "living_battery_voltage",
)

ENERGY_TREND_KEYS: tuple[tuple[str, str, str], ...] = (
    ("solar_panel_power", "Solar Power", "mdi:solar-power"),
    ("solar_current", "Solar Current", "mdi:current-dc"),
    ("living_battery_current", "Battery Current", "mdi:battery-charging"),
    ("battery_time_remaining", "Battery Runtime", "mdi:timer-outline"),
    ("charger_current", "Charger Current", "mdi:current-dc"),
    ("charger_input_current", "Charger Input Current", "mdi:current-ac"),
)

SOLAR_CHARGING_KEYS: tuple[str, ...] = (
    "solar_panel_power",
    "solar_current",
    "solar_voltage",
)

ENERGY_VOLTAGE_KEYS: tuple[str, ...] = (
    "living_battery_voltage",
    "starter_battery_voltage",
    "charge_voltage",
    "scu_voltage",
)

BATTERY_RUNTIME_KEYS: tuple[str, ...] = (
    "battery_time_remaining",
    "battery_capacity_remaining",
    "battery_relative_capacity",
    "available_capacity",
    "dischargeable_capacity",
)

PERCENT_GAUGE_SEVERITY: dict[str, int] = {
    "green": 50,
    "yellow": 25,
    "red": 10,
}

HEALTH_GAUGE_SEVERITY: dict[str, int] = {
    "green": 80,
    "yellow": 60,
    "red": 40,
}

VOLTAGE_GAUGE_SEVERITY: dict[str, float] = {
    "green": 12.4,
    "yellow": 11.8,
    "red": 11,
}

DASHBOARD_NAME_OVERRIDES: dict[str, str] = {
    "battery_soc": "Battery SOC",
    "battery_state_of_health": "Battery SoH",
    "fresh_water_level": "Fresh Water",
    "waste_water_level": "Grey Water",
    "black_water_level": "Black Water",
    "living_battery_voltage": "Leisure Battery",
    "starter_battery_voltage": "Vehicle Battery",
    "charge_voltage": "Charge Voltage",
    "scu_voltage": "Smart Unit Voltage",
    "living_battery_current": "Battery Current",
    "battery_time_remaining": "Battery Runtime",
    "battery_capacity_remaining": "Battery Capacity",
    "battery_relative_capacity": "Relative Capacity",
    "available_capacity": "Available Capacity",
    "dischargeable_capacity": "Dischargeable Capacity",
    "charger_current": "Charger Current",
    "charger_input_current": "Charger Input",
    "solar_panel_power": "Solar Power",
    "solar_voltage": "Solar Voltage",
    "solar_current": "Solar Current",
    "solar_energy": "Solar Energy",
    "fuel_level": "Fuel",
    "lpg_level": "Gas",
    "second_lpg_level": "Second Gas",
    "shoreline_connected": "230 V Connection",
    "main_switch": "12 V Switch",
    "water_pump": "Water Pump",
}


@dataclass(frozen=True)
class DashboardEntity:
    """One entity placed into the generated dashboard."""

    entity_id: str
    unique_id: str
    name: str
    domain: str
    tab: str
    section: str
    order: int
    render_as: str = "entity"
    bucket: str | None = None
    source_key: str | None = None


def _parse_canonical_key(entry_id: str, unique_id: str) -> str | None:
    prefix = f"{entry_id}_canonical_"
    if unique_id.startswith(prefix):
        return unique_id[len(prefix):]
    return None


def _parse_raw_slot(entry_id: str, unique_id: str) -> tuple[int, int] | None:
    match = re.match(rf"^{re.escape(entry_id)}_b(\d+)_s(\d+)$", unique_id)
    if not match:
        return None
    return int(match.group(1)), int(match.group(2))


def _light_match(entry_id: str, unique_id: str) -> re.Match[str] | None:
    return re.match(rf"^{re.escape(entry_id)}_light_b(\d+)(?:_(.+))?$", unique_id)


def _light_bucket(name: str) -> str:
    lowered = name.lower()
    if any(keyword in lowered for keyword in LIGHT_OUTSIDE_KEYWORDS):
        return "outside"
    if any(keyword in lowered for keyword in LIGHT_PERSONAL_KEYWORDS):
        return "personal"
    if any(keyword in lowered for keyword in LIGHT_COMMUNAL_KEYWORDS):
        return "communal"
    return "other"


def describe_dashboard_entity(
    entry_id: str,
    *,
    entity_id: str,
    unique_id: str,
    name: str,
) -> DashboardEntity | None:
    """Classify one integration entity onto an app-style dashboard tab."""
    domain = entity_id.split(".", 1)[0]

    canonical_key = _parse_canonical_key(entry_id, unique_id)
    if canonical_key is not None:
        layout = CANONICAL_LAYOUT.get(canonical_key)
        if layout is None:
            return None
        render_as = "tile" if domain in {"switch", "button"} else "entity"
        return DashboardEntity(
            entity_id=entity_id,
            unique_id=unique_id,
            name=name,
            domain=domain,
            tab=layout[0],
            section=layout[1],
            order=layout[2],
            render_as=render_as,
            source_key=canonical_key,
        )

    if unique_id == f"{entry_id}_device_tracker":
        return DashboardEntity(
            entity_id,
            unique_id,
            name,
            domain,
            "info",
            "Location",
            10,
            render_as="map",
            source_key="location",
        )
    if unique_id == f"{entry_id}_restart_system":
        return None

    if unique_id == f"{entry_id}_vehicle_model":
        return DashboardEntity(
            entity_id, unique_id, name, domain, "info", "Basic Data", 10
        )
    if unique_id == f"{entry_id}_vehicle_model_year":
        return DashboardEntity(
            entity_id, unique_id, name, domain, "info", "Basic Data", 20
        )
    if unique_id == f"{entry_id}_vehicle_vin":
        return DashboardEntity(
            entity_id, unique_id, name, domain, "info", "Basic Data", 30
        )

    if unique_id.startswith(f"{entry_id}_scene_"):
        return DashboardEntity(
            entity_id,
            unique_id,
            name,
            domain,
            "scenarios",
            "Scenes",
            10,
            render_as="scene_button",
            source_key="scene",
        )

    if light_match := _light_match(entry_id, unique_id):
        bucket = _light_bucket(name)
        bus_id = int(light_match.group(1))
        has_label_suffix = light_match.group(2) is not None
        light_name = name
        if not has_label_suffix:
            component = component_meta(bus_id)
            if component is not None:
                light_name = component.name
                bucket = _light_bucket(component.name)
        return DashboardEntity(
            entity_id=entity_id,
            unique_id=unique_id,
            name=light_name,
            domain=domain,
            tab="light",
            section=LIGHT_BUCKET_SECTION[bucket],
            order=20 if has_label_suffix else 10,
            render_as="light",
            bucket=bucket,
            source_key="light_member" if has_label_suffix else "light_group",
        )

    template_layouts: tuple[tuple[str, str, str, int, str], ...] = (
        (f"{entry_id}_fridge_power_b", "components", "Fridge", 10, "tile"),
        (f"{entry_id}_fridge_level_b", "components", "Fridge", 20, "entity"),
        (f"{entry_id}_heater_b", "climate", "Heater", 10, "climate"),
        (f"{entry_id}_heater_neo_b", "climate", "Heater", 20, "climate"),
        (f"{entry_id}_heater_zone_b", "climate", "Heater", 30, "climate"),
        (f"{entry_id}_heater_energy_b", "climate", "Heater", 40, "entity"),
        (f"{entry_id}_aircon_b", "climate", "Air Conditioning", 10, "climate"),
        (f"{entry_id}_airxcel_", "climate", "Air Conditioning", 20, "climate"),
        (f"{entry_id}_boiler_mode_b", "climate", "Warm Water", 10, "entity"),
        (f"{entry_id}_fan_b", "climate", "Fans", 10, "entity"),
        (f"{entry_id}_awning_b", "components", "Awning", 10, "entity"),
    )
    for prefix, tab, section, order, render_as in template_layouts:
        if unique_id.startswith(prefix):
            return DashboardEntity(
                entity_id=entity_id,
                unique_id=unique_id,
                name=name,
                domain=domain,
                tab=tab,
                section=section,
                order=order,
                render_as=render_as,
                source_key=prefix[len(f"{entry_id}_"):].rstrip("_b"),
            )

    raw_slot = _parse_raw_slot(entry_id, unique_id)
    if raw_slot is None:
        return None

    meta = slot_meta(*raw_slot)
    if meta is None:
        return None
    component = component_meta(raw_slot[0])

    if component is not None and component.kind == "fridge":
        layout = FRIDGE_LABEL_LAYOUT.get(meta.label)
        if layout is not None:
            return DashboardEntity(
                entity_id=entity_id,
                unique_id=unique_id,
                name=name,
                domain=domain,
                tab=layout[0],
                section=layout[1],
                order=layout[2],
                source_key=meta.label,
            )

    layout = RAW_LABEL_LAYOUT.get(meta.label)
    if layout is None:
        return None
    render_as = "tile" if domain in {"switch", "button"} else "entity"
    return DashboardEntity(
        entity_id=entity_id,
        unique_id=unique_id,
        name=name,
        domain=domain,
        tab=layout[0],
        section=layout[1],
        order=layout[2],
        render_as=render_as,
        source_key=meta.label,
    )


def _dedupe_entities(items: Iterable[DashboardEntity]) -> list[DashboardEntity]:
    seen: set[str] = set()
    unique: list[DashboardEntity] = []
    for item in items:
        if item.entity_id in seen:
            continue
        seen.add(item.entity_id)
        unique.append(item)
    return unique


def _sorted_items(items: Iterable[DashboardEntity]) -> list[DashboardEntity]:
    return sorted(
        _dedupe_entities(items),
        key=lambda item: (
            APP_TAB_ORDER.index(item.tab) if item.tab in APP_TAB_ORDER else 999,
            SECTION_ORDER.get(item.tab, ()).index(item.section)
            if item.section in SECTION_ORDER.get(item.tab, ())
            else 999,
            item.order,
            item.name.lower(),
            item.entity_id,
        ),
    )


def _entities_card(
    title: str | None,
    entities: Iterable[str | dict[str, str]],
) -> dict[str, Any] | None:
    entity_refs = list(entities)
    if not entity_refs:
        return None
    card: dict[str, Any] = {
        "type": "entities",
        "show_header_toggle": False,
        "entities": entity_refs,
    }
    if title:
        card["title"] = title
    return card


def _grid_card(cards: list[dict[str, Any]], *, columns: int = 2) -> dict[str, Any] | None:
    if not cards:
        return None
    return {
        "type": "grid",
        "columns": columns,
        "square": False,
        "cards": cards,
    }


def _responsive_columns(count: int, *, maximum: int = 3) -> int:
    return max(1, min(count, maximum))


def _vertical_stack(
    cards: list[dict[str, Any]],
    *,
    title: str | None = None,
) -> dict[str, Any] | None:
    if not cards:
        return None
    card: dict[str, Any] = {"type": "vertical-stack", "cards": cards}
    if title:
        card["title"] = title
    return card


def _horizontal_stack(cards: list[dict[str, Any]]) -> dict[str, Any] | None:
    if not cards:
        return None
    return {"type": "horizontal-stack", "cards": cards}


def _section_stack(
    title: str,
    cards: list[dict[str, Any]],
    *,
    force_stack_title: bool = False,
) -> dict[str, Any] | None:
    if not cards:
        return None
    titled_cards = list(cards)
    first = dict(titled_cards[0])
    if first.get("type") in {"entities", "grid"}:
        if "title" not in first and len(titled_cards) == 1 and not force_stack_title:
            first["title"] = title
    titled_cards[0] = first
    if len(titled_cards) == 1 and not force_stack_title:
        return titled_cards[0]
    return _vertical_stack(titled_cards, title=title)


def _dashboard_name_prefixes(title: str) -> tuple[str, ...]:
    normalized = re.sub(r"\s+", " ", title).strip()
    candidates = [normalized]
    suffix = " dashboard"
    if normalized.lower().endswith(suffix):
        candidates.append(normalized[: -len(suffix)])
    return tuple(
        sorted(
            {candidate for candidate in candidates if candidate},
            key=len,
            reverse=True,
        )
    )


def _display_name(item: DashboardEntity, dashboard_title: str) -> str:
    if item.source_key in DASHBOARD_NAME_OVERRIDES:
        return DASHBOARD_NAME_OVERRIDES[item.source_key]
    name = re.sub(r"\s+", " ", item.name).strip()
    for prefix in _dashboard_name_prefixes(dashboard_title):
        if not name.lower().startswith(prefix.lower()):
            continue
        stripped = name[len(prefix):].lstrip(" -:|")
        if stripped:
            return stripped
    return name


def _entity_ref(item: DashboardEntity, dashboard_title: str) -> dict[str, str]:
    return {
        "entity": item.entity_id,
        "name": _display_name(item, dashboard_title),
    }


def _light_card(item: DashboardEntity, dashboard_title: str) -> dict[str, Any]:
    return {
        "type": "tile",
        "entity": item.entity_id,
        "name": _display_name(item, dashboard_title),
        "icon": "mdi:lightbulb-group-outline"
        if item.source_key == "light_group"
        else "mdi:lightbulb-outline",
    }


def _light_group_card(item: DashboardEntity, dashboard_title: str) -> dict[str, Any]:
    return {
        "type": "tile",
        "entity": item.entity_id,
        "name": _display_name(item, dashboard_title),
        "icon": "mdi:lightbulb-group",
        "features": [{"type": "light-brightness"}],
        "vertical": True,
    }


def _is_light_area_group(item: DashboardEntity, dashboard_title: str) -> bool:
    if item.source_key != "light_group":
        return False
    name = _display_name(item, dashboard_title).lower().strip()
    if name in {"communal", "personal", "outside", "outdoor"}:
        return True
    return bool(
        re.match(r"^\d+\s+(communal|personal|outside|outdoor)\s+lights?$", name)
    )


def _light_section_group_items(
    section: str,
    items: list[DashboardEntity],
    dashboard_title: str,
) -> list[DashboardEntity]:
    """Return the aggregate area-control light for a section, if present."""
    expected_names = {
        "Communal Lights": ("communal",),
        "Personal Lights": ("personal",),
        "Outside Lights": ("outside", "outdoor"),
    }.get(section, ())
    if not expected_names:
        return []
    candidates = [item for item in items if item.source_key == "light_group"]
    for expected in expected_names:
        for item in candidates:
            name = _display_name(item, dashboard_title).lower().strip()
            if name == expected or re.match(rf"^\d+\s+{expected}\s+lights?$", name):
                return [item]
    return []


def _light_entity_row(
    item: DashboardEntity,
    dashboard_title: str,
    *,
    name: str | None = None,
    group: bool = False,
) -> dict[str, str]:
    return {
        "entity": item.entity_id,
        "name": name or _display_name(item, dashboard_title),
        "icon": "mdi:lightbulb-group" if group else "mdi:lightbulb-outline",
    }


def _scene_button(item: DashboardEntity, dashboard_title: str) -> dict[str, Any]:
    return {
        "type": "button",
        "entity": item.entity_id,
        "name": _display_name(item, dashboard_title),
        "tap_action": {"action": "toggle"},
    }


def _climate_control_card(item: DashboardEntity, dashboard_title: str) -> dict[str, Any]:
    return {
        "type": "tile",
        "entity": item.entity_id,
        "name": _display_name(item, dashboard_title),
        "features": [
            {"type": "climate-hvac-modes"},
            {"type": "target-temperature"},
        ],
    }


def _items_by_source_key(items: Iterable[DashboardEntity]) -> dict[str, DashboardEntity]:
    by_key: dict[str, DashboardEntity] = {}
    for item in items:
        if item.source_key is None or item.source_key in by_key:
            continue
        by_key[item.source_key] = item
    return by_key


def _gauge_card(item: DashboardEntity, dashboard_title: str) -> dict[str, Any]:
    card: dict[str, Any] = {
        "type": "gauge",
        "entity": item.entity_id,
        "name": _display_name(item, dashboard_title),
        "needle": True,
    }
    if item.source_key in {
        "living_battery_voltage",
        "starter_battery_voltage",
        "charge_voltage",
        "scu_voltage",
    }:
        card.update(
            {
                "min": 10,
                "max": 15,
                "severity": VOLTAGE_GAUGE_SEVERITY,
            }
        )
    else:
        card.update(
            {
                "min": 0,
                "max": 100,
                "severity": HEALTH_GAUGE_SEVERITY
                if item.source_key == "battery_state_of_health"
                else PERCENT_GAUGE_SEVERITY,
            }
        )
    return card


def _sensor_graph_card(
    item: DashboardEntity,
    *,
    name: str,
    icon: str,
) -> dict[str, Any]:
    return {
        "type": "sensor",
        "entity": item.entity_id,
        "name": name,
        "icon": icon,
        "graph": "line",
        "hours_to_show": 24,
        "detail": 2,
    }


def _history_graph_card(
    title: str,
    items: Iterable[DashboardEntity],
    dashboard_title: str,
    *,
    hours_to_show: int = 24,
) -> dict[str, Any] | None:
    entities = [
        _entity_ref(item, dashboard_title)
        for item in _dedupe_entities(items)
    ]
    if not entities:
        return None
    return {
        "type": "history-graph",
        "title": title,
        "hours_to_show": hours_to_show,
        "entities": entities,
    }


def _map_card(
    title: str,
    items: Iterable[DashboardEntity],
) -> dict[str, Any] | None:
    entities = [item.entity_id for item in _dedupe_entities(items)]
    if not entities:
        return None
    return {
        "type": "map",
        "title": title,
        "entities": entities,
        "hours_to_show": 0,
        "default_zoom": 14,
    }


def _statistics_graph_card(
    title: str,
    item: DashboardEntity,
    dashboard_title: str,
) -> dict[str, Any]:
    return {
        "type": "statistics-graph",
        "title": title,
        "entities": [_entity_ref(item, dashboard_title)],
        "stat_types": ["change"],
        "period": "day",
        "days_to_show": 30,
    }


def _summary_items(items: list[DashboardEntity]) -> list[DashboardEntity]:
    by_key = _items_by_source_key(items)
    selected: list[DashboardEntity] = []
    for key in SUMMARY_PRIORITY:
        item = by_key.get(key)
        if item is not None:
            selected.append(item)
    if selected:
        return selected
    fallback_tabs = {"water", "energy", "info"}
    return [item for item in items if item.tab in fallback_tabs and item.render_as == "entity"][:6]


def _main_action_items(items: list[DashboardEntity]) -> list[DashboardEntity]:
    preferred_keys = (
        "main_switch",
        "water_pump",
        "fridge_power",
        "inverter_enabled",
        "charger_enabled",
    )
    by_key = _items_by_source_key(items)
    selected = [
        by_key[key]
        for key in preferred_keys
        if key in by_key and by_key[key].domain in {"switch", "button"}
    ]
    selected.extend(
        item
        for item in items
        if item.source_key not in preferred_keys
        and item.render_as == "tile"
        and item.domain in {"switch", "button"}
    )
    return _dedupe_entities(selected)[:6]


def _build_dashboard_view(title: str, items: list[DashboardEntity]) -> dict[str, Any] | None:
    columns: list[dict[str, Any]] = []
    by_key = _items_by_source_key(items)

    actions = _main_action_items(items)
    action_card = _entities_card(
        None,
        [_entity_ref(item, title) for item in actions],
    )
    action_stack = _section_stack("Main Actions", [action_card] if action_card else [])
    if action_stack is not None:
        columns.append(action_stack)

    overview_gauges = [
        by_key[key]
        for key in OVERVIEW_GAUGE_KEYS
        if key in by_key
    ]
    gauge_grid = _grid_card(
        [_gauge_card(item, title) for item in overview_gauges],
        columns=1,
    )
    summary = _summary_items(items)
    summary_card = None
    if summary:
        summary_card = _entities_card(
            None,
            [_entity_ref(item, title) for item in summary],
        )
    summary_stack = _section_stack("Summary", [summary_card] if summary_card else [])

    climates = [
        item for item in items
        if item.render_as == "climate"
    ][:2]
    middle_cards: list[dict[str, Any]] = []
    if summary_stack is not None:
        middle_cards.append(summary_stack)
    if climates:
        climate_grid = _grid_card(
            [_climate_control_card(item, title) for item in climates],
            columns=1,
        )
        climate_stack = _section_stack(
            "Climate",
            [climate_grid] if climate_grid else [],
        )
        if climate_stack is not None:
            middle_cards.append(climate_stack)
    location = by_key.get("location")
    map_card = _map_card("Location", [location] if location is not None else [])
    if map_card is not None:
        middle_cards.append(map_card)

    middle_column = _vertical_stack(middle_cards)
    if middle_column is not None:
        columns.append(middle_column)

    if gauge_grid is not None:
        columns.append(gauge_grid)

    root_grid = _grid_card(columns, columns=min(len(columns), 3))

    if root_grid is None:
        return None

    return {
        "title": APP_TAB_TITLES["dashboard"],
        "path": "dashboard",
        "icon": APP_TAB_ICONS["dashboard"],
        "panel": True,
        "cards": [root_grid],
    }


def _build_energy_view(
    items: list[DashboardEntity],
    dashboard_title: str,
) -> dict[str, Any] | None:
    if not items:
        return None

    cards: list[dict[str, Any]] = []
    by_key = _items_by_source_key(items)

    trend_cards = [
        _sensor_graph_card(by_key[key], name=name, icon=icon)
        for key, name, icon in ENERGY_TREND_KEYS
        if key in by_key
    ]
    trend_grid = _grid_card(trend_cards, columns=1)
    if trend_grid is not None:
        section = _section_stack("Power Trends", [trend_grid])
        if section is not None:
            cards.append(section)

    solar_history = _history_graph_card(
        "Solar Charging (24h)",
        [by_key[key] for key in SOLAR_CHARGING_KEYS if key in by_key],
        dashboard_title,
    )
    if solar_history is not None:
        cards.append(solar_history)
    runtime_history = _history_graph_card(
        "Battery Runtime (24h)",
        [by_key[key] for key in BATTERY_RUNTIME_KEYS if key in by_key],
        dashboard_title,
    )
    if runtime_history is not None:
        cards.append(runtime_history)
    solar_energy = by_key.get("solar_energy")
    if solar_energy is not None:
        cards.append(
            _statistics_graph_card(
                "Solar Energy (kWh/day)",
                solar_energy,
                dashboard_title,
            )
        )
    voltage_history = _history_graph_card(
        "Battery Voltages (24h)",
        [by_key[key] for key in ENERGY_VOLTAGE_KEYS if key in by_key],
        dashboard_title,
    )
    if voltage_history is not None:
        cards.append(voltage_history)

    gauges = [
        by_key[key]
        for key in ENERGY_GAUGE_KEYS
        if key in by_key
    ]
    gauge_grid = _grid_card(
        [_gauge_card(item, dashboard_title) for item in gauges],
        columns=3,
    )
    if gauge_grid is not None:
        cards.append(gauge_grid)

    standard_view = _build_standard_view("energy", items, dashboard_title)
    if standard_view is not None:
        cards.extend(standard_view["cards"])

    if not cards:
        return None

    root_grid = _grid_card(cards, columns=2)
    if root_grid is None:
        return None

    return {
        "title": APP_TAB_TITLES["energy"],
        "path": "energy",
        "icon": APP_TAB_ICONS["energy"],
        "panel": True,
        "cards": [root_grid],
    }


def _build_standard_view(
    tab: str,
    items: list[DashboardEntity],
    dashboard_title: str,
) -> dict[str, Any] | None:
    if not items:
        return None

    cards: list[dict[str, Any]] = []
    ordered_sections = SECTION_ORDER.get(tab, ())
    by_section: dict[str, list[DashboardEntity]] = {}
    for item in items:
        by_section.setdefault(item.section, []).append(item)

    for section in ordered_sections:
        section_items = by_section.get(section, [])
        if not section_items:
            continue

        if tab == "info" and section == "Location":
            map_card = _map_card("Location", section_items)
            if map_card is not None:
                cards.append(map_card)
            continue

        if tab == "light":
            group_items = _light_section_group_items(
                section,
                section_items,
                dashboard_title,
            )
            group_entity_ids = {item.entity_id for item in group_items}
            member_items = [
                item
                for item in section_items
                if item.entity_id not in group_entity_ids
            ]
            light_rows = [
                *[
                    _light_entity_row(
                        item,
                        dashboard_title,
                        name="All on/off",
                        group=True,
                    )
                    for item in group_items
                ],
                *[
                    _light_entity_row(item, dashboard_title)
                    for item in member_items
                ],
            ]
            entity_card = _entities_card(
                None,
                light_rows,
            )
            section_stack = _section_stack(
                section,
                [entity_card] if entity_card else [],
                force_stack_title=True,
            )
            if section_stack is not None:
                cards.append(section_stack)
            continue

        if tab == "scenarios":
            section_cards = [
                _scene_button(item, dashboard_title)
                for item in section_items
            ]
            card = _grid_card(section_cards, columns=2)
            section_stack = _section_stack(section, [card] if card else [])
            if section_stack is not None:
                cards.append(section_stack)
            continue

        if tab == "climate":
            climate_items = [
                item for item in section_items if item.render_as == "climate"
            ]
            entity_items = [
                item for item in section_items if item.render_as != "climate"
            ]
            section_cards: list[dict[str, Any]] = []
            climate_grid = _grid_card(
                [
                    _climate_control_card(item, dashboard_title)
                    for item in climate_items
                ],
                columns=_responsive_columns(len(climate_items), maximum=3),
            )
            if climate_grid is not None:
                section_cards.append(climate_grid)
            entity_card = _entities_card(
                None,
                [_entity_ref(item, dashboard_title) for item in entity_items],
            )
            if entity_card is not None:
                section_cards.append(entity_card)
            section_stack = _section_stack(section, section_cards)
            if section_stack is not None:
                cards.append(section_stack)
            continue

        tile_items = [item for item in section_items if item.render_as == "tile"]
        entity_items = [item for item in section_items if item.render_as != "tile"]
        section_cards = []
        entity_card = _entities_card(
            None,
            [
                _entity_ref(item, dashboard_title)
                for item in [*tile_items, *entity_items]
            ],
        )
        if entity_card is not None:
            section_cards.append(entity_card)
        section_stack = _section_stack(section, section_cards)
        if section_stack is not None:
            cards.append(section_stack)

    if not cards:
        return None

    view = {
        "title": APP_TAB_TITLES[tab],
        "path": tab,
        "icon": APP_TAB_ICONS[tab],
        "cards": cards,
    }
    if tab == "climate":
        root_grid = _grid_card(cards, columns=_responsive_columns(len(cards), maximum=2))
        if root_grid is not None:
            view["panel"] = True
            view["cards"] = [root_grid]
    return view


def build_dashboard_config(
    title: str,
    items: Iterable[DashboardEntity],
) -> dict[str, Any]:
    """Build the full Lovelace dashboard config for one HYMER entry."""
    sorted_items = _sorted_items(items)
    by_tab: dict[str, list[DashboardEntity]] = {}
    for item in sorted_items:
        by_tab.setdefault(item.tab, []).append(item)

    views: list[dict[str, Any]] = []
    dashboard_view = _build_dashboard_view(title, sorted_items)
    if dashboard_view is not None:
        views.append(dashboard_view)

    for tab in APP_TAB_ORDER[1:]:
        if tab == "energy":
            view = _build_energy_view(by_tab.get(tab, []), title)
        else:
            view = _build_standard_view(tab, by_tab.get(tab, []), title)
        if view is not None:
            views.append(view)

    return {
        "title": title,
        "views": views,
    }


def write_dashboard_yaml(path: Path, config: dict[str, Any]) -> None:
    """Write a generated Lovelace YAML file to disk."""
    path.parent.mkdir(parents=True, exist_ok=True)
    rendered = yaml.safe_dump(
        config,
        allow_unicode=True,
        default_flow_style=False,
        sort_keys=False,
        width=1000,
    )
    header = (
        "# Generated by the HYMER Connect Metadata dashboard generator.\n"
        "# Regenerate this file after adding hardware support, changing entity naming,\n"
        "# or enabling additional capabilities.\n\n"
    )
    path.write_text(header + rendered)


def write_dashboard_storage(
    config_dir: Path,
    *,
    storage_id: str,
    url_path: str,
    title: str,
    config: dict[str, Any],
    icon: str = "mdi:caravan",
) -> None:
    """Persist the generated dashboard as a Lovelace storage dashboard."""
    storage_dir = config_dir / ".storage"
    storage_dir.mkdir(parents=True, exist_ok=True)

    dashboards_path = storage_dir / "lovelace_dashboards"
    if dashboards_path.exists():
        dashboards_payload = json.loads(dashboards_path.read_text())
    else:
        dashboards_payload = {
            "version": 1,
            "minor_version": 1,
            "key": "lovelace_dashboards",
            "data": {"items": []},
        }

    items = dashboards_payload.setdefault("data", {}).setdefault("items", [])
    dashboard_item = {
        "id": storage_id,
        "show_in_sidebar": True,
        "icon": icon,
        "title": title,
        "require_admin": False,
        "mode": "storage",
        "url_path": url_path,
    }

    replaced = False
    for index, item in enumerate(items):
        if item.get("id") == storage_id or item.get("url_path") == url_path:
            items[index] = dashboard_item
            replaced = True
            break
    if not replaced:
        items.append(dashboard_item)

    dashboards_path.write_text(
        json.dumps(dashboards_payload, indent=2, sort_keys=False) + "\n"
    )

    dashboard_path = storage_dir / f"lovelace.{storage_id}"
    dashboard_path.write_text(
        json.dumps(
            {
                "version": 1,
                "minor_version": 1,
                "key": f"lovelace.{storage_id}",
                "data": {"config": config},
            },
            indent=2,
            sort_keys=False,
        )
        + "\n"
    )
