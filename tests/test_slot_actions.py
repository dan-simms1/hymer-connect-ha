from __future__ import annotations

import importlib
import unittest

from tests.hymer_test_support import ensure_package_paths


ensure_package_paths()

slot_actions = importlib.import_module("custom_components.hymer_connect_metadata.slot_actions")


class SlotActionTests(unittest.TestCase):
    def test_float_slot_actions_follow_slot_datatype(self) -> None:
        sensor = slot_actions.serialize_slot_action(
            {"component_id": 5, "sensor_id": 3, "value": 22}
        )
        self.assertEqual(
            sensor,
            {"bus_id": 5, "sensor_id": 3, "float_value": 22.0},
        )

    def test_read_only_slots_are_rejected(self) -> None:
        with self.assertRaises(slot_actions.SlotActionError):
            slot_actions.serialize_slot_action(
                {"component_id": 3, "sensor_id": 8, "value": 80}
            )

    def test_write_only_button_slot_stays_bool(self) -> None:
        sensor = slot_actions.serialize_slot_action(
            {"component_id": 107, "sensor_id": 1, "value": True}
        )
        self.assertEqual(
            sensor,
            {"bus_id": 107, "sensor_id": 1, "bool_value": True},
        )

    def test_out_of_range_numeric_actions_are_rejected(self) -> None:
        with self.assertRaises(slot_actions.SlotActionError):
            slot_actions.serialize_slot_action(
                {"component_id": 34, "sensor_id": 3, "value": 6}
            )

    def test_div3600_transform_round_trip(self) -> None:
        discovery = importlib.import_module("custom_components.hymer_connect_metadata.discovery")
        self.assertEqual(discovery.apply_transform(7200, "div3600"), 2.0)
        self.assertEqual(discovery.reverse_transform(2.0, "div3600"), 7200.0)


if __name__ == "__main__":
    unittest.main()
