"""Data update coordinator for HYMER Connect.

Uses the SCC REST API for vehicle metadata and SignalR for real-time sensor data.
The coordinator polls REST periodically and merges SignalR push data on arrival.
"""

from __future__ import annotations

import asyncio
from datetime import timedelta
import logging
import time
from typing import Any, Awaitable, Callable

import aiohttp

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed, HomeAssistantError
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .api import HymerConnectApi, HymerConnectApiError, HymerConnectAuthError
from .capability_resolver import main_switch_slots
from homeassistant.const import CONF_USERNAME

from .const import (
    CONF_VEHICLE_ID,
    CONF_VEHICLE_MODEL,
    CONF_VEHICLE_MODEL_GROUP,
    CONF_VEHICLE_MODEL_YEAR,
    CONF_VEHICLE_NAME,
    CONF_SCU_URN,
    CONF_VEHICLE_URN,
    CONF_VIN,
    DEFAULT_SCAN_INTERVAL,
    DOMAIN,
)
from .signalr_client import HymerSignalRClient

_LOGGER = logging.getLogger(__name__)

# Reconnection backoff constants mirror the app's DataHub reconnect policy:
# 1s, 2s, 4s, 8s, 16s, then stop fast retries until the next coordinator cycle.
_INITIAL_BACKOFF = 1
_MAX_BACKOFF = 16
_MAX_CONSECUTIVE_FAILURES = 5
_REST_METADATA_INTERVAL = 600  # 10 minutes between full REST metadata refreshes
_CAPABILITY_RELOAD_DEBOUNCE_S = 5
_ACTIVE_SLOT_WINDOW_S = 30 * 60  # 30 min — recent enough to count as currently active


class _SignalRCommandProxy:
    """Coordinator-backed sender that reconnects and retries once on failure."""

    def __init__(self, coordinator: HymerConnectCoordinator) -> None:
        self._coordinator = coordinator

    async def send_light_command(self, bus_id: int, sensor_id: int, **kwargs: Any) -> bool:
        await self._coordinator.async_send_light_command(bus_id, sensor_id, **kwargs)
        return True

    async def send_multi_sensor_command(self, sensors: list[dict[str, Any]]) -> bool:
        await self._coordinator.async_send_multi_sensor_command(sensors)
        return True

    async def send_slot_actions(self, actions: list[dict[str, Any]]) -> bool:
        await self._coordinator.async_send_slot_actions(actions)
        return True

    async def send_pia_request(self, payload: str) -> bool:
        await self._coordinator.async_send_pia_request(payload)
        return True

    async def send_restart_system_command(self, *, cold: bool = True) -> bool:
        await self._coordinator.async_send_restart_system_command(cold=cold)
        return True


class HymerConnectCoordinator(DataUpdateCoordinator[dict[str, Any]]):
    """Coordinator to manage fetching HYMER Connect data."""

    config_entry: ConfigEntry

    def __init__(
        self,
        hass: HomeAssistant,
        api: HymerConnectApi,
        session: aiohttp.ClientSession,
        entry: ConfigEntry,
        vehicle_urn: str = "",
        scu_urn: str = "",
        vehicle_id: int | None = None,
        vin: str = "",
        ehg_refresh_token: str = "",
    ) -> None:
        """Initialize the coordinator."""
        self.api = api
        self._session = session
        self._vehicle_urn = vehicle_urn  # urn:ehg:vehicle:hy-XXXXXXXXXX
        self._scu_urn = scu_urn  # urn:ehg:scu:sXXX.XX.XX.XXX.XXX
        self._vehicle_id = vehicle_id
        self._vin = vin
        self._ehg_refresh_token = ehg_refresh_token  # BLE-derived refresh token
        self._signalr: HymerSignalRClient | None = None
        # Slot-keyed (bus_id, sensor_id) → raw value.  Populated by the
        # discovery-aware SignalR callback.  Observed slot set is derived
        # from this and is how platform setup knows what to instantiate.
        self._slot_data: dict[tuple[int, int], Any] = {}
        self._slot_last_seen: dict[tuple[int, int], float] = {}
        self._reconnect_backoff: int = _INITIAL_BACKOFF
        self._last_reconnect_attempt: float = 0.0
        self._consecutive_failures: int = 0
        self._last_rest_metadata_refresh: float = 0.0
        self._cached_rest_data: dict[str, Any] = {}
        self._entry_setup_complete = False
        self._capability_reload_task: asyncio.Task | None = None
        self._capability_reload_slots: set[tuple[int, int]] = set()
        self._reconnect_task: asyncio.Task | None = None
        self._suppress_connection_lost_refresh = False
        self._shutting_down = False
        self._background_tasks: set[asyncio.Task] = set()
        self._setup_slot_baseline: set[tuple[int, int]] | None = None
        self._pending_setup_slots: set[tuple[int, int]] = set()
        self._platform_discovery_profile: dict[str, Any] = {}
        self._platform_refresh_callbacks: dict[
            str,
            Callable[[set[tuple[int, int]]], Awaitable[None]],
        ] = {}
        self._signalr_commands = _SignalRCommandProxy(self)
        super().__init__(
            hass,
            _LOGGER,
            name=DOMAIN,
            update_interval=timedelta(seconds=DEFAULT_SCAN_INTERVAL),
            config_entry=entry,
        )

    @property
    def signalr_client(self) -> HymerSignalRClient | None:
        """Return the active SignalR client for sending commands."""
        return self._signalr

    def _ensure_signalr_start_lock(self) -> asyncio.Lock:
        """Return the coordinator-wide start/stop lock for SignalR lifecycle."""
        lock = getattr(self, "_signalr_start_lock", None)
        if lock is None:
            lock = asyncio.Lock()
            self._signalr_start_lock = lock
        return lock

    def _on_signalr_update(
        self,
        slot_data: dict[tuple[int, int], Any],
    ) -> None:
        """Handle incoming SignalR slot data."""
        previous_slots = set(self._slot_data)
        self._slot_data.update(slot_data)
        now = time.monotonic()
        for slot in slot_data:
            self._slot_last_seen[slot] = now
        new_slots = set(self._slot_data) - previous_slots
        _LOGGER.debug(
            "SignalR push: %d slots",
            len(self._slot_data),
        )
        # Trigger HA entity updates immediately
        self.async_set_updated_data({
            **(self.data or {}),
            "signalr_slots": self._slot_data,
        })
        if not new_slots:
            return
        if self._entry_setup_complete:
            self._schedule_capability_reload(new_slots)
            return
        if self._setup_slot_baseline is not None:
            self._pending_setup_slots.update(
                slot for slot in new_slots
                if slot not in self._setup_slot_baseline
            )

    @property
    def slot_data(self) -> dict[tuple[int, int], Any]:
        """Return the current (bus_id, sensor_id) → raw value map."""
        return self._slot_data

    @property
    def observed_slots(self) -> set[tuple[int, int]]:
        """Return the set of (bus_id, sensor_id) pairs the SCU has emitted."""
        return set(self._slot_data.keys())

    @property
    def active_slots(self) -> set[tuple[int, int]]:
        """Return slots refreshed recently enough to count as active."""
        cutoff = time.monotonic() - _ACTIVE_SLOT_WINDOW_S
        return {
            slot
            for slot, last_seen in self._slot_last_seen.items()
            if last_seen >= cutoff
        }

    @property
    def stale_slots(self) -> set[tuple[int, int]]:
        """Return historical slots not refreshed within the active window."""
        return self.observed_slots - self.active_slots

    @property
    def slot_last_seen(self) -> dict[tuple[int, int], float]:
        """Return a snapshot of slot activity timestamps."""
        return dict(self._slot_last_seen)

    @property
    def active_slot_window_seconds(self) -> int:
        """Return the staleness window for active slot tracking."""
        return _ACTIVE_SLOT_WINDOW_S

    @staticmethod
    def _main_switch_value_is_on(value: Any) -> bool | None:
        """Normalize a main-switch slot value to on/off where possible."""
        if value is None:
            return None
        if isinstance(value, bool):
            return value
        if isinstance(value, (int, float)):
            return bool(value)
        if isinstance(value, str):
            upper = value.upper()
            if upper in {"ON", "TRUE", "YES"}:
                return True
            if upper in {"OFF", "FALSE", "NO"}:
                return False
        return None

    @property
    def habitation_power_state(self) -> bool | None:
        """Return the resolved 12V habitation power state."""
        slots = (self.data or {}).get("signalr_slots") or self._slot_data
        states: list[bool] = []
        for slot in main_switch_slots():
            if slot not in slots:
                continue
            normalized = self._main_switch_value_is_on(slots.get(slot))
            if normalized is None:
                continue
            states.append(normalized)
        if any(states):
            return True
        if states:
            return False
        return None

    def is_habitation_power_available(self) -> bool:
        """Return True unless 12V habitation power is explicitly off."""
        return self.habitation_power_state is not False

    def mark_slots_recent(
        self,
        slots: set[tuple[int, int]] | None = None,
    ) -> None:
        """Refresh slot activity timestamps without changing their values."""
        if slots is None:
            slots = set(self._slot_data)
        if not slots:
            return
        now = time.monotonic()
        for slot in slots:
            if slot in self._slot_data:
                self._slot_last_seen[slot] = now

    @property
    def platform_discovery_profile(self) -> dict[str, Any]:
        """Return the latest per-platform discovery profile."""
        return {
            platform: dict(profile)
            for platform, profile in self._platform_discovery_profile.items()
        }

    @property
    def scu_urn(self) -> str:
        """Return the currently known Smart Control Unit identifier."""
        return self._scu_urn

    def set_platform_discovery_profile(
        self,
        platform: str,
        profile: dict[str, Any],
    ) -> None:
        """Store one platform's latest discovery profile."""
        updated = dict(self._platform_discovery_profile)
        updated[platform] = dict(profile)
        self._platform_discovery_profile = updated

    def register_platform_refresh(
        self,
        platform: str,
        callback: Callable[[set[tuple[int, int]]], Awaitable[None]],
    ) -> None:
        """Register one platform's dynamic entity refresh callback."""
        updated = dict(self._platform_refresh_callbacks)
        updated[platform] = callback
        self._platform_refresh_callbacks = updated

    def unregister_platform_refresh(self, platform: str) -> None:
        """Remove one platform's dynamic refresh callback."""
        if platform not in self._platform_refresh_callbacks:
            return
        updated = dict(self._platform_refresh_callbacks)
        updated.pop(platform, None)
        self._platform_refresh_callbacks = updated

    def clear_platform_refresh_callbacks(self) -> None:
        """Remove all registered platform refresh callbacks."""
        self._platform_refresh_callbacks = {}

    def track_background_task(self, task: asyncio.Task) -> asyncio.Task:
        """Track one coordinator-owned task so unload can cancel it cleanly."""
        self._background_tasks.add(task)

        def _remove(done_task: asyncio.Task) -> None:
            self._background_tasks.discard(done_task)
            if done_task.cancelled():
                return
            exc = done_task.exception()
            if exc is not None:
                _LOGGER.warning(
                    "Background coordinator task failed for %s",
                    self.config_entry.title,
                    exc_info=exc,
                )

        task.add_done_callback(_remove)
        return task

    async def async_cancel_background_tasks(self) -> None:
        """Cancel and await coordinator-owned background tasks."""
        tasks = list(getattr(self, "_background_tasks", set()))
        self._background_tasks.clear()
        if not tasks:
            return
        for task in tasks:
            if not task.done():
                task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)

    def _clear_reconnect_task(self, task: asyncio.Task) -> None:
        """Forget the reconnect loop once it completes."""
        if self._reconnect_task is task:
            self._reconnect_task = None

    def _cancel_reconnect_task(self) -> None:
        """Cancel any in-flight reconnect loop."""
        task = self._reconnect_task
        if task is None:
            return
        current = asyncio.current_task()
        if task is current:
            return
        if not task.done():
            task.cancel()
        self._reconnect_task = None

    def mark_shutting_down(self) -> None:
        """Prevent any further SignalR reconnect work for this coordinator."""
        self._shutting_down = True
        self._suppress_connection_lost_refresh = True
        self._cancel_reconnect_task()

    async def async_prepare_for_shutdown(self) -> None:
        """Quiesce coordinator-owned background work during unload/shutdown."""
        self.mark_shutting_down()
        await self.async_cancel_background_tasks()
        await self.stop_signalr()

    async def wait_for_first_frame(self, timeout: float = 30.0) -> bool:
        """Wait for at least one slot to appear (capability discovery).

        Returns True if data arrived before timeout, False otherwise.  Callers
        should handle the False case by still creating entities for whatever
        slots do exist — there may be zero initially on a cold start.
        """
        start = time.monotonic()
        while not self._slot_data:
            if time.monotonic() - start > timeout:
                return False
            await asyncio.sleep(0.5)
        if self._setup_slot_baseline is None:
            self._setup_slot_baseline = set(self._slot_data)
        return True

    def mark_entry_setup_complete(self) -> None:
        """Allow late-discovered slots to trigger dynamic platform refresh."""
        self._entry_setup_complete = True
        if self._pending_setup_slots:
            pending = set(self._pending_setup_slots)
            self._pending_setup_slots.clear()
            self._schedule_capability_reload(pending)

    async def async_ensure_signalr_healthy(self) -> HymerSignalRClient:
        """Return a healthy SignalR client, reconnecting if needed."""
        client = self._signalr
        if client and client.connected:
            return client
        _LOGGER.info(
            "SignalR not ready for %s — attempting reconnect before command",
            self.config_entry.title,
        )
        await self.start_signalr()
        client = self._signalr
        if client and client.connected:
            return client
        raise HomeAssistantError(
            "SignalR is not connected. Try reloading the integration."
        )

    async def async_ensure_signalr_connected(self) -> _SignalRCommandProxy:
        """Return a coordinator-backed sender with reconnect + retry semantics."""
        await self.async_ensure_signalr_healthy()
        return self._signalr_commands

    async def _send_with_retry(
        self,
        method_name: str,
        *args: Any,
        **kwargs: Any,
    ) -> None:
        """Send one command, reconnecting and retrying once on failure."""
        for attempt in range(2):
            client = await self.async_ensure_signalr_healthy()
            method = getattr(client, method_name)
            try:
                ok = await method(*args, **kwargs)
            except Exception:
                ok = False
                _LOGGER.warning(
                    "%s raised for %s on attempt %d",
                    method_name,
                    self.config_entry.title,
                    attempt + 1,
                    exc_info=True,
                )
            if ok:
                return
            if attempt == 0:
                _LOGGER.warning(
                    "%s send failed for %s — reconnecting for one retry",
                    method_name,
                    self.config_entry.title,
                )
                client.mark_disconnected()
        raise HomeAssistantError(
            "Command failed after reconnect and retry. Try reloading the integration."
        )

    async def async_send_light_command(
        self,
        bus_id: int,
        sensor_id: int,
        **kwargs: Any,
    ) -> None:
        """Send a light/switch command with reconnect + retry."""
        await self._send_with_retry(
            "send_light_command",
            bus_id,
            sensor_id,
            **kwargs,
        )

    async def async_send_multi_sensor_command(
        self,
        sensors: list[dict[str, Any]],
    ) -> None:
        """Send a multi-sensor command with reconnect + retry."""
        await self._send_with_retry("send_multi_sensor_command", sensors)

    async def async_send_slot_actions(
        self,
        actions: list[dict[str, Any]],
    ) -> None:
        """Send scenario/catalog slot actions with reconnect + retry."""
        await self._send_with_retry("send_slot_actions", actions)

    async def async_send_pia_request(self, payload: str) -> None:
        """Send a raw PIA request with reconnect + retry."""
        await self._send_with_retry("send_pia_request", payload)

    async def async_send_restart_system_command(
        self,
        *,
        cold: bool = True,
    ) -> None:
        """Send the app-style Smart Unit restart command with reconnect + retry."""
        await self._send_with_retry("send_restart_system_command", cold=cold)

    async def _refresh_registered_platforms(
        self,
        new_slots: set[tuple[int, int]],
    ) -> None:
        """Ask registered platforms to discover and add any new entities."""
        callbacks = list(self._platform_refresh_callbacks.items())
        if not callbacks:
            _LOGGER.warning(
                "Discovered %d new slots for %s but no platform refresh callbacks are registered yet",
                len(new_slots),
                self.config_entry.title,
            )
            return

        refreshed = 0
        for platform, callback in callbacks:
            try:
                await callback(set(new_slots))
                refreshed += 1
            except Exception:  # pragma: no cover - defensive logging
                _LOGGER.exception(
                    "Platform %s refresh failed after discovering new slots for %s",
                    platform,
                    self.config_entry.title,
                )
        _LOGGER.info(
            "Refreshed %d platforms for %s after discovering %d new slots",
            refreshed,
            self.config_entry.title,
            len(new_slots),
        )

    def _schedule_capability_reload(self, new_slots: set[tuple[int, int]]) -> None:
        """Refresh platforms after a sliding debounce when new slots appear."""
        self._capability_reload_slots.update(new_slots)

        if self._capability_reload_task is not None:
            self._capability_reload_task.cancel()

        async def _reload_later() -> None:
            try:
                await asyncio.sleep(_CAPABILITY_RELOAD_DEBOUNCE_S)
                slots = set(self._capability_reload_slots)
                self._capability_reload_slots.clear()
                _LOGGER.info(
                    "Refreshing %s after discovering %d new slots: %s",
                    self.config_entry.title,
                    len(slots),
                    sorted(slots),
                )
                await self._refresh_registered_platforms(slots)
            except asyncio.CancelledError:
                raise
            finally:
                current = asyncio.current_task()
                if self._capability_reload_task is current:
                    self._capability_reload_task = None

        self._capability_reload_task = self.hass.async_create_task(_reload_later())

    def _on_signalr_connection_lost(self) -> None:
        """Handle unexpected SignalR loss by starting the app-like retry loop."""
        if getattr(self, "_shutting_down", False):
            _LOGGER.debug(
                "Ignoring SignalR connection-lost callback for %s during shutdown",
                self.config_entry.title,
            )
            return
        if self._suppress_connection_lost_refresh:
            _LOGGER.debug(
                "Ignoring SignalR connection-lost callback for %s during intentional stop",
                self.config_entry.title,
            )
            return
        if self._reconnect_task is not None and not self._reconnect_task.done():
            _LOGGER.debug(
                "SignalR reconnect loop already running for %s",
                self.config_entry.title,
            )
            return
        _LOGGER.info("SignalR connection lost — starting reconnect loop")

        async def _reconnect_loop() -> None:
            delay = _INITIAL_BACKOFF
            attempts = 0
            while attempts < _MAX_CONSECUTIVE_FAILURES and not self._suppress_connection_lost_refresh:
                attempts += 1
                await asyncio.sleep(delay)
                if self._suppress_connection_lost_refresh:
                    return
                try:
                    await self.start_signalr()
                except Exception:
                    _LOGGER.warning(
                        "SignalR reconnect attempt %d/%d failed for %s",
                        attempts,
                        _MAX_CONSECUTIVE_FAILURES,
                        self.config_entry.title,
                        exc_info=True,
                    )
                if self._signalr and self._signalr.connected:
                    _LOGGER.info(
                        "SignalR reconnect loop restored connectivity for %s",
                        self.config_entry.title,
                    )
                    return
                delay = min(delay * 2, _MAX_BACKOFF)
            _LOGGER.warning(
                "SignalR reconnect loop exhausted for %s",
                self.config_entry.title,
            )

        task = self.hass.async_create_task(_reconnect_loop())
        task.add_done_callback(self._clear_reconnect_task)
        self._reconnect_task = task

    async def start_signalr(self) -> None:
        """Start the SignalR WebSocket connection."""
        lock = self._ensure_signalr_start_lock()
        async with lock:
            if getattr(self, "_shutting_down", False):
                _LOGGER.debug(
                    "Skipping SignalR start for %s during shutdown",
                    self.config_entry.title,
                )
                return
            if not self._scu_urn:
                _LOGGER.warning("No SCU URN — skipping SignalR")
                return

            self._suppress_connection_lost_refresh = False

            if self._signalr and self._signalr.connected:
                _LOGGER.debug("SignalR already connected")
                return

            # Stop any existing dead/stale connection first.
            if self._signalr:
                _LOGGER.info("Stopping stale SignalR client before reconnect")
                await self._stop_signalr_locked()

            client = HymerSignalRClient(
                api=self.api,
                session=self._session,
                vehicle_urn=self._vehicle_urn,
                scu_urn=self._scu_urn,
                ehg_refresh_token=self._ehg_refresh_token,
                on_sensor_update=self._on_signalr_update,
                on_connection_lost=self._on_signalr_connection_lost,
            )
            self._signalr = client

            try:
                await client.start()
                _LOGGER.info("SignalR connected for %s", self._vehicle_urn)
                # Reset backoff/failure state on successful connection.
                self._reconnect_backoff = _INITIAL_BACKOFF
                self._consecutive_failures = 0
            except HymerConnectApiError as err:
                self._consecutive_failures += 1
                _LOGGER.warning(
                    "SignalR connection failed (%d/%d): %s",
                    self._consecutive_failures,
                    _MAX_CONSECUTIVE_FAILURES,
                    err,
                )
                if self._signalr is client:
                    self._signalr = None
                self._reconnect_backoff = min(
                    self._reconnect_backoff * 2, _MAX_BACKOFF
                )
                _LOGGER.info(
                    "Next SignalR reconnect attempt in %ds", self._reconnect_backoff
                )
            except Exception:
                if self._signalr is client:
                    self._signalr = None
                raise

    async def _stop_signalr_locked(self) -> None:
        """Stop the SignalR WebSocket connection while holding the lifecycle lock."""
        if self._capability_reload_task is not None:
            self._capability_reload_task.cancel()
            self._capability_reload_task = None
        self._capability_reload_slots.clear()
        self._cancel_reconnect_task()
        self._suppress_connection_lost_refresh = True
        if self._signalr:
            try:
                await self._signalr.stop()
            finally:
                self._signalr = None

    async def stop_signalr(self) -> None:
        """Stop the SignalR WebSocket connection."""
        lock = self._ensure_signalr_start_lock()
        async with lock:
            await self._stop_signalr_locked()

    async def _async_update_data(self) -> dict[str, Any]:
        """Fetch data from the REST API and merge with SignalR data."""
        now = time.monotonic()

        # Only refresh full REST metadata periodically (URNs, VIN, model are static)
        needs_metadata_refresh = (
            not self._cached_rest_data
            or (now - self._last_rest_metadata_refresh) > _REST_METADATA_INTERVAL
        )

        if needs_metadata_refresh:
            try:
                rest_data = await self.api.get_vehicle_status(
                    vehicle_id=self._vehicle_id,
                    vehicle_urn=self._vehicle_urn,
                    vin=self._vin,
                    scu_urn=self._scu_urn,
                )
                self._cached_rest_data = rest_data
                self._last_rest_metadata_refresh = now
            except HymerConnectAuthError as err:
                raise ConfigEntryAuthFailed(
                    f"Authentication error: {err}"
                ) from err
            except HymerConnectApiError as err:
                raise UpdateFailed(
                    f"Error communicating with API: {err}"
                ) from err
        else:
            rest_data = dict(self._cached_rest_data)

        # Store URNs from REST data if not set yet
        if not self._scu_urn and rest_data.get("vehicle"):
            vehicle = rest_data["vehicle"]
            self._scu_urn = vehicle.get("smartUnitUrn", "")
            vin = vehicle.get("vin", "")
            _LOGGER.info("Discovered VIN=%s SCU=%s", vin, self._scu_urn)

        if not self._vehicle_id and rest_data.get("vehicle_id") is not None:
            self._vehicle_id = rest_data["vehicle_id"]
        if not self._vin and rest_data.get("vin"):
            self._vin = rest_data["vin"]

        # Get vehicle URN (urn:ehg:vehicle:hy-...) from EHG API
        if not self._vehicle_urn and rest_data.get("vehicle_urn"):
            self._vehicle_urn = rest_data["vehicle_urn"]
            _LOGGER.info(
                "Discovered vehicle_urn=%s, scu_urn=%s",
                self._vehicle_urn,
                self._scu_urn,
            )

        data_updates = dict(self.config_entry.data)
        metadata_changed = False

        def _apply_data_update(key: str, value: Any) -> None:
            nonlocal metadata_changed
            if value in (None, ""):
                return
            if data_updates.get(key) != value:
                data_updates[key] = value
                metadata_changed = True

        _apply_data_update(CONF_VEHICLE_URN, self._vehicle_urn)
        _apply_data_update(CONF_SCU_URN, self._scu_urn)
        _apply_data_update(CONF_VEHICLE_ID, self._vehicle_id)
        _apply_data_update(CONF_VIN, self._vin)
        _apply_data_update(CONF_VEHICLE_NAME, rest_data.get("name"))
        _apply_data_update(CONF_VEHICLE_MODEL, rest_data.get("model"))
        _apply_data_update(CONF_VEHICLE_MODEL_GROUP, rest_data.get("model_group"))
        _apply_data_update(CONF_VEHICLE_MODEL_YEAR, rest_data.get("model_year"))

        entry_manager = getattr(self.hass, "config_entries", None)
        if metadata_changed:
            if (
                entry_manager is not None
                and hasattr(entry_manager, "async_entries")
                and hasattr(entry_manager, "async_update_entry")
            ):
                legacy_unique_id = self.config_entry.data.get(CONF_USERNAME, "").lower()
                new_unique_id = self._vehicle_urn or self._vin or self.config_entry.unique_id
                unique_id_update = self.config_entry.unique_id
                if self.config_entry.unique_id in (None, legacy_unique_id):
                    duplicate_entry_exists = any(
                        entry.entry_id != self.config_entry.entry_id
                        and entry.unique_id == new_unique_id
                        for entry in entry_manager.async_entries(DOMAIN)
                    )
                    if not duplicate_entry_exists:
                        unique_id_update = new_unique_id
                entry_manager.async_update_entry(
                    self.config_entry,
                    data=data_updates,
                    unique_id=unique_id_update,
                )

        if not self._scu_urn and self._vehicle_urn:
            self._scu_urn = self._vehicle_urn

        # --- SignalR connection management ---
        signalr_connected = self._signalr is not None and self._signalr.connected

        if not signalr_connected:
            if self._reconnect_task is not None and not self._reconnect_task.done():
                _LOGGER.debug(
                    "SignalR reconnect loop already active for %s",
                    self.config_entry.title,
                )
                rest_data["signalr_slots"] = self._slot_data
                return rest_data
            # Apply exponential backoff between reconnection attempts
            since_last_attempt = now - self._last_reconnect_attempt
            if since_last_attempt >= self._reconnect_backoff:
                _LOGGER.info(
                    "SignalR not connected for %s (obj=%s), attempting start",
                    self.config_entry.title,
                    self._signalr is not None,
                )
                self._last_reconnect_attempt = now
                try:
                    await self.start_signalr()
                except Exception:
                    _LOGGER.warning("SignalR connect attempt failed", exc_info=True)
            else:
                remaining = self._reconnect_backoff - since_last_attempt
                _LOGGER.warning(
                    "SignalR reconnect backoff: %.0fs remaining (attempt %d/%d)",
                    remaining,
                    self._consecutive_failures,
                    _MAX_CONSECUTIVE_FAILURES,
                )

        # Merge REST + SignalR data
        signalr_ok = self._signalr.connected if self._signalr else False
        _LOGGER.debug(
            "Data update: signalr_slots=%d, signalr_connected=%s",
            len(self._slot_data),
            signalr_ok,
        )
        rest_data["signalr_slots"] = self._slot_data
        return rest_data
