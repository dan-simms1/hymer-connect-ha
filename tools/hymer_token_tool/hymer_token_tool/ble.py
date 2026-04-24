"""BLE helpers for the standalone token tool."""

from __future__ import annotations

from dataclasses import asdict, dataclass
import math
import random
import time
from typing import Any
import zlib

try:
    from bleak import BleakClient, BleakScanner
except ImportError:  # pragma: no cover - handled at runtime
    BleakClient = None
    BleakScanner = None


class BleSupportError(RuntimeError):
    """Raised when BLE dependencies are unavailable."""


UART_SERVICE_UUID = "6e400001-b5a3-f393-e0a9-e50e24dcca9e"
UART_RX_CHARACTERISTIC_UUID = "6e400002-b5a3-f393-e0a9-e50e24dcca9e"
UART_TX_CHARACTERISTIC_UUID = "6e400003-b5a3-f393-e0a9-e50e24dcca9e"
POWER_SERVICE_UUID = "fff40001-13c9-42f3-9d46-e1d1aa2a7232"
POWER_STATE_CHARACTERISTIC_UUID = "fff40002-13c9-42f3-9d46-e1d1aa2a7232"
POWER_CONTROL_CHARACTERISTIC_UUID = "fff40003-13c9-42f3-9d46-e1d1aa2a7232"
BONDING_STATE_CHARACTERISTIC_UUID = "fff40004-13c9-42f3-9d46-e1d1aa2a7232"
SIU_DEVICE_INFO_SERVICE_UUID = "0000180a-0000-1000-8000-00805f9b34fb"
SIU_MANUFACTURER_NAME_CHARACTERISTIC_UUID = "00002a29-0000-1000-8000-00805f9b34fb"
SIU_FIRMWARE_REVISION_CHARACTERISTIC_UUID = "00002a26-0000-1000-8000-00805f9b34fb"
SIU_MODEL_NUMBER_CHARACTERISTIC_UUID = "00002a24-0000-1000-8000-00805f9b34fb"

BLE_PIA_MAGIC = bytes((0xA0, 0xCB))
BLE_PIA_HEADER_SIZE = 10
APP_PIA_VERSION = "v0.32.0"

_WIRE_VARINT = 0
_WIRE_LEN = 2

_BLE_PROTOCOL_REQUEST_FIELD = 1
_BLE_PROTOCOL_RESPONSE_FIELD = 2

_REQUEST_REQUEST_ID_FIELD = 1
_REQUEST_VERSION_FIELD = 2
_REQUEST_TIMESTAMP_FIELD = 3
_REQUEST_USER_FIELD = 8

_RESPONSE_REQUEST_ID_FIELD = 1
_RESPONSE_STATUS_FIELD = 2
_RESPONSE_TIMESTAMP_FIELD = 3
_RESPONSE_MOBILE_PAIR_FIELD = 9

_USER_PAIR_MOBILE_DEVICE_FIELD = 4
_USER_PAIR_MOBILE_CONFIRMATION_FIELD = 6

_PAIR_MOBILE_REQUEST_ACTIVATION_TOKEN_FIELD = 1
_PAIR_MOBILE_REQUEST_CONFIRMATION_TOKEN_FIELD = 2
_PAIR_MOBILE_REQUEST_MOBILE_DEVICE_NAME_FIELD = 3
_PAIR_MOBILE_REQUEST_WAIT_FOR_CONFIRMATION_FIELD = 4

_PAIR_MOBILE_CONFIRMATION_SUCCESS_FIELD = 1

_PAIR_MOBILE_RESPONSE_ACCESS_TOKEN_FIELD = 1
_PAIR_MOBILE_RESPONSE_ACCESS_REFRESH_TOKEN_FIELD = 2
_PAIR_MOBILE_RESPONSE_CONFIRMATION_REQUIRED_FIELD = 3


def _require_bleak() -> None:
    if BleakScanner is None or BleakClient is None:
        raise BleSupportError(
            "BLE support is not available. Install the tool with its default "
            "dependencies and make sure the OS Bluetooth stack is available."
        )


@dataclass
class DiscoveredBleDevice:
    """Simple BLE scan result."""

    identifier: str
    name: str
    address: str
    rssi: int | None
    manufacturer_data: dict[str, str]
    service_uuids: list[str]

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class CharacteristicRecord:
    """Characteristic description from a connected device."""

    uuid: str
    properties: list[str]

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class ServiceRecord:
    """Service description from a connected device."""

    uuid: str
    characteristics: list[CharacteristicRecord]

    def to_dict(self) -> dict[str, Any]:
        return {
            "uuid": self.uuid,
            "characteristics": [item.to_dict() for item in self.characteristics],
        }


@dataclass
class BlePiaFrame:
    """Decoded BLE PIA transport frame."""

    payload_length: int
    crc32: int
    payload: bytes
    crc32_valid: bool

    def to_dict(self) -> dict[str, Any]:
        return {
            "payload_length": self.payload_length,
            "crc32": f"0x{self.crc32:08x}",
            "payload_hex": self.payload.hex(),
            "crc32_valid": self.crc32_valid,
        }


@dataclass
class BlePairMobileResponse:
    """Decoded PairMobileResponse plus its outer Response envelope fields."""

    remote_access_token: str
    remote_access_refresh_token: str
    confirmation_required: bool
    request_id: int | None = None
    status: int | None = None
    timestamp: int | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _normalize_hex(value: str) -> str:
    return "".join(value.split()).lower()


def _encode_varint(value: int) -> bytes:
    if value < 0:
        raise ValueError("varint value must be non-negative")
    encoded = bytearray()
    remaining = value
    while True:
        next_byte = remaining & 0x7F
        remaining >>= 7
        if remaining:
            encoded.append(next_byte | 0x80)
            continue
        encoded.append(next_byte)
        return bytes(encoded)


def _decode_varint(buffer: bytes, offset: int = 0) -> tuple[int, int]:
    shift = 0
    value = 0
    index = offset
    while index < len(buffer):
        current = buffer[index]
        value |= (current & 0x7F) << shift
        index += 1
        if not current & 0x80:
            return value, index
        shift += 7
        if shift >= 64:
            break
    raise ValueError("unterminated protobuf varint")


def _encode_key(field_number: int, wire_type: int) -> bytes:
    return _encode_varint((field_number << 3) | wire_type)


def _encode_varint_field(field_number: int, value: int) -> bytes:
    return _encode_key(field_number, _WIRE_VARINT) + _encode_varint(value)


def _encode_bool_field(field_number: int, value: bool) -> bytes:
    return _encode_varint_field(field_number, 1 if value else 0)


def _encode_string_field(field_number: int, value: str) -> bytes:
    encoded = value.encode("utf-8")
    return _encode_length_delimited_field(field_number, encoded)


def _encode_length_delimited_field(field_number: int, value: bytes) -> bytes:
    return _encode_key(field_number, _WIRE_LEN) + _encode_varint(len(value)) + value


def _decode_length_delimited(buffer: bytes, offset: int) -> tuple[bytes, int]:
    length, next_offset = _decode_varint(buffer, offset)
    end = next_offset + length
    if end > len(buffer):
        raise ValueError("protobuf length-delimited field overruns buffer")
    return buffer[next_offset:end], end


def _skip_protobuf_field(buffer: bytes, offset: int, wire_type: int) -> int:
    if wire_type == _WIRE_VARINT:
        _, next_offset = _decode_varint(buffer, offset)
        return next_offset
    if wire_type == _WIRE_LEN:
        _, next_offset = _decode_length_delimited(buffer, offset)
        return next_offset
    if wire_type == 1:
        end = offset + 8
    elif wire_type == 5:
        end = offset + 4
    else:
        raise ValueError(f"unsupported protobuf wire type {wire_type}")
    if end > len(buffer):
        raise ValueError("protobuf fixed-width field overruns buffer")
    return end


def app_like_request_id() -> int:
    """Mirror the app's Math.ceil(Math.random() * 1000000) + 1 behavior."""
    return math.ceil(random.random() * 1_000_000) + 1


def app_like_request_timestamp() -> int:
    """Mirror the app's rounded epoch-seconds request timestamp."""
    return round(time.time())


def build_pair_mobile_request_payload(
    activation_token: str,
    confirmation_token: str,
    mobile_device_name: str,
    *,
    wait_for_confirmation: bool = True,
) -> bytes:
    """Encode PairMobileRequest."""
    return b"".join(
        (
            _encode_string_field(
                _PAIR_MOBILE_REQUEST_ACTIVATION_TOKEN_FIELD,
                activation_token,
            ),
            _encode_string_field(
                _PAIR_MOBILE_REQUEST_CONFIRMATION_TOKEN_FIELD,
                confirmation_token,
            ),
            _encode_string_field(
                _PAIR_MOBILE_REQUEST_MOBILE_DEVICE_NAME_FIELD,
                mobile_device_name,
            ),
            _encode_bool_field(
                _PAIR_MOBILE_REQUEST_WAIT_FOR_CONFIRMATION_FIELD,
                wait_for_confirmation,
            ),
        )
    )


def build_pair_mobile_confirmation_payload(*, success: bool = True) -> bytes:
    """Encode PairMobileConfirmation."""
    return _encode_bool_field(_PAIR_MOBILE_CONFIRMATION_SUCCESS_FIELD, success)


def build_user_pair_mobile_request_topic(
    activation_token: str,
    confirmation_token: str,
    mobile_device_name: str,
    *,
    wait_for_confirmation: bool = True,
) -> bytes:
    """Encode UserRequestTopic with the pairMobileDevice branch set."""
    return _encode_length_delimited_field(
        _USER_PAIR_MOBILE_DEVICE_FIELD,
        build_pair_mobile_request_payload(
            activation_token,
            confirmation_token,
            mobile_device_name,
            wait_for_confirmation=wait_for_confirmation,
        ),
    )


def build_user_pair_mobile_confirmation_topic(*, success: bool = True) -> bytes:
    """Encode UserRequestTopic with the pairMobileDeviceConfirmation branch set."""
    return _encode_length_delimited_field(
        _USER_PAIR_MOBILE_CONFIRMATION_FIELD,
        build_pair_mobile_confirmation_payload(success=success),
    )


def build_request_message(
    user_topic_payload: bytes,
    *,
    request_id: int,
    timestamp: int,
    version: str = APP_PIA_VERSION,
) -> bytes:
    """Encode the Request envelope used by BasePiaApi.createRequest()."""
    return b"".join(
        (
            _encode_varint_field(_REQUEST_REQUEST_ID_FIELD, request_id),
            _encode_string_field(_REQUEST_VERSION_FIELD, version),
            _encode_varint_field(_REQUEST_TIMESTAMP_FIELD, timestamp),
            _encode_length_delimited_field(_REQUEST_USER_FIELD, user_topic_payload),
        )
    )


def build_ble_protocol_request_payload(request_message: bytes) -> bytes:
    """Wrap one Request inside BleProtocol."""
    return _encode_length_delimited_field(_BLE_PROTOCOL_REQUEST_FIELD, request_message)


def build_pair_mobile_ble_protocol_request(
    activation_token: str,
    confirmation_token: str,
    mobile_device_name: str,
    *,
    request_id: int,
    timestamp: int,
    version: str = APP_PIA_VERSION,
    wait_for_confirmation: bool = True,
) -> bytes:
    """Build the protobuf payload sent before BLE PIA framing."""
    return build_ble_protocol_request_payload(
        build_request_message(
            build_user_pair_mobile_request_topic(
                activation_token,
                confirmation_token,
                mobile_device_name,
                wait_for_confirmation=wait_for_confirmation,
            ),
            request_id=request_id,
            timestamp=timestamp,
            version=version,
        )
    )


def build_pair_mobile_ble_pia_frame(
    activation_token: str,
    confirmation_token: str,
    mobile_device_name: str,
    *,
    request_id: int | None = None,
    timestamp: int | None = None,
    version: str = APP_PIA_VERSION,
    wait_for_confirmation: bool = True,
) -> bytes:
    """Build the exact app-style PairMobileRequest frame sent to BLE."""
    payload = build_pair_mobile_ble_protocol_request(
        activation_token,
        confirmation_token,
        mobile_device_name,
        request_id=app_like_request_id() if request_id is None else request_id,
        timestamp=app_like_request_timestamp() if timestamp is None else timestamp,
        version=version,
        wait_for_confirmation=wait_for_confirmation,
    )
    return encode_ble_pia_frame(payload)


def build_pair_mobile_confirmation_ble_pia_frame(
    *,
    request_id: int | None = None,
    timestamp: int | None = None,
    version: str = APP_PIA_VERSION,
    success: bool = True,
) -> bytes:
    """Build the app-style PairMobileConfirmation BLE frame."""
    payload = build_ble_protocol_request_payload(
        build_request_message(
            build_user_pair_mobile_confirmation_topic(success=success),
            request_id=app_like_request_id() if request_id is None else request_id,
            timestamp=app_like_request_timestamp() if timestamp is None else timestamp,
            version=version,
        )
    )
    return encode_ble_pia_frame(payload)


def decode_pair_mobile_response_payload(payload: bytes) -> BlePairMobileResponse:
    """Decode a BleProtocol payload that carries Response.mobilePair."""
    response_payload: bytes | None = None
    offset = 0
    while offset < len(payload):
        key, offset = _decode_varint(payload, offset)
        field_number = key >> 3
        wire_type = key & 0x07
        if field_number == _BLE_PROTOCOL_RESPONSE_FIELD and wire_type == _WIRE_LEN:
            response_payload, offset = _decode_length_delimited(payload, offset)
            continue
        offset = _skip_protobuf_field(payload, offset, wire_type)
    if response_payload is None:
        raise ValueError("BleProtocol payload does not contain a response")

    request_id: int | None = None
    status: int | None = None
    timestamp: int | None = None
    mobile_pair_payload: bytes | None = None
    offset = 0
    while offset < len(response_payload):
        key, offset = _decode_varint(response_payload, offset)
        field_number = key >> 3
        wire_type = key & 0x07
        if wire_type == _WIRE_VARINT:
            value, offset = _decode_varint(response_payload, offset)
            if field_number == _RESPONSE_REQUEST_ID_FIELD:
                request_id = value
            elif field_number == _RESPONSE_STATUS_FIELD:
                status = value
            elif field_number == _RESPONSE_TIMESTAMP_FIELD:
                timestamp = value
            continue
        if field_number == _RESPONSE_MOBILE_PAIR_FIELD and wire_type == _WIRE_LEN:
            mobile_pair_payload, offset = _decode_length_delimited(response_payload, offset)
            continue
        offset = _skip_protobuf_field(response_payload, offset, wire_type)
    if mobile_pair_payload is None:
        raise ValueError("Response payload does not contain mobilePair")

    remote_access_token = ""
    remote_access_refresh_token = ""
    confirmation_required = False
    offset = 0
    while offset < len(mobile_pair_payload):
        key, offset = _decode_varint(mobile_pair_payload, offset)
        field_number = key >> 3
        wire_type = key & 0x07
        if wire_type == _WIRE_LEN:
            value, offset = _decode_length_delimited(mobile_pair_payload, offset)
            text_value = value.decode("utf-8")
            if field_number == _PAIR_MOBILE_RESPONSE_ACCESS_TOKEN_FIELD:
                remote_access_token = text_value
            elif field_number == _PAIR_MOBILE_RESPONSE_ACCESS_REFRESH_TOKEN_FIELD:
                remote_access_refresh_token = text_value
            continue
        if wire_type == _WIRE_VARINT:
            value, offset = _decode_varint(mobile_pair_payload, offset)
            if field_number == _PAIR_MOBILE_RESPONSE_CONFIRMATION_REQUIRED_FIELD:
                confirmation_required = bool(value)
            continue
        offset = _skip_protobuf_field(mobile_pair_payload, offset, wire_type)

    return BlePairMobileResponse(
        remote_access_token=remote_access_token,
        remote_access_refresh_token=remote_access_refresh_token,
        confirmation_required=confirmation_required,
        request_id=request_id,
        status=status,
        timestamp=timestamp,
    )


def decode_pair_mobile_response_frame(frame: bytes, *, validate_crc: bool = True) -> BlePairMobileResponse:
    """Decode one framed BLE PIA PairMobileResponse."""
    return decode_pair_mobile_response_payload(
        decode_ble_pia_frame(frame, validate_crc=validate_crc).payload
    )


def build_ble_pia_header(payload_length: int, crc32_value: int = 0) -> bytes:
    """Build the fixed-width BLE PIA header."""
    if payload_length < 0:
        raise ValueError("payload_length must be non-negative")
    return (
        BLE_PIA_MAGIC
        + payload_length.to_bytes(4, byteorder="big", signed=False)
        + (crc32_value & 0xFFFFFFFF).to_bytes(4, byteorder="big", signed=False)
    )


def encode_ble_pia_frame(payload: bytes) -> bytes:
    """Encode one protobuf payload into the app's BLE PIA frame."""
    provisional_header = build_ble_pia_header(len(payload), 0)
    crc32_value = zlib.crc32(provisional_header + payload) & 0xFFFFFFFF
    return build_ble_pia_header(len(payload), crc32_value) + payload


def decode_ble_pia_frame(frame: bytes, *, validate_crc: bool = True) -> BlePiaFrame:
    """Decode one BLE PIA frame into its payload and metadata."""
    if len(frame) < BLE_PIA_HEADER_SIZE:
        raise ValueError("frame is shorter than the BLE PIA header")
    if not frame.startswith(BLE_PIA_MAGIC):
        raise ValueError("frame does not start with the BLE PIA magic bytes")
    payload_length = int.from_bytes(frame[2:6], byteorder="big", signed=False)
    crc32_value = int.from_bytes(frame[6:10], byteorder="big", signed=False)
    payload = frame[BLE_PIA_HEADER_SIZE:]
    if len(payload) != payload_length:
        raise ValueError(
            f"frame payload length mismatch: expected {payload_length}, got {len(payload)}"
        )
    expected_crc32 = zlib.crc32(build_ble_pia_header(payload_length, 0) + payload) & 0xFFFFFFFF
    crc32_valid = expected_crc32 == crc32_value
    if validate_crc and not crc32_valid:
        raise ValueError(
            f"BLE PIA CRC32 mismatch: expected 0x{expected_crc32:08x}, got 0x{crc32_value:08x}"
        )
    return BlePiaFrame(
        payload_length=payload_length,
        crc32=crc32_value,
        payload=payload,
        crc32_valid=crc32_valid,
    )


def is_ble_pia_first_chunk(hex_chunk: str) -> bool:
    """Return True if a hex notification chunk starts a BLE PIA message."""
    return _normalize_hex(hex_chunk).startswith(BLE_PIA_MAGIC.hex())


def ble_pia_payload_length_from_hex(hex_message: str) -> int:
    """Read the encoded payload length from a hex BLE PIA message."""
    normalized = _normalize_hex(hex_message)
    if len(normalized) < BLE_PIA_HEADER_SIZE * 2:
        raise ValueError("hex message is shorter than the BLE PIA header")
    if not is_ble_pia_first_chunk(normalized):
        raise ValueError("hex message does not start with the BLE PIA magic bytes")
    return int.from_bytes(bytes.fromhex(normalized[4:12]), byteorder="big", signed=False)


def split_ble_pia_messages(hex_stream: str) -> list[str]:
    """Split a concatenated hex stream on repeated BLE PIA magic bytes."""
    normalized = _normalize_hex(hex_stream)
    marker = BLE_PIA_MAGIC.hex()
    return [chunk for chunk in normalized.replace(marker, f",{marker}").split(",") if chunk]


async def scan_devices(
    *,
    timeout: float = 8.0,
    name_contains: str = "",
) -> list[DiscoveredBleDevice]:
    """Scan nearby BLE devices."""
    _require_bleak()
    discovered = await BleakScanner.discover(timeout=timeout, return_adv=True)
    wanted = name_contains.lower().strip()
    devices: list[DiscoveredBleDevice] = []
    for _, pair in discovered.items():
        device, advertisement = pair
        name = device.name or advertisement.local_name or ""
        if wanted and wanted not in name.lower():
            continue
        manufacturer_data = {
            str(key): value.hex()
            for key, value in advertisement.manufacturer_data.items()
        }
        devices.append(
            DiscoveredBleDevice(
                identifier=device.address,
                name=name,
                address=device.address,
                rssi=advertisement.rssi,
                manufacturer_data=manufacturer_data,
                service_uuids=list(advertisement.service_uuids or []),
            )
        )
    devices.sort(key=lambda item: item.rssi if item.rssi is not None else -999, reverse=True)
    return devices


async def probe_device(
    identifier: str,
    *,
    timeout: float = 10.0,
) -> list[ServiceRecord]:
    """Connect to one BLE device and return its services/characteristics."""
    _require_bleak()
    async with BleakClient(identifier, timeout=timeout) as client:
        services = await client.get_services()
        records: list[ServiceRecord] = []
        for service in services:
            characteristics = [
                CharacteristicRecord(
                    uuid=characteristic.uuid,
                    properties=sorted(characteristic.properties),
                )
                for characteristic in service.characteristics
            ]
            records.append(ServiceRecord(uuid=service.uuid, characteristics=characteristics))
    return records
