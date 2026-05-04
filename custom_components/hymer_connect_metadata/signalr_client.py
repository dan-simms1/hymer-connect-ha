"""SignalR client for the HYMER Connect datahub.

Connects to Azure SignalR Service via the scc-appcomm negotiate endpoint
and exchanges PiaRequest/PiaResponse messages over WebSocket.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
import json
import logging
import time
from typing import Any, Callable

import aiohttp

from .api import HymerConnectApi, HymerConnectApiError
from .capability_resolver import main_switch_slots
from .const import USER_AGENT
from .pia_decoder import (
    build_restart_system_request,
    decode_pia_slots,
    decode_pia_slots_bytes,
    decode_transport_response,
    extract_request_id_from_payload,
    STATUS_AUTH_TOKEN_EXPIRED,
    STATUS_REMOTE_TOKEN_EXPIRED,
    STATUS_SUCCESS,
    build_subscription_requests,
    build_light_command,
    build_multi_sensor_command,
)
from .slot_actions import serialize_slot_action

_LOGGER = logging.getLogger(__name__)

# SignalR protocol constants
SIGNALR_RECORD_SEPARATOR = "\x1e"
MSG_TYPE_INVOCATION = 1
MSG_TYPE_COMPLETION = 3
MSG_TYPE_PING = 6

PIA_REQUEST_TIMEOUT = 30.0
STANDBY_WAKE_UPDATE_TOKENS_DELAY = 3.0
STANDBY_WAKE_RESUBSCRIBE_DELAY = 0.75


def _is_closed_transport_error(err: RuntimeError) -> bool:
    """Return True when aiohttp/httpx reports a closed client/session."""
    text = str(err).lower()
    return "session is closed" in text or "client has been closed" in text


@dataclass
class _PendingPiaRequest:
    """One queued/in-flight PIA request awaiting a DataHub response."""

    payload: str
    future: asyncio.Future[dict[str, Any]]
    sent: bool = False


def _main_switch_slots() -> frozenset[tuple[int, int]]:
    """Load main-switch provider slots lazily from provider metadata."""
    return main_switch_slots()


class HymerSignalRClient:
    """SignalR WebSocket client for the HYMER datahub."""

    def __init__(
        self,
        api: HymerConnectApi,
        session: aiohttp.ClientSession,
        vehicle_urn: str,
        scu_urn: str,
        ehg_refresh_token: str = "",
        on_sensor_update: Callable[[dict[tuple[int, int], Any]], None] | None = None,
        on_connection_lost: Callable[[], None] | None = None,
        known_slots: set[tuple[int, int]] | frozenset[tuple[int, int]] | None = None,
    ) -> None:
        """Initialize the SignalR client."""
        self._api = api
        self._session = session
        self._vehicle_urn = vehicle_urn
        self._scu_urn = scu_urn
        self._ehg_refresh_token = ehg_refresh_token  # Long-lived refresh token (ett=access-refresh)
        self._on_sensor_update = on_sensor_update
        self._on_connection_lost = on_connection_lost
        self._ws: aiohttp.ClientWebSocketResponse | None = None
        self._running = False
        self._task: asyncio.Task | None = None
        # slot-keyed (raw) data used by the discovery-driven entity layer
        self._slot_data: dict[tuple[int, int], Any] = {}
        self._connected = False
        self._signalr_token: str = ""
        self._connected_at: float = 0.0  # monotonic timestamp of connection
        self._last_data_received: float = 0.0  # monotonic timestamp of last data
        self._connection_lost_notified = False
        self._completion_futures: dict[str, asyncio.Future[bool]] = {}
        self._next_invocation_id = 0
        self._pending_requests: dict[int, _PendingPiaRequest] = {}
        self._waiting_request_ids: list[int] = []
        self._token_refresh_task: asyncio.Task | None = None
        self._standby_wake_refresh_task: asyncio.Task | None = None
        self._known_slots = frozenset(known_slots or ())

    @property
    def connected(self) -> bool:
        """Return True if the WebSocket is connected and healthy."""
        if not self._connected or not self._ws or self._ws.closed:
            return False
        return True

    @staticmethod
    def _is_standby_value(value: Any) -> bool:
        """Return True if a main-switch value indicates habitation standby."""
        if isinstance(value, str):
            return value.upper() == "OFF"
        if isinstance(value, bool):
            return value is False
        return False

    def _apply_optimistic_main_switch_state(
        self,
        bus_id: int,
        sensor_id: int,
        value: Any,
    ) -> None:
        """Log a main-switch command ack without mutating transport state."""
        slot = (bus_id, sensor_id)
        if slot not in _main_switch_slots():
            return
        _LOGGER.debug(
            "Main switch command accepted for %s on %s; waiting for SCU readback",
            value,
            slot,
        )

    def _is_vehicle_standby(self) -> bool:
        """Return True when any known main-switch provider reports standby."""
        return any(
            self._is_standby_value(self._slot_data.get(slot))
            for slot in _main_switch_slots()
        )

    @property
    def needs_reconnect(self) -> bool:
        """Return False for healthy sessions; reconnect is transport-driven."""
        return False

    def _notify_connection_lost(self) -> None:
        """Notify the coordinator once that this connection is no longer usable."""
        if self._connection_lost_notified:
            return
        self._connection_lost_notified = True
        if self._on_connection_lost:
            self._on_connection_lost()

    def mark_disconnected(self) -> None:
        """Mark the current connection unusable so callers trigger a reconnect."""
        self._connected = False

    def _next_completion_invocation_id(self) -> str:
        """Return the next SignalR invocation id."""
        self._next_invocation_id += 1
        return str(self._next_invocation_id)

    def _clear_token_refresh_task(self, task: asyncio.Task) -> None:
        """Drop the token-refresh task reference once it finishes."""
        if self._token_refresh_task is task:
            self._token_refresh_task = None
        if task.cancelled():
            return
        exc = task.exception()
        if exc is not None:
            _LOGGER.warning(
                "SignalR token refresh task failed for %s",
                self._vehicle_urn,
                exc_info=exc,
            )

    def _clear_standby_wake_refresh_task(self, task: asyncio.Task) -> None:
        """Drop the post-wake refresh task reference once it finishes."""
        if self._standby_wake_refresh_task is task:
            self._standby_wake_refresh_task = None

    def _schedule_standby_wake_refresh(self) -> None:
        """Refresh hub auth and subscriptions after the SCU wakes from standby."""
        if (
            self._standby_wake_refresh_task is not None
            and not self._standby_wake_refresh_task.done()
        ):
            _LOGGER.debug(
                "Post-standby SignalR refresh already scheduled for %s",
                self._vehicle_urn,
            )
            return
        task = asyncio.create_task(self._run_standby_wake_refresh())
        task.add_done_callback(self._clear_standby_wake_refresh_task)
        self._standby_wake_refresh_task = task

    async def _run_standby_wake_refresh(self) -> None:
        """Re-authenticate and resubscribe after the SCU reports a 12V wake."""
        try:
            _LOGGER.info(
                "SCU wake detected for %s — refreshing SignalR tokens and subscriptions",
                self._vehicle_urn,
            )
            await asyncio.sleep(STANDBY_WAKE_UPDATE_TOKENS_DELAY)
            if not self.connected:
                return
            if not await self._send_update_tokens(wait_response=True):
                raise HymerConnectApiError("Post-standby UpdateTokens failed")
            await asyncio.sleep(STANDBY_WAKE_RESUBSCRIBE_DELAY)
            if self.connected:
                await self._send_initial_subscriptions()
        except asyncio.CancelledError:
            raise
        except Exception:
            _LOGGER.warning(
                "Post-standby SignalR refresh failed for %s",
                self._vehicle_urn,
                exc_info=True,
            )
            self._connected = False
            self._notify_connection_lost()

    async def connect(self) -> None:
        """Establish the SignalR WebSocket connection."""
        # Step 1: Negotiate with scc-appcomm to get Azure SignalR URL + token
        try:
            negotiate1 = await self._api.signalr_negotiate()
        except HymerConnectApiError as err:
            _LOGGER.error("SignalR negotiate (step 1) failed: %s", err)
            raise

        azure_url = negotiate1.get("url")
        signalr_token = negotiate1.get("accessToken")

        _LOGGER.info(
            "SignalR negotiate (step 1): url=%s, hasToken=%s, keys=%s",
            bool(azure_url),
            bool(signalr_token),
            list(negotiate1.keys()) if isinstance(negotiate1, dict) else "not-dict",
        )

        if not azure_url or not signalr_token:
            raise HymerConnectApiError(
                "SignalR negotiate did not return url/accessToken"
            )

        self._signalr_token = signalr_token

        # Step 2: Negotiate with Azure SignalR to get connectionToken
        negotiate2_url = azure_url.replace("client/?", "client/negotiate?")
        headers = {
            "Authorization": f"Bearer {signalr_token}",
            "X-Requested-With": "XMLHttpRequest",
            "X-SignalR-User-Agent": (
                "Microsoft SignalR/6.0 "
                "(6.0.25; Unknown OS; Browser; Unknown Runtime Version)"
            ),
            "Content-Type": "text/plain;charset=UTF-8",
            "User-Agent": USER_AGENT,
        }

        try:
            async with self._session.post(
                negotiate2_url, headers=headers, data=""
            ) as resp:
                if resp.status >= 400:
                    text = await resp.text()
                    raise HymerConnectApiError(
                        f"SignalR negotiate (step 2) failed: {resp.status} {text[:200]}"
                    )
                negotiate2 = await resp.json()
        except aiohttp.ClientError as err:
            raise HymerConnectApiError(f"SignalR negotiate (step 2) error: {err}") from err
        except RuntimeError as err:
            if _is_closed_transport_error(err):
                raise HymerConnectApiError(
                    f"SignalR negotiate (step 2) error: {err}"
                ) from err
            raise

        connection_token = negotiate2.get("connectionToken")
        if not connection_token:
            raise HymerConnectApiError(
                "SignalR negotiate (step 2) did not return connectionToken"
            )

        # Step 3: Build WebSocket URL and connect
        ws_url = azure_url.replace("https://", "wss://")
        ws_url += f"&id={connection_token}&access_token={signalr_token}"

        try:
            self._ws = await self._session.ws_connect(
                ws_url,
                headers={
                    "User-Agent": USER_AGENT,
                    "Origin": azure_url.split("/client/")[0],
                },
            )
        except aiohttp.ClientError as err:
            raise HymerConnectApiError(f"WebSocket connect failed: {err}") from err
        except RuntimeError as err:
            if _is_closed_transport_error(err):
                raise HymerConnectApiError(f"WebSocket connect failed: {err}") from err
            raise

        # Step 4: Send protocol handshake
        handshake = json.dumps({"protocol": "json", "version": 1})
        await self._ws.send_str(handshake + SIGNALR_RECORD_SEPARATOR)

        # Wait for handshake response
        msg = await self._ws.receive(timeout=10)
        if msg.type != aiohttp.WSMsgType.TEXT:
            raise HymerConnectApiError(f"Unexpected handshake response: {msg.type}")

        _LOGGER.info("SignalR handshake accepted")
        self._connected = True
        self._connected_at = time.monotonic()
        self._last_data_received = time.monotonic()
        self._connection_lost_notified = False
        _LOGGER.info("SignalR connected to datahub for %s", self._vehicle_urn)

    async def _send_initial_subscriptions(self) -> None:
        """Send the captured app subscription burst on a fresh connection."""
        requests = build_subscription_requests()
        _LOGGER.info("Sending %d PiaRequest subscriptions", len(requests))
        for payload in requests:
            ok = await self.send_pia_request(payload)
            if not ok:
                request_id = extract_request_id_from_payload(payload)
                raise HymerConnectApiError(
                    f"Initial PiaRequest subscription failed for request_id={request_id}"
                )

    async def _send_request_payload(self, request_id: int, payload: str) -> None:
        """Send one encoded PiaRequest payload to the hub."""
        if not self._ws or self._ws.closed:
            raise HymerConnectApiError("SignalR WebSocket is not connected")
        msg = {
            "arguments": [payload],
            "target": "PiaRequest",
            "type": MSG_TYPE_INVOCATION,
        }
        await self._ws.send_str(json.dumps(msg) + SIGNALR_RECORD_SEPARATOR)
        pending = self._pending_requests.get(request_id)
        if pending is not None:
            pending.sent = True

    async def _retry_waiting_requests(self) -> None:
        """Replay any requests queued while token refresh was in progress."""
        while True:
            queued_ids = list(dict.fromkeys(self._waiting_request_ids))
            self._waiting_request_ids.clear()
            if not queued_ids:
                return
            for request_id in queued_ids:
                pending = self._pending_requests.get(request_id)
                if pending is None or pending.future.done():
                    continue
                try:
                    await self._send_request_payload(request_id, pending.payload)
                except Exception as err:
                    if not pending.future.done():
                        pending.future.set_exception(err)
                    self._pending_requests.pop(request_id, None)

    def _schedule_token_refresh_retry(
        self,
        status: int,
        request_id: int | None,
    ) -> None:
        """Refresh hub auth once and replay any queued requests."""
        if request_id is not None and request_id not in self._waiting_request_ids:
            self._waiting_request_ids.append(request_id)
        if self._token_refresh_task is not None and not self._token_refresh_task.done():
            _LOGGER.debug(
                "SignalR token refresh already running for %s",
                self._vehicle_urn,
            )
            return

        async def _refresh_and_retry() -> None:
            try:
                if status == STATUS_AUTH_TOKEN_EXPIRED:
                    await self._api._refresh_access_token()
                ok = await self._send_update_tokens(wait_response=True)
                if not ok:
                    raise HymerConnectApiError("UpdateTokens refresh failed")
                await self._retry_waiting_requests()
            except Exception as err:
                queued_ids = list(dict.fromkeys(self._waiting_request_ids))
                self._waiting_request_ids.clear()
                for queued_id in queued_ids:
                    pending = self._pending_requests.pop(queued_id, None)
                    if pending is None or pending.future.done():
                        continue
                    pending.future.set_exception(err)
                raise

        task = asyncio.create_task(_refresh_and_retry())
        task.add_done_callback(self._clear_token_refresh_task)
        self._token_refresh_task = task

    def _completion_success(self, msg: dict[str, Any]) -> bool:
        """Return True if a SignalR completion message represents success."""
        result_data = msg.get("result", {})
        error = msg.get("error")
        if error:
            _LOGGER.error("SignalR completion failed: %s", error)
            return False

        response = result_data.get("response", {}) if isinstance(result_data, dict) else {}
        status = response.get("status", "UNKNOWN")
        if status in {"OK", "SUCCESS", "ACCEPTED"}:
            _LOGGER.info("UpdateTokens SUCCESS for %s", self._vehicle_urn)
            return True

        _LOGGER.error("SignalR completion failed: status=%s", status)
        return False

    async def _send_update_tokens(self, wait_response: bool = True) -> bool:
        """Send UpdateTokens invocation to authenticate the SignalR connection.

        Uses the EHG refresh token (ett=access-refresh) to obtain a fresh
        short-lived access token (ett=access) via the remoteAccessToken API,
        then sends it in the UpdateTokens invocation.
        """
        if not self._ws:
            return False

        scu = self._scu_urn
        vehicle = self._vehicle_urn

        if not self._ehg_refresh_token:
            _LOGGER.warning(
                "No EHG refresh token configured — cannot authenticate SignalR. "
                "Provide the EHG Remote Access Refresh Token in the integration config."
            )
            return False

        if not vehicle:
            _LOGGER.warning("No vehicle URN — cannot request remote access token")
            return False

        # Exchange refresh token for a fresh short-lived access token
        try:
            ehg_access_token = await self._api.get_remote_access_token(
                vehicle, self._ehg_refresh_token
            )
            _LOGGER.info(
                "Obtained fresh EHG access token (len=%d) for %s",
                len(ehg_access_token),
                vehicle,
            )
        except HymerConnectApiError as err:
            _LOGGER.error("Failed to get remote access token: %s", err)
            return False

        access = self._api.access_token

        args = {
            "accessToken": access,
            "ehgAccessToken": ehg_access_token,
            "vehicleUrn": vehicle,
            "scuUrn": scu,
        }

        msg = {
            "arguments": [args],
            "invocationId": self._next_completion_invocation_id(),
            "target": "UpdateTokens",
            "type": MSG_TYPE_INVOCATION,
        }
        _LOGGER.info("Sending UpdateTokens for %s", vehicle)
        invocation_id = msg["invocationId"]
        future: asyncio.Future[bool] | None = None
        if wait_response:
            future = asyncio.get_running_loop().create_future()
            self._completion_futures[invocation_id] = future
        await self._ws.send_str(json.dumps(msg) + SIGNALR_RECORD_SEPARATOR)

        if not wait_response:
            _LOGGER.debug("UpdateTokens sent in fire-and-forget mode")
            return True

        try:
            return await asyncio.wait_for(future, timeout=PIA_REQUEST_TIMEOUT)
        except asyncio.TimeoutError:
            _LOGGER.warning("UpdateTokens timed out for %s", self._vehicle_urn)
            return False
        finally:
            self._completion_futures.pop(invocation_id, None)

    def _handle_message(self, msg: dict[str, Any]) -> None:
        """Handle an incoming SignalR message."""
        msg_type = msg.get("type")

        if msg_type == MSG_TYPE_PING:
            return
        if msg_type == MSG_TYPE_COMPLETION:
            invocation_id = str(msg.get("invocationId", ""))
            future = self._completion_futures.get(invocation_id)
            success = self._completion_success(msg)
            if future is not None and not future.done():
                future.set_result(success)
            return

        target = msg.get("target", "")
        args = msg.get("arguments", [])

        _LOGGER.debug(
            "SignalR message: type=%s target=%s args_count=%d raw=%s",
            msg_type,
            target,
            len(args),
            json.dumps(msg, default=str)[:300],
        )

        if target == "PiaResponse" and args:
            b64_payload = args[0] if isinstance(args[0], str) else ""
            if not b64_payload:
                return
            response = decode_transport_response(b64_payload)
            request_id = None if response is None else response.get("request_id")
            status = None if response is None else response.get("status")
            payload = None if response is None else response.get("payload")

            if status in {
                STATUS_AUTH_TOKEN_EXPIRED,
                STATUS_REMOTE_TOKEN_EXPIRED,
            }:
                _LOGGER.info(
                    "SignalR request %s reported token expiry status=%s for %s",
                    request_id,
                    status,
                    self._vehicle_urn,
                )
                self._schedule_token_refresh_retry(int(status), request_id)
                return

            slot_data: dict[tuple[int, int], Any] = {}
            if isinstance(payload, bytes):
                slot_data = decode_pia_slots_bytes(
                    payload,
                    known_slots=self._known_slots,
                )
            else:
                slot_data = decode_pia_slots(
                    b64_payload,
                    known_slots=self._known_slots,
                )

            if slot_data:
                was_standby = self._is_vehicle_standby()
                self._slot_data.update(slot_data)
                is_standby = self._is_vehicle_standby()
                self._last_data_received = time.monotonic()
                _LOGGER.debug(
                    "PiaResponse: %d slots updated, keys=%s",
                    len(slot_data),
                    list(slot_data.keys())[:20],
                )
                if was_standby and not is_standby:
                    self._schedule_standby_wake_refresh()
                if self._on_sensor_update:
                    self._on_sensor_update(slot_data)

            if request_id is None:
                return
            pending = self._pending_requests.get(int(request_id))
            if pending is None or pending.future.done():
                return
            if status in (None, STATUS_SUCCESS):
                pending.future.set_result(response or {"status": STATUS_SUCCESS})
            else:
                pending.future.set_exception(
                    HymerConnectApiError(
                        f"PiaRequest {request_id} failed with status={status}"
                    )
                )
            self._pending_requests.pop(int(request_id), None)

    async def listen(self) -> None:
        """Listen for incoming messages on the WebSocket."""
        if not self._ws:
            return

        self._running = True
        _LOGGER.info("SignalR listen loop started for %s", self._vehicle_urn)
        msg_count = 0
        try:
            async for msg in self._ws:
                if not self._running:
                    break
                if msg.type == aiohttp.WSMsgType.TEXT:
                    for part in msg.data.split(SIGNALR_RECORD_SEPARATOR):
                        part = part.strip()
                        if not part:
                            continue
                        try:
                            parsed = json.loads(part)
                        except json.JSONDecodeError:
                            continue
                        if parsed.get("type") == MSG_TYPE_PING:
                            # Respond to ping with ping
                            await self._ws.send_str(
                                json.dumps({"type": MSG_TYPE_PING})
                                + SIGNALR_RECORD_SEPARATOR
                            )
                        else:
                            msg_count += 1
                            try:
                                self._handle_message(parsed)
                            except Exception:
                                _LOGGER.warning(
                                    "Error handling SignalR message #%d",
                                    msg_count,
                                    exc_info=True,
                                )
                elif msg.type == aiohttp.WSMsgType.CLOSED:
                    _LOGGER.info(
                        "SignalR WebSocket closed after %d messages",
                        msg_count,
                    )
                    break
                elif msg.type == aiohttp.WSMsgType.ERROR:
                    _LOGGER.warning(
                        "SignalR WebSocket error after %d messages",
                        msg_count,
                    )
                    break
        except Exception:
            _LOGGER.warning(
                "SignalR listen loop exception after %d messages",
                msg_count,
                exc_info=True,
            )
        finally:
            _LOGGER.info(
                "SignalR listen loop ended after %d messages — requesting reconnect",
                msg_count,
            )
            self._connected = False
            self._running = False
            if self._token_refresh_task is not None:
                self._token_refresh_task.cancel()
                try:
                    await self._token_refresh_task
                except asyncio.CancelledError:
                    pass
                finally:
                    self._token_refresh_task = None
            if self._standby_wake_refresh_task is not None:
                self._standby_wake_refresh_task.cancel()
                try:
                    await self._standby_wake_refresh_task
                except asyncio.CancelledError:
                    pass
                finally:
                    self._standby_wake_refresh_task = None
            err = HymerConnectApiError("SignalR connection closed")
            for future in self._completion_futures.values():
                if not future.done():
                    future.set_exception(err)
            self._completion_futures.clear()
            for pending in self._pending_requests.values():
                if not pending.future.done():
                    pending.future.set_exception(err)
            self._pending_requests.clear()
            self._waiting_request_ids.clear()
            self._notify_connection_lost()

    async def send_pia_request(self, b64_payload: str) -> bool:
        """Send a PiaRequest message to the SCU."""
        if not self._ws or self._ws.closed:
            _LOGGER.warning("Cannot send PiaRequest — not connected")
            return False

        request_id = extract_request_id_from_payload(b64_payload)
        if request_id is None:
            _LOGGER.warning("Cannot send PiaRequest — no request id in payload")
            return False
        loop = asyncio.get_running_loop()
        pending = self._pending_requests.get(request_id)
        if pending is None or pending.future.done():
            pending = _PendingPiaRequest(
                payload=b64_payload,
                future=loop.create_future(),
                sent=False,
            )
            self._pending_requests[request_id] = pending

        if self._token_refresh_task is not None and not self._token_refresh_task.done():
            if request_id not in self._waiting_request_ids:
                self._waiting_request_ids.append(request_id)
            _LOGGER.debug(
                "Token refresh in progress — queueing request %s for %s",
                request_id,
                self._vehicle_urn,
            )
        elif not pending.sent:
            try:
                await self._send_request_payload(request_id, b64_payload)
            except Exception:
                _LOGGER.error(
                    "Failed to send PiaRequest — marking SignalR as disconnected",
                    exc_info=True,
                )
                self._connected = False
                self._notify_connection_lost()
                self._pending_requests.pop(request_id, None)
                return False

        try:
            await asyncio.wait_for(pending.future, timeout=PIA_REQUEST_TIMEOUT)
        except asyncio.TimeoutError:
            _LOGGER.warning("PiaRequest %s timed out", request_id)
            self._pending_requests.pop(request_id, None)
            return False
        except Exception:
            self._pending_requests.pop(request_id, None)
            raise
        return True

    async def send_light_command(
        self,
        bus_id: int,
        sensor_id: int,
        *,
        bool_value: bool | None = None,
        uint_value: int | None = None,
        str_value: str | None = None,
    ) -> bool:
        """Send a light/switch control command to the SCU.

        Args:
            bus_id: Bus ID (e.g. 11 for living ceiling, 3 for main switch).
            sensor_id: 1=on/off, 2=brightness, 3=color_temp.
            bool_value: True/False for on/off.
            uint_value: 0-100 for brightness/color_temp.
            str_value: String value (e.g. "On"/"Off" for main switch).
        """
        payload = build_light_command(
            bus_id, sensor_id,
            bool_value=bool_value, uint_value=uint_value, str_value=str_value,
        )
        _LOGGER.info(
            "Sending light command: bus=%d sid=%d bool=%s uint=%s str=%s",
            bus_id, sensor_id, bool_value, uint_value, str_value,
        )
        if await self.send_pia_request(payload):
            if str_value is not None:
                self._apply_optimistic_main_switch_state(
                    bus_id, sensor_id, str_value
                )
            elif bool_value is not None:
                self._apply_optimistic_main_switch_state(
                    bus_id, sensor_id, bool_value
                )
            return True
        return False

    async def send_multi_sensor_command(
        self,
        sensors: list[dict],
    ) -> bool:
        """Send a multi-sensor command to the SCU.

        Args:
            sensors: List of sensor dicts with bus_id, sensor_id, and value.
        """
        payload = build_multi_sensor_command(sensors)
        _LOGGER.info(
            "Sending multi-sensor command: %s",
            [(s.get("bus_id"), s.get("sensor_id")) for s in sensors],
        )
        if await self.send_pia_request(payload):
            for sensor in sensors:
                value = None
                if "str_value" in sensor:
                    value = sensor["str_value"]
                elif "bool_value" in sensor:
                    value = sensor["bool_value"]
                if value is not None:
                    self._apply_optimistic_main_switch_state(
                        sensor["bus_id"],
                        sensor["sensor_id"],
                        value,
                    )
            return True
        return False

    async def send_slot_actions(
        self,
        actions: list[dict[str, Any]],
    ) -> bool:
        """Send a catalog-driven list of generic slot writes."""
        if not actions:
            _LOGGER.debug("Ignoring empty slot-action request")
            return True

        sensors: list[dict[str, Any]] = []
        for action in actions:
            sensors.append(serialize_slot_action(action))

        _LOGGER.info(
            "Sending %d slot actions: %s",
            len(sensors),
            [(s.get("bus_id"), s.get("sensor_id")) for s in sensors],
        )
        return await self.send_multi_sensor_command(sensors)

    async def send_restart_system_command(
        self,
        *,
        cold: bool = True,
    ) -> bool:
        """Send the app-style Request.command.restart Smart Unit restart."""
        payload = build_restart_system_request(cold=cold)
        _LOGGER.info(
            "Sending Smart Unit restart command for %s (cold=%s)",
            self._vehicle_urn,
            cold,
        )
        return await self.send_pia_request(payload)

    async def start(self) -> None:
        """Connect and start listening in the background."""
        await self.connect()
        self._running = True
        self._task = asyncio.create_task(self.listen())
        try:
            if not await self._send_update_tokens(wait_response=True):
                raise HymerConnectApiError("UpdateTokens failed")
            await self._send_initial_subscriptions()
        except Exception:
            await self.stop()
            raise

    async def stop(self) -> None:
        """Stop listening and close the WebSocket."""
        self._running = False
        if self._token_refresh_task and not self._token_refresh_task.done():
            self._token_refresh_task.cancel()
            try:
                await self._token_refresh_task
            except asyncio.CancelledError:
                pass
        self._token_refresh_task = None
        if self._standby_wake_refresh_task and not self._standby_wake_refresh_task.done():
            self._standby_wake_refresh_task.cancel()
            try:
                await self._standby_wake_refresh_task
            except asyncio.CancelledError:
                pass
        self._standby_wake_refresh_task = None
        if self._ws and not self._ws.closed:
            await self._ws.close()
        if self._task and not self._task.done():
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        self._connected = False
        self._task = None
        self._ws = None
        _LOGGER.debug("SignalR client stopped")
