"""aiohttp web server: serves webapp/ static files + /catalog.json + API endpoints."""

import hashlib
import hmac
import json
import logging
import os

import aiohttp
from aiohttp import web

from storage import load_filters, save_filters
from bot import build_filter, parse_filter_label

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
    )
    year = payload.get("year")
    filter_item = {"q": api_query, "year": list(year)} if year else api_query

    filters = load_filters(user_id)
    filters.append(filter_item)
    save_filters(user_id, filters)

    label = parse_filter_label(filter_item)
    log.info("Filter added via web for user=%s: %s", user_id, label)
    return web.json_response({"ok": True, "label": label}, headers=_CORS)


async def _on_startup(app: web.Application) -> None:
    app["http_session"] = aiohttp.ClientSession()


async def _on_cleanup(app: web.Application) -> None:
    await app["http_session"].close()


def build_web_app(secret: str = "") -> web.Application:
    app = web.Application()
    app["secret"] = secret
    app.on_startup.append(_on_startup)
    app.on_cleanup.append(_on_cleanup)
    # Explicit routes first, static catch-all last
    app.router.add_get("/", handle_index)
    app.router.add_get("/add", handle_add_page)
    app.router.add_get("/catalog.json", handle_catalog)
    app.router.add_get("/api/badges", handle_badges)
    app.router.add_get("/api/count", handle_count)
    app.router.add_post("/api/add_filter", handle_add_filter)
    if os.path.isdir(WEBAPP_DIR):
        app.router.add_static("/", WEBAPP_DIR, show_index=False)
    return app
