"""Tests for the APK-provisioning repair flow (repairs.py)."""

from __future__ import annotations

import asyncio
import importlib
import sys
import types
import unittest
from types import SimpleNamespace
from unittest import mock

from tests.hymer_test_support import ensure_package_paths, install_homeassistant_stubs


class _StubRepairsFlow:
    hass = None

    def async_show_form(self, **kwargs):
        return {"type": "form", **kwargs}

    def async_create_entry(self, **kwargs):
        return {"type": "create_entry", **kwargs}


def _load_repairs():
    repairs_mod = types.ModuleType("homeassistant.components.repairs")
    repairs_mod.RepairsFlow = _StubRepairsFlow
    def_mod = sys.modules.get("homeassistant.data_entry_flow") or types.ModuleType(
        "homeassistant.data_entry_flow"
    )
    if not hasattr(def_mod, "FlowResult"):
        def_mod.FlowResult = dict
    with mock.patch.dict(
        sys.modules,
        {
            "homeassistant.components.repairs": repairs_mod,
            "homeassistant.data_entry_flow": def_mod,
        },
    ):
        sys.modules.pop("custom_components.hymer_connect_metadata.repairs", None)
        return importlib.import_module("custom_components.hymer_connect_metadata.repairs")


class RepairsFlowTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        install_homeassistant_stubs()
        ensure_package_paths()
        try:
            cls.repairs = _load_repairs()
        except ImportError as err:
            raise unittest.SkipTest(f"repairs deps unavailable: {err}") from err

    def _flow(self):
        flow = self.repairs.MissingRuntimeMetadataRepairFlow()
        flow.hass = SimpleNamespace(
            config_entries=SimpleNamespace(async_entries=lambda _domain: [])
        )
        return flow

    def test_empty_url_reports_required(self) -> None:
        flow = self._flow()
        result = asyncio.run(flow.async_step_provision({"apk_url": "   "}))
        self.assertEqual(result["errors"]["apk_url"], "apk_url_required")

    def test_provision_failure_is_surfaced(self) -> None:
        flow = self._flow()

        async def boom(_hass, _url):
            raise self.repairs.ApkProvisionError("bad apk")

        with mock.patch.object(self.repairs, "async_provision_metadata_from_apk", boom):
            result = asyncio.run(
                flow.async_step_provision({"apk_url": "https://example.com/app.apk"})
            )
        self.assertEqual(result["errors"]["base"], "provision_failed")

    def test_successful_provision_creates_entry(self) -> None:
        flow = self._flow()
        called = {}

        async def ok(_hass, url):
            called["url"] = url
            return ["oauth_client.json"]

        with mock.patch.object(self.repairs, "async_provision_metadata_from_apk", ok):
            result = asyncio.run(
                flow.async_step_provision({"apk_url": "https://example.com/app.apk"})
            )
        self.assertEqual(result["type"], "create_entry")
        self.assertEqual(called["url"], "https://example.com/app.apk")


if __name__ == "__main__":
    unittest.main()
