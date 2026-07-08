# Security

See also: [README](README.md) · [CONFIGURATION](CONFIGURATION.md) · [DEPLOY](DEPLOY.md) · [RUNBOOK](RUNBOOK.md)

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

Prefer keeping these values out of the Code node entirely once your instance is set up:

- store runner API keys and the report chat ID as n8n environment variables and read them with
  `$env.HEALTH_RUNNER_API_KEY` / `$env.SPEED_RUNNER_API_KEY` / `$env.REPORT_CHAT_ID` instead of
  literal strings in `Build Report Config` / `Normalize Run`, or
- move the `x-api-key` header into an n8n **Header Auth** credential on the HTTP Request nodes
  instead of building it from a config field.

Either option means a real secret can never end up back in an exported workflow JSON, even if you
fill in the Code node once for your own instance and later re-export the workflow to update the
public template.

## Runner access

Both runners can launch Chromium and request URLs from a payload. Never expose them without authentication.

Production rules:

- keep `ALLOW_PUBLIC_ACCESS=false`
- set runner API keys
- set `ALLOWED_HOSTS`
- use HTTPS endpoints
- keep runner logs free of private payloads

Both runners also disable the FastAPI docs (`/docs`, `/redoc`) and the `/openapi.json` schema by
default (`docs_url=None`, `redoc_url=None`, `openapi_url=None`), since those routes are not gated
by `require_api_key` and would otherwise stay publicly reachable regardless of the API key.

## URL safety

The runners reject non-public/internal URLs and only allow `http` and `https` pages. This protects the service from being used as a browser-based SSRF proxy.

Use `ALLOWED_HOSTS` in production even if URL validation is enabled.

Example:

```env
ALLOWED_HOSTS=example.com,www.example.com
```

### Known limitation: DNS rebinding

The public-IP check resolves the target host once (`socket.getaddrinfo`, cached for the process
lifetime) and then hands the URL to Chromium, which resolves DNS again on its own when it actually
connects. If an attacker controls DNS for the checked host, they could in theory return a public IP
at check time and a private/internal IP at connect time (a classic TOCTOU/DNS-rebinding gap).

This is a known, accepted limitation, not something the allowlist fully closes on its own:

- keep `ALLOWED_HOSTS` restricted to domains you own and trust — do not use either runner as a
  general-purpose "check any URL" proxy for third-party or user-supplied domains
- treat the SSRF checks as defense-in-depth for your own storefront, not as a sandbox safe enough
  to point at arbitrary external sites

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
