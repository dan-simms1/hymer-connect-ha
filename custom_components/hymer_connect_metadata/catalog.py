"""Runtime catalogs for controls, scenarios, and vehicle metadata."""

from __future__ import annotations

import json
import logging
import re
from functools import lru_cache
from typing import Any

from .const import CONF_BRAND, CONF_VEHICLE_MODEL, CONF_VEHICLE_MODEL_GROUP
from .runtime_metadata import data_path
from .slot_actions import action_is_supported

_LOGGER = logging.getLogger(__name__)

_CONTROL_CATALOG = data_path("control_catalog.json")
_SCENARIO_CATALOG = data_path("scenario_catalog.json")
_VEHICLE_CATALOG = data_path("vehicle_catalog.json")
_COVERAGE_AUDIT = data_path("coverage_audit.json")
_SUPPORT_MATRIX = data_path("support_matrix.json")

_BRAND_NORMALIZATION = {
    "hymer": "hymer",
    "dethleffs": "dethleffs",
    "lmc": "lmc",
    "buerstner": "buerstner",
    "eriba": "eriba",
    "laika": "laika",
    "niesmann+bischoff": "niesmann-bischoff",
    "niesmann-bischoff": "niesmann-bischoff",
    "sunlight": "sunlight",
    "carado": "carado",
    "thor": "thor",
}


def _normalize_text(value: str | None) -> str:
    if not value:
        return ""
    return re.sub(r"[^a-z0-9]+", "", value.lower())


def _normalize_brand(value: str | None) -> str:
    if not value:
        return ""
    return _BRAND_NORMALIZATION.get(value.lower(), value.lower())


@lru_cache(maxsize=1)
def control_catalog() -> dict[str, dict[str, Any]]:
    if not _CONTROL_CATALOG.exists():
        return {}
    raw = json.loads(_CONTROL_CATALOG.read_text())
    return raw.get("labels", {})


@lru_cache(maxsize=1)
def slot_control_catalog() -> dict[str, dict[str, Any]]:
    if not _CONTROL_CATALOG.exists():
        return {}
    raw = json.loads(_CONTROL_CATALOG.read_text())
    return raw.get("slots", {})


@lru_cache(maxsize=1)
def scenario_catalog() -> list[dict[str, Any]]:
    if not _SCENARIO_CATALOG.exists():
        return []
    raw = json.loads(_SCENARIO_CATALOG.read_text())
    return raw.get("entries", [])


@lru_cache(maxsize=1)
def vehicle_catalog() -> dict[str, dict[str, Any]]:
    if not _VEHICLE_CATALOG.exists():
        return {}
    raw = json.loads(_VEHICLE_CATALOG.read_text())
    return raw.get("models", {})


@lru_cache(maxsize=1)
def coverage_audit() -> dict[str, Any]:
    if not _COVERAGE_AUDIT.exists():
        return {}
    return json.loads(_COVERAGE_AUDIT.read_text())


@lru_cache(maxsize=1)
def support_matrix() -> dict[str, Any]:
    if not _SUPPORT_MATRIX.exists():
        return {}
    return json.loads(_SUPPORT_MATRIX.read_text())


def invalidate_catalog_cache() -> None:
    """Clear cached runtime catalogs so entry reloads see local JSON updates."""
    control_catalog.cache_clear()
    slot_control_catalog.cache_clear()
    scenario_catalog.cache_clear()
    vehicle_catalog.cache_clear()
    coverage_audit.cache_clear()
    support_matrix.cache_clear()


def warm_catalog_cache() -> None:
    """Load runtime catalogs into cache off the event loop."""
    control_catalog()
    slot_control_catalog()
    scenario_catalog()
    vehicle_catalog()
    coverage_audit()
    support_matrix()


def match_vehicle_metadata(entry_data: dict[str, Any]) -> dict[str, Any]:
    """Match the configured vehicle metadata against the runtime vehicle catalog."""
    brand = _normalize_brand(entry_data.get(CONF_BRAND))
    model = entry_data.get(CONF_VEHICLE_MODEL, "")
    model_group = entry_data.get(CONF_VEHICLE_MODEL_GROUP, "")
    normalized_model = _normalize_text(model)
    normalized_group = _normalize_text(model_group)

    exact_matches: list[dict[str, Any]] = []
    group_matches: list[dict[str, Any]] = []

    for key, meta in vehicle_catalog().items():
        meta_brand = _normalize_brand(meta.get("brand"))
        meta_name = meta.get("name", "")
        meta_group = meta.get("group", "")

        if brand and meta_brand and meta_brand != brand:
            continue

        candidate = {
            "key": key,
            "name": meta_name,
            "brand": meta.get("brand"),
            "group": meta_group,
        }

        if normalized_model and _normalize_text(meta_name) == normalized_model:
            exact_matches.append(candidate)
            continue

        if normalized_group and _normalize_text(meta_group) == normalized_group:
            group_matches.append(candidate)

    return {
        "query": {
            "brand": entry_data.get(CONF_BRAND),
            "model": model,
            "model_group": model_group,
        },
        "exact_matches": exact_matches,
        "group_matches": group_matches[:20],
    }


def scenario_availability(observed_slots: set[tuple[int, int]]) -> list[dict[str, Any]]:
    """Return availability information for runtime scenarios/scenes."""
    available: list[dict[str, Any]] = []
    for entry in scenario_catalog():
        actions = entry.get("actions", [])
        if not isinstance(actions, list):
            continue
        present_actions = [
            action for action in actions
            if (action.get("component_id"), action.get("sensor_id")) in observed_slots
        ]
        supported_actions = [
            action for action in actions
            if action_is_supported(action, observed_slots)
        ]
        available.append(
            {
                "key": entry.get("key"),
                "kind": entry.get("kind"),
                "id": entry.get("id"),
                "name": entry.get("name"),
                "icon": entry.get("icon"),
                "action_count": len(actions),
                "present_action_count": len(present_actions),
                "supported_action_count": len(supported_actions),
                "executable_for_vehicle": bool(actions) and len(supported_actions) == len(actions),
                "complete_for_vehicle": bool(actions) and len(present_actions) == len(actions),
                "present_actions": present_actions,
                "supported_actions": supported_actions,
            }
        )
    return available


def resolved_scenarios(observed_slots: set[tuple[int, int]]) -> list[dict[str, Any]]:
    """Return executable scenario/scene entries for the current vehicle.

    The scenario catalog is a union across supported provider families. Runtime
    execution therefore uses the actions whose `(component_id, sensor_id)` pairs
    are actually present on the selected vehicle.
    """
    resolved: list[dict[str, Any]] = []
    for entry in scenario_availability(observed_slots):
        supported_actions = entry.get("supported_actions", [])
        if not entry.get("executable_for_vehicle") or not supported_actions:
            continue
        resolved.append(
            {
                "key": entry.get("key"),
                "kind": entry.get("kind"),
                "id": entry.get("id"),
                "name": entry.get("name"),
                "icon": entry.get("icon"),
                "action_count": entry.get("action_count", 0),
                "present_action_count": entry.get("present_action_count", 0),
                "supported_action_count": entry.get("supported_action_count", 0),
                "actions": supported_actions,
            }
        )
    return resolved


def observed_component_profile(observed_slots: set[tuple[int, int]]) -> list[dict[str, Any]]:
    """Return coverage-oriented component summaries for the current vehicle."""
    audit = coverage_audit()
    slot_audit = audit.get("slots", {})
    component_audit = audit.get("components", {})
    grouped: dict[int, dict[str, Any]] = {}

    for component_id, sensor_id in sorted(observed_slots):
        slot_key = f"{component_id}:{sensor_id}"
        slot_meta = slot_audit.get(slot_key, {})
        component_meta = component_audit.get(str(component_id), {})
        if not slot_meta:
            _LOGGER.warning(
                "Observed slot %s is not present in the local coverage audit",
                slot_key,
            )
        grouped.setdefault(
            component_id,
            {
                "component_id": component_id,
                "name": component_meta.get("name"),
                "kind": component_meta.get("kind"),
                "coverage_tags": component_meta.get("coverage_tags", []),
                "observed_slots": [],
                "observed_writable_slots": [],
                "suppressed_writable_slots": [],
                "audit_missing_slots": [],
            },
        )
        grouped[component_id]["observed_slots"].append(slot_key)
        if slot_meta.get("write_status") in {"supported", "suppressed"}:
            grouped[component_id]["observed_writable_slots"].append(slot_key)
        if slot_meta.get("write_status") == "suppressed":
            grouped[component_id]["suppressed_writable_slots"].append(slot_key)
        if not slot_meta:
            grouped[component_id]["audit_missing_slots"].append(slot_key)
            if "audit_missing" not in grouped[component_id]["coverage_tags"]:
                grouped[component_id]["coverage_tags"].append("audit_missing")

    for component in grouped.values():
        component["observed_slot_count"] = len(component["observed_slots"])
        component["observed_writable_slot_count"] = len(component["observed_writable_slots"])
        component["suppressed_writable_slot_count"] = len(component["suppressed_writable_slots"])
        component["audit_missing_slot_count"] = len(component["audit_missing_slots"])
        component["coverage_tags"] = sorted(component["coverage_tags"])

    return list(grouped.values())


def observed_slot_support_profile(observed_slots: set[tuple[int, int]]) -> dict[str, Any]:
    """Return writable/support coverage summaries for the current vehicle."""
    audit = coverage_audit()
    slot_audit = audit.get("slots", {})
    supported_writable: list[dict[str, Any]] = []
    suppressed_writable: list[dict[str, Any]] = []
    audit_missing_slots: list[str] = []
    by_support_class: dict[str, int] = {}
    read_validation_status_counts: dict[str, int] = {}
    write_validation_status_counts: dict[str, int] = {}

    for component_id, sensor_id in sorted(observed_slots):
        slot_key = f"{component_id}:{sensor_id}"
        slot_meta = slot_audit.get(slot_key)
        if not isinstance(slot_meta, dict):
            audit_missing_slots.append(slot_key)
            by_support_class["audit_missing"] = by_support_class.get("audit_missing", 0) + 1
            read_validation_status_counts["audit_missing"] = (
                read_validation_status_counts.get("audit_missing", 0) + 1
            )
            write_validation_status_counts["audit_missing"] = (
                write_validation_status_counts.get("audit_missing", 0) + 1
            )
            continue
        support_class = str(slot_meta.get("support_class", "unknown"))
        by_support_class[support_class] = by_support_class.get(support_class, 0) + 1
        read_validation_status = str(
            slot_meta.get("read_validation_status", "unknown")
        )
        write_validation_status = str(
            slot_meta.get("write_validation_status", "unknown")
        )
        read_validation_status_counts[read_validation_status] = (
            read_validation_status_counts.get(read_validation_status, 0) + 1
        )
        write_validation_status_counts[write_validation_status] = (
            write_validation_status_counts.get(write_validation_status, 0) + 1
        )
        write_status = slot_meta.get("write_status")
        if write_status == "supported":
            supported_writable.append(slot_meta)
        elif write_status == "suppressed":
            suppressed_writable.append(slot_meta)

    return {
        "support_class_counts": by_support_class,
        "read_validation_status_counts": read_validation_status_counts,
        "write_validation_status_counts": write_validation_status_counts,
        "supported_writable_slots": supported_writable,
        "suppressed_writable_slots": suppressed_writable,
        "audit_missing_slots": audit_missing_slots,
    }
