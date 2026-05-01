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

    def fields(self):
        return {
            "temperature_c": round(self.temperature_c, 2),
            "temperature_f": round((self.temperature_c * 9 / 5) + 32, 2),
            "humidity_percent": round(self.humidity_percent, 2),
            "soil_moisture_voltage": round(self.soil_moisture_voltage, 3),
            "light_lux": round(self.light_lux, 2),
        }


class DHT22:
    def __init__(self, sensor):
        self.sensor = sensor

    @classmethod
    def build(cls):
        return cls(adafruit_dht.DHT22(board.D17))

    def read(self):
        temperature_c = self.sensor.temperature
        humidity_percent = self.sensor.humidity

        if temperature_c is None or humidity_percent is None:
            raise RuntimeError("DHT22 returned no data")

        return {
            "temperature_c": temperature_c,
            "humidity_percent": humidity_percent,
        }

    def close(self):
        self.sensor.exit()


class SoilProbe:
    def __init__(self, channel):
        self.channel = channel

    @classmethod
    def build(cls, i2c_bus, config):
        adc = ADS1115(i2c_bus, address=config.ads1115_address)
        soil_channel = AnalogIn(adc, ads1x15.Pin.A0)

        return cls(soil_channel)

    def read(self):
        return {
            "soil_moisture_voltage": self.channel.voltage,
        }

    def close(self):
        # AnalogIn / ADS1115 do not expose a sensor-level close.
        pass


class LightSensor:
    def __init__(self, sensor):
        self.sensor = sensor

    @classmethod
    def build(cls, i2c_bus, config):
        sensor = adafruit_bh1750.BH1750(i2c_bus, address=config.bh1750_address)

        return cls(sensor)

    def read(self):
        return {
            "light_lux": self.sensor.lux,
        }

    def close(self):
        # BH1750 does not expose a sensor-level close.
        pass


class Environment:
    def __init__(self, config):
        i2c_bus = board.I2C()

        self.sensors = [
            DHT22.build(),
            SoilProbe.build(i2c_bus, config),
            LightSensor.build(i2c_bus, config),
        ]

    @classmethod
    def build(cls, config):
        return cls(config)

    def read(self):
        values = {}

        for sensor in self.sensors:
            values.update(sensor.read())

        return Reading(**values)

    def close(self):
        errors = []

        for sensor in reversed(self.sensors):
            try:
                sensor.close()
            except Exception as error:
                errors.append(error)

        if errors:
            raise RuntimeError("One or more sensors failed to close") from errors[0]


class InfluxDB:
    def __init__(self, config):
        self.url = config.influxdb_url
        self.org = config.influxdb_org
        self.bucket = config.influxdb_bucket
        self.token = config.influxdb_token
        self.location = config.location
        self.measurement = config.measurement

    def write(self, reading):
        response = requests.post(
            self.url,
            params={
                "org": self.org,
                "bucket": self.bucket,
                "precision": "s",
            },
            headers={
                "Authorization": f"Token {self.token}",
                "Content-Type": "text/plain",
            },
            data=self.line_protocol(reading),
            timeout=5,
        )

        response.raise_for_status()

    def close(self):
        pass

    def line_protocol(self, reading):
        return (
            f"{self.measurement},location={self.location} "
            f"{','.join(f'{name}={value}' for name, value in reading.fields().items())}"
        )


def print_reading(reading):
    current_time = datetime.now().strftime("%a %b %d, %I:%M:%S %p")

    print(
        f"time={current_time} "
        f"{' '.join(f'{name}={value}' for name, value in reading.fields().items())}"
    )


def main():
    config = Config.from_env()
    environment = Environment.build(config)
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
