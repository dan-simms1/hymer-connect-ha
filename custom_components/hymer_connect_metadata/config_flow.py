"""Config flow for HYMER Connect integration."""

from __future__ import annotations

import logging
from typing import Any

import voluptuous as vol

from homeassistant.config_entries import ConfigEntry, ConfigFlow, ConfigFlowResult
from homeassistant.const import CONF_PASSWORD, CONF_USERNAME
from homeassistant.helpers.aiohttp_client import async_create_clientsession

try:
    from homeassistant.config_entries import OptionsFlowWithReload
except ImportError:  # pragma: no cover - compatibility with older HA cores
    from homeassistant.config_entries import OptionsFlow as OptionsFlowWithReload

from .api import HymerConnectApi, HymerConnectApiError, HymerConnectAuthError
from .const import (
    BRANDS,
    CONF_ACCESS_TOKEN,
    CONF_BRAND,
    CONF_EHG_REFRESH_TOKEN,
    CONF_REFRESH_TOKEN,
    CONF_SHOW_ADMIN_ACTIONS,
    CONF_SHOW_DEBUG_DIAGNOSTICS,
    CONF_SCU_URN,
    CONF_USE_FAHRENHEIT,
    CONF_USE_MILES,
    CONF_VEHICLE_ID,
    CONF_VEHICLE_MODEL,
    CONF_VEHICLE_MODEL_GROUP,
    CONF_VEHICLE_MODEL_YEAR,
    CONF_VEHICLE_NAME,
    CONF_VEHICLE_URN,
    CONF_VIN,
    DOMAIN,
)
from .runtime_metadata import RuntimeMetadataMissingError

_LOGGER = logging.getLogger(__name__)

CONF_SELECTED_VEHICLE = "selected_vehicle"

STEP_USER_DATA_SCHEMA = vol.Schema(
    {
        vol.Required(CONF_BRAND, default="hymer"): vol.In(BRANDS),
        vol.Required(CONF_USERNAME): str,
        vol.Required(CONF_PASSWORD): str,
    }
)

class HymerConnectConfigFlow(ConfigFlow, domain=DOMAIN):
    """Handle a config flow for HYMER Connect."""

    VERSION = 1

    def __init__(self) -> None:
        """Initialize the HYMER Connect config flow."""
        self._pending_entry_data: dict[str, Any] = {}
        self._vehicle_choices: dict[str, dict[str, Any]] = {}

    @staticmethod
    def async_get_options_flow(config_entry: ConfigEntry) -> HymerConnectOptionsFlow:
        """Return the integration options flow."""
        return HymerConnectOptionsFlow(config_entry)

    async def _async_authenticate_api(
        self, brand: str, username: str, password: str
    ) -> tuple[HymerConnectApi, dict[str, str]]:
        """Authenticate and return an initialized API client with tokens."""
        session = async_create_clientsession(self.hass)
        api = HymerConnectApi(session, brand=brand)
        tokens = await api.authenticate(username, password)
        return api, tokens

    @staticmethod
    def _vehicle_unique_id(
        vehicle: dict[str, Any], fallback_scope: str = ""
    ) -> str:
        """Return the best stable identifier for a selected vehicle."""
        for key in ("vehicle_urn", "vin", "scu_urn"):
            value = vehicle.get(key)
            if value not in (None, ""):
                return str(value)
        if vehicle.get("vehicle_id") not in (None, ""):
            if fallback_scope:
                return f"{fallback_scope}:{vehicle['vehicle_id']}"
            return str(vehicle["vehicle_id"])
        return ""

    @staticmethod
    def _vehicle_data(vehicle: dict[str, Any]) -> dict[str, Any]:
        """Return config-entry fields for a selected vehicle."""
        data: dict[str, Any] = {}
        if vehicle.get("vehicle_id") is not None:
            data[CONF_VEHICLE_ID] = vehicle["vehicle_id"]
        if vehicle.get("vehicle_urn"):
            data[CONF_VEHICLE_URN] = vehicle["vehicle_urn"]
        if vehicle.get("scu_urn"):
            data[CONF_SCU_URN] = vehicle["scu_urn"]
        if vehicle.get("vin"):
            data[CONF_VIN] = vehicle["vin"]
        if vehicle.get("name"):
            data[CONF_VEHICLE_NAME] = vehicle["name"]
        if vehicle.get("model"):
            data[CONF_VEHICLE_MODEL] = vehicle["model"]
        if vehicle.get("model_group"):
            data[CONF_VEHICLE_MODEL_GROUP] = vehicle["model_group"]
        if vehicle.get("model_year") is not None:
            data[CONF_VEHICLE_MODEL_YEAR] = vehicle["model_year"]
        return data

    async def _async_resolve_entry_vehicle(
        self, api: HymerConnectApi, entry: ConfigEntry
    ) -> dict[str, Any] | None:
        """Resolve the config entry back to a discovered vehicle."""
        return await api.resolve_vehicle_selection(
            vehicle_id=entry.data.get(CONF_VEHICLE_ID),
            vehicle_urn=entry.data.get(CONF_VEHICLE_URN, ""),
            vin=entry.data.get(CONF_VIN, ""),
            scu_urn=entry.data.get(CONF_SCU_URN, ""),
        )

    async def _async_prepare_entry_identity(
        self, entry: ConfigEntry, stable_unique_id: str
    ) -> None:
        """Ensure the config entry identity matches the selected vehicle."""
        if not stable_unique_id:
            return

        await self.async_set_unique_id(stable_unique_id)

        legacy_account_unique_id = entry.data.get(CONF_USERNAME, "").lower()
        if entry.unique_id == stable_unique_id:
            self._abort_if_unique_id_mismatch()
            return

        if entry.unique_id not in (None, legacy_account_unique_id):
            self._abort_if_unique_id_mismatch()
            return

        self._abort_if_unique_id_configured()
        self.hass.config_entries.async_update_entry(
            entry,
            unique_id=stable_unique_id,
        )

    def _select_vehicle_schema(self, default_token: str = "") -> vol.Schema:
        """Build the vehicle-selection schema."""
        return vol.Schema(
            {
                vol.Required(CONF_SELECTED_VEHICLE): vol.In(
                    {
                        key: vehicle["title"]
                        for key, vehicle in self._vehicle_choices.items()
                    }
                ),
                vol.Optional(
                    CONF_EHG_REFRESH_TOKEN, default=default_token
                ): str,
            }
        )

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Handle the initial step."""
        errors: dict[str, str] = {}

        if user_input is not None:
            try:
                api, tokens = await self._async_authenticate_api(
                    user_input[CONF_BRAND],
                    user_input[CONF_USERNAME],
                    user_input[CONF_PASSWORD],
                )
                vehicles = await api.discover_vehicles()
            except RuntimeMetadataMissingError:
                errors["base"] = "missing_runtime_metadata"
            except HymerConnectAuthError:
                errors["base"] = "invalid_auth"
            except HymerConnectApiError:
                errors["base"] = "cannot_connect"
            except Exception:
                _LOGGER.exception("Unexpected error during authentication")
                errors["base"] = "unknown"
            else:
                if not vehicles:
                    errors["base"] = "no_vehicles"
                else:
                    self._pending_entry_data = {
                        CONF_BRAND: user_input[CONF_BRAND],
                        CONF_USERNAME: user_input[CONF_USERNAME],
                        CONF_PASSWORD: user_input[CONF_PASSWORD],
                        CONF_ACCESS_TOKEN: tokens["access_token"],
                        CONF_REFRESH_TOKEN: tokens["refresh_token"],
                    }
                    self._vehicle_choices = {
                        str(index): vehicle for index, vehicle in enumerate(vehicles)
                    }
                    return await self.async_step_select_vehicle()

        return self.async_show_form(
            step_id="user",
            data_schema=STEP_USER_DATA_SCHEMA,
            errors=errors,
        )

    async def async_step_select_vehicle(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Handle vehicle selection after successful authentication."""
        errors: dict[str, str] = {}

        if not self._vehicle_choices or not self._pending_entry_data:
            return await self.async_step_user()

        if user_input is not None:
            vehicle = self._vehicle_choices.get(user_input[CONF_SELECTED_VEHICLE])
            if vehicle is None:
                errors["base"] = "unknown"
            else:
                unique_id = self._vehicle_unique_id(
                    vehicle,
                    fallback_scope=(
                        f"{self._pending_entry_data[CONF_BRAND]}:"
                        f"{self._pending_entry_data[CONF_USERNAME].lower()}"
                    ),
                )
                if unique_id:
                    await self.async_set_unique_id(unique_id)
                    self._abort_if_unique_id_configured()

                entry_data = {
                    **self._pending_entry_data,
                    **self._vehicle_data(vehicle),
                    CONF_EHG_REFRESH_TOKEN: user_input.get(
                        CONF_EHG_REFRESH_TOKEN, ""
                    ).strip(),
                }
                return self.async_create_entry(
                    title="HYMER Connect",
                    data=entry_data,
                )

        return self.async_show_form(
            step_id="select_vehicle",
            data_schema=self._select_vehicle_schema(),
            errors=errors,
        )

    async def async_step_reauth(
        self, entry_data: dict[str, Any]
    ) -> ConfigFlowResult:
        """Handle reauth when credentials expire."""
        return await self.async_step_reauth_confirm()

    async def async_step_reauth_confirm(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Handle reauth confirmation."""
        errors: dict[str, str] = {}
        reauth_entry = self._get_reauth_entry()

        if user_input is not None:
            try:
                api, tokens = await self._async_authenticate_api(
                    reauth_entry.data[CONF_BRAND],
                    user_input[CONF_USERNAME],
                    user_input[CONF_PASSWORD],
                )
                vehicle = await self._async_resolve_entry_vehicle(api, reauth_entry)
            except HymerConnectAuthError:
                errors["base"] = "invalid_auth"
            except HymerConnectApiError:
                errors["base"] = "cannot_connect"
            except Exception:
                _LOGGER.exception("Unexpected error during reauthentication")
                errors["base"] = "unknown"
            else:
                if vehicle is None:
                    errors["base"] = "wrong_account"
                else:
                    await self._async_prepare_entry_identity(
                        reauth_entry,
                        self._vehicle_unique_id(
                            vehicle,
                            fallback_scope=reauth_entry.data.get(
                                CONF_USERNAME, ""
                            ).lower(),
                        ),
                    )
                    return self.async_update_reload_and_abort(
                        reauth_entry,
                        data_updates={
                            CONF_USERNAME: user_input[CONF_USERNAME],
                            CONF_PASSWORD: user_input[CONF_PASSWORD],
                            CONF_ACCESS_TOKEN: tokens["access_token"],
                            CONF_REFRESH_TOKEN: tokens["refresh_token"],
                            **self._vehicle_data(vehicle),
                        },
                    )

        return self.async_show_form(
            step_id="reauth_confirm",
            data_schema=vol.Schema(
                {
                    vol.Required(
                        CONF_USERNAME,
                        default=reauth_entry.data.get(CONF_USERNAME, ""),
                    ): str,
                    vol.Required(CONF_PASSWORD): str,
                }
            ),
            errors=errors,
        )

    async def async_step_reconfigure(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Allow the user to update the app-extracted remote-access token."""
        errors: dict[str, str] = {}
        entry = self._get_reconfigure_entry()

        if user_input is not None:
            ehg_refresh_token = user_input.get(CONF_EHG_REFRESH_TOKEN, "").strip()
            vehicle: dict[str, Any] | None = None
            try:
                api, tokens = await self._async_authenticate_api(
                    entry.data[CONF_BRAND],
                    entry.data[CONF_USERNAME],
                    entry.data[CONF_PASSWORD],
                )
                vehicle = await self._async_resolve_entry_vehicle(api, entry)
                if vehicle is None:
                    errors["base"] = "vehicle_not_found"
                elif ehg_refresh_token:
                    vehicle_urn = vehicle.get("vehicle_urn", "")
                    if not vehicle_urn:
                        errors["base"] = "vehicle_not_found"
                    else:
                        await api.get_remote_access_token(
                            vehicle_urn, ehg_refresh_token
                        )
            except HymerConnectAuthError:
                errors["base"] = "invalid_auth"
            except HymerConnectApiError:
                if not errors:
                    errors["base"] = "invalid_remote_access_token"
            except Exception:
                _LOGGER.exception("Unexpected error during reconfigure")
                errors["base"] = "unknown"
            else:
                if not errors and vehicle is not None:
                    await self._async_prepare_entry_identity(
                        entry,
                        self._vehicle_unique_id(
                            vehicle,
                            fallback_scope=entry.data.get(CONF_USERNAME, "").lower(),
                        ),
                    )
                    return self.async_update_reload_and_abort(
                        entry,
                        data_updates={
                            CONF_ACCESS_TOKEN: tokens["access_token"],
                            CONF_REFRESH_TOKEN: tokens["refresh_token"],
                            CONF_EHG_REFRESH_TOKEN: ehg_refresh_token,
                            **self._vehicle_data(vehicle),
                        },
                    )

        return self.async_show_form(
            step_id="reconfigure",
            data_schema=vol.Schema(
                {
                    vol.Optional(
                        CONF_EHG_REFRESH_TOKEN,
                        default=entry.data.get(CONF_EHG_REFRESH_TOKEN, ""),
                    ): str,
                }
            ),
            errors=errors,
        )


class HymerConnectOptionsFlow(OptionsFlowWithReload):
    """Handle HYMER Connect options."""

    def __init__(self, config_entry: ConfigEntry) -> None:
        """Initialize the options flow."""
        self._config_entry = config_entry

    async def async_step_init(
        self,
        user_input: dict[str, Any] | None = None,
    ) -> ConfigFlowResult:
        """Manage the integration options."""
        if user_input is not None:
            return self.async_create_entry(title="", data=user_input)

        options = {
            CONF_SHOW_ADMIN_ACTIONS: self._config_entry.options.get(
                CONF_SHOW_ADMIN_ACTIONS,
                False,
            ),
            CONF_SHOW_DEBUG_DIAGNOSTICS: self._config_entry.options.get(
                CONF_SHOW_DEBUG_DIAGNOSTICS,
                False,
            ),
            CONF_USE_MILES: self._config_entry.options.get(
                CONF_USE_MILES,
                False,
            ),
            CONF_USE_FAHRENHEIT: self._config_entry.options.get(
                CONF_USE_FAHRENHEIT,
                False,
            ),
        }
        return self.async_show_form(
            step_id="init",
            data_schema=vol.Schema(
                {
                    vol.Optional(
                        CONF_SHOW_ADMIN_ACTIONS,
                        default=options[CONF_SHOW_ADMIN_ACTIONS],
                    ): bool,
                    vol.Optional(
                        CONF_SHOW_DEBUG_DIAGNOSTICS,
                        default=options[CONF_SHOW_DEBUG_DIAGNOSTICS],
                    ): bool,
                    vol.Optional(
                        CONF_USE_MILES,
                        default=options[CONF_USE_MILES],
                    ): bool,
                    vol.Optional(
                        CONF_USE_FAHRENHEIT,
                        default=options[CONF_USE_FAHRENHEIT],
                    ): bool,
                }
            ),
        )
