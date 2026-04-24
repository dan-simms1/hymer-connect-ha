"""Test package bootstrap for lightweight unittest-based coverage."""

from __future__ import annotations

import importlib
import sys
import types
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
CUSTOM_COMPONENTS_DIR = ROOT / "custom_components"
INTEGRATION_DIR = CUSTOM_COMPONENTS_DIR / "hymer_connect_metadata"
FIXTURE_RUNTIME_METADATA_DIR = ROOT / "tests" / "fixtures" / "runtime_metadata"


if "custom_components" not in sys.modules:
    custom_components = types.ModuleType("custom_components")
    custom_components.__path__ = [str(CUSTOM_COMPONENTS_DIR)]
    sys.modules["custom_components"] = custom_components
if "custom_components.hymer_connect_metadata" not in sys.modules:
    integration = types.ModuleType("custom_components.hymer_connect_metadata")
    integration.__path__ = [str(INTEGRATION_DIR)]
    sys.modules["custom_components.hymer_connect_metadata"] = integration

runtime_metadata = importlib.import_module("custom_components.hymer_connect_metadata.runtime_metadata")
runtime_metadata.DATA_DIR = FIXTURE_RUNTIME_METADATA_DIR
