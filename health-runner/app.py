import asyncio
import base64
import hmac
import ipaddress
import os
import socket
import traceback
from functools import lru_cache
from typing import Any, Dict, List, Optional
from urllib.parse import urlparse

from fastapi import FastAPI, HTTPException, Request
from pydantic import BaseModel, Field
from playwright.async_api import async_playwright


def env_int(name: str, default: int) -> int:
    try:
        return int(os.getenv(name, str(default)))
    except Exception:
        return default


def clamp_int(value: Any, default: int, minimum: int, maximum: int) -> int:
    try:
        number = int(value)
    except Exception:
        number = default
    return max(minimum, min(maximum, number))


app = FastAPI(title="Ecommerce Health Runner")

MIN_TIMEOUT_MS = max(1000, env_int("MIN_TIMEOUT_MS", 5000))
MAX_TIMEOUT_MS = max(MIN_TIMEOUT_MS, env_int("MAX_TIMEOUT_MS", 120000))
DEFAULT_TIMEOUT_MS = clamp_int(env_int("DEFAULT_TIMEOUT_MS", 45000), 45000, MIN_TIMEOUT_MS, MAX_TIMEOUT_MS)
VIEWPORT_WIDTH = max(320, env_int("VIEWPORT_WIDTH", 1440))
VIEWPORT_HEIGHT = max(320, env_int("VIEWPORT_HEIGHT", 1200))
MAX_CONCURRENT_RUNS = max(1, env_int("MAX_CONCURRENT_RUNS", 1))
QUEUE_TIMEOUT_MS = max(1000, env_int("QUEUE_TIMEOUT_MS", 300000))
RUN_SEMAPHORE = asyncio.Semaphore(MAX_CONCURRENT_RUNS)

HEALTH_RUNNER_API_KEY = (os.getenv("HEALTH_RUNNER_API_KEY") or os.getenv("RUNNER_API_KEY") or "").strip()
ALLOW_PUBLIC_ACCESS = os.getenv("ALLOW_PUBLIC_ACCESS", "false").strip().lower() in {"1", "true", "yes", "y"}
ALLOWED_HOSTS = [item.strip().lower().lstrip(".") for item in os.getenv("ALLOWED_HOSTS", "").split(",") if item.strip()]
BLOCKED_HOSTNAMES = {"localhost", "metadata", "metadata.google.internal", "host.docker.internal", "kubernetes.default"}
USER_AGENT = os.getenv(
    "USER_AGENT",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
)

DEFAULT_CHECK_IDS = [
    "home_page_open",
    "product_page_open",
    "cart_page_open",
    "product_gallery_visible",
    "product_common_sanity",
    "add_product_to_cart",
    "add_product_with_accessory",
    "update_accessory_without_quantity_change",
    "double_click_add_guard",
    "cart_sanity",
    "cart_no_duplicate_lines",
    "cart_qty_and_total",
    "cart_motivator_popup",
    "cart_remove_line",
]


class HealthPayload(BaseModel):
    report_id: str = ""
    project: Dict[str, Any] = Field(default_factory=dict)
    pages: Dict[str, Any] = Field(default_factory=dict)
    options: Dict[str, Any] = Field(default_factory=dict)
    selectors: Dict[str, Any] = Field(default_factory=dict)
    product: Dict[str, Any] = Field(default_factory=dict)
    expectations: Dict[str, Any] = Field(default_factory=dict)
    health_tests: Dict[str, Any] = Field(default_factory=dict)
    checks: Dict[str, Any] = Field(default_factory=dict)


@app.get("/")
async def root():
    return {
        "ok": True,
        "service": "ecom-health-runner",
        "endpoints": ["/health-check"],
        "auth_required": not ALLOW_PUBLIC_ACCESS,
        "allowed_hosts_configured": bool(ALLOWED_HOSTS),
        "max_concurrent_runs": MAX_CONCURRENT_RUNS,
        "min_timeout_ms": MIN_TIMEOUT_MS,
        "max_timeout_ms": MAX_TIMEOUT_MS,
    }


@app.get("/health")
async def health():
    return {"ok": True, "service": "ecom-health-runner"}


def safe_int(value: Any, default: int = 0) -> int:
    try:
        if value is None:
            return default
        return int(value)
    except Exception:
        return default


def get_timeout_ms(payload: HealthPayload) -> int:
    return clamp_int(payload.options.get("timeout_ms") or DEFAULT_TIMEOUT_MS, DEFAULT_TIMEOUT_MS, MIN_TIMEOUT_MS, MAX_TIMEOUT_MS)


def host_matches(host: str, allowed_host: str) -> bool:
    host = (host or "").lower().strip(".")
    allowed_host = (allowed_host or "").lower().strip(".")
    return bool(host and allowed_host and (host == allowed_host or host.endswith(f".{allowed_host}")))


def host_allowed_by_env(host: str) -> bool:
    if not ALLOWED_HOSTS:
        return True
    return any(host_matches(host, allowed_host) for allowed_host in ALLOWED_HOSTS)


def ip_is_public(ip_value: str) -> bool:
    try:
        ip = ipaddress.ip_address(ip_value)
    except ValueError:
        return False
    return not (ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_reserved or ip.is_multicast or ip.is_unspecified)


@lru_cache(maxsize=512)
def host_resolves_to_public_ips(host: str) -> bool:
    host = (host or "").lower().strip(".")
    if not host or host in BLOCKED_HOSTNAMES or host.endswith(".localhost"):
        return False
    try:
        ipaddress.ip_address(host)
        return ip_is_public(host)
    except ValueError:
        pass
    try:
        addresses = socket.getaddrinfo(host, None)
    except Exception:
        return False
    if not addresses:
        return False
    return all(ip_is_public(address[4][0]) for address in addresses)


def validate_url(url: Any, enforce_allowlist: bool = True) -> tuple[bool, str]:
    parsed = urlparse(str(url or "").strip())
    if parsed.scheme not in {"http", "https"}:
        return False, "URL must use http or https"
    host = (parsed.hostname or "").lower().strip(".")
    if not host:
        return False, "URL host is empty"
    if enforce_allowlist and not host_allowed_by_env(host):
        return False, "URL host is not allowed"
    if not host_resolves_to_public_ips(host):
        return False, "URL host is not public"
    return True, ""


async def require_api_key(request: Request):
    if ALLOW_PUBLIC_ACCESS:
        return
    if not HEALTH_RUNNER_API_KEY:
        raise HTTPException(status_code=503, detail="HEALTH_RUNNER_API_KEY is not configured")
    provided = (request.headers.get("x-api-key") or request.headers.get("x-health-runner-key") or "").strip()
    authorization = request.headers.get("authorization", "").strip()
    if authorization.lower().startswith("bearer "):
        provided = authorization[7:].strip()
    if not provided or not hmac.compare_digest(provided, HEALTH_RUNNER_API_KEY):
        raise HTTPException(status_code=401, detail="Invalid API key")


def make_check(check_id: str, title: str, status: str, actual: Optional[Dict[str, Any]] = None, expected: Optional[Dict[str, Any]] = None, error: Optional[Any] = None, screenshot_base64: Optional[str] = None):
    item = {"id": check_id, "title": title, "status": status, "actual": actual or {}, "expected": expected or {}}
    if error:
        item["error"] = str(error)
    if screenshot_base64:
        item["screenshot_base64"] = screenshot_base64
    return item


def warning_check(check_id: str, title: str, reason: str, expected: Optional[Dict[str, Any]] = None):
    return make_check(check_id, title, "warning", actual={"skipped": True, "reason": reason}, expected=expected or {})


def build_summary(checks: List[Dict[str, Any]]):
    total = len(checks)
    passed = sum(1 for check in checks if check["status"] == "ok")
    warnings = sum(1 for check in checks if check["status"] == "warning")
    failed = sum(1 for check in checks if check["status"] in {"failed", "error"})
    status = "failed" if failed else "warning" if warnings else "ok"
    return status, {"total": total, "passed": passed, "warnings": warnings, "failed": failed}


def page_url(payload: HealthPayload, page_id: str) -> str:
    item = payload.pages.get(page_id)
    return str(item.get("url") or "").strip() if isinstance(item, dict) else ""


def selector_value(payload: HealthPayload, key: str, default: str = "") -> str:
    value = payload.selectors.get(key)
    if value is None:
        value = payload.product.get(key)
    if value is None:
        value = payload.expectations.get(key)
    return str(value or default).strip()


def get_check_ids(payload: HealthPayload) -> List[str]:
    explicit = payload.options.get("checks") or payload.checks.get("ids")
    if isinstance(explicit, list):
        ids = [str(item.get("id") if isinstance(item, dict) else item).strip() for item in explicit]
        ids = [item for item in ids if item]
        if ids:
            return ids
    ids = []
    for group in [payload.health_tests.get("product_page") or [], payload.health_tests.get("cart_page") or []]:
        if isinstance(group, list):
            for item in group:
                if isinstance(item, dict) and item.get("id"):
                    ids.append(str(item["id"]).strip())
                elif isinstance(item, str):
                    ids.append(item.strip())
    return [item for item in ids if item] or DEFAULT_CHECK_IDS


async def screenshot_base64(page, enabled: bool):
    if not enabled:
        return None
    try:
        raw = await page.screenshot(full_page=True)
        return base64.b64encode(raw).decode("utf-8")
    except Exception:
        return None


async def setup_routes(page, block_resource_types: List[str]):
    async def route_guard(route):
        ok, _ = validate_url(route.request.url, enforce_allowlist=False)
        if not ok or route.request.resource_type in block_resource_types:
            await route.abort()
            return
        await route.continue_()
    await page.route("**/*", route_guard)


async def attach_collectors(page, artifacts: Dict[str, Any], limit: int):
    def add_limited(key: str, item: Any):
        if len(artifacts[key]) < limit:
            artifacts[key].append(item)
    page.on("console", lambda msg: add_limited("console", {"type": msg.type, "text": msg.text[:500]}) if msg.type in {"error", "warning"} else None)
    page.on("pageerror", lambda err: add_limited("page_errors", str(err)[:1000]))
    page.on("requestfailed", lambda req: add_limited("failed_requests", {"url": req.url[:500], "method": req.method, "failure": str(req.failure)[:500]}))


async def goto_checked(page, url: str, timeout_ms: int):
    ok, reason = validate_url(url, enforce_allowlist=True)
    if not ok:
        raise ValueError(reason)
    response = await page.goto(url, wait_until="domcontentloaded", timeout=timeout_ms)
    await page.wait_for_timeout(1000)
    status = response.status if response else 0
    return {"url": url, "status_code": status, "ok": status == 0 or status < 400}


async def visible_state(page, selector: str):
    return await page.evaluate(
        """
        (selector) => {
          const el = document.querySelector(selector);
          if (!el) return { exists: false, visible: false };
          const style = getComputedStyle(el);
          const rect = el.getBoundingClientRect();
          return {
            exists: true,
            visible: style.display !== 'none' && style.visibility !== 'hidden' && rect.width > 0 && rect.height > 0,
            width: rect.width,
            height: rect.height
          };
        }
        """,
        selector,
    )


async def cart_state(page, payload: HealthPayload):
    selectors = {
        "cart_count": selector_value(payload, "cart_count"),
        "cart_total": selector_value(payload, "cart_total"),
        "cart_item": selector_value(payload, "cart_item", ".cart-item, [data-cart-item], .cart__item"),
    }
    return await page.evaluate(
        """
        (selectors) => {
          const parseNumber = (value) => {
            const match = String(value || '').replace(/\\s/g, '').match(/\\d+[\\d.,]*/);
            if (!match) return null;
            return Number(match[0].replace(',', '.'));
          };
          const result = { has_cart_object: false, qty: null, total_price: null, lines_count: null, lines: [], source: null, error: null };
          try {
            if (window.Cart && Cart.order && typeof Cart.order.get === 'function') {
              const order = Cart.order.get();
              const lines = Array.isArray(order.order_lines) ? order.order_lines : [];
              result.has_cart_object = true;
              result.qty = lines.reduce((sum, line) => sum + (+line.quantity || 0), 0);
              result.total_price = Number(order.total_price || 0);
              result.lines_count = lines.length;
              result.lines = lines.map((line) => ({ product_id: line.product_id, variant_id: line.variant_id, quantity: line.quantity, accessory_value_ids: line.accessory_value_ids }));
              result.source = 'cart_object';
              return result;
            }
            if (selectors.cart_count) {
              const countEl = document.querySelector(selectors.cart_count);
              if (countEl) { result.qty = parseNumber(countEl.textContent); result.source = 'cart_count_selector'; }
            }
            if (selectors.cart_total) {
              const totalEl = document.querySelector(selectors.cart_total);
              if (totalEl) result.total_price = parseNumber(totalEl.textContent);
            }
            if (selectors.cart_item) result.lines_count = document.querySelectorAll(selectors.cart_item).length;
            return result;
          } catch (error) {
            result.error = String(error);
            return result;
          }
        }
        """,
        selectors,
    )


def add_button_selectors(payload: HealthPayload):
    return [selector_value(payload, "add_to_cart_button"), "[data-add-cart-counter-btn]", "[data-add-cart]", "[data-cart-add]", "[data-item-add]", "button[name='add_cart']", "button[name='add']", "form[action*='cart'] button[type='submit']"]


def accessory_selectors(payload: HealthPayload):
    return [selector_value(payload, "accessory_option"), ".accessory-groups-container .accessory-values__item", ".accessory-groups-container .option-value", "[data-accessory-value]", "[data-option-value]"]


def remove_button_selectors(payload: HealthPayload):
    return [selector_value(payload, "cart_remove_button"), "[data-cart-remove]", "[data-remove]", ".cart-item [data-remove]", ".cart-item [class*='remove']"]


async def click_first(page, selectors: List[str], wait_after_ms: int = 500):
    return await page.evaluate(
        """
        async ({ selectors, waitAfterMs }) => {
          const sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms));
          for (const selector of selectors) {
            if (!selector) continue;
            const el = document.querySelector(selector);
            if (el) {
              el.click();
              await sleep(waitAfterMs);
              return { clicked: true, selector, text: (el.textContent || '').trim().slice(0, 200) };
            }
          }
          return { clicked: false, selector: null, text: null };
        }
        """,
        {"selectors": selectors, "waitAfterMs": wait_after_ms},
    )


async def click_add_and_measure(page, payload: HealthPayload, clicks: int, delay_after_ms: int = 2500):
    before = await cart_state(page, payload)
    click = await page.evaluate(
        """
        async ({ selectors, clicks, delayAfterMs }) => {
          const sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms));
          let button = null;
          let usedSelector = null;
          for (const selector of selectors) {
            if (!selector) continue;
            button = document.querySelector(selector);
            if (button) { usedSelector = selector; break; }
          }
          if (!button) return { clicked: false, reason: 'NO_ADD_BUTTON' };
          for (let i = 0; i < clicks; i += 1) button.click();
          await sleep(delayAfterMs);
          return { clicked: true, selector: usedSelector, clicks };
        }
        """,
        {"selectors": add_button_selectors(payload), "clicks": clicks, "delayAfterMs": delay_after_ms},
    )
    after = await cart_state(page, payload)
    bq = before.get("qty")
    aq = after.get("qty")
    delta = aq - bq if isinstance(bq, (int, float)) and isinstance(aq, (int, float)) else None
    return {"before": before, "after": after, "delta": delta, "click": click, "can_measure_quantity": delta is not None}


async def check_page_open(page, payload: HealthPayload, check_id: str, page_id: str, title: str, timeout_ms: int):
    url = page_url(payload, page_id)
    if not url:
        return make_check(check_id, title, "failed", actual={"reason": "PAGE_URL_MISSING"})
    try:
        actual = await goto_checked(page, url, timeout_ms)
        return make_check(check_id, title, "ok" if actual["ok"] else "failed", actual=actual, expected={"status_code": "<400"})
    except Exception as error:
        return make_check(check_id, title, "failed", actual={"url": url}, error=error)


async def ensure_product_in_cart(page, payload: HealthPayload, timeout_ms: int):
    product_url = page_url(payload, "product")
    if not product_url:
        return {"ok": False, "reason": "PRODUCT_URL_MISSING"}
    await goto_checked(page, product_url, timeout_ms)
    result = await click_add_and_measure(page, payload, 1)
    result["ok"] = bool(result["click"].get("clicked"))
    return result


async def run_single_check(browser, payload: HealthPayload, check_id: str):
    timeout_ms = get_timeout_ms(payload)
    include_screenshots = payload.options.get("include_screenshots") is True
    artifact_limit = clamp_int(payload.options.get("artifact_limit"), 20, 0, 100)
    block_resource_types = payload.options.get("block_resource_types")
    if not isinstance(block_resource_types, list):
        block_resource_types = ["image", "media", "font"]
    artifacts = {"console": [], "page_errors": [], "failed_requests": []}
    context = await browser.new_context(viewport={"width": VIEWPORT_WIDTH, "height": VIEWPORT_HEIGHT}, user_agent=USER_AGENT, locale=str(payload.options.get("locale") or "en-US"), reduced_motion="reduce")
    page = await context.new_page()
    await setup_routes(page, [str(item) for item in block_resource_types])
    await attach_collectors(page, artifacts, artifact_limit)
    try:
        if check_id == "home_page_open":
            check = await check_page_open(page, payload, check_id, "home", "Home page opens", timeout_ms)
        elif check_id == "product_page_open":
            check = await check_page_open(page, payload, check_id, "product", "Product page opens", timeout_ms)
        elif check_id == "cart_page_open":
            check = await check_page_open(page, payload, check_id, "cart", "Cart page opens", timeout_ms)
        elif check_id == "product_gallery_visible":
            product_url = page_url(payload, "product")
            if not product_url:
                check = make_check(check_id, "Product gallery visible", "failed", actual={"reason": "PRODUCT_URL_MISSING"})
            else:
                await goto_checked(page, product_url, timeout_ms)
                selector = selector_value(payload, "product_gallery", ".js-product-gallery-main, [data-product-gallery], .product-gallery, .product__gallery")
                actual = await visible_state(page, selector)
                check = make_check(check_id, "Product gallery visible", "ok" if actual.get("visible") else "failed", actual={**actual, "selector": selector}, expected={"visible": True})
        elif check_id == "product_common_sanity":
            product_url = page_url(payload, "product")
            if not product_url:
                check = make_check(check_id, "Product runtime sanity", "failed", actual={"reason": "PRODUCT_URL_MISSING"})
            else:
                await goto_checked(page, product_url, timeout_ms)
                state = await cart_state(page, payload)
                check = make_check(check_id, "Product runtime sanity", "ok" if state.get("has_cart_object") else "warning", actual=state, expected={"cart_object_or_configured_selectors": True})
        elif check_id in {"add_product_to_cart", "double_click_add_guard"}:
            product_url = page_url(payload, "product")
            if not product_url:
                check = make_check(check_id, check_id, "failed", actual={"reason": "PRODUCT_URL_MISSING"})
            else:
                await goto_checked(page, product_url, timeout_ms)
                clicks = 2 if check_id == "double_click_add_guard" else 1
                expected = safe_int(payload.expectations.get("expected_double_click_delta" if clicks == 2 else "expected_add_delta"), 1)
                actual = await click_add_and_measure(page, payload, clicks, 3200 if clicks == 2 else 2500)
                if not actual["click"].get("clicked"):
                    status = "failed"
                elif actual["can_measure_quantity"]:
                    status = "ok" if actual.get("delta") == expected else "failed"
                else:
                    status = "warning"
                title = "Double click add guard" if clicks == 2 else "Add product to cart"
                check = make_check(check_id, title, status, actual=actual, expected={"delta": expected})
        elif check_id == "add_product_with_accessory":
            product_url = page_url(payload, "product")
            if not product_url:
                check = make_check(check_id, "Add product with option/accessory", "failed", actual={"reason": "PRODUCT_URL_MISSING"})
            else:
                await goto_checked(page, product_url, timeout_ms)
                selected = await click_first(page, accessory_selectors(payload), 500)
                if not selected.get("clicked"):
                    check = warning_check(check_id, "Add product with option/accessory", "NO_ACCESSORY_SELECTOR_OR_OPTION_FOUND")
                else:
                    actual = await click_add_and_measure(page, payload, 1)
                    actual["selected_option"] = selected
                    expected = safe_int(payload.expectations.get("expected_accessory_add_delta"), 1)
                    status = "warning" if not actual["can_measure_quantity"] else "ok" if actual.get("delta") == expected else "failed"
                    check = make_check(check_id, "Add product with option/accessory", status, actual=actual, expected={"delta": expected})
        elif check_id == "update_accessory_without_quantity_change":
            product_url = page_url(payload, "product")
            if not product_url:
                check = make_check(check_id, "Update option without quantity change", "failed", actual={"reason": "PRODUCT_URL_MISSING"})
            else:
                await goto_checked(page, product_url, timeout_ms)
                add_result = await click_add_and_measure(page, payload, 1)
                selected = await click_first(page, accessory_selectors(payload), 500)
                update_selector = selector_value(payload, "accessory_update_button", ".accessory-cart-primary, [data-accessory-update]")
                before = await cart_state(page, payload)
                update_click = await click_first(page, [update_selector], 2500)
                after = await cart_state(page, payload)
                bq = before.get("qty")
                aq = after.get("qty")
                delta = aq - bq if isinstance(bq, (int, float)) and isinstance(aq, (int, float)) else None
                actual = {"add_result": add_result, "selected_option": selected, "update_click": update_click, "before": before, "after": after, "delta": delta}
                status = "warning" if not update_click.get("clicked") or delta is None else "ok" if delta == safe_int(payload.expectations.get("expected_update_delta"), 0) else "failed"
                check = make_check(check_id, "Update option without quantity change", status, actual=actual, expected={"delta": safe_int(payload.expectations.get("expected_update_delta"), 0)})
        elif check_id in {"cart_sanity", "cart_no_duplicate_lines", "cart_qty_and_total", "cart_remove_line", "cart_motivator_popup"}:
            cart_url = page_url(payload, "cart")
            if not cart_url:
                check = make_check(check_id, check_id, "failed", actual={"reason": "CART_URL_MISSING"})
            else:
                setup = await ensure_product_in_cart(page, payload, timeout_ms)
                await goto_checked(page, cart_url, timeout_ms)
                state_before = await cart_state(page, payload)
                if check_id == "cart_sanity":
                    detected = state_before.get("has_cart_object") or state_before.get("lines_count") is not None or state_before.get("qty") is not None
                    check = make_check(check_id, "Cart sanity", "ok" if detected else "warning", actual={"setup_result": setup, "cart_state": state_before}, expected={"cart_state_detected": True})
                elif check_id == "cart_no_duplicate_lines":
                    lines = state_before.get("lines") or []
                    if not lines:
                        check = make_check(check_id, "Cart has no duplicate lines", "warning", actual={"setup_result": setup, "cart_state": state_before, "reason": "CART_LINES_UNAVAILABLE"})
                    else:
                        keys = [f"{line.get('variant_id')}:{line.get('accessory_value_ids')}" for line in lines]
                        no_dupes = len(lines) == len(set(keys))
                        check = make_check(check_id, "Cart has no duplicate lines", "ok" if no_dupes else "failed", actual={"lines_count": len(lines), "unique_count": len(set(keys)), "lines": lines}, expected={"no_duplicate_lines": True})
                elif check_id == "cart_qty_and_total":
                    qty_ok = isinstance(state_before.get("qty"), (int, float)) and state_before.get("qty") > 0
                    total_known = isinstance(state_before.get("total_price"), (int, float))
                    total_ok = total_known and state_before.get("total_price") > 0
                    status = "ok" if qty_ok and (total_ok or not total_known) else "warning" if state_before.get("qty") is None and state_before.get("total_price") is None else "failed"
                    check = make_check(check_id, "Cart quantity and total", status, actual={"setup_result": setup, "cart_state": state_before}, expected={"qty_gt_zero": True, "total_price_gt_zero_if_available": True})
                elif check_id == "cart_remove_line":
                    click = await click_first(page, remove_button_selectors(payload), 2000)
                    state_after = await cart_state(page, payload)
                    before_value = state_before.get("lines_count") if state_before.get("lines_count") is not None else state_before.get("qty")
                    after_value = state_after.get("lines_count") if state_after.get("lines_count") is not None else state_after.get("qty")
                    decreased = after_value < before_value if isinstance(before_value, (int, float)) and isinstance(after_value, (int, float)) else None
                    status = "failed" if not click.get("clicked") else "warning" if decreased is None else "ok" if decreased else "failed"
                    check = make_check(check_id, "Cart remove line", status, actual={"setup_result": setup, "before": state_before, "after": state_after, "click": click, "decreased": decreased}, expected={"decreased": True})
                else:
                    open_selector = selector_value(payload, "motivator_open_button")
                    card_selector = selector_value(payload, "motivator_card")
                    if not open_selector and not card_selector:
                        check = warning_check(check_id, "Cart motivator popup", "NO_MOTIVATOR_SELECTORS_CONFIGURED")
                    else:
                        if open_selector:
                            await click_first(page, [open_selector], 900)
                        card_selector = card_selector or ".motivator-popup__card, [data-motivator-card]"
                        visible = await visible_state(page, card_selector)
                        check = make_check(check_id, "Cart motivator popup", "ok" if visible.get("visible") else "warning", actual={"setup_result": setup, "card": visible, "card_selector": card_selector}, expected={"card_visible": True})
        else:
            check = make_check(check_id, check_id, "warning", actual={"reason": "UNKNOWN_CHECK_ID"})
        if check["status"] in {"failed", "error"}:
            check["screenshot_base64"] = await screenshot_base64(page, include_screenshots)
        check["artifacts"] = artifacts
        return check
    except Exception as error:
        return make_check(check_id, check_id, "error", actual={"traceback": traceback.format_exc()}, error=error, screenshot_base64=await screenshot_base64(page, include_screenshots))
    finally:
        await context.close()


@app.post("/health-check")
async def health_check(request: Request, payload: HealthPayload):
    await require_api_key(request)
    check_ids = get_check_ids(payload)
    if not check_ids:
        return {"compact_version": "v1", "status": "warning", "skipped": True, "summary": {"total": 0, "passed": 0, "warnings": 1, "failed": 0}, "checks": [], "errors": [], "warnings": [{"id": "no_checks", "title": "No health checks configured"}], "artifacts": {}}
    try:
        await asyncio.wait_for(RUN_SEMAPHORE.acquire(), timeout=QUEUE_TIMEOUT_MS / 1000)
    except asyncio.TimeoutError as error:
        raise HTTPException(status_code=429, detail="Runner is busy") from error
    try:
        checks = []
        async with async_playwright() as p:
            browser = await p.chromium.launch(headless=True, args=["--no-sandbox", "--disable-setuid-sandbox", "--disable-dev-shm-usage", "--disable-gpu", "--disable-software-rasterizer", "--disable-extensions", "--disable-background-networking", "--disable-default-apps", "--disable-sync", "--disable-translate", "--hide-scrollbars", "--mute-audio", "--no-first-run", "--no-zygote"])
            try:
                for check_id in check_ids:
                    checks.append(await run_single_check(browser, payload, check_id))
            finally:
                await browser.close()
        status, summary = build_summary(checks)
        return {
            "compact_version": "v1",
            "status": status,
            "skipped": False,
            "summary": summary,
            "checks": [{"id": check.get("id"), "title": check.get("title"), "status": check.get("status"), "actual": check.get("actual", {}), "expected": check.get("expected", {}), "error": check.get("error")} for check in checks],
            "errors": [{"id": check.get("id"), "title": check.get("title"), "error": check.get("error"), "actual": check.get("actual", {}), "screenshot_base64": check.get("screenshot_base64")} for check in checks if check.get("status") in {"failed", "error"}],
            "warnings": [{"id": check.get("id"), "title": check.get("title"), "actual": check.get("actual", {})} for check in checks if check.get("status") == "warning"],
            "artifacts": {
                "console_errors": [{"check_id": check.get("id"), "items": check.get("artifacts", {}).get("console", [])} for check in checks if check.get("artifacts", {}).get("console")],
                "page_errors": [{"check_id": check.get("id"), "items": check.get("artifacts", {}).get("page_errors", [])} for check in checks if check.get("artifacts", {}).get("page_errors")],
                "failed_requests": [{"check_id": check.get("id"), "items": check.get("artifacts", {}).get("failed_requests", [])} for check in checks if check.get("artifacts", {}).get("failed_requests")],
            },
        }
    finally:
        RUN_SEMAPHORE.release()
