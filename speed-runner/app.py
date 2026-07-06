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


def env_int(name, default):
    try:
        return int(os.getenv(name, str(default)))
    except Exception:
        return default


def clamp(value, default, minimum, maximum):
    try:
        value = int(value)
    except Exception:
        value = default
    return max(minimum, min(value, maximum))


app = FastAPI(title="Ecommerce Speed Runner")

MIN_TIMEOUT_MS = max(1000, env_int("MIN_TIMEOUT_MS", 5000))
MAX_TIMEOUT_MS = max(MIN_TIMEOUT_MS, env_int("MAX_TIMEOUT_MS", 120000))
DEFAULT_TIMEOUT_MS = clamp(env_int("DEFAULT_TIMEOUT_MS", 60000), 60000, MIN_TIMEOUT_MS, MAX_TIMEOUT_MS)
MAX_VISUAL_WAIT_MS = clamp(env_int("MAX_VISUAL_WAIT_MS", 15000), 15000, 1000, MAX_TIMEOUT_MS)
MAX_CONCURRENT_RUNS = max(1, env_int("MAX_CONCURRENT_RUNS", 1))
QUEUE_TIMEOUT_MS = max(1000, env_int("QUEUE_TIMEOUT_MS", 300000))
VIEWPORT_WIDTH = max(320, env_int("VIEWPORT_WIDTH", 1365))
VIEWPORT_HEIGHT = max(320, env_int("VIEWPORT_HEIGHT", 768))

RUN_LOCK = asyncio.Semaphore(MAX_CONCURRENT_RUNS)

SPEED_RUNNER_API_KEY = os.getenv("SPEED_RUNNER_API_KEY", "").strip()
ALLOW_PUBLIC_ACCESS = os.getenv("ALLOW_PUBLIC_ACCESS", "false").strip().lower() in {"1", "true", "yes", "y"}
ALLOWED_HOSTS = [x.strip().lower().lstrip(".") for x in os.getenv("ALLOWED_HOSTS", "").split(",") if x.strip()]

USER_AGENT = os.getenv(
    "USER_AGENT",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
)

BROWSER_ARGS = [
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
]


@app.get("/")
async def root():
    return {
        "ok": True,
        "service": "ecom-speed-runner",
        "endpoints": ["/speed-report-json", "/speed-report-image"],
        "auth_required": not ALLOW_PUBLIC_ACCESS,
        "allowed_hosts_configured": bool(ALLOWED_HOSTS),
        "max_concurrent_runs": MAX_CONCURRENT_RUNS,
        "min_timeout_ms": MIN_TIMEOUT_MS,
        "max_timeout_ms": MAX_TIMEOUT_MS,
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


def get_timeout_ms(payload):
    speed = payload.get("speed") if isinstance(payload.get("speed"), dict) else {}
    return clamp(payload.get("timeout_ms") or speed.get("timeout_ms") or DEFAULT_TIMEOUT_MS, DEFAULT_TIMEOUT_MS, MIN_TIMEOUT_MS, MAX_TIMEOUT_MS)


def host(url):
    try:
        return (urlparse(url).hostname or "").lower().strip(".")
    except Exception:
        return ""


def host_matches(value, allowed):
    value = (value or "").lower().strip(".")
    allowed = (allowed or "").lower().strip(".")
    return bool(value and allowed and (value == allowed or value.endswith(f".{allowed}")))


def host_allowed(value):
    if not ALLOWED_HOSTS:
        return True
    return any(host_matches(value, allowed) for allowed in ALLOWED_HOSTS)


def public_ip(value):
    try:
        ip = ipaddress.ip_address(value)
    except ValueError:
        return False
    return not (ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_reserved or ip.is_multicast or ip.is_unspecified)


@lru_cache(maxsize=512)
def public_host(value):
    value = (value or "").lower().strip(".")
    if not value or value == "localhost" or value.endswith(".localhost"):
        return False
    try:
        return public_ip(value)
    except ValueError:
        pass
    try:
        addresses = socket.getaddrinfo(value, None)
    except Exception:
        return False
    return bool(addresses) and all(public_ip(address[4][0]) for address in addresses)


def valid_url(url, enforce_allowlist=True):
    parsed = urlparse(str(url or "").strip())
    if parsed.scheme not in {"http", "https"}:
        return False
    value = (parsed.hostname or "").lower().strip(".")
    if not value:
        return False
    if enforce_allowlist and not host_allowed(value):
        return False
    return public_host(value)


async def require_api_key(request: Request):
    if ALLOW_PUBLIC_ACCESS:
        return
    if not SPEED_RUNNER_API_KEY:
        raise HTTPException(status_code=503, detail="SPEED_RUNNER_API_KEY is not configured")
    provided = (request.headers.get("x-api-key") or request.headers.get("x-speed-runner-key") or "").strip()
    authorization = request.headers.get("authorization", "").strip()
    if authorization.lower().startswith("bearer "):
        provided = authorization[7:].strip()
    if not provided or not hmac.compare_digest(provided, SPEED_RUNNER_API_KEY):
        raise HTTPException(status_code=401, detail="Invalid API key")


def escape_html(value):
    return html.escape(str(value or ""), quote=False)


def fmt_time(seconds):
    seconds = safe_float(seconds)
    if seconds <= 0:
        return "0"
    if seconds < 1:
        return f"{round(seconds * 1000)}ms"
    return f"{seconds:.1f}s"


def source_group(url, main_host):
    current_host = host(url)
    if main_host and host_matches(current_host, main_host):
        return "origin"
    if any(x in current_host for x in ["cdn", "cloudfront", "akamai", "fastly", "static", "assets", "img.", "image.", "images.", "storage."]):
        return "cdn"
    return "third_party"


def is_image(url, resource_type):
    low = (url or "").lower().split("?")[0]
    return resource_type == "image" or low.endswith((".jpg", ".jpeg", ".png", ".webp", ".avif", ".gif", ".svg"))


def prepare_screenshot(png_bytes):
    image = Image.open(io.BytesIO(png_bytes)).convert("L")
    return image.resize((240, 135))


def visual_similarity(a_bytes, b_bytes):
    try:
        first = prepare_screenshot(a_bytes)
        second = prepare_screenshot(b_bytes)
        diff = ImageChops.difference(first, second)
        stat = ImageStat.Stat(diff)
        rms = sum(value ** 2 for value in stat.rms) ** 0.5
        return 1 - min(rms / 255, 1)
    except Exception:
        return 0


async def measure_visual_ready(page, started, max_wait_ms):
    frames = []
    for _ in range(max(1, int(max_wait_ms / 500))):
        try:
            frames.append({"time": round(time.time() - started, 2), "shot": await page.screenshot(full_page=False, type="png", timeout=3000)})
        except Exception:
            pass
        await page.wait_for_timeout(500)
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
    if not url or not valid_url(url):
        return None
    name = str(item.get("name") or item.get("title") or fallback_name).strip() or fallback_name
    return {"name": name[:48], "url": url}


def extract_sites(payload):
    pages = payload.get("pages") if isinstance(payload.get("pages"), dict) else {}
    speed = payload.get("speed") if isinstance(payload.get("speed"), dict) else {}
    sites = []
    home = pages.get("home") if isinstance(pages.get("home"), dict) else {}
    home_site = normalize_site({"name": home.get("title") or "Home", "url": home.get("url")}, "Home")
    if home_site:
        sites.append(home_site)
    for group in [speed.get("sites"), speed.get("reference_pages"), pages.get("references"), pages.get("competitors")]:
        if isinstance(group, list):
            for index, item in enumerate(group, start=1):
                site = normalize_site(item, f"Reference {index}")
                if site:
                    sites.append(site)
    unique = []
    seen = set()
    for site in sites:
        if site["url"] not in seen:
            unique.append(site)
            seen.add(site["url"])
    return unique[:4]


def path_name(url):
    try:
        parsed = urlparse(url)
        return (parsed.path.split("/")[-1] or parsed.netloc or "home")[:80]
    except Exception:
        return str(url)[:80]


async def collect_performance(page):
    return await page.evaluate(
        """
        () => {
          const nav = performance.getEntriesByType('navigation')[0] || {};
          const resources = performance.getEntriesByType('resource') || [];
          return {
            nav: {
              dom: nav.domContentLoadedEventEnd || 0,
              load: nav.loadEventEnd || 0
            },
            resources: resources.map(item => ({
              url: item.name,
              type: item.initiatorType || '',
              duration: item.duration || 0,
              size: item.transferSize || 0
            }))
          };
        }
        """
    )


async def measure_site(browser, site, timeout_ms):
    main_host = host(site["url"])
    failed = []
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
            if valid_url(route.request.url, enforce_allowlist=False):
                await route.continue_()
            else:
                failed.append(route.request.url)
                await route.abort()

        await page.route("**/*", route_guard)
        page.on("requestfailed", lambda request: failed.append(request.url))

        started = time.time()
        load_error = None
        try:
            await page.goto(site["url"], wait_until="commit", timeout=timeout_ms)
        except Exception as error:
            load_error = str(error)[:300]

        visual_ready_sec = await measure_visual_ready(page, started, min(MAX_VISUAL_WAIT_MS, max(1000, timeout_ms - 5000)))

        try:
            await page.wait_for_load_state("load", timeout=3000)
        except Exception:
            pass

        perf = await collect_performance(page)
        await context.close()
        context = None

        resources = perf.get("resources") or []
        countable = [item for item in resources if safe_float(item.get("duration")) <= 30000]
        images = [item for item in countable if is_image(item.get("url"), item.get("type"))]
        sources = [source_group(item.get("url"), main_host) for item in resources]
        top_slow = sorted(countable, key=lambda item: safe_float(item.get("duration")), reverse=True)[:3]

        metrics = {
            "visual_ready_sec": round(safe_float(visual_ready_sec), 2),
            "dom_sec": round(safe_float((perf.get("nav") or {}).get("dom")) / 1000, 2),
            "onload_sec": round(safe_float((perf.get("nav") or {}).get("load")) / 1000, 2),
            "max_request_sec": round(max([safe_float(item.get("duration")) / 1000 for item in countable], default=0), 2),
            "failed_status0": len(set(failed)),
            "slow_3": len([item for item in countable if safe_float(item.get("duration")) > 3000]),
            "slow_10": len([item for item in countable if safe_float(item.get("duration")) > 10000]),
            "origin_requests": len([x for x in sources if x == "origin"]),
            "cdn_requests": len([x for x in sources if x == "cdn"]),
            "third_party_requests": len([x for x in sources if x == "third_party"]),
            "image_count": len(images),
            "image_max_sec": round(max([safe_float(item.get("duration")) / 1000 for item in images], default=0), 2),
            "load_error": load_error,
            "top_slow": [{"name": path_name(item.get("url")), "time_sec": round(safe_float(item.get("duration")) / 1000, 2), "source": source_group(item.get("url"), main_host)} for item in top_slow],
        }
        return {"name": site["name"], "url": site["url"], "metrics": metrics}
    except Exception as error:
        return {"name": site.get("name", "Page"), "url": site.get("url", ""), "metrics": {"visual_ready_sec": 0, "dom_sec": 0, "onload_sec": 0, "max_request_sec": 0, "failed_status0": 0, "slow_3": 0, "slow_10": 0, "origin_requests": 0, "cdn_requests": 0, "third_party_requests": 0, "image_count": 0, "image_max_sec": 0, "load_error": str(error)[:300], "top_slow": []}}
    finally:
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
    load_error = metric(primary, "load_error", "")
    speed = payload.get("speed") if isinstance(payload.get("speed"), dict) else {}
    thresholds = speed.get("thresholds") if isinstance(speed.get("thresholds"), dict) else {}
    ok_visual = safe_float(thresholds.get("ok_visual_sec"), 6)
    warn_visual = safe_float(thresholds.get("warn_visual_sec"), 12)
    ok_slow_3 = safe_int(thresholds.get("ok_slow_3"), 2)
    warn_slow_3 = safe_int(thresholds.get("warn_slow_3"), 10)
    if not primary or visual <= 0:
        status, status_title, conclusion = "bad", "🔴 no data", "the primary page could not be measured correctly."
    elif load_error:
        status, status_title, conclusion = "warn", "⚠️ warning", "the primary page returned a navigation error during measurement."
    elif visual <= ok_visual and slow_3 <= ok_slow_3:
        status, status_title, conclusion = "ok", "✅ ok", "no critical speed issues were detected on the primary page."
    elif visual <= warn_visual and slow_3 <= warn_slow_3:
        status, status_title, conclusion = "warn", "⚠️ warning", "the primary page has moderate delay."
    else:
        status, status_title, conclusion = "bad", "🔴 slow", "the primary page is noticeably slower than expected."
    primary_name = primary.get("name") if primary else "Primary page"
    lines = [f"⚡ <b>Speed: {status_title}</b>", "", f"<b>{escape_html(primary_name)}:</b> first screen {fmt_time(visual)}, slow requests &gt;3s — {slow_3}."]
    references = {}
    for index, item in enumerate(results[1:], start=1):
        item_name = item.get("name") or f"Reference {index}"
        ref_visual = safe_float(metric(item, "visual_ready_sec"))
        lines.append(f"<b>{escape_html(item_name)}:</b> first screen {fmt_time(ref_visual)}.")
        references[f"reference_{index}"] = {"name": item_name, "url": item.get("url"), "visual_sec": ref_visual, "slow_3": safe_int(metric(item, "slow_3"))}
    lines.extend(["", f"<b>Conclusion:</b> {escape_html(conclusion)}"])
    return {"ok": status == "ok", "status": status, "status_title": status_title, "telegramMessage": "\n".join(lines), "primary": {"name": primary_name, "url": primary.get("url") if primary else "", "visual_sec": visual, "slow_3": slow_3, "max_request_sec": safe_float(metric(primary, "max_request_sec")), "image_max_sec": safe_float(metric(primary, "image_max_sec")), "load_error": load_error}, "references": references, "results": results, "errors": []}


def load_font(size, bold=False):
    paths = [
        "/usr/share/fonts/truetype/noto/NotoSans-Bold.ttf" if bold else "/usr/share/fonts/truetype/noto/NotoSans-Regular.ttf",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf" if bold else "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
    ]
    for path in paths:
        if os.path.exists(path):
            return ImageFont.truetype(path, size)
    return ImageFont.load_default()


def text_fit(draw, text, font, max_width):
    text = str(text)
    if draw.textlength(text, font=font) <= max_width:
        return text
    while text and draw.textlength(text + "…", font=font) > max_width:
        text = text[:-1]
    return text + "…"


def level(key, value):
    value = safe_float(value)
    if value <= 0:
        return "neutral"
    if key == "visual_ready_sec":
        return "good" if value <= 6 else "warn" if value <= 12 else "bad"
    if key == "max_request_sec":
        return "good" if value <= 2 else "warn" if value <= 8 else "bad"
    if key == "slow_3":
        return "good" if value <= 2 else "warn" if value <= 15 else "bad"
    if key == "image_max_sec":
        return "good" if value <= 1.5 else "warn" if value <= 6 else "bad"
    return "neutral"


def render_error_png(message):
    image = Image.new("RGB", (1200, 360), "#12151c")
    draw = ImageDraw.Draw(image)
    title_font = load_font(30, True)
    text_font = load_font(18)
    draw.rounded_rectangle([36, 36, 1164, 324], radius=18, fill="#181c25")
    draw.text((60, 64), "Speed report — error", fill="#f2f4f8", font=title_font)
    draw.rounded_rectangle([60, 130, 1140, 196], radius=10, fill="#a73636")
    draw.text((80, 152), text_fit(draw, message, text_font, 1040), fill="#ffffff", font=text_font)
    draw.text((60, 246), "Check request payload, API key, allowed hosts, and runner logs.", fill="#aeb6c5", font=text_font)
    return image


def render_png(results, project_name=""):
    visible = results[:4]
    site_count = max(len(visible), 1)
    width, margin, header_h, row_h, section_h, footer_h = 1200, 36, 108, 46, 38, 70
    col_metric = 330
    col_w = int((width - margin * 2 - 40 - col_metric) / site_count)
    rows = [("Visual loading", None), ("First screen stable", "visual_ready_sec"), ("Request tail", None), ("Max request", "max_request_sec"), ("Slow requests >3s", "slow_3"), ("Images", None), ("Max image time", "image_max_sec")]
    height = margin + header_h + row_h + footer_h + margin + len(rows) * row_h
    colors = {"bg": "#12151c", "panel": "#181c25", "section": "#232936", "header": "#202633", "grid": "#343b4a", "text": "#f2f4f8", "muted": "#aeb6c5", "good": "#1f8f5f", "warn": "#a67818", "bad": "#a73636", "neutral": "#2a3040"}
    image = Image.new("RGB", (width, height), colors["bg"])
    draw = ImageDraw.Draw(image)
    title_font, sub_font, header_font, cell_font, cell_bold, footer_font = load_font(30, True), load_font(17), load_font(18, True), load_font(18), load_font(18, True), load_font(16)
    draw.rounded_rectangle([margin, margin, width - margin, height - margin], radius=18, fill=colors["panel"])
    title = project_name or "Ecommerce site"
    report_date = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    draw.text((60, 56), f"Speed report — {title}", fill=colors["text"], font=title_font)
    draw.text((60, 98), f"Date: {report_date}   Desktop {VIEWPORT_WIDTH}×{VIEWPORT_HEIGHT}", fill=colors["muted"], font=sub_font)
    table_x, table_y, table_width = margin + 20, margin + header_h, width - margin * 2 - 40
    col_x = [table_x, table_x + col_metric] + [table_x + col_metric + col_w * index for index in range(1, site_count)]
    draw.rectangle([table_x, table_y, table_x + table_width, table_y + row_h], fill=colors["header"])
    for index, header in enumerate(["Metric"] + [item["name"] for item in visible]):
        draw.text((col_x[index] + 12, table_y + 12), text_fit(draw, header, header_font, (col_metric if index == 0 else col_w) - 24), fill=colors["text"], font=header_font)
    y = table_y + row_h
    for label, key in rows:
        if key is None:
            draw.rectangle([table_x, y, table_x + table_width, y + section_h], fill=colors["section"])
            draw.text((table_x + 12, y + 9), label, fill=colors["text"], font=header_font)
            y += section_h
            continue
        draw.rectangle([table_x, y, table_x + table_width, y + row_h], fill=colors["panel"])
        draw.text((table_x + 12, y + 13), label, fill=colors["muted"], font=cell_font)
        for index, item in enumerate(visible):
            value = metric(item, key)
            fill = colors[level(key, value)]
            x = col_x[index + 1]
            draw.rounded_rectangle([x + 8, y + 7, x + col_w - 8, y + row_h - 7], radius=8, fill=fill)
            text = str(safe_int(value)) if key == "slow_3" else fmt_time(value)
            draw.text((x + 16, y + 13), text_fit(draw, text, cell_bold, col_w - 30), fill="#ffffff", font=cell_bold)
        draw.line([table_x, y + row_h, table_x + table_width, y + row_h], fill=colors["grid"], width=1)
        y += row_h
    summary = " | ".join([f"{item['name']}: screen {fmt_time(metric(item, 'visual_ready_sec'))}, slow >3s {safe_int(metric(item, 'slow_3'))}" for item in visible])
    summary = text_fit(draw, summary, footer_font, width - margin * 2 - 48)
    draw.text((60, height - margin - footer_h + 14), "Summary:", fill=colors["muted"], font=footer_font)
    draw.text((148, height - margin - footer_h + 14), summary, fill=colors["text"], font=footer_font)
    return image


async def run_speed_results(payload):
    timeout_ms = get_timeout_ms(payload)
    sites = extract_sites(payload)
    if not sites:
        return []
    try:
        await asyncio.wait_for(RUN_LOCK.acquire(), timeout=QUEUE_TIMEOUT_MS / 1000)
    except asyncio.TimeoutError as error:
        raise HTTPException(status_code=429, detail="Runner is busy") from error
    try:
        async with async_playwright() as playwright:
            browser = await playwright.chromium.launch(headless=True, args=BROWSER_ARGS)
            try:
                return [await measure_site(browser, site, timeout_ms) for site in sites]
            finally:
                await browser.close()
    finally:
        RUN_LOCK.release()


@app.post("/speed-report-json")
async def speed_report_json(request: Request, payload: dict):
    await require_api_key(request)
    results = await run_speed_results(payload)
    if not results:
        return {"ok": False, "status": "error", "status_title": "❌ error", "telegramMessage": "❌ <b>Speed: error</b>\n\nNo valid public page URL was provided.", "primary": {}, "references": {}, "results": [], "errors": ["No valid public page URL was provided"]}
    return build_speed_json(results, payload)


@app.post("/speed-report-image")
async def speed_report_image(request: Request, payload: dict):
    await require_api_key(request)
    results = await run_speed_results(payload)
    project = payload.get("project") if isinstance(payload.get("project"), dict) else {}
    project_name = str(project.get("name") or project.get("slug") or "")
    image = render_error_png("No valid public page URL was provided.") if not results else render_png(results, project_name=project_name)
    output = io.BytesIO()
    image.save(output, format="PNG", optimize=True)
    output.seek(0)
    return StreamingResponse(output, media_type="image/png", headers={"Content-Disposition": "inline; filename=speed-report.png"})
