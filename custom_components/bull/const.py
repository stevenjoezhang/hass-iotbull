"""Constants for bull-iot integration."""

from homeassistant.const import UnitOfPower, UnitOfElectricPotential, UnitOfElectricCurrent, SERVICE_RELOAD

DOMAIN = "bull"
BULL_DEVICES = "bull_devices"
BULL_API_CLIENTS = "bull_api_clients"
SUPPORTED_PLATFORMS = ["switch", "sensor", "cover"]

APPSECRET = b"t3f9hqri8ciuici50aem25xmcyqsopey"
API_URL = "https://api.iotbull.com"

SWITCH_PRODUCT_ID = [4, 5, 6, 7, 13, 14, 34, 35, 36, 180]
COVER_PRODUCT_ID = [31]

SENSOR_MAPPING = {
    "RealTimePower": {"name": "功率", "unit": UnitOfPower.WATT},
    "RealTimeVoltage": {"name": "电压", "unit": UnitOfElectricPotential.VOLT},
    "RealTimeCurrent": {"name": "电流", "unit": UnitOfElectricCurrent.AMPERE},
}
