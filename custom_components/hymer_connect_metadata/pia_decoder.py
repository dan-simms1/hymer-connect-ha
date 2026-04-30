"""PIA Protobuf decoder/encoder for HYMER Connect sensor data.

Decodes Base64-encoded Protobuf payloads from SignalR PiaResponse messages.
Encodes PiaRequest subscription messages for sensor data streaming.
"""

from __future__ import annotations

import base64
import logging
import struct
import time
from typing import Any

_LOGGER = logging.getLogger(__name__)

# Historical transform hints intentionally retained for the registry generator.
# These are not used by the live runtime decode path.
LEGACY_TRANSFORM_HINTS: dict[tuple[int, int], dict[str, str | None]] = {
    (1, 1): {"label": "odometer", "unit": "km", "transform": "div1000"},
    (1, 2): {"label": "fuel_level", "unit": "%", "transform": "invert100"},
    (1, 5): {"label": "distance_to_service", "unit": "km"},
    (1, 7): {"label": "adblue_remaining_distance", "unit": "km", "transform": "div1000"},
    (34, 7): {"label": "heat_setpoint_raw", "unit": None, "transform": "div1000"},
    (108, 2): {"label": "fuel_level", "unit": "%", "transform": "invert100"},
}

# Sentinel float values that indicate "sensor unavailable / not connected".
# The SCU stores 32768 (0x8000) as CAN "no data" — scaled to float as 3276.8.
_FLOAT_SENTINELS: set[float] = {3276.8, 32768.0, 65535.0, 6553.5}

# Protocol-compatible subscription groups observed on the wire and re-expressed
# here as original structured data so the repository does not ship captured
# base64 transport blobs.
_SUBSCRIPTION_GROUPS: tuple[tuple[int, str | None, tuple[int, ...]], ...] = (
    (1, "can0", tuple(range(1, 24))),
    (3, "lin1", tuple(range(1, 23))),
    (8, "lin2", tuple(range(1, 8))),
    (11, None, (1, 2)),
    (12, None, (1, 2, 3)),
    (15, None, (1, 2, 3)),
    (16, None, (1, 2)),
    (19, None, (1, 2)),
    (21, None, (1, 2)),
    (22, None, (1, 2)),
    (24, None, (1, 2, 3)),
    (25, None, (1, 2)),
    (27, None, (1, 2, 3)),
    (30, None, tuple(range(1, 15))),
    (34, "lin1", tuple(range(1, 8))),
    (37, None, (1, 2)),
    (43, None, (1, 2)),
    (44, None, (1, 2)),
    (45, "lin1", (8, 9, 10, 11)),
    (49, "lin1", (8, 10, 11)),
    (58, "lin1", tuple(range(4, 15))),
    (99, "can2", tuple(range(1, 11))),
)

_APP_PROTOCOL_VERSION = "v0.32.0"


# Cloud DataHub response statuses reverse-engineered from the app transport
# protocol. Only the values used by the live SignalR path are named here.
STATUS_SUCCESS = 1
STATUS_AUTH_TOKEN_EXPIRED = 12
STATUS_REMOTE_TOKEN_EXPIRED = 13


def build_subscription_requests() -> list[str]:
    """Build PiaRequest payloads for sensor data subscription.

    Returns a list of Base64-encoded protobuf payloads ready to send
    as PiaRequest arguments.  The 7 requests initialise different
    sensor groups and trigger the full data flow from the SCU.
    """
    return [
        _build_subscription_request(
            topic_field_number=4,
            topic_payload=_encode_bytes_field(1, b""),
        ),
        _build_subscription_request(
            topic_field_number=4,
            topic_payload=_build_subscription_catalog_payload(),
        ),
        _build_subscription_request(
            topic_field_number=4,
            topic_payload=_encode_bytes_field(
                9,
                _encode_bytes_field(1, _encode_varint_field(1, 0)),
            ),
        ),
        _build_subscription_request(
            topic_field_number=5,
            topic_payload=_encode_bytes_field(3, b""),
        ),
        _build_subscription_request(
            topic_field_number=12,
            topic_payload=_encode_bytes_field(1, b""),
        ),
        _build_subscription_request(
            topic_field_number=9,
            topic_payload=_encode_bytes_field(1, b""),
        ),
        _build_subscription_request(topic_field_number=15, topic_payload=b""),
    ]


def _build_subscription_catalog_payload() -> bytes:
    """Build the protocol-compatible subscription catalog payload."""
    entries = bytearray()
    for bus_id, bus_name, sensor_ids in _SUBSCRIPTION_GROUPS:
        for sensor_id in sensor_ids:
            entries += _build_subscription_entry(
                sensor_id=sensor_id,
                bus_id=bus_id,
                bus_name=bus_name,
            )
    return _encode_bytes_field(3, bytes(entries))


def _build_subscription_entry(
    *,
    sensor_id: int,
    bus_id: int,
    bus_name: str | None = None,
) -> bytes:
    """Build one subscription entry for the SCU sensor catalog."""
    entry = _encode_varint_field(1, sensor_id)
    entry += _encode_varint_field(2, bus_id)
    if bus_name:
        entry += _encode_str_field(10, bus_name)
    return _encode_bytes_field(1, entry)


def _build_subscription_request(
    *,
    topic_field_number: int,
    topic_payload: bytes,
) -> str:
    """Build a wrapped PiaRequest transport envelope for startup subscriptions."""
    import random

    request_id = random.randint(1, 10_000_000)
    timestamp = int(time.time())

    wrapper = _encode_varint_field(1, request_id)
    wrapper += _encode_bytes_field(2, _APP_PROTOCOL_VERSION.encode("utf-8"))
    wrapper += _encode_varint_field(3, timestamp)
    wrapper += _encode_bytes_field(topic_field_number, topic_payload)
    return base64.b64encode(_encode_bytes_field(2, wrapper)).decode("ascii")


def _build_cloud_request(
    *,
    topic_field_number: int,
    topic_payload: bytes,
) -> str:
    """Build a generic DataHub cloud request envelope.

    The app uses this transport for non-slot command topics such as
    ``Request.command.restart`` while still sending the resulting base64 over
    the same SignalR ``PiaRequest`` hub target.
    """
    import random

    request_id = random.randint(1, 1_000_000)
    timestamp = round(time.time())

    request = _encode_varint_field(1, request_id)
    request += _encode_str_field(2, _APP_PROTOCOL_VERSION)
    request += _encode_varint_field(3, timestamp)
    request += _encode_bytes_field(topic_field_number, topic_payload)
    return base64.b64encode(request).decode("ascii")


def build_restart_system_request(*, cold: bool = True) -> str:
    """Build a Request.command.restart Smart Unit restart request.

    Mirrors the app's ``request.command.restart`` path:

    - ``Request.command`` → field 9
    - ``CommandRequestTopic.restart`` → field 2
    - ``RestartCommand.cold`` → field 1
    """
    restart_command = _encode_varint_field(1, 1 if cold else 0)
    command_topic = _encode_bytes_field(2, restart_command)
    return _build_cloud_request(topic_field_number=9, topic_payload=command_topic)


def build_refresh_command() -> str:
    """Build a PiaRequest poll/refresh command to force SCU to re-report all states.

    The EHG app sends this after subscribing (shows "aktualisiere").
    Uses protobuf field 9 (empty) which triggers a full state refresh.
    """
    import random
    msg_id = random.randint(1, 10_000_000)
    ts = int(time.time())

    wrapper = _encode_varint_field(1, msg_id)
    wrapper += _encode_bytes_field(2, b"v0.32.0")
    wrapper += _encode_varint_field(3, ts)
    wrapper += _encode_bytes_field(9, b"")  # field 9 = refresh/poll

    payload = _encode_bytes_field(2, wrapper)
    return base64.b64encode(payload).decode("ascii")


def decode_pia_slots(
    b64_payload: str,
    *,
    known_slots: set[tuple[int, int]] | frozenset[tuple[int, int]] | None = None,
) -> dict[tuple[int, int], Any]:
    """Decode a PiaResponse into slot-keyed raw values.

    Returns a dict keyed by (bus_id, sensor_id).  Values are untransformed —
    callers apply per-slot transforms using discovery metadata.  Sentinel
    values are filtered out.
    """
    try:
        raw = base64.b64decode(b64_payload)
    except Exception:
        _LOGGER.warning("Failed to base64-decode PIA payload")
        return {}
    return decode_pia_slots_bytes(raw, known_slots=known_slots)


def decode_pia_slots_bytes(
    raw: bytes,
    *,
    known_slots: set[tuple[int, int]] | frozenset[tuple[int, int]] | None = None,
) -> dict[tuple[int, int], Any]:
    """Decode raw protobuf bytes containing slot entries."""
    slots: dict[tuple[int, int], Any] = {}
    for fn, wt, v in _decode_protobuf(raw):
        if wt != 2 or not isinstance(v, bytes):
            continue
        _extract_slots_recursive(v, slots, depth=0, known_slots=known_slots)
    return slots


def extract_request_id_from_payload(b64_payload: str) -> int | None:
    """Extract the app-style request id from a PIA request payload."""
    try:
        raw = base64.b64decode(b64_payload)
    except Exception:
        return None
    request = _find_request_message(raw)
    if request is None:
        return None
    return int(request.get("request_id", 0)) or None


def decode_transport_response(b64_payload: str) -> dict[str, Any] | None:
    """Decode a DataHub transport response envelope from base64."""
    try:
        raw = base64.b64decode(b64_payload)
    except Exception:
        return None
    return _find_response_message(raw)


def _extract_slots_recursive(
    data: bytes,
    slots: dict[tuple[int, int], Any],
    depth: int,
    *,
    known_slots: set[tuple[int, int]] | frozenset[tuple[int, int]] | None,
) -> None:
    """Recursively search for sensor entries, storing by (bus_id, sensor_id).

    Mirrors _extract_sensors_recursive but skips the name lookup and stores
    raw values. Depth 4 entries are accepted only when the local runtime
    metadata pack knows the slot; this keeps the phantom-wrapper guard while
    allowing deeper real-time SCU push frames for known capabilities.
    """
    if depth > 5:
        return
    fields = _decode_protobuf(data)
    has_sid = any(fn == 1 and wt == 0 for fn, wt, _ in fields)
    has_bus = any(fn == 2 and wt == 0 for fn, wt, _ in fields)
    has_value = any(
        (fn in (3, 4, 5, 6, 7) and wt in (0, 2, 5)) for fn, wt, _ in fields
    )
    if has_sid and has_bus and has_value:
        sid_val = next((v for fn, wt, v in fields if fn == 1 and wt == 0), 0)
        bus_val = next((v for fn, wt, v in fields if fn == 2 and wt == 0), 0)
        slot_key = (bus_val, sid_val)
        known_depth4_slot = (
            depth == 4
            and known_slots is not None
            and slot_key in known_slots
        )
        if sid_val < 1000 and bus_val < 1000 and (depth <= 3 or known_depth4_slot):
            entry = _parse_sensor_entry(data)
            if entry and entry["value"] is not None:
                val = entry["value"]
                if isinstance(val, (int, float)) and val in _FLOAT_SENTINELS:
                    return
                slots[(entry["bus_id"], entry["sensor_id"])] = val
            return
    for fn, wt, v in fields:
        if wt == 2 and isinstance(v, bytes) and len(v) > 2:
            _extract_slots_recursive(
                v,
                slots,
                depth + 1,
                known_slots=known_slots,
            )


def _encode_varint(value: int) -> bytes:
    """Encode an integer as a protobuf varint."""
    result = bytearray()
    while value > 0x7F:
        result.append((value & 0x7F) | 0x80)
        value >>= 7
    result.append(value & 0x7F)
    return bytes(result)


def _encode_field(field_number: int, wire_type: int, data: bytes) -> bytes:
    """Encode a protobuf field with tag and data."""
    tag = _encode_varint((field_number << 3) | wire_type)
    return tag + data


def _encode_varint_field(field_number: int, value: int) -> bytes:
    """Encode a varint field."""
    return _encode_field(field_number, 0, _encode_varint(value))


def _encode_bytes_field(field_number: int, data: bytes) -> bytes:
    """Encode a length-delimited field."""
    return _encode_field(field_number, 2, _encode_varint(len(data)) + data)


def _encode_str_field(field_number: int, value: str) -> bytes:
    """Encode a string as a length-delimited field."""
    data = value.encode("utf-8")
    return _encode_bytes_field(field_number, data)


def _encode_float_field(field_number: int, value: float) -> bytes:
    """Encode a 32-bit float field (wire type 5)."""
    return _encode_field(field_number, 5, struct.pack("<f", value))


def build_light_command(
    bus_id: int,
    sensor_id: int,
    *,
    bool_value: bool | None = None,
    uint_value: int | None = None,
    str_value: str | None = None,
) -> str:
    """Build a PiaRequest payload to control a light or switch.

    Args:
        bus_id: The bus ID (e.g. 11 for living ceiling, 3 for main switch).
        sensor_id: 1=on/off, 2=brightness, 3=color_temp.
        bool_value: True/False for on/off (sensor_id=1).
        uint_value: 0-100 for brightness/color_temp (sensor_id=2,3).
        str_value: String value (e.g. "On"/"Off" for main switch on bus 3).

    Returns:
        Base64-encoded protobuf payload ready to send as PiaRequest argument.
    """
    # Build sensor entry: field1=sensor_id, field2=bus_id, field3/4/5=value
    sensor_data = _encode_varint_field(1, sensor_id)
    sensor_data += _encode_varint_field(2, bus_id)
    if str_value is not None:
        sensor_data += _encode_str_field(4, str_value)
    elif bool_value is not None:
        sensor_data += _encode_varint_field(5, 1 if bool_value else 0)
    elif uint_value is not None:
        sensor_data += _encode_varint_field(3, uint_value)

    # Nest: sensor_data inside field1 of sub2, inside field2 of inner
    sub2 = _encode_bytes_field(1, sensor_data)
    inner = _encode_bytes_field(2, sub2)

    # Build wrapper: msg_id, version, timestamp, command
    import random
    msg_id = random.randint(1, 10_000_000)
    version_bytes = _APP_PROTOCOL_VERSION.encode("utf-8")
    ts = int(time.time())

    wrapper = _encode_varint_field(1, msg_id)
    wrapper += _encode_bytes_field(2, version_bytes)
    wrapper += _encode_varint_field(3, ts)
    wrapper += _encode_bytes_field(4, inner)

    # Top-level: field 2 = wrapper
    payload = _encode_bytes_field(2, wrapper)

    return base64.b64encode(payload).decode("ascii")


def build_multi_sensor_command(
    sensors: list[dict],
) -> str:
    """Build a PiaRequest payload with multiple sensor entries.

    Each sensor dict must have:
        bus_id: int
        sensor_id: int
    And one of:
        bool_value: bool
        uint_value: int
        str_value: str
        float_value: float

    Used for heater setpoint (temp + fuel type) and boiler mode commands.
    """
    import random

    entries = b""
    for s in sensors:
        sensor_data = _encode_varint_field(1, s["sensor_id"])
        sensor_data += _encode_varint_field(2, s["bus_id"])
        if "bool_value" in s:
            sensor_data += _encode_varint_field(5, 1 if s["bool_value"] else 0)
        elif "uint_value" in s:
            sensor_data += _encode_varint_field(3, s["uint_value"])
        elif "str_value" in s:
            sensor_data += _encode_str_field(4, s["str_value"])
        elif "float_value" in s:
            sensor_data += _encode_float_field(6, s["float_value"])
        entries += _encode_bytes_field(1, sensor_data)

    inner = _encode_bytes_field(2, entries)

    msg_id = random.randint(1, 10_000_000)
    ts = int(time.time())

    wrapper = _encode_varint_field(1, msg_id)
    wrapper += _encode_bytes_field(2, _APP_PROTOCOL_VERSION.encode("utf-8"))
    wrapper += _encode_varint_field(3, ts)
    wrapper += _encode_bytes_field(4, inner)

    payload = _encode_bytes_field(2, wrapper)
    return base64.b64encode(payload).decode("ascii")


def _decode_varint(data: bytes, pos: int) -> tuple[int, int]:
    """Decode a varint, return (value, new_pos)."""
    result = 0
    shift = 0
    while pos < len(data):
        b = data[pos]
        result |= (b & 0x7F) << shift
        pos += 1
        if not (b & 0x80):
            break
        shift += 7
    return result, pos


def _decode_protobuf(data: bytes) -> list[tuple[int, int, Any]]:
    """Decode raw protobuf into (field_number, wire_type, value) tuples."""
    fields: list[tuple[int, int, Any]] = []
    pos = 0
    while pos < len(data):
        try:
            tag, pos = _decode_varint(data, pos)
        except (IndexError, ValueError):
            break
        field_number = tag >> 3
        wire_type = tag & 0x07
        if wire_type == 0:  # varint
            value, pos = _decode_varint(data, pos)
            fields.append((field_number, 0, value))
        elif wire_type == 1:  # fixed64
            if pos + 8 > len(data):
                break
            value = struct.unpack_from("<d", data, pos)[0]
            pos += 8
            fields.append((field_number, 1, value))
        elif wire_type == 5:  # fixed32
            if pos + 4 > len(data):
                break
            value = struct.unpack_from("<f", data, pos)[0]
            pos += 4
            fields.append((field_number, 5, round(value, 2)))
        elif wire_type == 2:  # length-delimited
            length, pos = _decode_varint(data, pos)
            if pos + length > len(data):
                break
            value = data[pos : pos + length]
            pos += length
            fields.append((field_number, 2, value))
        else:
            break
    return fields


def _find_request_message(data: bytes) -> dict[str, Any] | None:
    """Recursively find a request wrapper carrying the app request id."""
    fields = _decode_protobuf(data)
    request_id = next(
        (int(v) for fn, wt, v in fields if fn == 1 and wt == 0),
        None,
    )
    timestamp = next(
        (int(v) for fn, wt, v in fields if fn == 3 and wt == 0),
        None,
    )
    if request_id is not None and timestamp is not None:
        return {"request_id": request_id, "timestamp": timestamp}
    for _, wt, value in fields:
        if wt == 2 and isinstance(value, bytes) and len(value) > 2:
            nested = _find_request_message(value)
            if nested is not None:
                return nested
    return None


def _find_response_message(data: bytes) -> dict[str, Any] | None:
    """Recursively find a response wrapper carrying request/status/data."""
    fields = _decode_protobuf(data)
    request_id = next(
        (int(v) for fn, wt, v in fields if fn == 1 and wt == 0),
        None,
    )
    status = next(
        (int(v) for fn, wt, v in fields if fn == 2 and wt == 0),
        None,
    )
    timestamp = next(
        (int(v) for fn, wt, v in fields if fn == 3 and wt == 0),
        None,
    )
    payload = next(
        (
            value
            for fn, wt, value in fields
            if wt == 2 and isinstance(value, bytes) and fn in (5, 6, 19)
        ),
        None,
    )
    if request_id is not None and status is not None:
        return {
            "request_id": request_id,
            "status": status,
            "timestamp": timestamp,
            "payload": payload,
        }
    for _, wt, value in fields:
        if wt == 2 and isinstance(value, bytes) and len(value) > 2:
            nested = _find_response_message(value)
            if nested is not None:
                return nested
    return None


def _try_string(data: bytes) -> str | None:
    """Try decoding bytes as UTF-8 printable string."""
    try:
        text = data.decode("utf-8")
        if text and all(c.isprintable() or c in "\r\n\t" for c in text):
            return text
    except (UnicodeDecodeError, ValueError):
        pass
    return None


def _parse_sensor_entry(data: bytes) -> dict[str, Any] | None:
    """Parse a single sensor entry from protobuf bytes.

    Each sensor carries its value in exactly one of several typed protobuf
    fields (uint, string, bool, float, int).  However the SCU sometimes
    populates *both* a uint/int field **and** the bool field for the same
    sensor.  Because ``True == 1`` in Python the bool would silently
    satisfy an ``on_value=1`` check even when the uint is 0.

    To avoid this, we collect *all* value candidates and prefer the more
    specific numeric types (uint → field 3, int → field 7) over the
    boolean (field 5) whenever both are present.
    """
    fields = _decode_protobuf(data)
    sensor_id = 0
    bus_id = 0
    bus_name = ""
    # Collect value candidates keyed by protobuf field number.
    values: dict[int, Any] = {}

    for fn, wt, v in fields:
        if fn == 1 and wt == 0:
            sensor_id = v
        elif fn == 2 and wt == 0:
            bus_id = v
        elif fn == 3 and wt == 0:
            values[3] = v  # uint
        elif fn == 4 and wt == 2:
            s = _try_string(v)
            if s is not None:
                values[4] = s
        elif fn == 5 and wt == 0:
            values[5] = bool(v)  # bool stored as varint
        elif fn == 6 and wt == 5:
            values[6] = v  # float32
        elif fn == 7 and wt == 0:
            values[7] = v  # signed int (as varint)
        elif fn == 10 and wt == 2:
            s = _try_string(v)
            if s:
                bus_name = s

    # Pick the best value: prefer string → float → uint → int → bool.
    # uint/int take precedence over bool to avoid True==1 confusion.
    value: Any = None
    for candidate_field in (4, 6, 3, 7, 5):
        if candidate_field in values:
            value = values[candidate_field]
            break

    if not sensor_id and value is None:
        return None

    return {
        "sensor_id": sensor_id,
        "bus_id": bus_id,
        "bus_name": bus_name,
        "value": value,
    }
