from __future__ import annotations

import asyncio
import importlib
import unittest
from types import SimpleNamespace

from tests.hymer_test_support import ensure_package_paths, install_homeassistant_stubs


ensure_package_paths()
install_homeassistant_stubs()

const = importlib.import_module("custom_components.hymer_connect_metadata.const")
template_specs = importlib.import_module("custom_components.hymer_connect_metadata.template_specs")
light = importlib.import_module("custom_components.hymer_connect_metadata.templates.light")
fan = importlib.import_module("custom_components.hymer_connect_metadata.templates.fan")
awning = importlib.import_module("custom_components.hymer_connect_metadata.templates.awning")
boiler = importlib.import_module("custom_components.hymer_connect_metadata.templates.boiler")
fridge = importlib.import_module("custom_components.hymer_connect_metadata.templates.fridge")
heater_energy = importlib.import_module("custom_components.hymer_connect_metadata.templates.heater_energy")
air_conditioner = importlib.import_module("custom_components.hymer_connect_metadata.templates.air_conditioner")
climate = importlib.import_module("custom_components.hymer_connect_metadata.templates.climate")


class _DummyCoordinator:
    def __init__(self) -> None:
        self.data = {"signalr_slots": {}}
        self.habitation_power_available = True
        self.client = None

    def is_habitation_power_available(self) -> bool:
        return self.habitation_power_available

    async def async_ensure_signalr_connected(self):
        return self.client


class TemplateSpecsTests(unittest.TestCase):
    def setUp(self) -> None:
        self.coordinator = _DummyCoordinator()
        self.entry = SimpleNamespace(entry_id="entry-1")

    def test_template_specs_load_expected_high_volume_templates(self) -> None:
        simple = template_specs.simple_light_spec()
        self.assertEqual(simple.fallback_name, "Light")
        self.assertEqual([item.sensor_id for item in simple.required_slots], [1, 2])

        fan_specs = template_specs.fan_entity_specs()
        self.assertEqual(len(fan_specs), 5)
        self.assertEqual(fan_specs[0].kind, "airxcel_roof_fan")
        self.assertEqual(fan_specs[-1].name, "Bathroom Fan")

        awning_spec = template_specs.awning_cover_spec()
        self.assertEqual(awning_spec.component_kinds, ("awning",))
        self.assertEqual(awning_spec.position_slot, 7)

        fridge_power_spec = template_specs.fridge_power_spec()
        self.assertEqual(fridge_power_spec.component_kinds, ("fridge",))
        self.assertEqual(fridge_power_spec.power_slot, 1)

        fridge_level_spec = template_specs.fridge_level_spec()
        self.assertEqual(fridge_level_spec.component_kinds, ("fridge",))
        self.assertEqual(fridge_level_spec.level_slot, 3)

        boiler_spec = template_specs.boiler_mode_spec()
        self.assertEqual(boiler_spec.options, ("Off", "ECO", "Turbo"))
        self.assertEqual(boiler_spec.wire_map["Turbo"], "HOT")

        heater_spec = template_specs.heater_energy_spec()
        self.assertEqual(heater_spec.options[0], "Diesel")
        self.assertEqual(heater_spec.writes["Mix 1800W"][-1].value, 1800)

        aircon_spec = template_specs.air_conditioner_single_zone_spec()
        self.assertEqual(aircon_spec.target_sid, 1)
        self.assertEqual(aircon_spec.name, "Air Conditioner")

        airxcel_specs = template_specs.airxcel_zone_specs()
        self.assertEqual(len(airxcel_specs), 2)
        self.assertEqual(airxcel_specs[0].zone, "front")

        truma_spec = template_specs.truma_panel_heater_spec()
        self.assertEqual(truma_spec.setpoint_sid, 8)

        modern_specs = template_specs.modern_heater_specs()
        self.assertEqual({spec.variant for spec in modern_specs}, {"heater_neo", "heater_zone"})

    def test_light_template_builds_simple_light_from_metadata(self) -> None:
        entities, claimed = light.LightTemplate().build(
            self.coordinator,
            self.entry,
            {(12, 1), (12, 2), (12, 3)},
        )
        self.assertEqual(len(entities), 1)
        self.assertEqual(claimed, {(12, 1), (12, 2), (12, 3)})

    def test_light_entities_become_unavailable_when_12v_is_off(self) -> None:
        entities, _ = light.LightTemplate().build(
            self.coordinator,
            self.entry,
            {(12, 1), (12, 2), (12, 3)},
        )
        self.coordinator.habitation_power_available = False
        self.assertFalse(entities[0].available)

    def test_fan_template_builds_airxcel_family_from_metadata(self) -> None:
        entities, claimed = fan.FanTemplate().build(
            self.coordinator,
            self.entry,
            {(95, 6), (95, 8), (95, 9)},
        )
        self.assertEqual(len(entities), 1)
        self.assertEqual(claimed, {(95, 6), (95, 8), (95, 9)})

    def test_metadata_rich_template_claims_match_airxcel_fan_runtime_shape(self) -> None:
        claims = template_specs.rich_template_claims(
            component_id=95,
            component_kind="air_conditioner",
            slots_for_component={
                6: {"label": "air_con_front", "datatype": "bool", "mode": "rw"},
                8: {"label": "fan_mode_front", "datatype": "string", "mode": "rw"},
                9: {"label": "fan_speed_front", "datatype": "string", "mode": "rw"},
            },
        )
        self.assertEqual(claims, {6: "fan", 8: "fan", 9: "fan"})

    def test_awning_template_builds_cover_from_metadata(self) -> None:
        entities, claimed = awning.AwningCoverTemplate().build(
            self.coordinator,
            self.entry,
            {(107, 1), (107, 2), (107, 7)},
        )
        self.assertEqual(len(entities), 1)
        self.assertEqual(claimed, {(107, 1), (107, 2), (107, 7)})

    def test_boiler_template_builds_select_from_metadata(self) -> None:
        entities, claimed = boiler.BoilerSelectTemplate().build(
            self.coordinator,
            self.entry,
            {(6, 5)},
        )
        self.assertEqual(len(entities), 1)
        self.assertEqual(claimed, {(6, 5)})
        self.assertEqual(entities[0]._attr_name, "Warm Water Boiler")

    def test_heater_energy_template_builds_select_from_metadata(self) -> None:
        entities, claimed = heater_energy.HeaterEnergyTemplate().build(
            self.coordinator,
            self.entry,
            {(6, 4), (6, 6)},
        )
        self.assertEqual(len(entities), 1)
        self.assertEqual(claimed, {(6, 4), (6, 6)})

    def test_fridge_templates_build_separate_power_and_level_entities(self) -> None:
        power_entities, power_claimed = fridge.FridgePowerTemplate().build(
            self.coordinator,
            self.entry,
            {(34, 2)},
        )
        level_entities, level_claimed = fridge.FridgeLevelTemplate().build(
            self.coordinator,
            self.entry,
            {(34, 2)},
        )

        self.assertEqual(len(power_entities), 1)
        self.assertEqual(power_claimed, {(34, 1)})
        self.assertEqual(power_entities[0]._attr_name, "Fridge")

        self.assertEqual(len(level_entities), 1)
        self.assertEqual(level_claimed, {(34, 3)})
        self.assertEqual(level_entities[0]._attr_name, "Fridge Temperature Level")

    def test_fridge_level_uses_direct_numeric_wire_values(self) -> None:
        class _DummyClient:
            def __init__(self) -> None:
                self.calls: list[tuple[int, int, dict[str, object]]] = []

            async def send_light_command(self, bus_id, sensor_id, **kwargs):
                self.calls.append((bus_id, sensor_id, kwargs))

        self.coordinator.client = _DummyClient()
        entities, _ = fridge.FridgeLevelTemplate().build(
            self.coordinator,
            self.entry,
            {(34, 2)},
        )
        self.assertEqual(len(entities), 1)

        entity = entities[0]
        self.coordinator.data["signalr_slots"] = {(34, 3): 1}
        self.assertEqual(entity.current_option, "1")

        asyncio.run(entity.async_select_option("1"))
        self.assertEqual(
            self.coordinator.client.calls[-1],
            (34, 3, {"uint_value": 1}),
        )

    def test_air_conditioner_template_builds_single_zone_from_metadata(self) -> None:
        entities, claimed = air_conditioner.AirConditionerClimateTemplate().build(
            self.coordinator,
            self.entry,
            {(7, 1), (7, 2), (7, 3), (7, 4)},
        )
        self.assertEqual(len(entities), 1)
        self.assertEqual(claimed, {(7, 1), (7, 2), (7, 3), (7, 4)})

    def test_air_conditioner_template_builds_airxcel_zone_from_metadata(self) -> None:
        entities, claimed = air_conditioner.AirConditionerClimateTemplate().build(
            self.coordinator,
            self.entry,
            {(95, 1), (95, 4), (95, 5)},
        )
        self.assertEqual(len(entities), 1)
        self.assertEqual(claimed, {(95, 1), (95, 4), (95, 5)})

    def test_truma_climate_template_builds_from_metadata(self) -> None:
        entities, claimed = climate.TrumaClimateTemplate().build(
            self.coordinator,
            self.entry,
            {(6, 8)},
        )
        self.assertEqual(len(entities), 1)
        self.assertEqual(claimed, {(6, 8)})
        self.assertEqual(entities[0]._attr_name, "Heater")

    def test_modern_heater_template_builds_from_metadata(self) -> None:
        entities, claimed = climate.ModernHeaterClimateTemplate().build(
            self.coordinator,
            self.entry,
            {(119, 10), (119, 11), (119, 13)},
        )
        self.assertEqual(len(entities), 1)
        self.assertEqual(claimed, {(119, 10), (119, 11), (119, 13)})

    def test_template_entities_attach_to_root_vehicle_device(self) -> None:
        entities, _ = light.LightTemplate().build(
            self.coordinator,
            self.entry,
            {(12, 1), (12, 2), (12, 3)},
        )

        self.assertEqual(
            entities[0]._attr_device_info["identifiers"],
            {(const.DOMAIN, self.entry.entry_id)},
        )

    def test_modern_heater_filters_absolute_zero_sentinel_temperatures(self) -> None:
        entities, _ = climate.ModernHeaterClimateTemplate().build(
            self.coordinator,
            self.entry,
            {(119, 10), (119, 11), (119, 13)},
        )
        self.coordinator.data["signalr_slots"] = {
            (119, 10): -273.0,
            (119, 11): -273.0,
            (119, 13): "OFF",
        }

        entity = entities[0]
        self.assertIsNone(entity.target_temperature)
        self.assertIsNone(entity.current_temperature)

    def test_air_conditioner_filters_absolute_zero_sentinel_temperatures(self) -> None:
        entities, _ = air_conditioner.AirConditionerClimateTemplate().build(
            self.coordinator,
            self.entry,
            {(7, 1), (7, 2), (7, 3), (7, 4)},
        )
        self.coordinator.data["signalr_slots"] = {
            (7, 1): -273.0,
            (7, 2): -273.0,
            (7, 3): "OFF",
            (7, 4): "AUTO",
        }

        entity = entities[0]
        self.assertIsNone(entity.target_temperature)
        self.assertIsNone(entity.current_temperature)

    def test_airxcel_zone_filters_absolute_zero_sentinel_temperatures(self) -> None:
        entities, _ = air_conditioner.AirConditionerClimateTemplate().build(
            self.coordinator,
            self.entry,
            {(95, 1), (95, 4), (95, 5)},
        )
        self.coordinator.data["signalr_slots"] = {
            (95, 1): "OFF",
            (95, 4): -273.0,
            (95, 5): -273.0,
        }

        entity = entities[0]
        self.assertIsNone(entity.target_temperature)
        self.assertIsNone(entity.target_temperature_low)
        self.assertIsNone(entity.target_temperature_high)


if __name__ == "__main__":
    unittest.main()
