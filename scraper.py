import requests

API_URL = "https://api.encar.com/search/car/list/general"
PAGE_SIZE = 100

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/120.0.0.0 Safari/537.36"
    ),
    "Referer": "https://www.encar.com/",
}


def fetch_page(filter_query: str, offset: int = 0, limit: int = 20) -> tuple[int, list[dict]]:
    """Fetch a single page. Returns (total_count, cars)."""
    params = {
        "count": "true",
        "q": filter_query,
        "sr": f"|ModifiedDate|{offset}|{limit}",
    }
    r = requests.get(API_URL, params=params, headers=HEADERS, timeout=15)
    r.raise_for_status()
    data = r.json()
    return data.get("Count", 0), data.get("SearchResults", [])


def fetch_cars(filter_query: str) -> list[dict]:
    _, cars = fetch_page(filter_query, offset=0, limit=PAGE_SIZE)
    return cars


def get_photo_url(car: dict) -> str | None:
    photos = car.get("Photos")
    if not photos:
        return None
    location = photos[0].get("location", "")
    if not location:
        return None
    return f"https://ci.encar.com{location}"


def get_listing_url(car: dict) -> str:
    return f"https://fem.encar.com/cars/detail/{car['Id']}"
