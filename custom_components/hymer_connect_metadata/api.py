"""API client for HYMER Connect."""

from __future__ import annotations

import logging
from collections.abc import Callable
from typing import Any
from urllib.parse import quote

import aiohttp

from .const import (
    API_BASE_URL,
    API_BASE_URL_APPCOMM,
    API_BASE_URL_SCC,
    APP_VERSION,
    AUTH_GRANT_TYPE_PASSWORD,
    AUTH_GRANT_TYPE_REFRESH,
    ENDPOINT_ACCOUNTS_ME,
    ENDPOINT_AUTH,
    ENDPOINT_CONFIRMATION_TOKEN,
    ENDPOINT_CONFIG_BRANDS,
    ENDPOINT_RV_TWIN_VEHICLES,
    ENDPOINT_SERVICE_CATALOGUE,
    HEADER_ACCESS_TOKEN,
    HEADER_BRAND,
    HEADER_EHG_BRAND,
    HEADER_LOCALE,
    SIGNALR_NEGOTIATE_PATH,
    USER_AGENT,
)
from .runtime_metadata import load_oauth_basic_auth_header

_LOGGER = logging.getLogger(__name__)

TokenUpdateCallback = Callable[[str, str], None]


class HymerConnectApiError(Exception):
    """Base exception for API errors."""


class HymerConnectAuthError(HymerConnectApiError):
    """Authentication error."""


class HymerConnectApi:
    """Client for the HYMER Connect cloud API."""

    def __init__(
        self,
        session: aiohttp.ClientSession,
        brand: str = "hymer",
        locale: str = "de-DE",
    ) -> None:
        """Initialize the API client."""
        self._session = session
        self._brand = brand
        self._locale = locale
        self._access_token: str | None = None
        self._refresh_token: str | None = None
        self._token_update_callback: TokenUpdateCallback | None = None

    @property
    def access_token(self) -> str | None:
        """Return the current access token."""
        return self._access_token

    @property
    def authenticated(self) -> bool:
        """Return True if we have an access token."""
        return self._access_token is not None

    @property
    def refresh_token(self) -> str | None:
        """Return the current OAuth refresh token."""
        return self._refresh_token

    def set_tokens(self, access_token: str, refresh_token: str) -> None:
        """Set auth tokens directly (from stored config)."""
        self._access_token = access_token
        self._refresh_token = refresh_token

    def set_token_update_callback(
        self,
        callback: TokenUpdateCallback | None,
    ) -> None:
        """Install a callback used to persist rotated OAuth tokens."""
        self._token_update_callback = callback

    def _notify_tokens_updated(self) -> None:
        """Notify the owner when OAuth tokens have changed."""
        if (
            self._token_update_callback is not None
            and self._access_token
            and self._refresh_token
        ):
            self._token_update_callback(self._access_token, self._refresh_token)

    @staticmethod
    def _is_closed_client_error(err: RuntimeError) -> bool:
        """Return True when aiohttp/httpx reports a closed client/session."""
        text = str(err).lower()
        return "session is closed" in text or "client has been closed" in text

    @staticmethod
    def _basic_auth_header() -> str:
        """Return the locally generated Basic auth header for OAuth2."""
        return load_oauth_basic_auth_header()

    def _main_api_headers(self) -> dict[str, str]:
        """Build headers for the main API (smartrv.erwinhymergroup.com)."""
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
        """Build headers for the SCC API (scc-api.smartrv.erwinhymergroup.com)."""
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
        _retried: bool = False,
    ) -> dict[str, Any] | list[Any]:
        """Make an API request."""
        try:
            async with self._session.request(
                method, url, headers=headers, data=data, json=json_data
            ) as resp:
                if resp.status == 401:
                    if self._refresh_token and not _retried:
                        await self._refresh_access_token()
                        if headers and HEADER_ACCESS_TOKEN in headers:
                            headers[HEADER_ACCESS_TOKEN] = self._access_token
                        elif headers and "Authorization" in headers:
                            headers["Authorization"] = f"Bearer {self._access_token}"
                        return await self._request(
                            method,
                            url,
                            data=data,
                            json_data=json_data,
                            headers=headers,
                            _retried=True,
                        )
                    raise HymerConnectAuthError("Authentication failed")
                if resp.status == 403:
                    raise HymerConnectAuthError("Access forbidden")
                if resp.status >= 400:
                    text = await resp.text()
                    raise HymerConnectApiError(
                        f"API error {resp.status}: {text[:200]}"
                    )
                if resp.content_type and "json" in resp.content_type:
                    return await resp.json()
                return {}
        except aiohttp.ClientError as err:
            raise HymerConnectApiError(f"Connection error: {err}") from err
        except RuntimeError as err:
            if self._is_closed_client_error(err):
                raise HymerConnectApiError(f"Connection error: {err}") from err
            raise

    # --- Authentication ---

    async def authenticate(self, username: str, password: str) -> dict[str, str]:
        """Authenticate using OAuth2 ROPC with HTTP Basic client auth."""
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
                "POST", url, headers=headers, data=data
            ) as resp:
                _LOGGER.debug("Auth response status: %s", resp.status)
                if resp.status == 401:
                    raise HymerConnectAuthError("Invalid email or password")
                if resp.status >= 400:
                    text = await resp.text()
                    _LOGGER.error("Auth error %s: %s", resp.status, text[:200])
                    raise HymerConnectApiError(
                        f"Auth error {resp.status}: {text[:200]}"
                    )
                result = await resp.json()
                if "access_token" in result:
                    self._access_token = result["access_token"]
                    self._refresh_token = result.get("refresh_token")
                    self._notify_tokens_updated()
                    return {
                        "access_token": self._access_token,
                        "refresh_token": self._refresh_token or "",
                    }
                raise HymerConnectAuthError("No access_token in auth response")
        except aiohttp.ClientError as err:
            raise HymerConnectApiError(f"Connection error: {err}") from err
        except RuntimeError as err:
            if self._is_closed_client_error(err):
                raise HymerConnectApiError(f"Connection error: {err}") from err
            raise

    async def _refresh_access_token(self) -> None:
        """Refresh the access token using OAuth2 refresh_token grant."""
        if not self._refresh_token:
            raise HymerConnectAuthError("No refresh token available")
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
                "POST", url, headers=headers, data=data
            ) as resp:
                _LOGGER.debug("Token refresh status: %s", resp.status)
                if resp.status >= 400:
                    text = await resp.text()
                    _LOGGER.warning(
                        "Token refresh failed %s: %s", resp.status, text[:200]
                    )
                    if resp.status in (400, 401, 403):
                        raise HymerConnectAuthError("Token refresh failed")
                    raise HymerConnectApiError(
                        f"Token refresh temporarily failed: {resp.status}"
                    )
                result = await resp.json()
                if "access_token" in result:
                    self._access_token = result["access_token"]
                    self._refresh_token = result.get(
                        "refresh_token", self._refresh_token
                    )
                    self._notify_tokens_updated()
                    return
        except aiohttp.ClientError as err:
            raise HymerConnectApiError(f"Connection error: {err}") from err
        except RuntimeError as err:
            if self._is_closed_client_error(err):
                raise HymerConnectApiError(f"Connection error: {err}") from err
            raise
        raise HymerConnectAuthError("Token refresh failed")

    # --- Main API ---

    async def get_account(self) -> dict[str, Any]:
        """Get current account info."""
        url = f"{API_BASE_URL}{ENDPOINT_ACCOUNTS_ME}"
        return await self._request("GET", url, headers=self._main_api_headers())

    async def get_confirmation_token(self) -> dict[str, Any]:
        """Get a confirmation token for remote access."""
        url = f"{API_BASE_URL}{ENDPOINT_CONFIRMATION_TOKEN}"
        return await self._request("POST", url, headers=self._main_api_headers())

    async def get_remote_access_token(
        self, vehicle_urn: str, refresh_token: str
    ) -> str:
        """Exchange a remote-access-refresh token for a fresh remote-access token.

        POST /api/ehg/v1/vehicles/{urn}/remoteAccessToken
        Body: {"token": "<refresh_token_jwt>"}
        Returns the new access token (ett=access) string.
        """
        url = f"{API_BASE_URL}/api/ehg/v1/vehicles/{vehicle_urn}/remoteAccessToken"
        headers = self._main_api_headers()
        headers["Content-Type"] = "application/json"
        result = await self._request(
            "POST", url, json_data={"token": refresh_token}, headers=headers
        )
        if isinstance(result, dict) and "token" in result:
            return result["token"]
        raise HymerConnectApiError(
            "remoteAccessToken response did not contain a token"
        )

    async def get_ehg_vehicles(self) -> list[Any]:
        """Get vehicles from the main EHG API (returns vehicle URN).

        Response is paginated: {content: [...], totalElements: N, ...}
        """
        url = f"{API_BASE_URL}/api/ehg/v1/vehicles"
        result = await self._request("GET", url, headers=self._main_api_headers())
        if isinstance(result, dict) and "content" in result:
            return result["content"]
        if isinstance(result, list):
            return result
        return [result]

    @staticmethod
    def _clean_str(value: Any) -> str:
        """Return a stripped string or an empty string."""
        if isinstance(value, str):
            return value.strip()
        return ""

    @classmethod
    def _normalize_vin(cls, value: Any) -> str:
        """Normalize VIN values for matching across APIs."""
        return cls._clean_str(value).upper()

    @classmethod
    def _ehg_vehicle_vin(cls, vehicle: dict[str, Any]) -> str:
        """Extract a VIN-like value from an EHG vehicle payload."""
        return cls._normalize_vin(
            vehicle.get("vin")
            or vehicle.get("vehicleVin")
            or vehicle.get("vehicleIdentificationNumber")
            or vehicle.get("fin")
        )

    @classmethod
    def _build_vehicle_title(cls, vehicle: dict[str, Any]) -> str:
        """Build a readable per-vehicle title for config entries."""
        base = (
            cls._clean_str(vehicle.get("model"))
            or cls._clean_str(vehicle.get("model_group"))
            or cls._clean_str(vehicle.get("name"))
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
        """Match an SCC vehicle to an EHG vehicle, preferring VIN."""
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

    async def discover_vehicles(self) -> list[dict[str, Any]]:
        """Return normalized per-vehicle records merged from SCC and EHG APIs."""
        scc_vehicles_raw = await self.get_vehicles()
        try:
            ehg_vehicles_raw = await self.get_ehg_vehicles()
        except HymerConnectApiError as err:
            _LOGGER.info("Could not fetch EHG vehicles during discovery: %s", err)
            ehg_vehicles_raw = []
        ehg_vehicles = [
            vehicle for vehicle in ehg_vehicles_raw if isinstance(vehicle, dict)
        ]

        discovered: list[dict[str, Any]] = []

        for scc_vehicle in scc_vehicles_raw:
            if not isinstance(scc_vehicle, dict):
                continue

            ehg_vehicle = self._match_ehg_vehicle(scc_vehicle, ehg_vehicles)

            # Single-vehicle accounts often have matching records without a VIN
            # present on both payloads. In that case, pair the only SCC and EHG
            # records so the integration can still be configured.
            if (
                ehg_vehicle is None
                and len(scc_vehicles_raw) == 1
                and len(ehg_vehicles) == 1
            ):
                ehg_vehicle = ehg_vehicles[0]

            record: dict[str, Any] = {
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
            record["title"] = self._build_vehicle_title(record)
            discovered.append(record)

        return discovered

    async def resolve_vehicle_selection(
        self,
        *,
        vehicle_id: int | None = None,
        vehicle_urn: str = "",
        vin: str = "",
        scu_urn: str = "",
    ) -> dict[str, Any] | None:
        """Resolve a single vehicle from the normalized discovered vehicles."""
        discovered = await self.discover_vehicles()

        clean_vehicle_urn = self._clean_str(vehicle_urn)
        if clean_vehicle_urn:
            for vehicle in discovered:
                if vehicle.get("vehicle_urn") == clean_vehicle_urn:
                    return vehicle

        if vehicle_id is not None:
            for vehicle in discovered:
                if vehicle.get("vehicle_id") == vehicle_id:
                    return vehicle

        normalized_vin = self._normalize_vin(vin)
        if normalized_vin:
            for vehicle in discovered:
                if self._normalize_vin(vehicle.get("vin")) == normalized_vin:
                    return vehicle

        clean_scu_urn = self._clean_str(scu_urn)
        if clean_scu_urn:
            for vehicle in discovered:
                if vehicle.get("scu_urn") == clean_scu_urn:
                    return vehicle

        if len(discovered) == 1:
            return discovered[0]

        return None

    async def get_vehicle_by_token(self, ehg_token: str) -> dict[str, Any]:
        """Get vehicle info using an activation/owner token."""
        url = f"{API_BASE_URL}/api/ehg/v1/vehicles/byToken"
        headers = self._main_api_headers()
        headers["ehg-token"] = ehg_token
        return await self._request("GET", url, headers=headers)

    # --- SCC API ---

    async def get_vehicles(self) -> list[Any]:
        """Get list of vehicles from the RV-Twin API."""
        url = f"{API_BASE_URL_SCC}{ENDPOINT_RV_TWIN_VEHICLES}"
        result = await self._request("GET", url, headers=self._scc_api_headers())
        if isinstance(result, list):
            return result
        return [result]

    async def get_vehicle(self, vehicle_id: int) -> dict[str, Any]:
        """Get single vehicle details including tanks."""
        url = f"{API_BASE_URL_SCC}{ENDPOINT_RV_TWIN_VEHICLES}/{vehicle_id}"
        return await self._request("GET", url, headers=self._scc_api_headers())

    async def get_brand_details(self) -> dict[str, Any]:
        """Get brand configuration details."""
        url = f"{API_BASE_URL_SCC}{ENDPOINT_CONFIG_BRANDS}"
        return await self._request("GET", url, headers=self._scc_api_headers())

    async def get_service_catalogue(self) -> dict[str, Any]:
        """Get available services."""
        url = f"{API_BASE_URL_SCC}{ENDPOINT_SERVICE_CATALOGUE}"
        return await self._request("GET", url, headers=self._scc_api_headers())

    # --- SignalR Negotiate ---

    async def signalr_negotiate(self) -> dict[str, Any]:
        """Negotiate a SignalR connection to the datahub."""
        url = f"{API_BASE_URL_APPCOMM}{SIGNALR_NEGOTIATE_PATH}?negotiateVersion=1"
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
        # NOTE: The mobile app sends negotiate WITHOUT auth headers.
        # Adding auth headers causes INVALID_INPUT on UpdateTokens.
        return await self._request("POST", url, headers=headers, data="")

    # --- Aggregated Data ---

    async def get_vehicle_status(
        self,
        *,
        vehicle_id: int | None = None,
        vehicle_urn: str = "",
        vin: str = "",
        scu_urn: str = "",
    ) -> dict[str, Any]:
        """Get aggregated vehicle status from the SCC REST API."""
        data: dict[str, Any] = {}

        try:
            vehicles = await self.discover_vehicles()
            _LOGGER.debug("Fetched %d vehicles", len(vehicles) if vehicles else 0)
            if vehicles:
                data["vehicles"] = vehicles
                vehicle = await self.resolve_vehicle_selection(
                    vehicle_id=vehicle_id,
                    vehicle_urn=vehicle_urn,
                    vin=vin,
                    scu_urn=scu_urn,
                )
                if vehicle is None:
                    # Maintain legacy behaviour for older entries that were
                    # account-scoped and had no vehicle selection stored.
                    vehicle = vehicles[0]

                data["vehicle"] = {
                    **vehicle,
                    "id": vehicle.get("vehicle_id"),
                    "smartUnitUrn": vehicle.get("scu_urn"),
                    "modelGroup": vehicle.get("model_group"),
                    "modelYear": vehicle.get("model_year"),
                    "typeId": vehicle.get("type_id"),
                }
                data["vehicle_id"] = vehicle.get("vehicle_id")
                data["vehicle_urn"] = vehicle.get("vehicle_urn", "")
                data["vin"] = vehicle.get("vin", "")
                data["name"] = vehicle.get("name", "")
                data["model"] = vehicle.get("model", "")
                data["model_group"] = vehicle.get("model_group", "")
                data["model_year"] = vehicle.get("model_year")
                data["scu_urn"] = vehicle.get("scu_urn", "")
                data["type_id"] = vehicle.get("type_id")

                selected_vehicle_id = vehicle.get("vehicle_id")
                if selected_vehicle_id:
                    try:
                        details = await self.get_vehicle(selected_vehicle_id)
                        data["vehicle_details"] = details
                        data["tanks"] = details.get("tanks", [])
                    except HymerConnectApiError:
                        _LOGGER.debug("Could not fetch vehicle details")
        except HymerConnectAuthError:
            raise
        except HymerConnectApiError as err:
            _LOGGER.warning("Could not fetch vehicles: %s", err)

        try:
            account = await self.get_account()
            data["account"] = account
        except HymerConnectApiError:
            _LOGGER.debug("Could not fetch account info")
        _LOGGER.debug(
            "Vehicle status keys: %s, model=%s, vin=%s",
            list(data.keys()),
            data.get("model"),
            data.get("vin"),
        )
        return data
