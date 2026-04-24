from __future__ import annotations

import asyncio
import importlib
import unittest

from tests.hymer_test_support import ensure_package_paths, install_homeassistant_stubs


ensure_package_paths()


class SceneTests(unittest.TestCase):
    def _arrival_scenario(self) -> dict:
        catalog = importlib.import_module("custom_components.hymer_connect_metadata.catalog")
        return next(
            entry
            for entry in catalog.scenario_catalog()
            if entry["key"] == "ARRIVAL"
        )

    def test_scene_entity_tracks_active_slots_and_activation(self) -> None:
        install_homeassistant_stubs()
        scene_mod = importlib.import_module("custom_components.hymer_connect_metadata.scene")
        exceptions = importlib.import_module("homeassistant.exceptions")

        scenario = self._arrival_scenario()
        active_slots = {
            (action["component_id"], action["sensor_id"])
            for action in scenario["actions"]
        }
        sent_actions: list[list[dict]] = []

        class Client:
            async def send_slot_actions(self, actions):
                sent_actions.append(list(actions))

        class Coordinator:
            def __init__(self) -> None:
                self.active_slots = set(active_slots)
                self.observed_slots = set(active_slots)

            async def async_ensure_signalr_connected(self):
                return Client()

        coordinator = Coordinator()
        entry = type(
            "Entry",
            (),
            {"entry_id": "entry-1", "title": "Test Van", "data": {}},
        )()

        entity = scene_mod.HymerScenarioScene(coordinator, entry, scenario)

        self.assertTrue(entity.available)
        self.assertEqual(
            entity.extra_state_attributes["supported_action_count"],
            len(scenario["actions"]),
        )

        asyncio.run(entity.async_activate())
        self.assertEqual(len(sent_actions), 1)
        self.assertEqual(len(sent_actions[0]), len(scenario["actions"]))

        coordinator.active_slots = set()
        self.assertFalse(entity.available)
        self.assertEqual(entity.extra_state_attributes["supported_action_count"], 0)
        self.assertEqual(
            entity.extra_state_attributes["action_count"],
            len(scenario["actions"]),
        )

        with self.assertRaises(exceptions.HomeAssistantError):
            asyncio.run(entity.async_activate())

    def test_scene_setup_refresh_adds_late_executable_scene_without_duplicates(self) -> None:
        install_homeassistant_stubs()
        scene_mod = importlib.import_module("custom_components.hymer_connect_metadata.scene")
        const = importlib.import_module("custom_components.hymer_connect_metadata.const")

        scenario = self._arrival_scenario()
        scenario_slots = {
            (action["component_id"], action["sensor_id"])
            for action in scenario["actions"]
        }

        class Coordinator:
            def __init__(self) -> None:
                self.active_slots = set()
                self.observed_slots = set()
                self.refresh_callbacks = {}

            async def wait_for_first_frame(self, timeout=30.0):
                return True

            def register_platform_refresh(self, platform, callback):
                self.refresh_callbacks[platform] = callback

        coordinator = Coordinator()
        entry = type(
            "Entry",
            (),
            {"entry_id": "entry-1", "title": "Test Van", "data": {}},
        )()
        hass = type(
            "Hass",
            (),
            {"data": {const.DOMAIN: {entry.entry_id: coordinator}}},
        )()
        collected = []

        async def run() -> None:
            await scene_mod.async_setup_entry(
                hass,
                entry,
                lambda entities: collected.extend(entities),
            )
            self.assertEqual(len(collected), 0)
            coordinator.active_slots = set(scenario_slots)
            coordinator.observed_slots = set(scenario_slots)
            await coordinator.refresh_callbacks["scene"](set(scenario_slots))
            first_count = len(collected)
            await coordinator.refresh_callbacks["scene"](set(scenario_slots))
            self.assertEqual(len(collected), first_count)

        asyncio.run(run())

        self.assertGreaterEqual(len(collected), 1)
        self.assertTrue(all(entity.available for entity in collected))


if __name__ == "__main__":
    unittest.main()
