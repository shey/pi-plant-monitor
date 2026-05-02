import os
import sys
from dataclasses import dataclass
from datetime import datetime

import time
from statistics import mean

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
    dht22_sample_count: int
    dht22_sample_delay_seconds: float
    dht22_discard_initial_samples: int


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
            dht22_sample_count=int(os.getenv("DHT22_SAMPLE_COUNT", "5")),
            dht22_sample_delay_seconds=float(os.getenv("DHT22_SAMPLE_DELAY_SECONDS", "2")),
            dht22_discard_initial_samples=int(os.getenv("DHT22_DISCARD_INITIAL_SAMPLES", "1")),
        )


@dataclass(frozen=True)
class Reading:
    temperature_c: float
    humidity_percent: float
    soil_moisture_voltage: float
    light_lux: float

    @property
    def temperature_c_value(self):
        return round(self.temperature_c, 2)

    @property
    def temperature_f(self):
        return round((self.temperature_c * 9 / 5) + 32, 2)

    @property
    def humidity_percent_value(self):
        return round(self.humidity_percent, 2)

    @property
    def soil_moisture_voltage_value(self):
        return round(self.soil_moisture_voltage, 3)

    @property
    def light_lux_value(self):
        return round(self.light_lux, 2)

    @property
    def fields(self):
        return {
            "temperature_c": self.temperature_c_value,
            "temperature_f": self.temperature_f,
            "humidity_percent": self.humidity_percent_value,
            "soil_moisture_voltage": self.soil_moisture_voltage_value,
            "light_lux": self.light_lux_value,
        }


class InfluxReading:
    def __init__(self, reading):
        self.reading = reading

    @property
    def line_protocol_fields(self):
        return ",".join(
            f"{name}={value}"
            for name, value in self.reading.fields.items()
        )


class ConsoleReading:
    def __init__(self, reading):
        self.reading = reading

    @property
    def timestamp(self):
        return datetime.now().strftime("%a %b %d, %I:%M:%S %p")

    @property
    def fields(self):
        return " ".join(
            f"{name}={value}"
            for name, value in self.reading.fields.items()
        )

    @property
    def output(self):
        return f"time={self.timestamp} {self.fields}"


class DHT22:
    def __init__(self, sensor, sample_count, sample_delay_seconds, discard_initial_samples):
        self.sensor = sensor
        self.sample_count = sample_count
        self.sample_delay_seconds = sample_delay_seconds
        self.discard_initial_samples = discard_initial_samples

    @classmethod
    def build(cls, config):
        return cls(
            sensor=adafruit_dht.DHT22(board.D17),
            sample_count=config.dht22_sample_count,
            sample_delay_seconds=config.dht22_sample_delay_seconds,
            discard_initial_samples=config.dht22_discard_initial_samples,
        )

    def read(self):
        samples = self.samples()

        if not samples:
            raise RuntimeError("DHT22 returned no usable samples")

        return {
            "temperature_c": mean(sample["temperature_c"] for sample in samples),
            "humidity_percent": mean(sample["humidity_percent"] for sample in samples),
        }

    def samples(self):
        samples = []

        for attempt in range(self.total_attempts):
            sample = self.sample()

            if sample and attempt >= self.discard_initial_samples:
                samples.append(sample)

            self.sleep_between_samples(attempt)

        return samples

    def sample(self):
        try:
            temperature_c = self.sensor.temperature
            humidity_percent = self.sensor.humidity

            if temperature_c is None or humidity_percent is None:
                return None

            return {
                "temperature_c": temperature_c,
                "humidity_percent": humidity_percent,
            }

        except RuntimeError:
            # DHT22 reads are occasionally flaky. Skip the failed sample.
            return None

    def sleep_between_samples(self, attempt):
        if attempt < self.total_attempts - 1:
            time.sleep(self.sample_delay_seconds)

    @property
    def total_attempts(self):
        return self.sample_count + self.discard_initial_samples

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
            DHT22.build(config),
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


class Console:
    def write(self, reading):
        print(ConsoleReading(reading).output)

    def close(self):
        pass


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
            f"{InfluxReading(reading).line_protocol_fields}"
        )


def main():
    config = Config.from_env()
    environment = Environment.build(config)
    console = Console()
    influxdb = InfluxDB(config)

    try:
        reading = environment.read()

        console.write(reading)
        influxdb.write(reading)

        return 0

    finally:
        close_errors = []

        for resource in (environment, console, influxdb):
            try:
                resource.close()
            except Exception as error:
                close_errors.append(error)

        if close_errors:
            raise RuntimeError("One or more resources failed to close") from close_errors[0]


if __name__ == "__main__":
    sys.exit(main())
