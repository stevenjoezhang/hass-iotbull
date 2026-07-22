"""Constants for bull-iot integration."""

from homeassistant.const import (
    PERCENTAGE,
    UnitOfPower,
    UnitOfElectricPotential,
    UnitOfElectricCurrent,
    UnitOfEnergy,
    UnitOfTime,
    UnitOfTemperature,
    SERVICE_RELOAD as HA_SERVICE_RELOAD,
)

DOMAIN = "bull"
BULL_API_CLIENTS = "bull_api_clients"
SERVICE_RELOAD = HA_SERVICE_RELOAD
SUPPORTED_PLATFORMS = [
    "switch",
    "light",
    "sensor",
    "cover",
    "binary_sensor",
    "button",
    "select",
]

# MosHome 5.1.13 production CloudAPI credentials.  These are application
# credentials embedded in the official client, rather than user credentials.
APPKEY = "2037288812025"
APPSECRET = b"XxNjYxonJBt6DskF368fdjLEQZC9ziMeLOZPf"
APP_VERSION = "5.1.13"
APP_CLIENT_ID = "paascloudclientuic"
APP_CLIENT_SECRET = "paascloudClientSecret"
# Base64 of the AES key used by the Android client to encrypt login usernames.
LOGIN_AES_KEY = "cjJ2R1dCQTNRQVJOblJsUg=="
API_URL = "https://api.iotbull.com"

SWITCH_PRODUCT_ID = {
    4,
    5,
    6,
    7,
    13,
    14,
    30,
    34,
    35,
    36,
    53,
    102,
    103,
    104,
    149,
    157,
    158,
    159,
    180,
    206,
    219,
    220,
    221,
    279,
}
CHARGER_PRODUCT_ID = {74, 75, 141, 196, 199, 258, 274, 275, 309}
COVER_PRODUCT_ID = {31, 56, 59, 168, 198, 291}
LIGHT_PRODUCT_ID = {79, 125, 127, 171, 175, 236, 237}
SENSOR_PRODUCT_ID = {61, 63, 66, 68}

BUTTON_ENTITY_MAPPING = {
    258: [
        {
            "entity_identifier": "activate_charging",
            "name": "激活充电",
            "service_identifier": "SheduleAuth",
            "available_condition": {"all": [{"DeviceWorkState.WorkState": 8}]},
        }
    ]
}

SENSOR_MAPPING = {
    # For product 63, 66, 68
    "BatteryPercent": {
        "name": "电池电量",
        "translation_key": "battery",
        "unit": PERCENTAGE,
        "class": "battery",
    },
    # For product 7, 14, 30, 53, 180
    "RealTimePower": {"name": "功率", "unit": UnitOfPower.WATT, "class": "power"},
    # For product 53, 180
    "RealTimeVoltage": {
        "name": "电压",
        "unit": UnitOfElectricPotential.VOLT,
        "class": "voltage",
    },
    "RealTimeCurrent": {
        "name": "电流",
        "unit": UnitOfElectricCurrent.AMPERE,
        "class": "current",
    },
    # For product 75, 141, 196
    "ActivePower": {"name": "功率", "unit": UnitOfPower.WATT, "class": "power"},
    "Voltage": {
        "name": "电压",
        "unit": UnitOfElectricPotential.VOLT,
        "class": "voltage",
        "scale": 10,
    },
    "Current": {
        "name": "电流",
        "unit": UnitOfElectricCurrent.AMPERE,
        "class": "current",
        "scale": 100,
    },
    "ChargingTime": {
        "name": "充电时长",
        "unit": UnitOfTime.MINUTES,
        "class": "duration",
    },
    "DeviceFaultCodeInfo": {
        "name": "故障信息",
        "unit": None,
        "class": None,
    },
    "EnergyUsed": {
        "name": "充电量",
        "unit": UnitOfEnergy.KILO_WATT_HOUR,
        "class": "energy",
        "scale": 100,
    },
    "ChargeMode": {
        "name": "充电模式",
        "unit": None,
        "class": None,
        "value_map": {
            0: "即插即充",
            1: "无感充电",
        },
    },
    "WorkState": {
        "name": "充电状态",
        "unit": None,
        "class": None,
        "value_map": {
            0: "待机",
            1: "启动中",
            2: "充电中",
            3: "停止中",
            4: "已完成",
            5: "已暂停",
            6: "故障",
            7: "维护",
            8: "待认证",
            9: "定时中",
            10: "降额充电",
        },
    },
    "GunState": {
        "name": "插枪状态",
        "unit": None,
        "class": None,
        "value_map": {0: "未插枪", 1: "已插枪"},
    },
    "DeviceRealInfo": {
        "ChargingTime": {
            "name": "充电时长",
            "unit": UnitOfTime.SECONDS,
            "class": "duration",
        },
        "ChargeVoltage": {
            "name": "实时电压",
            "unit": UnitOfElectricPotential.VOLT,
            "class": "voltage",
        },
        "ChargeCurrent": {
            "name": "实时电流",
            "unit": UnitOfElectricCurrent.AMPERE,
            "class": "current",
        },
        "ChargeActivePower": {
            "name": "实时功率",
            "unit": UnitOfPower.KILO_WATT,
            "class": "power",
        },
        "ChargeEnergyUsed": {
            "name": "当前充电量",
            "unit": UnitOfEnergy.KILO_WATT_HOUR,
            "class": "energy",
        },
        "ChargeMBTemp": {
            "name": "主板温度",
            "unit": UnitOfTemperature.CELSIUS,
            "class": "temperature",
        },
        "ChargeSlotTemp": {
            "name": "插座温度",
            "unit": UnitOfTemperature.CELSIUS,
            "class": "temperature",
        },
        "ChargeGunTemp": {
            "name": "枪头温度",
            "unit": UnitOfTemperature.CELSIUS,
            "class": "temperature",
        },
        "EnergyUsed": {
            "name": "当前充电量",
            "unit": UnitOfEnergy.KILO_WATT_HOUR,
            "class": "energy",
        },
        "Current": {
            "name": "实时电流",
            "unit": UnitOfElectricCurrent.AMPERE,
            "class": "current",
        },
        "Voltage": {
            "name": "实时电压",
            "unit": UnitOfElectricPotential.VOLT,
            "class": "voltage",
        },
        "ActivePower": {
            "name": "实时功率",
            "unit": UnitOfPower.KILO_WATT,
            "class": "power",
        },
        "GunTemp": {
            "name": "枪头温度",
            "unit": UnitOfTemperature.CELSIUS,
            "class": "temperature",
        },
        "SlotTemp": {
            "name": "插座温度",
            "unit": UnitOfTemperature.CELSIUS,
            "class": "temperature",
        },
        "MBTemp": {
            "name": "主板温度",
            "unit": UnitOfTemperature.CELSIUS,
            "class": "temperature",
        },
    },
    "DeviceWorkState": {
        "WorkState": {
            "name": "充电状态",
            "unit": None,
            "class": None,
            "value_map": {
                0: "未工作",
                2: "充电中",
                8: "已插枪未激活",
                9: "已插枪已激活",
            },
        },
        "GunState": {
            "name": "插枪状态",
            "unit": None,
            "class": None,
            "value_map": {
                0: "未插枪",
                1: "已插枪",
            },
        },
    },
}
