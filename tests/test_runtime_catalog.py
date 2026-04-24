from __future__ import annotations

import importlib
import unittest
from dataclasses import replace

from tests.hymer_test_support import ensure_package_paths


ensure_package_paths()

catalog = importlib.import_module("custom_components.hymer_connect_metadata.catalog")
capability_resolver = importlib.import_module("custom_components.hymer_connect_metadata.capability_resolver")


class RuntimeCatalogTests(unittest.TestCase):
    def test_coverage_summary_matches_fixture_metadata(self) -> None:
        audit = catalog.coverage_audit()
        summary = audit["summary"]
        self.assertEqual(summary["component_count"], len(audit["components"]))
        self.assertEqual(summary["slot_count"], len(audit["slots"]))
        self.assertGreater(summary["writable_slot_count"], 0)
        self.assertEqual(
            summary["supported_writable_slot_count"] + summary["suppressed_writable_slot_count"],
            summary["writable_slot_count"],
        )
        self.assertEqual(
            summary["writable_coverage_basis"],
            "runtime_serialization_path",
        )
        self.assertEqual(
            summary["read_validation_basis"],
            "shipped_metadata_inference",
        )
        self.assertEqual(
            summary["read_validation_status"],
            "not_tracked_beyond_inference",
        )
        self.assertEqual(
            summary["write_validation_basis"],
            "runtime_serialization_path",
        )
        self.assertEqual(
            summary["write_validation_status"],
            "runtime_path_only",
        )
        self.assertEqual(summary["wire_validation_status"], "not_tracked")
        self.assertGreater(summary["scenario_count"], 0)
        self.assertGreater(summary["scene_count"], 0)

    def test_support_matrix_summary_matches_fixture_metadata(self) -> None:
        matrix = catalog.support_matrix()
        summary = matrix["summary"]
        self.assertEqual(
            summary["canonical_capability_count"],
            len(matrix["canonical_capabilities"]),
        )
        self.assertEqual(
            summary["rich_template_entry_count"],
            len(matrix["rich_templates"]),
        )
        self.assertEqual(
            summary["generic_component_kind_count"],
            len(matrix["generic_component_kinds"]),
        )
        self.assertEqual(
            summary["support_tier_counts"],
            {
                "canonical": len(matrix["canonical_capabilities"]),
                "generic_runtime": len(matrix["generic_component_kinds"]),
                "rich_template": len(matrix["rich_templates"]),
            },
        )
        self.assertIn("inferred", summary["read_validation_status_counts"])
        self.assertIn("runtime_path_only", summary["write_validation_status_counts"])

    def test_support_matrix_marks_switch_capabilities_as_runtime_path_only(self) -> None:
        matrix = catalog.support_matrix()
        main_switch = next(
            entry
            for entry in matrix["canonical_capabilities"]
            if entry["key"] == "main_switch"
        )
        self.assertEqual(main_switch["write_validation_status"], "runtime_path_only")
        self.assertEqual(main_switch["evidence_sources"], ["registry", "bundle"])

    def test_coverage_audit_uses_provider_specs_for_canonical_slots(self) -> None:
        slots = catalog.coverage_audit()["slots"]
        self.assertEqual(slots["121:1"]["support_class"], "canonical_generic")
        self.assertEqual(slots["121:9"]["support_class"], "canonical_generic")
        self.assertEqual(slots["121:3"]["support_class"], "canonical_generic")
        self.assertEqual(slots["121:11"]["support_class"], "canonical_generic")
        self.assertEqual(slots["56:12"]["support_class"], "canonical_generic")
        self.assertEqual(slots["56:15"]["support_class"], "canonical_generic")
        self.assertEqual(slots["99:8"]["support_class"], "canonical_generic")
        self.assertEqual(slots["105:24"]["support_class"], "canonical_generic")
        self.assertEqual(slots["30:4"]["support_class"], "canonical_generic")
        self.assertEqual(slots["30:5"]["support_class"], "canonical_generic")

    def test_resolved_scenarios_require_full_supported_action_set(self) -> None:
        entry = next(
            candidate
            for candidate in catalog.scenario_catalog()
            if candidate["kind"] == "scenario" and candidate["key"] == "GOOD_MORNING"
        )
        all_slots = {
            (action["component_id"], action["sensor_id"])
            for action in entry["actions"]
        }
        partial_slots = {next(iter(all_slots))}

        self.assertEqual(catalog.resolved_scenarios(partial_slots), [])

        resolved = catalog.resolved_scenarios(all_slots)
        self.assertTrue(any(item["key"] == "GOOD_MORNING" for item in resolved))

    def test_canonical_claims_only_the_selected_provider_slot(self) -> None:
        observed = {(3, 8), (2, 8)}
        claimed = capability_resolver.canonical_claimed_slots(observed)
        self.assertEqual(claimed, {(3, 8), (2, 8)})

    def test_provider_specs_do_not_reuse_candidate_slots_across_capabilities(self) -> None:
        seen: dict[tuple[int, int], str] = {}
        for spec in capability_resolver.all_capability_specs():
            for candidate in spec.candidates:
                self.assertNotIn(candidate.key, seen)
                seen[candidate.key] = spec.key

    def test_capability_collision_validator_rejects_duplicate_slots(self) -> None:
        spec = capability_resolver.capability_spec("available_capacity")
        self.assertIsNotNone(spec)
        duplicate = replace(spec, key="duplicate_available_capacity")
        with self.assertRaises(ValueError):
            capability_resolver._validate_unique_candidate_slots((spec, duplicate))

    def test_main_switch_slots_are_loaded_from_provider_metadata(self) -> None:
        self.assertEqual(
            capability_resolver.main_switch_slots(),
            frozenset({(3, 1), (2, 1), (110, 1), (122, 1)}),
        )
        spec = capability_resolver.capability_spec("main_switch")
        self.assertIsNotNone(spec)
        self.assertEqual(spec.platform, "switch")
        self.assertEqual(spec.write_validation_status, "runtime_path_only")
        self.assertEqual(spec.read_validation_status, "inferred")
        self.assertEqual(spec.evidence_sources, ("registry", "bundle"))

    def test_inverter_frequency_capability_exposes_frequency_device_class(self) -> None:
        spec = capability_resolver.capability_spec("charger_input_frequency")
        self.assertIsNotNone(spec)
        self.assertEqual(spec.sensor_device_class, "frequency")
        self.assertEqual(
            capability_resolver.candidate_slots_for_key("charger_input_frequency"),
            ((121, 17),),
        )

    def test_toilet_bluetooth_capability_is_marked_as_connectivity(self) -> None:
        spec = capability_resolver.capability_spec(
            "external_bluetooth_device_connected"
        )
        self.assertIsNotNone(spec)
        self.assertEqual(spec.platform, "binary_sensor")
        self.assertEqual(spec.binary_device_class, "connectivity")
        self.assertEqual(
            capability_resolver.candidate_slots_for_key(
                "external_bluetooth_device_connected"
            ),
            ((56, 15),),
        )

    def test_bms_capacity_and_fault_capabilities_are_loaded_from_provider_metadata(
        self,
    ) -> None:
        available = capability_resolver.capability_spec("available_capacity")
        self.assertIsNotNone(available)
        self.assertEqual(available.platform, "sensor")
        self.assertEqual(
            capability_resolver.candidate_slots_for_key("available_capacity"),
            ((105, 22),),
        )

        remaining = capability_resolver.capability_spec("battery_capacity_remaining")
        self.assertIsNotNone(remaining)
        self.assertEqual(
            capability_resolver.candidate_slots_for_key("battery_capacity_remaining"),
            ((99, 7),),
        )

        defect = capability_resolver.capability_spec("battery_defect")
        self.assertIsNotNone(defect)
        self.assertEqual(defect.platform, "binary_sensor")
        self.assertEqual(defect.binary_device_class, "problem")
        self.assertEqual(
            capability_resolver.candidate_slots_for_key("battery_defect"),
            ((105, 6),),
        )
        self.assertEqual(
            capability_resolver.candidate_slots_for_key("battery_low_warning"),
            ((96, 3),),
        )

    def test_observed_slot_support_profile_marks_audit_missing_slots(self) -> None:
        profile = catalog.observed_slot_support_profile({(999, 1)})
        self.assertEqual(profile["audit_missing_slots"], ["999:1"])
        self.assertEqual(profile["support_class_counts"]["audit_missing"], 1)
        self.assertEqual(profile["read_validation_status_counts"]["audit_missing"], 1)
        self.assertEqual(profile["write_validation_status_counts"]["audit_missing"], 1)


if __name__ == "__main__":
    unittest.main()
