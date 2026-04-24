from __future__ import annotations

import importlib
import unittest
from types import SimpleNamespace

from tests.hymer_test_support import ensure_package_paths, install_homeassistant_stubs


ensure_package_paths()
install_homeassistant_stubs()

device_tracker = importlib.import_module("custom_components.hymer_connect_metadata.device_tracker")


class _DummyCoordinator:
    def __init__(self) -> None:
        self.data = {
            "signalr_slots": {
                (30, 1): "51.5074,-0.1278",
                (30, 3): "GOOD",
            }
        }


class DeviceTrackerTests(unittest.TestCase):
    def test_tracker_reads_coordinates_from_slot_metadata(self) -> None:
        coordinator = _DummyCoordinator()
        entry = SimpleNamespace(entry_id="entry-1", title="Vehicle")
        entity = device_tracker.HymerDeviceTracker(coordinator, entry)

        self.assertEqual(entity.latitude, 51.5074)
        self.assertEqual(entity.longitude, -0.1278)
        self.assertEqual(entity.extra_state_attributes["signal_quality"], "GOOD")
        self.assertIsNone(entity._attr_entity_category)


if __name__ == "__main__":
    unittest.main()
