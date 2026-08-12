"""Konstanten der Integration."""

DOMAIN = "sma_meter_sim"
STORAGE_KEY = f"{DOMAIN}.energy"
STORAGE_VERSION = 1

# --- Zaehleridentitaet / Versand ---------------------------------------
CONF_SERIAL = "serial"
CONF_SUSY_ID = "susy_id"
CONF_DEVICE_TYPE = "device_type"
CONF_SEND_INTERVAL_MS = "send_interval_ms"
CONF_SMOOTHING_MS = "smoothing_ms"
CONF_INTERFACE_IP = "interface_ip"

# --- Quellen ------------------------------------------------------------
CONF_SOURCES = "sources"
CONF_SOURCE_TYPE = "source_type"
CONF_NAME = "name"
CONF_PROFILE = "profile"

SOURCE_MODBUS = "modbus"
SOURCE_MQTT = "mqtt"

# Modbus
CONF_HOST = "host"
CONF_PORT = "port"
CONF_UNIT = "unit"
CONF_INTERVAL_MS = "interval_ms"
CONF_WORD_SWAP = "word_swap"
CONF_INVERT_SIGN = "invert_sign"

# MQTT
CONF_USE_HA_BROKER = "use_ha_broker"
CONF_BROKER = "broker"
CONF_USERNAME = "username"
CONF_PASSWORD = "password"
CONF_TOPICS = "topics"
CONF_TOPIC = "topic"
CONF_VALUE_PATH = "value_path"

# Manuelle Zuordnung
CONF_KEY = "key"
CONF_PHASE = "phase"
CONF_SCALE = "scale"
CONF_ADDRESS = "address"
CONF_DTYPE = "dtype"
CONF_INPUT_REGISTER = "input_register"
CONF_ADD_ANOTHER = "add_another"
CONF_REGISTERS = "registers"
CONF_SOURCE_INDEX = "source_index"
CONF_TEST_ACTION = "test_action"

MEASURE_KEYS = [
    "p",
    "q",
    "s",
    "current",
    "voltage",
    "cos_phi",
    "frequency",
    "e_import",
    "e_export",
    "eq_import",
    "eq_export",
]
PHASE_CHOICES = ["", "l1", "l2", "l3"]
DTYPES = ["float32", "float64", "int16", "uint16", "int32", "uint32", "int64"]

DEVICE_TYPES = {
    "energy_meter_20": 349,
    "home_manager_20": 372,
}

DEFAULT_SEND_INTERVAL_MS = 1000
DEFAULT_SMOOTHING_MS = 100
DEFAULT_POLL_INTERVAL_MS = 100
DEFAULT_MODBUS_PORT = 502
DEFAULT_MODBUS_UNIT = 1
DEFAULT_MQTT_PORT = 1883

SERVICE_RESET_ENERGY = "reset_energy"

# Persistenzintervall der Energiezaehler (Sekunden)
ENERGY_SAVE_INTERVAL = 60
