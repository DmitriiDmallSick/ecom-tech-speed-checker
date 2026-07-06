# Code review: `speed-runner/app.py`

## Review scope

Reviewed file: `speed-runner/app.py`.

The runner is a generic FastAPI service for ecommerce speed checks. It accepts a JSON payload from n8n, opens configured public page URLs in Playwright Chromium, measures first-screen visual stability and request timing, and returns either JSON or a PNG report.

This review describes the expected final implementation for the template version of the speed runner. The file must stay generic and must not contain project-specific domains, private URLs, Telegram chat IDs, real API keys, or customer data.

## Verdict

The code is suitable as a public template v1 after smoke testing in Docker.

It is not intended to be a full enterprise monitoring service. It is a lightweight runner that can be deployed as a separate protected HTTP service and called by n8n.

Current status:

- Ready for code review: yes
- Ready as a public template: yes
- Ready for production deployment: yes, after Docker smoke tests and env configuration
- Main remaining v2 improvement: avoid double measurement for JSON and PNG

## What the runner does

The service exposes:

- `GET /`
- `GET /health`
- `POST /speed-report-json`
- `POST /speed-report-image`

The JSON endpoint returns structured speed data for Telegram/text reports.

The image endpoint returns a PNG summary table for Telegram/photo reports.

The runner supports up to four measured pages per request: the main page plus reference pages or competitors if provided in the payload.

## Security review

### API key protection

The template must not contain a real key. The key is configured only during deployment through an environment variable:

```env
SPEED_RUNNER_API_KEY=replace-with-your-own-secret
```

Accepted request headers:

```http
x-api-key: replace-with-your-own-secret
```

Alternative supported forms:

```http
x-speed-runner-key: replace-with-your-own-secret
Authorization: Bearer replace-with-your-own-secret
```

Expected behavior:

- If `ALLOW_PUBLIC_ACCESS=false` and `SPEED_RUNNER_API_KEY` is set, requests without the key return `401`.
- If `ALLOW_PUBLIC_ACCESS=false` and `SPEED_RUNNER_API_KEY` is missing, protected endpoints return `503` instead of silently becoming public.
- If `ALLOW_PUBLIC_ACCESS=true`, auth is disabled for local testing only.

Production recommendation:

```env
ALLOW_PUBLIC_ACCESS=false
SPEED_RUNNER_API_KEY=replace-with-your-own-secret
```

### URL validation

The runner should only accept `http` and `https` URLs.

Blocked URL classes:

- `localhost`
- `.localhost`
- `127.0.0.0/8`
- `0.0.0.0`
- private IPv4 ranges
- loopback IPs
- link-local IPs
- reserved IPs
- multicast IPs
- unspecified IPs
- metadata/internal hostnames
- non-HTTP schemes such as `file:`, `ftp:`, `data:`

This is required because the runner opens URLs inside a browser. Without validation, it could become an SSRF tool.

### Domain allowlist

The template supports optional domain allowlisting:

```env
ALLOWED_HOSTS=example.com,another-shop.com
```

Expected behavior:

- `example.com` is allowed.
- `www.example.com` is allowed.
- `sub.example.com` is allowed.
- `badexample.com` is not allowed.
- domains outside the list are not allowed.

Production recommendation:

```env
ALLOWED_HOSTS=your-store-domain.com
```

This is strongly recommended for real deployments.

### Playwright route guard

The security check must apply not only to the initial page URL, but also to internal page requests made by the browser.

The runner should abort unsafe requests during page load. This prevents a public page, redirect, script, image, iframe, or XHR from forcing the browser to request private/internal addresses.

## Resource and stability review

### Timeout limits

The runner must not blindly trust `timeout_ms` from payload.

Recommended env values:

```env
MIN_TIMEOUT_MS=5000
DEFAULT_TIMEOUT_MS=60000
MAX_TIMEOUT_MS=120000
```

Expected behavior:

- Too-small timeout is raised to `MIN_TIMEOUT_MS`.
- Too-large timeout is reduced to `MAX_TIMEOUT_MS`.
- Missing timeout uses `DEFAULT_TIMEOUT_MS`.

This protects the container from accidental or abusive long-running requests.

### Concurrency limit

Each speed check launches Chromium. Running several browser checks at the same time can overload a small container.

Recommended env value:

```env
MAX_CONCURRENT_RUNS=1
QUEUE_TIMEOUT_MS=300000
```

Expected behavior:

- One browser run is active at a time by default.
- Additional requests wait for the semaphore.
- If the queue wait limit is exceeded, the service returns `429 Runner is busy`.

This is acceptable for an n8n-triggered monitoring workflow because reports are not high-frequency API traffic.

### Browser lifecycle

The runner should:

- open Chromium only inside a speed run
- close the browser in `finally`
- close page/context in `finally`
- cancel pending async request tasks during cleanup

This avoids leaked browser processes and memory growth.

## Measurement review

### Visual readiness

The runner estimates visual readiness by taking viewport screenshots at intervals and comparing each frame to the final frame.

This is useful for ecommerce monitoring because browser `load` time alone often does not reflect when the first screen actually appears stable.

Limitations:

- It is an approximation, not Lighthouse.
- Animated widgets may make the page appear unstable for longer.
- Cookie banners, popups, or lazy widgets can affect the result.

### Request timing

The runner collects request duration, status, source group, slow requests, image count, and image max time.

Useful metrics:

- `visual_ready_sec`
- `dom_sec`
- `onload_sec`
- `max_request_sec`
- `slow_3`
- `slow_10`
- `errors_4xx`
- `errors_5xx`
- `origin_requests`
- `cdn_requests`
- `third_party_requests`
- `image_count`
- `image_max_sec`
- `top_slow`

Known limitation:

Requests longer than the current countable threshold may be excluded from some request timing summaries. This is acceptable for v1 but can be improved in v2 by counting long requests separately instead of dropping them.

## API behavior

### `/speed-report-json`

Expected successful response:

```json
{
  "ok": true,
  "status": "ok",
  "status_title": "✅ ok",
  "telegramMessage": "...",
  "primary": {},
  "references": {},
  "results": [],
  "errors": []
}
```

Expected invalid URL response:

```json
{
  "ok": false,
  "status": "error",
  "status_title": "❌ error",
  "telegramMessage": "❌ <b>Speed: error</b>\n\nNo valid public page URL was provided.",
  "primary": {},
  "references": {},
  "results": [],
  "errors": ["No valid public page URL was provided"]
}
```

### `/speed-report-image`

Expected behavior:

- returns `image/png`
- renders a table when there are valid results
- renders an error card when payload contains no valid public URL

## n8n integration notes

The n8n workflow should call both speed endpoints with the same API key header:

```http
x-api-key: {{$json.runners.speed_api_key}}
```

The key in the n8n template should be a placeholder only. Real deployments should store the key in n8n credentials, environment variables, or a private config section, not in a public repository.

Required n8n config values:

```js
speed_json_url: ''
speed_image_url: ''
speed_runner_api_key: ''
```

Required runner env values:

```env
SPEED_RUNNER_API_KEY=replace-with-your-own-secret
ALLOWED_HOSTS=your-store-domain.com
ALLOW_PUBLIC_ACCESS=false
```

## Smoke test checklist

Before calling the runner production-ready, run these checks in Docker.

### Build

```bash
docker build -t ecom-speed-runner ./speed-runner
```

### Run locally

```bash
docker run --rm -p 8080:8080 \
  -e SPEED_RUNNER_API_KEY=test-secret \
  -e ALLOWED_HOSTS=example.com \
  ecom-speed-runner
```

### Health

```bash
curl http://localhost:8080/health
```

Expected: `{"ok": true, ...}`.

### Unauthorized request

```bash
curl -X POST http://localhost:8080/speed-report-json \
  -H 'Content-Type: application/json' \
  -d '{"pages":{"home":{"url":"https://example.com/"}}}'
```

Expected: `401`.

### Authorized request

```bash
curl -X POST http://localhost:8080/speed-report-json \
  -H 'Content-Type: application/json' \
  -H 'x-api-key: test-secret' \
  -d '{"pages":{"home":{"url":"https://example.com/"}}}'
```

Expected: structured JSON with `status`, `primary`, and `results`.

### Block localhost

```bash
curl -X POST http://localhost:8080/speed-report-json \
  -H 'Content-Type: application/json' \
  -H 'x-api-key: test-secret' \
  -d '{"pages":{"home":{"url":"http://127.0.0.1:8080/"}}}'
```

Expected: no valid public URL error.

### PNG response

```bash
curl -X POST http://localhost:8080/speed-report-image \
  -H 'Content-Type: application/json' \
  -H 'x-api-key: test-secret' \
  -d '{"pages":{"home":{"url":"https://example.com/"}}}' \
  --output speed-report.png
```

Expected: valid PNG file.

## Remaining limitations

### Double measurement for JSON and PNG

At v1 level, `/speed-report-json` and `/speed-report-image` each run their own measurement.

Impact:

- extra Chromium launch
- higher runtime cost
- JSON and PNG can differ slightly

This is not a security issue. It is a v2 optimization.

Possible v2 solution:

- `/speed-report-json` stores results by `report_id`
- `/speed-report-image` reuses stored results
- short TTL cache, for example 10-30 minutes

### DNS cache and DNS rebinding

The runner caches host DNS checks for performance. This is acceptable when `ALLOWED_HOSTS` is configured.

For stricter environments, combine this with platform-level network restrictions.

### Not a Lighthouse replacement

The runner measures practical first-screen stability and network timing. It does not replace Lighthouse, PageSpeed Insights, or WebPageTest.

## Final recommendation

Approve as template v1 after Docker smoke tests.

Required before real deployment:

- set `SPEED_RUNNER_API_KEY`
- set `ALLOWED_HOSTS`
- keep `ALLOW_PUBLIC_ACCESS=false`
- add `x-api-key` header in n8n
- run smoke tests

Recommended for v2:

- result cache by `report_id`
- avoid double measurement for JSON and PNG
- more detailed image weight metrics
- structured logs
- optional per-domain network policy
