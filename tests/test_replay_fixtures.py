from __future__ import annotations

import importlib
import json
import unittest
from pathlib import Path

from tests.hymer_test_support import ensure_package_paths, install_homeassistant_stubs


FIXTURE_PATH = Path("tests/fixtures/pia_replay_fixtures.json")


def _load_fixture(name: str) -> dict:
    payload = json.loads(FIXTURE_PATH.read_text())
    return payload["fixtures"][name]


def _observed_from_fixture(name: str) -> dict[tuple[int, int], object]:
    install_homeassistant_stubs()
    ensure_package_paths()
    pia_decoder = importlib.import_module("custom_components.hymer_connect_metadata.pia_decoder")

    observed: dict[tuple[int, int], object] = {}
    fixture = _load_fixture(name)
    for frame in fixture["frames"]:
        sensors: list[dict] = []
        for slot in frame["slots"]:
            sensor = {
                "bus_id": slot["component_id"],
                "sensor_id": slot["sensor_id"],
            }
            for key in ("bool_value", "uint_value", "str_value", "float_value"):
                if key in slot:
                    sensor[key] = slot[key]
            sensors.append(sensor)
        payload = pia_decoder.build_multi_sensor_command(sensors)
        observed.update(pia_decoder.decode_pia_slots(payload))
    return observed


class ReplayFixtureTests(unittest.TestCase):
    def test_committed_s700_fixture_resolves_water_and_scu(self) -> None:
        install_homeassistant_stubs()
        ensure_package_paths()
        capability_resolver = importlib.import_module(
            "custom_components.hymer_connect_metadata.capability_resolver"
        )

        observed = _observed_from_fixture("s700_water_snapshot")
        self.assertIn((3, 8), observed)
        self.assertIn((3, 9), observed)
        self.assertIn((22, 2), observed)
        self.assertIn((30, 5), observed)

        resolved = {
            capability.spec.key: capability.slot
            for capability in capability_resolver.all_resolved_capabilities(set(observed))
        }
        self.assertEqual(resolved["fresh_water_level"], (3, 8))
        self.assertEqual(resolved["waste_water_level"], (3, 9))
        self.assertEqual(resolved["scu_voltage"], (30, 5))
        self.assertEqual(resolved["lte_connection_state"], (30, 4))
        self.assertEqual(resolved["vehicle_movement"], (30, 14))
        self.assertEqual(resolved["battery_cutoff_switch"], (30, 8))
        self.assertNotIn(
            (22, 2), capability_resolver.canonical_claimed_slots(set(observed))
        )

    def test_synthetic_bms_fixture_resolves_new_bms_capabilities(self) -> None:
        install_homeassistant_stubs()
        ensure_package_paths()
        capability_resolver = importlib.import_module(
            "custom_components.hymer_connect_metadata.capability_resolver"
        )

        observed = _observed_from_fixture("synthetic_bms_snapshot")
        resolved = {
            capability.spec.key: capability.slot
            for capability in capability_resolver.all_resolved_capabilities(set(observed))
        }
        self.assertEqual(resolved["battery_relative_capacity"], (99, 8))
        self.assertEqual(resolved["available_capacity"], (105, 22))
        self.assertEqual(resolved["dischargeable_capacity"], (105, 23))
        self.assertEqual(resolved["nominal_capacity"], (105, 24))
        self.assertEqual(resolved["battery_low_voltage"], (105, 2))
        self.assertEqual(resolved["battery_defect"], (105, 6))


if __name__ == "__main__":
    unittest.main()
