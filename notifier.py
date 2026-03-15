import os
import requests

TELEGRAM_API = "https://api.telegram.org/bot{token}/{method}"

BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "")
CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID", "")


def _api(method: str, payload: dict) -> dict:
    url = TELEGRAM_API.format(token=BOT_TOKEN, method=method)
    response = requests.post(url, json=payload, timeout=15)
    response.raise_for_status()
    return response.json()


def _build_caption(car: dict, listing_url: str) -> str:
    year_raw = str(car.get("Year", ""))
    year = year_raw[:4] if len(year_raw) >= 4 else year_raw

    mileage = car.get("Mileage", 0)
    price = car.get("Price", 0)

    return (
        f"🚗 {car.get('Manufacturer', '')} {car.get('Model', '')}\n"
        f"\n"
        f"Year: {year}\n"
        f"Mileage: {mileage:,} km\n"
        f"Price: {price} 만원\n"
        f"Location: {car.get('OfficeCityState', '')}\n"
        f"\n"
        f"{listing_url}"
    )


def send_car_alert(car: dict, listing_url: str, photo_url: str | None = None) -> None:
    caption = _build_caption(car, listing_url)

    if photo_url:
        _api("sendPhoto", {
            "chat_id": CHAT_ID,
            "photo": photo_url,
            "caption": caption,
        })
    else:
        _api("sendMessage", {
            "chat_id": CHAT_ID,
            "text": caption,
            "disable_web_page_preview": False,
        })
