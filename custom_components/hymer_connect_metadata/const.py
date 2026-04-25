"""Constants for HYMER Connect Metadata integration."""

DOMAIN = "hymer_connect_metadata"
MANUFACTURER = "Erwin Hymer Group"
STATIC_URL_PATH = f"/{DOMAIN}_static"
VEHICLE_ENTITY_PICTURE = f"{STATIC_URL_PATH}/campervan.svg"

# --- Base URLs ---
API_BASE_URL = "https://smartrv.erwinhymergroup.com"
API_BASE_URL_SCC = "https://scc-api.smartrv.erwinhymergroup.com"
API_BASE_URL_RVTWIN = "https://scc-rvtwin.smartrv.erwinhymergroup.com"
API_BASE_URL_APPCOMM = "https://scc-appcomm.smartrv.erwinhymergroup.com"

# --- OAuth2 Authentication ---
ENDPOINT_AUTH = "/api/v2/oauth/token"
AUTH_GRANT_TYPE_PASSWORD = "password"
AUTH_GRANT_TYPE_REFRESH = "refresh_token"

# --- Main API Endpoints ---
ENDPOINT_ACCOUNTS_ME = "/api/ehg/v1/accounts/me"
ENDPOINT_VEHICLES_BY_TOKEN = "/api/ehg/v1/vehicles/byToken"
ENDPOINT_CONFIRMATION_TOKEN = "/api/ehg/v1/accounts/confirmationToken"

# --- SCC API Endpoints ---
ENDPOINT_RV_TWIN_VEHICLES = "/api/rv-twin/vehicles"
ENDPOINT_CONFIG_MENU = "/api/config/menu"
ENDPOINT_CONFIG_BRANDS = "/api/config/brands/details"
ENDPOINT_SERVICE_CATALOGUE = "/api/service-catalogue/services"
ENDPOINT_PUSH_NOTIFICATIONS = "/api/push-notifications/subscriptions/scu"
ENDPOINT_PUSH_DEVICE_REG = "/api/push-notifications/devices"

# --- SignalR ---
SIGNALR_NEGOTIATE_PATH = "/datahub/negotiate"
SIGNALR_HUB_NAME = "datahub"

# --- Headers ---
HEADER_ACCESS_TOKEN = "scc-csngaccesstoken"
HEADER_BRAND = "scc-brand"
HEADER_LOCALE = "scc-locale"
HEADER_APP_VERSION = "scc-appversion"
HEADER_EHG_BRAND = "ehg-smart-caravan-brand"

# --- App Version ---
APP_VERSION = "2.10.14"
USER_AGENT = "okhttp/4.10.0"

# --- Brands ---
BRANDS = {
    "hymer": "HYMER",
    "buerstner": "Bürstner",
    "dethleffs": "Dethleffs",
    "eriba": "Eriba",
    "lmc": "LMC",
    "niesmann-bischoff": "Niesmann+Bischoff",
    "sunlight": "Sunlight",
    "carado": "Carado",
    "laika": "Laika",
    "freeontour": "FreeOnTour",
}

# Default scan interval (seconds)
DEFAULT_SCAN_INTERVAL = 60

# Config keys
CONF_BRAND = "brand"
CONF_ACCESS_TOKEN = "access_token"
CONF_REFRESH_TOKEN = "refresh_token"
CONF_VEHICLE_URN = "vehicle_urn"
CONF_SCU_URN = "scu_urn"
CONF_VEHICLE_ID = "vehicle_id"
CONF_VIN = "vin"
CONF_VEHICLE_NAME = "vehicle_name"
CONF_VEHICLE_MODEL = "vehicle_model"
CONF_VEHICLE_MODEL_GROUP = "vehicle_model_group"
CONF_VEHICLE_MODEL_YEAR = "vehicle_model_year"
CONF_EHG_TOKEN = "ehg_access_token"
CONF_EHG_REFRESH_TOKEN = "ehg_refresh_token"
CONF_SHOW_ADMIN_ACTIONS = "show_admin_actions"
CONF_SHOW_DEBUG_DIAGNOSTICS = "show_debug_diagnostics"
CONF_USE_MILES = "use_miles"
CONF_USE_FAHRENHEIT = "use_fahrenheit"

# Platforms — discovery-driven. `cover`, `fan`, and `scene` are template-only
# platforms; `number` handles writable numeric slots from the raw layer.
PLATFORMS = [
    "sensor", "binary_sensor", "device_tracker",
    "light", "switch", "button", "text", "climate", "cover", "fan", "select", "number", "scene",
]
