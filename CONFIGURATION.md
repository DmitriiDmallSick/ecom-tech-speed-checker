# Configuration

See also: [README](README.md) · [DEPLOY](DEPLOY.md) · [RUNBOOK](RUNBOOK.md) · [SECURITY](SECURITY.md)

This guide explains how to configure the n8n workflow after importing the template.

The workflow is designed so that most project-specific settings are stored in one place:

```text
Build Report Config
```

The only exception is the scheduled Telegram chat ID, which is configured in the `Normalize Run` node.

After importing the workflow, you need to configure:

- Telegram credentials
- scheduled report chat ID
- project base URL
- product and cart paths
- health runner URL
- speed runner JSON URL
- speed runner PNG URL
- storefront selectors
- cart and accessory expectations

The template is intentionally shipped with empty values.

## 1. Import the workflow

Import the workflow template into your n8n instance:

```text
n8n/tech-checker-template.json
```

After import, keep the workflow inactive until all required settings are configured.

Do not enable the schedule immediately.

## 2. Connect Telegram credentials

The workflow uses Telegram for two things:

- receiving manual commands
- sending reports

Open every Telegram node and connect your own Telegram credentials.

Telegram nodes in the template:

```text
Telegram Trigger
Send status message
Delete a chat message
Send a text message
Send PNG
```

The template does not include Telegram tokens or credentials.

If n8n shows an existing Telegram credential after import, check that it belongs to your own n8n instance and that you want to use it.

For public templates, credentials should not be stored in the workflow JSON.

## 3. Configure scheduled report chat

Manual Telegram commands automatically use the chat ID from the incoming message.

Scheduled reports do not have an incoming Telegram message, so you need to set the report chat manually.

Open the `Normalize Run` node and find:

```js
const REPORT_CHAT_ID = 'TELEGRAM_CHAT_ID_HERE';
```

Replace it with your real Telegram chat ID:

```js
const REPORT_CHAT_ID = '-1001234567890';
```

Use your own chat ID or group ID.

Where possible, prefer an n8n environment variable (`$env.REPORT_CHAT_ID`) over a literal value in the Code node — see [SECURITY.md](SECURITY.md#secrets) for why this matters if you re-export the workflow later.

## 4. Configure `Build Report Config`

Most project-specific settings are stored in one node:

```text
Build Report Config
```

Open this node and fill in the `CONFIG` object.

The template is shipped with empty values on purpose. This prevents real project URLs, product IDs, runner URLs, and private settings from being committed to a public repository.

### Main project settings

```js
const CONFIG = {
  project_slug: '',
  project_name: '',
  base_url: '',

  product_path: '',
  cart_path: '',

  health_runner_url: '',
  health_runner_api_key: '',
  speed_json_url: '',
  speed_image_url: '',
  speed_runner_api_key: ''
};
```

| Field | Description | Example |
|---|---|---|
| `project_slug` | Short machine-readable project name used in report IDs | `my-store` |
| `project_name` | Human-readable project name shown in Telegram reports | `My Store` |
| `base_url` | Main website URL without trailing slash | `https://store.example.com` |
| `product_path` | Path to a test product page | `/products/test-product` |
| `cart_path` | Path to the cart page | `/cart_items` |
| `health_runner_url` | Public HTTP endpoint for the health runner | `https://your-health-runner.example.com/health-check` |
| `health_runner_api_key` | Value sent as the `x-api-key` header to the health runner; must match `HEALTH_RUNNER_API_KEY` on that runner | see [DEPLOY.md](DEPLOY.md) |
| `speed_json_url` | Public HTTP endpoint for speed JSON report | `https://your-speed-runner.example.com/speed-report-json` |
| `speed_image_url` | Public HTTP endpoint for speed PNG report | `https://your-speed-runner.example.com/speed-report-image` |
| `speed_runner_api_key` | Value sent as the `x-api-key` header to the speed runner; must match `SPEED_RUNNER_API_KEY` on that runner | see [DEPLOY.md](DEPLOY.md) |

As with the chat ID above, prefer reading these two keys from n8n environment variables
(`$env.HEALTH_RUNNER_API_KEY`, `$env.SPEED_RUNNER_API_KEY`) instead of pasting the real secret into
the Code node — see [SECURITY.md](SECURITY.md#secrets).

The workflow builds full page URLs automatically from:

```text
base_url + product_path
base_url + cart_path
```

For example:

```js
base_url: 'https://store.example.com',
product_path: '/products/test-product',
cart_path: '/cart_items'
```

will produce:

```text
https://store.example.com/products/test-product
https://store.example.com/cart_items
```

### Timeouts

```js
health_timeout_ms: 45000,
speed_timeout_ms: 60000,
```

| Field | Description |
|---|---|
| `health_timeout_ms` | Timeout for health checks |
| `speed_timeout_ms` | Timeout for speed checks |

Browser-based checks can take longer than simple HTTP requests, especially on cold starts or serverless platforms.

Recommended starting values:

```js
health_timeout_ms: 45000,
speed_timeout_ms: 60000,
```

If your runners often fail because of timeout errors, increase these values and also check timeout limits on your hosting platform.

### Selectors

```js
selectors: {
  product_gallery: ''
}
```

| Field | Description |
|---|---|
| `product_gallery` | CSS selector used to check whether the product gallery is visible |

Example:

```js
selectors: {
  product_gallery: '.product-gallery'
}
```

Use a selector that is stable and visible on the product page.

Avoid selectors that depend on temporary layout, animation state, A/B tests, or dynamic IDs.

### Product settings

```js
product: {
  expected_title_contains: '',
  expected_quantity_after_update: 1,
  accessory_variants: []
}
```

| Field | Description |
|---|---|
| `expected_title_contains` | Optional text expected in the product title |
| `expected_quantity_after_update` | Expected cart quantity after accessory or cart update checks |
| `accessory_variants` | Optional list of accessory/bundle variants used by the health runner |

These fields depend on your storefront and runner implementation.

For a simple setup, you can start with:

```js
product: {
  expected_title_contains: '',
  expected_quantity_after_update: 1,
  accessory_variants: []
}
```

### Expectations

```js
expectations: {
  base_accessory_id: '',
  expected_paid_accessory_example: '',
  expected_add_delta: 1,
  expected_accessory_add_delta: 1,
  expected_update_delta: 0,
  expected_double_click_delta: 1,
  expected_quantity_after_update: 1,
  motivator_max_discount_total: 30000
}
```

| Field | Description |
|---|---|
| `base_accessory_id` | Default/base accessory ID, if your storefront uses accessory variants |
| `expected_paid_accessory_example` | Example paid accessory ID used to verify accessory replacement logic |
| `expected_add_delta` | Expected cart quantity change after normal add-to-cart |
| `expected_accessory_add_delta` | Expected cart quantity change after adding product with accessory |
| `expected_update_delta` | Expected quantity change after accessory update |
| `expected_double_click_delta` | Expected cart quantity change after double-click add-to-cart stress test |
| `expected_quantity_after_update` | Expected final cart quantity after update |
| `motivator_max_discount_total` | Cart total threshold used by motivator/promo popup checks |

Default values are designed for a common ecommerce flow where:

- adding a product increases quantity by `1`
- adding a product with accessory still creates one cart line
- changing accessory does not increase quantity
- double-clicking add-to-cart should not create duplicate quantity
- cart quantity after update should remain `1`

If your cart logic works differently, adjust these expectations.

### Minimal required configuration

At minimum, configure:

```text
project_slug
project_name
base_url
product_path
cart_path
health_runner_url
health_runner_api_key
speed_json_url
speed_image_url
speed_runner_api_key
selectors.product_gallery
```

Then run manual checks:

```text
/speed
/health
/report
```

Do not enable scheduled reports until manual `/report` works correctly.

## 5. Configure runner HTTP nodes

The workflow calls external HTTP runners from n8n.

These nodes should not contain hardcoded project URLs. They should use values prepared in the `Build Report Config` node.

### Health runner request

Node:

```text
Call Health Runner
```

Expected URL expression:

```js
={{ $json.runners.health_url }}
```

Expected JSON body:

```js
={{ $json.health_runner_payload }}
```

Recommended timeout:

```text
300000
```

The health runner should return a JSON response with health check results.

### Speed runner JSON request

Node:

```text
Call Speed Runner
```

Expected URL expression:

```js
={{ $json.runners.speed_json_url }}
```

Expected JSON body:

```js
={{ $json.speed_runner_payload }}
```

Recommended response format:

```text
Text
```

Recommended timeout:

```text
300000
```

The response is parsed later by the `Normalize Speed Result` node.

### Speed runner PNG request

Node:

```text
Call Speed PNG
```

Expected URL expression:

```js
={{ $json.runners.speed_image_url }}
```

Expected JSON body:

```js
={{ $json.speed_runner_payload }}
```

Expected response format:

```text
File
```

Expected binary property:

```text
data
```

Recommended timeout:

```text
300000
```

The returned binary file is sent to Telegram by the `Send PNG` node.

## 6. Check Telegram node chat IDs

Telegram nodes should not contain real hardcoded chat IDs in the template.

After import, use expressions where possible.

### Send status message

This node runs before the final report and can use the current item data:

```js
={{ $json.chat_id }}
```

### Delete a chat message

This node should use the chat ID from the final report data:

```js
={{ $('Build Final Text Report').first().json.telegram.chat_id }}
```

Message ID expression:

```js
={{ $('Send status message').first().json.message_id || $('Send status message').first().json.result?.message_id }}
```

### Send a text message

Use:

```js
={{ $('Build Final Text Report').first().json.telegram.chat_id }}
```

Text:

```js
={{ $('Build Final Text Report').first().json.telegram_message }}
```

Parse mode:

```text
HTML
```

### Send PNG

Use:

```js
={{ $('Build Final Text Report').first().json.telegram.chat_id }}
```

Binary data should be enabled.

Binary property:

```text
data
```

## 7. Test manually

Before enabling the schedule, test the workflow manually from Telegram.

Recommended order:

```text
/help
/speed
/health
/report
```

Start with:

```text
/help
```

This checks whether Telegram Trigger and Telegram credentials work.

Then run:

```text
/speed
```

This checks whether the speed runner JSON endpoint and PNG endpoint work.

Then run:

```text
/health
```

This checks whether the health runner can open the configured product and cart pages.

Finally run:

```text
/report
```

This checks the full workflow path.

## 8. Enable schedule

Enable the workflow schedule only after manual `/report` works correctly.

Check the `Schedule Trigger` node and adjust the schedule for your project.

The default template can run more than once per day, but you should choose a schedule that matches your monitoring needs and runner costs.

For example:

```text
morning check before traffic starts
evening check after major site updates or business hours
```

## Common mistakes

### Real credentials accidentally exported

If you export the workflow after reconnecting credentials in your own n8n instance, n8n may include credential references again.

Before committing the workflow JSON, search for:

```text
credentials
telegramApi
```

The public template should not contain real credential references.

### Hardcoded runner URLs

Before publishing, search the workflow JSON for real runner domains.

Runner URLs should be configured only inside:

```text
Build Report Config
```

HTTP nodes should use expressions:

```js
={{ $json.runners.health_url }}
={{ $json.runners.speed_json_url }}
={{ $json.runners.speed_image_url }}
```

### Hardcoded runner API keys

`health_runner_api_key` and `speed_runner_api_key` in `Build Report Config` end up in the exported
JSON as plain text if you fill them in directly, the same way a hardcoded URL or chat ID would.
Prefer `$env.HEALTH_RUNNER_API_KEY` / `$env.SPEED_RUNNER_API_KEY`, or move the `x-api-key` header
into an n8n Header Auth credential on `Call Health Runner`, `Call Speed Runner`, and `Call Speed PNG`
instead. See [SECURITY.md](SECURITY.md#secrets).

### Hardcoded Telegram chat IDs

Do not commit real Telegram chat IDs to a public repository.

Search for:

```text
-100
TELEGRAM_CHAT_ID_HERE
```

The placeholder is fine. Real chat IDs should stay only in your private n8n instance.

### Empty selector

If `selectors.product_gallery` is empty or incorrect, the product gallery check will fail.

Use a stable CSS selector that exists on the configured product page.

### Wrong product or cart path

Make sure `product_path` and `cart_path` are paths, not full URLs.

Correct:

```js
base_url: 'https://store.example.com',
product_path: '/products/test-product',
cart_path: '/cart_items'
```

Incorrect:

```js
product_path: 'https://store.example.com/products/test-product'
```

### Runner timeout

Browser-based checks may fail on cold starts or slow serverless platforms.

If this happens, increase:

```js
health_timeout_ms
speed_timeout_ms
```

and also check the timeout settings of your hosting platform and HTTP Request nodes in n8n.

