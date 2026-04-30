from __future__ import annotations

import asyncio
import importlib
import unittest

from tests.hymer_test_support import ensure_package_paths, install_homeassistant_stubs


ensure_package_paths()
install_homeassistant_stubs()

api = importlib.import_module("custom_components.hymer_connect_metadata.api")


class ApiTitleTests(unittest.TestCase):
    def test_vehicle_title_prefers_model_and_vin_suffix(self) -> None:
        title = api.HymerConnectApi._build_vehicle_title(
            {
                "name": "My Camper",
                "model": "BMC-i 680",
                "vin": "WDB123456789ABCDEF",
            }
        )
        self.assertEqual(title, "BMC-i 680 (ABCDEF)")

    def test_vehicle_title_falls_back_to_model_without_vin(self) -> None:
        title = api.HymerConnectApi._build_vehicle_title(
            {
                "name": "My Camper",
                "model": "ML-T 580",
                "vin": "",
            }
        )
        self.assertEqual(title, "ML-T 580")


class _RefreshResponse:
    def __init__(self, status: int, body: str = "") -> None:
        self.status = status
        self._body = body

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb) -> None:
        return None

    async def text(self) -> str:
        return self._body

    async def json(self) -> dict[str, str]:
        return {
            "access_token": "new-access",
            "refresh_token": "new-refresh",
        }


class _RefreshSession:
    def __init__(self, response: _RefreshResponse) -> None:
        self.response = response

    def request(self, *_args, **_kwargs) -> _RefreshResponse:
        return self.response


class ApiRefreshTests(unittest.TestCase):
    def test_token_refresh_server_error_is_temporary_api_error(self) -> None:
        client = api.HymerConnectApi(_RefreshSession(_RefreshResponse(502)))
        client.set_tokens("old-access", "old-refresh")

        with self.assertRaises(api.HymerConnectApiError) as err:
            asyncio.run(client._refresh_access_token())
        self.assertNotIsInstance(err.exception, api.HymerConnectAuthError)

    def test_token_refresh_rejected_token_is_auth_error(self) -> None:
        client = api.HymerConnectApi(_RefreshSession(_RefreshResponse(401)))
        client.set_tokens("old-access", "old-refresh")

        with self.assertRaises(api.HymerConnectAuthError):
            asyncio.run(client._refresh_access_token())


if __name__ == "__main__":
    unittest.main()
