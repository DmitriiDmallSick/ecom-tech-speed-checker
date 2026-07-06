import asyncio
import hmac
import html
import io
import ipaddress
import os
import socket
import time
from datetime import datetime, timezone
from functools import lru_cache
from urllib.parse import urlparse

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import StreamingResponse
from PIL import Image, ImageChops, ImageDraw, ImageFont, ImageStat
from playwright.async_api import async_playwright


app = FastAPI(title="Ecommerce Speed Runner")

DEFAULT_TIMEOUT_MS = int(os.getenv("DEFAULT_TIMEOUT_MS", "60000"))
VIEWPORT_WIDTH = int(os.getenv("VIEWPORT_WIDTH", "1365"))
VIEWPORT_HEIGHT = int(os.getenv("VIEWPORT_HEIGHT", "768"))
MAX_VISUAL_WAIT_MS = int(os.getenv("MAX_VISUAL_WAIT_MS", "15000"))

SPEED_RUNNER_API_KEY = os.getenv("SPEED_RUNNER_API_KEY", "").strip()
ALLOW_PUBLIC_ACCESS = os.getenv("ALLOW_PUBLIC_ACCESS", "false").strip().lower() in {
    "1",
    "true",
    "yes",
    "y",
}

ALLOWED_HOSTS = [
    item.strip().lower().lstrip(".")
    for item in os.getenv("ALLOWED_HOSTS", "").split(",")
    if item.strip()
]

BLOCKED_HOSTNAMES = {
    "localhost",
    "metadata",
    "metadata.google.internal",
    "host.docker.internal",
    "kubernetes.default",
}

USER_AGENT = os.getenv(
    "USER_AGENT",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/120.0.0.0 Safari/537.36",
)


@app.get("/")
async def root():
    return {
        "ok": True,
        "service": "ecom-speed-runner",
        "endpoints": ["/speed-report-json", "/speed-report-image"],
        "auth": "disabled" if ALLOW_PUBLIC_ACCESS else "required",
        "allowed_hosts_configured": bool(ALLOWED_HOSTS),
    }


@app.get("/health")
async def health():
    return {"ok": True, "service": "ecom-speed-runner"}


def safe_float(value, default=0.0):
    try:
        if value is None:
            return default
        return float(value)
    except Exception:
        return default


def safe_int(value, default=0):
    try:
        if value is None:
            return default
        return int(value)
    except Exception:
        return default


def fmt_time(seconds):
    seconds = safe_float(seconds, 0)
    if seconds <= 0:
        return "0"
    if seconds < 1:
        return f"{round(seconds * 1000)}ms"
    return f"{seconds:.1f}s"


def escape_html(value):
    return html.escape(str(value or ""), quote=False)


def hostname(url):
    try:
        parsed = urlparse(url)
        return (parsed.hostname or "").lower().strip(".")
    except Exception:
        return ""


def host_matches(host, allowed_host):
    host = (host or "").lower().strip(".")
    allowed_host = (allowed_host or "").lower().strip(".")

    if not host or not allowed_host:
        return False

    return host == allowed_host or host.endswith(f".{allowed_host}")


def host_allowed_by_env(host):
    if not ALLOWED_HOSTS:
        return True

    return any(host_matches(host, allowed_host) for allowed_host in ALLOWED_HOSTS)


def ip_is_public(ip_value):
    try:
        ip = ipaddress.ip_address(ip_value)
    except ValueError:
        return False

    if ip.is_private:
        return False
    if ip.is_loopback:
        return False
    if ip.is_link_local:
        return False
    if ip.is_reserved:
        return False
    if ip.is_multicast:
        return False
    if ip.is_unspecified:
        return False

    return True


@lru_cache(maxsize=512)
def host_resolves_to_public_ips(host):
    host = (host or "").lower().strip(".")

    if not host:
        return False

    if host in BLOCKED_HOSTNAMES or host.endswith(".localhost"):
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

    for address in addresses:
        ip_value = address[4][0]
        if not ip_is_public(ip_value):
            return False

    return True


def validate_url(url, enforce_allowlist=True):
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

    if not SPEED_RUNNER_API_KEY:
        raise HTTPException(
            status_code=503,
            detail="SPEED_RUNNER_API_KEY is not configured",
        )

    provided = (
        request.headers.get("x-api-key")
        or request.headers.get("x-speed-runner-key")
        or ""
    ).strip()

    authorization = request.headers.get("authorization", "").strip()

    if authorization.lower().startswith("bearer "):
        provided = authorization[7:].strip()

    if not provided or not hmac.compare_digest(provided, SPEED_RUNNER_API_KEY):
        raise HTTPException(status_code=401, detail="Invalid API key")


def is_image(url, resource_type):
    low = (url or "").lower().split("?")[0]
    if resource_type == "image":
        return True
    return low.endswith((".jpg", ".jpeg", ".png", ".webp", ".avif", ".gif", ".svg"))


def source_group(url, main_host):
    h = hostname(url)

    if main_host and host_matches(h, main_host):
        return "origin"

    if any(
        marker in h
        for marker in [
            "cdn",
            "cloudfront",
            "akamai",
            "fastly",
            "static",
            "assets",
            "img.",
            "image.",
            "images.",
            "storage.",
            "blob.core.",
            "r2.cloudflarestorage.",
        ]
    ):
        return "cdn"

    return "third_party"


def path_name(url):
    try:
        parsed = urlparse(url)
        name = parsed.path.split("/")[-1] or parsed.netloc or "home"
        return name.split("?")[0][:80]
    except Exception:
        return str(url)[:80]


def prepare_screenshot(png_bytes):
    image = Image.open(io.BytesIO(png_bytes)).convert("L")
    return image.resize((240, 135))


def visual_similarity(a_bytes, b_bytes):
    try:
        a = prepare_screenshot(a_bytes)
        b = prepare_screenshot(b_bytes)
        diff = ImageChops.difference(a, b)
        stat = ImageStat.Stat(diff)
        rms = sum(value**2 for value in stat.rms) ** 0.5
        return 1 - min(rms / 255, 1)
    except Exception:
        return 0


async def measure_visual_ready(page, started, max_wait_ms=MAX_VISUAL_WAIT_MS, step_ms=500):
    frames = []
    steps = max(1, int(max_wait_ms / step_ms))

    for _ in range(steps):
        try:
            shot = await page.screenshot(full_page=False, type="png", timeout=3000)
            frames.append({"time": round(time.time() - started, 2), "shot": shot})
        except Exception:
            pass

        await page.wait_for_timeout(step_ms)

    if len(frames) < 3:
        return 0

    final_shot = frames[-1]["shot"]

    for frame in frames:
        if visual_similarity(frame["shot"], final_shot) >= 0.94:
            return frame["time"]

    return frames[-1]["time"]


def normalize_site(item, fallback_name):
    if not isinstance(item, dict):
        return None

    url = str(item.get("url") or "").strip()
    if not url:
        return None

    ok, _ = validate_url(url, enforce_allowlist=True)
    if not ok:
        return None

    name = str(item.get("name") or item.get("title") or fallback_name).strip()
    if not name:
        name = fallback_name

    return {"name": name[:48], "url": url}


def extract_sites(payload):
    pages = payload.get("pages") or {}
    speed = payload.get("speed") or {}

    sites = []

    home = pages.get("home") or {}
    home_site = normalize_site(
        {"name": home.get("title") or "Home", "url": home.get("url")},
        "Home",
    )

    if home_site:
        sites.append(home_site)

    optional_lists = [
        speed.get("sites"),
        speed.get("reference_pages"),
        pages.get("references"),
        pages.get("competitors"),
    ]

    for group in optional_lists:
        if not isinstance(group, list):
            continue

        for index, item in enumerate(group, start=1):
            site = normalize_site(item, f"Reference {index}")
            if site:
                sites.append(site)

    unique = []
    seen = set()

    for site in sites:
        if site["url"] in seen:
            continue
        seen.add(site["url"])
        unique.append(site)

    return unique[:4]


async def measure_site(browser, site, timeout_ms):
    main_host = hostname(site["url"])
    requests = {}
    completed = []
    failed = []
    pending_tasks = set()

    context = None
    page = None

    try:
        context = await browser.new_context(
            viewport={"width": VIEWPORT_WIDTH, "height": VIEWPORT_HEIGHT},
            user_agent=USER_AGENT,
            locale="en-US",
            reduced_motion="reduce",
        )

        page = await context.new_page()

        async def route_guard(route):
            ok, _ = validate_url(route.request.url, enforce_allowlist=False)

            if not ok:
                await route.abort()
                return

            await route.continue_()

        await page.route("**/*", route_guard)

        def on_request(request):
            requests[request] = time.time()

        async def collect_request_finished(request):
            started = requests.pop(request, None)
            if not started:
                return

            try:
                response = await request.response()
                status = response.status if response else 0
            except Exception:
                status = 0

            completed.append(
                {
                    "url": request.url,
                    "resource_type": request.resource_type,
                    "status": status,
                    "time_sec": time.time() - started,
                    "source": source_group(request.url, main_host),
                }
            )

        def on_request_finished(request):
            task = asyncio.create_task(collect_request_finished(request))
            pending_tasks.add(task)
            task.add_done_callback(pending_tasks.discard)

        def on_request_failed(request):
            started = requests.pop(request, None)
            failed.append(
                {
                    "url": request.url,
                    "resource_type": request.resource_type,
                    "status": 0,
                    "time_sec": time.time() - started if started else 0,
                    "source": source_group(request.url, main_host),
                }
            )

        page.on("request", on_request)
        page.on("requestfinished", on_request_finished)
        page.on("requestfailed", on_request_failed)

        started = time.time()
        load_error = None

        try:
            await page.goto(site["url"], wait_until="commit", timeout=timeout_ms)
        except Exception as error:
            load_error = str(error)[:300]

        visual_ready_sec = await measure_visual_ready(
            page=page,
            started=started,
            max_wait_ms=min(MAX_VISUAL_WAIT_MS, max(3000, timeout_ms - 5000)),
        )

        try:
            await page.wait_for_load_state("load", timeout=3000)
        except Exception:
            pass

        if pending_tasks:
            _, pending = await asyncio.wait(pending_tasks, timeout=5)
            for task in pending:
                task.cancel()

        try:
            nav = await page.evaluate(
                """
                () => {
                  const n = performance.getEntriesByType('navigation')[0];
                  if (!n) return {};
                  return {
                    domContentLoadedEventEnd: n.domContentLoadedEventEnd || 0,
                    loadEventEnd: n.loadEventEnd || 0
                  };
                }
                """
            )
        except Exception:
            nav = {}

        await context.close()
        context = None

        entries = completed + failed

        countable = [
            entry
            for entry in entries
            if entry["status"] != 0 and entry["time_sec"] <= 30
        ]

        image_entries = [
            entry
            for entry in countable
            if is_image(entry["url"], entry.get("resource_type"))
        ]

        max_request_sec = max([entry["time_sec"] for entry in countable], default=0)
        image_max_sec = max([entry["time_sec"] for entry in image_entries], default=0)

        metrics = {
            "visual_ready_sec": round(safe_float(visual_ready_sec), 2),
            "dom_sec": round(safe_float(nav.get("domContentLoadedEventEnd")) / 1000, 2),
            "onload_sec": round(safe_float(nav.get("loadEventEnd")) / 1000, 2),
            "max_request_sec": round(max_request_sec, 2),
            "failed_status0": len(failed),
            "slow_3": len([entry for entry in countable if entry["time_sec"] > 3]),
            "slow_10": len([entry for entry in countable if entry["time_sec"] > 10]),
            "errors_4xx": len([entry for entry in countable if 400 <= entry["status"] <= 499]),
            "errors_5xx": len([entry for entry in countable if entry["status"] >= 500]),
            "origin_requests": len([entry for entry in entries if entry["source"] == "origin"]),
            "cdn_requests": len([entry for entry in entries if entry["source"] == "cdn"]),
            "third_party_requests": len([entry for entry in entries if entry["source"] == "third_party"]),
            "image_count": len(image_entries),
            "image_max_sec": round(image_max_sec, 2),
            "load_error": load_error,
            "top_slow": [
                {
                    "name": path_name(entry["url"]),
                    "time_sec": round(entry["time_sec"], 2),
                    "source": entry["source"],
                }
                for entry in sorted(countable, key=lambda x: x["time_sec"], reverse=True)[:3]
            ],
        }

        return {"name": site["name"], "url": site["url"], "metrics": metrics}

    except Exception as error:
        return {
            "name": site.get("name", "Page"),
            "url": site.get("url", ""),
            "metrics": {
                "visual_ready_sec": 0,
                "dom_sec": 0,
                "onload_sec": 0,
                "max_request_sec": 0,
                "failed_status0": 0,
                "slow_3": 0,
                "slow_10": 0,
                "errors_4xx": 0,
                "errors_5xx": 1,
                "origin_requests": 0,
                "cdn_requests": 0,
                "third_party_requests": 0,
                "image_count": 0,
                "image_max_sec": 0,
                "load_error": str(error)[:300],
                "top_slow": [],
            },
        }
    finally:
        for task in list(pending_tasks):
            task.cancel()

        try:
            if page:
                await page.close()
        except Exception:
            pass

        try:
            if context:
                await context.close()
        except Exception:
            pass


def metric(result, key, default=0):
    return ((result or {}).get("metrics") or {}).get(key, default)


def build_speed_json(results, payload):
    primary = results[0] if results else None

    visual = safe_float(metric(primary, "visual_ready_sec"))
    slow_3 = safe_int(metric(primary, "slow_3"))
    max_request = safe_float(metric(primary, "max_request_sec"))
    image_max = safe_float(metric(primary, "image_max_sec"))
    load_error = metric(primary, "load_error", "")

    thresholds = ((payload.get("speed") or {}).get("thresholds") or {})
    ok_visual = safe_float(thresholds.get("ok_visual_sec"), 6)
    warn_visual = safe_float(thresholds.get("warn_visual_sec"), 12)
    ok_slow_3 = safe_int(thresholds.get("ok_slow_3"), 2)
    warn_slow_3 = safe_int(thresholds.get("warn_slow_3"), 10)

    if not primary or visual <= 0:
        status = "bad"
        status_title = "🔴 no data"
        conclusion = "the primary page could not be measured correctly."
    elif load_error:
        status = "warn"
        status_title = "⚠️ warning"
        conclusion = "the primary page returned a navigation error during measurement. Check runner logs and page availability."
    elif visual <= ok_visual and slow_3 <= ok_slow_3:
        status = "ok"
        status_title = "✅ ok"
        conclusion = "no critical speed issues were detected on the primary page."
    elif visual <= warn_visual and slow_3 <= warn_slow_3:
        status = "warn"
        status_title = "⚠️ warning"
        conclusion = "the primary page has moderate delay. Check trends and recent changes."
    else:
        status = "bad"
        status_title = "🔴 slow"
        conclusion = "the primary page is noticeably slower than expected. Check images, CDN, third-party scripts, and JavaScript."

    primary_name = primary.get("name") if primary else "Primary page"

    lines = [
        f"⚡ <b>Speed: {status_title}</b>",
        "",
        f"<b>{escape_html(primary_name)}:</b> first screen {fmt_time(visual)}, slow requests &gt;3s — {slow_3}.",
    ]

    references = {}

    for index, item in enumerate(results[1:], start=1):
        ref_visual = safe_float(metric(item, "visual_ready_sec"))
        item_name = item.get("name") or f"Reference {index}"
        lines.append(f"<b>{escape_html(item_name)}:</b> first screen {fmt_time(ref_visual)}.")
        references[f"reference_{index}"] = {
            "name": item_name,
            "url": item.get("url"),
            "visual_sec": ref_visual,
            "slow_3": safe_int(metric(item, "slow_3")),
        }

    lines.extend(["", f"<b>Conclusion:</b> {escape_html(conclusion)}"])

    return {
        "ok": status == "ok",
        "status": status,
        "status_title": status_title,
        "telegramMessage": "\n".join(lines),
        "primary": {
            "name": primary_name,
            "url": primary.get("url") if primary else "",
            "visual_sec": visual,
            "slow_3": slow_3,
            "max_request_sec": max_request,
            "image_max_sec": image_max,
            "load_error": load_error,
        },
        "references": references,
        "results": results,
        "errors": [],
    }


def load_font(size, bold=False):
    candidates = [
        "/usr/share/fonts/truetype/noto/NotoSans-Bold.ttf" if bold else "/usr/share/fonts/truetype/noto/NotoSans-Regular.ttf",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf" if bold else "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
    ]

    for path in candidates:
        if os.path.exists(path):
            return ImageFont.truetype(path, size)

    return ImageFont.load_default()


def cell_level(key, value):
    value = safe_float(value)

    if value <= 0:
        return "neutral"

    if key == "visual_ready_sec":
        if value <= 6:
            return "good"
        if value <= 12:
            return "warn"
        return "bad"

    if key == "max_request_sec":
        if value <= 2:
            return "good"
        if value <= 8:
            return "warn"
        return "bad"

    if key == "slow_3":
        if value <= 2:
            return "good"
        if value <= 15:
            return "warn"
        return "bad"

    if key == "image_max_sec":
        if value <= 1.5:
            return "good"
        if value <= 6:
            return "warn"
        return "bad"

    return "neutral"


def text_fit(draw, text, font, max_width):
    text = str(text)

    if draw.textlength(text, font=font) <= max_width:
        return text

    result = text

    while result and draw.textlength(result + "…", font=font) > max_width:
        result = result[:-1]

    return result + "…"


def render_error_png(message):
    width = 1200
    height = 360
    margin = 36

    colors = {
        "bg": "#12151c",
        "panel": "#181c25",
        "text": "#f2f4f8",
        "muted": "#aeb6c5",
        "bad": "#a73636",
    }

    image = Image.new("RGB", (width, height), colors["bg"])
    draw = ImageDraw.Draw(image)

    font_title = load_font(30, True)
    font_text = load_font(18)

    draw.rounded_rectangle([margin, margin, width - margin, height - margin], radius=18, fill=colors["panel"])
    draw.text((margin + 24, margin + 28), "Speed report — error", fill=colors["text"], font=font_title)
    draw.rounded_rectangle([margin + 24, margin + 94, width - margin - 24, margin + 160], radius=10, fill=colors["bad"])
    draw.text((margin + 44, margin + 116), text_fit(draw, message, font_text, width - margin * 2 - 88), fill="#ffffff", font=font_text)
    draw.text((margin + 24, margin + 210), "Check request payload, API key, allowed hosts, and runner logs.", fill=colors["muted"], font=font_text)

    return image


def render_png(results, project_name=""):
    visible = results[:4]
    site_count = max(len(visible), 1)

    width = 1200
    margin = 36
    header_h = 108
    row_h = 46
    section_h = 38
    footer_h = 70

    col_metric = 330
    col_w = int((width - margin * 2 - 40 - col_metric) / site_count)

    rows = [
        ("Visual loading", None),
        ("First screen stable", "visual_ready_sec"),
        ("Request tail", None),
        ("Max request", "max_request_sec"),
        ("Slow requests >3s", "slow_3"),
        ("Images", None),
        ("Max image time", "image_max_sec"),
    ]

    height = margin + header_h + row_h + footer_h + margin + len(rows) * row_h

    colors = {
        "bg": "#12151c",
        "panel": "#181c25",
        "section": "#232936",
        "header": "#202633",
        "grid": "#343b4a",
        "text": "#f2f4f8",
        "muted": "#aeb6c5",
        "good": "#1f8f5f",
        "warn": "#a67818",
        "bad": "#a73636",
        "neutral": "#2a3040",
    }

    image = Image.new("RGB", (width, height), colors["bg"])
    draw = ImageDraw.Draw(image)

    font_title = load_font(30, True)
    font_sub = load_font(17)
    font_header = load_font(18, True)
    font_cell = load_font(18)
    font_cell_bold = load_font(18, True)
    font_footer = load_font(16)

    draw.rounded_rectangle([margin, margin, width - margin, height - margin], radius=18, fill=colors["panel"])

    title = project_name or "Ecommerce site"
    report_date = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")

    draw.text((margin + 24, margin + 20), f"Speed report — {title}", fill=colors["text"], font=font_title)
    draw.text(
        (margin + 24, margin + 62),
        f"Date: {report_date}   Desktop {VIEWPORT_WIDTH}×{VIEWPORT_HEIGHT}",
        fill=colors["muted"],
        font=font_sub,
    )

    table_x = margin + 20
    table_y = margin + header_h
    table_width = width - margin * 2 - 40

    col_x = [table_x, table_x + col_metric]

    for index in range(1, site_count):
        col_x.append(table_x + col_metric + col_w * index)

    draw.rectangle([table_x, table_y, table_x + table_width, table_y + row_h], fill=colors["header"])
    headers = ["Metric"] + [item["name"] for item in visible]

    for index, header in enumerate(headers):
        x = col_x[index]
        width_limit = col_metric if index == 0 else col_w
        draw.text((x + 12, table_y + 12), text_fit(draw, header, font_header, width_limit - 24), fill=colors["text"], font=font_header)

    y = table_y + row_h

    for label, key in rows:
        if key is None:
            draw.rectangle([table_x, y, table_x + table_width, y + section_h], fill=colors["section"])
            draw.text((table_x + 12, y + 9), label, fill=colors["text"], font=font_header)
            y += section_h
            continue

        draw.rectangle([table_x, y, table_x + table_width, y + row_h], fill=colors["panel"])
        draw.text((table_x + 12, y + 13), label, fill=colors["muted"], font=font_cell)

        for index, item in enumerate(visible):
            value = metric(item, key)
            level = cell_level(key, value)
            fill = colors.get(level, colors["neutral"])
            x = col_x[index + 1]

            draw.rounded_rectangle([x + 8, y + 7, x + col_w - 8, y + row_h - 7], radius=8, fill=fill)

            text = str(safe_int(value)) if key == "slow_3" else fmt_time(value)
            draw.text((x + 16, y + 13), text_fit(draw, text, font_cell_bold, col_w - 30), fill="#ffffff", font=font_cell_bold)

        draw.line([table_x, y + row_h, table_x + table_width, y + row_h], fill=colors["grid"], width=1)
        y += row_h

    summary_parts = [
        f"{item['name']}: screen {fmt_time(metric(item, 'visual_ready_sec'))}, slow >3s {safe_int(metric(item, 'slow_3'))}"
        for item in visible
    ]

    summary = text_fit(draw, " | ".join(summary_parts), font_footer, width - margin * 2 - 48)
    footer_y = height - margin - footer_h + 14

    draw.text((margin + 24, footer_y), "Summary:", fill=colors["muted"], font=font_footer)
    draw.text((margin + 112, footer_y), summary, fill=colors["text"], font=font_footer)

    return image


async def run_speed_results(payload):
    timeout_ms = safe_int(
        payload.get("timeout_ms")
        or (payload.get("speed") or {}).get("timeout_ms")
        or DEFAULT_TIMEOUT_MS,
        DEFAULT_TIMEOUT_MS,
    )

    sites = extract_sites(payload)

    if not sites:
        return []

    results = []

    async with async_playwright() as p:
        browser = await p.chromium.launch(
            headless=True,
            args=[
                "--no-sandbox",
                "--disable-setuid-sandbox",
                "--disable-dev-shm-usage",
                "--disable-gpu",
                "--disable-software-rasterizer",
                "--disable-extensions",
                "--disable-background-networking",
                "--disable-default-apps",
                "--disable-sync",
                "--disable-translate",
                "--hide-scrollbars",
                "--mute-audio",
                "--no-first-run",
                "--no-zygote",
            ],
        )

        try:
            for site in sites:
                results.append(await measure_site(browser, site, timeout_ms))
        finally:
            await browser.close()

    return results


@app.post("/speed-report-json")
async def speed_report_json(request: Request, payload: dict):
    await require_api_key(request)

    results = await run_speed_results(payload)

    if not results:
        return {
            "ok": False,
            "status": "error",
            "status_title": "❌ error",
            "telegramMessage": "❌ <b>Speed: error</b>\n\nNo valid public page URL was provided.",
            "primary": {},
            "references": {},
            "results": [],
            "errors": ["No valid public page URL was provided"],
        }

    return build_speed_json(results, payload)


@app.post("/speed-report-image")
async def speed_report_image(request: Request, payload: dict):
    await require_api_key(request)

    results = await run_speed_results(payload)
    project = payload.get("project") or {}
    project_name = str(project.get("name") or project.get("slug") or "")

    if not results:
        image = render_error_png("No valid public page URL was provided.")
    else:
        image = render_png(results, project_name=project_name)

    output = io.BytesIO()
    image.save(output, format="PNG", optimize=True)
    output.seek(0)

    return StreamingResponse(
        output,
        media_type="image/png",
        headers={"Content-Disposition": 'inline; filename="speed-report.png"},
    )
