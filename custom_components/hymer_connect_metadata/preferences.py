"""Config-entry display and visibility preferences."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from homeassistant.const import UnitOfTemperature

from .const import (
    CONF_SHOW_ADMIN_ACTIONS,
    CONF_SHOW_DEBUG_DIAGNOSTICS,
    CONF_USE_FAHRENHEIT,
    CONF_USE_MILES,
)

_MILES_PER_KILOMETER = 0.621371192237334


def _entry_options(entry: Any) -> Mapping[str, Any]:
    options = getattr(entry, "options", None)
    return options if isinstance(options, Mapping) else {}


def debug_diagnostics_enabled(entry: Any) -> bool:
    """Return whether debug-only diagnostic entities should be shown."""
    return bool(_entry_options(entry).get(CONF_SHOW_DEBUG_DIAGNOSTICS, False))


def admin_actions_enabled(entry: Any) -> bool:
    """Return whether risky admin/root actions should be exposed."""
    return bool(_entry_options(entry).get(CONF_SHOW_ADMIN_ACTIONS, False))


def use_miles(entry: Any) -> bool:
    """Return whether distance entities should display in miles."""
    return bool(_entry_options(entry).get(CONF_USE_MILES, False))


def use_fahrenheit(entry: Any) -> bool:
    """Return whether temperature entities should display in Fahrenheit."""
    return bool(_entry_options(entry).get(CONF_USE_FAHRENHEIT, False))


def distance_display_unit(native_unit: str | None, entry: Any) -> str | None:
    """Return the preferred display unit for a native distance unit."""
    if native_unit == "km" and use_miles(entry):
        return "mi"
    return native_unit


def temperature_display_unit(entry: Any) -> str:
    """Return the preferred HA temperature unit."""
    if use_fahrenheit(entry):
        return getattr(UnitOfTemperature, "FAHRENHEIT", "°F")
    return UnitOfTemperature.CELSIUS


def display_unit(native_unit: str | None, entry: Any) -> str | None:
    """Return the preferred display unit for a native unit string."""
    if native_unit == "km":
        return distance_display_unit(native_unit, entry)
    if native_unit == "°C" and use_fahrenheit(entry):
        return getattr(UnitOfTemperature, "FAHRENHEIT", "°F")
    return native_unit


def display_value(value: Any, native_unit: str | None, entry: Any) -> Any:
    """Convert a native value to the preferred display unit."""
    if not isinstance(value, (int, float)):
        return value
    if native_unit == "km" and use_miles(entry):
        return value * _MILES_PER_KILOMETER
    if native_unit == "°C" and use_fahrenheit(entry):
        return (value * 9 / 5) + 32
    return value


def native_value_from_display(value: Any, native_unit: str | None, entry: Any) -> Any:
    """Convert a displayed value back to the native unit."""
    if not isinstance(value, (int, float)):
        return value
    if native_unit == "km" and use_miles(entry):
        return value / _MILES_PER_KILOMETER
    if native_unit == "°C" and use_fahrenheit(entry):
        return (value - 32) * 5 / 9
    return value


def display_temperature_step(native_step: float) -> float:
    """Return a user-friendly temperature step for climate entities."""
    del native_step
    return 1.0
