# Ecom Tech Speed Checker

A lightweight n8n-based monitoring template for ecommerce websites.

It runs scheduled technical checks, sends Telegram reports, and helps detect issues that regular uptime monitoring often misses: broken product pages, cart problems, failed add-to-cart flows, duplicated cart lines, slow first-screen rendering, and failed speed reports.

The project is designed for ecommerce owners, developers, technical marketers, and small teams who need a practical daily health report without building a full monitoring infrastructure from scratch.

Instead of only checking whether a website is online, this workflow checks whether the key buying path still works:

- product page is loaded correctly
- product gallery is visible
- add-to-cart flow works
- product with accessory/bundle can be added
- cart page initializes correctly
- cart totals and quantities look valid
- duplicate cart lines are not created
- cart item removal works
- speed runner returns a visual performance summary
- Telegram report is sent automatically

The template is platform-agnostic. The runners are simple HTTP services and can be deployed to any Docker-friendly platform such as Yandex Cloud Serverless Containers, Google Cloud Run, AWS App Runner, Azure Container Apps, Render, Railway, Fly.io, or a VPS with Docker.

![Work sample](./docs/images/1.jpg)

## What problem does it solve?

Most uptime monitors only answer one question: “Is the website responding?”

For ecommerce, that is not enough.

A store can be technically online while the actual buying flow is broken. Product pages may load with JavaScript errors, the gallery may disappear, the add-to-cart button may stop working, cart totals may be wrong, bundled products may duplicate lines, or the first screen may become slow enough to hurt conversions.

These issues are often noticed too late — after ads are already running, traffic is coming in, and sales suddenly drop.

This template helps catch such problems earlier by running practical checks against the real user flow and sending a clear Telegram report.

It is useful when you want to know:

- whether the product page still works after theme changes
- whether cart logic still works after custom JavaScript updates
- whether add-to-cart actions behave correctly
- whether product bundles or accessories create duplicate cart lines
- whether cart totals and quantities look valid
- whether basic performance has degraded
- whether scheduled checks completed successfully

The goal is not to replace full observability platforms, but to provide a simple technical safety net for small and medium ecommerce projects.

![Web Speed Test](./docs/images/2.jpg)

## Who is it for?

This template is useful for teams and solo operators who run ecommerce websites and need a simple way to detect technical problems before they affect sales.

It is especially suitable for:

- ecommerce store owners who want a daily technical report without manually checking the site
- developers who maintain custom themes, cart logic, product pages, or JavaScript-heavy storefronts
- technical marketers who run paid traffic and need to know if the buying path is working
- agencies that support multiple ecommerce clients
- small teams that do not need a full observability stack, but still want practical monitoring
- solo founders who want to be notified when something important breaks

The template is not tied to a specific ecommerce platform. It can be adapted for InSales, Shopify, WooCommerce, custom storefronts, or any website where the key user flow can be checked through browser automation and HTTP runners.

## What does it check?

The template is built around two types of checks: health checks and speed checks.

### Health checks

Health checks verify that the main ecommerce flow still works from the user’s point of view.

By default, the workflow can check:

- product page availability
- product gallery visibility
- basic JavaScript sanity checks
- add-to-cart flow
- adding a product with an accessory or bundle
- accessory update without creating duplicate cart lines
- double-click protection for add-to-cart actions
- cart page initialization
- cart line duplication issues
- cart quantity and total validation
- cart motivator or promotional popup behavior
- cart item removal

These checks are useful after theme updates, custom JavaScript changes, ecommerce platform updates, product page edits, or any changes that may affect the buying path.

![Web Health Check](./docs/images/3.jpg)

### Speed checks

Speed checks run a browser-based performance test and return a compact summary.

The speed runner can report:

- first-screen visual loading time
- slow requests
- slowest request timing
- basic competitor or reference-page comparison
- speed status for the Telegram report
- optional PNG summary table

The goal is not to replace Lighthouse or full RUM analytics. The goal is to quickly understand whether the storefront became noticeably slower and whether the issue is visible enough to require attention.

### Telegram report

The workflow sends a Telegram report with:

- overall status
- health check summary
- failed or warning checks
- speed summary
- final result
- optional PNG speed report

This makes the monitoring result easy to review without opening n8n, server logs, analytics tools, or browser devtools.
