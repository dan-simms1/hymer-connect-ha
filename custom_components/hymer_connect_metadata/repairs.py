"""Repair flows: self-provision the runtime metadata pack from a HYMER APK.

Turns the "runtime metadata is missing" repair issue into a fixable flow: the
user pastes a HYMER APK URL and Home Assistant downloads it, reconstructs the
catalogs and OAuth client from the Hermes bytecode in-process (no external
toolchain), writes the pack, and reloads the integration.
"""

from __future__ import annotations

import logging

import voluptuous as vol
from homeassistant.components.repairs import RepairsFlow
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResult

from .apk_provision import ApkProvisionError, async_provision_metadata_from_apk
from .const import CONF_APK_URL, DOMAIN

_LOGGER = logging.getLogger(__name__)


class MissingRuntimeMetadataRepairFlow(RepairsFlow):
    """Ask for an APK URL and provision the metadata pack from it."""

    async def async_step_init(
        self, user_input: dict | None = None
    ) -> FlowResult:
        return await self.async_step_provision()

    async def async_step_provision(
        self, user_input: dict | None = None
    ) -> FlowResult:
        errors: dict[str, str] = {}
        if user_input is not None:
            apk_url = str(user_input.get(CONF_APK_URL, "") or "").strip()
            if not apk_url:
                errors[CONF_APK_URL] = "apk_url_required"
            else:
                try:
                    await async_provision_metadata_from_apk(self.hass, apk_url)
                except ApkProvisionError:
                    _LOGGER.warning(
                        "Metadata provisioning from APK failed", exc_info=True
                    )
                    errors["base"] = "provision_failed"
                else:
                    # Reload so the retrying entry picks up the freshly written pack.
                    for entry in self.hass.config_entries.async_entries(DOMAIN):
                        self.hass.async_create_task(
                            self.hass.config_entries.async_reload(entry.entry_id)
                        )
                    return self.async_create_entry(title="", data={})

        return self.async_show_form(
            step_id="provision",
            data_schema=vol.Schema({vol.Required(CONF_APK_URL): str}),
            errors=errors,
        )


async def async_create_fix_flow(
    hass: HomeAssistant,
    issue_id: str,
    data: dict | None,
) -> RepairsFlow:
    """Return the repair flow for a fixable issue."""
    return MissingRuntimeMetadataRepairFlow()
