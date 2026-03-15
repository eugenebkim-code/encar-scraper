"""Per-user persistent storage.

Layout on disk:
    users/
        {user_id}/
            info.json       ← name, username (set on /start)
            filters.json    ← list of filter query strings
            seen_ids.json   ← set of already-notified car IDs
"""

import json
import os

# DATA_DIR env var lets Railway (or any host) mount a persistent volume.
# Falls back to a local "users/" directory alongside this file.
_BASE = os.path.join(
    os.environ.get("DATA_DIR", os.path.dirname(__file__)),
    "users",
)


# ── Internal helpers ───────────────────────────────────────────────────────────

def _user_dir(user_id: int) -> str:
    path = os.path.join(_BASE, str(user_id))
    os.makedirs(path, exist_ok=True)
    return path


def _path(user_id: int, filename: str) -> str:
    return os.path.join(_user_dir(user_id), filename)


# ── User info ──────────────────────────────────────────────────────────────────

def save_user_info(user_id: int, first_name: str, username: str | None) -> None:
    with open(_path(user_id, "info.json"), "w", encoding="utf-8") as f:
        json.dump({"first_name": first_name, "username": username}, f, ensure_ascii=False)


def load_user_info(user_id: int) -> dict:
    p = _path(user_id, "info.json")
    if not os.path.exists(p):
        return {"first_name": str(user_id), "username": None}
    with open(p, encoding="utf-8") as f:
        return json.load(f)


# ── Filters ────────────────────────────────────────────────────────────────────

def load_filters(user_id: int) -> list[str]:
    p = _path(user_id, "filters.json")
    if not os.path.exists(p):
        return []
    with open(p, encoding="utf-8") as f:
        return json.load(f).get("filters", [])


def save_filters(user_id: int, filters: list[str]) -> None:
    with open(_path(user_id, "filters.json"), "w", encoding="utf-8") as f:
        json.dump({"filters": filters}, f, ensure_ascii=False, indent=2)


# ── Seen IDs ───────────────────────────────────────────────────────────────────

def load_seen_ids(user_id: int) -> set[str]:
    p = _path(user_id, "seen_ids.json")
    if not os.path.exists(p):
        return set()
    with open(p, encoding="utf-8") as f:
        return set(json.load(f))


def save_seen_ids(user_id: int, seen_ids: set[str]) -> None:
    with open(_path(user_id, "seen_ids.json"), "w", encoding="utf-8") as f:
        json.dump(list(seen_ids), f, ensure_ascii=False, indent=2)


# ── Directory listing ──────────────────────────────────────────────────────────

def list_all_users() -> list[int]:
    """Return IDs of every user that has a data directory."""
    if not os.path.exists(_BASE):
        return []
    return [int(d) for d in os.listdir(_BASE) if d.isdigit()]
