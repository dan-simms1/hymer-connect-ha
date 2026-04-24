"""Canonical capability resolver for cross-vehicle component families.

Several habitation concepts are implemented by multiple component families.
The SCU only emits the slots for hardware present on the selected vehicle, so
we resolve stable Home Assistant capabilities by inspecting the observed
`(component_id, sensor_id)` tuples and selecting the first matching provider
from a preference-ordered metadata list.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from functools import lru_cache
import logging
from typing import Any, Literal

from .discovery import component_meta
from .runtime_metadata import spec_path

PlatformKey = Literal["sensor", "binary_sensor", "switch"]
WriteStyle = Literal["bool", "string_on_off"]

_VALID_PLATFORMS: tuple[PlatformKey, ...] = ("sensor", "binary_sensor", "switch")
_VALID_WRITE_STYLES: tuple[WriteStyle, ...] = ("bool", "string_on_off")
_PROVIDER_SPECS_PATH = spec_path("provider_specs.json")

_LOGGER = logging.getLogger(__name__)


@dataclass(frozen=True)
class SlotCandidate:
    """A concrete provider slot for a canonical capability."""

    component_id: int
    sensor_id: int
    write_style: WriteStyle | None = None
    transform: str | None = None

    @property
    def key(self) -> tuple[int, int]:
        return (self.component_id, self.sensor_id)


@dataclass(frozen=True)
class CapabilitySpec:
    """Canonical capability definition."""

    key: str
    platform: PlatformKey
    candidates: tuple[SlotCandidate, ...]
    unit: str | None = None
    icon: str | None = None
    sensor_device_class: str | None = None
    binary_device_class: str | None = None
    read_validation_status: str = "inferred"
    write_validation_status: str = "not_applicable"
    evidence_sources: tuple[str, ...] = ()


@dataclass(frozen=True)
class ResolvedCapability:
    """A canonical capability bound to one concrete provider slot."""

    spec: CapabilitySpec
    candidate: SlotCandidate

    @property
    def component_id(self) -> int:
        return self.candidate.component_id

    @property
    def sensor_id(self) -> int:
        return self.candidate.sensor_id

    @property
    def slot(self) -> tuple[int, int]:
        return self.candidate.key

    @property
    def component_name(self) -> str | None:
        comp = component_meta(self.component_id)
        return comp.name if comp else None


def _require_string(payload: dict[str, Any], key: str) -> str:
    value = payload.get(key)
    if not isinstance(value, str) or not value:
        raise ValueError(f"provider_specs.json entry missing valid '{key}'")
    return value


def _optional_string(payload: dict[str, Any], key: str) -> str | None:
    value = payload.get(key)
    if value is None:
        return None
    if not isinstance(value, str) or not value:
        raise ValueError(f"provider_specs.json entry has invalid '{key}'")
    return value


def _string_tuple(payload: dict[str, Any], key: str) -> tuple[str, ...]:
    value = payload.get(key)
    if value is None:
        return ()
    if not isinstance(value, list) or not all(
        isinstance(item, str) and item for item in value
    ):
        raise ValueError(f"provider_specs.json entry has invalid '{key}'")
    return tuple(value)


def _provider_defaults(payload: dict[str, Any]) -> dict[str, Any]:
    defaults = payload.get("defaults")
    if not isinstance(defaults, dict):
        defaults = {}
    return {
        "read_validation_status": _optional_string(defaults, "read_validation_status")
        or "inferred",
        "write_validation_status": _optional_string(defaults, "write_validation_status")
        or "not_applicable",
        "evidence_sources": _string_tuple(defaults, "evidence_sources"),
    }


def _candidate_from_dict(payload: dict[str, Any]) -> SlotCandidate:
    if not isinstance(payload, dict):
        raise ValueError("provider_specs.json candidate must be an object")
    component_id = int(payload["component_id"])
    sensor_id = int(payload["sensor_id"])
    write_style = _optional_string(payload, "write_style")
    if write_style is not None and write_style not in _VALID_WRITE_STYLES:
        raise ValueError(
            f"provider_specs.json candidate has unsupported write_style '{write_style}'"
        )
    return SlotCandidate(
        component_id=component_id,
        sensor_id=sensor_id,
        write_style=write_style,
        transform=_optional_string(payload, "transform"),
    )


def _spec_from_dict(payload: dict[str, Any], defaults: dict[str, Any]) -> CapabilitySpec:
    if not isinstance(payload, dict):
        raise ValueError("provider_specs.json capability entry must be an object")
    key = _require_string(payload, "key")
    platform = _require_string(payload, "platform")
    if platform not in _VALID_PLATFORMS:
        raise ValueError(
            f"provider_specs.json capability '{key}' has unsupported platform '{platform}'"
        )
    candidates_raw = payload.get("candidates")
    if not isinstance(candidates_raw, list) or not candidates_raw:
        raise ValueError(
            f"provider_specs.json capability '{key}' must define non-empty candidates"
        )
    return CapabilitySpec(
        key=key,
        platform=platform,
        candidates=tuple(_candidate_from_dict(candidate) for candidate in candidates_raw),
        unit=_optional_string(payload, "unit"),
        icon=_optional_string(payload, "icon"),
        sensor_device_class=_optional_string(payload, "sensor_device_class"),
        binary_device_class=_optional_string(payload, "binary_device_class"),
        read_validation_status=(
            _optional_string(payload, "read_validation_status")
            or defaults["read_validation_status"]
        ),
        write_validation_status=(
            _optional_string(payload, "write_validation_status")
            or defaults["write_validation_status"]
        ),
        evidence_sources=_string_tuple(payload, "evidence_sources")
        or defaults["evidence_sources"],
    )


def _validate_unique_candidate_slots(
    specs: tuple[CapabilitySpec, ...],
) -> tuple[CapabilitySpec, ...]:
    seen: dict[tuple[int, int], str] = {}
    for spec in specs:
        for candidate in spec.candidates:
            existing = seen.get(candidate.key)
            if existing is not None and existing != spec.key:
                raise ValueError(
                    "provider_specs.json reuses slot "
                    f"{candidate.key} for both '{existing}' and '{spec.key}'"
                )
            seen[candidate.key] = spec.key
    return specs


@lru_cache(maxsize=1)
def all_capability_specs() -> tuple[CapabilitySpec, ...]:
    """Return all canonical capability specs."""
    payload = json.loads(_PROVIDER_SPECS_PATH.read_text())
    capabilities_raw = payload.get("capabilities")
    if not isinstance(capabilities_raw, list):
        raise ValueError("provider_specs.json must define a 'capabilities' list")
    defaults = _provider_defaults(payload)
    return _validate_unique_candidate_slots(
        tuple(_spec_from_dict(item, defaults) for item in capabilities_raw)
    )


@lru_cache(maxsize=None)
def capability_spec(key: str) -> CapabilitySpec | None:
    """Return one canonical capability spec by key."""
    for spec in all_capability_specs():
        if spec.key == key:
            return spec
    return None


def candidate_slots_for_key(key: str) -> tuple[tuple[int, int], ...]:
    """Return the provider slots for one canonical capability key."""
    spec = capability_spec(key)
    if spec is None:
        return ()
    return tuple(candidate.key for candidate in spec.candidates)


def main_switch_slots() -> frozenset[tuple[int, int]]:
    """Return all slots that participate in canonical main-switch resolution."""
    return frozenset(candidate_slots_for_key("main_switch"))


def present_candidate_slots(
    capability: ResolvedCapability,
    observed: set[tuple[int, int]],
) -> tuple[tuple[int, int], ...]:
    """Return every observed candidate slot for a resolved capability."""
    return tuple(
        candidate.key
        for candidate in capability.spec.candidates
        if candidate.key in observed
    )


def resolved_capabilities(
    observed: set[tuple[int, int]],
    platform: PlatformKey,
) -> list[ResolvedCapability]:
    """Resolve canonical capabilities for one HA platform.

    Canonical entities are authoritative for these concepts. The raw per-slot
    layer remains a fallback for everything else.
    """
    resolved: list[ResolvedCapability] = []
    for spec in all_capability_specs():
        if spec.platform != platform:
            continue
        for candidate in spec.candidates:
            if candidate.key not in observed:
                continue
            resolved.append(
                ResolvedCapability(
                    spec=spec,
                    candidate=candidate,
                )
            )
            alternates = tuple(
                alt.key
                for alt in spec.candidates
                if alt.key in observed and alt.key != candidate.key
            )
            if alternates:
                _LOGGER.info(
                    "Canonical capability %s matched multiple providers; using %s and suppressing generic fallbacks for %s",
                    spec.key,
                    candidate.key,
                    alternates,
                )
            break
    return resolved


def all_resolved_capabilities(
    observed: set[tuple[int, int]],
) -> list[ResolvedCapability]:
    """Resolve canonical capabilities across every platform."""
    resolved: list[ResolvedCapability] = []
    for platform in _VALID_PLATFORMS:
        resolved.extend(resolved_capabilities(observed, platform))
    return resolved


def canonical_claimed_slots(
    observed: set[tuple[int, int]],
) -> set[tuple[int, int]]:
    """Return all observed slots claimed by canonical capability resolution."""
    claimed: set[tuple[int, int]] = set()
    for capability in all_resolved_capabilities(observed):
        claimed.update(present_candidate_slots(capability, observed))
    return claimed


def invalidate_capability_cache() -> None:
    """Clear cached provider metadata so entry reloads pick up spec updates."""
    capability_spec.cache_clear()
    all_capability_specs.cache_clear()


def warm_capability_cache() -> None:
    """Load provider specs into cache off the event loop."""
    all_capability_specs()
