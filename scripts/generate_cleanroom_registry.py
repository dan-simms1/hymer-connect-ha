"""Generate normalized runtime metadata for discovery and templating.

The generated data is used at runtime by the integration. It covers:

- component grouping and naming
- slot typing, labels, units, and transforms
- validated writable control metadata
- vehicle model/catalog metadata
- scenario and scene metadata

Note:
- the historical "cleanroom" filename predates the current provenance wording
- this generator intentionally derives interoperability metadata from the
  user's local HYMER app artefact; it is not a claim that the inputs are
  independent of the app bundle
"""

from __future__ import annotations

import argparse
import ast
import copy
import json
import re
import sys
from pathlib import Path
import types
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
CUSTOM_COMPONENTS_DIR = ROOT / "custom_components"
INTEGRATION_DIR = CUSTOM_COMPONENTS_DIR / "hymer_connect_metadata"

if "custom_components" not in sys.modules:
    custom_components = types.ModuleType("custom_components")
    custom_components.__path__ = [str(CUSTOM_COMPONENTS_DIR)]
    sys.modules["custom_components"] = custom_components
if "custom_components.hymer_connect_metadata" not in sys.modules:
    integration = types.ModuleType("custom_components.hymer_connect_metadata")
    integration.__path__ = [str(INTEGRATION_DIR)]
    sys.modules["custom_components.hymer_connect_metadata"] = integration

from custom_components.hymer_connect_metadata.template_specs import rich_template_claims

DEFAULT_REGISTRY = ROOT / "reference" / "full_registry.json"
DEFAULT_I18N = ROOT / "reference" / "i18n_en.json"
DEFAULT_SCENARIOS = ROOT / "reference" / "scenario_templates.json"
DEFAULT_VEHICLE_GROUPS = ROOT / "reference" / "vehicle_groups.json"
DEFAULT_BUNDLE = ROOT / "reference" / "bundle.js"
DEFAULT_PIA_DECODER = ROOT / "custom_components" / "hymer_connect_metadata" / "pia_decoder.py"
DEFAULT_PROVIDER_SPECS = (
    ROOT / "custom_components" / "hymer_connect_metadata" / "specs" / "provider_specs.json"
)
DEFAULT_TEMPLATE_SPECS = (
    ROOT / "custom_components" / "hymer_connect_metadata" / "specs" / "template_specs.json"
)
DEFAULT_COMPONENTS_OUT = (
    ROOT / "custom_components" / "hymer_connect_metadata" / "data" / "component_kinds.json"
)
DEFAULT_SLOTS_OUT = (
    ROOT / "custom_components" / "hymer_connect_metadata" / "data" / "sensor_labels.json"
)
DEFAULT_CONTROLS_OUT = (
    ROOT / "custom_components" / "hymer_connect_metadata" / "data" / "control_catalog.json"
)
DEFAULT_VEHICLES_OUT = (
    ROOT / "custom_components" / "hymer_connect_metadata" / "data" / "vehicle_catalog.json"
)
DEFAULT_SCENARIO_OUT = (
    ROOT / "custom_components" / "hymer_connect_metadata" / "data" / "scenario_catalog.json"
)
DEFAULT_COVERAGE_OUT = (
    ROOT / "custom_components" / "hymer_connect_metadata" / "data" / "coverage_audit.json"
)
DEFAULT_SUPPORT_MATRIX_OUT = (
    ROOT / "custom_components" / "hymer_connect_metadata" / "data" / "support_matrix.json"
)

LIGHT_COMPONENT_RE = re.compile(r"^Light(Circuit|Group)\d+$")
LIGHTING_MODULE_RE = re.compile(r"^(LIM|ToptronDimmer|HegotecLightModule)")
LIGHT_CIRCUIT_KEY_RE = re.compile(r"^LightCircuit(\d+)$")
LIGHT_GROUP_KEY_RE = re.compile(r"^LightGroup0*(\d+)$")
TRUMA_COMBI_NAMES = {"TrumaCombi_E", "TrumaCombi", "TrumaCombi_D", "TrumaCombi_DE"}
AIRCON_COMPONENT_RE = re.compile(
    r"^(TrumaAventa|TrumaSaphir|TelecoTelairDualClima|AirxcelACGateway)"
)
HEATER_NEO_RE = re.compile(r"^TrumaCombiNeo")
FRIDGE_POWER_NAMES = {"FridgeOn", "FridgeON", "AbsorberOn"}
WATER_LEVEL_NAMES = {
    "FreshWaterLevel",
    "FreshWaterTankLevel",
    "WasteWaterLevel",
    "GrayWaterLevel",
    "GreyWaterTankLevel",
    "BlackWaterLevel",
    "BlackWaterTankLevel",
    "BlackwaterLevel",
}

SAFE_STRING_WRITE_LABELS = {
    "main_switch",
    "heater_air_energy_source",
    "heater_water_energy_source",
    "water_heater_mode",
    "aircon_mode",
    "fan_mode",
    "power_mode",
    "set_power_mode",
    "fridge_mode",
    "mode",
}

AIRXCEL_AC_MODES = ["OFF", "COOL", "HEAT", "AUTO_HEAT_COOL", "FAN_ONLY", "AUX_HEAT"]
AIRXCEL_BINARY_STATES = ["OFF", "ON"]
AIRXCEL_AUTO_ON = ["AUTO", "ON"]
AIRXCEL_AUTO_FORCED = ["AUTO", "FORCED"]
AIRXCEL_AUTO_MANUAL = ["AUTO", "MANUAL"]
AIRXCEL_FAN_SPEEDS = ["LOW", "MED", "HIGH"]
AIRXCEL_ROOF_FAN_SPEEDS = ["LOW", "MEDIUM", "HIGH"]
AIRXCEL_AIRFLOW = ["AIR_OUT", "AIR_IN"]
AIRXCEL_DOME_POSITIONS = ["CLOSE", "N_1_4_OPEN", "N_1_2_OPEN", "N_3_4_OPEN", "OPEN", "STOP"]
TRUMA_NEO_WATER_MODES = ["OFF", "COMFORT", "SHOWER", "NIGHT", "AWAY", "DESCALING"]
TRUMA_NEO_AIR_MODES = ["OFF", "HEATING", "VENTILATING"]
TRUMA_NEO_AIR_SUBMODES = ["OFF", "COMFORT", "BOOST", "AWAY", "NIGHT"]
TRUMA_NEO_FAN_MODES = [
    "OFF",
    "LOW",
    "MID",
    "HIGH",
    "FAN_LEVEL_1",
    "FAN_LEVEL_2",
    "FAN_LEVEL_3",
    "FAN_LEVEL_4",
    "FAN_LEVEL_5",
]
TRUMA_NEO_FUEL_ONLY = ["OFF", "FUEL"]
TRUMA_NEO_E_ENERGY = ["OFF", "FUEL", "ELECTRICITY", "HYBRID"]
TRUMA_NEO_POWER_LIMIT = ["OFF", "N_1_K_W", "N_2_K_W", "N_3_K_W"]
TIMBERLINE_WATER_MODES = ["OFF", "COMBUSTION", "ELECTRIC", "HYBRID"]
TIMBERLINE_FURNACE_MODES = ["AUTO", "MANUAL"]
TIMBERLINE_AIR_MODES = ["OFF", "HEAT", "FAN_ONLY"]
TIMBERLINE_CIRCULATION_PUMP = ["OFF", "ON", "TEST"]
DOMETIC_COMPRESSOR_USER_MODES = ["PERFORMANCE_COOLING", "SILENT_MODE", "TURBO_MODE"]
DELLCOOL_POWER_MODES = ["NORMAL_MODE", "SILENT_MODE", "AUTO_MODE"]
INDELB_POWER_MODES = ["NORMAL_MODE", "NIGHT_MODE", "TURBO_MODE", "NIGHT_AND_TURBO_MODE"]
MAXXFAN_SPEEDS = ["OFF", "LOW", "MEDIUM", "HIGH"]
AIRCON_MODE_OPTIONS = ["OFF", "FAN", "COOL", "HEAT", "DEHUMIDIFY", "VENTILATION", "AUTO"]
BOILER_MODE_OPTIONS = ["OFF", "ECO", "HOT"]
FAN_MODE_OPTIONS = ["OFF", "LOW", "MID", "HIGH", "NIGHT", "AUTO"]
FRIDGE_POWER_OPTIONS = ["GAS", "VOLTAGE12", "VOLTAGE230", "AUTO", "NONE"]
FRIDGE_MODE_OPTIONS = ["NORMAL", "TURBO", "NIGHT", "SILENT"]
HEATER_ENERGY_OPTIONS = ["Diesel", "Electricity", "Both"]
SWITCH_PAD_MODE_OPTIONS = ["ON_BOARD_MODE", "AWAY_MODE", "SLEEP_MODE"]

GLOBAL_LABEL_OVERRIDES = {
    "On": "on_off",
    "Brightness": "brightness",
    "Color": "color_temp",
    "FreshWaterLevel": "fresh_water_level",
    "FreshWaterTankLevel": "fresh_water_level",
    "WasteWaterLevel": "waste_water_level",
    "GrayWaterLevel": "waste_water_level",
    "GreyWaterTankLevel": "waste_water_level",
    "BlackWaterLevel": "black_water_level",
    "BlackWaterTankLevel": "black_water_level",
    "BlackwaterLevel": "black_water_level",
    "SwitchPump": "water_pump",
    "LivingBatteryVoltage": "living_battery_voltage",
    "LivingBatteryCurrent": "living_battery_current",
    "LivingBatteryCapacity": "living_battery_capacity",
    "LivingBatteryType": "battery_type",
    "StarterBatteryVoltage": "starter_battery_voltage",
    "ShoreLineConnected": "shoreline_connected",
    "FreshWaterSensorFailure": "fresh_water_sensor_failure",
    "WasteWaterSensorFailure": "waste_water_sensor_failure",
    "DPlusState": "d_plus_state",
    "DoorOpen": "door_open",
    "Firmware": "firmware",
    "WarningErrorInformation": "warning_error_information",
    "ErrorWarningInformation": "error_warning_information",
    "DeviceFailure": "device_failure",
    "TargetRoomTemperature": "target_room_temperature",
    "ActualRoomTemperature": "actual_room_temperature",
    "AirConMode": "aircon_mode",
    "AirConError": "aircon_error",
    "ChargingCurrent": "solar_current",
    "SolarPanelVoltage": "solar_voltage",
    "SolarPanelPower": "solar_panel_power",
    "SolarVoltage": "solar_voltage",
    "SolarCurrent": "solar_current",
    "SolarActive": "solar_active",
    "ReducedPower": "solar_reduced_power",
    "AESActive": "solar_aes_active",
    "Mileage": "odometer",
    "NextService": "distance_to_service",
    "FuelTankLevel": "fuel_level",
    "AdBlueRemainDistance": "adblue_remaining_distance",
    "DistanceToService": "distance_to_service",
    "VIN": "vin",
    "EngineRunning": "engine_running",
    "ExternalTemperatureC": "outside_temperature",
    "BatteryVoltage": "battery_voltage",
    "BatteryCurrent": "battery_current",
    "BatteryTemperature": "battery_temperature",
    "BatteryStateOfCharge": "battery_soc",
    "BatteryTimeRemaining": "battery_time_remaining",
    "BatteryStateOfHealth": "battery_state_of_health",
    "BatteryCapacityRemaining": "battery_capacity_remaining",
    "BatteryRelativeCapacity": "battery_relative_capacity",
    "BatteryChargeDetected": "battery_charge_detected",
    "WaterPumpEnable": "water_pump",
}

CONTEXT_LABEL_OVERRIDES = {
    ("habitation", "12VSupply"): "main_switch",
    ("habitation", "ChargeMode"): "charge_phase",
    ("habitation", "ShoreLine"): "power_source",
    ("lighting_module", "12VSupply"): "lighting_module_12v_supply",
    ("lighting_module", "DPlusState"): "lighting_module_d_plus",
    ("lighting_module", "AllLightPower"): "lighting_module_all_off",
    ("lighting_module", "Firmware"): "lighting_module_firmware",
    ("fridge", "FridgeOn"): "fridge_power",
    ("fridge", "FridgeON"): "fridge_power",
    ("fridge", "AbsorberOn"): "fridge_power",
    ("truma_heater", "AirTemperatureEnergySource"): "heater_air_energy_source",
    ("truma_heater", "TargetWaterTemperature"): "water_heater_mode",
    ("truma_heater", "WaterTemperatureEnergySource"): "heater_water_energy_source",
    ("truma_heater", "AirTemperatureMode"): "heater_air_mode",
    ("heater_neo", "AirTemperatureEnergySource"): "heater_air_energy_source",
    ("heater_neo", "TargetWaterTemperature"): "water_heater_mode",
    ("heater_neo", "WaterTemperatureEnergySource"): "heater_water_energy_source",
    ("heater_neo", "AirTemperatureMode"): "heater_air_mode",
}

UNIT_OVERRIDES = {
    "step": None,
    "C": "°C",
    "degC": "°C",
    "°C": "°C",
    "F": "°F",
    "degF": "°F",
    "°F": "°F",
    "seconds": "s",
    "day": "d",
    "H": None,
    "M": None,
    "hh:mm": None,
}

BRAND_PREFIXES = {
    "HY": "Hymer",
    "DE": "Dethleffs",
    "LM": "LMC",
    "BU": "Buerstner",
    "ER": "Eriba",
    "LA": "Laika",
    "NB": "Niesmann+Bischoff",
    "SU": "Sunlight",
    "CA": "Carado",
    "TH": "Thor",
}

_VAR_ASSIGN_RE = re.compile(r"^\s*(r\d+)\s*=\s*(.+);\s*$")
_PROP_ASSIGN_RE = re.compile(r"^\s*(r\d+)\['([^']+)'\]\s*=\s*(.+);\s*$")
_INDEX_ASSIGN_RE = re.compile(r"^\s*(r\d+)\[(\d+)\]\s*=\s*(.+);\s*$")
_VAR_REF_RE = re.compile(r"^r\d+$")
_VAR_ATTR_REF_RE = re.compile(r"^(r\d+)(\.[A-Za-z_][A-Za-z0-9_]*)+$")
_NEW_ARRAY_RE = re.compile(r"^new Array\((\d+)\)$")
_BUNDLE_LIGHT_CIRCUIT_LABEL_RE = re.compile(
    r"'LIGHT_CIRCUIT_(\d+)'\s*:\s*'([^']+)'"
)
_BUNDLE_LIGHT_GROUP_LABEL_RE = re.compile(
    r"'LIGHT_GROUP_(\d+)'\s*:\s*'([^']+)'"
)
_BUNDLE_HEGOTEC_LABEL_RE = re.compile(
    r"'HEGOTEC_LIGHT_MODULE'\s*:\s*'([^']+)'"
)
def _normalize_js_literal(text: str) -> str:
    """Convert a JS-like literal subset into something ast.literal_eval accepts."""
    out: list[str] = []
    token: list[str] = []
    quote: str | None = None
    escape = False

    def flush_token() -> None:
        if not token:
            return
        value = "".join(token)
        if value == "true":
            out.append("True")
        elif value == "false":
            out.append("False")
        elif value == "null":
            out.append("None")
        else:
            out.append(value)
        token.clear()

    for char in text:
        if quote is not None:
            out.append(char)
            if escape:
                escape = False
            elif char == "\\":
                escape = True
            elif char == quote:
                quote = None
            continue

        if char in {"'", '"'}:
            flush_token()
            quote = char
            out.append(char)
            continue

        if char.isalnum() or char == "_":
            token.append(char)
            continue

        flush_token()
        out.append(char)

    flush_token()
    return "".join(out)


def _parse_js_literal(text: str) -> Any:
    return ast.literal_eval(_normalize_js_literal(text))


def _parse_rhs(rhs: str, variables: dict[str, Any]) -> Any:
    rhs = rhs.strip()
    array_match = _NEW_ARRAY_RE.match(rhs)
    if array_match:
        return []
    if _VAR_REF_RE.match(rhs):
        return variables.get(rhs)
    attr_match = _VAR_ATTR_REF_RE.match(rhs)
    if attr_match:
        current = variables.get(attr_match.group(1))
        for segment in rhs.split(".")[1:]:
            if not isinstance(current, dict):
                return None
            current = current.get(segment)
        return current
    return _parse_js_literal(rhs)


def _ensure_array_slot(values: list[Any], index: int) -> None:
    while len(values) <= index:
        values.append(None)


def _extract_bundle_runtime_data(
    bundle_path: Path,
) -> tuple[dict[tuple[int, int], dict[str, Any]], list[dict[str, Any]]]:
    """Extract richer slot metadata and built-in scenarios/scenes from bundle.js."""
    if not bundle_path.exists():
        return {}, []

    variables: dict[str, Any] = {}
    slot_defs: dict[tuple[int, int], dict[str, Any]] = {}
    scenario_defs: list[dict[str, Any]] = []

    with bundle_path.open(encoding="utf-8", errors="replace") as bundle_file:
        for raw_line in bundle_file:
            line = raw_line.rstrip("\n")

            match = _VAR_ASSIGN_RE.match(line)
            if match:
                var_name, rhs = match.groups()
                try:
                    value = _parse_rhs(rhs, variables)
                except Exception:
                    continue
                variables[var_name] = value
                if (
                    isinstance(value, dict)
                    and {"componentId", "id", "name", "datatype"} <= set(value)
                ):
                    component_id = value.get("componentId")
                    sensor_id = value.get("id")
                    if isinstance(component_id, int) and isinstance(sensor_id, int):
                        slot_defs[(component_id, sensor_id)] = value
                elif (
                    isinstance(value, dict)
                    and isinstance(value.get("name"), str)
                    and isinstance(value.get("id"), int)
                    and str(value["name"]).startswith(("SCENARIOS.", "SCENES."))
                ):
                    scenario_defs.append(value)
                continue

            match = _PROP_ASSIGN_RE.match(line)
            if match:
                var_name, prop_name, rhs = match.groups()
                target = variables.get(var_name)
                if not isinstance(target, dict):
                    continue
                try:
                    target[prop_name] = _parse_rhs(rhs, variables)
                except Exception:
                    continue
                continue

            match = _INDEX_ASSIGN_RE.match(line)
            if match:
                var_name, index_text, rhs = match.groups()
                target = variables.get(var_name)
                if not isinstance(target, (list, dict)):
                    continue
                try:
                    value = _parse_rhs(rhs, variables)
                except Exception:
                    continue
                index = int(index_text)
                if isinstance(target, list):
                    _ensure_array_slot(target, index)
                    target[index] = value
                else:
                    target[index] = value

    normalized_slots: dict[tuple[int, int], dict[str, Any]] = {}
    for key, value in slot_defs.items():
        normalized_slots[key] = {
            "component_id": int(value["componentId"]),
            "sensor_id": int(value["id"]),
            "name": value.get("name"),
            "mode": value.get("mode", "r"),
            "datatype": value.get("datatype"),
            "unit": value.get("unit"),
            "options": list(value.get("stringRange") or []),
            "range": dict(value["range"]) if isinstance(value.get("range"), dict) else None,
            "description": value.get("description"),
            "deprecated": bool(value.get("deprecated", False)),
        }

    normalized_scenarios: list[dict[str, Any]] = []
    seen_scenario_keys: set[tuple[str, int]] = set()
    for entry in scenario_defs:
        name = entry.get("name")
        if not isinstance(name, str) or not name.startswith(("SCENARIOS.", "SCENES.")):
            continue
        if "description" not in entry or "components" not in entry:
            continue
        key = (name, int(entry.get("id", 0)))
        if key in seen_scenario_keys:
            continue
        seen_scenario_keys.add(key)
        actions: list[dict[str, Any]] = []
        for action in entry.get("components", []):
            if not isinstance(action, dict):
                continue
            if {"componentId", "valueId", "value"} <= set(action):
                actions.append(
                    {
                        "component_id": int(action["componentId"]),
                        "sensor_id": int(action["valueId"]),
                        "value": action["value"],
                    }
                )
        normalized_scenarios.append(
            {
                "id": entry.get("id"),
                "version": entry.get("version"),
                "name": name,
                "description": entry.get("description"),
                "icon": entry.get("icon"),
                "preview_icon": entry.get("previewIcon"),
                "image": entry.get("image"),
                "scene_id": entry.get("sceneId"),
                "actions": actions,
                "checklist_count": len(entry.get("checklist") or []),
            }
        )

    return normalized_slots, normalized_scenarios


def _extract_bundle_registry_data(
    bundle_path: Path,
) -> tuple[dict[str, Any], list[dict[str, Any]], dict[Any, Any]]:
    """Extract component and vehicle metadata from an expanded bundle.js."""
    if not bundle_path.exists():
        return {}, [], {}

    variables: dict[str, Any] = {}
    component_defs: dict[int, dict[str, Any]] = {}
    vehicles: list[dict[str, Any]] = []
    vehicle_group_variant: dict[Any, Any] = {}

    with bundle_path.open(encoding="utf-8", errors="replace") as bundle_file:
        for raw_line in bundle_file:
            line = raw_line.rstrip("\n")

            match = _VAR_ASSIGN_RE.match(line)
            if match:
                var_name, rhs = match.groups()
                try:
                    value = _parse_rhs(rhs, variables)
                except Exception:
                    continue
                variables[var_name] = value
                if (
                    isinstance(value, dict)
                    and isinstance(value.get("id"), int)
                    and isinstance(value.get("name"), str)
                    and "capabilities" in value
                    and "settings" in value
                ):
                    component_defs[int(value["id"])] = {"name": str(value["name"])}
                continue

            match = _PROP_ASSIGN_RE.match(line)
            if match:
                var_name, prop_name, rhs = match.groups()
                try:
                    value = _parse_rhs(rhs, variables)
                except Exception:
                    continue
                if (
                    prop_name == "VEHICLES"
                    and isinstance(value, list)
                    and len(value) >= len(vehicles)
                ):
                    vehicles = copy.deepcopy(value)
                elif (
                    prop_name == "VehicleGroupVariant"
                    and isinstance(value, dict)
                    and len(value) >= len(vehicle_group_variant)
                ):
                    vehicle_group_variant = copy.deepcopy(value)
                target = variables.get(var_name)
                if isinstance(target, dict):
                    target[prop_name] = value
                continue

            match = _INDEX_ASSIGN_RE.match(line)
            if match:
                var_name, index_text, rhs = match.groups()
                target = variables.get(var_name)
                if not isinstance(target, (list, dict)):
                    continue
                try:
                    value = _parse_rhs(rhs, variables)
                except Exception:
                    continue
                index = int(index_text)
                if isinstance(target, list):
                    _ensure_array_slot(target, index)
                    target[index] = value
                else:
                    target[index] = value

    components = {
        str(component_id): {
            "name": component["name"],
            "sensors": {},
        }
        for component_id, component in sorted(component_defs.items())
    }

    bundle_slots, _bundle_scenarios = _extract_bundle_runtime_data(bundle_path)
    for (component_id, sensor_id), slot in sorted(bundle_slots.items()):
        component_key = str(component_id)
        components.setdefault(
            component_key,
            {"name": f"Component{component_id}", "sensors": {}},
        )
        components[component_key]["sensors"][str(sensor_id)] = {
            "name": slot.get("name"),
            "mode": slot.get("mode"),
            "datatype": slot.get("datatype"),
            "unit": slot.get("unit"),
        }

    components = {
        component_id: component
        for component_id, component in components.items()
        if component.get("sensors")
    }

    normalized_vehicles: list[dict[str, Any]] = []
    for entry in vehicles:
        if not isinstance(entry, dict) or not entry.get("key"):
            continue
        normalized_vehicles.append(
            {
                "key": entry.get("key"),
                "modelName": entry.get("modelName"),
                "group": entry.get("group"),
            }
        )

    return components, normalized_vehicles, vehicle_group_variant


def _load_i18n_subset(i18n_path: Path) -> dict[str, Any]:
    if not i18n_path.exists():
        return {}
    payload = json.loads(i18n_path.read_text())
    subset = payload.get("extracted_subset")
    return subset if isinstance(subset, dict) else {}


def _component_display_names_from_i18n_subset(
    subset: dict[str, Any],
) -> dict[str, str]:
    names: dict[str, str] = {}

    bulbs = subset.get("CONTROLS.LIGHTING.BULBS")
    if isinstance(bulbs, dict):
        for key, value in bulbs.items():
            if not isinstance(value, str):
                continue
            circuit_match = re.fullmatch(r"LIGHT_CIRCUIT_(\d+)", key)
            if circuit_match:
                names[f"LightCircuit{int(circuit_match.group(1)):02d}"] = value
            elif key == "HEGOTEC_LIGHT_MODULE":
                names["HegotecLightModule"] = value

    groups = subset.get("CONTROLS.LIGHTING.GROUPS")
    if isinstance(groups, dict):
        for key, value in groups.items():
            if not isinstance(value, str):
                continue
            group_match = re.fullmatch(r"LIGHT_GROUP_(\d+)", key)
            if group_match:
                names[f"LightGroup{int(group_match.group(1)):02d}"] = value

    return names


def _extract_component_display_names_from_bundle(bundle_path: Path) -> dict[str, str]:
    if not bundle_path.exists():
        return {}

    text = bundle_path.read_text(errors="ignore")
    names: dict[str, str] = {}

    for match in _BUNDLE_LIGHT_CIRCUIT_LABEL_RE.finditer(text):
        names.setdefault(f"LightCircuit{int(match.group(1)):02d}", match.group(2))

    for match in _BUNDLE_LIGHT_GROUP_LABEL_RE.finditer(text):
        names.setdefault(f"LightGroup{int(match.group(1)):02d}", match.group(2))

    hegotec_match = _BUNDLE_HEGOTEC_LABEL_RE.search(text)
    if hegotec_match:
        names.setdefault("HegotecLightModule", hegotec_match.group(1))

    return names


def _snake_case(text: str) -> str:
    text = text.replace("/", " ")
    text = text.replace("+", " plus ")
    text = re.sub(r"[^A-Za-z0-9]+", "_", text)
    text = re.sub(r"([a-z0-9])([A-Z])", r"\1_\2", text)
    text = re.sub(r"([A-Za-z])(\d)", r"\1_\2", text)
    text = re.sub(r"(\d)([A-Za-z])", r"\1_\2", text)
    text = re.sub(r"_+", "_", text).strip("_")
    return text.lower()


def _title_from_key(text: str) -> str:
    return " ".join(part.capitalize() for part in text.replace("_", " ").split())


def _normalize_unit(unit: str | None) -> str | None:
    if unit is None:
        return None
    return UNIT_OVERRIDES.get(unit, unit)


def _normalize_signed_32(value: Any) -> Any:
    """Convert unsigned 32-bit wrapped integers back to signed values."""
    if isinstance(value, bool) or not isinstance(value, int):
        return value
    if value > 0x7FFFFFFF:
        return value - 0x100000000
    return value


def _transform_numeric_value(value: Any, transform: str) -> Any:
    """Apply a known numeric transform to a scalar value."""
    if not isinstance(value, (int, float)):
        return value
    if transform == "div10":
        return value / 10
    if transform == "div100":
        return value / 100
    if transform == "div1000":
        return value / 1000
    if transform == "div3600":
        return value / 3600
    if transform == "invert100":
        return 100 - value
    return value


def _extract_legacy_transform_hints(
    pia_decoder_path: Path,
) -> dict[tuple[int, int], dict[str, Any]]:
    """Extract legacy transform hints from the historical transform table."""
    module = ast.parse(pia_decoder_path.read_text())
    for node in module.body:
        if not isinstance(node, ast.AnnAssign):
            continue
        if getattr(node.target, "id", None) != "LEGACY_TRANSFORM_HINTS":
            continue
        raw_map = ast.literal_eval(node.value)
        hints: dict[tuple[int, int], dict[str, Any]] = {}
        for key, value in raw_map.items():
            if not (
                isinstance(key, tuple)
                and len(key) == 2
                and isinstance(value, dict)
            ):
                continue
            label = value.get("label")
            unit = value.get("unit")
            transform = value.get("transform")
            if not transform:
                continue
            hints[key] = {
                "label": label,
                "unit": unit,
                "transform": transform,
            }
        return hints
    return {}


def _canonical_provider_slots(provider_specs_path: Path) -> set[tuple[int, int]]:
    """Return all slot tuples claimed by canonical provider metadata."""
    payload = json.loads(provider_specs_path.read_text())
    slots: set[tuple[int, int]] = set()
    for capability in payload.get("capabilities", []):
        if not isinstance(capability, dict):
            continue
        for candidate in capability.get("candidates", []):
            if not isinstance(candidate, dict):
                continue
            slots.add((int(candidate["component_id"]), int(candidate["sensor_id"])))
    return slots


def _classify_component(component_name: str, sensors: dict[str, dict]) -> str:
    sensor_names = {sensor["name"] for sensor in sensors.values()}
    if LIGHT_COMPONENT_RE.match(component_name):
        return "light"
    if component_name in TRUMA_COMBI_NAMES:
        return "truma_heater"
    if HEATER_NEO_RE.match(component_name):
        return "heater_neo"
    if AIRCON_COMPONENT_RE.match(component_name):
        return "air_conditioner"
    if (
        "FridgeLevel" in sensor_names
        and sensors.get("1", {}).get("name") in FRIDGE_POWER_NAMES
    ):
        return "fridge"
    if component_name == "DometicCompressorFridge":
        return "fridge"
    if component_name in {"VehicleSignal", "VehicleFiatChassis"}:
        return "chassis"
    if component_name == "VehicleInformation":
        return "vehicle_info"
    if component_name == "ScuSignals":
        return "scu_platform"
    if component_name.startswith("EBL") or component_name.startswith("AD100") or component_name in {
        "TelecoTEB310D",
        "CBE_PL50_DISS",
    }:
        return "habitation"
    if component_name in {"VotronicMPP250Duo", "CBESolarCharger"}:
        return "solar_charger"
    if LIGHTING_MODULE_RE.match(component_name):
        return "lighting_module"
    if component_name in {"ThetfordIndusToilet", "ThetfordIndusToiletEco"}:
        return "toilet"
    if (
        component_name == "SeeLevel_709_RVC_NLP"
        or "WaterSensor" in component_name
        or sensor_names.intersection(WATER_LEVEL_NAMES)
    ):
        return "tank_monitor"
    if component_name == "TPMS":
        return "tpms"
    if "Awning" in component_name:
        return "awning"
    if component_name in {"BatteryGuard1000", "VictronMultiplus"} or "Inverter" in component_name:
        return "inverter"
    if component_name in {"CerboGX", "ModulusPowerHub"}:
        return "power_system"
    if component_name == "SmartBatterySensor" or "BatteryStateOfCharge" in sensor_names:
        return "bms"
    if component_name.startswith("TimberlineHeater"):
        return "heater"
    if component_name == "SwitchPad":
        return "switch_pad"
    return "component"


def _suggested_area(kind: str) -> str | None:
    if kind == "fridge":
        return "Kitchen"
    if kind == "toilet":
        return "Bathroom"
    if kind == "awning":
        return "Outside"
    return None


def _component_display_name(
    source_name: str,
    component_display_names: dict[str, str],
) -> str | None:
    return component_display_names.get(source_name)


def _generic_component_name(
    component_id: str,
    kind: str,
    *,
    source_name: str,
    component_display_names: dict[str, str],
) -> str:
    cid = int(component_id)
    display_name = _component_display_name(source_name, component_display_names)
    if display_name:
        return display_name
    if kind == "chassis":
        return "Chassis Signals"
    if kind == "vehicle_info":
        return "Vehicle Information"
    if kind == "scu_platform":
        return "Connectivity Unit"
    if kind == "light":
        return f"Light Zone {cid}"
    if kind == "lighting_module":
        return f"Lighting Module {cid}"
    if kind == "habitation":
        return f"Habitation Controller {cid}"
    if kind == "fridge":
        return f"Fridge Module {cid}"
    if kind in {"truma_heater", "heater", "heater_neo"}:
        return f"Heater Module {cid}"
    if kind == "air_conditioner":
        return f"Climate Module {cid}"
    if kind == "solar_charger":
        return f"Solar Charger {cid}"
    if kind == "tank_monitor":
        return f"Tank Monitor {cid}"
    if kind == "toilet":
        return f"Toilet Module {cid}"
    if kind == "tpms":
        return "Tyre Pressure Monitor"
    if kind == "awning":
        return "Awning Controller"
    if kind == "inverter":
        return f"Power Converter {cid}"
    if kind == "power_system":
        return f"Power System {cid}"
    if kind == "bms":
        return f"Battery Monitor {cid}"
    if kind == "switch_pad":
        return f"Switch Panel {cid}"
    return f"Module {cid}"


def _normalize_label(kind: str, sensor_name: str) -> str:
    if (kind, sensor_name) in CONTEXT_LABEL_OVERRIDES:
        return CONTEXT_LABEL_OVERRIDES[(kind, sensor_name)]
    if sensor_name in GLOBAL_LABEL_OVERRIDES:
        return GLOBAL_LABEL_OVERRIDES[sensor_name]
    return _snake_case(sensor_name)


def _normalize_mode(
    *,
    label: str,
    datatype: str,
    mode: str,
    has_control_profile: bool = False,
) -> str:
    if (
        datatype == "string"
        and mode in {"rw", "w"}
        and not has_control_profile
        and label not in SAFE_STRING_WRITE_LABELS
    ):
        return "r"
    return mode


def _generate_component_record(
    component_id: str,
    component: dict[str, Any],
    component_display_names: dict[str, str],
) -> dict[str, Any]:
    source_name = component["name"]
    kind = _classify_component(source_name, component.get("sensors", {}))
    return {
        "kind": kind,
        "name": _generic_component_name(
            component_id,
            kind,
            source_name=source_name,
            component_display_names=component_display_names,
        ),
        "source_name": source_name,
        "suggested_area": _suggested_area(kind),
    }


def _control_profiles() -> dict[str, dict[str, Any]]:
    return {
        "main_switch": {
            "platform": "switch",
        },
        "heater_air_energy_source": {
            "platform": "select",
            "options": list(HEATER_ENERGY_OPTIONS),
        },
        "heater_water_energy_source": {
            "platform": "select",
            "options": list(HEATER_ENERGY_OPTIONS),
        },
        "water_heater_mode": {
            "platform": "select",
            "options": list(BOILER_MODE_OPTIONS),
        },
        "aircon_mode": {
            "platform": "select",
            "options": list(AIRCON_MODE_OPTIONS),
        },
        "fan_mode": {
            "platform": "select",
            "options": list(FAN_MODE_OPTIONS),
        },
        "fridge_mode": {
            "platform": "select",
            "options": list(FRIDGE_MODE_OPTIONS),
        },
        "power_mode": {
            "platform": "select",
            "options": list(FRIDGE_POWER_OPTIONS),
        },
        "set_power_mode": {
            "platform": "select",
            "options": list(FRIDGE_POWER_OPTIONS),
        },
        "mode": {
            "platform": "select",
            "options": list(SWITCH_PAD_MODE_OPTIONS),
        },
    }


def _slot_control_profiles() -> dict[tuple[int, int], dict[str, Any]]:
    def select(options: list[str]) -> dict[str, Any]:
        return {
            "platform": "select",
            "options": options,
        }

    def button() -> dict[str, Any]:
        return {"platform": "button"}

    return {
        (56, 17): button(),
        (56, 18): button(),
        (56, 19): button(),
        (34, 3): select(["1", "2", "3", "4", "5"]),
        (60, 1): select(DOMETIC_COMPRESSOR_USER_MODES),
        (102, 3): select(MAXXFAN_SPEEDS),
        (102, 9): select(MAXXFAN_SPEEDS),
        (95, 1): select(AIRXCEL_AC_MODES),
        (95, 2): select(AIRXCEL_AUTO_ON),
        (95, 3): select(AIRXCEL_FAN_SPEEDS),
        (95, 6): select(AIRXCEL_BINARY_STATES),
        (95, 7): select(AIRXCEL_AUTO_FORCED),
        (95, 8): select(AIRXCEL_AUTO_MANUAL),
        (95, 9): select(AIRXCEL_ROOF_FAN_SPEEDS),
        (95, 10): select(AIRXCEL_AIRFLOW),
        (95, 11): select(AIRXCEL_DOME_POSITIONS),
        (95, 12): select(AIRXCEL_BINARY_STATES),
        (95, 17): select(AIRXCEL_AC_MODES),
        (95, 18): select(AIRXCEL_AUTO_ON),
        (95, 19): select(AIRXCEL_FAN_SPEEDS),
        (95, 22): select(AIRXCEL_BINARY_STATES),
        (95, 23): select(AIRXCEL_AUTO_FORCED),
        (95, 24): select(AIRXCEL_AUTO_MANUAL),
        (95, 25): select(AIRXCEL_ROOF_FAN_SPEEDS),
        (95, 26): select(AIRXCEL_AIRFLOW),
        (95, 27): select(AIRXCEL_DOME_POSITIONS),
        (95, 28): select(AIRXCEL_BINARY_STATES),
        (107, 1): button(),
        (107, 2): button(),
        (107, 3): button(),
        (107, 4): button(),
        (110, 10): button(),
        (110, 11): button(),
        (116, 2): select(DELLCOOL_POWER_MODES),
        (119, 1): select(TRUMA_NEO_WATER_MODES),
        (119, 2): select(TRUMA_NEO_FUEL_ONLY),
        (119, 11): select(TRUMA_NEO_AIR_MODES),
        (119, 12): select(TRUMA_NEO_AIR_SUBMODES),
        (119, 13): select(TRUMA_NEO_FAN_MODES),
        (119, 14): select(TRUMA_NEO_FUEL_ONLY),
        (120, 1): select(TRUMA_NEO_WATER_MODES),
        (120, 2): select(TRUMA_NEO_E_ENERGY),
        (120, 3): select(TRUMA_NEO_POWER_LIMIT),
        (120, 11): select(TRUMA_NEO_AIR_MODES),
        (120, 12): select(TRUMA_NEO_AIR_SUBMODES),
        (120, 13): select(TRUMA_NEO_FAN_MODES),
        (120, 14): select(TRUMA_NEO_E_ENERGY),
        (120, 15): select(TRUMA_NEO_POWER_LIMIT),
        (122, 9): button(),
        (122, 10): button(),
        (124, 1): select(TIMBERLINE_WATER_MODES),
        (125, 1): select(TIMBERLINE_FURNACE_MODES),
        (125, 3): select(TIMBERLINE_AIR_MODES),
        (125, 12): select(TIMBERLINE_CIRCULATION_PUMP),
        (118, 2): select(INDELB_POWER_MODES),
    }


def _bundle_slot_profile(
    component_id: int,
    sensor_id: int,
    datatype: str,
    mode: str,
    bundle_slots: dict[tuple[int, int], dict[str, Any]],
) -> dict[str, Any]:
    slot = bundle_slots.get((component_id, sensor_id), {})
    options = [
        option
        for option in slot.get("options", [])
        if isinstance(option, str)
    ]
    if datatype == "string" and mode in {"rw", "w"} and options:
        return {
            "platform": "select",
            "options": options,
        }
    if datatype == "bool" and mode == "w":
        return {"platform": "button"}
    return {}


def _text_slot_profile(label: str, datatype: str, mode: str) -> dict[str, Any]:
    if datatype == "string" and mode in {"rw", "w"} and label.endswith("_start_time"):
        return {"platform": "text"}
    return {}


def _generate_slot_record(
    component_id: str,
    sensor_id: str,
    kind: str,
    sensor: dict[str, Any],
    label_controls: dict[str, dict[str, Any]],
    slot_controls: dict[tuple[int, int], dict[str, Any]],
    bundle_slots: dict[tuple[int, int], dict[str, Any]],
    legacy_transform_hints: dict[tuple[int, int], dict[str, Any]],
) -> dict[str, Any]:
    label = _normalize_label(kind, sensor["name"])
    datatype = sensor["datatype"]
    cid = int(component_id)
    sid = int(sensor_id)
    bundle_slot = bundle_slots.get((cid, sid), {})
    wire_mode = sensor.get("mode", "r")
    profile = {
        **_bundle_slot_profile(cid, sid, datatype, wire_mode, bundle_slots),
        **_text_slot_profile(label, datatype, wire_mode),
        **label_controls.get(label, {}),
        **slot_controls.get((cid, sid), {}),
    }
    mode = _normalize_mode(
        label=label,
        datatype=datatype,
        mode=wire_mode,
        has_control_profile=bool(profile),
    )
    record: dict[str, Any] = {
        "label": label,
        "datatype": datatype,
        "mode": mode,
        "wire_mode": wire_mode,
    }
    unit = _normalize_unit(sensor.get("unit"))
    if unit is not None and datatype in {"int", "float"}:
        record["unit"] = unit
    legacy_hint = legacy_transform_hints.get((cid, sid))
    if legacy_hint and legacy_hint.get("label") == label:
        transform = legacy_hint.get("transform")
        if transform:
            record["transform"] = transform
            legacy_unit = legacy_hint.get("unit")
            if legacy_unit and datatype in {"int", "float"}:
                record["unit"] = legacy_unit
    range_meta = bundle_slot.get("range")
    if isinstance(range_meta, dict):
        range_min = _normalize_signed_32(range_meta.get("min"))
        range_max = _normalize_signed_32(range_meta.get("max"))
        transform = record.get("transform")
        if transform == "invert100":
            if range_min is not None and range_max is not None:
                range_min, range_max = (
                    _transform_numeric_value(range_max, transform),
                    _transform_numeric_value(range_min, transform),
                )
            else:
                range_min = _transform_numeric_value(range_min, transform)
                range_max = _transform_numeric_value(range_max, transform)
        elif transform:
            range_min = _transform_numeric_value(range_min, transform)
            range_max = _transform_numeric_value(range_max, transform)
        if range_min is not None:
            record["min"] = range_min
        if range_max is not None:
            record["max"] = range_max
        if range_meta.get("resolution") is not None:
            step = range_meta["resolution"]
            if transform and transform != "invert100":
                step = _transform_numeric_value(step, transform)
            record["step"] = step
    if bundle_slot.get("description"):
        record["description"] = bundle_slot["description"]
    if bundle_slot.get("deprecated"):
        record["deprecated"] = True
    if profile is not None:
        if "platform" in profile:
            record["control_platform"] = profile["platform"]
        if "options" in profile and profile["options"]:
            record["options"] = profile["options"]
    return record


def _brand_from_key(model_key: str) -> str | None:
    prefix = model_key.split("_", 1)[0]
    return BRAND_PREFIXES.get(prefix)


def _build_vehicle_catalog(registry_path: Path, vehicle_groups_path: Path) -> dict[str, Any]:
    registry = json.loads(registry_path.read_text())
    groups = json.loads(vehicle_groups_path.read_text())
    registry_models = registry.get("vehicle_models", {})
    grouped_models = {
        row["key"]: row
        for row in groups.get("VEHICLES", [])
        if isinstance(row, dict) and row.get("key")
    }
    model_keys = sorted(
        set(registry_models).union(grouped_models),
        key=str,
    )

    out = {
        "_comment": (
            "Vehicle model metadata used by the integration for diagnostics "
            "and compatibility hints."
        ),
        "models": {},
    }
    for key in model_keys:
        registry_model = registry_models.get(key, {})
        grouped_model = grouped_models.get(key, {})
        out["models"][key] = {
            "name": grouped_model.get("modelName") or registry_model.get("name") or key,
            "brand": registry_model.get("brand") or _brand_from_key(key),
            "group": grouped_model.get("group") or registry_model.get("group"),
        }
    return out


def _build_vehicle_catalog_from_bundle(
    vehicles: list[dict[str, Any]],
    vehicle_group_variant: dict[Any, Any],
) -> dict[str, Any]:
    reverse_variant = {
        value: key
        for key, value in vehicle_group_variant.items()
        if isinstance(key, str) and isinstance(value, int)
    }
    out = {
        "_comment": (
            "Vehicle model metadata used by the integration for diagnostics "
            "and compatibility hints."
        ),
        "models": {},
    }
    for row in sorted(vehicles, key=lambda item: str(item.get("key", ""))):
        key = row.get("key")
        if not isinstance(key, str) or not key:
            continue
        group_value = row.get("group")
        group_name = vehicle_group_variant.get(
            group_value,
            reverse_variant.get(group_value, group_value),
        )
        out["models"][key] = {
            "name": row.get("modelName") or key,
            "brand": _brand_from_key(key),
            "group": group_name,
        }
    return out


def _scenario_actions(entry: dict[str, Any]) -> list[dict[str, Any]]:
    actions: list[dict[str, Any]] = []
    for key, value in entry.items():
        if key == "writes" and isinstance(value, list):
            actions.extend(value)
        elif key.startswith("writes_") and isinstance(value, list):
            for action in value:
                if not isinstance(action, dict):
                    continue
                action_copy = dict(action)
                if "componentId" not in action_copy:
                    suffix = key.removeprefix("writes_to_componentId_")
                    suffix = suffix.removeprefix("writes_componentId_")
                    if suffix.isdigit():
                        action_copy["componentId"] = int(suffix)
                actions.append(action_copy)
    normalized: list[dict[str, Any]] = []
    for action in actions:
        if not isinstance(action, dict):
            continue
        if "componentId" not in action or "valueId" not in action or "value" not in action:
            continue
        normalized.append(
            {
                "component_id": int(action["componentId"]),
                "sensor_id": int(action["valueId"]),
                "value": action["value"],
            }
        )
    return normalized


def _scenario_key_from_name(name: str) -> str:
    parts = name.split(".")
    if len(parts) >= 2:
        return parts[1]
    return name


def _build_scenario_catalog(
    scenarios_path: Path,
    bundle_scenarios: list[dict[str, Any]],
) -> dict[str, Any]:
    raw = json.loads(scenarios_path.read_text())
    entries_by_id: dict[tuple[str, int], dict[str, Any]] = {}
    for kind, source_key in (("scenario", "scenarios"), ("scene", "scenes")):
        for key, value in raw.get(source_key, {}).items():
            if not isinstance(value, dict):
                continue
            actions = _scenario_actions(value)
            entry_id = int(value.get("id") or 0)
            entries_by_id[(kind, entry_id)] = {
                "key": key,
                "kind": kind,
                "id": value.get("id"),
                "name": _title_from_key(key),
                "icon": value.get("icon"),
                "name_key": value.get("name_key"),
                "action_count": len(actions),
                "actions": actions,
                "source": "local",
            }

    for bundle_entry in bundle_scenarios:
        name = str(bundle_entry.get("name", ""))
        kind = "scenario" if name.startswith("SCENARIOS.") else "scene"
        entry_id = int(bundle_entry.get("id") or 0)
        key = _scenario_key_from_name(name)
        actions = list(bundle_entry.get("actions", []))
        bundle_record = {
            "key": key,
            "kind": kind,
            "id": bundle_entry.get("id"),
            "name": _title_from_key(key),
            "icon": bundle_entry.get("icon"),
            "name_key": name,
            "action_count": len(actions),
            "actions": actions,
            "scene_id": bundle_entry.get("scene_id"),
            "description_key": bundle_entry.get("description"),
            "preview_icon": bundle_entry.get("preview_icon"),
            "image": bundle_entry.get("image"),
            "checklist_count": bundle_entry.get("checklist_count", 0),
            "source": "bundle",
        }
        existing = entries_by_id.get((kind, entry_id))
        if existing is None or bundle_record["action_count"] >= existing.get("action_count", 0):
            entries_by_id[(kind, entry_id)] = bundle_record

    entries = list(entries_by_id.values())
    entries.sort(key=lambda item: (item["kind"], int(item["id"] or 0), item["key"]))
    return {
        "_comment": (
            "Scenario and scene metadata used by the integration for diagnostics "
            "and executable scene support."
        ),
        "brands": raw.get("templates_brand_keys", []),
        "entries": entries,
    }


def _build_scenario_catalog_from_bundle(
    bundle_scenarios: list[dict[str, Any]],
) -> dict[str, Any]:
    entries_by_id: dict[tuple[str, int], dict[str, Any]] = {}

    for bundle_entry in bundle_scenarios:
        name = str(bundle_entry.get("name", ""))
        if not name.startswith(("SCENARIOS.", "SCENES.")):
            continue
        kind = "scenario" if name.startswith("SCENARIOS.") else "scene"
        entry_id = int(bundle_entry.get("id") or 0)
        key = _scenario_key_from_name(name)
        actions = list(bundle_entry.get("actions", []))
        bundle_record = {
            "key": key,
            "kind": kind,
            "id": bundle_entry.get("id"),
            "name": _title_from_key(key),
            "icon": bundle_entry.get("icon"),
            "name_key": name,
            "action_count": len(actions),
            "actions": actions,
            "scene_id": bundle_entry.get("scene_id"),
            "description_key": bundle_entry.get("description"),
            "preview_icon": bundle_entry.get("preview_icon"),
            "image": bundle_entry.get("image"),
            "checklist_count": bundle_entry.get("checklist_count", 0),
            "source": "bundle",
        }
        existing = entries_by_id.get((kind, entry_id))
        if existing is None or bundle_record["action_count"] >= existing.get("action_count", 0):
            entries_by_id[(kind, entry_id)] = bundle_record

    entries = list(entries_by_id.values())
    entries.sort(key=lambda item: (item["kind"], int(item["id"] or 0), item["key"]))
    return {
        "_comment": (
            "Scenario and scene metadata used by the integration for diagnostics "
            "and executable scene support."
        ),
        "brands": [],
        "entries": entries,
    }


def _default_platform_for_record(record: dict[str, Any]) -> str | None:
    control_platform = record.get("control_platform")
    if isinstance(control_platform, str):
        return control_platform
    mode = record.get("mode")
    datatype = record.get("datatype")
    if mode == "r":
        return "binary_sensor" if datatype == "bool" else "sensor"
    if mode in {"rw", "w"}:
        if datatype == "bool":
            return "switch"
        if datatype in {"int", "float"}:
            return "number"
        if datatype == "string":
            return None
    return None


def _rich_template_claims(
    component_id: int,
    component_record: dict[str, Any],
    slots_for_component: dict[int, dict[str, Any]],
) -> dict[int, str]:
    return rich_template_claims(
        component_id=component_id,
        component_kind=component_record.get("kind"),
        slots_for_component=slots_for_component,
    )


def _build_coverage_audit(
    generated_components: dict[str, Any],
    generated_slots: dict[str, Any],
    generated_controls: dict[str, Any],
    generated_scenarios: dict[str, Any],
    canonical_provider_slots: set[tuple[int, int]],
) -> dict[str, Any]:
    component_slots: dict[int, dict[int, dict[str, Any]]] = {}
    for key, record in generated_slots["slots"].items():
        component_id, sensor_id = (int(part) for part in key.split(":"))
        component_slots.setdefault(component_id, {})[sensor_id] = record

    rich_claims: dict[tuple[int, int], str] = {}
    for component_id, component_record in generated_components["components"].items():
        cid = int(component_id)
        claims = _rich_template_claims(cid, component_record, component_slots.get(cid, {}))
        for sensor_id, tag in claims.items():
            rich_claims[(cid, sensor_id)] = tag

    slot_entries: dict[str, Any] = {}
    component_entries: dict[str, Any] = {}
    writable_supported = 0
    writable_suppressed = 0

    for component_id, component_record in generated_components["components"].items():
        cid = int(component_id)
        slots_for_component = component_slots.get(cid, {})
        component_tags: set[str] = set()
        component_writable_total = 0
        component_writable_supported = 0
        component_writable_suppressed = 0

        for sensor_id, record in sorted(slots_for_component.items()):
            slot_key = f"{cid}:{sensor_id}"
            wire_mode = str(record.get("wire_mode", record.get("mode", "r")))
            platform = _default_platform_for_record(record)
            rich_tag = rich_claims.get((cid, sensor_id))
            label = str(record.get("label", ""))
            is_writable = wire_mode in {"rw", "w"}

            if rich_tag is not None:
                support_class = f"template_{rich_tag}"
            elif (cid, sensor_id) in canonical_provider_slots:
                support_class = "canonical_generic"
            elif (
                is_writable
                and record.get("datatype") == "string"
                and record.get("mode") == "r"
                and record.get("control_platform") is None
            ):
                support_class = "suppressed_write"
            elif is_writable and platform is None:
                support_class = "suppressed_write"
            elif platform is not None:
                support_class = f"generic_{platform}"
            else:
                support_class = "generic_read"

            write_status = "read_only"
            if is_writable:
                component_writable_total += 1
                if support_class == "suppressed_write":
                    write_status = "suppressed"
                    component_writable_suppressed += 1
                    writable_suppressed += 1
                else:
                    write_status = "supported"
                    component_writable_supported += 1
                    writable_supported += 1

            read_validation_status = "inferred"
            if write_status == "supported":
                write_validation_status = "runtime_path_only"
            elif write_status == "suppressed":
                write_validation_status = "suppressed"
            else:
                write_validation_status = "not_applicable"

            if support_class.startswith("template_"):
                component_tags.add("rich_template")
            elif support_class.startswith("generic_"):
                component_tags.add("generic_runtime")
            elif support_class == "canonical_generic":
                component_tags.add("canonical_provider")
            elif support_class == "suppressed_write":
                component_tags.add("suppressed_write")

            slot_entries[slot_key] = {
                "component_id": cid,
                "sensor_id": sensor_id,
                "component_kind": component_record.get("kind"),
                "label": label,
                "datatype": record.get("datatype"),
                "mode": record.get("mode"),
                "wire_mode": wire_mode,
                "control_platform": record.get("control_platform"),
                "support_class": support_class,
                "write_status": write_status,
                "read_validation_status": read_validation_status,
                "write_validation_status": write_validation_status,
                "has_options": bool(record.get("options")),
                "min": record.get("min"),
                "max": record.get("max"),
                "step": record.get("step"),
                "deprecated": bool(record.get("deprecated", False)),
            }

        component_entries[str(cid)] = {
            "kind": component_record.get("kind"),
            "name": component_record.get("name"),
            "slot_count": len(slots_for_component),
            "writable_slot_count": component_writable_total,
            "supported_writable_slot_count": component_writable_supported,
            "suppressed_writable_slot_count": component_writable_suppressed,
            "coverage_tags": sorted(component_tags or {"generic_runtime"}),
        }

    scenario_entries = generated_scenarios.get("entries", [])
    scenario_count = len([entry for entry in scenario_entries if entry.get("kind") == "scenario"])
    scene_count = len([entry for entry in scenario_entries if entry.get("kind") == "scene"])

    return {
        "_comment": (
            "Coverage audit used by the integration to report local runtime "
            "coverage for components, slots, writable controls, and built-in "
            "scenarios. Writable coverage here means the integration has a "
            "runtime serialization path for the slot; it does not mean every "
            "write has been exercised on every supported vehicle family."
        ),
        "summary": {
            "component_count": len(generated_components["components"]),
            "slot_count": len(generated_slots["slots"]),
            "writable_slot_count": sum(
                1
                for record in generated_slots["slots"].values()
                if record.get("wire_mode", record.get("mode")) in {"rw", "w"}
            ),
            "supported_writable_slot_count": writable_supported,
            "suppressed_writable_slot_count": writable_suppressed,
            "writable_coverage_basis": "runtime_serialization_path",
            "read_validation_basis": "local_metadata_inference",
            "read_validation_status": "not_tracked_beyond_inference",
            "write_validation_basis": "runtime_serialization_path",
            "write_validation_status": "runtime_path_only",
            "wire_validation_status": "not_tracked",
            "label_control_count": len(generated_controls.get("labels", {})),
            "slot_control_count": len(generated_controls.get("slots", {})),
            "scenario_count": scenario_count,
            "scene_count": scene_count,
        },
        "components": component_entries,
        "slots": slot_entries,
        "scenarios": {
            "entries": scenario_entries,
        },
    }


def _validation_defaults(
    payload: dict[str, Any],
    *,
    default_read_status: str,
    default_write_status: str,
    default_evidence_sources: tuple[str, ...],
) -> dict[str, Any]:
    """Return normalized metadata validation defaults from one spec file."""
    defaults = payload.get("defaults")
    if not isinstance(defaults, dict):
        defaults = {}
    evidence_sources = defaults.get("evidence_sources", list(default_evidence_sources))
    if not isinstance(evidence_sources, list) or not all(
        isinstance(item, str) and item for item in evidence_sources
    ):
        evidence_sources = list(default_evidence_sources)
    return {
        "read_validation_status": str(
            defaults.get("read_validation_status", default_read_status)
        ),
        "write_validation_status": str(
            defaults.get("write_validation_status", default_write_status)
        ),
        "evidence_sources": tuple(evidence_sources),
    }


def _validation_metadata(
    payload: dict[str, Any],
    defaults: dict[str, Any],
) -> dict[str, Any]:
    """Return normalized validation metadata for one entry."""
    evidence_sources = payload.get("evidence_sources")
    if not isinstance(evidence_sources, list) or not all(
        isinstance(item, str) and item for item in evidence_sources
    ):
        evidence_sources = list(defaults["evidence_sources"])
    return {
        "read_validation_status": str(
            payload.get("read_validation_status", defaults["read_validation_status"])
        ),
        "write_validation_status": str(
            payload.get("write_validation_status", defaults["write_validation_status"])
        ),
        "evidence_sources": evidence_sources,
    }


def _build_support_matrix(
    generated_components: dict[str, Any],
    provider_specs_path: Path,
    template_specs_path: Path,
) -> dict[str, Any]:
    """Build a support matrix grouped by canonical capabilities and rich templates."""
    provider_payload = json.loads(provider_specs_path.read_text())
    template_payload = json.loads(template_specs_path.read_text())
    provider_defaults = _validation_defaults(
        provider_payload,
        default_read_status="inferred",
        default_write_status="not_applicable",
        default_evidence_sources=("registry", "bundle"),
    )
    template_defaults = _validation_defaults(
        template_payload,
        default_read_status="inferred",
        default_write_status="runtime_path_only",
        default_evidence_sources=("registry", "bundle"),
    )

    canonical_entries: list[dict[str, Any]] = []
    for capability in provider_payload.get("capabilities", []):
        if not isinstance(capability, dict):
            continue
        validation = _validation_metadata(capability, provider_defaults)
        candidates = capability.get("candidates", [])
        candidate_slots = [
            [int(candidate["component_id"]), int(candidate["sensor_id"])]
            for candidate in candidates
            if isinstance(candidate, dict)
        ]
        canonical_entries.append(
            {
                "key": capability.get("key"),
                "name": _title_from_key(str(capability.get("key", ""))),
                "platform": capability.get("platform"),
                "support_tier": "canonical",
                "candidate_slots": candidate_slots,
                "candidate_component_ids": sorted({slot[0] for slot in candidate_slots}),
                "provider_count": len(candidate_slots),
                **validation,
            }
        )

    rich_entries: list[dict[str, Any]] = []

    def add_template_entry(
        *,
        key: str,
        name: str,
        platform: str,
        payload: dict[str, Any],
        component_kinds: list[str] | None = None,
        component_ids: list[int] | None = None,
        required_slots: list[int] | None = None,
        claim_slots: list[int] | None = None,
        match_strategy: str,
    ) -> None:
        validation = _validation_metadata(payload, template_defaults)
        entry = {
            "key": key,
            "name": name,
            "platform": platform,
            "support_tier": "rich_template",
            "match_strategy": match_strategy,
            **validation,
        }
        if component_kinds:
            entry["component_kinds"] = component_kinds
        if component_ids:
            entry["component_ids"] = component_ids
        if required_slots:
            entry["required_slots"] = required_slots
        if claim_slots:
            entry["claim_slots"] = claim_slots
        rich_entries.append(entry)

    light_payload = template_payload.get("light", {})
    if isinstance(light_payload, dict):
        simple = light_payload.get("simple_component")
        if isinstance(simple, dict):
            add_template_entry(
                key="light.simple_component",
                name=str(simple.get("fallback_name", "Light")),
                platform="light",
                payload=simple,
                component_kinds=["light"],
                required_slots=[
                    int(item["sensor_id"])
                    for item in simple.get("required_slots", [])
                    if isinstance(item, dict)
                ],
                claim_slots=[
                    int(item["sensor_id"])
                    for item in (
                        list(simple.get("required_slots", []))
                        + list(simple.get("optional_slots", []))
                    )
                    if isinstance(item, dict)
                ],
                match_strategy="component_slots",
            )
        named = light_payload.get("named_channels")
        if isinstance(named, dict):
            add_template_entry(
                key="light.named_channels",
                name="Named Light Channels",
                platform="light",
                payload=named,
                match_strategy="label_pattern",
            )

    cover_payload = template_payload.get("cover", {})
    if isinstance(cover_payload, dict):
        awning = cover_payload.get("awning")
        if isinstance(awning, dict):
            add_template_entry(
                key="cover.awning",
                name="Awning",
                platform="cover",
                payload=awning,
                component_kinds=[
                    str(item) for item in awning.get("component_kinds", []) if isinstance(item, str)
                ],
                required_slots=[int(item) for item in awning.get("trigger_slots", []) if isinstance(item, int)],
                claim_slots=[int(item) for item in awning.get("claim_slots", []) if isinstance(item, int)],
                match_strategy="component_kind",
            )

    climate_payload = template_payload.get("climate", {})
    if isinstance(climate_payload, dict):
        single_zone = climate_payload.get("air_conditioner_single_zone")
        if isinstance(single_zone, dict):
            add_template_entry(
                key="climate.air_conditioner_single_zone",
                name=str(single_zone.get("name", "Air Conditioner")),
                platform="climate",
                payload=single_zone,
                component_kinds=[
                    str(item)
                    for item in single_zone.get("component_kinds", [])
                    if isinstance(item, str)
                ],
                required_slots=[
                    int(item["sensor_id"])
                    for item in single_zone.get("required_slots", [])
                    if isinstance(item, dict)
                ],
                claim_slots=[
                    int(item) for item in single_zone.get("claim_slots", []) if isinstance(item, int)
                ],
                match_strategy="component_kind",
            )
        for zone in climate_payload.get("airxcel_zones", []):
            if not isinstance(zone, dict):
                continue
            add_template_entry(
                key=f"climate.airxcel_zone.{zone.get('zone')}",
                name=str(zone.get("name", "Air Conditioner")),
                platform="climate",
                payload=zone,
                component_kinds=[
                    str(item) for item in zone.get("component_kinds", []) if isinstance(item, str)
                ],
                required_slots=[
                    int(item["sensor_id"])
                    for item in zone.get("required_slots", [])
                    if isinstance(item, dict)
                ],
                claim_slots=[
                    int(item) for item in zone.get("claim_slots", []) if isinstance(item, int)
                ],
                match_strategy="component_kind",
            )
        truma = climate_payload.get("truma_panel_heater")
        if isinstance(truma, dict):
            add_template_entry(
                key="climate.truma_panel_heater",
                name=str(truma.get("name", "Heater")),
                platform="climate",
                payload=truma,
                component_kinds=[
                    str(item) for item in truma.get("component_kinds", []) if isinstance(item, str)
                ],
                required_slots=[
                    int(item["sensor_id"])
                    for item in truma.get("required_slots", [])
                    if isinstance(item, dict)
                ],
                claim_slots=[
                    int(item) for item in truma.get("claim_slots", []) if isinstance(item, int)
                ],
                match_strategy="component_kind",
            )
        for heater in climate_payload.get("modern_heaters", []):
            if not isinstance(heater, dict):
                continue
            add_template_entry(
                key=f"climate.modern_heater.{heater.get('variant')}",
                name=str(heater.get("name", "Heater")),
                platform="climate",
                payload=heater,
                component_kinds=[
                    str(item) for item in heater.get("component_kinds", []) if isinstance(item, str)
                ],
                required_slots=[
                    int(item["sensor_id"])
                    for item in heater.get("required_slots", [])
                    if isinstance(item, dict)
                ],
                claim_slots=[
                    int(item) for item in heater.get("claim_slots", []) if isinstance(item, int)
                ],
                match_strategy="component_kind",
            )

    select_payload = template_payload.get("select", {})
    if isinstance(select_payload, dict):
        for key, name in (
            ("fridge_mode", "Fridge Mode"),
            ("boiler_mode", "Boiler Mode"),
            ("heater_energy", "Heater Energy"),
        ):
            payload = select_payload.get(key)
            if not isinstance(payload, dict):
                continue
            add_template_entry(
                key=f"select.{key}",
                name=name,
                platform="select",
                payload=payload,
                component_kinds=[
                    str(item) for item in payload.get("component_kinds", []) if isinstance(item, str)
                ],
                required_slots=[
                    int(item["sensor_id"])
                    for item in payload.get("required_slots", [])
                    if isinstance(item, dict)
                ],
                match_strategy="component_kind",
            )

    fan_payload = template_payload.get("fan", {})
    if isinstance(fan_payload, dict):
        for entity in fan_payload.get("entities", []):
            if not isinstance(entity, dict):
                continue
            claim_slots = [int(entity["state_sid"])]
            for key in ("speed_mode_sid", "speed_sid"):
                if isinstance(entity.get(key), int):
                    claim_slots.append(int(entity[key]))
            attribute_slots = entity.get("attribute_slots", {})
            if isinstance(attribute_slots, dict):
                claim_slots.extend(
                    int(value) for value in attribute_slots.values() if isinstance(value, int)
                )
            add_template_entry(
                key=f"fan.{int(entity.get('component_id', 0))}.{_snake_case(str(entity.get('name', 'fan')))}",
                name=str(entity.get("name", "Fan")),
                platform="fan",
                payload=entity,
                component_ids=[int(entity["component_id"])],
                claim_slots=claim_slots,
                match_strategy="component_id",
            )

    component_kind_map: dict[str, list[int]] = {}
    for component_id, component in generated_components["components"].items():
        kind = str(component.get("kind"))
        component_kind_map.setdefault(kind, []).append(int(component_id))
    generic_entries = [
        {
            "key": f"generic.{kind}",
            "name": _title_from_key(kind),
            "support_tier": "generic_runtime",
            "component_kind": kind,
            "component_ids": sorted(component_ids),
            "component_count": len(component_ids),
            "read_validation_status": "inferred",
            "write_validation_status": "slot_dependent",
            "evidence_sources": ["registry"],
        }
        for kind, component_ids in sorted(component_kind_map.items())
    ]

    all_entries = canonical_entries + rich_entries + generic_entries
    support_tier_counts: dict[str, int] = {}
    read_validation_status_counts: dict[str, int] = {}
    write_validation_status_counts: dict[str, int] = {}
    for entry in all_entries:
        support_tier = str(entry["support_tier"])
        support_tier_counts[support_tier] = support_tier_counts.get(support_tier, 0) + 1
        read_status = str(entry["read_validation_status"])
        write_status = str(entry["write_validation_status"])
        read_validation_status_counts[read_status] = (
            read_validation_status_counts.get(read_status, 0) + 1
        )
        write_validation_status_counts[write_status] = (
            write_validation_status_counts.get(write_status, 0) + 1
        )

    return {
        "_comment": (
            "Support matrix used by the integration to describe local semantic "
            "support across canonical capabilities, rich templates, and generic "
            "runtime component kinds."
        ),
        "summary": {
            "canonical_capability_count": len(canonical_entries),
            "rich_template_entry_count": len(rich_entries),
            "generic_component_kind_count": len(generic_entries),
            "support_tier_counts": support_tier_counts,
            "read_validation_status_counts": read_validation_status_counts,
            "write_validation_status_counts": write_validation_status_counts,
        },
        "canonical_capabilities": canonical_entries,
        "rich_templates": rich_entries,
        "generic_component_kinds": generic_entries,
    }


def generate_overlay(
    registry_path: Path,
    i18n_path: Path,
    scenarios_path: Path,
    vehicle_groups_path: Path,
    bundle_path: Path,
    pia_decoder_path: Path,
    provider_specs_path: Path,
    template_specs_path: Path,
) -> tuple[
    dict[str, Any],
    dict[str, Any],
    dict[str, Any],
    dict[str, Any],
    dict[str, Any],
    dict[str, Any],
    dict[str, Any],
]:
    raw = json.loads(registry_path.read_text())
    components = raw["components"]
    component_display_names = _component_display_names_from_i18n_subset(
        _load_i18n_subset(i18n_path)
    )
    controls = _control_profiles()
    slot_controls = _slot_control_profiles()
    bundle_slots, bundle_scenarios = _extract_bundle_runtime_data(bundle_path)
    legacy_transform_hints = _extract_legacy_transform_hints(pia_decoder_path)
    canonical_provider_slots = _canonical_provider_slots(provider_specs_path)

    generated_components = {
        "_comment": (
            "Component metadata used by the integration to group discovered "
            "components, name devices, and suggest areas."
        ),
        "components": {},
    }
    generated_slots = {
        "_comment": (
            "Slot metadata used by the integration to label discovered "
            "values and define datatype, units, mode, and transforms."
        ),
        "slots": {},
    }
    generated_controls = {
        "_comment": (
            "Control metadata used by the integration to validate writable "
            "options and describe higher-level controls."
        ),
        "labels": controls,
        "slots": {
            f"{component_id}:{sensor_id}": profile
            for (component_id, sensor_id), profile in sorted(slot_controls.items())
        },
    }
    generated_vehicles = _build_vehicle_catalog(registry_path, vehicle_groups_path)
    generated_scenarios = _build_scenario_catalog(scenarios_path, bundle_scenarios)

    for component_id, component in sorted(components.items(), key=lambda item: int(item[0])):
        component_record = _generate_component_record(
            component_id,
            component,
            component_display_names,
        )
        kind = component_record["kind"]
        generated_components["components"][component_id] = component_record
        for sensor_id, sensor in sorted(
            component.get("sensors", {}).items(),
            key=lambda item: int(item[0]),
        ):
            key = f"{component_id}:{sensor_id}"
            generated_slots["slots"][key] = _generate_slot_record(
                component_id,
                sensor_id,
                kind,
                sensor,
                controls,
                slot_controls,
                bundle_slots,
                legacy_transform_hints,
            )

    generated_coverage = _build_coverage_audit(
        generated_components,
        generated_slots,
        generated_controls,
        generated_scenarios,
        canonical_provider_slots,
    )
    generated_support_matrix = _build_support_matrix(
        generated_components,
        provider_specs_path,
        template_specs_path,
    )

    return (
        generated_components,
        generated_slots,
        generated_controls,
        generated_vehicles,
        generated_scenarios,
        generated_coverage,
        generated_support_matrix,
    )


def generate_overlay_from_bundle(
    bundle_path: Path,
    pia_decoder_path: Path,
    provider_specs_path: Path,
    template_specs_path: Path,
) -> tuple[
    dict[str, Any],
    dict[str, Any],
    dict[str, Any],
    dict[str, Any],
    dict[str, Any],
    dict[str, Any],
    dict[str, Any],
]:
    components, vehicles, vehicle_group_variant = _extract_bundle_registry_data(bundle_path)
    component_display_names = _extract_component_display_names_from_bundle(bundle_path)
    controls = _control_profiles()
    slot_controls = _slot_control_profiles()
    bundle_slots, bundle_scenarios = _extract_bundle_runtime_data(bundle_path)
    legacy_transform_hints = _extract_legacy_transform_hints(pia_decoder_path)
    canonical_provider_slots = _canonical_provider_slots(provider_specs_path)

    generated_components = {
        "_comment": (
            "Component metadata used by the integration to group discovered "
            "components, name devices, and suggest areas."
        ),
        "components": {},
    }
    generated_slots = {
        "_comment": (
            "Slot metadata used by the integration to label discovered "
            "values and define datatype, units, mode, and transforms."
        ),
        "slots": {},
    }
    generated_controls = {
        "_comment": (
            "Control metadata used by the integration to validate writable "
            "options and describe higher-level controls."
        ),
        "labels": controls,
        "slots": {
            f"{component_id}:{sensor_id}": profile
            for (component_id, sensor_id), profile in sorted(slot_controls.items())
        },
    }
    generated_vehicles = _build_vehicle_catalog_from_bundle(vehicles, vehicle_group_variant)
    generated_scenarios = _build_scenario_catalog_from_bundle(bundle_scenarios)

    for component_id, component in sorted(components.items(), key=lambda item: int(item[0])):
        component_record = _generate_component_record(
            component_id,
            component,
            component_display_names,
        )
        kind = component_record["kind"]
        generated_components["components"][component_id] = component_record
        for sensor_id, sensor in sorted(
            component.get("sensors", {}).items(),
            key=lambda item: int(item[0]),
        ):
            key = f"{component_id}:{sensor_id}"
            generated_slots["slots"][key] = _generate_slot_record(
                component_id,
                sensor_id,
                kind,
                sensor,
                controls,
                slot_controls,
                bundle_slots,
                legacy_transform_hints,
            )

    generated_coverage = _build_coverage_audit(
        generated_components,
        generated_slots,
        generated_controls,
        generated_scenarios,
        canonical_provider_slots,
    )
    generated_support_matrix = _build_support_matrix(
        generated_components,
        provider_specs_path,
        template_specs_path,
    )
    return (
        generated_components,
        generated_slots,
        generated_controls,
        generated_vehicles,
        generated_scenarios,
        generated_coverage,
        generated_support_matrix,
    )


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--source",
        choices=("auto", "bundle", "legacy"),
        default="auto",
        help="Metadata source mode. 'bundle' parses an expanded bundle.js directly.",
    )
    parser.add_argument("--registry", type=Path, default=DEFAULT_REGISTRY)
    parser.add_argument("--i18n", type=Path, default=DEFAULT_I18N)
    parser.add_argument("--scenarios", type=Path, default=DEFAULT_SCENARIOS)
    parser.add_argument("--vehicle-groups", type=Path, default=DEFAULT_VEHICLE_GROUPS)
    parser.add_argument("--bundle", type=Path, default=DEFAULT_BUNDLE)
    parser.add_argument("--pia-decoder", type=Path, default=DEFAULT_PIA_DECODER)
    parser.add_argument("--provider-specs", type=Path, default=DEFAULT_PROVIDER_SPECS)
    parser.add_argument("--template-specs", type=Path, default=DEFAULT_TEMPLATE_SPECS)
    parser.add_argument("--components-out", type=Path, default=DEFAULT_COMPONENTS_OUT)
    parser.add_argument("--slots-out", type=Path, default=DEFAULT_SLOTS_OUT)
    parser.add_argument("--controls-out", type=Path, default=DEFAULT_CONTROLS_OUT)
    parser.add_argument("--vehicles-out", type=Path, default=DEFAULT_VEHICLES_OUT)
    parser.add_argument("--scenarios-out", type=Path, default=DEFAULT_SCENARIO_OUT)
    parser.add_argument("--coverage-out", type=Path, default=DEFAULT_COVERAGE_OUT)
    parser.add_argument("--support-matrix-out", type=Path, default=DEFAULT_SUPPORT_MATRIX_OUT)
    args = parser.parse_args()

    use_legacy = args.source == "legacy" or (
        args.source == "auto"
        and all(
            path.exists()
            for path in (args.registry, args.i18n, args.scenarios, args.vehicle_groups)
        )
    )

    if use_legacy:
        (
            generated_components,
            generated_slots,
            generated_controls,
            generated_vehicles,
            generated_scenarios,
            generated_coverage,
            generated_support_matrix,
        ) = generate_overlay(
            args.registry,
            args.i18n,
            args.scenarios,
            args.vehicle_groups,
            args.bundle,
            args.pia_decoder,
            args.provider_specs,
            args.template_specs,
        )
    else:
        (
            generated_components,
            generated_slots,
            generated_controls,
            generated_vehicles,
            generated_scenarios,
            generated_coverage,
            generated_support_matrix,
        ) = generate_overlay_from_bundle(
            args.bundle,
            args.pia_decoder,
            args.provider_specs,
            args.template_specs,
        )

    for path in (
        args.components_out,
        args.slots_out,
        args.controls_out,
        args.vehicles_out,
        args.scenarios_out,
        args.coverage_out,
        args.support_matrix_out,
    ):
        path.parent.mkdir(parents=True, exist_ok=True)

    args.components_out.write_text(
        json.dumps(generated_components, indent=2, sort_keys=True) + "\n"
    )
    args.slots_out.write_text(
        json.dumps(generated_slots, indent=2, sort_keys=True) + "\n"
    )
    args.controls_out.write_text(
        json.dumps(generated_controls, indent=2, sort_keys=True) + "\n"
    )
    args.vehicles_out.write_text(
        json.dumps(generated_vehicles, indent=2, sort_keys=True) + "\n"
    )
    args.scenarios_out.write_text(
        json.dumps(generated_scenarios, indent=2, sort_keys=True) + "\n"
    )
    args.coverage_out.write_text(
        json.dumps(generated_coverage, indent=2, sort_keys=True) + "\n"
    )
    args.support_matrix_out.write_text(
        json.dumps(generated_support_matrix, indent=2, sort_keys=True) + "\n"
    )

    print(
        "Generated",
        len(generated_components["components"]),
        "components,",
        len(generated_slots["slots"]),
        "slots,",
        len(generated_controls["labels"]),
        "label control profiles,",
        len(generated_controls["slots"]),
        "slot control profiles,",
        len(generated_vehicles["models"]),
        "vehicle models,",
        len(generated_scenarios["entries"]),
        "scenario entries,",
        generated_support_matrix["summary"]["canonical_capability_count"],
        "canonical capabilities,",
        generated_support_matrix["summary"]["rich_template_entry_count"],
        "rich template entries",
    )


if __name__ == "__main__":
    main()
