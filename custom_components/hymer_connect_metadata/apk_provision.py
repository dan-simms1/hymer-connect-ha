"""Provision the local runtime-metadata pack from a HYMER APK, in Home Assistant.

Downloads a user-supplied APK, reconstructs the catalog object literals and the
OAuth client straight from its Hermes bytecode (no decompiler), runs the
clean-room overlay generator, and writes the eight ``data/*.json`` files -- the
same pack ``scripts/prepare_runtime_metadata.py`` produces offline, but from
within Home Assistant so users never need an external toolchain.

The heavy CPU work (bytecode reconstruction + overlay generation) is offloaded
to the executor; only the download is awaited on the event loop.
"""

from __future__ import annotations

import asyncio
import json
import logging
from pathlib import Path

from homeassistant.core import HomeAssistant
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .runtime_metadata import (
    DATA_DIR,
    INTEGRATION_DIR,
    OAUTH_CLIENT_FILENAME,
    SPECS_DIR,
    invalidate_oauth_client_cache,
)

_LOGGER = logging.getLogger(__name__)

_MAX_APK_BYTES = 300 * 1024 * 1024  # safety cap on the download
_DOWNLOAD_TIMEOUT = 300  # seconds


class ApkProvisionError(RuntimeError):
    """Raised when the metadata pack cannot be provisioned from an APK."""


async def async_provision_metadata_from_apk(
    hass: HomeAssistant,
    apk_url: str,
    *,
    data_dir: Path | None = None,
) -> list[str]:
    """Download ``apk_url`` and write the full metadata pack. Returns filenames."""
    apk_bytes = await _async_download_apk(hass, apk_url)
    target = Path(data_dir) if data_dir is not None else DATA_DIR
    written = await hass.async_add_executor_job(_build_and_write, apk_bytes, target)
    invalidate_oauth_client_cache()
    _LOGGER.info("Provisioned %d metadata files from APK into %s", len(written), target)
    return written


async def _async_download_apk(hass: HomeAssistant, apk_url: str) -> bytes:
    if not apk_url or not apk_url.lower().startswith(("http://", "https://")):
        raise ApkProvisionError("Provide an http(s) URL that returns the .apk file.")
    session = async_get_clientsession(hass)
    chunks: list[bytes] = []
    total = 0
    try:
        async with asyncio.timeout(_DOWNLOAD_TIMEOUT):
            async with session.get(apk_url) as resp:
                if resp.status != 200:
                    raise ApkProvisionError(f"APK download failed: HTTP {resp.status}")
                async for chunk in resp.content.iter_chunked(1 << 16):
                    total += len(chunk)
                    if total > _MAX_APK_BYTES:
                        raise ApkProvisionError("APK exceeds the size limit.")
                    chunks.append(chunk)
    except ApkProvisionError:
        raise
    except (TimeoutError, asyncio.TimeoutError) as err:
        raise ApkProvisionError("APK download timed out.") from err
    except Exception as err:
        raise ApkProvisionError(f"APK download error: {err}") from err
    if not chunks:
        raise ApkProvisionError("APK download returned no data.")
    return b"".join(chunks)


def _build_and_write(apk_bytes: bytes, data_dir: Path) -> list[str]:
    # Imported lazily: metadata_overlay is a heavy module only needed here.
    from .apk_hermes import reconstruct_object_literals
    from .apk_oauth import extract_oauth_client
    from .metadata_overlay import generate_overlay_from_bundle

    try:
        objects = reconstruct_object_literals(apk_bytes)
        (
            components,
            slots,
            controls,
            vehicles,
            scenarios,
            coverage,
            support,
        ) = generate_overlay_from_bundle(
            data_dir / "__no_bundle__",
            INTEGRATION_DIR / "pia_decoder.py",
            SPECS_DIR / "provider_specs.json",
            SPECS_DIR / "template_specs.json",
            objects=objects,
        )
        oauth_client = extract_oauth_client(apk_bytes)
    except Exception as err:
        raise ApkProvisionError(f"Could not build metadata from the APK: {err}") from err

    outputs = {
        "component_kinds.json": components,
        "sensor_labels.json": slots,
        "control_catalog.json": controls,
        "vehicle_catalog.json": vehicles,
        "scenario_catalog.json": scenarios,
        "coverage_audit.json": coverage,
        "support_matrix.json": support,
        OAUTH_CLIENT_FILENAME: oauth_client,
    }
    data_dir.mkdir(parents=True, exist_ok=True)
    for name, payload in outputs.items():
        (data_dir / name).write_text(
            json.dumps(payload, indent=2, sort_keys=True) + "\n"
        )
    return sorted(outputs)
