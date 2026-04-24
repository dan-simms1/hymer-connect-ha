"""Helpers for validating and serializing generic slot write actions."""

from __future__ import annotations

from typing import Any

from .discovery import reverse_transform, slot_meta

_STRING_TRUE_VALUES = {"ON", "TRUE", "YES", "OPEN", "UNLOCKED"}
_STRING_FALSE_VALUES = {"OFF", "FALSE", "NO", "CLOSE", "CLOSED", "LOCKED"}


class SlotActionError(ValueError):
    """Raised when a catalog slot action cannot be serialized safely."""


def _validate_numeric_range(
    component_id: int,
    sensor_id: int,
    numeric: float,
    *,
    minimum: float | int | None,
    maximum: float | int | None,
) -> None:
    if minimum is not None and numeric < float(minimum):
        raise SlotActionError(
            f"Action value below minimum for {(component_id, sensor_id)}: {numeric!r} < {minimum!r}"
        )
    if maximum is not None and numeric > float(maximum):
        raise SlotActionError(
            f"Action value above maximum for {(component_id, sensor_id)}: {numeric!r} > {maximum!r}"
        )


def serialize_slot_action(action: dict[str, Any]) -> dict[str, Any]:
    """Validate and serialize one catalog action into a PIA write payload."""
    component_id = int(action["component_id"])
    sensor_id = int(action["sensor_id"])
    meta = slot_meta(component_id, sensor_id)
    if meta is None:
        raise SlotActionError(f"Unknown slot {(component_id, sensor_id)}")
    if meta.wire_mode not in {"rw", "w"}:
        raise SlotActionError(f"Read-only slot {(component_id, sensor_id)}")

    user_value = action["value"]
    value = reverse_transform(user_value, meta.transform)
    sensor: dict[str, Any] = {
        "bus_id": component_id,
        "sensor_id": sensor_id,
    }

    if meta.datatype == "bool":
        if isinstance(value, bool):
            sensor["bool_value"] = value
            return sensor
        if isinstance(value, (int, float)) and not isinstance(value, bool) and value in {0, 1}:
            sensor["bool_value"] = bool(value)
            return sensor
        if isinstance(value, str):
            normalized = value.upper()
            if normalized in _STRING_TRUE_VALUES:
                sensor["bool_value"] = True
                return sensor
            if normalized in _STRING_FALSE_VALUES:
                sensor["bool_value"] = False
                return sensor
        raise SlotActionError(
            f"Unsupported bool action value for {(component_id, sensor_id)}: {value!r}"
        )

    if meta.datatype == "float":
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise SlotActionError(
                f"Unsupported float action value for {(component_id, sensor_id)}: {value!r}"
            )
        _validate_numeric_range(
            component_id,
            sensor_id,
            float(user_value),
            minimum=meta.min_value,
            maximum=meta.max_value,
        )
        sensor["float_value"] = float(value)
        return sensor

    if meta.datatype == "int":
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise SlotActionError(
                f"Unsupported int action value for {(component_id, sensor_id)}: {value!r}"
            )
        numeric = float(value)
        if not numeric.is_integer():
            raise SlotActionError(
                f"Non-integer int action value for {(component_id, sensor_id)}: {value!r}"
            )
        integer = int(numeric)
        if integer < 0:
            if meta.min_value is not None and float(meta.min_value) < 0:
                raise SlotActionError(
                    f"Signed int writes are not supported for {(component_id, sensor_id)}"
                )
            raise SlotActionError(
                f"Negative int action value for {(component_id, sensor_id)}: {value!r}"
            )
        _validate_numeric_range(
            component_id,
            sensor_id,
            float(user_value),
            minimum=meta.min_value,
            maximum=meta.max_value,
        )
        sensor["uint_value"] = integer
        return sensor

    if meta.datatype == "string":
        if not isinstance(value, str):
            raise SlotActionError(
                f"Unsupported string action value for {(component_id, sensor_id)}: {value!r}"
            )
        sensor["str_value"] = value
        return sensor

    raise SlotActionError(
        f"Unsupported slot datatype {meta.datatype!r} for {(component_id, sensor_id)}"
    )


def action_is_supported(
    action: dict[str, Any],
    observed_slots: set[tuple[int, int]] | None = None,
) -> bool:
    """Return True when the action can be serialized for the current vehicle."""
    try:
        sensor = serialize_slot_action(action)
    except SlotActionError:
        return False
    if observed_slots is None:
        return True
    return (sensor["bus_id"], sensor["sensor_id"]) in observed_slots
