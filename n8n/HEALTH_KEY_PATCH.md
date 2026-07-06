# n8n health runner key patch

The current public n8n template must be updated before using the protected health runner in production.

## Required changes

### 1. Add config value

In the `Build Report Config` node, add:

```js
health_runner_api_key: '',
```

near:

```js
health_runner_url: '',
speed_json_url: '',
speed_image_url: '',
speed_runner_api_key: '',
```

### 2. Expose it in `runners`

In the same node, change:

```js
runners: {
  health_url: CONFIG.health_runner_url,
  speed_json_url: CONFIG.speed_json_url,
  speed_image_url: CONFIG.speed_image_url,
  speed_api_key: CONFIG.speed_runner_api_key
}
```

to:

```js
runners: {
  health_url: CONFIG.health_runner_url,
  health_api_key: CONFIG.health_runner_api_key,
  speed_json_url: CONFIG.speed_json_url,
  speed_image_url: CONFIG.speed_image_url,
  speed_api_key: CONFIG.speed_runner_api_key
}
```

### 3. Add header to `Call Health Runner`

In the `Call Health Runner` HTTP Request node, enable headers and add:

```text
x-api-key: {{ $json.runners.health_api_key }}
```

The node should still send:

```js
url: ={{ $json.runners.health_url }}
body: ={{ $json.health_runner_payload }}
```

### 4. Recommended starter health checks

For the first production setup, start with a minimal check list:

```js
health_checks: [
  'home_page_open',
  'product_page_open',
  'cart_page_open'
]
```

Then expand to cart/add-to-cart checks after selectors are configured.

## Runner env

The key in n8n must match:

```env
HEALTH_RUNNER_API_KEY=replace-with-your-own-secret
```

Do not commit the real key to the public repo.
