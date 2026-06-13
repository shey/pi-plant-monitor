import arrow
import requests

from main import Config

config = Config.from_env()

start = arrow.utcnow().shift(minutes=-5)
stop = arrow.utcnow().shift(seconds=-5)

delete_url = config.influxdb_url.replace("/api/v2/write", "/api/v2/delete")

predicate = (
    f'_measurement="{config.measurement}" '
    f'AND location="{config.location}"'
)

response = requests.post(
    delete_url,
    params={
        "org": config.influxdb_org,
        "bucket": config.influxdb_bucket,
    },
    headers={
        "Authorization": f"Token {config.influxdb_token}",
        "Content-Type": "application/json",
    },
    json={
        "start": start.format("YYYY-MM-DDTHH:mm:ss[Z]"),
        "stop": stop.format("YYYY-MM-DDTHH:mm:ss[Z]"),
        "predicate": predicate,
    },
    timeout=10,
)

print(response.status_code)
print(response.text)

response.raise_for_status()
