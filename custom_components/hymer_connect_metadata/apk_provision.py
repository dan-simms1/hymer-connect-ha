"""Provision the local runtime-metadata pack from a HYMER APK, in Home Assistant.

Downloads a user-supplied APK, reconstructs the catalog object literals and the
OAuth client straight from its Hermes bytecode (no decompiler), runs the
clean-room overlay generator, and writes the eight ``data/*.json`` files -- the
same pack ``scripts/prepare_runtime_metadata.py`` produces offline, but from
within Home Assistant so users never need an external toolchain.

The APK is untrusted input fetched from a user-supplied URL, so the whole path
is bounded and fail-closed: HTTPS-only (redirects included), the download is
streamed to a size-capped spooled file (never held in memory twice), the pack
is built + validated in full before anything is published, and the eight files
are swapped into place transactionally (with rollback) under a lock so a
mid-write failure can never leave a half-old/half-new pack behind. The heavy CPU
work (unzip + reconstruction + overlay) runs in the executor.
"""

from __future__ import annotations

import asyncio
import base64
import json
import logging
import os
import tempfile
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

_MAX_APK_BYTES = 300 * 1024 * 1024  # hard cap on the download
_SPOOL_MAX_BYTES = 32 * 1024 * 1024  # keep in RAM up to here, then spill to disk
_DOWNLOAD_TIMEOUT = 300  # seconds

# One provisioning at a time per Home Assistant instance: the repair flow and
# the options action both write the same pack, and their file swaps must not
# interleave.
_PROVISION_LOCK = asyncio.Lock()


class ApkProvisionError(RuntimeError):
    """Raised when the metadata pack cannot be provisioned from an APK."""


async def async_provision_metadata_from_apk(
    hass: HomeAssistant,
    apk_url: str,
    *,
    data_dir: Path | None = None,
) -> list[str]:
    """Download ``apk_url`` and write the full metadata pack. Returns filenames."""
    async with _PROVISION_LOCK:
        apk_source = await _async_download_apk(hass, apk_url)
        try:
            target = Path(data_dir) if data_dir is not None else DATA_DIR
            written = await hass.async_add_executor_job(
                _build_and_write, apk_source, target
            )
        finally:
            apk_source.close()
        invalidate_oauth_client_cache()
    _LOGGER.info(
        "Provisioned %d metadata files from APK into %s", len(written), target
    )
    return written


async def _async_download_apk(hass: HomeAssistant, apk_url: str):
    """Stream the APK to a size-capped spooled temp file. Returns the open file."""
    # Require HTTPS: the APK is the trust root for the whole pack (it yields the
    # OAuth client and every catalog), so it must not be fetched over a channel
    # a network attacker can tamper with.
    if not apk_url or not apk_url.lower().startswith("https://"):
        raise ApkProvisionError("Provide an https URL that returns the .apk file.")
    session = async_get_clientsession(hass)
    spool = tempfile.SpooledTemporaryFile(max_size=_SPOOL_MAX_BYTES, mode="w+b")
    total = 0
    try:
        async with asyncio.timeout(_DOWNLOAD_TIMEOUT):
            async with session.get(apk_url) as resp:
                # A redirect must not downgrade the scheme: reject any hop (or the
                # final URL) that is not HTTPS.
                for hop in (*resp.history, resp):
                    if getattr(hop.url, "scheme", "https") != "https":
                        raise ApkProvisionError(
                            "APK URL redirected to a non-HTTPS location."
                        )
                if resp.status != 200:
                    raise ApkProvisionError(f"APK download failed: HTTP {resp.status}")
                async for chunk in resp.content.iter_chunked(1 << 16):
                    total += len(chunk)
                    if total > _MAX_APK_BYTES:
                        raise ApkProvisionError("APK exceeds the size limit.")
                    spool.write(chunk)
    except ApkProvisionError:
        spool.close()
        raise
    except (TimeoutError, asyncio.TimeoutError) as err:
        spool.close()
        raise ApkProvisionError("APK download timed out.") from err
    except Exception as err:  # noqa: BLE001 - surface any client error uniformly
        spool.close()
        raise ApkProvisionError(f"APK download error: {err}") from err
    if not total:
        spool.close()
        raise ApkProvisionError("APK download returned no data.")
    spool.seek(0)
    return spool


def _validate_pack(outputs: dict[str, object]) -> None:
    """Reject an implausible pack before it can overwrite a working one.

    Generated payloads are wrappers like ``{"_comment": ..., "components": {}}``,
    so the *inner* catalogs -- not just the wrapper -- must be non-empty, and the
    OAuth header must decode to a real ``username:password`` pair.
    """

    def _nonempty_nested(name: str, key: str) -> None:
        payload = outputs.get(name)
        inner = payload.get(key) if isinstance(payload, dict) else None
        if not isinstance(inner, dict) or not inner:
            raise ApkProvisionError(f"APK produced an empty {name}.")

    _nonempty_nested("component_kinds.json", "components")
    _nonempty_nested("sensor_labels.json", "slots")
    _nonempty_nested("vehicle_catalog.json", "models")

    oauth = outputs.get(OAUTH_CLIENT_FILENAME)
    header = oauth.get("authorization_header") if isinstance(oauth, dict) else None
    if not isinstance(header, str) or not header.startswith("Basic "):
        raise ApkProvisionError("APK produced no OAuth client.")
    try:
        creds = base64.b64decode(header[6:], validate=True).decode("utf-8")
    except Exception as err:  # noqa: BLE001 - malformed base64/text
        raise ApkProvisionError("APK produced a malformed OAuth client.") from err
    user, sep, password = creds.partition(":")
    if not sep or not user or not password:
        raise ApkProvisionError("APK produced an incomplete OAuth client.")

    for name in (
        "control_catalog.json",
        "scenario_catalog.json",
        "coverage_audit.json",
        "support_matrix.json",
    ):
        if not isinstance(outputs.get(name), dict):
            raise ApkProvisionError(f"APK produced an invalid {name}.")


def _atomic_publish(data_dir: Path, serialized: dict[str, str]) -> None:
    """Stage every file, then swap them into place with rollback on failure."""
    staged: dict[str, Path] = {}
    backups: dict[str, bytes] = {}
    replaced: list[str] = []
    try:
        for name, text in serialized.items():
            tmp = data_dir / f".{name}.new"
            tmp.write_text(text)
            staged[name] = tmp
        for name, tmp in staged.items():
            final = data_dir / name
            if final.exists():
                backups[name] = final.read_bytes()
            os.replace(tmp, final)
            replaced.append(name)
    except (OSError, TypeError) as err:
        # Roll back any files already swapped, then drop leftover temp files. A
        # file that had no prior version (fresh install) is removed rather than
        # restored, so rollback never leaves a partial new pack behind.
        for name in replaced:
            final = data_dir / name
            try:
                if name in backups:
                    final.write_bytes(backups[name])
                else:
                    final.unlink(missing_ok=True)
            except OSError:
                _LOGGER.error("Failed to roll back %s after a provisioning error", name)
        for tmp in staged.values():
            try:
                tmp.unlink(missing_ok=True)
            except OSError:
                pass
        raise ApkProvisionError(f"Could not write metadata pack: {err}") from err


def _build_and_write(apk_source, data_dir: Path) -> list[str]:
    # Imported lazily: metadata_overlay is a heavy module only needed here.
    from .apk_hermes import reconstruct_object_literals_from_bundle
    from .apk_oauth import extract_oauth_client_from_objects, read_bundle_asset
    from .metadata_overlay import generate_overlay_from_bundle

    try:
        # Unzip the bundle once (bounded), reconstruct once, and reuse the
        # reconstructed objects for both the catalogs and the OAuth client.
        bundle = read_bundle_asset(apk_source)
        objects = reconstruct_object_literals_from_bundle(bundle)
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
        oauth_client = extract_oauth_client_from_objects(objects)
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

    # Validate + serialise the whole pack before touching disk, so any failure
    # aborts before a single file is replaced.
    _validate_pack(outputs)
    try:
        serialized = {
            name: json.dumps(payload, indent=2, sort_keys=True) + "\n"
            for name, payload in outputs.items()
        }
    except TypeError as err:
        raise ApkProvisionError(f"Metadata pack is not serialisable: {err}") from err

    try:
        data_dir.mkdir(parents=True, exist_ok=True)
    except OSError as err:
        raise ApkProvisionError(f"Could not create the data directory: {err}") from err
    _atomic_publish(data_dir, serialized)
    return sorted(outputs)
