"""BLE transport for the HYMER SCU, wired into Home Assistant's Bluetooth stack.

This is the local counterpart to the SignalR cloud client. It connects to the
SCU over Bluetooth, completes the legacy TLS handshake, and sends PIA commands
(`setValues`) with the encoding proven against a real vehicle on 2026-08-22 --
including the field-1 wrapper and write-with-response that the upstream project
was missing.

It exposes the same command surface the coordinator already uses for the cloud
path (`send_light_command`, `send_multi_sensor_command`, `connected`, `start`,
`stop`), so it can slot in as an alternate transport.

Connection strategy, in order of preference:
  1. Home Assistant's Bluetooth: `async_ble_device_from_address` +
     `bleak_retry_connector.establish_connection` (the supported HA path).
  2. Raw `bleak.BleakClient` (standalone / non-HA use, and a fallback).

Bonding: the SCU requires a link-layer bond. HA's managed Bluetooth API does
not do pairing, so -- exactly as the standalone tool does on a Pi -- we register
a `NoInputNoOutput` agent on the system D-Bus (via `dbus_fast`, which ships with
HA's Bluetooth stack) and call `Device1.Pair()`. This needs a *local* BlueZ
adapter; it cannot work over a remote Bluetooth proxy.
"""

from __future__ import annotations

import asyncio
from collections import deque
import logging
from typing import Any, Callable

from . import ble_pia
from .ble_tls import LegacyTlsClient, TlsSupportError

_LOGGER = logging.getLogger(__name__)

UART_RX = "6e400002-b5a3-f393-e0a9-e50e24dcca9e"  # write (host -> SCU)
UART_TX = "6e400003-b5a3-f393-e0a9-e50e24dcca9e"  # notify (SCU -> host)
POWER_CONTROL = "fff40003-13c9-42f3-9d46-e1d1aa2a7232"
WAKE_BYTE = b"\x0a"

APP_REQUESTED_MTU = 245
DEFAULT_MTU = 23
CONNECT_TIMEOUT = 20.0
TLS_TIMEOUT = 30.0
REQUEST_TIMEOUT = 30.0
UART_WRITE_PACING_S = 0.005


class BleTransportError(RuntimeError):
    """Raised when the BLE transport cannot proceed."""


def _values_from_light_args(
    bus_id: int,
    sensor_id: int,
    *,
    bool_value: bool | None,
    uint_value: int | None,
    str_value: str | None,
) -> bytes:
    """Map the coordinator's (bus, sensor, value) into a ConnectedComponentValue.

    bus_id is the component id and sensor_id the value id (proven on the
    vehicle). uint (brightness/level) is an int32; str is a string value.
    """
    return ble_pia.build_connected_component_value(
        value_id=sensor_id,
        component_id=bus_id,
        bool_value=bool_value,
        int_value=uint_value,
        string_value=str_value,
    )


def _value_from_sensor_dict(sensor: dict[str, Any]) -> bytes:
    """Map one multi-sensor dict into a ConnectedComponentValue."""
    kwargs: dict[str, Any] = {}
    if "bool_value" in sensor:
        kwargs["bool_value"] = bool(sensor["bool_value"])
    elif "uint_value" in sensor:
        kwargs["int_value"] = int(sensor["uint_value"])
    elif "int_value" in sensor:
        kwargs["int_value"] = int(sensor["int_value"])
    elif "float_value" in sensor:
        kwargs["float_value"] = float(sensor["float_value"])
    elif "str_value" in sensor:
        kwargs["string_value"] = str(sensor["str_value"])
    else:
        raise BleTransportError(f"sensor dict has no value: {sensor!r}")
    return ble_pia.build_connected_component_value(
        value_id=int(sensor["sensor_id"]),
        component_id=int(sensor["bus_id"]),
        instance=sensor.get("instance"),
        **kwargs,
    )


class HymerBleTransport:
    """A local BLE transport for one SCU, matching the cloud command surface."""

    def __init__(
        self,
        hass: Any,
        scu_address: str,
        *,
        adapter: str = "hci0",
        on_response: Callable[[bytes], None] | None = None,
    ) -> None:
        self._hass = hass
        self._address = scu_address
        self._adapter = adapter
        self._on_response = on_response
        self._client: Any = None
        self._tls: LegacyTlsClient | None = None
        self._mtu = DEFAULT_MTU
        self._write_chunk = 20
        self._inbound: asyncio.Queue[bytes] = asyncio.Queue()
        self._pending_frames: deque[bytes] = deque()
        self._frame_buffer = bytearray()
        self._notify_started = False

    # ------------------------------------------------------------------ status

    @property
    def connected(self) -> bool:
        client = self._client
        return bool(
            client is not None
            and getattr(client, "is_connected", False)
            and self._tls is not None
            and self._tls.handshake_complete
        )

    # ------------------------------------------------------------------ lifecycle

    async def start(self) -> None:
        """Connect, bond if needed, wake, and complete the TLS handshake."""
        await self._connect()
        await self._ensure_bonded()
        await self._acquire_mtu()
        await self._start_notify()
        await self._wake()
        await self._establish_tls()
        _LOGGER.info("BLE transport ready for %s", self._address)

    async def stop(self) -> None:
        client = self._client
        self._client = None
        self._tls = None
        self._notify_started = False
        if client is not None:
            try:
                await client.disconnect()
            except Exception as err:  # noqa: BLE001 - best effort
                _LOGGER.debug("BLE disconnect error for %s: %s", self._address, err)

    # ------------------------------------------------------------------ commands

    async def send_light_command(
        self,
        bus_id: int,
        sensor_id: int,
        *,
        bool_value: bool | None = None,
        uint_value: int | None = None,
        str_value: str | None = None,
    ) -> bool:
        value = _values_from_light_args(
            bus_id, sensor_id,
            bool_value=bool_value, uint_value=uint_value, str_value=str_value,
        )
        return await self._send_values([value])

    async def send_multi_sensor_command(self, sensors: list[dict]) -> bool:
        if not sensors:
            return True
        values = [_value_from_sensor_dict(s) for s in sensors]
        return await self._send_values(values)

    async def _send_values(self, values: list[bytes]) -> bool:
        if not self.connected:
            _LOGGER.warning("BLE transport not connected — cannot send")
            return False
        frame, request_id = ble_pia.build_set_values_ble_pia_frame(values)
        try:
            response = await self._send_frame_await_response(frame, request_id)
        except (BleTransportError, TlsSupportError) as err:
            _LOGGER.warning("BLE setValues failed: %s", err)
            return False
        if not response.succeeded:
            _LOGGER.warning(
                "SCU rejected BLE setValues: status=%s (request_id=%s)",
                response.status, response.request_id,
            )
        return response.succeeded

    # ------------------------------------------------------------------ pairing

    async def pair_mobile(
        self,
        activation_token: str,
        confirmation_token: str,
        mobile_device_name: str,
        *,
        send_confirmation: bool = True,
    ) -> Any:
        """Run the BLE PairMobile ceremony and return the decoded response.

        Requires a connected, bonded, TLS-established session (call start()
        first). The user must press the vehicle's CONNECTION button so the SCU
        accepts the pairing. Returns a BlePairMobileResponse carrying the minted
        remote access + refresh tokens.
        """
        if self._tls is None or not self._tls.handshake_complete:
            raise BleTransportError("TLS not established — call start() first")
        frame = ble_pia.build_pair_mobile_ble_pia_frame(
            activation_token, confirmation_token, mobile_device_name,
        )
        response_frame = await self._send_frame_await_any(frame, timeout=REQUEST_TIMEOUT)
        response = ble_pia.decode_pair_mobile_response_frame(response_frame)
        if send_confirmation:
            confirm = ble_pia.build_pair_mobile_confirmation_ble_pia_frame(success=True)
            await self._write_plaintext(confirm)
        return response

    # ------------------------------------------------------------------ connect

    async def _connect(self) -> None:
        from bleak import BleakClient  # local import so unit tests need no bleak

        ble_device = None
        try:
            from homeassistant.components.bluetooth import (
                async_ble_device_from_address,
            )

            ble_device = async_ble_device_from_address(
                self._hass, self._address.upper(), connectable=True
            )
        except Exception:  # noqa: BLE001 - HA bluetooth may be unavailable
            ble_device = None

        if ble_device is not None:
            try:
                from bleak_retry_connector import establish_connection

                self._client = await establish_connection(
                    BleakClient, ble_device, self._address
                )
                _LOGGER.debug("BLE connected to %s via HA retry-connector", self._address)
                return
            except Exception as err:  # noqa: BLE001 - fall back to raw bleak
                _LOGGER.debug(
                    "HA retry-connector failed for %s (%s) — raw BleakClient",
                    self._address, err,
                )

        client = BleakClient(self._address, timeout=CONNECT_TIMEOUT)
        await client.connect()
        self._client = client
        _LOGGER.debug("BLE connected to %s via raw BleakClient", self._address)

    async def _ensure_bonded(self) -> None:
        """Bond with the SCU via a system-D-Bus pairing agent if not already.

        Skipped silently when dbus_fast or a local BlueZ adapter is unavailable
        (e.g. connecting over a remote Bluetooth proxy) -- in that case the SCU
        must already be bonded out of band.
        """
        try:
            from dbus_fast import BusType, Variant
            from dbus_fast.aio import MessageBus
            from dbus_fast.service import ServiceInterface, method
        except ImportError:
            _LOGGER.debug("dbus_fast unavailable — assuming SCU is already bonded")
            return

        dev_path = f"/org/bluez/{self._adapter}/dev_{self._address.replace(':', '_')}"
        agent_path = "/org/bluez/agent_hymer_metadata"

        try:
            bus = await MessageBus(bus_type=BusType.SYSTEM).connect()
        except Exception as err:  # noqa: BLE001 - no system bus (non-HAOS)
            _LOGGER.debug("No system D-Bus (%s) — assuming SCU is already bonded", err)
            return

        try:
            intro = await bus.introspect("org.bluez", dev_path)
            dprops = bus.get_proxy_object("org.bluez", dev_path, intro).get_interface(
                "org.freedesktop.DBus.Properties"
            )
            paired = await dprops.call_get("org.bluez.Device1", "Paired")
            if paired.value:
                _LOGGER.debug("SCU %s already bonded", self._address)
                return

            class _Agent(ServiceInterface):
                def __init__(self, allowed: str) -> None:
                    super().__init__("org.bluez.Agent1")
                    self._allowed = allowed

                def _ok(self, device: str) -> None:
                    if device != self._allowed:
                        from dbus_fast import DBusError

                        raise DBusError("org.bluez.Error.Rejected", "not our device")

                @method()
                def Release(self):  # noqa: N802
                    pass

                @method()
                def RequestConfirmation(self, device: "o", passkey: "u"):  # noqa: F821,N802
                    self._ok(device)

                @method()
                def RequestAuthorization(self, device: "o"):  # noqa: F821,N802
                    self._ok(device)

                @method()
                def AuthorizeService(self, device: "o", uuid: "s"):  # noqa: F821,N802
                    self._ok(device)

                @method()
                def Cancel(self):  # noqa: N802
                    pass

            agent = _Agent(dev_path)
            bus.export(agent_path, agent)
            mintro = await bus.introspect("org.bluez", "/org/bluez")
            mgr = bus.get_proxy_object("org.bluez", "/org/bluez", mintro).get_interface(
                "org.bluez.AgentManager1"
            )
            await mgr.call_register_agent(agent_path, "NoInputNoOutput")
            _LOGGER.info("Pairing with SCU %s (press the CONNECTION button)", self._address)
            dev = bus.get_proxy_object("org.bluez", dev_path, intro).get_interface(
                "org.bluez.Device1"
            )
            try:
                await asyncio.wait_for(dev.call_pair(), timeout=REQUEST_TIMEOUT)
            finally:
                try:
                    await mgr.call_unregister_agent(agent_path)
                except Exception:  # noqa: BLE001
                    pass
            try:
                await dprops.call_set("org.bluez.Device1", "Trusted", Variant("b", True))
            except Exception:  # noqa: BLE001
                pass
            _LOGGER.info("Bonded with SCU %s", self._address)
        finally:
            bus.disconnect()

    async def _acquire_mtu(self) -> None:
        acquire = getattr(self._client, "_acquire_mtu", None)
        if acquire is not None:
            try:
                await acquire()
            except Exception as err:  # noqa: BLE001 - MTU stays at default
                _LOGGER.debug("MTU acquisition failed (%s) — default", err)
        mtu = getattr(self._client, "mtu_size", None)
        if isinstance(mtu, int) and mtu > 0:
            self._mtu = mtu
        self._write_chunk = max(20, min(242, self._mtu - 3))

    async def _start_notify(self) -> None:
        await self._client.start_notify(UART_TX, self._on_notify)
        self._notify_started = True

    async def _wake(self) -> None:
        try:
            await self._client.write_gatt_char(POWER_CONTROL, WAKE_BYTE, response=False)
        except Exception as err:  # noqa: BLE001 - wake is best-effort
            _LOGGER.debug("SCU wake write failed (%s) — continuing", err)

    # ------------------------------------------------------------------ TLS + IO

    async def _establish_tls(self) -> None:
        self._tls = LegacyTlsClient()
        self._pending_frames.clear()
        result = self._tls.begin_handshake()
        await self._write_records(result.outbound_tls_records)
        loop = asyncio.get_running_loop()
        deadline = loop.time() + TLS_TIMEOUT
        while not self._tls.handshake_complete:
            chunk = await self._next_uart(deadline)
            result = self._tls.feed_encrypted(chunk)
            await self._write_records(result.outbound_tls_records)

    def _on_notify(self, _sender: Any, data: bytearray) -> None:
        self._inbound.put_nowait(bytes(data))

    async def _next_uart(self, deadline: float) -> bytes:
        remaining = deadline - asyncio.get_running_loop().time()
        if remaining <= 0:
            raise BleTransportError("Timed out waiting for SCU BLE/TLS data")
        try:
            return await asyncio.wait_for(self._inbound.get(), timeout=remaining)
        except asyncio.TimeoutError as err:
            raise BleTransportError("Timed out waiting for SCU BLE/TLS data") from err

    async def _write_records(self, data: bytes) -> None:
        """Write TLS records to RX, chunked to the MTU and paced, with response."""
        if not data:
            return
        for offset in range(0, len(data), self._write_chunk):
            chunk = data[offset : offset + self._write_chunk]
            await self._client.write_gatt_char(UART_RX, chunk, response=True)
            if offset + self._write_chunk < len(data):
                await asyncio.sleep(UART_WRITE_PACING_S)

    async def _write_plaintext(self, plaintext: bytes) -> None:
        if self._tls is None or not self._tls.handshake_complete:
            raise BleTransportError("TLS not established")
        exchange = self._tls.encrypt_plaintext(plaintext)
        await self._write_records(exchange.outbound_tls_records)

    async def _next_frame(self, deadline: float) -> bytes:
        while True:
            if self._pending_frames:
                return self._pending_frames.popleft()
            incoming = await self._next_uart(deadline)
            exchange = self._tls.feed_encrypted(incoming)
            await self._write_records(exchange.outbound_tls_records)
            for chunk in exchange.plaintext_chunks:
                for frame in self._feed_frames(chunk):
                    self._pending_frames.append(frame)

    def _feed_frames(self, chunk: bytes) -> list[bytes]:
        """Reassemble plaintext into complete BLE PIA frames.

        A frame is ``A0 CB | length_be32 | crc32_be32 | payload``; TLS record
        boundaries don't align with frame boundaries, so buffer until whole
        frames are available.
        """
        self._frame_buffer.extend(chunk)
        magic = ble_pia.BLE_PIA_MAGIC
        header = ble_pia.BLE_PIA_HEADER_SIZE
        frames: list[bytes] = []
        buf = self._frame_buffer
        while True:
            idx = buf.find(magic)
            if idx < 0:
                # keep a trailing partial-magic byte, drop the rest
                if buf and buf[-1] == magic[0]:
                    del buf[:-1]
                else:
                    buf.clear()
                return frames
            if idx > 0:
                del buf[:idx]
            if len(buf) < header:
                return frames
            length = int.from_bytes(buf[2:6], "big")
            total = header + length
            if len(buf) < total:
                return frames
            frames.append(bytes(buf[:total]))
            del buf[:total]

    async def _send_frame_await_response(self, frame: bytes, request_id: int) -> Any:
        """Send a request and wait for the response with the matching id."""
        await self._write_plaintext(frame)
        deadline = asyncio.get_running_loop().time() + REQUEST_TIMEOUT
        while True:
            response_frame = await self._next_frame(deadline)
            try:
                response = ble_pia.decode_ble_response_frame(response_frame)
            except ValueError:
                continue  # a subscription push or frame we do not model
            if response.request_id == request_id:
                return response

    async def _send_frame_await_any(self, frame: bytes, *, timeout: float) -> bytes:
        await self._write_plaintext(frame)
        deadline = asyncio.get_running_loop().time() + timeout
        return await self._next_frame(deadline)


async def async_pair_over_ble(
    hass: Any,
    api: Any,
    scu_address: str,
    activation_token: str,
    *,
    mobile_device_name: str = "home-assistant",
) -> dict[str, str]:
    """Mint an EHG remote-access refresh token by pairing with the SCU over BLE.

    Orchestrates the whole ceremony:
      1. fetch a one-time confirmation token from the cloud (needs auth),
      2. connect + bond + TLS to the SCU over Bluetooth,
      3. send PairMobileRequest (the user must press the vehicle's CONNECTION
         button so the SCU accepts it),
      4. return the minted access + refresh tokens.

    Raises BleTransportError / the API's error on failure. The caller stores the
    refresh token in the config entry.
    """
    confirmation_token = await api.get_confirmation_token_value()
    transport = HymerBleTransport(hass, scu_address)
    try:
        await transport.start()
        response = await transport.pair_mobile(
            activation_token, confirmation_token, mobile_device_name
        )
    finally:
        await transport.stop()

    refresh = getattr(response, "remote_access_refresh_token", "")
    if not refresh:
        raise BleTransportError("Pairing completed but no refresh token was returned")
    return {
        "ehg_refresh_token": refresh,
        "ehg_access_token": getattr(response, "remote_access_token", ""),
        "confirmation_required": bool(getattr(response, "confirmation_required", False)),
    }
