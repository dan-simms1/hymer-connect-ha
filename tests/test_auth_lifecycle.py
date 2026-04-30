from __future__ import annotations

import asyncio
import importlib
import sys
import unittest

from tests.hymer_test_support import install_homeassistant_stubs


install_homeassistant_stubs()
sys.modules.pop("custom_components.hymer_connect_metadata", None)
integration = importlib.import_module("custom_components.hymer_connect_metadata")
api_mod = importlib.import_module("custom_components.hymer_connect_metadata.api")


class _ConfigEntries:
    def __init__(self) -> None:
        self.updates: list[dict[str, object]] = []

    def async_update_entry(self, entry, **kwargs) -> None:
        self.updates.append(kwargs)


class _Hass:
    def __init__(self) -> None:
        self.config_entries = _ConfigEntries()


class _Entry:
    def __init__(self, data: dict[str, object]) -> None:
        self.data = data


class _Api:
    def __init__(self) -> None:
        self.callback = None
        self.set_tokens_calls: list[tuple[str, str]] = []
        self.get_account_calls = 0
        self.authenticate_calls: list[tuple[str, str]] = []
        self.get_account_error: Exception | None = None

    def set_token_update_callback(self, callback) -> None:
        self.callback = callback

    def set_tokens(self, access_token: str, refresh_token: str) -> None:
        self.set_tokens_calls.append((access_token, refresh_token))

    async def get_account(self) -> dict[str, object]:
        self.get_account_calls += 1
        if self.get_account_error is not None:
            raise self.get_account_error
        if self.callback is not None:
            self.callback("rotated-access", "rotated-refresh")
        return {"ok": True}

    async def authenticate(self, username: str, password: str) -> dict[str, str]:
        self.authenticate_calls.append((username, password))
        if self.callback is not None:
            self.callback("password-access", "password-refresh")
        return {
            "access_token": "password-access",
            "refresh_token": "password-refresh",
        }


class AuthLifecycleTests(unittest.TestCase):
    def test_setup_uses_stored_refresh_tokens_before_password_login(self) -> None:
        hass = _Hass()
        entry = _Entry(
            {
                "username": "user@example.com",
                "password": "secret",
                "access_token": "stored-access",
                "refresh_token": "stored-refresh",
            }
        )
        api = _Api()

        asyncio.run(integration._async_prepare_authenticated_api(hass, entry, api))

        self.assertEqual(api.set_tokens_calls, [("stored-access", "stored-refresh")])
        self.assertEqual(api.get_account_calls, 1)
        self.assertEqual(api.authenticate_calls, [])
        self.assertEqual(
            hass.config_entries.updates[-1]["data"]["access_token"],
            "rotated-access",
        )
        self.assertEqual(
            hass.config_entries.updates[-1]["data"]["refresh_token"],
            "rotated-refresh",
        )

    def test_setup_falls_back_to_password_login_when_refresh_is_rejected(self) -> None:
        hass = _Hass()
        entry = _Entry(
            {
                "username": "user@example.com",
                "password": "secret",
                "access_token": "stored-access",
                "refresh_token": "stored-refresh",
            }
        )
        api = _Api()
        api.get_account_error = api_mod.HymerConnectAuthError("expired")

        asyncio.run(integration._async_prepare_authenticated_api(hass, entry, api))

        self.assertEqual(api.set_tokens_calls, [("stored-access", "stored-refresh")])
        self.assertEqual(api.authenticate_calls, [("user@example.com", "secret")])
        self.assertEqual(
            hass.config_entries.updates[-1]["data"]["access_token"],
            "password-access",
        )
        self.assertEqual(
            hass.config_entries.updates[-1]["data"]["refresh_token"],
            "password-refresh",
        )


if __name__ == "__main__":
    unittest.main()
