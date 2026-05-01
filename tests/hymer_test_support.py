"""Test helpers for loading integration modules without Home Assistant."""

from __future__ import annotations

import sys
import types
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CUSTOM_COMPONENTS_DIR = ROOT / "custom_components"
INTEGRATION_DIR = CUSTOM_COMPONENTS_DIR / "hymer_connect_metadata"


def ensure_package_paths() -> None:
    """Register package shells without importing the integration __init__."""
    if "custom_components" not in sys.modules:
        custom_components = types.ModuleType("custom_components")
        custom_components.__path__ = [str(CUSTOM_COMPONENTS_DIR)]
        sys.modules["custom_components"] = custom_components
    if "custom_components.hymer_connect_metadata" not in sys.modules:
        integration = types.ModuleType("custom_components.hymer_connect_metadata")
        integration.__path__ = [str(INTEGRATION_DIR)]
        sys.modules["custom_components.hymer_connect_metadata"] = integration


def install_homeassistant_stubs() -> None:
    """Install minimal Home Assistant stubs required by unit tests."""
    ensure_package_paths()

    def module(name: str) -> types.ModuleType:
        existing = sys.modules.get(name)
        if existing is not None:
            return existing
        created = types.ModuleType(name)
        sys.modules[name] = created
        return created

    homeassistant = module("homeassistant")
    homeassistant.__path__ = []  # type: ignore[attr-defined]
    components = module("homeassistant.components")
    components.__path__ = []  # type: ignore[attr-defined]
    helpers = module("homeassistant.helpers")
    helpers.__path__ = []  # type: ignore[attr-defined]

    binary_sensor = module("homeassistant.components.binary_sensor")
    binary_sensor.BinarySensorDeviceClass = types.SimpleNamespace(
        DOOR="door",
        LOCK="lock",
        CONNECTIVITY="connectivity",
        PROBLEM="problem",
        BATTERY_CHARGING="battery_charging",
        PLUG="plug",
        MOTION="motion",
        RUNNING="running",
    )
    binary_sensor.BinarySensorEntity = type("BinarySensorEntity", (), {})

    button = module("homeassistant.components.button")
    button.ButtonEntity = type("ButtonEntity", (), {})

    fan = module("homeassistant.components.fan")
    fan.FanEntity = type("FanEntity", (), {})
    fan.FanEntityFeature = types.SimpleNamespace(
        TURN_ON=1,
        TURN_OFF=2,
        SET_SPEED=4,
        SET_PERCENTAGE=4,
    )

    light = module("homeassistant.components.light")
    light.ATTR_BRIGHTNESS = "brightness"
    light.ATTR_COLOR_TEMP_KELVIN = "color_temp_kelvin"
    light.ColorMode = types.SimpleNamespace(
        ONOFF="onoff",
        BRIGHTNESS="brightness",
        COLOR_TEMP="color_temp",
    )
    light.LightEntity = type("LightEntity", (), {})

    number = module("homeassistant.components.number")
    number.NumberEntity = type("NumberEntity", (), {})

    select = module("homeassistant.components.select")
    select.SelectEntity = type("SelectEntity", (), {})

    sensor = module("homeassistant.components.sensor")
    sensor.SensorDeviceClass = types.SimpleNamespace(
        BATTERY="battery",
        CURRENT="current",
        DISTANCE="distance",
        DURATION="duration",
        FREQUENCY="frequency",
        POWER="power",
        PRESSURE="pressure",
        TEMPERATURE="temperature",
        VOLTAGE="voltage",
    )
    sensor.SensorEntity = type("SensorEntity", (), {})
    sensor.SensorStateClass = types.SimpleNamespace(MEASUREMENT="measurement")

    switch = module("homeassistant.components.switch")
    switch.SwitchDeviceClass = types.SimpleNamespace(SWITCH="switch")
    switch.SwitchEntity = type("SwitchEntity", (), {})

    text = module("homeassistant.components.text")
    text.TextEntity = type("TextEntity", (), {})

    climate = module("homeassistant.components.climate")
    climate.ClimateEntity = type("ClimateEntity", (), {})
    climate.ClimateEntityFeature = types.SimpleNamespace(
        TARGET_TEMPERATURE=1,
        FAN_MODE=2,
        TARGET_TEMPERATURE_RANGE=4,
    )
    climate.HVACAction = types.SimpleNamespace(
        HEATING="heating",
        COOLING="cooling",
        DRYING="drying",
        FAN="fan",
        IDLE="idle",
        OFF="off",
    )
    climate.HVACMode = types.SimpleNamespace(
        OFF="off",
        HEAT="heat",
        COOL="cool",
        DRY="dry",
        AUTO="auto",
        HEAT_COOL="heat_cool",
        FAN_ONLY="fan_only",
    )

    cover = module("homeassistant.components.cover")
    cover.CoverEntity = type("CoverEntity", (), {})
    cover.CoverEntityFeature = types.SimpleNamespace(
        OPEN=1,
        CLOSE=2,
        SET_POSITION=4,
    )

    diagnostics = module("homeassistant.components.diagnostics")
    diagnostics.async_redact_data = lambda data, _: data

    frontend = module("homeassistant.components.frontend")
    frontend.registered_panels = []

    def _async_register_built_in_panel(hass, component_name, **kwargs):
        frontend.registered_panels.append(
            {
                "hass": hass,
                "component_name": component_name,
                **kwargs,
            }
        )

    frontend.async_register_built_in_panel = _async_register_built_in_panel
    frontend.async_panel_exists = lambda hass, url_path: False
    frontend.async_remove_panel = lambda hass, url_path: None

    lovelace = module("homeassistant.components.lovelace")
    lovelace.LOVELACE_DATA = "lovelace_data"

    lovelace_const = module("homeassistant.components.lovelace.const")
    lovelace_const.CONF_FILENAME = "filename"
    lovelace_const.CONF_ICON = "icon"
    lovelace_const.CONF_MODE = "mode"
    lovelace_const.CONF_REQUIRE_ADMIN = "require_admin"
    lovelace_const.CONF_SHOW_IN_SIDEBAR = "show_in_sidebar"
    lovelace_const.CONF_TITLE = "title"
    lovelace_const.CONF_URL_PATH = "url_path"
    lovelace_const.DEFAULT_ICON = "mdi:view-dashboard"
    lovelace_const.DOMAIN = "lovelace"
    lovelace_const.MODE_YAML = "yaml"

    lovelace_dashboard = module("homeassistant.components.lovelace.dashboard")

    class _LovelaceYAML:
        def __init__(self, hass, url_path, config) -> None:
            self.hass = hass
            self.url_path = url_path
            self.config = {**config, "url_path": url_path}

        @property
        def mode(self):
            return "yaml"

    lovelace_dashboard.LovelaceYAML = _LovelaceYAML

    device_tracker = module("homeassistant.components.device_tracker")
    device_tracker.SourceType = types.SimpleNamespace(GPS="gps")

    device_tracker_config_entry = module(
        "homeassistant.components.device_tracker.config_entry"
    )
    device_tracker_config_entry.TrackerEntity = type("TrackerEntity", (), {})

    scene = module("homeassistant.components.scene")
    scene.Scene = type("Scene", (), {})

    config_entries = module("homeassistant.config_entries")
    config_entries.ConfigEntry = type("ConfigEntry", (), {})

    class _ConfigFlow:
        def __init_subclass__(cls, **kwargs):
            return None

        def async_show_form(self, **kwargs):
            return kwargs

        def async_create_entry(self, **kwargs):
            return kwargs

    class _OptionsFlow:
        def async_show_form(self, **kwargs):
            return kwargs

        def async_create_entry(self, **kwargs):
            return kwargs

    config_entries.ConfigFlow = _ConfigFlow
    config_entries.ConfigFlowResult = dict
    config_entries.OptionsFlow = _OptionsFlow
    config_entries.OptionsFlowWithReload = _OptionsFlow

    ha_const = module("homeassistant.const")
    class _Platform(str):
        def __new__(cls, value):
            return str.__new__(cls, value)

    ha_const.ATTR_TEMPERATURE = "temperature"
    ha_const.ATTR_TARGET_TEMP_HIGH = "target_temp_high"
    ha_const.ATTR_TARGET_TEMP_LOW = "target_temp_low"
    ha_const.CONF_FILENAME = "filename"
    ha_const.CONF_ICON = "icon"
    ha_const.CONF_MODE = "mode"
    ha_const.CONF_PASSWORD = "password"
    ha_const.CONF_USERNAME = "username"
    ha_const.EVENT_HOMEASSISTANT_STOP = "homeassistant_stop"
    ha_const.Platform = _Platform
    ha_const.UnitOfTemperature = types.SimpleNamespace(
        CELSIUS="°C",
        FAHRENHEIT="°F",
    )

    core = module("homeassistant.core")
    core.HomeAssistant = type("HomeAssistant", (), {})

    exceptions = module("homeassistant.exceptions")
    exceptions.ConfigEntryAuthFailed = type("ConfigEntryAuthFailed", (Exception,), {})
    exceptions.ConfigEntryNotReady = type("ConfigEntryNotReady", (Exception,), {})
    exceptions.HomeAssistantError = type("HomeAssistantError", (Exception,), {})
    exceptions.ServiceValidationError = type(
        "ServiceValidationError",
        (exceptions.HomeAssistantError,),
        {},
    )

    entity_helper = module("homeassistant.helpers.entity")
    entity_helper.EntityCategory = types.SimpleNamespace(
        CONFIG="config",
        DIAGNOSTIC="diagnostic",
    )

    device_registry = module("homeassistant.helpers.device_registry")
    device_registry.async_get = lambda hass=None: None
    device_registry.async_entries_for_config_entry = lambda registry, entry_id: []

    entity_registry = module("homeassistant.helpers.entity_registry")
    entity_registry.RegistryEntryDisabler = types.SimpleNamespace(
        INTEGRATION="integration",
        USER="user",
    )

    entity_platform = module("homeassistant.helpers.entity_platform")
    entity_platform.AddEntitiesCallback = type("AddEntitiesCallback", (), {})

    update_coordinator = module("homeassistant.helpers.update_coordinator")

    class _CoordinatorEntity:
        def __init__(self, coordinator=None) -> None:
            self.coordinator = coordinator

        @classmethod
        def __class_getitem__(cls, _item):
            return cls

        def async_write_ha_state(self) -> None:
            return None

        def _handle_coordinator_update(self) -> None:
            return None

    class _DataUpdateCoordinator:
        def __init__(self, hass=None, logger=None, name=None, update_interval=None, config_entry=None) -> None:
            self.hass = hass
            self.logger = logger
            self.name = name
            self.update_interval = update_interval
            self.config_entry = config_entry
            self.data = None

        @classmethod
        def __class_getitem__(cls, _item):
            return cls

        def async_set_updated_data(self, data) -> None:
            self.data = data

    update_coordinator.CoordinatorEntity = _CoordinatorEntity
    update_coordinator.DataUpdateCoordinator = _DataUpdateCoordinator
    update_coordinator.UpdateFailed = type("UpdateFailed", (Exception,), {})

    aiohttp_client = module("homeassistant.helpers.aiohttp_client")
    aiohttp_client.async_get_clientsession = lambda hass: None
    aiohttp_client.async_create_clientsession = lambda hass: None

    issue_registry = module("homeassistant.helpers.issue_registry")
    issue_registry.IssueSeverity = types.SimpleNamespace(ERROR="error", WARNING="warning")
    issue_registry.created_issues = []
    issue_registry.deleted_issues = []

    def _async_create_issue(hass, domain, issue_id, **kwargs):
        issue_registry.created_issues.append(
            {
                "hass": hass,
                "domain": domain,
                "issue_id": issue_id,
                **kwargs,
            }
        )

    def _async_delete_issue(hass, domain, issue_id):
        issue_registry.deleted_issues.append(
            {
                "hass": hass,
                "domain": domain,
                "issue_id": issue_id,
            }
        )

    issue_registry.async_create_issue = _async_create_issue
    issue_registry.async_delete_issue = _async_delete_issue

    stub_coordinator = types.ModuleType("custom_components.hymer_connect_metadata.coordinator")
    stub_coordinator.HymerConnectCoordinator = type("HymerConnectCoordinator", (), {})
    sys.modules.setdefault("custom_components.hymer_connect_metadata.coordinator", stub_coordinator)
