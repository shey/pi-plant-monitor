import os
import sys
from dataclasses import dataclass
from datetime import datetime

import requests
import board
import adafruit_dht
import adafruit_bh1750
from dotenv import load_dotenv
from adafruit_ads1x15 import ADS1115, AnalogIn, ads1x15


BASE_DIR = os.path.dirname(os.path.abspath(__file__))
load_dotenv(os.path.join(BASE_DIR, ".env"))


@dataclass(frozen=True)
class Config:
    influxdb_url: str
    influxdb_org: str
    influxdb_bucket: str
    influxdb_token: str
    location: str
    measurement: str
    ads1115_address: int
    bh1750_address: int

    @classmethod
    def from_env(cls):
        return cls(
            influxdb_url=os.environ["INFLUXDB_URL"],
            influxdb_org=os.environ["INFLUXDB_ORG"],
            influxdb_bucket=os.environ["INFLUXDB_BUCKET"],
            influxdb_token=os.environ["INFLUXDB_TOKEN"],
            location=os.getenv("SENSOR_LOCATION", "plant_shelf"),
            measurement=os.getenv("INFLUXDB_MEASUREMENT", "plant_environment"),
            ads1115_address=int(os.getenv("ADS1115_ADDRESS", "0x48"), 16),
            bh1750_address=int(os.getenv("BH1750_ADDRESS", "0x23"), 16),
        )


@dataclass(frozen=True)
class Reading:
    temperature_c: float
    humidity_percent: float
    soil_moisture_voltage: float
    light_lux: float

    @property
    def temperature_f(self):
        return (self.temperature_c * 9 / 5) + 32

    def rounded(self):
        return Reading(
            temperature_c=round(self.temperature_c, 2),
            humidity_percent=round(self.humidity_percent, 2),
            soil_moisture_voltage=round(self.soil_moisture_voltage, 3),
            light_lux=round(self.light_lux, 2),
        )


class DHT22:
    def __init__(self, pin):
        self._sensor = adafruit_dht.DHT22(pin)

    def read(self):
        temperature_c = self._sensor.temperature
        humidity_percent = self._sensor.humidity

        if temperature_c is None or humidity_percent is None:
            raise RuntimeError("DHT22 returned no data")

        return {
            "temperature_c": temperature_c,
            "humidity_percent": humidity_percent,
        }

    def close(self):
        self._sensor.exit()


class SoilProbe:
    def __init__(self, channel):
        self._channel = channel

    def read(self):
        return {
            "soil_moisture_voltage": self._channel.voltage,
        }

    def close(self):
        # AnalogIn / ADS1115 do not expose a sensor-level close.
        pass


class LightSensor:
    def __init__(self, sensor):
        self._sensor = sensor

    def read(self):
        return {
            "light_lux": self._sensor.lux,
        }

    def close(self):
        # BH1750 does not expose a sensor-level close.
        pass


class Environment:
    def __init__(self, sensors):
        self._sensors = sensors

    def read(self):
        values = {}

        for sensor in self._sensors:
            values.update(sensor.read())

        return Reading(
            temperature_c=values["temperature_c"],
            humidity_percent=values["humidity_percent"],
            soil_moisture_voltage=values["soil_moisture_voltage"],
            light_lux=values["light_lux"],
        ).rounded()

    def close(self):
        errors = []

        for sensor in reversed(self._sensors):
            try:
                sensor.close()
            except Exception as error:
                errors.append(error)

        if errors:
            raise RuntimeError("One or more sensors failed to close") from errors[0]


class InfluxDB:
    def __init__(self, config):
        self._config = config

    def write(self, reading):
        response = requests.post(
            self._config.influxdb_url,
            params={
                "org": self._config.influxdb_org,
                "bucket": self._config.influxdb_bucket,
                "precision": "s",
            },
            headers={
                "Authorization": f"Token {self._config.influxdb_token}",
                "Content-Type": "text/plain",
            },
            data=self._line_protocol(reading),
            timeout=5,
        )

        response.raise_for_status()

    def close(self):
        pass

    def _line_protocol(self, reading):
        return (
            f"{self._config.measurement},location={self._config.location} "
            f"temperature_c={reading.temperature_c},"
            f"temperature_f={round(reading.temperature_f, 2)},"
            f"humidity_percent={reading.humidity_percent},"
            f"soil_moisture_voltage={reading.soil_moisture_voltage},"
            f"light_lux={reading.light_lux}"
        )


def build_i2c_bus():
    return board.I2C()


def build_soil_probe(i2c_bus, config):
    adc = ADS1115(i2c_bus, address=config.ads1115_address)
    soil_channel = AnalogIn(adc, ads1x15.Pin.A0)

    return SoilProbe(soil_channel)


def build_light_sensor(i2c_bus, config):
    sensor = adafruit_bh1750.BH1750(i2c_bus, address=config.bh1750_address)

    return LightSensor(sensor)


def build_environment(config):
    i2c_bus = build_i2c_bus()

    dht22 = DHT22(board.D17)
    soil_probe = build_soil_probe(i2c_bus, config)
    light_sensor = build_light_sensor(i2c_bus, config)

    return Environment(
        sensors=[
            dht22,
            soil_probe,
            light_sensor,
        ]
    )


def print_reading(reading):
    current_time = datetime.now().strftime("%a %b %d, %I:%M:%S %p")

    print(
        f"time={current_time} "
        f"temp_f={reading.temperature_f:.1f} "
        f"temp_c={reading.temperature_c:.1f} "
        f"humidity={reading.humidity_percent:.1f} "
        f"soil_voltage={reading.soil_moisture_voltage:.3f} "
        f"light_lux={reading.light_lux:.2f}"
    )


def main():
    config = Config.from_env()
    environment = build_environment(config)
    influxdb = InfluxDB(config)

    try:
        reading = environment.read()

        print_reading(reading)
        influxdb.write(reading)

        return 0

    finally:
        close_errors = []

        for resource in (environment, influxdb):
            try:
                resource.close()
            except Exception as error:
                close_errors.append(error)

        if close_errors:
            raise RuntimeError("One or more resources failed to close") from close_errors[0]


if __name__ == "__main__":
    sys.exit(main())
