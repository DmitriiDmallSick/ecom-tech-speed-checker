# Runbook

See also: [README](README.md) · [CONFIGURATION](CONFIGURATION.md) · [DEPLOY](DEPLOY.md) · [SECURITY](SECURITY.md)

This runbook explains how to operate, test, debug, rebuild, and redeploy the ecommerce tech checker.

The project consists of:

- an n8n workflow
- a health runner HTTP service
- a speed runner HTTP service
- Telegram reports

The n8n workflow orchestrates checks. The runners perform browser-based website checks and return results to n8n.

## Main components

| Component | Purpose |
|---|---|
| n8n workflow | Runs scheduled/manual checks, calls runners, sends Telegram reports |
| Health runner | Checks product page, cart flow, add-to-cart logic, duplicate lines, totals |
| Speed runner | Checks first-screen visual speed and returns JSON/PNG reports |
| Telegram bot | Receives manual commands and sends reports |

## Manual Telegram commands

| Command | Purpose |
|---|---|
| `/help` | Show available commands |
| `/speed` | Run speed check only |
| `/health` | Run health check only |
| `/report` | Run full report |

Recommended manual test order:

```text
/help
/speed
/health
/report
```

Start with `/speed` and `/health` separately before testing `/report`.

## Runner endpoints

The workflow expects three public HTTP endpoints:

```text
health_runner_url
speed_json_url
speed_image_url
```

Example endpoint structure:

```text
https://your-health-runner.example.com/health-check
https://your-speed-runner.example.com/speed-report-json
https://your-speed-runner.example.com/speed-report-image
```

These URLs are configured in the n8n node:

```text
Build Report Config
```

## Runner logs

Both runners log to stdout/stderr using Python's standard `logging` module, so they show up in
whatever log viewer your hosting platform provides for the container (Yandex Cloud Serverless
Containers logs, Cloud Run logs, `docker logs`, etc.).

What gets logged:

- request start and finish for `/health-check` and the speed endpoints, including `report_id`,
  final status, and duration in milliseconds
- rejected requests with a missing or invalid API key (client host only — never the key itself)
- `429` responses when the runner is busy (`MAX_CONCURRENT_RUNS` reached)
- unhandled exceptions from an individual check or site measurement, with traceback

Set `LOG_LEVEL` (env var, default `INFO`) to `DEBUG`, `WARNING`, or `ERROR` to change verbosity. See
[DEPLOY.md](DEPLOY.md) for where to set it.

## Local runner test

Before deploying a runner, test it locally.

### Build Docker image

From the runner directory:

```bash
docker build -t health-runner .
```

or:

```bash
docker build -t speed-runner .
```

### Run container locally

```bash
docker run --rm -p 8080:8080 health-runner
```

or:

```bash
docker run --rm -p 8080:8080 speed-runner
```

### Test health runner locally

```bash
curl -X POST http://localhost:8080/health-check \
  -H "Content-Type: application/json" \
  -d '{}'
```

### Test speed JSON locally

```bash
curl -X POST http://localhost:8080/speed-report-json \
  -H "Content-Type: application/json" \
  -d '{}'
```

### Test speed PNG locally

```bash
curl -X POST http://localhost:8080/speed-report-image \
  -H "Content-Type: application/json" \
  -o speed-report.png \
  -d '{}'
```

Expected result:

- health runner returns JSON
- speed JSON endpoint returns JSON
- speed PNG endpoint returns an image file

## Rebuild runner image

Use this when code changes.

### Health runner

```bash
cd health-runner
docker build -t health-runner .
```

### Speed runner

```bash
cd speed-runner
docker build -t speed-runner .
```

## Deploy runners

The runners can be deployed to any platform that supports Docker containers and public HTTPS endpoints.

Examples:

- Yandex Cloud Serverless Containers
- Google Cloud Run
- AWS App Runner
- Azure Container Apps
- Render
- Railway
- Fly.io
- VPS with Docker

After deployment, copy the public endpoint URLs into the `Build Report Config` node.

## Post-deploy checklist

After deploying or updating runners:

1. Open the deployed health runner URL.
2. Test `health_runner_url` with a POST request.
3. Test `speed_json_url` with a POST request.
4. Test `speed_image_url` with a POST request.
5. Run `/speed` in Telegram.
6. Run `/health` in Telegram.
7. Run `/report` in Telegram.
8. Enable or keep the n8n schedule only after manual checks pass.

## n8n workflow checks

Before enabling the workflow, verify these nodes.

### `Normalize Run`

Check scheduled report chat ID:

```js
const REPORT_CHAT_ID = 'TELEGRAM_CHAT_ID_HERE';
```

In your private n8n instance, replace it with the real Telegram chat ID.

Do not commit real chat IDs to a public repository.

### `Build Report Config`

Check that all required values are filled:

```text
project_slug
project_name
base_url
product_path
cart_path
health_runner_url
speed_json_url
speed_image_url
selectors.product_gallery
```

### HTTP nodes

Expected URL expressions:

```js
={{ $json.runners.health_url }}
={{ $json.runners.speed_json_url }}
={{ $json.runners.speed_image_url }}
```

HTTP nodes should not contain hardcoded runner URLs.

### Telegram nodes

Telegram nodes should use workflow data for chat IDs where possible.

Expected expressions:

```js
={{ $json.chat_id }}
```

or:

```js
={{ $('Build Final Text Report').first().json.telegram.chat_id }}
```

## Common failures

### Telegram command does not trigger workflow

Check:

- workflow is active
- Telegram Trigger node is connected to credentials
- bot token is valid
- bot is added to the group/chat
- Telegram webhook is registered correctly
- command is sent to the correct bot

Test with:

```text
/help
```

### Scheduled report does not arrive

Check:

- workflow is active
- `Schedule Trigger` is enabled
- timezone is correct
- `REPORT_CHAT_ID` is set
- Telegram bot can send messages to the target chat
- Telegram chat ID is correct

### Health check fails

Check:

- `base_url` is correct
- `product_path` is correct
- `cart_path` is correct
- product page is publicly accessible
- cart page is publicly accessible
- `selectors.product_gallery` exists on the product page
- product can be added to cart manually
- runner has enough memory for browser automation
- runner timeout is long enough

### Speed check fails

Check:

- `speed_json_url` is correct
- speed runner is deployed and running
- endpoint accepts POST requests
- response is valid JSON
- runner has enough memory for Chromium or Playwright
- timeout is long enough
- target page is accessible from the runner environment

### PNG report is not sent

Check:

- `speed_image_url` is correct
- endpoint returns binary image data
- n8n HTTP node response format is `File`
- binary property name is `data`
- Telegram `Send PNG` node has binary data enabled
- Telegram bot can send photos to the chat

### Report text is sent, but PNG is missing

This usually means the main report succeeded, but the PNG branch failed.

Check nodes:

```text
Call Speed PNG
Send PNG
```

Also check the binary output of `Call Speed PNG`.

### Runner returns invalid JSON

Check the [runner logs](#runner-logs).

In n8n, check:

```text
Call Health Runner
Call Speed Runner
Normalize Health Result
Normalize Speed Result
```

The normalize nodes can handle some invalid responses, but the runner should still return valid JSON in normal operation.

### Browser automation times out

Increase runner timeout values in:

```text
Build Report Config
```

Fields:

```js
health_timeout_ms
speed_timeout_ms
```

Also check HTTP Request node timeout in n8n.

Recommended HTTP Request timeout:

```text
300000
```

## Before committing workflow JSON

Search the exported workflow JSON for private data.

Do not commit:

```text
credentials
telegramApi
Telegram bot token
real Telegram chat ID
private runner URLs
real runner API keys
real project domains
product IDs
variant IDs
customer data
order data
logs with private data
```

Prefer `$env.HEALTH_RUNNER_API_KEY` / `$env.SPEED_RUNNER_API_KEY` / `$env.REPORT_CHAT_ID` (or Header
Auth credentials) over literal values in `Build Report Config` / `Normalize Run` — see
[CONFIGURATION.md](CONFIGURATION.md#hardcoded-runner-api-keys) — so a real secret can't end up in
the exported JSON in the first place.

Safe template placeholders:

```text
TELEGRAM_CHAT_ID_HERE
project_slug: ''
project_name: ''
base_url: ''
health_runner_url: ''
health_runner_api_key: ''
speed_json_url: ''
speed_image_url: ''
speed_runner_api_key: ''
```

## Recovery checklist

If reports stop working:

1. Run `/help`.
2. Run `/speed`.
3. Run `/health`.
4. Check n8n execution logs.
5. Check runner logs.
6. Test runner endpoints with `curl`.
7. Check Telegram credentials.
8. Check runner deployment status.
9. Check recent website/theme/cart changes.
10. Rebuild and redeploy the affected runner if needed.

## Maintenance checklist

Recommended regular maintenance:

- review failed n8n executions
- check runner logs
- verify Telegram reports are still delivered
- retest after ecommerce theme updates
- retest after cart logic changes
- retest after changing product page layout
- update selectors when storefront markup changes
- keep Docker images and dependencies updated
- avoid committing private workflow exports

## When to redeploy runners

Redeploy a runner when:

- runner code changes
- Dockerfile changes
- dependencies change
- browser automation logic changes
- endpoint behavior changes
- hosting platform requires a new revision
- current deployment is broken or stale

## When to update n8n workflow

Update the n8n workflow when:

- report format changes
- Telegram commands change
- config structure changes
- runner payload changes
- runner response format changes
- schedule changes
- new checks are added
