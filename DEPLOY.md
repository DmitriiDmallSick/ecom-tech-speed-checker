# Deploy

See also: [README](README.md) · [CONFIGURATION](CONFIGURATION.md) · [RUNBOOK](RUNBOOK.md) · [SECURITY](SECURITY.md)

This guide describes the minimum deployment setup for the n8n ecommerce tech checker template.

## Components

The project has three runtime parts:

```text
n8n workflow
health-runner
speed-runner
```

The runners are Docker-based HTTP services. n8n calls them through public HTTPS endpoints.

## Required runner env

### Health runner

```env
PORT=8080
ALLOW_PUBLIC_ACCESS=false
HEALTH_RUNNER_API_KEY=replace-with-your-own-secret
ALLOWED_HOSTS=your-store-domain.com
MIN_TIMEOUT_MS=5000
DEFAULT_TIMEOUT_MS=45000
MAX_TIMEOUT_MS=120000
MAX_CONCURRENT_RUNS=1
QUEUE_TIMEOUT_MS=300000
LOG_LEVEL=INFO
```

### Speed runner

```env
PORT=8080
ALLOW_PUBLIC_ACCESS=false
SPEED_RUNNER_API_KEY=replace-with-your-own-secret
ALLOWED_HOSTS=your-store-domain.com
MIN_TIMEOUT_MS=5000
DEFAULT_TIMEOUT_MS=60000
MAX_TIMEOUT_MS=120000
MAX_CONCURRENT_RUNS=1
QUEUE_TIMEOUT_MS=300000
LOG_LEVEL=INFO
```

Use different API keys for health and speed if possible.

`LOG_LEVEL` controls the runner's own log verbosity (`DEBUG`, `INFO`, `WARNING`, `ERROR`; default `INFO`). Both runners log request start/finish, rejected auth attempts, and unhandled check/measurement errors — check your platform's container logs when a run fails.

## Build locally

### Health runner

```bash
cd health-runner
docker build -t ecom-health-runner .
```

### Speed runner

```bash
cd speed-runner
docker build -t ecom-speed-runner .
```

## Run locally

### Health runner

```bash
docker run --rm -p 8081:8080 \
  -e HEALTH_RUNNER_API_KEY=test-health-secret \
  -e ALLOWED_HOSTS=example.com \
  ecom-health-runner
```

### Speed runner

```bash
docker run --rm -p 8082:8080 \
  -e SPEED_RUNNER_API_KEY=test-speed-secret \
  -e ALLOWED_HOSTS=example.com \
  ecom-speed-runner
```

## Smoke tests

### Health runner health endpoint

```bash
curl http://localhost:8081/health
```

### Health runner protected check

```bash
curl -X POST http://localhost:8081/health-check \
  -H 'Content-Type: application/json' \
  -H 'x-api-key: test-health-secret' \
  -d '{"pages":{"home":{"url":"https://example.com/"}},"options":{"checks":["home_page_open"]}}'
```

### Speed runner JSON

```bash
curl -X POST http://localhost:8082/speed-report-json \
  -H 'Content-Type: application/json' \
  -H 'x-api-key: test-speed-secret' \
  -d '{"pages":{"home":{"url":"https://example.com/"}}}'
```

### Speed runner PNG

```bash
curl -X POST http://localhost:8082/speed-report-image \
  -H 'Content-Type: application/json' \
  -H 'x-api-key: test-speed-secret' \
  -d '{"pages":{"home":{"url":"https://example.com/"}}}' \
  --output speed-report.png
```

## n8n configuration

Import:

```text
n8n/tech-checker-template.json
```

Then configure the `Build Report Config` node.

Required values:

```js
project_slug: ''
project_name: ''
base_url: ''
product_path: ''
cart_path: ''
health_runner_url: ''
health_runner_api_key: ''
speed_json_url: ''
speed_image_url: ''
speed_runner_api_key: ''
```

The `health_runner_api_key` must match `HEALTH_RUNNER_API_KEY`.

The `speed_runner_api_key` must match `SPEED_RUNNER_API_KEY`.

For scheduled reports, configure `REPORT_CHAT_ID` in the `Normalize Run` node.

## Production checklist

Before enabling the schedule:

- health runner `/health` works
- speed runner `/health` works
- health runner rejects requests without key
- speed runner rejects requests without key
- `ALLOWED_HOSTS` is configured
- n8n Telegram credentials are connected
- manual `/help` works
- manual `/speed` works
- manual `/health` works
- manual `/report` works

Only enable the schedule after manual checks pass.
