"""Standalone cloud client for HYMER / EHG auth and vehicle discovery."""

from __future__ import annotations

from dataclasses import asdict, dataclass
import json
import logging
from pathlib import Path
from typing import Any
from urllib.parse import quote

import aiohttp

API_BASE_URL = "https://smartrv.erwinhymergroup.com"
API_BASE_URL_SCC = "https://scc-api.smartrv.erwinhymergroup.com"
USER_AGENT = "okhttp/4.10.0"
APP_VERSION = "2.10.14"
SIGNALR_NEGOTIATE_PATH = "/datahub/negotiate"
ENDPOINT_AUTH = "/api/v2/oauth/token"
ENDPOINT_ACCOUNTS_ME = "/api/ehg/v1/accounts/me"
ENDPOINT_CONFIRMATION_TOKEN = "/api/ehg/v1/accounts/confirmationToken"
ENDPOINT_RV_TWIN_VEHICLES = "/api/rv-twin/vehicles"
AUTH_GRANT_TYPE_PASSWORD = "password"
AUTH_GRANT_TYPE_REFRESH = "refresh_token"
HEADER_ACCESS_TOKEN = "scc-csngaccesstoken"
HEADER_BRAND = "scc-brand"
HEADER_LOCALE = "scc-locale"
HEADER_EHG_BRAND = "ehg-smart-caravan-brand"
REPO_ROOT = Path(__file__).resolve().parents[3]
LOCAL_OAUTH_CLIENT_PATHS = (
    REPO_ROOT
    / "custom_components"
    / "hymer_connect_metadata"
    / "data"
    / "oauth_client.json",
    REPO_ROOT / "source" / "runtime_metadata" / "generated_data" / "oauth_client.json",
)

_LOGGER = logging.getLogger(__name__)


class HymerTokenToolError(Exception):
    """Base exception for the standalone tool."""


class HymerAuthError(HymerTokenToolError):
    """Authentication failure."""


@dataclass
class VehicleRecord:
    """Normalized merged vehicle record."""

    vehicle_id: int | None
    vehicle_urn: str
    vin: str
    name: str
    model: str
    model_group: str
    model_year: int | str | None
    scu_urn: str
    type_id: int | str | None
    title: str

    def to_dict(self) -> dict[str, Any]:
        """Return a JSON-ready dictionary."""
        return asdict(self)


class HymerCloudClient:
    """Thin standalone client for the HYMER cloud APIs."""

    def __init__(
        self,
        session: aiohttp.ClientSession,
        *,
        brand: str = "hymer",
        locale: str = "de-DE",
    ) -> None:
        self._session = session
        self._brand = brand
        self._locale = locale
        self._access_token: str | None = None
        self._refresh_token: str | None = None

    @property
    def access_token(self) -> str | None:
        """Return the OAuth access token."""
        return self._access_token

    @property
    def refresh_token(self) -> str | None:
        """Return the OAuth refresh token."""
        return self._refresh_token

    @staticmethod
    def _basic_auth_header() -> str:
        """Return the locally generated OAuth client Basic auth header."""
        for path in LOCAL_OAUTH_CLIENT_PATHS:
            try:
                payload = json.loads(path.read_text())
            except (FileNotFoundError, OSError, json.JSONDecodeError):
                continue
            header = payload.get("authorization_header")
            if isinstance(header, str) and header.startswith("Basic "):
                return header
        raise HymerTokenToolError(
            "Local OAuth client auth is missing. From a full checkout of this "
            "repository, run `python3 scripts/prepare_runtime_metadata.py "
            "--apk-path /path/to/com.ehg.hymerconnect.apk` (and add `--bundle-js` "
            "when the APK contains Hermes bytecode) before using the token tool."
        )

    def _main_api_headers(self) -> dict[str, str]:
        headers: dict[str, str] = {
            "Accept": "application/json, text/plain, */*",
            "User-Agent": USER_AGENT,
            "Accept-Encoding": "gzip",
            HEADER_EHG_BRAND: f"{self._brand.capitalize()}/{APP_VERSION}",
        }
        if self._access_token:
            headers["Authorization"] = f"Bearer {self._access_token}"
        return headers

    def _scc_api_headers(self) -> dict[str, str]:
        headers: dict[str, str] = {
            "Accept": "application/json, text/plain, */*",
            "User-Agent": USER_AGENT,
            "Accept-Encoding": "gzip",
            HEADER_BRAND: self._brand,
            HEADER_LOCALE: self._locale,
        }
        if self._access_token:
            headers[HEADER_ACCESS_TOKEN] = self._access_token
        return headers

    async def _request(
        self,
        method: str,
        url: str,
        *,
        data: str | None = None,
        json_data: dict[str, Any] | None = None,
        headers: dict[str, str] | None = None,
    ) -> dict[str, Any] | list[Any]:
        try:
            async with self._session.request(
                method,
                url,
                headers=headers,
                data=data,
                json=json_data,
            ) as response:
                if response.status == 401:
                    if self._refresh_token:
                        await self.refresh_access_token()
                        if headers and HEADER_ACCESS_TOKEN in headers:
                            headers[HEADER_ACCESS_TOKEN] = self._access_token or ""
                        elif headers and "Authorization" in headers:
                            headers["Authorization"] = (
                                f"Bearer {self._access_token}"
                                if self._access_token
                                else ""
                            )
                        return await self._request(
                            method,
                            url,
                            data=data,
                            json_data=json_data,
                            headers=headers,
                        )
                    raise HymerAuthError("Authentication failed")
                if response.status == 403:
                    raise HymerAuthError("Access forbidden")
                if response.status >= 400:
                    text = await response.text()
                    raise HymerTokenToolError(
                        f"API error {response.status}: {text[:200]}"
                    )
                if response.content_type and "json" in response.content_type:
                    return await response.json()
                return {}
        except aiohttp.ClientError as err:
            raise HymerTokenToolError(f"Connection error: {err}") from err

    async def authenticate(self, username: str, password: str) -> dict[str, str]:
        """Authenticate with OAuth resource-owner-password flow."""
        url = f"{API_BASE_URL}{ENDPOINT_AUTH}"
        headers = {
            "Accept": "application/json, text/plain, */*",
            "Content-Type": "application/x-www-form-urlencoded",
            "Authorization": self._basic_auth_header(),
            "User-Agent": USER_AGENT,
            HEADER_EHG_BRAND: f"{self._brand.capitalize()}/{APP_VERSION}",
        }
        data = (
            f"grant_type={AUTH_GRANT_TYPE_PASSWORD}"
            f"&username={quote(username, safe='')}"
            f"&password={quote(password, safe='')}"
        )
        try:
            async with self._session.request(
                "POST",
                url,
                headers=headers,
                data=data,
            ) as response:
                if response.status == 401:
                    raise HymerAuthError("Invalid email or password")
                if response.status >= 400:
                    text = await response.text()
                    raise HymerTokenToolError(
                        f"Auth error {response.status}: {text[:200]}"
                    )
                result = await response.json()
        except aiohttp.ClientError as err:
            raise HymerTokenToolError(f"Connection error: {err}") from err

        access_token = result.get("access_token")
        if not access_token:
            raise HymerAuthError("No access_token in auth response")
        self._access_token = access_token
        self._refresh_token = result.get("refresh_token")
        return {
            "access_token": self._access_token,
            "refresh_token": self._refresh_token or "",
        }

    async def refresh_access_token(self) -> None:
        """Refresh the OAuth access token."""
        if not self._refresh_token:
            raise HymerAuthError("No refresh token available")
        url = f"{API_BASE_URL}{ENDPOINT_AUTH}"
        headers = {
            "Accept": "application/json, text/plain, */*",
            "Content-Type": "application/x-www-form-urlencoded",
            "Authorization": self._basic_auth_header(),
            "User-Agent": USER_AGENT,
        }
        data = (
            f"grant_type={AUTH_GRANT_TYPE_REFRESH}"
            f"&refresh_token={quote(self._refresh_token, safe='.-_~')}"
        )
        try:
            async with self._session.request(
                "POST",
                url,
                headers=headers,
                data=data,
            ) as response:
                if response.status >= 400:
                    text = await response.text()
                    raise HymerAuthError(
                        f"Token refresh failed: {response.status}: {text[:200]}"
                    )
                result = await response.json()
        except aiohttp.ClientError as err:
            raise HymerTokenToolError(f"Connection error: {err}") from err

        access_token = result.get("access_token")
        if not access_token:
            raise HymerAuthError("No access_token in refresh response")
        self._access_token = access_token
        self._refresh_token = result.get("refresh_token", self._refresh_token)

    async def get_account(self) -> dict[str, Any]:
        url = f"{API_BASE_URL}{ENDPOINT_ACCOUNTS_ME}"
        result = await self._request("GET", url, headers=self._main_api_headers())
        return result if isinstance(result, dict) else {}

    async def get_confirmation_token(self) -> dict[str, Any]:
        url = f"{API_BASE_URL}{ENDPOINT_CONFIRMATION_TOKEN}"
        result = await self._request("POST", url, headers=self._main_api_headers())
        return result if isinstance(result, dict) else {}

    async def get_confirmation_token_value(self) -> str:
        result = await self.get_confirmation_token()
        token = result.get("token")
        if not isinstance(token, str) or not token:
            raise HymerTokenToolError("Confirmation token response did not include a token")
        return token

    async def get_remote_access_token(
        self,
        vehicle_urn: str,
        remote_refresh_token: str,
    ) -> str:
        url = f"{API_BASE_URL}/api/ehg/v1/vehicles/{vehicle_urn}/remoteAccessToken"
        headers = self._main_api_headers()
        headers["Content-Type"] = "application/json"
        result = await self._request(
            "POST",
            url,
            headers=headers,
            json_data={"token": remote_refresh_token},
        )
        if isinstance(result, dict) and isinstance(result.get("token"), str):
            return result["token"]
        raise HymerTokenToolError(
            "remoteAccessToken response did not contain a token"
        )

    async def get_ehg_vehicles(self) -> list[dict[str, Any]]:
        url = f"{API_BASE_URL}/api/ehg/v1/vehicles"
        result = await self._request("GET", url, headers=self._main_api_headers())
        if isinstance(result, dict) and isinstance(result.get("content"), list):
            return [item for item in result["content"] if isinstance(item, dict)]
        if isinstance(result, list):
            return [item for item in result if isinstance(item, dict)]
        if isinstance(result, dict):
            return [result]
        return []

    async def get_vehicle_by_token(self, activation_token: str) -> dict[str, Any]:
        url = f"{API_BASE_URL}/api/ehg/v1/vehicles/byToken"
        headers = self._main_api_headers()
        headers["ehg-token"] = activation_token
        result = await self._request("GET", url, headers=headers)
        return result if isinstance(result, dict) else {}

    async def get_vehicles(self) -> list[dict[str, Any]]:
        url = f"{API_BASE_URL_SCC}{ENDPOINT_RV_TWIN_VEHICLES}"
        result = await self._request("GET", url, headers=self._scc_api_headers())
        if isinstance(result, list):
            return [item for item in result if isinstance(item, dict)]
        if isinstance(result, dict):
            return [result]
        return []

    async def signalr_negotiate(self) -> dict[str, Any]:
        """Keep available for later pairing/transport work."""
        url = f"{API_BASE_URL}{SIGNALR_NEGOTIATE_PATH}?negotiateVersion=1"
        headers = {
            "Content-Type": "text/plain;charset=UTF-8",
            "X-Requested-With": "XMLHttpRequest",
            "X-SignalR-User-Agent": (
                "Microsoft SignalR/6.0 "
                "(6.0.25; Unknown OS; Browser; Unknown Runtime Version)"
            ),
            "User-Agent": USER_AGENT,
            "Accept-Encoding": "gzip",
        }
        result = await self._request("POST", url, headers=headers, data="")
        return result if isinstance(result, dict) else {}

    @staticmethod
    def _clean_str(value: Any) -> str:
        return value.strip() if isinstance(value, str) else ""

    @classmethod
    def _normalize_vin(cls, value: Any) -> str:
        return cls._clean_str(value).upper()

    @classmethod
    def _ehg_vehicle_vin(cls, vehicle: dict[str, Any]) -> str:
        return cls._normalize_vin(
            vehicle.get("vin")
            or vehicle.get("vehicleVin")
            or vehicle.get("vehicleIdentificationNumber")
            or vehicle.get("fin")
        )

    @classmethod
    def _build_vehicle_title(cls, vehicle: dict[str, Any]) -> str:
        base = (
            cls._clean_str(vehicle.get("name"))
            or cls._clean_str(vehicle.get("model"))
            or cls._clean_str(vehicle.get("model_group"))
            or cls._clean_str(vehicle.get("vin"))
            or (
                f"Vehicle {vehicle['vehicle_id']}"
                if vehicle.get("vehicle_id") is not None
                else "Vehicle"
            )
        )
        vin = cls._normalize_vin(vehicle.get("vin"))
        if vin:
            suffix = vin[-6:]
            if suffix and suffix not in base:
                return f"{base} ({suffix})"
        return base

    @classmethod
    def _match_ehg_vehicle(
        cls,
        scc_vehicle: dict[str, Any],
        ehg_vehicles: list[dict[str, Any]],
    ) -> dict[str, Any] | None:
        vin = cls._normalize_vin(scc_vehicle.get("vin"))
        if vin:
            matches = [
                vehicle
                for vehicle in ehg_vehicles
                if cls._ehg_vehicle_vin(vehicle) == vin
            ]
            if len(matches) == 1:
                return matches[0]
        return None

    async def discover_vehicles(self) -> list[VehicleRecord]:
        scc_vehicles_raw = await self.get_vehicles()
        try:
            ehg_vehicles_raw = await self.get_ehg_vehicles()
        except HymerTokenToolError as err:
            _LOGGER.info("Could not fetch EHG vehicles during discovery: %s", err)
            ehg_vehicles_raw = []

        discovered: list[VehicleRecord] = []
        for scc_vehicle in scc_vehicles_raw:
            ehg_vehicle = self._match_ehg_vehicle(scc_vehicle, ehg_vehicles_raw)
            if (
                ehg_vehicle is None
                and len(scc_vehicles_raw) == 1
                and len(ehg_vehicles_raw) == 1
            ):
                ehg_vehicle = ehg_vehicles_raw[0]

            merged = {
                "vehicle_id": scc_vehicle.get("id"),
                "vehicle_urn": self._clean_str(
                    (ehg_vehicle or {}).get("urn")
                    or (ehg_vehicle or {}).get("vehicleUrn")
                ),
                "vin": self._normalize_vin(
                    scc_vehicle.get("vin") or self._ehg_vehicle_vin(ehg_vehicle or {})
                ),
                "name": self._clean_str(
                    scc_vehicle.get("name") or (ehg_vehicle or {}).get("name")
                ),
                "model": self._clean_str(
                    scc_vehicle.get("model") or (ehg_vehicle or {}).get("model")
                ),
                "model_group": self._clean_str(
                    scc_vehicle.get("modelGroup")
                    or (ehg_vehicle or {}).get("modelGroup")
                ),
                "model_year": scc_vehicle.get("modelYear")
                or (ehg_vehicle or {}).get("modelYear"),
                "scu_urn": self._clean_str(
                    scc_vehicle.get("smartUnitUrn")
                    or (ehg_vehicle or {}).get("smartUnitUrn")
                ),
                "type_id": scc_vehicle.get("typeId")
                or (ehg_vehicle or {}).get("typeId"),
            }
            merged["title"] = self._build_vehicle_title(merged)
            discovered.append(VehicleRecord(**merged))
        return discovered
