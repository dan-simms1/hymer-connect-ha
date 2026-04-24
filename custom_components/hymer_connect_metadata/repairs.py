"""Repair issue helpers for integration setup problems."""

from __future__ import annotations

from homeassistant.core import HomeAssistant
from homeassistant.helpers import issue_registry as ir

from .const import DOMAIN
from .runtime_metadata import default_prepare_command


MISSING_RUNTIME_METADATA_ISSUE_ID = "missing_runtime_metadata"
REPOSITORY_URL = "https://github.com/dan-simms1/hymer-connect-ha"
RUNTIME_METADATA_DOCS_URL = f"{REPOSITORY_URL}/blob/main/docs/runtime-metadata.md"


def async_create_missing_runtime_metadata_issue(
    hass: HomeAssistant,
    missing_files: tuple[str, ...],
    prepare_command: str | None = None,
) -> None:
    ir.async_create_issue(
        hass,
        DOMAIN,
        MISSING_RUNTIME_METADATA_ISSUE_ID,
        is_fixable=False,
        severity=ir.IssueSeverity.ERROR,
        learn_more_url=RUNTIME_METADATA_DOCS_URL,
        translation_key=MISSING_RUNTIME_METADATA_ISSUE_ID,
        translation_placeholders={
            "missing_files": ", ".join(missing_files),
            "prepare_command": prepare_command or default_prepare_command(),
        },
    )


def async_delete_missing_runtime_metadata_issue(hass: HomeAssistant) -> None:
    ir.async_delete_issue(hass, DOMAIN, MISSING_RUNTIME_METADATA_ISSUE_ID)
