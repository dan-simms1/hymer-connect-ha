"""Tests for in-integration APK metadata provisioning (apk_provision)."""

from __future__ import annotations

import asyncio
import importlib
import json
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from tests.hymer_test_support import ensure_package_paths, install_homeassistant_stubs

ROOT = Path(__file__).resolve().parents[1]
APK = ROOT / "reference" / "base.apk"


class ApkProvisionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        install_homeassistant_stubs()
        ensure_package_paths()
        cls.prov = importlib.import_module(
            "custom_components.hymer_connect_metadata.apk_provision"
        )

    def test_download_rejects_non_http_url(self) -> None:
        # The scheme check runs before any network use, so hass is unused here.
        for bad in ("", "ftp://example.com/app.apk", "file:///etc/passwd"):
            with self.assertRaises(self.prov.ApkProvisionError):
                asyncio.run(self.prov._async_download_apk(None, bad))

    @unittest.skipUnless(APK.exists(), "reference/base.apk not present (gitignored)")
    def test_build_and_write_produces_full_pack(self) -> None:
        apk_bytes = APK.read_bytes()
        with TemporaryDirectory() as tmp:
            data_dir = Path(tmp)
            written = self.prov._build_and_write(apk_bytes, data_dir)

            self.assertEqual(len(written), 8)
            for name in written:
                self.assertTrue((data_dir / name).exists(), name)

            components = json.loads((data_dir / "component_kinds.json").read_text())
            slots = json.loads((data_dir / "sensor_labels.json").read_text())
            vehicles = json.loads((data_dir / "vehicle_catalog.json").read_text())
            oauth = json.loads((data_dir / "oauth_client.json").read_text())

            self.assertGreater(len(components["components"]), 100)
            self.assertGreater(len(slots["slots"]), 500)
            self.assertGreaterEqual(len(vehicles["models"]), 10)
            self.assertTrue(oauth["authorization_header"].startswith("Basic "))


if __name__ == "__main__":
    unittest.main()
