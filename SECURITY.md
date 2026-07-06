# Security

This project is a public template. Do not commit real credentials, private URLs, Telegram chat IDs, product IDs, customer data, order data, screenshots, or logs.

## Secrets

Runner API keys must be configured only in the private deployment environment and in the private n8n workflow after import.

Recommended runner env values:

```env
ALLOW_PUBLIC_ACCESS=false
HEALTH_RUNNER_API_KEY=replace-with-your-own-secret
SPEED_RUNNER_API_KEY=replace-with-your-own-secret
ALLOWED_HOSTS=your-store-domain.com
```

Recommended n8n config values in the `Build Report Config` node:

```js
health_runner_api_key: ''
speed_runner_api_key: ''
```

The empty values in the public template are placeholders. Fill them only in your private n8n instance.

## Runner access

Both runners can launch Chromium and request URLs from a payload. Never expose them without authentication.

Production rules:

- keep `ALLOW_PUBLIC_ACCESS=false`
- set runner API keys
- set `ALLOWED_HOSTS`
- use HTTPS endpoints
- keep runner logs free of private payloads

## URL safety

The runners reject non-public/internal URLs and only allow `http` and `https` pages. This protects the service from being used as a browser-based SSRF proxy.

Use `ALLOWED_HOSTS` in production even if URL validation is enabled.

Example:

```env
ALLOWED_HOSTS=example.com,www.example.com
```

## Public template checklist

Before publishing or sharing a workflow export, check that it does not contain:

- Telegram bot tokens
- Telegram chat IDs
- n8n credentials
- runner API keys
- private runner URLs
- private store URLs if you do not want to publish them
- internal product IDs
- customer data
- order data
- screenshots with private information
- execution logs with private payloads

## Local testing

For local-only testing you may temporarily use:

```env
ALLOW_PUBLIC_ACCESS=true
```

Do not use this in production or on a public URL.
