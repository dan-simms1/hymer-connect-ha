"""Register a BlueZ pairing agent and pair with one specific SCU, in one process.

bluetoothctl is unusable here: it exits when its stdin closes, spams ANSI
prompts, and its agent dies with it. BlueZ needs an agent registered for the
whole exchange, so agent and Pair() must live in the same process.

The agent only ever answers for the device named in SCU. Any request that
arrives for a different device is rejected, so leaving this running during the
vehicle's pairing window cannot auto-accept a stranger. The agent is registered
for this D-Bus connection only; BlueZ routes Pair() requests to the caller's
own agent, so it does not need to become the system default. Set
AGENT_DEFAULT=1 to opt into that for troubleshooting.

Environment: SCU (required), ADAPTER (default hci0), AGENT_DEFAULT (opt-in).
Exit 0 = bonded, 1 = not bonded, 4 = bad inputs.
"""

from __future__ import annotations

import asyncio
import os
import sys

sys.path.insert(0, os.environ.get("BLE_LIBS", "/storage/hymer_libs"))

from dbus_fast import BusType, DBusError, Variant  # noqa: E402
from dbus_fast.aio import MessageBus  # noqa: E402
from dbus_fast.service import ServiceInterface, method  # noqa: E402

AGENT_PATH = "/hymer/agent"


class Agent(ServiceInterface):
    """NoInputNoOutput agent that auto-accepts for exactly one device."""

    def __init__(self, allowed_device_path: str) -> None:
        super().__init__("org.bluez.Agent1")
        self._allowed = allowed_device_path

    def _check(self, device: str, what: str) -> None:
        if device != self._allowed:
            print(f"    {what} from {device} -> REJECTED (not our SCU)", flush=True)
            raise DBusError("org.bluez.Error.Rejected", "not the device being paired")
        print(f"    {what} -> accept", flush=True)

    @method()
    def Release(self):  # noqa: N802 - BlueZ interface name
        print("    agent released", flush=True)

    @method()
    def RequestPinCode(self, device: "o") -> "s":  # noqa: F821,N802
        self._check(device, "RequestPinCode")
        return "0000"

    @method()
    def RequestPasskey(self, device: "o") -> "u":  # noqa: F821,N802
        self._check(device, "RequestPasskey")
        return 0

    @method()
    def DisplayPasskey(self, device: "o", passkey: "u", entered: "q"):  # noqa: F821,N802
        self._check(device, f"DisplayPasskey {passkey}")

    @method()
    def DisplayPinCode(self, device: "o", pincode: "s"):  # noqa: F821,N802
        self._check(device, f"DisplayPinCode {pincode}")

    @method()
    def RequestConfirmation(self, device: "o", passkey: "u"):  # noqa: F821,N802
        self._check(device, f"RequestConfirmation {passkey}")

    @method()
    def RequestAuthorization(self, device: "o"):  # noqa: F821,N802
        self._check(device, "RequestAuthorization")

    @method()
    def AuthorizeService(self, device: "o", uuid: "s"):  # noqa: F821,N802
        self._check(device, f"AuthorizeService {uuid}")

    @method()
    def Cancel(self):  # noqa: N802
        print("    pairing cancelled by remote", flush=True)


async def main() -> int:
    scu = os.environ.get("SCU")
    if not scu:
        print("error: SCU=<ble-address> is required", file=sys.stderr)
        return 4
    adapter = os.environ.get("ADAPTER", "hci0")
    dev_path = f"/org/bluez/{adapter}/dev_{scu.replace(':', '_')}"

    bus = await MessageBus(bus_type=BusType.SYSTEM).connect()
    agent = Agent(dev_path)
    bus.export(AGENT_PATH, agent)
    mgr_intro = await bus.introspect("org.bluez", "/org/bluez")
    mgr = bus.get_proxy_object("org.bluez", "/org/bluez", mgr_intro).get_interface(
        "org.bluez.AgentManager1"
    )
    await mgr.call_register_agent(AGENT_PATH, "NoInputNoOutput")
    if os.environ.get("AGENT_DEFAULT") == "1":
        await mgr.call_request_default_agent(AGENT_PATH)
        print("[agent] registered as DEFAULT (opt-in)", flush=True)
    else:
        print(f"[agent] registered for this connection only; accepts {dev_path} only", flush=True)

    try:
        a_intro = await bus.introspect("org.bluez", f"/org/bluez/{adapter}")
        a_props = bus.get_proxy_object(
            "org.bluez", f"/org/bluez/{adapter}", a_intro
        ).get_interface("org.freedesktop.DBus.Properties")
        await a_props.call_set("org.bluez.Adapter1", "Powered", Variant("b", True))

        try:
            d_intro = await bus.introspect("org.bluez", dev_path)
        except Exception as err:  # noqa: BLE001 - D-Bus error text is the diagnosis
            print(f"[pair] device {dev_path} not known to BlueZ: {err}", flush=True)
            print("[pair] run a scan first so BlueZ creates the device object", flush=True)
            return 1
        d_obj = bus.get_proxy_object("org.bluez", dev_path, d_intro)
        dev = d_obj.get_interface("org.bluez.Device1")
        d_props = d_obj.get_interface("org.freedesktop.DBus.Properties")

        if (await d_props.call_get("org.bluez.Device1", "Paired")).value:
            print("[pair] ALREADY BONDED", flush=True)
            return 0

        print("[pair] calling Pair() ...", flush=True)
        try:
            await asyncio.wait_for(dev.call_pair(), timeout=40)
        except asyncio.TimeoutError:
            print("[pair] Pair() timed out", flush=True)
            return 1
        except Exception as err:  # noqa: BLE001
            print(f"[pair] Pair() failed: {err}", flush=True)
            return 1

        if (await d_props.call_get("org.bluez.Device1", "Paired")).value:
            try:
                await d_props.call_set("org.bluez.Device1", "Trusted", Variant("b", True))
            except Exception:  # noqa: BLE001 - trust is a convenience
                pass
            print("[pair] BONDED", flush=True)
            return 0
        return 1
    finally:
        try:
            await mgr.call_unregister_agent(AGENT_PATH)
        except Exception:  # noqa: BLE001 - best effort on the way out
            pass
        bus.disconnect()


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
