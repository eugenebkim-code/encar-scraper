"""aiohttp web server: serves webapp/ static files + /catalog.json + API endpoints."""

import asyncio
import hashlib
import hmac
import json
import logging
import os
import urllib.parse

import aiohttp
from aiohttp import web

from storage import load_filters, save_filters
from bot import build_filter, parse_filter_label, _seed_and_show

log = logging.getLogger(__name__)

CATALOG_FILE = os.path.join(os.path.dirname(__file__), "catalog.json")
WEBAPP_DIR = os.path.join(os.path.dirname(__file__), "webapp")

_PROXY_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/120.0.0.0 Safari/537.36"
    ),
    "Referer": "https://www.encar.com/",
}
_CORS = {"Access-Control-Allow-Origin": "*"}


def _make_token(secret: str, user_id: int) -> str:
    return hmac.new(secret.encode(), str(user_id).encode(), hashlib.sha256).hexdigest()[:24]


def _verify_token(secret: str, user_id: int, token: str) -> bool:
    expected = _make_token(secret, user_id)
    return hmac.compare_digest(expected, token)


async def handle_index(request: web.Request) -> web.FileResponse:
    return web.FileResponse(os.path.join(WEBAPP_DIR, "index.html"))


async def handle_add_page(request: web.Request) -> web.FileResponse:
    return web.FileResponse(os.path.join(WEBAPP_DIR, "add.html"))


async def handle_catalog(request: web.Request) -> web.Response:
    if not os.path.exists(CATALOG_FILE):
        raise web.HTTPNotFound(reason="catalog.json not found — run discover_filters.py first")
    with open(CATALOG_FILE, encoding="utf-8") as f:
        data = f.read()
    return web.Response(text=data, content_type="application/json", headers=_CORS)


async def handle_badges(request: web.Request) -> web.Response:
    """Return unique Badge values for a given query (manufacturer+model)."""
    q = request.rel_url.query.get("q", "")
    if not q:
        raise web.HTTPBadRequest(reason="Missing 'q' parameter")
    session: aiohttp.ClientSession = request.app["http_session"]
    badges: set[str] = set()
    try:
        async with session.get(
            "https://api.encar.com/search/car/list/general",
            params={"count": "true", "q": q, "sr": "|ModifiedDate|0|100"},
            headers=_PROXY_HEADERS,
            timeout=aiohttp.ClientTimeout(total=10),
        ) as resp:
            resp.raise_for_status()
            data = await resp.json(content_type=None)
            for car in data.get("SearchResults", []):
                b = car.get("Badge")
                if b and isinstance(b, str) and b.strip():
                    badges.add(b.strip())
    except Exception as e:
        log.error("Badges proxy error: %s", e)
        return web.json_response({"badges": [], "error": str(e)}, headers=_CORS)
    return web.json_response({"badges": sorted(badges)}, headers=_CORS)


async def handle_count(request: web.Request) -> web.Response:
    """Proxy /api/count?q=<encar_query> → Encar API, return {"count": N}."""
    q = request.rel_url.query.get("q", "")
    if not q:
        raise web.HTTPBadRequest(reason="Missing 'q' parameter")
    session: aiohttp.ClientSession = request.app["http_session"]
    try:
        async with session.get(
            "https://api.encar.com/search/car/list/general",
            params={"count": "true", "q": q, "sr": "|ModifiedDate|0|1"},
            headers=_PROXY_HEADERS,
            timeout=aiohttp.ClientTimeout(total=10),
        ) as resp:
            resp.raise_for_status()
            data = await resp.json(content_type=None)
    except aiohttp.ClientResponseError as e:
        log.warning("Encar API error: %s", e)
        return web.json_response({"count": None, "error": str(e)}, headers=_CORS)
    except Exception as e:
        log.error("Proxy error: %s", e)
        return web.json_response({"count": None, "error": str(e)}, headers=_CORS)
    return web.json_response({"count": data.get("Count", 0)}, headers=_CORS)


async def handle_add_filter(request: web.Request) -> web.Response:
    """POST /api/add_filter — validate token, save filter, return label."""
    secret: str = request.app["secret"]
    try:
        body = await request.json()
    except Exception:
        return web.json_response({"ok": False, "error": "Invalid JSON"}, status=400, headers=_CORS)

    uid_raw = body.get("uid")
    token = body.get("tok", "")
    payload = body.get("filter", {})

    if not uid_raw or not token or not payload:
        return web.json_response({"ok": False, "error": "Missing fields"}, status=400, headers=_CORS)

    try:
        user_id = int(uid_raw)
    except (ValueError, TypeError):
        return web.json_response({"ok": False, "error": "Invalid uid"}, status=400, headers=_CORS)

    if not _verify_token(secret, user_id, token):
        return web.json_response({"ok": False, "error": "Invalid token"}, status=403, headers=_CORS)

    mfr = payload.get("manufacturer")
    if not mfr:
        return web.json_response({"ok": False, "error": "Manufacturer required"}, status=400, headers=_CORS)

    api_query = build_filter(
        manufacturer=mfr,
        car_type=payload.get("car_type", "Y"),
        model=payload.get("model") or None,
        badge=payload.get("badge") or None,
        fuel_type=payload.get("fuel_type") or None,
        region=payload.get("region") or None,
        price=tuple(payload["price"]) if payload.get("price") else None,
        mileage=tuple(payload["mileage"]) if payload.get("mileage") else None,
        color=payload.get("color") or None,
    )
    year = payload.get("year")
    badge = payload.get("badge") or None
    meta: dict = {"q": api_query}
    if year:
        meta["year"] = list(year)
    if badge:
        meta["badge"] = badge
    filter_item = meta if (year or badge) else api_query

    filters = load_filters(user_id)
    filters.append(filter_item)
    save_filters(user_id, filters)

    label = parse_filter_label(filter_item)
    log.info("Filter added via web for user=%s: %s", user_id, label)
    return web.json_response({"ok": True, "label": label}, headers=_CORS)


def _verify_initdata(bot_token: str, init_data: str) -> dict | None:
    """Verify Telegram WebApp initData. Returns user dict if valid, None otherwise."""
    try:
        params = dict(urllib.parse.parse_qsl(init_data, keep_blank_values=True))
    except Exception:
        return None
    hash_val = params.pop("hash", "")
    if not hash_val:
        return None
    data_check = "\n".join(f"{k}={v}" for k, v in sorted(params.items()))
    secret_key = hmac.new(b"WebAppData", bot_token.encode(), hashlib.sha256).digest()
    expected = hmac.new(secret_key, data_check.encode(), hashlib.sha256).hexdigest()
    if not hmac.compare_digest(expected, hash_val):
        return None
    try:
        return json.loads(params.get("user", "{}"))
    except Exception:
        return None


async def handle_add_filter_tg(request: web.Request) -> web.Response:
    """POST /api/add_filter_tg — verify Telegram initData, save filter, notify user."""
    bot_token: str = request.app["secret"]
    try:
        body = await request.json()
    except Exception:
        return web.json_response({"ok": False, "error": "Invalid JSON"}, status=400, headers=_CORS)

    init_data = body.get("init_data", "")
    payload = body.get("filter", {})

    if not init_data or not payload:
        return web.json_response({"ok": False, "error": "Missing fields"}, status=400, headers=_CORS)

    user_data = _verify_initdata(bot_token, init_data)
    if not user_data:
        return web.json_response({"ok": False, "error": "Invalid initData"}, status=403, headers=_CORS)

    user_id = user_data.get("id")
    if not user_id:
        return web.json_response({"ok": False, "error": "No user ID"}, status=400, headers=_CORS)

    mfr = payload.get("manufacturer")
    if not mfr:
        return web.json_response({"ok": False, "error": "Manufacturer required"}, status=400, headers=_CORS)

    api_query = build_filter(
        manufacturer=mfr,
        car_type=payload.get("car_type", "Y"),
        model=payload.get("model") or None,
        badge=payload.get("badge") or None,
        fuel_type=payload.get("fuel_type") or None,
        region=payload.get("region") or None,
        price=tuple(payload["price"]) if payload.get("price") else None,
        mileage=tuple(payload["mileage"]) if payload.get("mileage") else None,
        color=payload.get("color") or None,
    )
    year = payload.get("year")
    badge = payload.get("badge") or None
    meta: dict = {"q": api_query}
    if year:
        meta["year"] = list(year)
    if badge:
        meta["badge"] = badge
    filter_item = meta if (year or badge) else api_query

    filters = load_filters(user_id)
    filters.append(filter_item)
    save_filters(user_id, filters)

    label = parse_filter_label(filter_item)
    log.info("Filter added via webapp for user=%s: %s", user_id, label)

    ptb_app = request.app.get("ptb_app")
    if ptb_app:
        asyncio.create_task(_notify_new_filter(ptb_app, user_id, filter_item, label))

    return web.json_response({"ok": True, "label": label}, headers=_CORS)


async def _notify_new_filter(ptb_app, user_id: int, filter_item, label: str) -> None:
    try:
        await ptb_app.bot.send_message(
            chat_id=user_id,
            text=f"✅ Фильтр добавлен: *{label}*\n\nБуду уведомлять о новых объявлениях.",
            parse_mode="Markdown",
        )
        await _seed_and_show(ptb_app.bot, ptb_app.bot_data, user_id, filter_item)
    except Exception as e:
        log.error("Notify error for user=%s: %s", user_id, e)


async def _on_startup(app: web.Application) -> None:
    app["http_session"] = aiohttp.ClientSession()


async def _on_cleanup(app: web.Application) -> None:
    await app["http_session"].close()


def build_web_app(secret: str = "", ptb_app=None) -> web.Application:
    app = web.Application()
    app["secret"] = secret
    app["ptb_app"] = ptb_app
    app.on_startup.append(_on_startup)
    app.on_cleanup.append(_on_cleanup)
    # Explicit routes first, static catch-all last
    app.router.add_get("/", handle_index)
    app.router.add_get("/add", handle_add_page)
    app.router.add_get("/catalog.json", handle_catalog)
    app.router.add_get("/api/badges", handle_badges)
    app.router.add_get("/api/count", handle_count)
    app.router.add_post("/api/add_filter", handle_add_filter)
    app.router.add_post("/api/add_filter_tg", handle_add_filter_tg)
    if os.path.isdir(WEBAPP_DIR):
        app.router.add_static("/", WEBAPP_DIR, show_index=False)
    return app
