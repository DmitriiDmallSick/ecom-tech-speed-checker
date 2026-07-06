# Code review: `health-runner/app.py`

## Review scope

Reviewed file: `health-runner/app.py`.

The runner is a generic FastAPI service for ecommerce health checks. It receives a JSON payload from n8n, opens configured public pages in Playwright Chromium, performs configured browser checks, and returns a compact JSON report for Telegram/n8n.

The uploaded source version was adapted from a project-specific runner. The new template version must stay generic and must not contain project-specific domains, private URLs, Telegram chat IDs, real API keys, or customer data.

## Verdict

The code is acceptable as a public template v1 after Docker smoke testing.

It is not a universal ecommerce testing framework. It is a configurable browser runner intended to catch common storefront breakages:

- key pages do not open
- product page elements disappear
- add-to-cart flow breaks
- cart state cannot be detected
- cart quantity/total/remove behavior breaks
- option/accessory selection has obvious regressions

Current status:

- Ready for code review: yes
- Ready as a public template v1: yes
- Ready for production deployment: yes, after smoke tests and n8n integration update
- Main integration blocker: n8n template must send the health runner API key header if auth is enabled

## Files in scope

- `health-runner/app.py`
- `health-runner/Dockerfile`
- `health-runner/requirements.txt`

## What changed from the uploaded project-specific code

The uploaded code used a project-specific service name and contained assumptions tied to a particular shop implementation. The adapted version changes this into a template runner.

Major template changes:

- service name changed to `ecom-health-runner`
- endpoint kept as `/health-check`
- project-specific service name removed
- API key mechanism added through env
- URL validation added
- domain allowlist added
- private/internal URL protection added
- Playwright route guard added
- timeout limits added
- concurrency limit added
- selectors moved toward payload/env configuration
- screenshots disabled by default
- artifact size limited
- response kept compatible with n8n: `status`, `summary`, `checks`, `errors`, `warnings`, `artifacts`

## Security review

### API key protection

The template must not contain a real key. The key is configured only during deployment:

```env
HEALTH_RUNNER_API_KEY=replace-with-your-own-secret
```

Fallback env supported:

```env
RUNNER_API_KEY=replace-with-your-own-secret
```

Accepted request headers:

```http
x-api-key: replace-with-your-own-secret
```

Alternative supported forms:

```http
x-health-runner-key: replace-with-your-own-secret
Authorization: Bearer replace-with-your-own-secret
```

Expected behavior:

- if `ALLOW_PUBLIC_ACCESS=false` and key is configured, requests without the key return `401`
- if `ALLOW_PUBLIC_ACCESS=false` and key is missing, protected endpoints return `503`
- if `ALLOW_PUBLIC_ACCESS=true`, auth is disabled for local testing only

Production recommendation:

```env
ALLOW_PUBLIC_ACCESS=false
HEALTH_RUNNER_API_KEY=replace-with-your-own-secret
```

### URL validation

The runner opens URLs in a real browser, so it must not accept arbitrary internal addresses.

Allowed URL schemes:

- `http`
- `https`

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

This protects the service from becoming an SSRF/browser proxy.

### Domain allowlist

The runner supports optional domain allowlisting:

```env
ALLOWED_HOSTS=example.com,another-shop.com
```

Expected behavior:

- `example.com` is allowed
- `www.example.com` is allowed
- `sub.example.com` is allowed
- `badexample.com` is not allowed
- domains outside the list are not allowed

Production recommendation:

```env
ALLOWED_HOSTS=your-store-domain.com
```

This should be considered required for real deployments.

### Playwright route guard

The runner applies URL validation not only to the initial page URL but also to requests made by the page.

The browser aborts unsafe requests during page load. This prevents a page, script, redirect, iframe, image, or XHR from forcing the browser to request private/internal addresses.

## Resource and stability review

### Timeout limits

The runner must not blindly trust `timeout_ms` from payload.

Recommended env values:

```env
MIN_TIMEOUT_MS=5000
DEFAULT_TIMEOUT_MS=45000
MAX_TIMEOUT_MS=120000
```

Expected behavior:

- too-small timeout is raised to `MIN_TIMEOUT_MS`
- too-large timeout is reduced to `MAX_TIMEOUT_MS`
- missing timeout uses `DEFAULT_TIMEOUT_MS`

This protects the container from accidental or abusive long-running checks.

### Concurrency limit

Each health check run launches Chromium. Multiple browser runs at the same time can overload small containers.

Recommended env values:

```env
MAX_CONCURRENT_RUNS=1
QUEUE_TIMEOUT_MS=300000
```

Expected behavior:

- one browser run is active at a time by default
- additional requests wait for the semaphore
- if the queue wait limit is exceeded, the service returns `429 Runner is busy`

This is acceptable for an n8n monitoring workflow because reports are not high-frequency API traffic.

### Browser lifecycle

The runner should:

- open Chromium only inside `/health-check`
- close the browser in `finally`
- create a fresh browser context per check
- close context in `finally`
- limit console/page error artifacts

This reduces cross-test state leakage and memory growth.

## Functional review

### Payload model

The runner accepts a generic payload with these main sections:

```json
{
  "report_id": "...",
  "project": {},
  "pages": {},
  "options": {},
  "selectors": {},
  "product": {},
  "expectations": {},
  "health_tests": {},
  "checks": {}
}
```

Expected minimum useful payload:

```json
{
  "pages": {
    "home": { "url": "https://example.com/" },
    "product": { "url": "https://example.com/products/example" },
    "cart": { "url": "https://example.com/cart" }
  },
  "options": {
    "timeout_ms": 45000,
    "checks": ["home_page_open", "product_page_open", "cart_page_open"]
  }
}
```

### Check discovery

The runner supports two ways to decide which checks to run:

1. explicit list in `options.checks` or `checks.ids`
2. legacy-style `health_tests.product_page` and `health_tests.cart_page`

If no check list is provided, the runner uses a default check set.

Review note: the default set is intentionally broad. It is useful for ecommerce templates but can produce warnings/failures on shops that do not expose cart state or matching selectors. Real integrations should configure an explicit check list.

### Supported checks

Default check IDs:

- `home_page_open`
- `product_page_open`
- `cart_page_open`
- `product_gallery_visible`
- `product_common_sanity`
- `add_product_to_cart`
- `add_product_with_accessory`
- `update_accessory_without_quantity_change`
- `double_click_add_guard`
- `cart_sanity`
- `cart_no_duplicate_lines`
- `cart_qty_and_total`
- `cart_motivator_popup`
- `cart_remove_line`

### Page open checks

These checks are generic and reliable:

- `home_page_open`
- `product_page_open`
- `cart_page_open`

They verify that the page URL exists, is allowed, opens in Chromium, and returns a response under 400.

These are the safest checks for a first integration.

### Product gallery check

`product_gallery_visible` checks that the configured gallery selector exists and is visible.

Default selector fallback:

```css
.js-product-gallery-main,
[data-product-gallery],
.product-gallery,
.product__gallery
```

Review note: this is generic but still selector-dependent. Real integrations should provide a selector in payload:

```json
{
  "selectors": {
    "product_gallery": ".your-gallery-selector"
  }
}
```

### Add-to-cart checks

The runner tries to detect cart state through:

1. `window.Cart.order.get()` if available
2. configured cart count selector
3. configured cart item selector
4. configured cart total selector

This keeps the runner usable across different ecommerce platforms.

Review note: full add-to-cart validation is only strong when the site exposes a measurable cart state. If the runner can click the button but cannot measure quantity, the check should return `warning`, not a false `ok`.

### Accessory/option checks

The original code was specific to accessory groups. The template keeps the idea but makes it selector-driven.

Relevant selector keys:

- `accessory_option`
- `accessory_update_button`

Review note: for generic stores, these checks are optional. They should be enabled only when the integration provides selectors or the site uses one of the fallback patterns.

### Cart checks

Cart checks are useful but platform-sensitive.

Strong checks:

- `cart_page_open`
- `cart_qty_and_total` when cart state is measurable
- `cart_remove_line` when remove selector is configured

Weaker/generic checks:

- `cart_sanity`
- `cart_no_duplicate_lines` if cart line details are not available
- `cart_motivator_popup` unless selectors are provided

Review note: `cart_motivator_popup` should usually be disabled in generic installs unless the user explicitly configures motivator selectors.

## API behavior

### `/health-check`

Expected successful response shape:

```json
{
  "compact_version": "v1",
  "status": "ok",
  "skipped": false,
  "summary": {
    "total": 3,
    "passed": 3,
    "warnings": 0,
    "failed": 0
  },
  "checks": [],
  "errors": [],
  "warnings": [],
  "artifacts": {}
}
```

Expected status values:

- `ok`
- `warning`
- `failed`

Check-level status values:

- `ok`
- `warning`
- `failed`
- `error`

## n8n integration review

Important: the health runner is protected separately from the speed runner.

The n8n template must send the key header when calling the health runner:

```http
x-api-key: {{$json.runners.health_api_key}}
```

Recommended n8n config values:

```js
health_runner_url: ''
health_runner_api_key: ''
```

Recommended runner env values:

```env
HEALTH_RUNNER_API_KEY=replace-with-your-own-secret
ALLOWED_HOSTS=your-store-domain.com
ALLOW_PUBLIC_ACCESS=false
```

Current integration note: if the n8n template only has `health_runner_url` and does not send a key header, the protected health runner will return `401` or `503`. The workflow template must be updated before production use.

## Smoke test checklist

Before calling the runner production-ready, run these checks in Docker.

### Build

```bash
docker build -t ecom-health-runner ./health-runner
```

### Run locally

```bash
docker run --rm -p 8080:8080 \
  -e HEALTH_RUNNER_API_KEY=test-secret \
  -e ALLOWED_HOSTS=example.com \
  ecom-health-runner
```

### Health

```bash
curl http://localhost:8080/health
```

Expected: `{"ok": true, ...}`.

### Unauthorized request

```bash
curl -X POST http://localhost:8080/health-check \
  -H 'Content-Type: application/json' \
  -d '{"pages":{"home":{"url":"https://example.com/"}},"options":{"checks":["home_page_open"]}}'
```

Expected: `401`.

### Authorized basic page check

```bash
curl -X POST http://localhost:8080/health-check \
  -H 'Content-Type: application/json' \
  -H 'x-api-key: test-secret' \
  -d '{"pages":{"home":{"url":"https://example.com/"}},"options":{"checks":["home_page_open"]}}'
```

Expected: `status`, `summary`, and one check result.

### Block localhost

```bash
curl -X POST http://localhost:8080/health-check \
  -H 'Content-Type: application/json' \
  -H 'x-api-key: test-secret' \
  -d '{"pages":{"home":{"url":"http://127.0.0.1:8080/"}},"options":{"checks":["home_page_open"]}}'
```

Expected: failed check with URL validation error, not a successful browser request.

### Product/cart smoke test

Use a real test product and cart URL from the target shop:

```bash
curl -X POST http://localhost:8080/health-check \
  -H 'Content-Type: application/json' \
  -H 'x-api-key: test-secret' \
  -d '{
    "pages": {
      "product": { "url": "https://example.com/products/test-product" },
      "cart": { "url": "https://example.com/cart" }
    },
    "selectors": {
      "add_to_cart_button": "button[type=submit]",
      "cart_item": ".cart-item",
      "cart_remove_button": ".cart-item [data-remove]"
    },
    "options": {
      "checks": ["product_page_open", "add_product_to_cart", "cart_qty_and_total"]
    }
  }'
```

Expected: checks either pass or return clear selector/cart-state warnings.

## Review findings

### Finding 1: n8n template must be updated for health API key

Severity: High for integration, not a runner-code blocker.

The runner now has proper auth. The workflow must pass the key to `/health-check`.

Required n8n change:

- add `health_runner_api_key` to config
- expose it as `runners.health_api_key`
- add `x-api-key` header to `Call Health Runner`

### Finding 2: default check list is aggressive

Severity: Medium.

If the payload does not specify checks, the runner attempts the full ecommerce suite. This is useful for a prepared template but can be noisy on stores without matching selectors.

Recommendation:

- use explicit `options.checks` for first setup
- document recommended starter checks:
  - `home_page_open`
  - `product_page_open`
  - `cart_page_open`

### Finding 3: `clear_cart_before_test` is accepted by n8n but not fully implemented

Severity: Medium.

The runner creates a fresh browser context per check, which usually gives a clean session. However, the `clear_cart_before_test` option is not explicitly implemented as a cart-clearing action.

Recommendation for v2:

- implement platform-specific or selector-based cart clearing
- or remove this option from the template until implemented

### Finding 4: add-to-cart checks depend on measurable cart state

Severity: Medium.

The runner can click a button generically, but proving that cart quantity changed requires either `window.Cart.order.get()` or configured selectors.

Recommendation:

- document required selectors for non-InSales/non-Cart-object stores
- return `warning` instead of `failed` when quantity cannot be measured

### Finding 5: image/media/font blocking may affect some themes

Severity: Low/Medium.

Blocking heavy resources speeds up health checks, but a few storefront themes may depend on images/fonts/media for layout or visibility.

Recommendation:

Allow disabling resource blocking through payload:

```json
{
  "options": {
    "block_resource_types": []
  }
}
```

This is already supported by the current implementation.

### Finding 6: screenshots are disabled by default

Severity: Low.

This is a good default for a public template because base64 screenshots can make responses very large.

Recommendation:

Enable only for debugging:

```json
{
  "options": {
    "include_screenshots": true
  }
}
```

## Final recommendation

Approve `health-runner/app.py` as template v1 after Docker smoke tests.

Required before real deployment:

- set `HEALTH_RUNNER_API_KEY`
- set `ALLOWED_HOSTS`
- keep `ALLOW_PUBLIC_ACCESS=false`
- update n8n to send `x-api-key` to health runner
- configure explicit starter checks
- run smoke tests

Recommended starter checks for first production run:

```json
{
  "options": {
    "checks": [
      "home_page_open",
      "product_page_open",
      "cart_page_open"
    ]
  }
}
```

Recommended v2 improvements:

- explicit cart clearing
- better platform presets
- more readable per-check logs
- optional HTML screenshot artifacts stored externally instead of inline base64
- retry policy for flaky checks
- separate strict mode vs soft mode
