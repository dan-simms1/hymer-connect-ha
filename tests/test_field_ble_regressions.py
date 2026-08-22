"""Regression guards for the BLE field tooling.

These came out of the 2026-08-22 Codex review. They pin three things: the
bleak-version compatibility shim, the exact values of the PIA status enum read
from the decompiled app, and the rule that field secrets never cross argv or
the git boundary.
"""

from __future__ import annotations

import asyncio
import importlib
from pathlib import Path
import re
import subprocess
import sys
import unittest

from tests.hymer_test_support import ensure_package_paths, install_homeassistant_stubs

REPO_ROOT = Path(__file__).resolve().parents[1]
TOOL_ROOT = REPO_ROOT / "tools" / "hymer_token_tool"
sys.path.insert(0, str(TOOL_ROOT))

from hymer_token_tool import ble, scu  # noqa: E402


class ServiceDiscoveryCompatibilityTests(unittest.TestCase):
    def test_modern_bleak_uses_services_without_legacy_getter(self) -> None:
        services = object()

        class ModernClient:
            @property
            def services(self):
                return services

            def __getattr__(self, name: str):
                if name == "get_services":
                    raise AssertionError("legacy getter must not be accessed")
                raise AttributeError(name)

        self.assertIs(asyncio.run(scu._discover_services(ModernClient())), services)

    def test_legacy_bleak_awaits_get_services(self) -> None:
        services = object()

        class LegacyClient:
            async def get_services(self):
                return services

        self.assertIs(asyncio.run(scu._discover_services(LegacyClient())), services)

    def test_missing_service_api_raises_domain_error(self) -> None:
        with self.assertRaisesRegex(
            scu.ScuBleSessionError, "neither `services` nor `get_services"
        ):
            asyncio.run(scu._discover_services(object()))

    def test_probe_device_has_the_same_guard(self) -> None:
        """ble.probe_device must not fall through to an AttributeError."""
        source = (TOOL_ROOT / "hymer_token_tool" / "ble.py").read_text(encoding="utf-8")
        self.assertIn("neither `services` nor `get_services()`", source)

    def test_write_mode_override_is_public(self) -> None:
        session = scu.ScuBleSession("scu")
        session.set_write_with_response(False)
        self.assertFalse(session.write_with_response)
        session.set_write_with_response(True)
        self.assertTrue(session.write_with_response)


class PiaStatusEnumTests(unittest.TestCase):
    def test_complete_enum_matches_decompiled_values(self) -> None:
        install_homeassistant_stubs()
        ensure_package_paths()
        pia = importlib.import_module("custom_components.hymer_connect_metadata.pia_decoder")
        names = (
            "STATUS_NO_STATUS",
            "STATUS_SUCCESS",
            "STATUS_INVALID_INPUT",
            "STATUS_INTERNAL_ERROR",
            "STATUS_INVALID_PROTOCOL_VERSION",
            "STATUS_ACCESS_DENIED",
            "STATUS_TOKEN_EXPIRED",
            "STATUS_NOT_FOUND",
            "STATUS_UNAVAILABLE",
            "STATUS_INVALID_SIZE",
            "STATUS_MAIN_USER_ALREADY_PAIRED",
            "STATUS_MAIN_USER_CANNOT_ACCEPT_INVITATION",
            "STATUS_AUTH_TOKEN_EXPIRED",
            "STATUS_REMOTE_TOKEN_EXPIRED",
            "STATUS_VEHICLE_NOT_FOUND",
            "STATUS_SCU_IS_NOT_ONLINE",
            "STATUS_CALL_TO_SCU_FAILED",
            "STATUS_CLOUD_ERROR",
            "STATUS_CONNECTIVITY_ISSUE",
            "STATUS_BACKEND_SERVICE_ERROR",
        )
        self.assertEqual([getattr(pia, name) for name in names], list(range(20)))

    def test_access_denied_is_not_transient(self) -> None:
        """ACCESS_DENIED is an authorisation verdict; it must not be retried as upstream noise."""
        install_homeassistant_stubs()
        ensure_package_paths()
        pia = importlib.import_module("custom_components.hymer_connect_metadata.pia_decoder")
        self.assertNotIn(pia.STATUS_ACCESS_DENIED, pia.TRANSIENT_UPSTREAM_STATUSES)


class MtuAndWriteModeTests(unittest.TestCase):
    """The pairing timeout was a low-MTU RX-buffer overflow (see BLE_RUNBOOK)."""

    def test_acquire_large_mtu_calls_bluez_backend(self) -> None:
        calls = []

        class BluezClient:
            async def _acquire_mtu(self):
                calls.append(True)

        session = scu.ScuBleSession("scu")
        asyncio.run(session._acquire_large_mtu(BluezClient()))
        self.assertEqual(calls, [True])

    def test_acquire_large_mtu_is_a_noop_without_the_backend_hook(self) -> None:
        class CoreBluetoothClient:
            pass  # no _acquire_mtu; CoreBluetooth negotiates its own MTU

        session = scu.ScuBleSession("scu")
        # Must not raise.
        asyncio.run(session._acquire_large_mtu(CoreBluetoothClient()))

    def test_acquire_large_mtu_swallows_backend_errors(self) -> None:
        class FlakyClient:
            async def _acquire_mtu(self):
                raise RuntimeError("negotiation refused")

        session = scu.ScuBleSession("scu")
        asyncio.run(session._acquire_large_mtu(FlakyClient()))  # must not raise

    def test_connect_prefers_write_with_response_for_uart_rx(self) -> None:
        """The app uses WRITE_TYPE_DEFAULT; connect() must not prefer no-response."""
        source = (TOOL_ROOT / "hymer_token_tool" / "scu.py").read_text(encoding="utf-8")
        self.assertIn("prefer_without_response=False", source)
        self.assertNotIn("prefer_without_response=True", source)


class FieldScriptSecurityTests(unittest.TestCase):
    def test_activation_token_never_crosses_argv(self) -> None:
        script = (TOOL_ROOT / "full_pair.sh").read_text(encoding="utf-8")
        self.assertNotRegex(
            script,
            r"--activation-token\s+[\"']?\$",
            "owner JWT must be supplied through a protected file, not argv",
        )
        self.assertIn("--activation-token-file", script)
        self.assertIn("umask 077", script)

    def test_field_scripts_gate_actuation_and_require_targets(self) -> None:
        for name in ("full_pair.sh", "bond_test.py"):
            text = (TOOL_ROOT / name).read_text(encoding="utf-8")
            self.assertIn("CONFIRM", text, f"{name} must gate on CONFIRM")
            self.assertIn("COMPONENT_ID", text, f"{name} must take the target component id")
            self.assertIn("VALUE_ID", text, f"{name} must take the target value id")

    def test_no_real_identifiers_in_field_files(self) -> None:
        # Detect the SHAPES of real vehicle/host identifiers, so this guard
        # carries no real identifier of its own. Documentation placeholders are
        # allowlisted.
        shapes = (
            (re.compile(r"hy-\d{8,}"), "vehicle URN body"),
            (re.compile(r"\bT\d{3}(?:\.\d{2,3}){4}\b"), "SCU ID"),
            (re.compile(r"\b(?:[0-9A-Fa-f]{2}:){5}[0-9A-Fa-f]{2}\b"), "BLE MAC"),
            (re.compile(r"\b192\.168\.\d{1,3}\.\d{1,3}\b"), "private IPv4"),
            (re.compile(r"\bW1V[A-Z0-9]{14}\b"), "VIN"),
        )
        allowed = {"AA:BB:CC:DD:EE:FF"}  # documentation placeholder
        for name in ("BLE_RUNBOOK.md", "full_pair.sh", "bond_test.py", "dbus_pair.py", "setup_ble_host.sh"):
            text = (TOOL_ROOT / name).read_text(encoding="utf-8")
            for rx, what in shapes:
                for match in rx.findall(text):
                    self.assertIn(match, allowed, f"{name} contains a real {what}: {match}")

    def test_pairing_agent_is_locked_to_one_device(self) -> None:
        source = (TOOL_ROOT / "dbus_pair.py").read_text(encoding="utf-8")
        self.assertIn("org.bluez.Error.Rejected", source)
        self.assertIn("call_unregister_agent", source)
        # Default-agent registration must be opt-in, not unconditional.
        self.assertRegex(source, r"AGENT_DEFAULT.*\n.*call_request_default_agent")

    def test_secret_filenames_are_gitignored(self) -> None:
        for filename in ("activation.txt", "creds.ini", "remote-refresh.txt", "pair-session.json"):
            for location in (filename, f"tools/hymer_token_tool/{filename}"):
                result = subprocess.run(
                    ["git", "check-ignore", "--quiet", "--no-index", location],
                    cwd=REPO_ROOT,
                    check=False,
                )
                self.assertEqual(result.returncode, 0, f"{location} is not gitignored")


class UserTopicGateTests(unittest.TestCase):
    """scu-user-topic must refuse to send without explicit acknowledgement."""

    def _args(self, **over):
        import argparse

        base = dict(
            identifier="scu", field=7, payload_hex="", ack_destructive=False,
            timeout=1.0, tls_timeout=1.0, request_timeout=1.0, wake_up=False,
            wake_delay=0.0, json_output=False,
        )
        base.update(over)
        return argparse.Namespace(**base)

    def test_refuses_without_acknowledgement(self) -> None:
        from hymer_token_tool import cli

        with self.assertRaisesRegex(SystemExit, "Refusing to send"):
            asyncio.run(cli.command_scu_user_topic(self._args()))

    def test_refuses_pairing_fields(self) -> None:
        from hymer_token_tool import cli

        for field in (4, 6):
            with self.assertRaisesRegex(SystemExit, "pairing ceremony"):
                asyncio.run(cli.command_scu_user_topic(
                    self._args(field=field, ack_destructive=True)))

    def test_rejects_bad_hex(self) -> None:
        from hymer_token_tool import cli

        with self.assertRaisesRegex(SystemExit, "not valid hex"):
            asyncio.run(cli.command_scu_user_topic(
                self._args(ack_destructive=True, payload_hex="zz")))


class SecretFileReaderTests(unittest.TestCase):
    def test_rejects_group_or_other_readable_token_file(self) -> None:
        import tempfile

        from hymer_token_tool import cli

        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "activation.txt"
            path.write_text("fake-activation-token")
            path.chmod(0o644)
            with self.assertRaisesRegex(cli.HymerTokenToolError, "group/other readable"):
                cli._read_secret_file(path, what="Activation token")
            path.chmod(0o600)
            self.assertEqual(
                cli._read_secret_file(path, what="Activation token"), "fake-activation-token"
            )

    def test_rejects_empty_and_missing_files(self) -> None:
        import tempfile

        from hymer_token_tool import cli

        with tempfile.TemporaryDirectory() as tmp:
            missing = Path(tmp) / "nope.txt"
            with self.assertRaisesRegex(cli.HymerTokenToolError, "does not exist"):
                cli._read_secret_file(missing, what="Activation token")
            empty = Path(tmp) / "empty.txt"
            empty.write_text("   \n")
            empty.chmod(0o600)
            with self.assertRaisesRegex(cli.HymerTokenToolError, "is empty"):
                cli._read_secret_file(empty, what="Activation token")


if __name__ == "__main__":
    unittest.main()
