from __future__ import annotations

import asyncio
import importlib
import sys
import time
import unittest

from tests.hymer_test_support import ensure_package_paths, install_homeassistant_stubs


class DiagnosticsTests(unittest.TestCase):
    def test_diagnostics_reports_audit_missing_and_canonical_resolution(self) -> None:
        install_homeassistant_stubs()
        sys.modules.pop("custom_components.hymer_connect_metadata.diagnostics", None)
        ensure_package_paths()
        diagnostics = importlib.import_module("custom_components.hymer_connect_metadata.diagnostics")
        const = importlib.import_module("custom_components.hymer_connect_metadata.const")

        coordinator = type(
            "Coordinator",
            (),
            {
                "observed_slots": {(3, 8), (999, 1), (2, 8)},
                "active_slots": {(3, 8), (999, 1)},
                "stale_slots": {(2, 8)},
                "slot_last_seen": {(2, 8): time.monotonic() - 3600},
                "slot_data": {(3, 8): 42, (999, 1): "mystery"},
                "signalr_client": None,
                "active_slot_window_seconds": 1800,
                "platform_discovery_profile": {
                    "sensor": {
                        "entity_count": 2,
                        "generic_entity_count": 1,
                        "template_summaries": [
                            {"template": "CanonicalSensorTemplate", "entity_count": 1, "claimed_slot_count": 1}
                        ],
                    }
                },
            },
        )()
        entry = type(
            "Entry",
            (),
            {
                "entry_id": "entry-1",
                "title": "Test Van",
                "data": {
                    "brand": "hymer",
                    "vehicle_model": "Grand Canyon S 700",
                    "vin": "VIN123",
                    "vehicle_urn": "urn:ehg:vehicle:test",
                    "scu_urn": "urn:ehg:scu:test",
                    "vehicle_id": 1234,
                },
            },
        )()
        hass = type(
            "Hass",
            (),
            {"data": {const.DOMAIN: {entry.entry_id: coordinator}}},
        )()

        def redact(data, keys):
            if not isinstance(data, dict):
                return data
            payload = dict(data)
            for key in keys:
                if key in payload:
                    payload[key] = "[redacted]"
            return payload

        diagnostics.async_redact_data = redact

        coordinator.slot_data[(30, 5)] = "53.6513049,-1.325278233"
        coordinator.active_slots.add((30, 5))
        coordinator.observed_slots.add((30, 5))
        coordinator.slot_last_seen[(30, 5)] = time.monotonic()

        payload = asyncio.run(
            diagnostics.async_get_config_entry_diagnostics(hass, entry)
        )

        self.assertEqual(payload["entry"]["vin"], "[redacted]")
        self.assertEqual(payload["entry"]["vehicle_urn"], "[redacted]")
        self.assertEqual(payload["entry"]["scu_urn"], "[redacted]")
        self.assertEqual(payload["entry"]["vehicle_id"], "[redacted]")

        self.assertGreater(payload["coverage_audit_summary"]["component_count"], 0)
        self.assertEqual(
            payload["coverage_audit_summary"]["read_validation_status"],
            "not_tracked_beyond_inference",
        )
        self.assertEqual(
            payload["coverage_audit_summary"]["write_validation_status"],
            "runtime_path_only",
        )
        self.assertEqual(
            payload["support_matrix_summary"]["canonical_capability_count"],
            3,
        )
        self.assertEqual(payload["historical_observed_slot_count"], 4)
        self.assertEqual(payload["active_slot_count"], 3)
        self.assertEqual(payload["stale_slot_count"], 1)
        self.assertEqual(
            payload["active_slot_definition"],
            "recently updated by the active SignalR DataHub session",
        )
        self.assertEqual(payload["active_slot_window_seconds"], 1800)
        self.assertEqual(
            payload["support_matrix_summary"]["rich_template_entry_count"],
            8,
        )
        self.assertEqual(
            payload["audit_missing_slots"][0]["slot"],
            [999, 1],
        )
        self.assertEqual(
            payload["audit_missing_slots"][0]["read_validation_status"],
            None,
        )
        self.assertEqual(
            payload["observed_slot_support_profile"]["audit_missing_slots"],
            ["999:1"],
        )
        self.assertEqual(
            payload["historical_observed_slot_support_profile"]["support_class_counts"]["canonical_generic"],
            3,
        )
        self.assertEqual(payload["stale_slots"][0]["slot"], [2, 8])
        redacted_gps = diagnostics._slot_snapshot(
            (30, 1),
            "53.6513049,-1.325278233",
        )
        self.assertIn(redacted_gps["label"], {"gps_coordinates", "gps_location"})
        self.assertEqual(redacted_gps["value"], "[redacted]")
        self.assertEqual(
            payload["observed_slot_support_profile"]["read_validation_status_counts"]["audit_missing"],
            1,
        )
        self.assertEqual(
            payload["platform_discovery_profile"]["sensor"]["entity_count"],
            2,
        )
        resolved = payload["canonical_resolved_capabilities"]
        fresh = next(item for item in resolved if item["key"] == "fresh_water_level")
        self.assertEqual(fresh["capability_read_validation_status"], "inferred")
        self.assertEqual(fresh["capability_write_validation_status"], "not_applicable")
        self.assertEqual(fresh["capability_evidence_sources"], ["registry", "bundle"])
        self.assertEqual(fresh["provider_read_validation_status"], "inferred")
        self.assertEqual(fresh["provider_write_validation_status"], "not_applicable")


if __name__ == "__main__":
    unittest.main()
