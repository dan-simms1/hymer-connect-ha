"""Thin shim: the implementation now ships inside the integration package.

Kept so the offline dev pipeline (prepare_runtime_metadata.py, tests) can import
the reusable logic. A fake ``custom_components.hymer_connect_metadata`` package
lets us load the submodules without executing the integration's HA-heavy
``__init__``.
"""
from __future__ import annotations

import sys
import types
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
if "custom_components" not in sys.modules:
    _cc = types.ModuleType("custom_components")
    _cc.__path__ = [str(_ROOT / "custom_components")]
    sys.modules["custom_components"] = _cc
if "custom_components.hymer_connect_metadata" not in sys.modules:
    _ig = types.ModuleType("custom_components.hymer_connect_metadata")
    _ig.__path__ = [str(_ROOT / "custom_components" / "hymer_connect_metadata")]
    sys.modules["custom_components.hymer_connect_metadata"] = _ig

from custom_components.hymer_connect_metadata.apk_oauth import *
from custom_components.hymer_connect_metadata.apk_oauth import (  # noqa: F401
    OAuthExtractionError,
    _b64_decode,
    _read_bundle,
    _StringTable,
    extract_oauth_client,
    extract_oauth_client_from_path,
)
