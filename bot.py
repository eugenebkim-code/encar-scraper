"""Telegram bot: per-user filter management + scraper job + admin panel."""

import asyncio
import datetime
import hashlib
import hmac
import json
import logging
import os
import re

from telegram import (
    InlineKeyboardButton, InlineKeyboardMarkup, Update,
    KeyboardButton, ReplyKeyboardMarkup, WebAppInfo,
    MenuButtonWebApp,
)
from telegram.ext import (
    Application,
    CallbackQueryHandler,
    CommandHandler,
    ConversationHandler,
    ContextTypes,
    MessageHandler,
    filters as tg_filters,
)

from scraper import fetch_cars, fetch_page, get_listing_url
from storage import (
    load_filters, save_filters,
    load_seen_ids, save_seen_ids,
    save_user_info, load_user_info,
    list_all_users,
)
from translations import (
    MANUFACTURER_EN, FUEL_TYPE_EN, REGION_EN, COLOR_EN,
    translate_model,
)

log = logging.getLogger(__name__)

CATALOG_FILE = os.path.join(os.path.dirname(__file__), "catalog.json")
MODELS_PER_PAGE = 10
REGIONS_PER_PAGE = 12

ADMIN_ID = 2115245228
WEBAPP_URL = os.environ.get("WEBAPP_URL", "").rstrip("/")

# Conversation states
MANUFACTURER, MODEL, FUEL_TYPE, REGION, PRICE, YEAR, MILEAGE = range(7)

PRICE_OPTIONS = [
    ("Без ограничений", None),
    ("До 1,000만 KRW", (0, 1000)),
    ("До 2,000만 KRW", (0, 2000)),
    ("До 3,000만 KRW", (0, 3000)),
    ("До 5,000만 KRW", (0, 5000)),
    ("1,000–3,000만 KRW", (1000, 3000)),
    ("2,000–5,000만 KRW", (2000, 5000)),
    ("5,000만+ KRW", (5000, 99999)),
]

YEAR_OPTIONS = [
    ("Без ограничений", None),
    ("2020+", (202001, 209912)),
    ("2022+", (202201, 209912)),
    ("2023+", (202301, 209912)),
    ("2018–2022", (201801, 202212)),
    ("2015–2020", (201501, 202012)),
]

MILEAGE_OPTIONS = [
    ("Без ограничений", None),
    ("До 30,000 км", (0, 30000)),
    ("До 50,000 км", (0, 50000)),
    ("До 100,000 км", (0, 100000)),
    ("До 150,000 км", (0, 150000)),
]

MANUFACTURERS_FALLBACK = [
    "기아", "현대", "제네시스",
    "BMW", "아우디", "메르세데스-벤츠", "볼보",
    "토요타", "렉서스", "혼다",
]


# ── Catalog helpers ────────────────────────────────────────────────────────────

def load_catalog() -> dict:
    if not os.path.exists(CATALOG_FILE):
        return {}
    with open(CATALOG_FILE, "r", encoding="utf-8") as f:
        return json.load(f)


def _mfr_data(catalog: dict) -> dict:
    if "manufacturers" in catalog:
        return catalog["manufacturers"]
    return {k: v for k, v in catalog.items() if not k.startswith("_")}


def get_manufacturers(catalog: dict) -> list[str]:
    return sorted(_mfr_data(catalog).keys()) if catalog else MANUFACTURERS_FALLBACK


def get_car_type(manufacturer: str, catalog: dict) -> str:
    entry = _mfr_data(catalog).get(manufacturer, {})
    return entry.get("car_type", "Y") if isinstance(entry, dict) else "Y"


def get_models(manufacturer: str, catalog: dict) -> list[str]:
    entry = _mfr_data(catalog).get(manufacturer, {})
    if isinstance(entry, dict):
        return entry.get("models", [])
    if isinstance(entry, list):
        return entry
    return []


def get_fuel_types(manufacturer: str, catalog: dict) -> list[str]:
    entry = _mfr_data(catalog).get(manufacturer, {})
    if isinstance(entry, dict):
        local = entry.get("fuel_types", [])
        if local:
            return local
    gf = catalog.get("_global_filters", {})
    return gf.get("FuelType", {}).get("values", [])


def get_regions(catalog: dict) -> list[str]:
    gf = catalog.get("_global_filters", {})
    regions = set(gf.get("OfficeCityState", {}).get("values", []))
    regions.update(gf.get("OfficeCityState_extra", {}).get("values", []))
    return sorted(regions)


# ── Filter helpers ─────────────────────────────────────────────────────────────

def _mileage_range(query: str):
    """Extract mileage range from query string for local filtering.
    The Encar API silently ignores Mileage in q=, so we filter client-side."""
    m = re.search(r"Mileage\.(\d+)\|(\d+)\.", query)
    return (int(m.group(1)), int(m.group(2))) if m else None


def build_filter(
    manufacturer: str,
    car_type: str = "Y",
    model=None,
    badge=None,
    fuel_type=None,
    region=None,
    price=None,
    mileage=None,
    color=None,
) -> str:
    """Build the Encar API query string.

    Year is intentionally excluded — the API returns 404 for any Year filter
    in the q= parameter. Year filtering is applied client-side after fetching.
    Mileage is included in the query but also filtered client-side (API ignores it).
    """
    extra = []
    if model:
        extra.append(f"Model.{model}.")
    if badge:
        extra.append(f"Badge.{badge}.")
    if fuel_type:
        extra.append(f"FuelType.{fuel_type}.")
    if region:
        extra.append(f"OfficeCityState.{region}.")
    if price:
        extra.append(f"Price.{price[0]}|{price[1]}.")
    if mileage:
        extra.append(f"Mileage.{mileage[0]}|{mileage[1]}.")
    if color:
        extra.append(f"Color.{color}.")
    extra_str = "".join(f"_.{c}" for c in extra)
    return f"(And.(And.Hidden.N._.(C.CarType.{car_type}._.Manufacturer.{manufacturer}.)){extra_str})"


def parse_filter_label(item) -> str:
    """Return a human-readable English label for a stored filter item.

    A filter item is either a plain query string (legacy) or a dict
    {"q": "...", "year": [lo, hi]} when a year constraint is set.
    """
    if isinstance(item, dict):
        query = item.get("q", "")
        year_range = item.get("year")
    else:
        query = item
        year_range = None

    parts = []
    if m := re.search(r"Manufacturer\.([^.]+)\.", query):
        kr = m.group(1)
        parts.append(MANUFACTURER_EN.get(kr, kr))
    if m := re.search(r"Model\.([^.]+)\.", query):
        parts.append(translate_model(m.group(1)))
    if m := re.search(r"Badge\.([^.]+)\.", query):
        parts.append(m.group(1))
    if m := re.search(r"FuelType\.([^.]+)\.", query):
        kr = m.group(1)
        parts.append(FUEL_TYPE_EN.get(kr, kr))
    if m := re.search(r"OfficeCityState\.([^.]+)\.", query):
        kr = m.group(1)
        parts.append(REGION_EN.get(kr, kr))
    if m := re.search(r"Price\.(\d+)\|(\d+)\.", query):
        lo, hi = int(m.group(1)), int(m.group(2))
        if lo == 0:
            parts.append(f"до {hi:,}만 KRW")
        elif hi >= 99999:
            parts.append(f"{lo:,}만+ KRW")
        else:
            parts.append(f"{lo:,}–{hi:,}만 KRW")
    if year_range:
        lo_s, hi_s = str(year_range[0]).zfill(6), str(year_range[1]).zfill(6)
        lo_fmt = f"{lo_s[:4]}.{lo_s[4:]}"
        hi_fmt = f"{hi_s[:4]}.{hi_s[4:]}"
        if int(hi_s[:4]) >= 2090:
            parts.append(f"{lo_fmt}+")
        else:
            parts.append(f"{lo_fmt}–{hi_fmt}")
    if m := re.search(r"Mileage\.(\d+)\|(\d+)\.", query):
        hi = int(m.group(2))
        parts.append(f"до {hi:,} км")
    if m := re.search(r"Color\.([^.]+)\.", query):
        kr = m.group(1)
        parts.append(COLOR_EN.get(kr, kr))
    return " | ".join(parts) if parts else query[:60]


def _user_display(user_id: int) -> str:
    info = load_user_info(user_id)
    name = info.get("first_name", str(user_id))
    uname = info.get("username")
    return f"{name} (@{uname})" if uname else f"{name} [{user_id}]"


# ── Browse helpers ─────────────────────────────────────────────────────────────

BROWSE_PAGE = 30


def _car_line(car: dict, idx: int) -> str:
    year_raw = str(int(car.get("Year") or 0)).zfill(6)
    year = f"{year_raw[:4]}.{year_raw[4:]}" if len(year_raw) >= 6 and year_raw[4:] != "00" else year_raw[:4]
    mileage = int(car.get("Mileage") or 0)
    price = int(car.get("Price") or 0)
    region = REGION_EN.get(car.get("OfficeCityState", ""), car.get("OfficeCityState", ""))
    mfr = MANUFACTURER_EN.get(car.get("Manufacturer", ""), car.get("Manufacturer", ""))
    model = translate_model(car.get("Model", ""))
    url = get_listing_url(car)
    return f"{idx}. {mfr} {model} · {year} · {mileage:,} km · {price:,}만원 · {region}\n   {url}"


async def _send_browse_page(
    bot,
    bot_data: dict,
    user_id: int,
    filter_item,
    offset: int,
) -> None:
    if isinstance(filter_item, dict):
        q = filter_item["q"]
        year_range = filter_item.get("year")
    else:
        q = filter_item
        year_range = None

    try:
        total, cars = await asyncio.to_thread(fetch_page, q, offset, BROWSE_PAGE)
    except Exception as e:
        await bot.send_message(chat_id=user_id, text=f"⚠ Не удалось получить объявления: {e}")
        return

    if year_range:
        lo, hi = year_range
        cars = [c for c in cars if lo <= (c.get("Year") or 0) <= hi]

    mil_range = _mileage_range(q)
    if mil_range:
        lo, hi = mil_range
        cars = [c for c in cars if lo <= (c.get("Mileage") or 0) <= hi]

    if not cars:
        msg = "Объявлений по этому фильтру не найдено." if offset == 0 else "✅ Больше объявлений нет."
        await bot.send_message(chat_id=user_id, text=msg)
        return

    local_note = []
    if year_range:
        local_note.append("год")
    if mil_range:
        local_note.append("пробег")
    header = f"📋 *{total:,} объявлений* · показано {offset + 1}–{offset + len(cars)}"
    if local_note:
        header += f" _({', '.join(local_note)} отфильтровано локально)_"
    lines = [header, ""]
    for i, car in enumerate(cars, offset + 1):
        lines.append(_car_line(car, i))

    # Split if too long (Telegram limit 4096)
    text = "\n".join(lines)
    if len(text) > 4000:
        text = text[:4000] + "\n…"

    next_offset = offset + len(cars)
    has_more = next_offset < total
    kb_rows = []
    if has_more:
        remaining = total - next_offset
        bot_data[f"browse_{user_id}"] = filter_item
        kb_rows.append([InlineKeyboardButton(
            f"Следующие {BROWSE_PAGE} → (ещё {remaining:,})",
            callback_data=f"brw_next:{next_offset}",
        )])
    kb_rows.append([InlineKeyboardButton("✓ Готово", callback_data="brw_stop")])

    await bot.send_message(
        chat_id=user_id,
        text=text,
        parse_mode="Markdown",
        reply_markup=InlineKeyboardMarkup(kb_rows),
        disable_web_page_preview=True,
    )


async def _seed_and_show(
    bot,
    bot_data: dict,
    user_id: int,
    filter_item,
) -> None:
    """Seed seen IDs from current listings, then show first page of results."""
    q = filter_item["q"] if isinstance(filter_item, dict) else filter_item
    year_range = filter_item.get("year") if isinstance(filter_item, dict) else None

    # Fetch a large batch to seed as many existing IDs as possible
    try:
        _, seed_cars = await asyncio.to_thread(fetch_page, q, 0, 100)
    except Exception as e:
        log.error("Seed fetch error user=%s: %s", user_id, e)
        seed_cars = []

    if year_range:
        lo, hi = year_range
        seed_cars = [c for c in seed_cars if lo <= (c.get("Year") or 0) <= hi]

    mil_range = _mileage_range(q)
    if mil_range:
        lo, hi = mil_range
        seed_cars = [c for c in seed_cars if lo <= (c.get("Mileage") or 0) <= hi]

    seen_ids: set[str] = bot_data.setdefault(
        f"seen_{user_id}", load_seen_ids(user_id)
    )
    for car in seed_cars:
        car_id = str(car.get("Id", ""))
        if car_id:
            seen_ids.add(car_id)
    if seed_cars:
        save_seen_ids(user_id, seen_ids)
        log.info("Seeded %d IDs for user=%s", len(seed_cars), user_id)

    # Store filter for pagination and show first page
    bot_data[f"browse_{user_id}"] = filter_item
    await _send_browse_page(bot, bot_data, user_id, filter_item, offset=0)


# ── Keyboards ──────────────────────────────────────────────────────────────────

def manufacturers_kb(catalog: dict) -> InlineKeyboardMarkup:
    items = get_manufacturers(catalog)
    rows, row = [], []
    for mfr in items:
        label = MANUFACTURER_EN.get(mfr, mfr)
        row.append(InlineKeyboardButton(label, callback_data=f"mfr:{mfr}"))
        if len(row) == 3:
            rows.append(row)
            row = []
    if row:
        rows.append(row)
    return InlineKeyboardMarkup(rows)


def paged_kb(
    items: list[str],
    prefix: str,
    per_page: int,
    page: int,
    skip_label: str,
    labels: list[str] | None = None,
) -> InlineKeyboardMarkup:
    start = page * per_page
    page_items = items[start: start + per_page]
    page_labels = labels[start: start + per_page] if labels else page_items
    total_pages = (len(items) + per_page - 1) // per_page

    rows, row = [], []
    for i, (_, label) in enumerate(zip(page_items, page_labels)):
        idx = start + i
        row.append(InlineKeyboardButton(label, callback_data=f"{prefix}:{idx}"))
        if len(row) == 2:
            rows.append(row)
            row = []
    if row:
        rows.append(row)

    nav = []
    if page > 0:
        nav.append(InlineKeyboardButton("← Назад", callback_data=f"{prefix}_pg:{page - 1}"))
    if page < total_pages - 1:
        nav.append(InlineKeyboardButton("Вперёд →", callback_data=f"{prefix}_pg:{page + 1}"))
    if nav:
        rows.append(nav)

    rows.append([InlineKeyboardButton(f"— {skip_label} —", callback_data=f"{prefix}_skip")])
    return InlineKeyboardMarkup(rows)


def options_kb(options: list[tuple], prefix: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton(label, callback_data=f"{prefix}:{i}")]
        for i, (label, _) in enumerate(options)
    ])


def filters_delete_kb(filters: list[str]) -> InlineKeyboardMarkup:
    rows = [
        [InlineKeyboardButton(f"🗑 {parse_filter_label(f)}", callback_data=f"del:{i}")]
        for i, f in enumerate(filters)
    ]
    rows.append([InlineKeyboardButton("Отмена", callback_data="del_cancel")])
    return InlineKeyboardMarkup(rows)


# ── Admin keyboards ────────────────────────────────────────────────────────────

def admin_user_list_kb(bot_data: dict) -> InlineKeyboardMarkup:
    """Keyboard listing all users that have at least one filter."""
    users = list_all_users()
    rows = []
    for uid in sorted(users):
        filters = load_filters(uid)
        if not filters:
            continue
        paused = bot_data.get(f"paused_{uid}", False)
        icon = "⏸" if paused else "▶️"
        label = f"{icon} {_user_display(uid)} — {len(filters)} фильтр(ов)"
        rows.append([InlineKeyboardButton(label, callback_data=f"adm_user:{uid}")])
    if not rows:
        rows.append([InlineKeyboardButton("(нет активных пользователей)", callback_data="adm_noop")])
    return InlineKeyboardMarkup(rows)


def admin_user_detail_kb(user_id: int, bot_data: dict) -> InlineKeyboardMarkup:
    filters = load_filters(user_id)
    paused = bot_data.get(f"paused_{user_id}", False)
    rows = [
        [InlineKeyboardButton(
            f"🗑 {i + 1}. {parse_filter_label(f)}",
            callback_data=f"adm_del:{user_id}:{i}",
        )]
        for i, f in enumerate(filters)
    ]
    toggle_label = "▶️ Возобновить" if paused else "⏸ Пауза"
    rows.append([
        InlineKeyboardButton("🗑 Удалить ВСЕ фильтры", callback_data=f"adm_delall:{user_id}"),
        InlineKeyboardButton(toggle_label, callback_data=f"adm_toggle:{user_id}"),
    ])
    rows.append([InlineKeyboardButton("← Назад", callback_data="adm_back")])
    return InlineKeyboardMarkup(rows)


# ── Command handlers ───────────────────────────────────────────────────────────

async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user = update.effective_user
    save_user_info(user.id, user.first_name, user.username)
    if WEBAPP_URL:
        try:
            await context.bot.set_chat_menu_button(
                chat_id=user.id,
                menu_button=MenuButtonWebApp(
                    text="🔍 Фильтры",
                    web_app=WebAppInfo(url=WEBAPP_URL),
                ),
            )
        except Exception as e:
            log.warning("Menu button for user %s: %s", user.id, e)
    await update.message.reply_text(
        "🚗 *Encar Scraper Bot*\n\n"
        "Бот постоянно мониторит encar.com и мгновенно уведомляет вас о новых объявлениях. "
        "Список команд:\n\n"
        "/add — создать фильтр\n"
        "/filters — активные фильтры\n"
        "/delete — удалить фильтр\n"
        "/status — статус бота\n"
        "/pause — приостановить уведомления\n"
        "/resume — возобновить уведомления",
        parse_mode="Markdown",
    )


async def cmd_help(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.reply_text(
        "🚗 *Encar Scraper Bot*\n\n"
        "/add — добавить фильтр\n"
        "/link — личная ссылка на конструктор фильтров\n"
        "/filters — список активных фильтров\n"
        "/delete — удалить фильтр\n"
        "/status — статус бота\n"
        "/pause — приостановить\n"
        "/resume — возобновить\n"
        "/cancel — отменить текущее действие",
        parse_mode="Markdown",
    )


async def cmd_filters(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user_id = update.effective_user.id
    filters = load_filters(user_id)
    if not filters:
        await update.message.reply_text("Нет активных фильтров. Добавьте через /add")
        return
    lines = [f"`{i + 1}.` {parse_filter_label(f)}" for i, f in enumerate(filters)]
    await update.message.reply_text("\n".join(lines), parse_mode="Markdown")


async def cmd_status(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user_id = update.effective_user.id
    seen = context.bot_data.get(f"seen_{user_id}", load_seen_ids(user_id))
    filters = load_filters(user_id)
    last = context.bot_data.get(f"last_check_{user_id}", "ещё не запускался")
    paused = context.bot_data.get(f"paused_{user_id}", False)
    state = "⏸ На паузе" if paused else "✅ Активен"
    await update.message.reply_text(
        f"{state}\n"
        f"Фильтры: {len(filters)}\n"
        f"Известных авто: {len(seen)}\n"
        f"Последняя проверка: {last}"
    )


async def cmd_link(update: Update, _: ContextTypes.DEFAULT_TYPE) -> None:
    """Send the user a personal link to the standalone filter builder page."""
    if not WEBAPP_URL:
        await update.message.reply_text("⚠ WEBAPP_URL не задан. Запустите сервер с URL туннеля.")
        return
    user = update.effective_user
    save_user_info(user.id, user.first_name, user.username)
    token = hmac.new(
        os.environ.get("TELEGRAM_BOT_TOKEN", "").encode(),
        str(user.id).encode(),
        hashlib.sha256,
    ).hexdigest()[:24]
    url = f"{WEBAPP_URL}/add?uid={user.id}&tok={token}"
    await update.message.reply_text(
        f"🔗 Ваша личная ссылка на конструктор фильтров:\n{url}\n\n"
        "Откройте в любом браузере, настройте фильтр и нажмите «Добавить фильтр».",
        disable_web_page_preview=True,
    )


async def cmd_pause(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user_id = update.effective_user.id
    context.bot_data[f"paused_{user_id}"] = True
    await update.message.reply_text("⏸ Поиск приостановлен. Используйте /resume для продолжения.")


async def cmd_resume(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user_id = update.effective_user.id
    context.bot_data[f"paused_{user_id}"] = False
    await update.message.reply_text("▶️ Поиск возобновлён.")


async def cmd_delete(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user_id = update.effective_user.id
    filters = load_filters(user_id)
    if not filters:
        await update.message.reply_text("Нет фильтров для удаления.")
        return
    await update.message.reply_text(
        "Выберите фильтр для удаления:",
        reply_markup=filters_delete_kb(filters),
    )


async def on_delete(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()
    user_id = query.from_user.id
    if query.data == "del_cancel":
        await query.edit_message_text("Отменено.")
        return
    idx = int(query.data.split(":")[1])
    filters = load_filters(user_id)
    if idx >= len(filters):
        await query.edit_message_text("Фильтр не найден.")
        return
    label = parse_filter_label(filters.pop(idx))
    save_filters(user_id, filters)
    await query.edit_message_text(f"✅ Удалён: {label}")


# ── Admin handlers ─────────────────────────────────────────────────────────────

def _is_admin(update: Update) -> bool:
    return update.effective_user.id == ADMIN_ID


async def cmd_admin(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not _is_admin(update):
        await update.message.reply_text("⛔ Только для администратора.")
        return
    users = [u for u in list_all_users() if load_filters(u)]
    await update.message.reply_text(
        f"👑 *Панель администратора*\n{len(users)} польз. с активными фильтрами:",
        parse_mode="Markdown",
        reply_markup=admin_user_list_kb(context.bot_data),
    )


async def on_admin_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()

    if not _is_admin(update):
        await query.edit_message_text("⛔ Только для администратора.")
        return

    data = query.data

    if data == "adm_back":
        users = [u for u in list_all_users() if load_filters(u)]
        await query.edit_message_text(
            f"👑 *Панель администратора*\n{len(users)} польз. с активными фильтрами:",
            parse_mode="Markdown",
            reply_markup=admin_user_list_kb(context.bot_data),
        )

    elif data == "adm_noop":
        pass

    elif data.startswith("adm_user:"):
        uid = int(data.split(":")[1])
        filters = load_filters(uid)
        paused = context.bot_data.get(f"paused_{uid}", False)
        last = context.bot_data.get(f"last_check_{uid}", "—")
        state = "⏸ На паузе" if paused else "▶️ Активен"
        await query.edit_message_text(
            f"👤 *{_user_display(uid)}*\n"
            f"Статус: {state}\n"
            f"Фильтры: {len(filters)}\n"
            f"Последняя проверка: {last}\n\n"
            "Нажмите на фильтр для удаления:",
            parse_mode="Markdown",
            reply_markup=admin_user_detail_kb(uid, context.bot_data),
        )

    elif data.startswith("adm_del:"):
        _, uid_s, idx_s = data.split(":")
        uid, idx = int(uid_s), int(idx_s)
        filters = load_filters(uid)
        if idx < len(filters):
            label = parse_filter_label(filters.pop(idx))
            save_filters(uid, filters)
            msg = f"✅ Фильтр удалён: {label}"
        else:
            msg = "⚠ Фильтр не найден."
        if load_filters(uid):
            await query.edit_message_text(
                msg,
                reply_markup=admin_user_detail_kb(uid, context.bot_data),
            )
        else:
            users = [u for u in list_all_users() if load_filters(u)]
            await query.edit_message_text(
                f"{msg}\n\n👑 *Панель администратора*\n{len(users)} польз. с активными фильтрами:",
                parse_mode="Markdown",
                reply_markup=admin_user_list_kb(context.bot_data),
            )

    elif data.startswith("adm_delall:"):
        uid = int(data.split(":")[1])
        save_filters(uid, [])
        users = [u for u in list_all_users() if load_filters(u)]
        await query.edit_message_text(
            f"✅ Все фильтры удалены для {_user_display(uid)}\n\n"
            f"👑 *Панель администратора*\n{len(users)} польз. с активными фильтрами:",
            parse_mode="Markdown",
            reply_markup=admin_user_list_kb(context.bot_data),
        )

    elif data.startswith("adm_toggle:"):
        uid = int(data.split(":")[1])
        current = context.bot_data.get(f"paused_{uid}", False)
        context.bot_data[f"paused_{uid}"] = not current
        action = "возобновлён" if current else "приостановлен"
        filters = load_filters(uid)
        paused = not current
        last = context.bot_data.get(f"last_check_{uid}", "—")
        state = "⏸ На паузе" if paused else "▶️ Активен"
        await query.edit_message_text(
            f"✅ Мониторинг {action} для {_user_display(uid)}\n\n"
            f"👤 *{_user_display(uid)}*\n"
            f"Статус: {state}\n"
            f"Фильтры: {len(filters)}\n"
            f"Последняя проверка: {last}\n\n"
            "Нажмите на фильтр для удаления:",
            parse_mode="Markdown",
            reply_markup=admin_user_detail_kb(uid, context.bot_data),
        )


# ── Add filter conversation ────────────────────────────────────────────────────

def _summary(f: dict) -> str:
    mfr = f["manufacturer"]
    parts = [MANUFACTURER_EN.get(mfr, mfr)]
    if f.get("model"):
        parts.append(translate_model(f["model"]))
    if f.get("fuel_type"):
        parts.append(FUEL_TYPE_EN.get(f["fuel_type"], f["fuel_type"]))
    if f.get("region"):
        parts.append(REGION_EN.get(f["region"], f["region"]))
    return " | ".join(parts)


async def cmd_add(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    user = update.effective_user
    save_user_info(user.id, user.first_name, user.username)

    if WEBAPP_URL:
        kb = ReplyKeyboardMarkup(
            [[KeyboardButton("🔍 Открыть конструктор фильтров", web_app=WebAppInfo(url=WEBAPP_URL))]],
            resize_keyboard=True,
            one_time_keyboard=True,
        )
        await update.message.reply_text(
            "Откройте визуальный конструктор или используйте меню ниже:",
            reply_markup=kb,
        )

    catalog = load_catalog()
    context.user_data["catalog"] = catalog
    context.user_data["filter"] = {}
    await update.message.reply_text(
        "Выберите производителя:",
        reply_markup=manufacturers_kb(catalog),
    )
    return MANUFACTURER


async def on_manufacturer(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()
    mfr = query.data.split(":", 1)[1]
    mfr_en = MANUFACTURER_EN.get(mfr, mfr)
    catalog = context.user_data.get("catalog", {})
    context.user_data["filter"]["manufacturer"] = mfr
    context.user_data["filter"]["car_type"] = get_car_type(mfr, catalog)
    models = get_models(mfr, catalog)

    if models:
        model_labels = [translate_model(m) for m in models]
        context.user_data["models"] = models
        context.user_data["model_labels"] = model_labels
        await query.edit_message_text(
            f"*{mfr_en}* — выберите модель:",
            parse_mode="Markdown",
            reply_markup=paged_kb(models, "mdl", MODELS_PER_PAGE, 0, "Все модели", labels=model_labels),
        )
        return MODEL
    else:
        fuel_types = get_fuel_types(mfr, catalog)
        if fuel_types:
            fuel_labels = [FUEL_TYPE_EN.get(f, f) for f in fuel_types]
            context.user_data["fuel_types"] = fuel_types
            context.user_data["fuel_labels"] = fuel_labels
            await query.edit_message_text(
                f"*{mfr_en}* — выберите тип топлива:",
                parse_mode="Markdown",
                reply_markup=paged_kb(fuel_types, "fuel", len(fuel_types), 0, "Любой тип топлива", labels=fuel_labels),
            )
            return FUEL_TYPE
        return await _ask_region(query, context)


async def on_model(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()
    idx = int(query.data.split(":")[1])
    models = context.user_data.get("models", [])
    context.user_data["filter"]["model"] = models[idx] if idx < len(models) else None
    mfr = context.user_data["filter"]["manufacturer"]
    catalog = context.user_data.get("catalog", {})
    fuel_types = get_fuel_types(mfr, catalog)
    if fuel_types:
        fuel_labels = [FUEL_TYPE_EN.get(f, f) for f in fuel_types]
        context.user_data["fuel_types"] = fuel_types
        context.user_data["fuel_labels"] = fuel_labels
        await query.edit_message_text(
            f"*{_summary(context.user_data['filter'])}* — тип топлива:",
            parse_mode="Markdown",
            reply_markup=paged_kb(fuel_types, "fuel", len(fuel_types), 0, "Любой тип топлива", labels=fuel_labels),
        )
        return FUEL_TYPE
    return await _ask_region(query, context)


async def on_model_page(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()
    page = int(query.data.split(":")[1])
    models = context.user_data.get("models", [])
    model_labels = context.user_data.get("model_labels", models)
    await query.edit_message_text(
        "Выберите модель:",
        reply_markup=paged_kb(models, "mdl", MODELS_PER_PAGE, page, "Все модели", labels=model_labels),
    )
    return MODEL


async def on_model_skip(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()
    context.user_data["filter"]["model"] = None
    mfr = context.user_data["filter"]["manufacturer"]
    mfr_en = MANUFACTURER_EN.get(mfr, mfr)
    catalog = context.user_data.get("catalog", {})
    fuel_types = get_fuel_types(mfr, catalog)
    if fuel_types:
        fuel_labels = [FUEL_TYPE_EN.get(f, f) for f in fuel_types]
        context.user_data["fuel_types"] = fuel_types
        context.user_data["fuel_labels"] = fuel_labels
        await query.edit_message_text(
            f"*{mfr_en}* — тип топлива:",
            parse_mode="Markdown",
            reply_markup=paged_kb(fuel_types, "fuel", len(fuel_types), 0, "Любой тип топлива", labels=fuel_labels),
        )
        return FUEL_TYPE
    return await _ask_region(query, context)


async def on_fuel(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()
    idx = int(query.data.split(":")[1])
    fuel_types = context.user_data.get("fuel_types", [])
    context.user_data["filter"]["fuel_type"] = fuel_types[idx] if idx < len(fuel_types) else None
    return await _ask_region(query, context)


async def on_fuel_skip(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()
    context.user_data["filter"]["fuel_type"] = None
    return await _ask_region(query, context)


async def _ask_region(query, context: ContextTypes.DEFAULT_TYPE) -> int:
    catalog = context.user_data.get("catalog", {})
    regions = get_regions(catalog)
    if regions:
        region_labels = [REGION_EN.get(r, r) for r in regions]
        context.user_data["regions"] = regions
        context.user_data["region_labels"] = region_labels
        await query.edit_message_text(
            f"*{_summary(context.user_data['filter'])}* — регион:",
            parse_mode="Markdown",
            reply_markup=paged_kb(regions, "reg", REGIONS_PER_PAGE, 0, "Любой регион", labels=region_labels),
        )
        return REGION
    return await _ask_price(query, context)


async def on_region(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()
    idx = int(query.data.split(":")[1])
    regions = context.user_data.get("regions", [])
    context.user_data["filter"]["region"] = regions[idx] if idx < len(regions) else None
    return await _ask_price(query, context)


async def on_region_page(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()
    page = int(query.data.split(":")[1])
    regions = context.user_data.get("regions", [])
    region_labels = context.user_data.get("region_labels", regions)
    await query.edit_message_text(
        "Выберите регион:",
        reply_markup=paged_kb(regions, "reg", REGIONS_PER_PAGE, page, "Любой регион", labels=region_labels),
    )
    return REGION


async def on_region_skip(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()
    context.user_data["filter"]["region"] = None
    return await _ask_price(query, context)


async def _ask_price(query, context: ContextTypes.DEFAULT_TYPE) -> int:
    await query.edit_message_text(
        f"*{_summary(context.user_data['filter'])}* — цена:",
        parse_mode="Markdown",
        reply_markup=options_kb(PRICE_OPTIONS, "price"),
    )
    return PRICE


async def on_price(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()
    context.user_data["filter"]["price"] = PRICE_OPTIONS[int(query.data.split(":")[1])][1]
    await query.edit_message_text(
        "Выберите диапазон года:",
        reply_markup=options_kb(YEAR_OPTIONS, "year"),
    )
    return YEAR


async def on_year(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()
    context.user_data["filter"]["year"] = YEAR_OPTIONS[int(query.data.split(":")[1])][1]
    await query.edit_message_text(
        "Выберите ограничение пробега:",
        reply_markup=options_kb(MILEAGE_OPTIONS, "mil"),
    )
    return MILEAGE


async def on_mileage(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()
    user_id = query.from_user.id
    f = context.user_data["filter"]
    f["mileage"] = MILEAGE_OPTIONS[int(query.data.split(":")[1])][1]

    # Year is NOT sent to the API (causes 404); stored as metadata for
    # client-side filtering after the results are fetched.
    api_query = build_filter(
        manufacturer=f["manufacturer"],
        car_type=f.get("car_type", "Y"),
        model=f.get("model"),
        badge=f.get("badge"),
        fuel_type=f.get("fuel_type"),
        region=f.get("region"),
        price=f.get("price"),
        mileage=f.get("mileage"),
    )
    year = f.get("year")
    filter_item = {"q": api_query, "year": list(year)} if year else api_query

    filters = load_filters(user_id)
    filters.append(filter_item)
    save_filters(user_id, filters)

    await query.edit_message_text(
        f"✅ Фильтр добавлен: *{parse_filter_label(filter_item)}*\n\n"
        "Буду уведомлять о новых объявлениях.",
        parse_mode="Markdown",
    )
    await _seed_and_show(context.bot, context.bot_data, user_id, filter_item)
    return ConversationHandler.END


async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    await update.message.reply_text("Отменено.")
    return ConversationHandler.END


async def on_browse_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()
    user_id = query.from_user.id

    if query.data == "brw_stop":
        await query.edit_message_reply_markup(reply_markup=None)
        return

    # brw_next:{offset}
    offset = int(query.data.split(":")[1])
    filter_item = context.bot_data.get(f"browse_{user_id}")
    if not filter_item:
        await query.edit_message_text("Сессия истекла. Используйте /filters и пересоздайте фильтр.")
        return

    await query.edit_message_reply_markup(reply_markup=None)
    await _send_browse_page(context.bot, context.bot_data, user_id, filter_item, offset)


# ── Web App data handler ───────────────────────────────────────────────────────

async def on_webapp_data(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Receive filter JSON submitted by the Mini App via sendData()."""
    user = update.effective_user
    save_user_info(user.id, user.first_name, user.username)

    try:
        payload = json.loads(update.message.web_app_data.data)
    except (json.JSONDecodeError, AttributeError):
        await update.message.reply_text("⚠ Не удалось разобрать данные фильтра из веб-приложения.")
        return

    mfr = payload.get("manufacturer")
    if not mfr:
        await update.message.reply_text("⚠ Производитель обязателен.")
        return

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
    filter_item = {"q": api_query, "year": list(year)} if year else api_query

    filters_list = load_filters(user.id)
    filters_list.append(filter_item)
    save_filters(user.id, filters_list)

    await update.message.reply_text(
        f"✅ Фильтр добавлен: *{parse_filter_label(filter_item)}*\n\n"
        "Буду уведомлять о новых объявлениях.",
        parse_mode="Markdown",
    )
    await _seed_and_show(context.bot, context.bot_data, user.id, filter_item)


# ── Scraper job ────────────────────────────────────────────────────────────────

async def scraper_job(context: ContextTypes.DEFAULT_TYPE) -> None:
    all_users = list_all_users()
    if not all_users:
        return

    now = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    total_new = 0

    for user_id in all_users:
        if context.bot_data.get(f"paused_{user_id}", False):
            continue

        filters = load_filters(user_id)
        if not filters:
            continue

        seen_ids: set[str] = context.bot_data.setdefault(
            f"seen_{user_id}", load_seen_ids(user_id)
        )
        new_ids: set[str] = set()

        # Collect all new cars across all filters, deduped
        new_cars: list[dict] = []
        for filter_item in filters:
            if isinstance(filter_item, dict):
                filter_query = filter_item["q"]
                year_range = filter_item.get("year")
            else:
                filter_query = filter_item
                year_range = None

            try:
                cars = await asyncio.to_thread(fetch_cars, filter_query)
            except Exception as e:
                log.error("Fetch error user=%s: %s", user_id, e)
                continue

            if year_range:
                lo, hi = year_range
                cars = [c for c in cars if lo <= (c.get("Year") or 0) <= hi]

            mil_range = _mileage_range(filter_query)
            if mil_range:
                lo, hi = mil_range
                cars = [c for c in cars if lo <= (c.get("Mileage") or 0) <= hi]

            for car in cars:
                car_id = str(car.get("Id", ""))
                if car_id and car_id not in seen_ids and car_id not in {str(c.get("Id")) for c in new_cars}:
                    new_cars.append(car)
                    new_ids.add(car_id)

        # Send all new cars, splitting only at Telegram's 4096 char limit
        if new_cars:
            header = f"🔔 *Найдено новых объявлений: {len(new_cars)}*\n"
            messages, current = [], header
            for i, car in enumerate(new_cars, 1):
                line = _car_line(car, i) + "\n"
                if len(current) + len(line) > 4000:
                    messages.append(current)
                    current = line
                else:
                    current += line
            messages.append(current)
            for msg in messages:
                try:
                    await context.bot.send_message(
                        chat_id=user_id, text=msg,
                        parse_mode="Markdown", disable_web_page_preview=True,
                    )
                except Exception as e:
                    log.error("Send error user=%s: %s", user_id, e)
            log.info("Alert sent to user=%s cars=%d msgs=%d", user_id, len(new_cars), len(messages))

        if new_ids:
            seen_ids |= new_ids
            save_seen_ids(user_id, seen_ids)
            total_new += len(new_ids)

        context.bot_data[f"last_check_{user_id}"] = now

    log.info("Scraper cycle done. Users: %d, new listings: %d", len(all_users), total_new)


# ── App builder ────────────────────────────────────────────────────────────────

async def post_init(application: Application) -> None:
    # Pre-load seen IDs for all existing users into bot_data cache
    all_users = list_all_users()
    for uid in all_users:
        application.bot_data[f"seen_{uid}"] = load_seen_ids(uid)
    log.info("Loaded seen_ids for %d user(s)", len(all_users))
    if WEBAPP_URL:
        menu_button = MenuButtonWebApp(
            text="🔍 Фильтры",
            web_app=WebAppInfo(url=WEBAPP_URL),
        )
        # Set global default
        await application.bot.set_chat_menu_button(menu_button=menu_button)
        # Also update every known user's chat explicitly so desktop clients refresh
        for uid in all_users:
            try:
                await application.bot.set_chat_menu_button(
                    chat_id=uid, menu_button=menu_button
                )
            except Exception as e:
                log.warning("Menu button for user %s: %s", uid, e)
        log.info("Menu button set for %d user(s) → %s", len(all_users), WEBAPP_URL)


def build_app(token: str) -> Application:
    app = Application.builder().token(token).post_init(post_init).build()

    conv = ConversationHandler(
        entry_points=[CommandHandler("add", cmd_add)],
        states={
            MANUFACTURER: [CallbackQueryHandler(on_manufacturer, pattern=r"^mfr:")],
            MODEL: [
                CallbackQueryHandler(on_model, pattern=r"^mdl:\d+$"),
                CallbackQueryHandler(on_model_page, pattern=r"^mdl_pg:"),
                CallbackQueryHandler(on_model_skip, pattern=r"^mdl_skip$"),
            ],
            FUEL_TYPE: [
                CallbackQueryHandler(on_fuel, pattern=r"^fuel:\d+$"),
                CallbackQueryHandler(on_fuel_skip, pattern=r"^fuel_skip$"),
            ],
            REGION: [
                CallbackQueryHandler(on_region, pattern=r"^reg:\d+$"),
                CallbackQueryHandler(on_region_page, pattern=r"^reg_pg:"),
                CallbackQueryHandler(on_region_skip, pattern=r"^reg_skip$"),
            ],
            PRICE: [CallbackQueryHandler(on_price, pattern=r"^price:")],
            YEAR: [CallbackQueryHandler(on_year, pattern=r"^year:")],
            MILEAGE: [CallbackQueryHandler(on_mileage, pattern=r"^mil:")],
        },
        fallbacks=[CommandHandler("cancel", cancel)],
    )

    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("link", cmd_link))
    app.add_handler(CommandHandler("help", cmd_help))
    app.add_handler(CommandHandler("filters", cmd_filters))
    app.add_handler(CommandHandler("delete", cmd_delete))
    app.add_handler(CommandHandler("status", cmd_status))
    app.add_handler(CommandHandler("pause", cmd_pause))
    app.add_handler(CommandHandler("resume", cmd_resume))
    app.add_handler(CommandHandler("admin", cmd_admin))
    app.add_handler(CallbackQueryHandler(on_delete, pattern=r"^del"))
    app.add_handler(CallbackQueryHandler(on_admin_callback, pattern=r"^adm_"))
    app.add_handler(CallbackQueryHandler(on_browse_callback, pattern=r"^brw"))
    app.add_handler(MessageHandler(tg_filters.StatusUpdate.WEB_APP_DATA, on_webapp_data))
    app.add_handler(conv)

    app.job_queue.run_repeating(scraper_job, interval=60, first=5)

    return app
