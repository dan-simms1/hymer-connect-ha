"""Scene platform for HYMER Connect runtime scenarios."""

from __future__ import annotations

from typing import Any

from homeassistant.components.scene import Scene
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .catalog import resolved_scenarios
from .const import DOMAIN
from .coordinator import HymerConnectCoordinator
from .entity_base import root_device_info

_ICON_MAP = {
    "arrival": "mdi:home-import-outline",
    "departure": "mdi:home-export-outline",
    "good_night": "mdi:sleep",
    "reading_light": "mdi:book-open-variant",
    "sun_downer": "mdi:glass-cocktail",
}


def _scene_unique_id(entry: ConfigEntry, scenario: dict[str, Any]) -> str:
    """Stable id keyed by (kind, id, key).

    The runtime catalog can hold a ``scenario`` and a ``scene`` with the same
    ``key`` (e.g. GOOD_MORNING); keying by ``key`` alone collapses them onto one
    entity, so one silently disappears. Including kind + id keeps them distinct.
    """
    kind = str(scenario.get("kind", "scenario")).lower()
    ident = str(scenario.get("id", "")).lower()
    key = str(scenario.get("key", "scenario")).lower()
    parts = [part for part in (kind, ident, key) if part]
    return f"{entry.entry_id}_scene_" + "_".join(parts)


def _icon_for(entry: dict[str, Any]) -> str:
    icon = entry.get("icon")
    key = str(entry.get("key", "")).lower()
    if isinstance(icon, str) and icon.startswith("mdi:"):
        return icon
    if key in _ICON_MAP:
        return _ICON_MAP[key]
    return "mdi:play-circle-outline"


class HymerScenarioScene(
    CoordinatorEntity[HymerConnectCoordinator],
    Scene,
):
    """Scenario/scene entity backed by the runtime scenario catalog."""

    _attr_has_entity_name = True

    def __init__(
        self,
        coordinator: HymerConnectCoordinator,
        entry: ConfigEntry,
        scenario: dict[str, Any],
    ) -> None:
        super().__init__(coordinator)
        self._scenario = scenario
        key = str(scenario.get("key", "scenario"))
        self._scenario_key = key
        self._scenario_kind = str(scenario.get("kind", "scenario"))
        self._scenario_id = str(scenario.get("id", ""))
        self._attr_unique_id = _scene_unique_id(entry, scenario)
        self._attr_name = str(scenario.get("name", key.lower().replace("_", " ").title()))
        self._attr_icon = _icon_for(scenario)
        self._attr_device_info = root_device_info(entry)

    def _matches(self, scenario: dict[str, Any]) -> bool:
        return (
            str(scenario.get("key")) == self._scenario_key
            and str(scenario.get("kind", "scenario")) == self._scenario_kind
            and str(scenario.get("id", "")) == self._scenario_id
        )

    def _current_scenario(self) -> dict[str, Any] | None:
        for scenario in resolved_scenarios(self.coordinator.active_slots):
            if self._matches(scenario):
                return scenario
        return None

    @property
    def available(self) -> bool:
        return self._current_scenario() is not None

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        current = self._current_scenario()
        scenario = current or self._scenario
        base_actions = list(self._scenario.get("actions", []))
        actions = list(current.get("actions", [])) if current else []
        return {
            "kind": scenario.get("kind"),
            "catalog_key": scenario.get("key"),
            "catalog_icon": scenario.get("icon"),
            "action_count": scenario.get("action_count", len(base_actions)),
            "present_action_count": scenario.get(
                "present_action_count",
                len(actions),
            ),
            "supported_action_count": scenario.get(
                "supported_action_count",
                len(actions),
            ),
            "actions": actions,
        }

    async def async_activate(self, **kwargs: Any) -> None:
        scenario = self._current_scenario()
        if scenario is None:
            raise HomeAssistantError(
                f"Scene {self._scenario_key} is not currently executable for this vehicle"
            )
        client = await self.coordinator.async_ensure_signalr_connected()
        await client.send_slot_actions(list(scenario.get("actions", [])))


def _migrate_scene_unique_ids(
    hass: HomeAssistant,
    entry: ConfigEntry,
    scenarios: list[dict[str, Any]],
) -> None:
    """Rename pre-2.0 ``_scene_<key>`` ids to the (kind, id, key) scheme.

    Only the first catalog entry that owned a given old id migrates onto its new
    id (that is the one that was actually visible); any others were previously
    shadowed and simply appear fresh under their new ids.
    """
    try:
        from homeassistant.helpers import entity_registry as er

        registry = er.async_get(hass)
    except Exception:  # noqa: BLE001 - best-effort; skip under test stubs/old HA
        return
    claimed: set[str] = set()
    for scenario in scenarios:
        key = str(scenario.get("key", "scenario")).lower()
        old_uid = f"{entry.entry_id}_scene_{key}"
        if old_uid in claimed:
            continue
        claimed.add(old_uid)
        new_uid = _scene_unique_id(entry, scenario)
        if new_uid == old_uid:
            continue
        old_entity = registry.async_get_entity_id("scene", DOMAIN, old_uid)
        if old_entity and registry.async_get_entity_id("scene", DOMAIN, new_uid) is None:
            registry.async_update_entity(old_entity, new_unique_id=new_uid)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    coordinator: HymerConnectCoordinator = hass.data[DOMAIN][entry.entry_id]
    await coordinator.wait_for_first_frame(timeout=30.0)
    _migrate_scene_unique_ids(
        hass, entry, resolved_scenarios(coordinator.active_slots)
    )
    tracked_unique_ids: set[str] = set()

    def _new_entities(scenarios: list[dict[str, Any]]) -> list[HymerScenarioScene]:
        additions: list[HymerScenarioScene] = []
        for scenario in scenarios:
            entity = HymerScenarioScene(coordinator, entry, scenario)
            unique_id = getattr(entity, "unique_id", None) or getattr(
                entity, "_attr_unique_id", None
            )
            if unique_id in tracked_unique_ids:
                continue
            if unique_id is not None:
                tracked_unique_ids.add(unique_id)
            additions.append(entity)
        return additions

    initial_entities = _new_entities(resolved_scenarios(coordinator.active_slots))

    async def _refresh_scenes(new_slots: set[tuple[int, int]]) -> None:
        if not new_slots:
            return
        additions = _new_entities(resolved_scenarios(coordinator.active_slots))
        if additions:
            async_add_entities(additions)

    coordinator.register_platform_refresh("scene", _refresh_scenes)

    if initial_entities:
        async_add_entities(initial_entities)
