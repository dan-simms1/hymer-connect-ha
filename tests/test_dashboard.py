from __future__ import annotations

import asyncio
import importlib
import json
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest

import yaml

from tests.hymer_test_support import ensure_package_paths, install_homeassistant_stubs


class DashboardGenerationTests(unittest.TestCase):
    def setUp(self) -> None:
        ensure_package_paths()
        self.dashboard = importlib.import_module(
            "custom_components.hymer_connect_metadata.dashboard"
        )

    def test_describe_dashboard_entity_classifies_canonical_capability(self) -> None:
        described = self.dashboard.describe_dashboard_entity(
            "entry-1",
            entity_id="switch.main_switch",
            unique_id="entry-1_canonical_main_switch",
            name="12 V Switch",
        )

        self.assertIsNotNone(described)
        self.assertEqual(described.tab, "energy")
        self.assertEqual(described.section, "Controls")
        self.assertEqual(described.render_as, "tile")

    def test_describe_dashboard_entity_classifies_raw_slot_fallback(self) -> None:
        described = self.dashboard.describe_dashboard_entity(
            "entry-1",
            entity_id="sensor.odometer",
            unique_id="entry-1_b1_s1",
            name="Mileage",
        )

        self.assertIsNotNone(described)
        self.assertEqual(described.tab, "info")
        self.assertEqual(described.section, "Chassis Information")

    def test_describe_dashboard_entity_buckets_named_lights(self) -> None:
        described = self.dashboard.describe_dashboard_entity(
            "entry-1",
            entity_id="light.bedroom_overhead_cabinet",
            unique_id="entry-1_light_b12_bedroom_overhead_cabinet",
            name="Bedroom overhead cabinet",
        )

        self.assertIsNotNone(described)
        self.assertEqual(described.tab, "light")
        self.assertEqual(described.section, "Personal Lights")
        self.assertEqual(described.render_as, "light")

    def test_build_dashboard_config_creates_app_like_views(self) -> None:
        items = [
            self.dashboard.describe_dashboard_entity(
                "entry-1",
                entity_id="switch.main_switch",
                unique_id="entry-1_canonical_main_switch",
                name="12 V Switch",
            ),
            self.dashboard.describe_dashboard_entity(
                "entry-1",
                entity_id="switch.water_pump",
                unique_id="entry-1_canonical_water_pump",
                name="Water Pump",
            ),
            self.dashboard.describe_dashboard_entity(
                "entry-1",
                entity_id="sensor.battery_soc",
                unique_id="entry-1_canonical_battery_soc",
                name="Grand Canyon S 700 Living Battery State Of Charge",
            ),
            self.dashboard.describe_dashboard_entity(
                "entry-1",
                entity_id="sensor.living_battery_voltage",
                unique_id="entry-1_canonical_living_battery_voltage",
                name="Living Battery Voltage",
            ),
            self.dashboard.describe_dashboard_entity(
                "entry-1",
                entity_id="sensor.battery_state_of_health",
                unique_id="entry-1_canonical_battery_state_of_health",
                name="Living Battery State Of Health",
            ),
            self.dashboard.describe_dashboard_entity(
                "entry-1",
                entity_id="sensor.living_battery_current",
                unique_id="entry-1_canonical_living_battery_current",
                name="Living Battery Current",
            ),
            self.dashboard.describe_dashboard_entity(
                "entry-1",
                entity_id="sensor.solar_panel_power",
                unique_id="entry-1_canonical_solar_panel_power",
                name="Grand Canyon S 700 Solar Panel Power",
            ),
            self.dashboard.describe_dashboard_entity(
                "entry-1",
                entity_id="sensor.fresh_water_level",
                unique_id="entry-1_canonical_fresh_water_level",
                name="Fresh Water",
            ),
            self.dashboard.describe_dashboard_entity(
                "entry-1",
                entity_id="sensor.waste_water_level",
                unique_id="entry-1_canonical_waste_water_level",
                name="Grey Water",
            ),
            self.dashboard.describe_dashboard_entity(
                "entry-1",
                entity_id="sensor.odometer",
                unique_id="entry-1_b1_s1",
                name="Mileage",
            ),
            self.dashboard.describe_dashboard_entity(
                "entry-1",
                entity_id="sensor.fuel_level",
                unique_id="entry-1_b1_s2",
                name="Fuel Level",
            ),
            self.dashboard.describe_dashboard_entity(
                "entry-1",
                entity_id="device_tracker.test_van",
                unique_id="entry-1_device_tracker",
                name="Location",
            ),
            self.dashboard.describe_dashboard_entity(
                "entry-1",
                entity_id="climate.heater",
                unique_id="entry-1_heater_b6",
                name="Heater",
            ),
            self.dashboard.describe_dashboard_entity(
                "entry-1",
                entity_id="select.heater_energy_source",
                unique_id="entry-1_heater_energy_b6",
                name="Heater Energy Source",
            ),
            self.dashboard.describe_dashboard_entity(
                "entry-1",
                entity_id="select.warm_water_boiler",
                unique_id="entry-1_boiler_mode_b6",
                name="Warm Water Boiler",
            ),
            self.dashboard.describe_dashboard_entity(
                "entry-1",
                entity_id="light.communal_lights",
                unique_id="entry-1_light_b12",
                name="3 Communal Lights",
            ),
            self.dashboard.DashboardEntity(
                entity_id="light.communal_group",
                unique_id="entry-1_light_group_communal",
                name="3 Communal Lights",
                domain="light",
                tab="light",
                section="Communal Lights",
                order=5,
                render_as="light",
                bucket="communal",
                source_key="light_group",
            ),
            self.dashboard.describe_dashboard_entity(
                "entry-1",
                entity_id="light.kitchen_main",
                unique_id="entry-1_light_b12_kitchen_main",
                name="Kitchen Main",
            ),
            self.dashboard.describe_dashboard_entity(
                "entry-1",
                entity_id="light.outside_light",
                unique_id="entry-1_light_b12_outside_light",
                name="Outside Light",
            ),
            self.dashboard.describe_dashboard_entity(
                "entry-1",
                entity_id="switch.fridge_power",
                unique_id="entry-1_fridge_power_b34",
                name="Fridge",
            ),
            self.dashboard.describe_dashboard_entity(
                "entry-1",
                entity_id="select.fridge_level",
                unique_id="entry-1_fridge_level_b34",
                name="Fridge Temperature Level",
            ),
            self.dashboard.describe_dashboard_entity(
                "entry-1",
                entity_id="scene.arrival",
                unique_id="entry-1_scene_arrival",
                name="Arrival",
            ),
        ]

        config = self.dashboard.build_dashboard_config(
            "Grand Canyon S 700 Dashboard",
            [item for item in items if item is not None],
        )

        titles = [view["title"] for view in config["views"]]
        self.assertEqual(titles[0], "Dashboard")
        self.assertIn("Info", titles)
        self.assertIn("Water", titles)
        self.assertIn("Light", titles)
        self.assertIn("Energy", titles)
        self.assertIn("Climate", titles)
        self.assertIn("Components", titles)
        self.assertIn("Scenarios", titles)

        def walk_cards(cards):
            for card in cards:
                yield card
                yield from walk_cards(card.get("cards", []))

        dashboard_view = next(
            view for view in config["views"] if view["title"] == "Dashboard"
        )
        dashboard_cards = list(walk_cards(dashboard_view["cards"]))
        self.assertNotIn("panel", dashboard_view)
        root_cards = dashboard_view["cards"]
        self.assertEqual(len(root_cards), 3)
        self.assertEqual(
            [card.get("title") or card.get("type") for card in root_cards],
            ["Main Actions", "vertical-stack", "grid"],
        )
        action_entities = next(
            card["entities"]
            for card in dashboard_cards
            if card.get("type") == "entities"
            and card.get("title") == "Main Actions"
        )
        self.assertEqual(action_entities[0]["entity"], "switch.main_switch")
        self.assertNotIn(
            "Status",
            [card.get("title") for card in dashboard_cards],
        )
        self.assertEqual(root_cards[0].get("title"), "Main Actions")
        self.assertEqual(root_cards[1]["type"], "vertical-stack")
        self.assertIsNone(root_cards[2].get("title"))
        dashboard_maps = [
            card
            for card in dashboard_cards
            if card.get("type") == "map"
            and card.get("title") == "Location"
        ]
        self.assertEqual(dashboard_maps[0]["entities"], ["device_tracker.test_van"])
        middle_titles = [
            card.get("title")
            for card in walk_cards(root_cards[1]["cards"])
            if card.get("title")
        ]
        self.assertEqual(middle_titles, ["Summary", "Climate", "Location"])
        self.assertTrue(
            any(card["type"] == "gauge" for card in dashboard_cards)
        )
        dashboard_gauge_names = [
            card["name"]
            for card in dashboard_cards
            if card["type"] == "gauge"
        ]
        self.assertIn("Battery SOC", dashboard_gauge_names)
        self.assertNotIn(
            "Grand Canyon S 700 Living Battery State Of Charge",
            dashboard_gauge_names,
        )
        climate_cards = [
            card
            for card in dashboard_cards
            if card.get("entity") == "climate.heater"
        ]
        self.assertEqual(climate_cards[0]["type"], "tile")
        self.assertIn(
            {"type": "climate-hvac-modes"},
            climate_cards[0]["features"],
        )
        dashboard_climate_grid = next(
            card
            for card in dashboard_cards
            if card.get("type") == "grid"
            and card.get("title") == "Climate"
        )
        self.assertEqual(dashboard_climate_grid["columns"], 1)

        energy_view = next(view for view in config["views"] if view["title"] == "Energy")
        self.assertNotIn("panel", energy_view)
        self.assertTrue(
            all(
                card["type"] == "vertical-stack"
                for card in energy_view["cards"]
            )
        )
        energy_columns = energy_view["cards"]
        self.assertEqual(len(energy_columns), 3)
        self.assertEqual(
            [
                card.get("title") or card.get("type")
                for card in energy_columns[0]["cards"]
            ],
            ["Controls", "grid", "Electricity"],
        )
        self.assertEqual(
            [
                card.get("title") or card.get("type")
                for card in energy_columns[1]["cards"]
            ],
            ["Power Trends", "Battery Voltages"],
        )
        self.assertEqual(
            [
                card.get("title") or card.get("type")
                for card in energy_columns[2]["cards"]
            ],
            ["Solar Charging (24h)", "Solar Panel"],
        )
        energy_cards = list(walk_cards(energy_view["cards"]))
        energy_grids = [card for card in energy_cards if card["type"] == "grid"]
        self.assertNotIn(
            "Energy Overview",
            [card.get("title") for card in energy_cards],
        )
        self.assertTrue(
            any(
                card.get("title") == "Power Trends"
                and card.get("columns") == 1
                for card in energy_grids
            )
        )
        energy_card_types = [
            child["type"]
            for card in energy_grids
            for child in card["cards"]
        ]
        self.assertIn("sensor", energy_card_types)
        self.assertTrue(
            any(card["type"] == "history-graph" for card in energy_cards)
        )
        voltage_section = next(
            card
            for card in energy_cards
            if card.get("title") == "Battery Voltages"
        )
        voltage_graphs = [
            card
            for card in walk_cards(voltage_section["cards"])
            if card.get("type") == "sensor"
        ]
        self.assertEqual(
            [card["name"] for card in voltage_graphs],
            ["Leisure Battery"],
        )
        self.assertFalse(
            any(card.get("title") == "Battery Voltages (24h)" for card in energy_cards)
        )
        self.assertTrue(any(card["columns"] == 1 for card in energy_grids))
        self.assertTrue(
            all(
                "Grand Canyon S 700" not in str(card)
                for view in config["views"]
                for card in view["cards"]
            )
        )

        info_view = next(view for view in config["views"] if view["title"] == "Info")
        info_maps = [
            card
            for card in walk_cards(info_view["cards"])
            if card.get("type") == "map"
            and card.get("title") == "Location"
        ]
        self.assertEqual(info_maps[0]["entities"], ["device_tracker.test_van"])

        light_view = next(view for view in config["views"] if view["title"] == "Light")
        light_titles = [
            card["title"]
            for card in walk_cards(light_view["cards"])
            if card["type"] == "grid"
        ]
        self.assertNotIn("Group Control", light_titles)
        self.assertNotIn("Individual Lights", light_titles)
        light_section_titles = [
            card.get("title")
            for card in walk_cards(light_view["cards"])
        ]
        self.assertIn("Communal Lights", light_section_titles)
        self.assertIn("Outside Lights", light_section_titles)
        communal_entities = next(
            card["entities"]
            for card in walk_cards(light_view["cards"])
            if card.get("type") == "entities"
            and any(
                entity.get("entity") == "light.communal_group"
                for entity in card.get("entities", [])
            )
        )
        self.assertEqual(communal_entities[0]["name"], "All on/off")
        self.assertEqual(communal_entities[0]["icon"], "mdi:lightbulb-group")
        self.assertTrue(
            any(
                entity["entity"] == "light.kitchen_main"
                and entity["name"] == "Kitchen Main"
                and entity["icon"] == "mdi:lightbulb-outline"
                for entity in communal_entities
            )
        )
        light_tile_cards = [
            card
            for card in walk_cards(light_view["cards"])
            if card.get("type") == "tile"
            and str(card.get("entity", "")).startswith("light.")
        ]
        self.assertEqual(light_tile_cards, [])
        self.assertFalse(
            any(card["type"] == "markdown" for card in walk_cards(light_view["cards"]))
        )

        climate_view = next(view for view in config["views"] if view["title"] == "Climate")
        self.assertNotIn("panel", climate_view)
        self.assertEqual(
            [card.get("title") for card in climate_view["cards"]],
            ["Heater", "Warm Water"],
        )
        heater_section = next(
            card
            for card in walk_cards(climate_view["cards"])
            if card.get("title") == "Heater"
        )
        self.assertEqual(heater_section["type"], "vertical-stack")
        heater_grid = next(
            card
            for card in walk_cards(heater_section["cards"])
            if card.get("type") == "grid"
        )
        self.assertEqual(heater_grid["columns"], 1)

        scenarios_view = next(
            view for view in config["views"] if view["title"] == "Scenarios"
        )
        self.assertTrue(
            any(card["type"] == "grid" for card in walk_cards(scenarios_view["cards"]))
        )

    def test_write_dashboard_yaml_outputs_valid_yaml(self) -> None:
        config = self.dashboard.build_dashboard_config(
            "Test Van Dashboard",
            [
                self.dashboard.describe_dashboard_entity(
                    "entry-1",
                    entity_id="switch.main_switch",
                    unique_id="entry-1_canonical_main_switch",
                    name="12 V Switch",
                )
            ],
        )

        with TemporaryDirectory() as tmp_dir:
            path = Path(tmp_dir) / "dashboard.yaml"
            self.dashboard.write_dashboard_yaml(
                path,
                config,
            )
            rendered = path.read_text()

        self.assertIn("Generated by the HYMER Connect Metadata dashboard generator", rendered)
        self.assertEqual(yaml.safe_load(rendered), config)

    def test_write_dashboard_storage_persists_lovelace_storage_files(self) -> None:
        config = self.dashboard.build_dashboard_config(
            "Test Van Dashboard",
            [
                self.dashboard.describe_dashboard_entity(
                    "entry-1",
                    entity_id="switch.main_switch",
                    unique_id="entry-1_canonical_main_switch",
                    name="12 V Switch",
                )
            ],
        )

        with TemporaryDirectory() as tmp_dir:
            self.dashboard.write_dashboard_storage(
                Path(tmp_dir),
                storage_id="hymer_connect_metadata_entry_1",
                url_path="test-van",
                title="Test Van Dashboard",
                config=config,
            )

            dashboards = json.loads(
                (Path(tmp_dir) / ".storage" / "lovelace_dashboards").read_text()
            )
            dashboard_config = json.loads(
                (
                    Path(tmp_dir)
                    / ".storage"
                    / "lovelace.hymer_connect_metadata_entry_1"
                ).read_text()
            )

        self.assertEqual(
            dashboards["data"]["items"][0]["id"],
            "hymer_connect_metadata_entry_1",
        )
        self.assertEqual(dashboards["data"]["items"][0]["mode"], "storage")
        self.assertEqual(dashboard_config["data"]["config"], config)

    def test_register_yaml_dashboard_adds_lovelace_panel(self) -> None:
        install_homeassistant_stubs()
        init_mod = importlib.import_module(
            "custom_components.hymer_connect_metadata.__init__"
        )
        frontend = importlib.import_module("homeassistant.components.frontend")
        frontend.registered_panels.clear()
        lovelace = importlib.import_module("homeassistant.components.lovelace")

        hass = SimpleNamespace(
            data={
                lovelace.LOVELACE_DATA: SimpleNamespace(
                    dashboards={},
                    yaml_dashboards={},
                )
            }
        )

        asyncio.run(
            init_mod._async_register_yaml_dashboard(
                hass,
                url_path="grand-canyon-s-700",
                title="Grand Canyon S 700 Dashboard",
                filename="dashboards/hymer_connect_metadata/grand-canyon-s-700.yaml",
            )
        )

        self.assertIn("grand-canyon-s-700", hass.data[lovelace.LOVELACE_DATA].dashboards)
        self.assertEqual(
            hass.data[lovelace.LOVELACE_DATA].yaml_dashboards["grand-canyon-s-700"][
                "filename"
            ],
            "dashboards/hymer_connect_metadata/grand-canyon-s-700.yaml",
        )
        self.assertEqual(len(frontend.registered_panels), 1)
        self.assertEqual(
            frontend.registered_panels[0]["frontend_url_path"],
            "grand-canyon-s-700",
        )

    def test_distance_unit_registry_override_is_cleared(self) -> None:
        install_homeassistant_stubs()
        init_mod = importlib.import_module(
            "custom_components.hymer_connect_metadata.__init__"
        )

        self.assertEqual(
            init_mod._distance_unit_override_updates(
                SimpleNamespace(unit="km"),
                SimpleNamespace(unit_of_measurement="km"),
                SimpleNamespace(options={"use_miles": True}),
            ),
            {"unit_of_measurement": "mi"},
        )
        self.assertEqual(
            init_mod._distance_unit_override_updates(
                SimpleNamespace(unit="km"),
                SimpleNamespace(unit_of_measurement="mi"),
                SimpleNamespace(options={}),
            ),
            {"unit_of_measurement": None},
        )
        self.assertEqual(
            init_mod._distance_unit_override_updates(
                SimpleNamespace(unit="%"),
                SimpleNamespace(unit_of_measurement="%"),
                SimpleNamespace(options={"use_miles": True}),
            ),
            {},
        )


if __name__ == "__main__":
    unittest.main()
