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

    def test_download_rejects_non_https_url(self) -> None:
        # The scheme check runs before any network use, so hass is unused here.
        # Plain http is refused too: the APK is the trust root for the pack.
        for bad in (
            "",
            "http://example.com/app.apk",
            "ftp://example.com/app.apk",
            "file:///etc/passwd",
        ):
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

    def test_validate_pack_rejects_incomplete(self) -> None:
        with self.assertRaises(self.prov.ApkProvisionError):
            self.prov._validate_pack({})
        with self.assertRaises(self.prov.ApkProvisionError):
            self.prov._validate_pack(
                {
                    "component_kinds.json": {"x": 1},
                    "sensor_labels.json": {"x": 1},
                    "vehicle_catalog.json": {"models": {}},  # empty -> rejected
                    "oauth_client.json": {"authorization_header": "Basic zzz"},
                }
            )

    def test_atomic_publish_rolls_back_on_failure(self) -> None:
        prov = self.prov
        with TemporaryDirectory() as tmp:
            data_dir = Path(tmp)
            for name in ("a.json", "b.json", "c.json"):
                (data_dir / name).write_text("OLD")

            real_replace = prov.os.replace
            calls = {"n": 0}

            def flaky(src, dst):
                calls["n"] += 1
                if calls["n"] == 2:  # fail mid-swap
                    raise OSError("disk full")
                return real_replace(src, dst)

            prov.os.replace = flaky
            try:
                with self.assertRaises(prov.ApkProvisionError):
                    prov._atomic_publish(
                        data_dir,
                        {"a.json": "NEW", "b.json": "NEW", "c.json": "NEW"},
                    )
            finally:
                prov.os.replace = real_replace

            # Every original is intact (the one already swapped was rolled back)...
            for name in ("a.json", "b.json", "c.json"):
                self.assertEqual((data_dir / name).read_text(), "OLD", name)
            # ...and no staging temp files are left behind.
            self.assertEqual(list(data_dir.glob(".*.new")), [])

    def test_atomic_publish_fresh_install_rollback_removes_new_files(self) -> None:
        # No prior files exist: a mid-swap failure must leave the directory
        # clean (already-swapped new files removed), not a partial pack.
        prov = self.prov
        with TemporaryDirectory() as tmp:
            data_dir = Path(tmp)
            real_replace = prov.os.replace
            calls = {"n": 0}

            def flaky(src, dst):
                calls["n"] += 1
                if calls["n"] == 2:
                    raise OSError("disk full")
                return real_replace(src, dst)

            prov.os.replace = flaky
            try:
                with self.assertRaises(prov.ApkProvisionError):
                    prov._atomic_publish(
                        data_dir,
                        {"a.json": "NEW", "b.json": "NEW", "c.json": "NEW"},
                    )
            finally:
                prov.os.replace = real_replace

            self.assertEqual(sorted(p.name for p in data_dir.iterdir()), [])


if __name__ == "__main__":
    unittest.main()
