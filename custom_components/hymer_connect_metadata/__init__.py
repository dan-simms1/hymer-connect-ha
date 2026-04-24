"""HYMER Connect integration for Home Assistant."""

from __future__ import annotations

import asyncio
import logging

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import (
    CONF_PASSWORD,
    CONF_USERNAME,
    EVENT_HOMEASSISTANT_STOP,
    Platform,
)
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed, ConfigEntryNotReady
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers import device_registry as dr, entity_registry as er
from homeassistant.helpers.entity import EntityCategory

from .api import HymerConnectApi, HymerConnectApiError, HymerConnectAuthError
from .capability_resolver import invalidate_capability_cache, warm_capability_cache
from .catalog import invalidate_catalog_cache, warm_catalog_cache
from .const import (
    CONF_ACCESS_TOKEN,
    CONF_BRAND,
    CONF_EHG_REFRESH_TOKEN,
    CONF_REFRESH_TOKEN,
    CONF_VEHICLE_ID,
    CONF_VEHICLE_MODEL,
    CONF_SCU_URN,
    CONF_VEHICLE_URN,
    CONF_VIN,
    DOMAIN,
    MANUFACTURER,
    PLATFORMS,
)
from .coordinator import HymerConnectCoordinator
from .repairs import (
    async_create_missing_runtime_metadata_issue,
    async_delete_missing_runtime_metadata_issue,
)
from .discovery import invalidate_runtime_metadata_cache, warm_runtime_metadata_cache
from .discovery import all_components, all_slots
from .entity_base import (
    entry_vehicle_display_name,
    slot_entity_category,
    slot_entity_disabled_by_default,
    slot_entity_hidden_by_default,
    slot_entity_name_override,
)
from .preferences import admin_actions_enabled
from .runtime_metadata import RuntimeMetadataMissingError, ensure_runtime_metadata_present
from .template_specs import (
    invalidate_template_spec_cache,
    rich_template_claims,
    warm_template_spec_cache,
)

_LOGGER = logging.getLogger(__name__)

PLATFORM_LIST = [Platform(p) for p in PLATFORMS]

HymerConnectConfigEntry = ConfigEntry


def _bus_device_identifier_prefix(entry_id: str) -> str:
    return f"{entry_id}_bus_"


def _schedule_coordinator_shutdown(
    hass: HomeAssistant,
    coordinator: HymerConnectCoordinator,
) -> None:
    """Schedule coordinator shutdown from any Home Assistant thread context."""
    hass.create_task(coordinator.async_prepare_for_shutdown())


_RICH_TEMPLATE_PLATFORM_TAGS = {
    "air_conditioner": "climate",
    "awning": "cover",
    "boiler": "select",
    "climate": "climate",
    "fan": "fan",
    "fridge_level": "select",
    "fridge_power": "switch",
    "heater_energy": "select",
    "light": "light",
}


def _rich_template_claimed_generic_unique_ids(entry_id: str) -> set[str]:
    """Return generic slot entity unique IDs that should be removed.

    Rich entities should replace older generic slot entities after upgrades.
    The only raw generic control we intentionally keep alongside a rich entity
    is light brightness, because HA's device page still exposes that more
    clearly as a dedicated NumberEntity.
    """
    claimed: set[str] = set()
    slots = all_slots()
    for component_id, component in all_components().items():
        slots_for_component = {
            sensor_id: meta
            for (bus_id, sensor_id), meta in slots.items()
            if bus_id == component_id
        }
        for sensor_id, tag in rich_template_claims(
            component_id,
            component.kind if component else None,
            slots_for_component,
        ).items():
            if _RICH_TEMPLATE_PLATFORM_TAGS.get(tag) is None:
                continue
            meta = slots_for_component.get(sensor_id)
            if meta is None:
                continue
            if tag == "light" and meta.label == "brightness":
                continue
            claimed.add(f"{entry_id}_b{component_id}_s{sensor_id}")
    return claimed


def _generic_slot_from_unique_id(
    entry_id: str,
    unique_id: str | None,
) -> tuple[int, int] | None:
    """Parse a generic slot entity unique_id back to its slot tuple."""
    if unique_id is None:
        return None
    prefix = f"{entry_id}_b"
    if not unique_id.startswith(prefix):
        return None
    remainder = unique_id[len(prefix):]
    if "_s" not in remainder:
        return None
    bus_text, sensor_text = remainder.split("_s", 1)
    if not (bus_text.isdigit() and sensor_text.isdigit()):
        return None
    return int(bus_text), int(sensor_text)


def _simple_light_bus_from_unique_id(
    entry_id: str,
    unique_id: str | None,
) -> int | None:
    """Parse a simple light template unique_id back to its component bus."""
    if unique_id is None:
        return None
    prefix = f"{entry_id}_light_b"
    if not unique_id.startswith(prefix):
        return None
    remainder = unique_id[len(prefix):]
    if not remainder.isdigit():
        return None
    return int(remainder)


def _named_entity_policy_for_unique_id(
    entry: HymerConnectConfigEntry,
    unique_id: str | None,
) -> dict[str, object | None]:
    """Return registry updates for non-slot entities with stable unique_ids."""
    if unique_id is None:
        return {}
    entry_id = entry.entry_id
    if unique_id == f"{entry_id}_device_tracker":
        return {
            "original_name": "Location",
            "entity_category": None,
        }
    if unique_id == f"{entry_id}_restart_system":
        return {
            "original_name": "Restart System",
            "entity_category": EntityCategory.CONFIG,
            "disabled_by": (
                None
                if admin_actions_enabled(entry)
                else er.RegistryEntryDisabler.INTEGRATION
            ),
        }
    named_exact = {
        f"{entry_id}_canonical_battery_capacity_remaining": "Living Battery Capacity Remaining",
        f"{entry_id}_canonical_battery_charge_detected": "Living Battery Charging",
        f"{entry_id}_canonical_battery_cutoff_switch": "Living Battery Cutoff Switch",
        f"{entry_id}_canonical_battery_relative_capacity": "Living Battery Relative Capacity",
        f"{entry_id}_canonical_battery_soc": "Living Battery State Of Charge",
        f"{entry_id}_canonical_battery_state_of_health": "Living Battery State Of Health",
        f"{entry_id}_canonical_battery_temperature": "Living Battery Temperature",
        f"{entry_id}_canonical_battery_time_remaining": "Living Battery Time Remaining",
        f"{entry_id}_canonical_lte_connection_quality": "LTE Connection Quality",
        f"{entry_id}_canonical_lte_connection_state": "LTE Connection State",
        f"{entry_id}_canonical_scu_voltage": "SCU Voltage",
        f"{entry_id}_canonical_solar_aes_active": "Solar AES Active",
        f"{entry_id}_canonical_starter_battery_voltage": "Vehicle Battery Voltage",
    }
    if unique_id in named_exact:
        return {"original_name": named_exact[unique_id]}
    named_prefixes = {
        f"{entry_id}_heater_b": "Heater",
        f"{entry_id}_heater_energy_b": "Heater Energy Source",
        f"{entry_id}_boiler_mode_b": "Warm Water Boiler",
    }
    for prefix, display_name in named_prefixes.items():
        if unique_id.startswith(prefix):
            return {"original_name": display_name}
    return {}


def _legacy_unique_id_should_remove(
    entry_id: str,
    unique_id: str | None,
) -> bool:
    """Return whether a legacy entity unique_id should be removed."""
    if unique_id is None:
        return False
    return unique_id.startswith(f"{entry_id}_fridge_mode_b")


async def _async_apply_generic_slot_entity_policy(
    hass: HomeAssistant,
    entry: HymerConnectConfigEntry,
) -> None:
    """Apply category/visibility policy to generic raw slot entities."""
    entity_registry = er.async_get(hass)
    updated = 0

    for entity_entry in list(er.async_entries_for_config_entry(entity_registry, entry.entry_id)):
        if entity_entry.platform != DOMAIN:
            continue
        slot = _generic_slot_from_unique_id(entry.entry_id, entity_entry.unique_id)
        if slot is None:
            continue
        meta = all_slots().get(slot)
        if meta is None:
            continue
        component = all_components().get(slot[0])

        name_override = slot_entity_name_override(meta, component)
        category = slot_entity_category(meta)
        updates: dict[str, object | None] = {}
        if name_override is not None and entity_entry.original_name != name_override:
            updates["original_name"] = name_override
            if entity_entry.name is None:
                updates["name"] = name_override
        if entity_entry.entity_category != category:
            updates["entity_category"] = category
        integration_disabled = entity_entry.disabled_by == er.RegistryEntryDisabler.INTEGRATION
        should_disable = slot_entity_disabled_by_default(meta, entry)
        if should_disable:
            if entity_entry.disabled_by is None:
                updates["disabled_by"] = er.RegistryEntryDisabler.INTEGRATION
        elif integration_disabled and not slot_entity_hidden_by_default(meta):
            updates["disabled_by"] = None
        if not slot_entity_hidden_by_default(meta):
            if entity_entry.hidden_by is not None:
                updates["hidden_by"] = None
        if not updates:
            continue

        entity_registry.async_update_entity(
            entity_entry.entity_id,
            **updates,
        )
        updated += 1

    if updated:
        _LOGGER.info(
            "Applied raw-slot entity policy for %s: updated %d entities",
            entry.title,
            updated,
        )


async def _async_remove_legacy_entities(
    hass: HomeAssistant,
    entry: HymerConnectConfigEntry,
) -> None:
    """Remove legacy entities that no longer exist in the runtime model."""
    entity_registry = er.async_get(hass)
    removed = 0

    for entity_entry in list(er.async_entries_for_config_entry(entity_registry, entry.entry_id)):
        if entity_entry.platform != DOMAIN:
            continue
        if not _legacy_unique_id_should_remove(entry.entry_id, entity_entry.unique_id):
            continue
        entity_registry.async_remove(entity_entry.entity_id)
        removed += 1

    if removed:
        _LOGGER.info(
            "Removed %d legacy HYMER entities for %s",
            removed,
            entry.title,
        )


async def _async_apply_light_entity_name_policy(
    hass: HomeAssistant,
    entry: HymerConnectConfigEntry,
) -> None:
    """Refresh simple light entity names from generated component metadata."""
    entity_registry = er.async_get(hass)
    updated = 0

    for entity_entry in list(er.async_entries_for_config_entry(entity_registry, entry.entry_id)):
        if entity_entry.platform != DOMAIN:
            continue
        bus_id = _simple_light_bus_from_unique_id(entry.entry_id, entity_entry.unique_id)
        if bus_id is None:
            continue
        component = all_components().get(bus_id)
        if component is None or component.kind != "light":
            continue
        if entity_entry.original_name == component.name:
            continue
        entity_registry.async_update_entity(
            entity_entry.entity_id,
            original_name=component.name,
        )
        updated += 1

    if updated:
        _LOGGER.info(
            "Applied light-entity naming policy for %s: updated %d light entities",
            entry.title,
            updated,
        )


async def _async_apply_light_entity_name_policy_later(
    hass: HomeAssistant,
    entry: HymerConnectConfigEntry,
    *,
    delay_s: float = 1.0,
) -> None:
    """Re-apply light naming after entity registration settles."""
    await asyncio.sleep(delay_s)
    await _async_apply_light_entity_name_policy(hass, entry)


async def _async_apply_named_entity_policy(
    hass: HomeAssistant,
    entry: HymerConnectConfigEntry,
) -> None:
    """Refresh names/categories for non-slot entities with stable unique_ids."""
    entity_registry = er.async_get(hass)
    updated = 0

    for entity_entry in list(er.async_entries_for_config_entry(entity_registry, entry.entry_id)):
        if entity_entry.platform != DOMAIN:
            continue
        updates = _named_entity_policy_for_unique_id(entry, entity_entry.unique_id)
        if not updates:
            continue
        if (
            "original_name" in updates
            and entity_entry.name is None
            and entity_entry.original_name != updates["original_name"]
        ):
            updates["name"] = updates["original_name"]
        if (
            updates.get("original_name") == entity_entry.original_name
            and updates.get("entity_category", entity_entry.entity_category)
            == entity_entry.entity_category
            and updates.get("disabled_by", entity_entry.disabled_by)
            == entity_entry.disabled_by
            and (
                "name" not in updates
                or updates.get("name", entity_entry.name) == entity_entry.name
            )
        ):
            continue
        entity_registry.async_update_entity(
            entity_entry.entity_id,
            **updates,
        )
        updated += 1

    if updated:
        _LOGGER.info(
            "Applied named-entity policy for %s: updated %d entities",
            entry.title,
            updated,
        )


async def _async_apply_named_entity_policy_later(
    hass: HomeAssistant,
    entry: HymerConnectConfigEntry,
    *,
    delay_s: float = 1.0,
) -> None:
    """Re-apply named-entity policy after entity registration settles."""
    await asyncio.sleep(delay_s)
    await _async_apply_named_entity_policy(hass, entry)


async def _async_apply_generic_slot_entity_policy_later(
    hass: HomeAssistant,
    entry: HymerConnectConfigEntry,
    *,
    delay_s: float = 1.0,
) -> None:
    """Re-apply raw-slot policy after entity registration settles."""
    await asyncio.sleep(delay_s)
    await _async_apply_generic_slot_entity_policy(hass, entry)


async def _async_collapse_entities_to_vehicle_device(
    hass: HomeAssistant,
    entry: HymerConnectConfigEntry,
) -> None:
    """Attach all HYMER entities for one entry to the root vehicle device."""
    device_registry = dr.async_get(hass)
    entity_registry = er.async_get(hass)
    root_device = device_registry.async_get_or_create(
        config_entry_id=entry.entry_id,
        identifiers={(DOMAIN, entry.entry_id)},
        manufacturer=MANUFACTURER,
        model=entry.data.get(CONF_VEHICLE_MODEL) or "Smart Interface Unit",
        name=entry_vehicle_display_name(entry),
    )

    moved = 0
    for entity_entry in er.async_entries_for_config_entry(
        entity_registry,
        entry.entry_id,
    ):
        if entity_entry.platform != DOMAIN:
            continue
        if entity_entry.device_id == root_device.id:
            continue
        entity_registry.async_update_entity(
            entity_entry.entity_id,
            device_id=root_device.id,
        )
        moved += 1

    removable_bus_devices: list[str] = []
    bus_prefix = _bus_device_identifier_prefix(entry.entry_id)
    for device_entry in dr.async_entries_for_config_entry(
        device_registry,
        entry.entry_id,
    ):
        if device_entry.id == root_device.id:
            continue
        if not any(
            identifier[0] == DOMAIN and identifier[1].startswith(bus_prefix)
            for identifier in device_entry.identifiers
        ):
            continue
        if er.async_entries_for_device(
            entity_registry,
            device_entry.id,
            include_disabled_entities=True,
        ):
            continue
        removable_bus_devices.append(device_entry.id)

    for device_id in removable_bus_devices:
        device_registry.async_remove_device(device_id)

    if moved or removable_bus_devices:
        _LOGGER.info(
            "Collapsed HYMER registry devices for %s: moved %d entities, removed %d legacy component devices",
            entry.title,
            moved,
            len(removable_bus_devices),
        )


async def _async_remove_rich_template_raw_duplicates(
    hass: HomeAssistant,
    entry: HymerConnectConfigEntry,
) -> None:
    """Remove generic slot entities superseded by rich template entities."""
    entity_registry = er.async_get(hass)
    removable_unique_ids = _rich_template_claimed_generic_unique_ids(entry.entry_id)
    removed = 0

    for entity_entry in list(er.async_entries_for_config_entry(entity_registry, entry.entry_id)):
        if entity_entry.platform != DOMAIN:
            continue
        if entity_entry.unique_id not in removable_unique_ids:
            continue
        entity_registry.async_remove(entity_entry.entity_id)
        removed += 1

    if removed:
        _LOGGER.info(
            "Removed %d generic HYMER entities superseded by rich templates for %s",
            removed,
            entry.title,
        )


async def async_setup_entry(
    hass: HomeAssistant, entry: HymerConnectConfigEntry
) -> bool:
    """Set up HYMER Connect from a config entry."""
    try:
        ensure_runtime_metadata_present()
    except RuntimeMetadataMissingError as err:
        async_create_missing_runtime_metadata_issue(
            hass,
            err.missing_files,
            err.prepare_command,
        )
        raise ConfigEntryNotReady(str(err)) from err
    async_delete_missing_runtime_metadata_issue(hass)

    invalidate_runtime_metadata_cache()
    invalidate_capability_cache()
    invalidate_template_spec_cache()
    invalidate_catalog_cache()

    try:
        await hass.async_add_executor_job(warm_runtime_metadata_cache)
        await hass.async_add_executor_job(warm_capability_cache)
        await hass.async_add_executor_job(warm_template_spec_cache)
        await hass.async_add_executor_job(warm_catalog_cache)
    except Exception as err:
        raise ConfigEntryNotReady(
            f"Could not load HYMER metadata: {err}"
        ) from err

    session = async_get_clientsession(hass)
    brand = entry.data.get(CONF_BRAND, "hymer")
    api = HymerConnectApi(session, brand=brand)

    # Always re-authenticate with stored credentials to get fresh tokens
    if CONF_USERNAME in entry.data:
        try:
            tokens = await api.authenticate(
                entry.data[CONF_USERNAME], entry.data[CONF_PASSWORD]
            )
            # Update stored tokens
            hass.config_entries.async_update_entry(
                entry,
                data={
                    **entry.data,
                    CONF_ACCESS_TOKEN: tokens["access_token"],
                    CONF_REFRESH_TOKEN: tokens["refresh_token"],
                },
            )
        except HymerConnectAuthError as err:
            raise ConfigEntryAuthFailed(
                f"Authentication failed: {err}"
            ) from err
        except HymerConnectApiError as err:
            raise ConfigEntryNotReady(
                f"Cannot connect to HYMER API: {err}"
            ) from err
    else:
        raise ConfigEntryAuthFailed("No credentials available")

    vehicle_urn = entry.data.get(CONF_VEHICLE_URN, "")
    scu_urn = entry.data.get(CONF_SCU_URN, "")
    vehicle_id = entry.data.get(CONF_VEHICLE_ID)
    vin = entry.data.get(CONF_VIN, "")
    ehg_refresh_token = entry.data.get(CONF_EHG_REFRESH_TOKEN, "")

    coordinator = HymerConnectCoordinator(
        hass, api, session, entry,
        vehicle_urn=vehicle_urn,
        scu_urn=scu_urn,
        vehicle_id=vehicle_id,
        vin=vin,
        ehg_refresh_token=ehg_refresh_token,
    )

    if hasattr(hass, "bus") and hasattr(hass.bus, "async_listen_once"):
        def _handle_homeassistant_stop(_event: object) -> None:
            _schedule_coordinator_shutdown(hass, coordinator)

        stop_unsub = hass.bus.async_listen_once(
            EVENT_HOMEASSISTANT_STOP,
            _handle_homeassistant_stop,
        )
        if hasattr(entry, "async_on_unload"):
            entry.async_on_unload(stop_unsub)

    await coordinator.async_config_entry_first_refresh()

    hass.data.setdefault(DOMAIN, {})[entry.entry_id] = coordinator

    await _async_remove_rich_template_raw_duplicates(hass, entry)
    await _async_remove_legacy_entities(hass, entry)
    await _async_collapse_entities_to_vehicle_device(hass, entry)
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORM_LIST)
    await _async_apply_generic_slot_entity_policy(hass, entry)
    await _async_apply_light_entity_name_policy(hass, entry)
    await _async_apply_named_entity_policy(hass, entry)
    coordinator.track_background_task(
        hass.async_create_task(_async_apply_generic_slot_entity_policy_later(hass, entry))
    )
    coordinator.track_background_task(
        hass.async_create_task(
            _async_apply_light_entity_name_policy_later(hass, entry, delay_s=5.0)
        )
    )
    coordinator.track_background_task(
        hass.async_create_task(
            _async_apply_named_entity_policy_later(hass, entry, delay_s=5.0)
        )
    )
    coordinator.mark_entry_setup_complete()
    return True


async def async_unload_entry(
    hass: HomeAssistant, entry: HymerConnectConfigEntry
) -> bool:
    """Unload a config entry."""
    coordinator: HymerConnectCoordinator = hass.data[DOMAIN].get(entry.entry_id)
    if coordinator:
        coordinator.clear_platform_refresh_callbacks()
        await coordinator.async_prepare_for_shutdown()
    if unload_ok := await hass.config_entries.async_unload_platforms(
        entry, PLATFORM_LIST
    ):
        hass.data[DOMAIN].pop(entry.entry_id)
    return unload_ok
