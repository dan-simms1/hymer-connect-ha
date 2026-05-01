"""Diagnostics support for HYMER Connect."""

from __future__ import annotations

from datetime import datetime, timezone
import logging
from typing import Any

from homeassistant.components.diagnostics import async_redact_data
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_PASSWORD, CONF_USERNAME
from homeassistant.core import HomeAssistant

from .catalog import (
    coverage_audit,
    control_catalog,
    match_vehicle_metadata,
    observed_component_profile,
    observed_slot_support_profile,
    resolved_scenarios,
    scenario_availability,
    slot_control_catalog,
    support_matrix,
    vehicle_catalog,
)
from .capability_resolver import all_resolved_capabilities, canonical_claimed_slots
from .const import (
    CONF_ACCESS_TOKEN,
    CONF_EHG_REFRESH_TOKEN,
    CONF_REFRESH_TOKEN,
    CONF_SCU_URN,
    DOMAIN,
    CONF_VEHICLE_ID,
    CONF_VEHICLE_URN,
    CONF_VIN,
)
from .discovery import all_components, all_slots, component_meta, slot_meta

_LOGGER = logging.getLogger(__name__)

_REDACT_CONFIG = {
    CONF_ACCESS_TOKEN,
    CONF_REFRESH_TOKEN,
    CONF_EHG_REFRESH_TOKEN,
    CONF_PASSWORD,
    CONF_SCU_URN,
    CONF_USERNAME,
    CONF_VEHICLE_ID,
    CONF_VEHICLE_URN,
    CONF_VIN,
}

_REDACT_SLOT_LABELS = {
    "gps_coordinates",
    "gps_location",
    "vin",
    "vin_text",
}


def _redact_slot_value(label: str | None, value: Any) -> Any:
    """Return a privacy-safe slot value for diagnostics exports."""
    if label in _REDACT_SLOT_LABELS and value not in (None, ""):
        return "[redacted]"
    return value


def _slot_snapshot(slot: tuple[int, int], value: Any) -> dict[str, Any]:
    meta = slot_meta(*slot)
    component = component_meta(slot[0])
    audit_slot = coverage_audit().get("slots", {}).get(f"{slot[0]}:{slot[1]}", {})
    return {
        "slot": list(slot),
        "value": _redact_slot_value(meta.label if meta else None, value),
        "label": meta.label if meta else None,
        "unit": meta.unit if meta else None,
        "datatype": meta.datatype if meta else None,
        "mode": meta.mode if meta else None,
        "wire_mode": meta.wire_mode if meta else None,
        "control_platform": meta.control_platform if meta else None,
        "component_kind": component.kind if component else None,
        "component_name": component.name if component else None,
        "audit_known": bool(audit_slot),
        "support_class": audit_slot.get("support_class"),
        "write_status": audit_slot.get("write_status"),
        "read_validation_status": audit_slot.get("read_validation_status"),
        "write_validation_status": audit_slot.get("write_validation_status"),
    }


def _slot_metadata_snapshot(slot: tuple[int, int]) -> dict[str, Any]:
    """Return metadata coverage for a slot without including the live value."""
    meta = slot_meta(*slot)
    component = component_meta(slot[0])
    audit_slot = coverage_audit().get("slots", {}).get(f"{slot[0]}:{slot[1]}", {})
    return {
        "slot": list(slot),
        "slot_key": f"{slot[0]}:{slot[1]}",
        "metadata_present": meta is not None,
        "coverage_audit_present": bool(audit_slot),
        "label": meta.label if meta else audit_slot.get("label"),
        "unit": meta.unit if meta else audit_slot.get("unit"),
        "datatype": meta.datatype if meta else audit_slot.get("datatype"),
        "mode": meta.mode if meta else audit_slot.get("mode"),
        "wire_mode": meta.wire_mode if meta else audit_slot.get("wire_mode"),
        "control_platform": (
            meta.control_platform if meta else audit_slot.get("control_platform")
        ),
        "component_kind": component.kind if component else audit_slot.get("component_kind"),
        "component_name": component.name if component else audit_slot.get("component_name"),
        "support_class": audit_slot.get("support_class"),
        "write_status": audit_slot.get("write_status"),
        "read_validation_status": audit_slot.get("read_validation_status"),
        "write_validation_status": audit_slot.get("write_validation_status"),
    }


def build_slot_debug_report(entry: ConfigEntry, coordinator: Any) -> dict[str, Any]:
    """Return a value-free slot coverage report for debug investigations."""
    historical_observed = coordinator.observed_slots
    active_observed = coordinator.active_slots
    stale_slots = coordinator.stale_slots
    audit_slots = coverage_audit().get("slots", {})
    canonical_claimed = canonical_claimed_slots(active_observed)

    active_unknown_slots = sorted(
        slot for slot in active_observed if slot_meta(*slot) is None
    )
    historical_unknown_slots = sorted(
        slot for slot in historical_observed if slot_meta(*slot) is None
    )
    active_audit_missing_slots = sorted(
        slot
        for slot in active_observed
        if f"{slot[0]}:{slot[1]}" not in audit_slots
    )
    historical_audit_missing_slots = sorted(
        slot
        for slot in historical_observed
        if f"{slot[0]}:{slot[1]}" not in audit_slots
    )
    raw_fallback_slots = sorted(
        slot for slot in active_observed if slot not in canonical_claimed
    )

    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "entry_title": entry.title,
        "active_slot_definition": (
            "recently updated by the active SignalR DataHub session"
        ),
        "active_slot_window_seconds": coordinator.active_slot_window_seconds,
        "observed_slot_count": len(historical_observed),
        "active_slot_count": len(active_observed),
        "stale_slot_count": len(stale_slots),
        "known_metadata_slot_count": len(all_slots()),
        "known_component_count": len(all_components()),
        "active_unknown_slot_count": len(active_unknown_slots),
        "historical_unknown_slot_count": len(historical_unknown_slots),
        "active_audit_missing_slot_count": len(active_audit_missing_slots),
        "historical_audit_missing_slot_count": len(historical_audit_missing_slots),
        "raw_fallback_slot_count": len(raw_fallback_slots),
        "active_unknown_slots": [
            _slot_metadata_snapshot(slot) for slot in active_unknown_slots
        ],
        "historical_unknown_slots": [
            _slot_metadata_snapshot(slot) for slot in historical_unknown_slots
        ],
        "active_audit_missing_slots": [
            _slot_metadata_snapshot(slot) for slot in active_audit_missing_slots
        ],
        "historical_audit_missing_slots": [
            _slot_metadata_snapshot(slot) for slot in historical_audit_missing_slots
        ],
        "raw_fallback_slots": [
            _slot_metadata_snapshot(slot) for slot in raw_fallback_slots
        ],
        "stale_slots": [
            _slot_metadata_snapshot(slot) for slot in sorted(stale_slots)
        ],
    }


async def async_get_config_entry_diagnostics(
    hass: HomeAssistant,
    entry: ConfigEntry,
) -> dict[str, Any]:
    """Return diagnostics for a config entry."""
    coordinator = hass.data[DOMAIN][entry.entry_id]
    historical_observed = coordinator.observed_slots
    active_observed = coordinator.active_slots
    stale_slots = coordinator.stale_slots
    resolved = all_resolved_capabilities(active_observed)
    canonical_claimed = canonical_claimed_slots(active_observed)
    slot_values = coordinator.slot_data
    slot_last_seen = coordinator.slot_last_seen
    audit_slots = coverage_audit().get("slots", {})

    unknown_slots = sorted(slot for slot in active_observed if slot_meta(*slot) is None)
    raw_fallback_slots = sorted(
        slot for slot in active_observed if slot not in canonical_claimed
    )
    audit_missing_slots = sorted(
        slot for slot in active_observed
        if f"{slot[0]}:{slot[1]}" not in audit_slots
    )
    historical_audit_missing_slots = sorted(
        slot for slot in historical_observed
        if f"{slot[0]}:{slot[1]}" not in audit_slots
    )
    if audit_missing_slots:
        _LOGGER.warning(
            "Diagnostics found %d observed slots missing from local coverage audit: %s",
            len(audit_missing_slots),
            audit_missing_slots[:20],
        )

    return {
        "entry": async_redact_data(dict(entry.data), _REDACT_CONFIG),
        "entry_title": entry.title,
        "signalr_connected": bool(
            coordinator.signalr_client and coordinator.signalr_client.connected
        ),
        "active_slot_definition": (
            "recently updated by the active SignalR DataHub session"
        ),
        "active_slot_window_seconds": coordinator.active_slot_window_seconds,
        "historical_observed_slot_count": len(historical_observed),
        "active_slot_count": len(active_observed),
        "stale_slot_count": len(stale_slots),
        "known_component_count": len(all_components()),
        "known_slot_count": len(all_slots()),
        "known_control_count": len(control_catalog()) + len(slot_control_catalog()),
        "known_vehicle_model_count": len(vehicle_catalog()),
        "coverage_audit_summary": coverage_audit().get("summary", {}),
        "support_matrix_summary": support_matrix().get("summary", {}),
        "vehicle_catalog_match": match_vehicle_metadata(entry.data),
        "platform_discovery_profile": coordinator.platform_discovery_profile,
        "observed_component_profile": observed_component_profile(active_observed),
        "observed_slot_support_profile": observed_slot_support_profile(active_observed),
        "historical_observed_component_profile": observed_component_profile(
            historical_observed
        ),
        "historical_observed_slot_support_profile": observed_slot_support_profile(
            historical_observed
        ),
        "audit_missing_slots": [
            _slot_snapshot(slot, slot_values.get(slot))
            for slot in audit_missing_slots
        ],
        "historical_audit_missing_slots": [
            _slot_snapshot(slot, slot_values.get(slot))
            for slot in historical_audit_missing_slots
        ],
        "stale_slots": [
            {
                **_slot_snapshot(slot, slot_values.get(slot)),
                "last_seen_monotonic": slot_last_seen.get(slot),
            }
            for slot in sorted(stale_slots)
        ],
        "canonical_resolved_capabilities": [
            {
                "key": capability.spec.key,
                "platform": capability.spec.platform,
                "provider_slot": list(capability.slot),
                "provider_component_name": capability.component_name,
                "provider_component_id": capability.component_id,
                "provider_sensor_id": capability.sensor_id,
                "capability_read_validation_status": capability.spec.read_validation_status,
                "capability_write_validation_status": capability.spec.write_validation_status,
                "capability_evidence_sources": list(capability.spec.evidence_sources),
                "all_present_candidates": [
                    list(candidate.key)
                    for candidate in capability.spec.candidates
                    if candidate.key in active_observed
                ],
                "provider_read_validation_status": audit_slots.get(
                    f"{capability.component_id}:{capability.sensor_id}",
                    {},
                ).get("read_validation_status"),
                "provider_write_validation_status": audit_slots.get(
                    f"{capability.component_id}:{capability.sensor_id}",
                    {},
                ).get("write_validation_status"),
            }
            for capability in resolved
        ],
        "unknown_slots": [
            _slot_snapshot(slot, slot_values.get(slot))
            for slot in unknown_slots
        ],
        "raw_fallback_slots": [
            _slot_snapshot(slot, slot_values.get(slot))
            for slot in raw_fallback_slots
        ],
        "scenarios": scenario_availability(active_observed),
        "executable_scenarios": resolved_scenarios(active_observed),
    }
