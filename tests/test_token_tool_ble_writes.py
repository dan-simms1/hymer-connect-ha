"""Byte-level tests for the BLE control-write encoding.

These pin the encoding to what the decompiled app actually puts on the wire.
They are deliberately assertions about exact bytes rather than round-trips,
because the prior art's BLE writes round-tripped through its own encoder
perfectly while still being rejected by the SCU. Only agreement with the app
counts.

Reference: FINDINGS-ble-writes.md in the investigation package.
"""

from __future__ import annotations

from pathlib import Path
import sys
import unittest
import zlib

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools" / "hymer_token_tool"))

from hymer_token_tool import ble  # noqa: E402


def _read_key(buffer: bytes, offset: int) -> tuple[int, int, int]:
    """Return (field_number, wire_type, next_offset)."""
    key, next_offset = ble._decode_varint(buffer, offset)
    return key >> 3, key & 0x07, next_offset


def _fields(buffer: bytes) -> dict[int, list[bytes]]:
    """Collect length-delimited and varint fields by number, for assertions."""
    found: dict[int, list[bytes]] = {}
    offset = 0
    while offset < len(buffer):
        field_number, wire_type, offset = _read_key(buffer, offset)
        if wire_type == ble._WIRE_LEN:
            value, offset = ble._decode_length_delimited(buffer, offset)
        elif wire_type == ble._WIRE_VARINT:
            raw, offset = ble._decode_varint(buffer, offset)
            value = raw.to_bytes(8, "big")
        elif wire_type == ble._WIRE_FIXED32:
            value, offset = buffer[offset : offset + 4], offset + 4
        else:
            offset = ble._skip_protobuf_field(buffer, offset, wire_type)
            continue
        found.setdefault(field_number, []).append(value)
    return found


class InstanceEncodingTests(unittest.TestCase):
    """`instanceStringToBytes`: hyphen-separated base-16 parts, as raw bytes."""

    def test_hyphenated_hex_becomes_raw_bytes(self) -> None:
        self.assertEqual(ble.instance_string_to_bytes("01-0a-ff"), b"\x01\x0a\xff")

    def test_single_part_is_one_byte(self) -> None:
        self.assertEqual(ble.instance_string_to_bytes("0b"), b"\x0b")

    def test_parts_are_base_16_not_base_10(self) -> None:
        # "10" is 16, not 10. Parsing as decimal is a silent corruption.
        self.assertEqual(ble.instance_string_to_bytes("10"), b"\x10")

    def test_empty_instance_is_empty(self) -> None:
        self.assertEqual(ble.instance_string_to_bytes(""), b"")

    def test_invalid_instance_is_rejected(self) -> None:
        with self.assertRaises(ValueError):
            ble.instance_string_to_bytes("zz")


class ConnectedComponentValueTests(unittest.TestCase):
    """The value object the app's `toPiaValues` builds."""

    def test_id_and_component_id_are_always_set(self) -> None:
        encoded = ble.build_connected_component_value(
            value_id=7, component_id=42, bool_value=True
        )
        found = _fields(encoded)
        self.assertIn(ble._CC_VALUE_ID_FIELD, found)
        self.assertIn(ble._CC_VALUE_COMPONENT_ID_FIELD, found)
        self.assertEqual(int.from_bytes(found[ble._CC_VALUE_ID_FIELD][0], "big"), 7)
        self.assertEqual(
            int.from_bytes(found[ble._CC_VALUE_COMPONENT_ID_FIELD][0], "big"), 42
        )

    def test_connected_component_index_is_never_emitted(self) -> None:
        """Field 9 is the single clearest divergence in the prior art."""
        for kwargs in (
            {"bool_value": True},
            {"int_value": 5},
            {"string_value": "x"},
            {"float_value": 1.5},
        ):
            encoded = ble.build_connected_component_value(
                value_id=1, component_id=2, instance="01-02", **kwargs
            )
            self.assertNotIn(9, _fields(encoded), f"field 9 emitted for {kwargs}")

    def test_instance_is_omitted_when_absent_and_raw_bytes_when_present(self) -> None:
        without = ble.build_connected_component_value(
            value_id=1, component_id=2, bool_value=True
        )
        self.assertNotIn(ble._CC_VALUE_INSTANCE_FIELD, _fields(without))

        # An empty string is falsy in the app, so it must also emit nothing.
        empty = ble.build_connected_component_value(
            value_id=1, component_id=2, bool_value=True, instance=""
        )
        self.assertNotIn(ble._CC_VALUE_INSTANCE_FIELD, _fields(empty))

        with_instance = ble.build_connected_component_value(
            value_id=1, component_id=2, bool_value=True, instance="01-0a-ff"
        )
        self.assertEqual(
            _fields(with_instance)[ble._CC_VALUE_INSTANCE_FIELD][0], b"\x01\x0a\xff"
        )

    def test_each_datatype_uses_its_own_field(self) -> None:
        cases = (
            ({"int_value": 9}, ble._CC_VALUE_INT32_FIELD),
            ({"string_value": "auto"}, ble._CC_VALUE_STRING_FIELD),
            ({"bool_value": True}, ble._CC_VALUE_BOOL_FIELD),
            ({"float_value": 21.5}, ble._CC_VALUE_FLOAT_FIELD),
        )
        typed_fields = {
            ble._CC_VALUE_INT32_FIELD,
            ble._CC_VALUE_STRING_FIELD,
            ble._CC_VALUE_BOOL_FIELD,
            ble._CC_VALUE_FLOAT_FIELD,
        }
        for kwargs, expected_field in cases:
            found = _fields(
                ble.build_connected_component_value(
                    value_id=1, component_id=2, **kwargs
                )
            )
            self.assertIn(expected_field, found, f"{kwargs} missing field {expected_field}")
            # Exactly one typed field, never several.
            self.assertEqual(typed_fields & set(found), {expected_field})

    def test_float_is_little_endian_ieee754_single(self) -> None:
        found = _fields(
            ble.build_connected_component_value(
                value_id=1, component_id=2, float_value=21.5
            )
        )
        self.assertEqual(found[ble._CC_VALUE_FLOAT_FIELD][0], b"\x00\x00\xac\x41")

    def test_negative_int32_is_sign_extended(self) -> None:
        encoded = ble.build_connected_component_value(
            value_id=1, component_id=2, int_value=-1
        )
        # int32 -1 is ten 0xff/0x01 varint bytes, not a rejected negative.
        self.assertIn(b"\xff\xff\xff\xff\xff\xff\xff\xff\xff\x01", encoded)

    def test_exactly_one_typed_value_is_required(self) -> None:
        with self.assertRaises(ValueError):
            ble.build_connected_component_value(value_id=1, component_id=2)
        with self.assertRaises(ValueError):
            ble.build_connected_component_value(
                value_id=1, component_id=2, bool_value=True, int_value=1
            )


class SetValuesFrameTests(unittest.TestCase):
    """The full setValues frame, layer by layer."""

    def _build(self) -> tuple[bytes, int]:
        value = ble.build_connected_component_value(
            value_id=11, component_id=22, bool_value=True
        )
        return ble.build_set_values_ble_pia_frame(
            [value], request_id=4242, timestamp=1700000000
        )

    def test_frame_header_is_magic_length_and_valid_crc(self) -> None:
        frame, _ = self._build()
        self.assertTrue(frame.startswith(b"\xa0\xcb"))
        payload_length = int.from_bytes(frame[2:6], "big")
        self.assertEqual(payload_length, len(frame) - ble.BLE_PIA_HEADER_SIZE)
        # CRC is computed over the header with a zeroed CRC, plus the payload.
        expected = zlib.crc32(
            ble.build_ble_pia_header(payload_length, 0) + frame[ble.BLE_PIA_HEADER_SIZE :]
        )
        self.assertEqual(int.from_bytes(frame[6:10], "big"), expected & 0xFFFFFFFF)

    def test_payload_is_wrapped_as_ble_protocol_request_not_response(self) -> None:
        """The bug that silently killed upstream's writes.

        Field 1 is BleProtocol.request; field 2 is BleProtocol.response. A
        command sent in field 2 is parsed by the SCU as a response and
        discarded without any error.
        """
        frame, _ = self._build()
        payload = ble.decode_ble_pia_frame(frame).payload
        self.assertEqual(payload[0], 0x0A, "BleProtocol wrapper must be field 1, tag 0x0a")
        found = _fields(payload)
        self.assertIn(ble._BLE_PROTOCOL_REQUEST_FIELD, found)
        self.assertNotIn(ble._BLE_PROTOCOL_RESPONSE_FIELD, found)

    def test_request_envelope_carries_version_and_the_connected_component_topic(self) -> None:
        frame, request_id = self._build()
        payload = ble.decode_ble_pia_frame(frame).payload
        request = _fields(payload)[ble._BLE_PROTOCOL_REQUEST_FIELD][0]
        found = _fields(request)

        self.assertEqual(
            int.from_bytes(found[ble._REQUEST_REQUEST_ID_FIELD][0], "big"), request_id
        )
        self.assertEqual(found[ble._REQUEST_VERSION_FIELD][0], b"v0.32.0")
        self.assertEqual(
            int.from_bytes(found[ble._REQUEST_TIMESTAMP_FIELD][0], "big"), 1700000000
        )
        # Topic must be connectedComponent (4), not user (8) or command (9).
        self.assertIn(ble._REQUEST_CONNECTED_COMPONENT_FIELD, found)
        self.assertNotIn(ble._REQUEST_USER_FIELD, found)
        self.assertNotIn(ble._REQUEST_COMMAND_FIELD, found)

    def test_topic_nests_set_values_containing_the_repeated_values(self) -> None:
        first = ble.build_connected_component_value(
            value_id=11, component_id=22, bool_value=True
        )
        second = ble.build_connected_component_value(
            value_id=33, component_id=44, int_value=7
        )
        frame, _ = ble.build_set_values_ble_pia_frame(
            [first, second], request_id=1, timestamp=2
        )
        payload = ble.decode_ble_pia_frame(frame).payload
        request = _fields(payload)[ble._BLE_PROTOCOL_REQUEST_FIELD][0]
        topic = _fields(request)[ble._REQUEST_CONNECTED_COMPONENT_FIELD][0]
        set_values = _fields(topic)[ble._CC_REQUEST_TOPIC_SET_VALUES_FIELD][0]
        values = _fields(set_values)[ble._SET_VALUES_VALUE_FIELD]

        self.assertEqual(values, [first, second])

    def test_request_id_is_generated_when_not_supplied(self) -> None:
        frame, request_id = ble.build_set_values_ble_pia_frame(
            [ble.build_connected_component_value(
                value_id=1, component_id=2, bool_value=True
            )]
        )
        self.assertGreater(request_id, 0)
        request = _fields(ble.decode_ble_pia_frame(frame).payload)[
            ble._BLE_PROTOCOL_REQUEST_FIELD
        ][0]
        self.assertEqual(
            int.from_bytes(_fields(request)[ble._REQUEST_REQUEST_ID_FIELD][0], "big"),
            request_id,
        )

    def test_empty_value_list_is_rejected(self) -> None:
        with self.assertRaises(ValueError):
            ble.build_set_values_ble_pia_frame([])


class RestartFrameTests(unittest.TestCase):
    """Restart is a different topic from setValues, and must stay that way."""

    def test_restart_uses_the_command_topic_not_set_values(self) -> None:
        frame, _ = ble.build_restart_ble_pia_frame(request_id=5, timestamp=6)
        request = _fields(ble.decode_ble_pia_frame(frame).payload)[
            ble._BLE_PROTOCOL_REQUEST_FIELD
        ][0]
        found = _fields(request)
        self.assertIn(ble._REQUEST_COMMAND_FIELD, found)
        self.assertNotIn(ble._REQUEST_CONNECTED_COMPONENT_FIELD, found)

        command_topic = found[ble._REQUEST_COMMAND_FIELD][0]
        restart = _fields(command_topic)[ble._COMMAND_REQUEST_TOPIC_RESTART_FIELD][0]
        self.assertEqual(restart, b"\x08\x01")  # cold = true


class ResponseDecodingTests(unittest.TestCase):
    """Acknowledgement is a matching request id, nothing else."""

    def _response_frame(self, *, request_id: int, status: int | None) -> bytes:
        body = ble._encode_varint_field(ble._RESPONSE_REQUEST_ID_FIELD, request_id)
        if status is not None:
            body += ble._encode_varint_field(ble._RESPONSE_STATUS_FIELD, status)
        return ble.encode_ble_pia_frame(
            ble._encode_length_delimited_field(ble._BLE_PROTOCOL_RESPONSE_FIELD, body)
        )

    def test_request_id_and_status_are_recovered(self) -> None:
        decoded = ble.decode_ble_response_frame(
            self._response_frame(request_id=4242, status=ble.PIA_STATUS_SUCCESS)
        )
        self.assertEqual(decoded.request_id, 4242)
        self.assertEqual(decoded.status, ble.PIA_STATUS_SUCCESS)
        self.assertTrue(decoded.succeeded)

    def test_absent_or_zero_status_counts_as_success(self) -> None:
        for status in (None, 0):
            decoded = ble.decode_ble_response_frame(
                self._response_frame(request_id=1, status=status)
            )
            self.assertTrue(decoded.succeeded, f"status {status} should succeed")

    def test_error_status_does_not_count_as_success(self) -> None:
        # 15 is SCU_IS_NOT_ONLINE.
        decoded = ble.decode_ble_response_frame(
            self._response_frame(request_id=1, status=15)
        )
        self.assertFalse(decoded.succeeded)
        self.assertEqual(decoded.status, 15)

    def test_a_request_frame_is_not_mistaken_for_a_response(self) -> None:
        frame, _ = ble.build_set_values_ble_pia_frame(
            [ble.build_connected_component_value(
                value_id=1, component_id=2, bool_value=True
            )]
        )
        with self.assertRaises(ValueError):
            ble.decode_ble_response_frame(frame)

    def test_corrupt_crc_is_rejected(self) -> None:
        frame = bytearray(self._response_frame(request_id=1, status=1))
        frame[-1] ^= 0xFF
        with self.assertRaises(ValueError):
            ble.decode_ble_response_frame(bytes(frame))


class CloudEncodingAgreementTests(unittest.TestCase):
    """Cross-check the new BLE encoder against the proven cloud encoder.

    `pia_decoder.build_light_command` works against the live vehicle over
    SignalR. It was written from captured traffic; the BLE builders here were
    derived independently from the decompiled app. If both are right, the
    Request topic they produce for the same write must be byte-identical, and
    only the outer wrapper should differ: the cloud path wraps the Request in
    field 2 (the DataHub envelope), BLE wraps it in field 1
    (`BleProtocol.request`).

    That difference is precisely the bug that silently killed the prior art's
    BLE writes, so it is worth an executable assertion rather than a comment.
    """

    def setUp(self) -> None:
        import base64

        sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
        from custom_components.hymer_connect_metadata import pia_decoder

        self._pia_decoder = pia_decoder
        self._b64decode = base64.b64decode

    def _cloud_request(self, payload_b64: str) -> bytes:
        """Unwrap the cloud envelope (outer field 2) and return the Request."""
        raw = self._b64decode(payload_b64)
        self.assertEqual(raw[0], 0x12, "cloud payload should be wrapped in field 2")
        return _fields(raw)[2][0]

    def _ble_request(self, frame: bytes) -> bytes:
        payload = ble.decode_ble_pia_frame(frame).payload
        return _fields(payload)[ble._BLE_PROTOCOL_REQUEST_FIELD][0]

    def _assert_topics_match(self, *, cloud_b64: str, ble_frame: bytes) -> None:
        cloud_topic = _fields(self._cloud_request(cloud_b64))[4][0]
        ble_topic = _fields(self._ble_request(ble_frame))[
            ble._REQUEST_CONNECTED_COMPONENT_FIELD
        ][0]
        self.assertEqual(ble_topic, cloud_topic)

    def test_bool_write_topic_matches_the_cloud_encoder(self) -> None:
        bus_id, sensor_id = 11, 1
        cloud = self._pia_decoder.build_light_command(bus_id, sensor_id, bool_value=True)
        frame, _ = ble.build_set_values_ble_pia_frame(
            [ble.build_connected_component_value(
                value_id=sensor_id, component_id=bus_id, bool_value=True
            )],
            request_id=1,
            timestamp=2,
        )
        self._assert_topics_match(cloud_b64=cloud, ble_frame=frame)

    def test_brightness_write_topic_matches_the_cloud_encoder(self) -> None:
        bus_id, sensor_id = 11, 2
        cloud = self._pia_decoder.build_light_command(bus_id, sensor_id, uint_value=70)
        frame, _ = ble.build_set_values_ble_pia_frame(
            [ble.build_connected_component_value(
                value_id=sensor_id, component_id=bus_id, int_value=70
            )],
            request_id=1,
            timestamp=2,
        )
        self._assert_topics_match(cloud_b64=cloud, ble_frame=frame)

    def test_string_write_topic_matches_the_cloud_encoder(self) -> None:
        bus_id, sensor_id = 3, 1
        cloud = self._pia_decoder.build_light_command(bus_id, sensor_id, str_value="On")
        frame, _ = ble.build_set_values_ble_pia_frame(
            [ble.build_connected_component_value(
                value_id=sensor_id, component_id=bus_id, string_value="On"
            )],
            request_id=1,
            timestamp=2,
        )
        self._assert_topics_match(cloud_b64=cloud, ble_frame=frame)

    def test_the_two_transports_differ_only_in_the_outer_wrapper(self) -> None:
        cloud_raw = self._b64decode(
            self._pia_decoder.build_light_command(11, 1, bool_value=True)
        )
        frame, _ = ble.build_set_values_ble_pia_frame(
            [ble.build_connected_component_value(
                value_id=1, component_id=11, bool_value=True
            )]
        )
        ble_payload = ble.decode_ble_pia_frame(frame).payload

        # Cloud: field 2. BLE: field 1. Getting this backwards is the bug.
        self.assertEqual(cloud_raw[0], 0x12)
        self.assertEqual(ble_payload[0], 0x0A)


if __name__ == "__main__":
    unittest.main()
