"""Connect to an SCU, do TLS, and send exactly one confirmed setValues write.

Runs on Linux/BlueZ. Success is judged only on a response whose request id
matches; the GATT write completing and the light appearing to change are not
evidence. Nothing is sent to the vehicle unless CONFIRM=1.

Environment:
  SCU           BLE address of the SCU (required)
  COMPONENT_ID  numeric component id of the write target (required)
  VALUE_ID      numeric value id of the write target (required)
  CONFIRM       must be "1" to actuate
  WR            "1" write-with-response (default), "0" without
  BOND          "1" to request a bond during connect (default "0")
  TOOL_ROOT     where the token tool lives (default /storage/hymer/tools/hymer_token_tool)

Exit codes: 0 accepted, 1 error, 2 rejected by the SCU, 3 not confirmed,
4 bad inputs.
"""

from __future__ import annotations

import asyncio
import os
import sys

sys.path.insert(0, os.environ.get("TOOL_ROOT", "/storage/hymer/tools/hymer_token_tool"))

from hymer_token_tool import ble  # noqa: E402
from hymer_token_tool.scu import ScuBleSession  # noqa: E402


def _required_int(name: str) -> int:
    raw = os.environ.get(name)
    if raw is None or not raw.strip().lstrip("-").isdigit():
        print(f"error: {name} must be set to an integer", file=sys.stderr)
        raise SystemExit(4)
    return int(raw)


async def main() -> int:
    scu = os.environ.get("SCU")
    if not scu:
        print("error: SCU=<ble-address> is required", file=sys.stderr)
        return 4
    component_id = _required_int("COMPONENT_ID")
    value_id = _required_int("VALUE_ID")
    if os.environ.get("CONFIRM") != "1":
        print("refusing to actuate the vehicle without CONFIRM=1", file=sys.stderr)
        return 3
    with_response = os.environ.get("WR", "1") == "1"
    bond = os.environ.get("BOND", "0") == "1"

    session = ScuBleSession(scu, connect_timeout=25.0)
    try:
        print(f"[1] connecting to {scu} (bond={bond}) ...", flush=True)
        await session.connect(bond=bond)
        session.set_write_with_response(with_response)
        print(
            f"    connected bond={session.bond_status} mtu={session.mtu_size} "
            f"write_with_response={session.write_with_response}",
            flush=True,
        )
        try:
            await session.wake_up()
            print("    wake byte sent", flush=True)
        except Exception as err:  # noqa: BLE001 - wake is best-effort
            print(f"    wake skipped: {err}", flush=True)

        print("[2] TLS handshake ...", flush=True)
        info = await session.establish_tls(timeout=30.0)
        print(f"    TLS OK: {info['negotiated_tls_version']} {info['cipher_suite']}", flush=True)

        print(f"[3] setValues component={component_id} value={value_id} bool=true ...", flush=True)
        value = ble.build_connected_component_value(
            value_id=value_id, component_id=component_id, bool_value=True
        )
        resp = await session.set_values([value], timeout=30.0)
        print(
            f"    RESPONSE request_id={resp.request_id} status={resp.status} "
            f"accepted={resp.succeeded}",
            flush=True,
        )
        if resp.succeeded:
            print("    >>> SUCCESS - BLE WRITE ACCEPTED", flush=True)
            return 0
        print("    >>> REJECTED by SCU", flush=True)
        return 2
    except Exception as err:  # noqa: BLE001 - reported, then non-zero exit
        print(f"    FAILED [{type(err).__name__}]: {err}", file=sys.stderr, flush=True)
        return 1
    finally:
        await session.disconnect()
        print("    disconnected", flush=True)


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
