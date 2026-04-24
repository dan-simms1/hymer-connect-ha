from __future__ import annotations

import importlib
import unittest
from pathlib import Path

from tests.hymer_test_support import ensure_package_paths


REFERENCE_LOG = Path("reference/pia_capture_2026-04-19.log")


@unittest.skipUnless(
    REFERENCE_LOG.exists(),
    "local reference capture not available",
)
class LocalCaptureReplayTests(unittest.TestCase):
    def test_s700_capture_resolves_water_from_component_3(self) -> None:
        ensure_package_paths()
        pia_decoder = importlib.import_module("custom_components.hymer_connect_metadata.pia_decoder")
        capability_resolver = importlib.import_module(
            "custom_components.hymer_connect_metadata.capability_resolver"
        )

        observed: dict[tuple[int, int], object] = {}
        for line in REFERENCE_LOG.read_text().splitlines():
            payload = line.strip()
            if not payload:
                continue
            observed.update(pia_decoder.decode_pia_slots(payload))

        self.assertIn((3, 8), observed)
        self.assertIn((3, 9), observed)
        self.assertIn((22, 2), observed)
        self.assertNotIn((2, 8), observed)

        resolved = {
            capability.spec.key: capability.slot
            for capability in capability_resolver.all_resolved_capabilities(set(observed))
        }
        self.assertEqual(resolved["fresh_water_level"], (3, 8))
        self.assertEqual(resolved["waste_water_level"], (3, 9))
        self.assertNotIn((22, 2), capability_resolver.canonical_claimed_slots(set(observed)))


if __name__ == "__main__":
    unittest.main()
