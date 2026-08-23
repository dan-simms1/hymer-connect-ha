"""The integration's ble_pia must stay byte-identical to the proven tool encoder.

`custom_components/hymer_connect_metadata/ble_pia.py` is a clean-room-safe port
of `tools/hymer_token_tool/hymer_token_tool/ble.py` (the encoder proven on a real
vehicle 2026-08-22). HACS ships only `custom_components/`, so the integration
can't import the tool at runtime -- this pins the copy to the original so they
can't silently diverge.
"""

from __future__ import annotations

import importlib.util
import sys
import unittest
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
# Import the tool encoder as a package (it has its own namespace).
sys.path.insert(0, str(_ROOT / "tools" / "hymer_token_tool"))
from hymer_token_tool import ble as tool_ble


def _load_by_path(name: str, path: Path):
    """Load a module from a file without polluting sys.path.

    The integration dir contains a `select.py`; inserting it on sys.path would
    shadow the stdlib `select` module under `unittest discover`.
    """
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


integ_ble = _load_by_path(
    "_hymer_ble_pia_under_test",
    _ROOT / "custom_components" / "hymer_connect_metadata" / "ble_pia.py",
)


class BlePiaParityTests(unittest.TestCase):
    def test_connected_component_value_matches_for_every_datatype(self) -> None:
        cases = (
            {"bool_value": True},
            {"bool_value": False},
            {"int_value": 70},
            {"int_value": -1},
            {"string_value": "On"},
            {"float_value": 21.5},
        )
        for kw in cases:
            a = tool_ble.build_connected_component_value(value_id=1, component_id=11, **kw)
            b = integ_ble.build_connected_component_value(value_id=1, component_id=11, **kw)
            self.assertEqual(a, b, f"mismatch for {kw}")

    def test_instance_bytes_match(self) -> None:
        for inst in ("01-0a-ff", "0b", "10", ""):
            self.assertEqual(
                tool_ble.instance_string_to_bytes(inst),
                integ_ble.instance_string_to_bytes(inst),
            )

    def test_set_values_frame_matches(self) -> None:
        va = tool_ble.build_connected_component_value(value_id=1, component_id=11, bool_value=True)
        vb = integ_ble.build_connected_component_value(value_id=1, component_id=11, bool_value=True)
        fa, ra = tool_ble.build_set_values_ble_pia_frame([va], request_id=42, timestamp=99)
        fb, rb = integ_ble.build_set_values_ble_pia_frame([vb], request_id=42, timestamp=99)
        self.assertEqual(fa, fb)
        self.assertEqual(ra, rb)
        # And the property that matters: BleProtocol.request (field 1, tag 0x0a)
        self.assertEqual(integ_ble.decode_ble_pia_frame(fb).payload[0], 0x0A)

    def test_pairing_and_confirmation_frames_match(self) -> None:
        self.assertEqual(
            tool_ble.build_pair_mobile_ble_pia_frame("act", "conf", "dev", request_id=1, timestamp=2),
            integ_ble.build_pair_mobile_ble_pia_frame("act", "conf", "dev", request_id=1, timestamp=2),
        )
        self.assertEqual(
            tool_ble.build_pair_mobile_confirmation_ble_pia_frame(request_id=1, timestamp=2, success=True),
            integ_ble.build_pair_mobile_confirmation_ble_pia_frame(request_id=1, timestamp=2, success=True),
        )

    def test_restart_frame_matches(self) -> None:
        fa, ra = tool_ble.build_restart_ble_pia_frame(request_id=5, timestamp=6)
        fb, rb = integ_ble.build_restart_ble_pia_frame(request_id=5, timestamp=6)
        self.assertEqual((fa, ra), (fb, rb))

    def test_response_decoders_agree(self) -> None:
        body = tool_ble._encode_varint_field(1, 4242) + tool_ble._encode_varint_field(2, 1)
        frame = tool_ble.encode_ble_pia_frame(
            tool_ble._encode_length_delimited_field(2, body)
        )
        a = tool_ble.decode_ble_response_frame(frame)
        b = integ_ble.decode_ble_response_frame(frame)
        self.assertEqual((a.request_id, a.status, a.succeeded), (b.request_id, b.status, b.succeeded))


if __name__ == "__main__":
    unittest.main()
