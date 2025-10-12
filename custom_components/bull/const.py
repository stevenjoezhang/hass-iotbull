"""Constants for bull-iot integration."""

from homeassistant.const import UnitOfPower, UnitOfElectricPotential, UnitOfElectricCurrent, UnitOfEnergy, UnitOfTime, SERVICE_RELOAD

DOMAIN = "bull"
BULL_DEVICES = "bull_devices"
BULL_API_CLIENTS = "bull_api_clients"
SUPPORTED_PLATFORMS = ["switch", "sensor", "cover"]

APPSECRET = b"t3f9hqri8ciuici50aem25xmcyqsopey"
API_URL = "https://api.iotbull.com"

SWITCH_PRODUCT_ID = {4, 5, 6, 7, 13, 14, 30, 34, 35, 36, 53, 102, 104, 149, 157, 158, 159, 180}
CHARGER_PRODUCT_ID = {75, 141, 196}
COVER_PRODUCT_ID = {31, 56}

SENSOR_MAPPING = {
    # For product 7, 14, 30, 53, 180
    "RealTimePower": {"name": "功率", "unit": UnitOfPower.WATT, "class": "power"},
    # For product 53, 180
    "RealTimeVoltage": {"name": "电压", "unit": UnitOfElectricPotential.VOLT, "class": "voltage"},
    "RealTimeCurrent": {"name": "电流", "unit": UnitOfElectricCurrent.AMPERE, "class": "current"},
    # For product 75, 141, 196
    "ActivePower": {"name": "功率", "unit": UnitOfPower.WATT, "class": "power"},
    "Voltage": {"name": "电压", "unit": UnitOfElectricPotential.VOLT, "class": "voltage", "scale": 10},
    "Current": {"name": "电流", "unit": UnitOfElectricCurrent.AMPERE, "class": "current", "scale": 100},
    "ChargingTime": {"name": "充电时长", "unit": UnitOfTime.MINUTES, "class": "duration"},
    "EnergyUsed": {"name": "充电量", "unit": UnitOfEnergy.KILO_WATT_HOUR, "class": "energy", "scale": 100},
}
