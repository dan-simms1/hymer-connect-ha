"""Tests for in-flow metadata provisioning during first-time setup."""

from __future__ import annotations

import asyncio
import importlib
import unittest
from unittest import mock

from tests.hymer_test_support import ensure_package_paths, install_homeassistant_stubs


class ProvisionBootstrapTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        install_homeassistant_stubs()
        ensure_package_paths()
        try:
            cls.cf = importlib.import_module(
                "custom_components.hymer_connect_metadata.config_flow"
            )
            cls.const = importlib.import_module(
                "custom_components.hymer_connect_metadata.const"
            )
            cls.prov = importlib.import_module(
                "custom_components.hymer_connect_metadata.apk_provision"
            )
        except Exception as err:  # noqa: BLE001 - deps (voluptuous/HA) not in this env
            raise unittest.SkipTest(f"config_flow deps unavailable: {err}")

    def _flow(self):
        flow = self.cf.HymerConnectConfigFlow()
        flow.hass = None
        flow.async_show_form = lambda **kw: {"type": "form", **kw}
        return flow

    def test_missing_metadata_routes_to_provision_step(self) -> None:
        flow = self._flow()

        async def _absent() -> bool:
            return False

        flow._async_metadata_present = _absent
        result = asyncio.run(flow.async_step_user(None))
        self.assertEqual(result["step_id"], "provision")

    def test_present_metadata_shows_login(self) -> None:
        flow = self._flow()

        async def _present() -> bool:
            return True

        flow._async_metadata_present = _present
        result = asyncio.run(flow.async_step_user(None))
        self.assertEqual(result["step_id"], "user")

    def test_provision_requires_a_url(self) -> None:
        flow = self._flow()
        result = asyncio.run(
            flow.async_step_provision({self.const.CONF_APK_URL: ""})
        )
        self.assertEqual(result["errors"][self.const.CONF_APK_URL], "apk_url_required")

    def test_provision_success_proceeds_to_login(self) -> None:
        flow = self._flow()

        async def _present() -> bool:  # after provisioning, the pack is present
            return True

        flow._async_metadata_present = _present

        async def _ok(*_a, **_k):
            return ["sensor_labels.json"]

        with (
            mock.patch.object(self.prov, "async_provision_metadata_from_apk", _ok),
            mock.patch.object(self.cf, "_invalidate_all_metadata_caches", lambda: None),
        ):
            result = asyncio.run(
                flow.async_step_provision(
                    {self.const.CONF_APK_URL: "https://example.invalid/app.apk"}
                )
            )
        self.assertEqual(result["step_id"], "user")  # sign-in form, pack now built

    def test_provision_failure_reports_error(self) -> None:
        flow = self._flow()

        async def _boom(*_a, **_k):
            raise self.prov.ApkProvisionError("bad apk")

        with mock.patch.object(self.prov, "async_provision_metadata_from_apk", _boom):
            result = asyncio.run(
                flow.async_step_provision(
                    {self.const.CONF_APK_URL: "https://example.invalid/app.apk"}
                )
            )
        self.assertEqual(result["errors"]["base"], "provision_failed")


if __name__ == "__main__":
    unittest.main()
