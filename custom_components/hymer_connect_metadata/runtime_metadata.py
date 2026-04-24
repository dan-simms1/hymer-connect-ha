"""Helpers for local runtime metadata and tracked integration specs."""

from __future__ import annotations

import json
from pathlib import Path


INTEGRATION_DIR = Path(__file__).resolve().parent
DATA_DIR = INTEGRATION_DIR / "data"
SPECS_DIR = INTEGRATION_DIR / "specs"
OAUTH_CLIENT_FILENAME = "oauth_client.json"

REQUIRED_RUNTIME_DATA_FILES: tuple[str, ...] = (
    "component_kinds.json",
    "sensor_labels.json",
    "control_catalog.json",
    "vehicle_catalog.json",
    "scenario_catalog.json",
    "coverage_audit.json",
    "support_matrix.json",
    OAUTH_CLIENT_FILENAME,
)

REQUIRED_SPEC_FILES: tuple[str, ...] = (
    "provider_specs.json",
    "template_specs.json",
)
DEFAULT_HOME_ASSISTANT_CONFIG_DIR = "/config"


class RuntimeMetadataMissingError(RuntimeError):
    """Raised when required local metadata files are not available."""

    def __init__(
        self,
        missing_files: tuple[str, ...],
        prepare_command: str,
    ) -> None:
        self.missing_files = missing_files
        self.prepare_command = prepare_command
        super().__init__(
            "HYMER runtime metadata is not available. Missing files: "
            + ", ".join(missing_files)
            + ". From a full checkout of this repository on any machine, "
            + "prepare the local metadata pack with `"
            + prepare_command
            + "`, then extract the generated zip into `"
            + DEFAULT_HOME_ASSISTANT_CONFIG_DIR
            + "/custom_components/hymer_connect_metadata/data/`. Supply "
            + "`--bundle-js` or `--bundle-js-url` when the APK contains a "
            + "Hermes bytecode bundle."
        )


def data_path(filename: str) -> Path:
    return DATA_DIR / filename


def spec_path(filename: str) -> Path:
    return SPECS_DIR / filename


def default_prepare_command() -> str:
    return "python3 scripts/prepare_runtime_metadata.py --apk-url <apk-url> --zip-out hymer_connect_metadata_runtime_metadata.zip"


def load_oauth_basic_auth_header() -> str:
    """Return the locally generated OAuth client Basic auth header."""
    file_path = data_path(OAUTH_CLIENT_FILENAME)
    missing_files = (f"data/{OAUTH_CLIENT_FILENAME}",)
    try:
        payload = json.loads(file_path.read_text())
    except (FileNotFoundError, json.JSONDecodeError, OSError) as err:
        raise RuntimeMetadataMissingError(
            missing_files,
            default_prepare_command(),
        ) from err

    header = payload.get("authorization_header")
    if not isinstance(header, str) or not header.startswith("Basic "):
        raise RuntimeMetadataMissingError(
            missing_files,
            default_prepare_command(),
        )
    return header


def missing_runtime_data_files() -> tuple[str, ...]:
    return tuple(
        filename
        for filename in REQUIRED_RUNTIME_DATA_FILES
        if not data_path(filename).exists()
    )


def missing_spec_files() -> tuple[str, ...]:
    return tuple(
        filename
        for filename in REQUIRED_SPEC_FILES
        if not spec_path(filename).exists()
    )


def ensure_runtime_metadata_present() -> None:
    missing_specs = missing_spec_files()
    missing_data = missing_runtime_data_files()
    if not missing_specs and not missing_data:
        return

    missing = [*(f"specs/{name}" for name in missing_specs), *(f"data/{name}" for name in missing_data)]
    raise RuntimeMetadataMissingError(
        tuple(missing),
        default_prepare_command(),
    )
