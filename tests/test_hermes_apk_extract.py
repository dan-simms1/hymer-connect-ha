"""Regression tests for the pure-Python Hermes APK extractor.

These run only when a real HYMER APK is present at ``reference/base.apk`` (which
is gitignored and never shipped), so they are skipped in CI. They guard that the
mini-decompiler still reconstructs the catalog source objects -- including their
NESTED data -- and that the OAuth client comes out well-formed, without asserting
any credential value.
"""

from __future__ import annotations

import base64
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
APK = ROOT / "reference" / "base.apk"
SCRIPTS = ROOT / "scripts"


@unittest.skipUnless(APK.exists(), "reference/base.apk not present (gitignored)")
class HermesApkExtractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        if str(SCRIPTS) not in sys.path:
            sys.path.insert(0, str(SCRIPTS))

    def test_reconstructs_catalog_sources_with_nested_data(self) -> None:
        import hermes_apk_extract as H

        objects = H.reconstruct_object_literals_from_path(str(APK))

        def sig(keys):
            return [
                o for o in objects
                if isinstance(o, dict) and set(keys) <= set(o)
            ]

        slots = sig(("componentId", "id", "name", "datatype"))
        components = [
            o for o in sig(("id", "name", "capabilities", "settings"))
            if isinstance(o.get("id"), int)
        ]
        scenarios = [
            o for o in objects
            if isinstance(o, dict)
            and isinstance(o.get("name"), str)
            and o["name"].startswith(("SCENARIOS.", "SCENES."))
        ]
        vehicles = sig(("key", "modelName"))

        self.assertGreater(len(slots), 500)
        self.assertGreater(len(components), 100)
        self.assertGreaterEqual(len(scenarios), 1)
        self.assertGreaterEqual(len(vehicles), 10)
        # The nested structures are what a decompiler-free string scrape misses.
        self.assertTrue(
            any(isinstance(s.get("range"), dict) for s in slots),
            "expected some slots to carry a nested range{}",
        )
        self.assertTrue(
            any(isinstance(s.get("stringRange"), list) for s in slots),
            "expected some slots to carry nested enum options",
        )
        self.assertTrue(
            any(isinstance(s.get("components"), list) and s["components"] for s in scenarios),
            "expected some scenarios to carry a nested components[] action list",
        )

    def test_full_catalog_pack_generates_from_apk(self) -> None:
        import generate_cleanroom_registry as G
        import hermes_apk_extract as H

        objects = H.reconstruct_object_literals_from_path(str(APK))
        components, slots, _controls, vehicles, scenarios, _cov, _sup = (
            G.generate_overlay_from_bundle(
                Path("/nonexistent/bundle.js"),
                G.DEFAULT_PIA_DECODER,
                G.DEFAULT_PROVIDER_SPECS,
                G.DEFAULT_TEMPLATE_SPECS,
                objects=objects,
            )
        )
        self.assertGreater(len(components["components"]), 100)
        self.assertGreater(len(slots["slots"]), 500)
        self.assertGreaterEqual(len(vehicles["models"]), 10)
        self.assertGreaterEqual(len(scenarios["entries"]), 1)

    def test_oauth_client_is_well_formed(self) -> None:
        import extract_oauth_from_apk as ex

        payload = ex.extract_oauth_client_from_path(str(APK))
        header = payload["authorization_header"]
        self.assertTrue(header.startswith("Basic "))
        # Decodes to a non-empty ``username:password`` pair (values not asserted).
        decoded = base64.b64decode(header[6:]).decode()
        user, sep, secret = decoded.partition(":")
        self.assertEqual(sep, ":")
        self.assertTrue(user)
        self.assertTrue(secret)


if __name__ == "__main__":
    unittest.main()
