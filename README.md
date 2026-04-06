# loadtester

`loadtester` is a local CLI for closed-loop API load testing with JSON request bodies.

## What it does

- Discovers top-level JSON configs from a config directory
- Optionally fetches one bearer token before the timed run
- Preloads sample request payloads before timing begins
- Runs one async worker per virtual user against a shared `httpx.AsyncClient`
- Streams per-request results to `details.csv`
- Writes run metadata and aggregates to `summary.json`

## Warning about secrets

Auth configuration values are treated as literal config values in v1. If a config contains client secrets or other credentials, do not commit that file.

## Install

```bash
python -m pip install -e .
```

## Run

```bash
python -m loadtester --config-dir ./configs
python -m loadtester --config-dir ./configs --index 0
python -m loadtester --config ./configs/orders_create.json
python -m loadtester --config-dir ./configs --list
python -m loadtester --config ./configs/orders_create.json --validate-only
loadtester --config-dir ./configs --output-dir ./runs
```

## Config shape

```json
{
  "service_name": "Orders API Create Order",
  "method": "POST",
  "url": "https://api.example.com/orders",
  "headers": {
    "Content-Type": "application/json"
  },
  "metadata": {
    "environment": "dev",
    "build_id": "42"
  },
  "auth": {
    "token_url": "https://api.example.com/auth/token",
    "method": "POST",
    "body_mode": "form",
    "headers": {
      "Content-Type": "application/x-www-form-urlencoded"
    },
    "body": {
      "client_id": "my-client",
      "client_secret": "my-secret"
    },
    "token_json_path": "access_token",
    "header_name": "Authorization",
    "header_template": "Bearer {token}"
  },
  "sample_request_files": [
    "../requests/order_001.json",
    "../requests/order_002.json"
  ],
  "duration_seconds": 300,
  "parallel_users": 20,
  "ramp_up_seconds": 30,
  "timeout_seconds": 30,
  "verify_tls": true
}
```

## Outputs

Each run creates a timestamped folder under the output root:

```text
runs/
  2026-04-06T14-05-31_orders-api-create-order/
    input_config.json
    details.csv
    summary.json
```

`summary.json` includes the original config metadata, request rate, and latency percentiles (`p50`, `p90`, `p95`, `p99`). The CLI also prints a periodic heartbeat during longer runs with attempts, responses, exceptions, and current request rate.
