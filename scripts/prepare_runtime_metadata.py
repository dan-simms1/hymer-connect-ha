#!/usr/bin/env python3
"""Prepare local HYMER runtime metadata from an APK or expanded bundle.js."""

from __future__ import annotations

import argparse
import base64
import binascii
import json
import re
import shutil
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any
from zipfile import ZIP_DEFLATED, ZipFile

try:
    from scripts.generate_cleanroom_registry import (
        DEFAULT_PIA_DECODER,
        DEFAULT_PROVIDER_SPECS,
        DEFAULT_TEMPLATE_SPECS,
        generate_overlay_from_bundle,
    )
except ImportError:  # pragma: no cover - used when the script is run directly
    from generate_cleanroom_registry import (
        DEFAULT_PIA_DECODER,
        DEFAULT_PROVIDER_SPECS,
        DEFAULT_TEMPLATE_SPECS,
        generate_overlay_from_bundle,
    )


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_WORK_DIR = ROOT / "source" / "runtime_metadata"
DEFAULT_DATA_DIR = ROOT / "custom_components" / "hymer_connect_metadata" / "data"
APK_ASSET_PATH = "assets/index.android.bundle"
ZIP_INSTALL_ROOT = Path("custom_components") / "hymer_connect_metadata" / "data"
OAUTH_CLIENT_FILENAME = "oauth_client.json"
CLIENT_USERNAME_PATTERN = re.compile(r"'CLIENT_USERNAME':\s*'([^']+)'")
CLIENT_PASSWORD_PATTERN = re.compile(r"'CLIENT_PASSWORD':\s*'([^']+)'")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Generate the local HYMER runtime metadata pack from a lawfully "
            "obtained APK or expanded bundle.js."
        )
    )
    parser.add_argument(
        "--apk-url",
        help=(
            "Direct download URL of a lawfully obtained HYMER Android APK "
            "(the URL should return the .apk file itself, not an HTML page)"
        ),
    )
    parser.add_argument(
        "--apk-path",
        type=Path,
        help="Path to a local HYMER Android APK (usually easiest if you already downloaded it)",
    )
    parser.add_argument(
        "--bundle-js",
        type=Path,
        help="Path to an expanded bundle.js file if the APK contains Hermes bytecode",
    )
    parser.add_argument(
        "--bundle-js-url",
        help="URL of an expanded bundle.js file if the APK contains Hermes bytecode",
    )
    parser.add_argument("--work-dir", type=Path, default=DEFAULT_WORK_DIR)
    target_group = parser.add_mutually_exclusive_group()
    target_group.add_argument(
        "--data-dir",
        type=Path,
        help=(
            "Directory to receive the generated JSON files. Defaults to the "
            "repo-local custom_components/hymer_connect_metadata/data directory."
        ),
    )
    target_group.add_argument(
        "--ha-config-dir",
        type=Path,
        help=(
            "Home Assistant config directory. Metadata will be installed into "
            "custom_components/hymer_connect_metadata/data under this directory."
        ),
    )
    parser.add_argument(
        "--zip-out",
        type=Path,
        help=(
            "Optional transfer zip. The archive preserves the "
            "custom_components/hymer_connect_metadata/data layout so it can be "
            "extracted directly into a Home Assistant config directory."
        ),
    )
    parser.add_argument("--pia-decoder", type=Path, default=DEFAULT_PIA_DECODER)
    parser.add_argument("--provider-specs", type=Path, default=DEFAULT_PROVIDER_SPECS)
    parser.add_argument("--template-specs", type=Path, default=DEFAULT_TEMPLATE_SPECS)
    return parser


def _download(url: str, destination: Path) -> Path:
    parsed = urllib.parse.urlparse(url)
    if parsed.scheme not in {"http", "https"}:
        raise ValueError("Only http:// and https:// URLs are supported for downloads")
    destination.parent.mkdir(parents=True, exist_ok=True)
    with urllib.request.urlopen(url) as response, destination.open("wb") as output:
        shutil.copyfileobj(response, output)
    return destination


def _extract_bundle_from_apk(apk_path: Path, destination: Path) -> Path:
    destination.parent.mkdir(parents=True, exist_ok=True)
    with ZipFile(apk_path) as archive:
        destination.write_bytes(archive.read(APK_ASSET_PATH))
    return destination


def _is_probably_text_bundle(path: Path) -> bool:
    sample = path.read_bytes()[:4096]
    if b"Hermes" in sample[:128]:
        return False
    try:
        decoded = sample.decode("utf-8")
    except UnicodeDecodeError:
        return False
    printable = sum(char.isprintable() or char in "\r\n\t" for char in decoded)
    return printable >= int(len(decoded) * 0.9)


def _resolve_expanded_bundle(args: argparse.Namespace, work_dir: Path) -> Path:
    if args.bundle_js:
        return args.bundle_js.resolve()
    if args.bundle_js_url:
        return _download(args.bundle_js_url, work_dir / "bundle.js").resolve()

    apk_path: Path | None = None
    if args.apk_path:
        apk_path = args.apk_path.resolve()
    elif args.apk_url:
        apk_path = _download(args.apk_url, work_dir / "source.apk").resolve()

    if apk_path is None:
        raise RuntimeError("Provide --apk-url, --apk-path, --bundle-js, or --bundle-js-url.")

    raw_bundle = _extract_bundle_from_apk(apk_path, work_dir / "index.android.bundle").resolve()
    if _is_probably_text_bundle(raw_bundle):
        return raw_bundle

    raise RuntimeError(
        "The APK contains a Hermes bytecode bundle, not an expanded bundle.js. "
        "Download the APK with this script, then rerun with either "
        "`--bundle-js /path/to/bundle.js` or `--bundle-js-url <url>` pointing "
        "to an expanded bundle.js produced on your own machine or by a "
        "community metadata workflow."
    )


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n")


def _decode_base64_text(encoded: str, *, field_name: str) -> str:
    try:
        return base64.b64decode(encoded, validate=True).decode("utf-8")
    except (binascii.Error, UnicodeDecodeError) as err:
        raise RuntimeError(
            f"Could not decode {field_name} from the expanded HYMER app bundle."
        ) from err


def _extract_oauth_client_payload(bundle_path: Path) -> dict[str, Any]:
    bundle_text = bundle_path.read_text(errors="ignore")
    username_match = CLIENT_USERNAME_PATTERN.search(bundle_text)
    password_match = CLIENT_PASSWORD_PATTERN.search(bundle_text)
    if username_match is None or password_match is None:
        raise RuntimeError(
            "Could not extract the local OAuth client auth material from the "
            "expanded HYMER app bundle. Provide the correct expanded bundle.js "
            "for the same app release as the APK."
        )

    username = _decode_base64_text(
        username_match.group(1),
        field_name="CLIENT_USERNAME",
    )
    password = _decode_base64_text(
        password_match.group(1),
        field_name="CLIENT_PASSWORD",
    )
    basic_auth = base64.b64encode(f"{username}:{password}".encode("utf-8")).decode(
        "ascii"
    )
    return {
        "_comment": (
            "Locally generated OAuth client auth derived from the user's own "
            "HYMER app artefact. Sensitive local file. Do not share or publish."
        ),
        "authorization_header": f"Basic {basic_auth}",
    }


def _resolve_output_dir(args: argparse.Namespace) -> Path:
    if args.ha_config_dir:
        return (
            args.ha_config_dir.resolve()
            / "custom_components"
            / "hymer_connect_metadata"
            / "data"
        )
    if args.data_dir:
        return args.data_dir.resolve()
    if args.zip_out:
        return args.work_dir.resolve() / "generated_data"
    return DEFAULT_DATA_DIR.resolve()


def _write_zip(zip_path: Path, outputs: dict[str, dict[str, Any]]) -> None:
    zip_path.parent.mkdir(parents=True, exist_ok=True)
    readme = (
        "Extract this archive into your Home Assistant config directory.\n"
        "The JSON files are already laid out under "
        "custom_components/hymer_connect_metadata/data/.\n"
        "This archive is local-only and contains app-derived auth material in "
        "oauth_client.json. Do not share or publish it.\n"
    )
    with ZipFile(zip_path, "w", compression=ZIP_DEFLATED) as archive:
        archive.writestr("README.txt", readme)
        for filename, payload in outputs.items():
            archive.writestr(
                str(ZIP_INSTALL_ROOT / filename),
                json.dumps(payload, indent=2, sort_keys=True) + "\n",
            )


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()

    work_dir = args.work_dir.resolve()
    bundle_path = _resolve_expanded_bundle(args, work_dir)

    (
        generated_components,
        generated_slots,
        generated_controls,
        generated_vehicles,
        generated_scenarios,
        generated_coverage,
        generated_support_matrix,
    ) = generate_overlay_from_bundle(
        bundle_path,
        args.pia_decoder.resolve(),
        args.provider_specs.resolve(),
        args.template_specs.resolve(),
    )
    generated_oauth_client = _extract_oauth_client_payload(bundle_path)

    outputs = {
        "component_kinds.json": generated_components,
        "sensor_labels.json": generated_slots,
        "control_catalog.json": generated_controls,
        "vehicle_catalog.json": generated_vehicles,
        "scenario_catalog.json": generated_scenarios,
        "coverage_audit.json": generated_coverage,
        "support_matrix.json": generated_support_matrix,
        OAUTH_CLIENT_FILENAME: generated_oauth_client,
    }
    data_dir = _resolve_output_dir(args)
    for filename, payload in outputs.items():
        _write_json(data_dir / filename, payload)

    if args.zip_out:
        zip_path = args.zip_out.resolve()
        _write_zip(zip_path, outputs)
        print(f"Wrote metadata transfer pack to {zip_path}")

    print(f"Prepared runtime metadata in {data_dir}")
    print(f"Expanded bundle source: {bundle_path}")
    print(
        "Generated files:",
        ", ".join(sorted(outputs)),
    )
    if args.ha_config_dir:
        print("Home Assistant install target: custom_components/hymer_connect_metadata/data")
        print("Restart Home Assistant or reload the HYMER Connect Metadata integration.")
    elif args.zip_out:
        print(
            "Extract the zip into the Home Assistant config directory so the "
            "files land under custom_components/hymer_connect_metadata/data/."
        )


if __name__ == "__main__":
    main()
