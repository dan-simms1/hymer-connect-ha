"""HYMER Connect integration for Home Assistant."""

from __future__ import annotations

import asyncio
from functools import partial
import logging
from pathlib import Path
import re
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import (
    CONF_FILENAME,
    CONF_ICON,
    CONF_MODE,
    CONF_PASSWORD,
    CONF_USERNAME,
    EVENT_HOMEASSISTANT_STOP,
    Platform,
)
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import (
    ConfigEntryAuthFailed,
    ConfigEntryNotReady,
    HomeAssistantError,
)
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
    STATIC_URL_PATH,
)
from .coordinator import HymerConnectCoordinator
from .dashboard import (
    build_dashboard_config,
    describe_dashboard_entity,
    write_dashboard_storage,
    write_dashboard_yaml,
)
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
SERVICE_GENERATE_DASHBOARD = "generate_dashboard"
_GENERATED_DASHBOARD_KEY = "generated_dashboard"
_GENERATED_DASHBOARD_TITLE = "title"
_GENERATED_DASHBOARD_FILENAME = "filename"
_GENERATED_DASHBOARD_URL_PATH = "url_path"
_GENERATED_DASHBOARD_STORAGE_ID = "storage_id"
_STATIC_DIR = Path(__file__).with_name("static")

HymerConnectConfigEntry = ConfigEntry


def _bus_device_identifier_prefix(entry_id: str) -> str:
    return f"{entry_id}_bus_"


def _schedule_coordinator_shutdown(
    hass: HomeAssistant,
    coordinator: HymerConnectCoordinator,
) -> None:
    """Schedule coordinator shutdown from any Home Assistant thread context."""
    hass.create_task(coordinator.async_prepare_for_shutdown())


def _dashboard_slug(value: str) -> str:
    rendered = re.sub(r"[^a-z0-9]+", "-", value.lower()).strip("-")
    return rendered or "hymer-connect"


def _dashboard_storage_id(entry: HymerConnectConfigEntry) -> str:
    return f"{DOMAIN}_{entry.entry_id.lower()}"


def _persist_oauth_tokens(
    hass: HomeAssistant,
    entry: HymerConnectConfigEntry,
    access_token: str,
    refresh_token: str,
) -> None:
    """Persist OAuth token rotation back to the config entry."""
    if not access_token or not refresh_token:
        return
    if (
        entry.data.get(CONF_ACCESS_TOKEN) == access_token
        and entry.data.get(CONF_REFRESH_TOKEN) == refresh_token
    ):
        return
    hass.config_entries.async_update_entry(
        entry,
        data={
            **entry.data,
            CONF_ACCESS_TOKEN: access_token,
            CONF_REFRESH_TOKEN: refresh_token,
        },
    )


async def _async_prepare_authenticated_api(
    hass: HomeAssistant,
    entry: HymerConnectConfigEntry,
    api: HymerConnectApi,
) -> None:
    """Prepare an API client using app-like refresh-first auth behavior."""
    api.set_token_update_callback(
        partial(_persist_oauth_tokens, hass, entry)
    )

    stored_access_token = entry.data.get(CONF_ACCESS_TOKEN)
    stored_refresh_token = entry.data.get(CONF_REFRESH_TOKEN)
    if stored_access_token and stored_refresh_token:
        api.set_tokens(stored_access_token, stored_refresh_token)
        try:
            await api.get_account()
            return
        except HymerConnectAuthError:
            _LOGGER.info(
                "Stored HYMER OAuth tokens could not be refreshed; "
                "falling back to credential login"
            )
        except HymerConnectApiError as err:
            raise ConfigEntryNotReady(
                f"Cannot connect to HYMER API: {err}"
            ) from err

    if CONF_USERNAME not in entry.data or CONF_PASSWORD not in entry.data:
        raise ConfigEntryAuthFailed("No credentials available")

    try:
        await api.authenticate(entry.data[CONF_USERNAME], entry.data[CONF_PASSWORD])
    except HymerConnectAuthError as err:
        raise ConfigEntryAuthFailed(
            f"Authentication failed: {err}"
        ) from err
    except HymerConnectApiError as err:
        raise ConfigEntryNotReady(
            f"Cannot connect to HYMER API: {err}"
        ) from err


def _dashboard_config_dir(hass: HomeAssistant) -> Path:
    return (
        Path(hass.config.path())
        if hasattr(hass, "config") and hasattr(hass.config, "path")
        else Path("/config")
    )


def _dashboard_output_path(
    hass: HomeAssistant,
    entry: HymerConnectConfigEntry,
    filename: str | None,
) -> Path:
    base_dir = (
        Path(hass.config.path("dashboards", DOMAIN))
        if hasattr(hass, "config") and hasattr(hass.config, "path")
        else Path("/config") / "dashboards" / DOMAIN
    )
    stem = filename or entry.title or entry.entry_id
    return base_dir / f"{_dashboard_slug(stem)}.yaml"


def _dashboard_relative_filename(
    hass: HomeAssistant,
    output_path: Path,
) -> str:
    """Return a Lovelace-friendly config-relative filename."""
    config_dir = _dashboard_config_dir(hass)
    try:
        return str(output_path.relative_to(config_dir))
    except ValueError:
        return str(output_path)


def _dashboard_absolute_path(
    hass: HomeAssistant,
    filename: str,
) -> Path:
    """Resolve a dashboard filename back to an absolute path."""
    file_path = Path(filename)
    if file_path.is_absolute():
        return file_path
    return Path(hass.config.path(filename))


def _generated_dashboard_registration(
    entry: HymerConnectConfigEntry,
) -> dict[str, str] | None:
    """Return persisted generated-dashboard metadata for an entry."""
    entry_data = getattr(entry, "data", None)
    if not isinstance(entry_data, dict):
        return None
    registration = entry_data.get(_GENERATED_DASHBOARD_KEY)
    if not isinstance(registration, dict):
        return None
    title = registration.get(_GENERATED_DASHBOARD_TITLE)
    filename = registration.get(_GENERATED_DASHBOARD_FILENAME)
    url_path = registration.get(_GENERATED_DASHBOARD_URL_PATH)
    storage_id = registration.get(_GENERATED_DASHBOARD_STORAGE_ID)
    if not all(isinstance(value, str) and value for value in (title, filename, url_path)):
        return None
    result = {
        _GENERATED_DASHBOARD_TITLE: title,
        _GENERATED_DASHBOARD_FILENAME: filename,
        _GENERATED_DASHBOARD_URL_PATH: url_path,
    }
    if isinstance(storage_id, str) and storage_id:
        result[_GENERATED_DASHBOARD_STORAGE_ID] = storage_id
    return result


async def _async_register_yaml_dashboard(
    hass: HomeAssistant,
    *,
    url_path: str,
    title: str,
    filename: str,
    icon: str = "mdi:caravan",
    show_in_sidebar: bool = True,
    require_admin: bool = False,
) -> None:
    """Create or update a YAML Lovelace dashboard entry."""
    from homeassistant.components import frontend
    from homeassistant.components.lovelace import LOVELACE_DATA
    from homeassistant.components.lovelace import dashboard as lovelace_dashboard
    from homeassistant.components.lovelace.const import (
        CONF_REQUIRE_ADMIN,
        CONF_SHOW_IN_SIDEBAR,
        CONF_TITLE,
        CONF_URL_PATH,
        DEFAULT_ICON,
        DOMAIN as LOVELACE_DOMAIN,
        MODE_YAML,
    )

    lovelace_data = hass.data.get(LOVELACE_DATA)
    if lovelace_data is None:
        raise HomeAssistantError(
            "Lovelace is not loaded; cannot auto-register the generated dashboard"
        )

    config = {
        CONF_TITLE: title,
        CONF_ICON: icon or DEFAULT_ICON,
        CONF_SHOW_IN_SIDEBAR: show_in_sidebar,
        CONF_REQUIRE_ADMIN: require_admin,
        CONF_MODE: MODE_YAML,
        CONF_FILENAME: filename,
        CONF_URL_PATH: url_path,
    }

    existing = lovelace_data.dashboards.get(url_path)
    if existing is not None and getattr(existing, "mode", None) != MODE_YAML:
        raise HomeAssistantError(
            f"A non-YAML dashboard already exists at '/{url_path}'"
        )

    update = existing is not None
    lovelace_data.yaml_dashboards[url_path] = config
    if existing is None:
        lovelace_data.dashboards[url_path] = lovelace_dashboard.LovelaceYAML(
            hass,
            url_path,
            config,
        )
    else:
        existing.config = config

    frontend.async_register_built_in_panel(
        hass,
        LOVELACE_DOMAIN,
        frontend_url_path=url_path,
        require_admin=require_admin,
        show_in_sidebar=show_in_sidebar,
        sidebar_title=title,
        sidebar_icon=icon or DEFAULT_ICON,
        config={"mode": MODE_YAML},
        update=update,
    )


async def _async_restore_generated_dashboard(
    hass: HomeAssistant,
    entry: HymerConnectConfigEntry,
) -> None:
    """Restore a previously generated YAML dashboard into Lovelace."""
    registration = _generated_dashboard_registration(entry)
    if registration is None:
        return
    if registration.get(_GENERATED_DASHBOARD_STORAGE_ID):
        return

    output_path = _dashboard_absolute_path(
        hass,
        registration[_GENERATED_DASHBOARD_FILENAME],
    )
    if not output_path.exists():
        _LOGGER.warning(
            "Generated HYMER dashboard file for %s is missing: %s",
            entry.title,
            output_path,
        )
        return

    try:
        await _async_register_yaml_dashboard(
            hass,
            url_path=_dashboard_slug(
                registration[_GENERATED_DASHBOARD_URL_PATH]
            ),
            title=registration[_GENERATED_DASHBOARD_TITLE],
            filename=registration[_GENERATED_DASHBOARD_FILENAME],
        )
    except HomeAssistantError as err:
        _LOGGER.warning(
            "Could not restore generated HYMER dashboard for %s: %s",
            entry.title,
            err,
        )


def _entity_display_name(entity_entry: Any) -> str:
    name = getattr(entity_entry, "name", None) or getattr(
        entity_entry,
        "original_name",
        None,
    )
    if isinstance(name, str) and name:
        return name
    entity_id = getattr(entity_entry, "entity_id", "entity")
    if isinstance(entity_id, str) and "." in entity_id:
        entity_id = entity_id.split(".", 1)[1]
    return str(entity_id).replace("_", " ").title()


def _dashboard_allows_entity_category(entity: Any) -> bool:
    """Return True when a categorized entity still belongs on the dashboard."""
    return (
        getattr(entity, "tab", None) == "info"
        and getattr(entity, "section", None)
        in {
            "Basic Data",
            "Chassis Information",
            "Connectivity",
            "Doors",
            "Location",
        }
    )


async def _async_handle_generate_dashboard_service(
    hass: HomeAssistant,
    call: Any,
) -> None:
    loaded_entries = hass.data.get(DOMAIN, {})
    if not loaded_entries:
        raise HomeAssistantError(
            "No loaded HYMER Connect Metadata entries are available"
        )

    requested_entry_id = call.data.get("entry_id") if hasattr(call, "data") else None
    if requested_entry_id:
        coordinator = loaded_entries.get(requested_entry_id)
        if coordinator is None:
            raise HomeAssistantError(
                f"HYMER entry '{requested_entry_id}' is not currently loaded"
            )
    elif len(loaded_entries) == 1:
        coordinator = next(iter(loaded_entries.values()))
    else:
        raise HomeAssistantError(
            "Multiple HYMER entries are loaded; specify entry_id"
        )

    entry = getattr(coordinator, "config_entry", None)
    if entry is None:
        raise HomeAssistantError(
            "The loaded HYMER entry does not expose config entry metadata"
        )

    entity_registry = er.async_get(hass)
    dashboard_entities = []
    for entity_entry in er.async_entries_for_config_entry(entity_registry, entry.entry_id):
        if getattr(entity_entry, "platform", None) != DOMAIN:
            continue
        if getattr(entity_entry, "disabled_by", None) is not None:
            continue
        if getattr(entity_entry, "hidden_by", None) is not None:
            continue
        unique_id = getattr(entity_entry, "unique_id", None)
        entity_id = getattr(entity_entry, "entity_id", None)
        if not isinstance(unique_id, str) or not isinstance(entity_id, str):
            continue
        described = describe_dashboard_entity(
            entry.entry_id,
            entity_id=entity_id,
            unique_id=unique_id,
            name=_entity_display_name(entity_entry),
        )
        if described is not None:
            if (
                getattr(entity_entry, "entity_category", None) is not None
                and not _dashboard_allows_entity_category(described)
            ):
                continue
            dashboard_entities.append(described)

    config = build_dashboard_config(
        call.data.get("title") if hasattr(call, "data") and call.data.get("title") else f"{entry.title} Dashboard",
        dashboard_entities,
    )
    if not config.get("views"):
        raise HomeAssistantError(
            "No dashboard-worthy HYMER entities were found for the selected entry"
        )

    output_path = _dashboard_output_path(
        hass,
        entry,
        call.data.get("filename") if hasattr(call, "data") else None,
    )
    await hass.async_add_executor_job(write_dashboard_yaml, output_path, config)
    output_filename = _dashboard_relative_filename(hass, output_path)
    requested_url_path = (
        call.data.get("url_path") if hasattr(call, "data") else None
    )
    url_path = _dashboard_slug(
        requested_url_path or output_path.stem
    )
    storage_id = _dashboard_storage_id(entry)
    await hass.async_add_executor_job(
        partial(
            write_dashboard_storage,
            _dashboard_config_dir(hass),
            storage_id=storage_id,
            url_path=url_path,
            title=config["title"],
            config=config,
        )
    )
    try:
        await _async_register_yaml_dashboard(
            hass,
            url_path=url_path,
            title=config["title"],
            filename=output_filename,
        )
    except HomeAssistantError as err:
        _LOGGER.info(
            "Generated HYMER storage dashboard for %s, but live YAML panel "
            "registration was skipped: %s",
            entry.title,
            err,
        )
    registration = {
        _GENERATED_DASHBOARD_TITLE: config["title"],
        _GENERATED_DASHBOARD_FILENAME: output_filename,
        _GENERATED_DASHBOARD_URL_PATH: url_path,
        _GENERATED_DASHBOARD_STORAGE_ID: storage_id,
    }
    if _generated_dashboard_registration(entry) != registration:
        hass.config_entries.async_update_entry(
            entry,
            data={
                **entry.data,
                _GENERATED_DASHBOARD_KEY: registration,
            },
        )
    _LOGGER.info(
        "Generated HYMER dashboard for %s at %s (/%s)",
        entry.title,
        output_path,
        url_path,
    )


async def async_setup(hass: HomeAssistant, config: dict[str, object]) -> bool:
    """Register integration-wide services."""
    del config
    from homeassistant.components.http import StaticPathConfig

    await hass.http.async_register_static_paths(
        [
            StaticPathConfig(
                STATIC_URL_PATH,
                str(_STATIC_DIR),
                cache_headers=True,
            )
        ],
    )
    has_service = (
        hasattr(hass, "services")
        and hasattr(hass.services, "has_service")
        and hass.services.has_service(DOMAIN, SERVICE_GENERATE_DASHBOARD)
    )
    if hasattr(hass, "services") and not has_service:
        async def _handle_service(call: Any) -> None:
            await _async_handle_generate_dashboard_service(hass, call)

        hass.services.async_register(
            DOMAIN,
            SERVICE_GENERATE_DASHBOARD,
            _handle_service,
        )
    return True


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


def _distance_unit_override_updates(meta: Any, entity_entry: Any) -> dict[str, None]:
    """Clear stale distance unit overrides so entry display preferences apply."""
    if (
        getattr(meta, "unit", None) == "km"
        and getattr(entity_entry, "unit_of_measurement", None) in {"km", "mi"}
    ):
        return {"unit_of_measurement": None}
    return {}


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
        updates.update(_distance_unit_override_updates(meta, entity_entry))
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
    await _async_prepare_authenticated_api(hass, entry, api)

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
    await _async_restore_generated_dashboard(hass, entry)
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
