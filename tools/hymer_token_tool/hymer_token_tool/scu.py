"""SCU BLE session helpers for live TLS and pairing attempts."""

from __future__ import annotations

import asyncio
from collections import deque
import contextlib
from dataclasses import asdict, dataclass
import platform
import secrets
from typing import Any

try:
    from bleak import BleakClient
except ImportError:  # pragma: no cover - handled at runtime
    BleakClient = None

from .ble import (
    BONDING_STATE_CHARACTERISTIC_UUID,
    BleSupportError,
    POWER_CONTROL_CHARACTERISTIC_UUID,
    POWER_STATE_CHARACTERISTIC_UUID,
    UART_RX_CHARACTERISTIC_UUID,
    UART_TX_CHARACTERISTIC_UUID,
    build_pair_mobile_ble_pia_frame,
    build_pair_mobile_confirmation_ble_pia_frame,
    decode_pair_mobile_response_frame,
    BLE_PIA_HEADER_SIZE,
    BLE_PIA_MAGIC,
)
from .tls import LegacyTlsClient, TlsSupportError

WAKE_UP_COMMAND = bytes((0x0A,))
APP_REQUESTED_MTU = 245
DEFAULT_GATT_MTU = 23
DEFAULT_CONNECT_TIMEOUT = 10.0
DEFAULT_TLS_TIMEOUT = 20.0
DEFAULT_WAKE_DELAY = 2.0


class ScuBleSessionError(RuntimeError):
    """Raised when the live SCU BLE session cannot proceed."""


@dataclass
class ScuTlsProbeResult:
    """Summary of a live SCU BLE + TLS probe."""

    identifier: str
    device_name: str
    bond_requested: bool
    bond_status: str
    mtu_size: int
    app_requested_mtu: int
    write_with_response: bool
    write_chunk_size: int
    power_state_before_hex: str | None
    power_state_after_wake_hex: str | None
    wake_up_sent: bool
    power_service_available: bool
    power_notifications_enabled: bool
    bonding_state_characteristic_exists: bool
    bonding_state_probe: dict[str, Any] | None
    negotiated_tls_version: str
    cipher_suite: str
    cipher_protocol: str
    cipher_bits: int

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class ScuPairingAttemptResult:
    """Summary of a live SCU mobile pairing attempt."""

    identifier: str
    device_name: str
    bond_requested: bool
    bond_status: str
    mtu_size: int
    app_requested_mtu: int
    write_with_response: bool
    write_chunk_size: int
    power_state_before_hex: str | None
    power_state_after_wake_hex: str | None
    wake_up_sent: bool
    mobile_device_name: str
    bonding_state_characteristic_exists: bool
    bonding_state_probe: dict[str, Any] | None
    negotiated_tls_version: str
    cipher_suite: str
    cipher_protocol: str
    cipher_bits: int
    confirmation_sent: bool
    pair_mobile_response: dict[str, Any]

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class BondingStateProbeResult:
    """Result of the SCU bonding-state challenge/echo check."""

    challenge_hex: str
    response_hex: str
    state_value: int

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class BlePiaFrameAccumulator:
    """Accumulate plaintext bytes until complete BLE PIA frames are available."""

    def __init__(self) -> None:
        self._buffer = bytearray()

    def clear(self) -> None:
        self._buffer.clear()

    def feed(self, data: bytes) -> list[bytes]:
        if data:
            self._buffer.extend(data)
        frames: list[bytes] = []
        while True:
            marker_index = self._buffer.find(BLE_PIA_MAGIC)
            if marker_index < 0:
                if self._buffer.endswith(BLE_PIA_MAGIC[:1]):
                    del self._buffer[:-1]
                else:
                    self._buffer.clear()
                return frames
            if marker_index > 0:
                del self._buffer[:marker_index]
            if len(self._buffer) < BLE_PIA_HEADER_SIZE:
                return frames
            payload_length = int.from_bytes(self._buffer[2:6], byteorder="big", signed=False)
            frame_length = BLE_PIA_HEADER_SIZE + payload_length
            if len(self._buffer) < frame_length:
                return frames
            frames.append(bytes(self._buffer[:frame_length]))
            del self._buffer[:frame_length]


class ScuBleSession:
    """Live BLE session for an SCU device."""

    def __init__(
        self,
        identifier: str,
        *,
        connect_timeout: float = DEFAULT_CONNECT_TIMEOUT,
        bond_on_connect: bool = False,
        write_chunk_size: int | None = None,
    ) -> None:
        self.identifier = identifier
        self.connect_timeout = connect_timeout
        self._default_bond_on_connect = bond_on_connect
        self._requested_write_chunk_size = write_chunk_size
        self._loop: asyncio.AbstractEventLoop | None = None
        self._client: BleakClient | None = None
        self._uart_notifications: asyncio.Queue[bytes] = asyncio.Queue()
        self._power_notifications: asyncio.Queue[bytes] = asyncio.Queue()
        self._frame_accumulator = BlePiaFrameAccumulator()
        self._pending_frames: deque[bytes] = deque()
        self._tls_client: LegacyTlsClient | None = None
        self._device_name = identifier
        self._mtu_size = DEFAULT_GATT_MTU
        self._write_with_response = True
        self._write_chunk_size = 20
        self._power_notifications_enabled = False
        self._uart_rx_characteristic: Any = None
        self._uart_tx_characteristic: Any = None
        self._power_state_characteristic: Any = None
        self._power_control_characteristic: Any = None
        self._bonding_state_characteristic: Any = None
        self._bond_requested = False
        self._bond_status = "not-requested"

    async def __aenter__(self) -> ScuBleSession:
        await self.connect(bond=self._default_bond_on_connect)
        return self

    async def __aexit__(self, exc_type: Any, exc: Any, tb: Any) -> None:
        await self.disconnect()

    @property
    def device_name(self) -> str:
        return self._device_name

    @property
    def mtu_size(self) -> int:
        return self._mtu_size

    @property
    def write_with_response(self) -> bool:
        return self._write_with_response

    @property
    def write_chunk_size(self) -> int:
        return self._write_chunk_size

    @property
    def power_service_available(self) -> bool:
        return self._power_state_characteristic is not None and self._power_control_characteristic is not None

    @property
    def power_notifications_enabled(self) -> bool:
        return self._power_notifications_enabled

    @property
    def bonding_state_characteristic_exists(self) -> bool:
        return self._bonding_state_characteristic is not None

    @property
    def bond_requested(self) -> bool:
        return self._bond_requested

    @property
    def bond_status(self) -> str:
        return self._bond_status

    async def connect(self, *, bond: bool = False) -> None:
        if BleakClient is None:
            raise BleSupportError(
                "BLE support is not available. Install the tool with its default "
                "dependencies and make sure the OS Bluetooth stack is available."
            )
        if self._client is not None:
            return

        self._loop = asyncio.get_running_loop()
        self._bond_requested = bond
        self._bond_status = "not-requested"
        client = BleakClient(self.identifier, timeout=self.connect_timeout)
        try:
            await client.connect()
            if bond:
                self._bond_status = await self._pair_client(client)
                if not _client_is_connected(client):
                    await client.connect()
                services = await client.get_services()
            else:
                services = getattr(client, "services", None)
                if services is None:
                    services = await client.get_services()
            if services is None:
                services = await client.get_services()
            self._client = client
            self._device_name = getattr(client, "name", None) or self.identifier
            mtu_size = getattr(client, "mtu_size", None)
            if isinstance(mtu_size, int) and mtu_size > 0:
                self._mtu_size = mtu_size

            self._uart_rx_characteristic = _find_characteristic(services, UART_RX_CHARACTERISTIC_UUID)
            self._uart_tx_characteristic = _find_characteristic(services, UART_TX_CHARACTERISTIC_UUID)
            self._power_state_characteristic = _find_characteristic(services, POWER_STATE_CHARACTERISTIC_UUID)
            self._power_control_characteristic = _find_characteristic(services, POWER_CONTROL_CHARACTERISTIC_UUID)
            self._bonding_state_characteristic = _find_characteristic(services, BONDING_STATE_CHARACTERISTIC_UUID)

            if self._uart_rx_characteristic is None or self._uart_tx_characteristic is None:
                raise ScuBleSessionError(
                    f"{self.identifier} does not expose the SCU UART characteristics"
                )

            rx_properties = _characteristic_properties(self._uart_rx_characteristic)
            self._write_with_response = _choose_write_mode(
                properties=rx_properties,
                identifier=self.identifier,
                description="UART RX characteristic",
            )
            self._write_chunk_size = _choose_write_chunk_size(
                mtu_size=self._mtu_size,
                requested=self._requested_write_chunk_size,
            )

            await client.start_notify(UART_TX_CHARACTERISTIC_UUID, self._handle_uart_notification)
            if self._power_state_characteristic is not None:
                power_properties = {
                    property_name.lower()
                    for property_name in self._power_state_characteristic.properties
                }
                if "notify" in power_properties:
                    await client.start_notify(
                        POWER_STATE_CHARACTERISTIC_UUID,
                        self._handle_power_notification,
                    )
                    self._power_notifications_enabled = True
        except Exception:
            with contextlib.suppress(Exception):
                await client.disconnect()
            raise

    async def disconnect(self) -> None:
        client = self._client
        self._client = None
        if client is None:
            return
        with contextlib.suppress(Exception):
            await client.stop_notify(UART_TX_CHARACTERISTIC_UUID)
        if self._power_notifications_enabled:
            with contextlib.suppress(Exception):
                await client.stop_notify(POWER_STATE_CHARACTERISTIC_UUID)
        with contextlib.suppress(Exception):
            await client.disconnect()

    async def read_power_state(self) -> str | None:
        if self._client is None:
            raise ScuBleSessionError("SCU session is not connected")
        if self._power_state_characteristic is None:
            return None
        data = await self._client.read_gatt_char(POWER_STATE_CHARACTERISTIC_UUID)
        return bytes(data).hex()

    async def read_bonding_state(self) -> BondingStateProbeResult | None:
        self._ensure_connected()
        if self._bonding_state_characteristic is None:
            return None
        properties = _characteristic_properties(self._bonding_state_characteristic)
        response = _choose_write_mode(
            properties=properties,
            identifier=self.identifier,
            description="bonding-state characteristic",
        )
        challenge = secrets.token_bytes(4)
        await self._client.write_gatt_char(
            BONDING_STATE_CHARACTERISTIC_UUID,
            challenge,
            response=response,
        )
        payload = bytes(await self._client.read_gatt_char(BONDING_STATE_CHARACTERISTIC_UUID))
        if len(payload) < 5:
            raise ScuBleSessionError(
                f"{self.identifier} returned a short bonding-state payload: {payload.hex()}"
            )
        if payload[:4] != challenge:
            raise ScuBleSessionError(
                f"{self.identifier} returned an unexpected bonding-state echo: "
                f"challenge={challenge.hex()} response={payload.hex()}"
            )
        return BondingStateProbeResult(
            challenge_hex=challenge.hex(),
            response_hex=payload.hex(),
            state_value=int(payload[4]),
        )

    async def wake_up(self) -> None:
        if self._client is None:
            raise ScuBleSessionError("SCU session is not connected")
        if self._power_control_characteristic is None:
            raise ScuBleSessionError("SCU power-control characteristic is not available")
        properties = _characteristic_properties(self._power_control_characteristic)
        response = _choose_write_mode(
            properties=properties,
            identifier=self.identifier,
            description="power-control characteristic",
        )
        await self._client.write_gatt_char(
            POWER_CONTROL_CHARACTERISTIC_UUID,
            WAKE_UP_COMMAND,
            response=response,
        )

    async def establish_tls(self, *, timeout: float = DEFAULT_TLS_TIMEOUT) -> dict[str, Any]:
        self._ensure_connected()
        if self._tls_client is not None and self._tls_client.handshake_complete:
            return self._tls_metadata()

        self._clear_runtime_buffers()
        self._tls_client = LegacyTlsClient()
        exchange = self._tls_client.begin_handshake()
        await self._write_tls_records(exchange.outbound_tls_records)
        deadline = self._loop.time() + timeout if self._loop is not None else None
        while not exchange.handshake_complete:
            incoming = await self._next_uart_packet(deadline)
            exchange = self._tls_client.feed_encrypted(incoming)
            await self._write_tls_records(exchange.outbound_tls_records)
            self._buffer_plaintext_chunks(exchange.plaintext_chunks)
        return self._tls_metadata()

    async def probe_tls(
        self,
        *,
        wake_up: bool = False,
        wake_delay: float = DEFAULT_WAKE_DELAY,
        probe_bonding_state: bool = False,
        timeout: float = DEFAULT_TLS_TIMEOUT,
    ) -> ScuTlsProbeResult:
        bonding_state_probe = None
        if probe_bonding_state:
            probe = await self.read_bonding_state()
            if probe is not None:
                bonding_state_probe = probe.to_dict()
        power_state_before = await self.read_power_state()
        power_state_after_wake = None
        if wake_up:
            await self.wake_up()
            if wake_delay > 0:
                await asyncio.sleep(wake_delay)
            power_state_after_wake = await self.read_power_state()
        tls_metadata = await self.establish_tls(timeout=timeout)
        return ScuTlsProbeResult(
            identifier=self.identifier,
            device_name=self.device_name,
            bond_requested=self.bond_requested,
            bond_status=self.bond_status,
            mtu_size=self.mtu_size,
            app_requested_mtu=APP_REQUESTED_MTU,
            write_with_response=self.write_with_response,
            write_chunk_size=self.write_chunk_size,
            power_state_before_hex=power_state_before,
            power_state_after_wake_hex=power_state_after_wake,
            wake_up_sent=wake_up,
            power_service_available=self.power_service_available,
            power_notifications_enabled=self.power_notifications_enabled,
            bonding_state_characteristic_exists=self.bonding_state_characteristic_exists,
            bonding_state_probe=bonding_state_probe,
            negotiated_tls_version=str(tls_metadata["negotiated_tls_version"]),
            cipher_suite=str(tls_metadata["cipher_suite"]),
            cipher_protocol=str(tls_metadata["cipher_protocol"]),
            cipher_bits=int(tls_metadata["cipher_bits"]),
        )

    async def pair_mobile_device(
        self,
        *,
        activation_token: str,
        confirmation_token: str,
        mobile_device_name: str | None = None,
        wake_up: bool = False,
        wake_delay: float = DEFAULT_WAKE_DELAY,
        probe_bonding_state: bool = False,
        timeout: float = DEFAULT_TLS_TIMEOUT,
        send_confirmation: bool = True,
    ) -> ScuPairingAttemptResult:
        bonding_state_probe = None
        if probe_bonding_state:
            probe = await self.read_bonding_state()
            if probe is not None:
                bonding_state_probe = probe.to_dict()
        power_state_before = await self.read_power_state()
        power_state_after_wake = None
        if wake_up:
            await self.wake_up()
            if wake_delay > 0:
                await asyncio.sleep(wake_delay)
            power_state_after_wake = await self.read_power_state()

        tls_metadata = await self.establish_tls(timeout=timeout)
        friendly_device_name = mobile_device_name or default_mobile_device_name()
        pair_request = build_pair_mobile_ble_pia_frame(
            activation_token,
            confirmation_token,
            friendly_device_name,
        )
        pair_response_frame = await self._send_application_data_and_wait_for_frame(
            pair_request,
            timeout=timeout,
        )
        pair_response = decode_pair_mobile_response_frame(pair_response_frame)

        confirmation_sent = False
        if send_confirmation:
            confirmation_frame = build_pair_mobile_confirmation_ble_pia_frame(success=True)
            await self._send_application_data(confirmation_frame)
            confirmation_sent = True

        return ScuPairingAttemptResult(
            identifier=self.identifier,
            device_name=self.device_name,
            bond_requested=self.bond_requested,
            bond_status=self.bond_status,
            mtu_size=self.mtu_size,
            app_requested_mtu=APP_REQUESTED_MTU,
            write_with_response=self.write_with_response,
            write_chunk_size=self.write_chunk_size,
            power_state_before_hex=power_state_before,
            power_state_after_wake_hex=power_state_after_wake,
            wake_up_sent=wake_up,
            mobile_device_name=friendly_device_name,
            bonding_state_characteristic_exists=self.bonding_state_characteristic_exists,
            bonding_state_probe=bonding_state_probe,
            negotiated_tls_version=str(tls_metadata["negotiated_tls_version"]),
            cipher_suite=str(tls_metadata["cipher_suite"]),
            cipher_protocol=str(tls_metadata["cipher_protocol"]),
            cipher_bits=int(tls_metadata["cipher_bits"]),
            confirmation_sent=confirmation_sent,
            pair_mobile_response=pair_response.to_dict(),
        )

    async def _send_application_data_and_wait_for_frame(
        self,
        plaintext: bytes,
        *,
        timeout: float,
    ) -> bytes:
        await self._send_application_data(plaintext)
        deadline = self._loop.time() + timeout if self._loop is not None else None
        while True:
            if self._pending_frames:
                return self._pending_frames.popleft()
            incoming = await self._next_uart_packet(deadline)
            exchange = self._tls_client.feed_encrypted(incoming)
            await self._write_tls_records(exchange.outbound_tls_records)
            self._buffer_plaintext_chunks(exchange.plaintext_chunks)

    async def _send_application_data(self, plaintext: bytes) -> None:
        if self._tls_client is None or not self._tls_client.handshake_complete:
            raise ScuBleSessionError("TLS session is not established")
        exchange = self._tls_client.encrypt_plaintext(plaintext)
        await self._write_tls_records(exchange.outbound_tls_records)

    async def _write_tls_records(self, data: bytes) -> None:
        if not data:
            return
        self._ensure_connected()
        for offset in range(0, len(data), self._write_chunk_size):
            chunk = data[offset : offset + self._write_chunk_size]
            await self._client.write_gatt_char(
                UART_RX_CHARACTERISTIC_UUID,
                chunk,
                response=self._write_with_response,
            )

    async def _next_uart_packet(self, deadline: float | None) -> bytes:
        if deadline is None:
            return await self._uart_notifications.get()
        remaining = deadline - self._loop.time()
        if remaining <= 0:
            raise ScuBleSessionError("Timed out waiting for SCU BLE/TLS data")
        try:
            return await asyncio.wait_for(self._uart_notifications.get(), timeout=remaining)
        except asyncio.TimeoutError as err:
            raise ScuBleSessionError("Timed out waiting for SCU BLE/TLS data") from err

    def _buffer_plaintext_chunks(self, chunks: list[bytes]) -> None:
        for chunk in chunks:
            for frame in self._frame_accumulator.feed(chunk):
                self._pending_frames.append(frame)

    def _clear_runtime_buffers(self) -> None:
        self._frame_accumulator.clear()
        self._pending_frames.clear()
        _clear_queue(self._uart_notifications)
        _clear_queue(self._power_notifications)

    def _handle_uart_notification(self, _: Any, data: Any) -> None:
        if self._loop is None:
            return
        payload = bytes(data)
        self._loop.call_soon_threadsafe(self._uart_notifications.put_nowait, payload)

    def _handle_power_notification(self, _: Any, data: Any) -> None:
        if self._loop is None:
            return
        payload = bytes(data)
        self._loop.call_soon_threadsafe(self._power_notifications.put_nowait, payload)

    def _ensure_connected(self) -> None:
        if self._client is None:
            raise ScuBleSessionError("SCU session is not connected")

    def _tls_metadata(self) -> dict[str, Any]:
        if self._tls_client is None or not self._tls_client.handshake_complete:
            raise ScuBleSessionError("TLS session is not established")
        return self._tls_client.connection_info()

    async def _pair_client(self, client: BleakClient) -> str:
        pair_method = getattr(client, "pair", None)
        if not callable(pair_method):
            if platform.system() == "Darwin":
                return "unsupported-backend"
            raise ScuBleSessionError(
                "The installed BLE backend does not expose explicit bonding/pairing support"
            )
        try:
            result = await pair_method()
        except NotImplementedError:
            if platform.system() == "Darwin":
                return "unsupported-backend"
            raise
        except Exception as err:
            raise ScuBleSessionError(
                f"BLE bonding failed for {self.identifier}: {err}"
            ) from err
        if result is False:
            raise ScuBleSessionError(f"BLE bonding did not complete for {self.identifier}")
        if result is True:
            return "requested-succeeded"
        return "requested-no-result"


def default_mobile_device_name() -> str:
    candidate = platform.node().strip()
    return candidate or "hymer-token-tool"


def _find_characteristic(services: Any, uuid: str) -> Any:
    wanted = uuid.lower()
    for service in services:
        for characteristic in service.characteristics:
            if str(characteristic.uuid).lower() == wanted:
                return characteristic
    return None


def _characteristic_properties(characteristic: Any) -> set[str]:
    return {str(property_name).lower() for property_name in characteristic.properties}


def _choose_write_mode(
    *,
    properties: set[str],
    identifier: str,
    description: str,
) -> bool:
    # The decompiled Android manager uses PROPERTY_WRITE when it is available
    # (setWriteType(2)) and falls back to write-without-response otherwise.
    if "write" in properties:
        return True
    if "write-without-response" in properties:
        return False
    raise ScuBleSessionError(
        f"{identifier} {description} is not writable: {sorted(properties)}"
    )


def _choose_write_chunk_size(*, mtu_size: int, requested: int | None) -> int:
    if requested is not None:
        if requested <= 0:
            raise ScuBleSessionError("write chunk size must be positive")
        return requested
    negotiated_payload = max(DEFAULT_GATT_MTU - 3, mtu_size - 3)
    return max(20, min(242, negotiated_payload))


def _clear_queue(queue: asyncio.Queue[bytes]) -> None:
    while True:
        try:
            queue.get_nowait()
        except asyncio.QueueEmpty:
            return


def _client_is_connected(client: Any) -> bool:
    value = getattr(client, "is_connected", False)
    return value() if callable(value) else bool(value)
