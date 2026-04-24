"""
Interactive filter configurator for Encar scraper.
Run: python configure.py
"""

import json
import os

from InquirerPy import inquirer
from InquirerPy.validator import EmptyInputValidator

FILTERS_FILE = os.path.join(os.path.dirname(__file__), "filters.json")
CATALOG_FILE = os.path.join(os.path.dirname(__file__), "catalog.json")

MANUFACTURERS_FALLBACK = [
    "기아", "현대", "제네시스", "쉐보레(GM대우)", "르노코리아", "KG모빌리티",
    "BMW", "Mercedes-Benz", "Audi", "Volkswagen", "Volvo",
    "Toyota", "Lexus", "Honda", "Nissan",
]


def load_catalog() -> dict[str, list[str]]:
    if not os.path.exists(CATALOG_FILE):
        return {}
    with open(CATALOG_FILE, "r", encoding="utf-8") as f:
        data = json.load(f)
    return data.get("manufacturers", data)

PRICE_RANGES = [
    {"name": "No limit", "value": None},
    {"name": "Up to 1,000만 KRW", "value": (0, 1000)},
    {"name": "Up to 2,000만 KRW", "value": (0, 2000)},
    {"name": "Up to 3,000만 KRW", "value": (0, 3000)},
    {"name": "Up to 5,000만 KRW", "value": (0, 5000)},
    {"name": "1,000–3,000만 KRW", "value": (1000, 3000)},
    {"name": "2,000–5,000만 KRW", "value": (2000, 5000)},
    {"name": "Custom range", "value": "custom"},
]

YEAR_RANGES = [
    {"name": "No limit", "value": None},
    {"name": "2020+", "value": (202001, 209912)},
    {"name": "2022+", "value": (202201, 209912)},
    {"name": "2023+", "value": (202301, 209912)},
    {"name": "2018–2022", "value": (201801, 202212)},
    {"name": "Custom range", "value": "custom"},
]

MILEAGE_RANGES = [
    {"name": "No limit", "value": None},
    {"name": "Up to 30,000 km", "value": (0, 30000)},
    {"name": "Up to 50,000 km", "value": (0, 50000)},
    {"name": "Up to 100,000 km", "value": (0, 100000)},
    {"name": "Custom range", "value": "custom"},
]


def get_car_type(manufacturer: str, catalog: dict) -> str:
    entry = catalog.get(manufacturer, {})
    if isinstance(entry, dict):
        return entry.get("car_type", "Y")
    return "Y"


def get_models(manufacturer: str, catalog: dict) -> list[str]:
    entry = catalog.get(manufacturer, {})
    if isinstance(entry, dict):
        return entry.get("models", [])
    if isinstance(entry, list):
        return entry
    return []


def build_filter(
    manufacturer: str,
    car_type: str = "Y",
    model: str | None = None,
    badge: str | None = None,
    fuel_type: str | None = None,
    region: str | None = None,
    price=None,
    mileage=None,
) -> str:
    # Year and badge are excluded — apply client-side after fetching.
    # Badge values contain dots (e.g. "2.0 MPI") which corrupt the DSL syntax.
    extra = []
    if model:
        extra.append(f"Model.{model}.")
    # badge intentionally omitted from API query
    if fuel_type:
        extra.append(f"FuelType.{fuel_type}.")
    if region:
        extra.append(f"OfficeCityState.{region}.")
    if price:
        extra.append(f"Price.{price[0]}|{price[1]}.")
    if mileage:
        extra.append(f"Mileage.{mileage[0]}|{mileage[1]}.")
    extra_str = "".join(f"_.{c}" for c in extra)
    return f"(And.(And.Hidden.N._.(C.CarType.{car_type}._.Manufacturer.{manufacturer}.)){extra_str})"


def ask_custom_range(label: str, unit: str) -> tuple[int, int]:
    min_val = int(inquirer.text(
        message=f"{label} minimum ({unit}):",
        validate=lambda x: x.isdigit(),
        invalid_message="Please enter a number",
    ).execute())
    max_val = int(inquirer.text(
        message=f"{label} maximum ({unit}):",
        validate=lambda x: x.isdigit() and int(x) >= min_val,
        invalid_message=f"Please enter a number ≥ {min_val}",
    ).execute())
    return (min_val, max_val)


def create_filter() -> str:
    catalog = load_catalog()
    manufacturer_choices = sorted(catalog.keys()) if catalog else MANUFACTURERS_FALLBACK
    if not catalog:
        print("  (catalog.json not found — run discover_filters.py for full list)\n")

    manufacturer = inquirer.select(
        message="Select manufacturer:",
        choices=manufacturer_choices,
    ).execute()

    car_type = get_car_type(manufacturer, catalog)

    # Model selection (optional, only if catalog available)
    model_choice = None
    models = get_models(manufacturer, catalog)
    if models:
        model_choice = inquirer.select(
            message="Select model (all = no filter):",
            choices=[{"name": "All models (no filter)", "value": None}] +
                    [{"name": m, "value": m} for m in models],
        ).execute()

    price_choice = inquirer.select(
        message="Price range:",
        choices=PRICE_RANGES,
    ).execute()
    if price_choice == "custom":
        price_choice = ask_custom_range("Price", "만원 (10,000 KRW units)")

    year_choice = inquirer.select(
        message="Year range:",
        choices=YEAR_RANGES,
    ).execute()
    if year_choice == "custom":
        print("Format: YYYYMM  e.g. 202201 = January 2022")
        year_choice = ask_custom_range("Year", "YYYYMM")

    mileage_choice = inquirer.select(
        message="Mileage limit:",
        choices=MILEAGE_RANGES,
    ).execute()
    if mileage_choice == "custom":
        mileage_choice = ask_custom_range("Mileage", "km")

    return build_filter(manufacturer, car_type, model_choice, price_choice, year_choice, mileage_choice)


def load_filters() -> list[str]:
    if not os.path.exists(FILTERS_FILE):
        return []
    with open(FILTERS_FILE, "r", encoding="utf-8") as f:
        return json.load(f).get("filters", [])


def save_filters(filters: list[str]) -> None:
    with open(FILTERS_FILE, "w", encoding="utf-8") as f:
        json.dump({"filters": filters}, f, ensure_ascii=False, indent=2)


def main() -> None:
    filters = load_filters()
    print(f"\nActive filters: {len(filters)}")
    for i, f in enumerate(filters, 1):
        print(f"  {i}. {f[:80]}")
    print()

    while True:
        action = inquirer.select(
            message="Action:",
            choices=[
                {"name": "Add filter", "value": "add"},
                {"name": "Delete filter", "value": "delete"},
                {"name": "Save and exit", "value": "exit"},
            ],
        ).execute()

        if action == "add":
            new_filter = create_filter()
            filters.append(new_filter)
            print(f"\n✓ Added: {new_filter[:80]}\n")

        elif action == "delete":
            if not filters:
                print("No filters to delete.\n")
                continue
            to_delete = inquirer.select(
                message="Delete filter:",
                choices=[{"name": f[:80], "value": f} for f in filters],
            ).execute()
            filters.remove(to_delete)
            print("✓ Deleted\n")

        elif action == "exit":
            break

    save_filters(filters)
    print(f"\nSaved {len(filters)} filter(s) to filters.json")


if __name__ == "__main__":
    main()
