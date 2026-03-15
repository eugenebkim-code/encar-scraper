"""
Comprehensive filter test suite for Encar scraper.

Tests every manufacturer, fuel type, region, and key filter combinations
against the live Encar API and reports how many listings each query returns.

Usage:
    python test_filters.py              # run all tests
    python test_filters.py --fast       # skip per-model tests (much quicker)
"""

import argparse
import json
import os
import sys
import time

from scraper import fetch_cars
from translations import (
    MANUFACTURER_EN,
    FUEL_TYPE_EN,
    EV_TYPE_EN,
    REGION_EN,
    SELL_TYPE_EN,
    TRANSMISSION_EN,
    translate_model,
)

CATALOG_FILE = os.path.join(os.path.dirname(__file__), "catalog.json")

# ── Helpers ────────────────────────────────────────────────────────────────────

def load_catalog() -> dict:
    with open(CATALOG_FILE, encoding="utf-8") as f:
        return json.load(f)


def _run(label: str, query: str, indent: int = 2) -> int:
    """Fetch results for *query*, print status, return count (-1 on error)."""
    pad = " " * indent
    try:
        results = fetch_cars(query)
        n = len(results)
        icon = "✓" if n > 0 else "⚠"
        print(f"{pad}{icon}  {label}: {n} listing(s)")
        return n
    except Exception as exc:
        print(f"{pad}✗  {label}: ERROR — {exc}")
        return -1


def _mfr_query(mfr: str, car_type: str) -> str:
    return f"(And.(And.Hidden.N._.(C.CarType.{car_type}._.Manufacturer.{mfr}.)))"


def _combined(mfr: str, car_type: str, **extras) -> str:
    parts = []
    for key, val in extras.items():
        if key == "model":
            parts.append(f"Model.{val}.")
        elif key == "badge":
            parts.append(f"Badge.{val}.")
        elif key == "fuel":
            parts.append(f"FuelType.{val}.")
        elif key == "region":
            parts.append(f"OfficeCityState.{val}.")
        elif key == "price":
            parts.append(f"Price.{val[0]}|{val[1]}.")
        elif key == "year":
            parts.append(f"Year.{val[0]}|{val[1]}.")
        elif key == "mileage":
            parts.append(f"Mileage.{val[0]}|{val[1]}.")
        elif key == "sell":
            parts.append(f"SellType.{val}.")
        elif key == "transmission":
            parts.append(f"Transmission.{val}.")
    extra_str = "".join(f"_.{p}" for p in parts)
    return f"(And.(And.Hidden.N._.(C.CarType.{car_type}._.Manufacturer.{mfr}.)){extra_str})"


def section(title: str) -> None:
    print(f"\n{'─' * 60}")
    print(f"  {title}")
    print(f"{'─' * 60}")


# ── Test sections ──────────────────────────────────────────────────────────────

def test_manufacturers(catalog: dict) -> dict[str, int]:
    section("1. Manufacturers (all)")
    mfrs = catalog.get("manufacturers", {})
    counts: dict[str, int] = {}
    for mfr, data in mfrs.items():
        car_type = data.get("car_type", "Y") if isinstance(data, dict) else "Y"
        en = MANUFACTURER_EN.get(mfr, mfr)
        counts[mfr] = _run(f"{en} ({mfr})", _mfr_query(mfr, car_type))
        time.sleep(0.15)   # be polite to the API
    return counts


def test_fuel_types(catalog: dict) -> None:
    section("2. Fuel Types  (tested with Hyundai 현대, CarType=Y)")
    values = catalog["_global_filters"].get("FuelType", {}).get("values", [])
    for fuel in values:
        en = FUEL_TYPE_EN.get(fuel, fuel)
        q = _combined("현대", "Y", fuel=fuel)
        _run(f"{en} ({fuel})", q)
        time.sleep(0.15)


def test_ev_types(catalog: dict) -> None:
    section("3. EV / Eco Types  (tested with Hyundai 현대)")
    values = catalog["_global_filters"].get("EvType", {}).get("values", [])
    for ev in values:
        en = EV_TYPE_EN.get(ev, ev)
        # EvType maps to the EvType filter field
        parts = f"_.EvType.{ev}."
        q = f"(And.(And.Hidden.N._.(C.CarType.Y._.Manufacturer.현대.)){parts})"
        _run(f"{en} ({ev})", q)
        time.sleep(0.15)


def test_regions(catalog: dict) -> None:
    section("4. Regions  (tested with Hyundai 현대)")
    gf = catalog["_global_filters"]
    regions = list(gf.get("OfficeCityState", {}).get("values", []))
    regions += list(gf.get("OfficeCityState_extra", {}).get("values", []))
    for region in sorted(regions):
        en = REGION_EN.get(region, region)
        q = _combined("현대", "Y", region=region)
        _run(f"{en} ({region})", q)
        time.sleep(0.15)


def test_sell_types(catalog: dict) -> None:
    section("5. Sell Types  (tested with Hyundai 현대)")
    values = catalog["_global_filters"].get("SellType", {}).get("values", [])
    for sell in values:
        en = SELL_TYPE_EN.get(sell, sell)
        q = _combined("현대", "Y", sell=sell)
        _run(f"{en} ({sell})", q)
        time.sleep(0.15)


def test_transmissions(catalog: dict) -> None:
    section("6. Transmissions  (tested with Hyundai 현대)")
    values = catalog["_global_filters"].get("Transmission", {}).get("values", [])
    for trans in values:
        en = TRANSMISSION_EN.get(trans, trans)
        q = _combined("현대", "Y", transmission=trans)
        _run(f"{en} ({trans})", q)
        time.sleep(0.15)


def test_price_ranges(catalog: dict) -> None:
    section("7. Price Ranges  (tested with Hyundai 현대)")
    presets = catalog["_global_filters"].get("Price", {}).get("presets", [])
    for label_kr, (lo, hi) in presets:
        q = _combined("현대", "Y", price=(lo, hi))
        _run(f"{lo:,}–{hi:,}만 KRW  ({label_kr})", q)
        time.sleep(0.15)


def test_year_ranges(catalog: dict) -> None:
    """Year cannot be used in the API q= parameter (always returns 404).
    It is applied client-side after fetching. This section verifies that
    the base query still works and that year values present in results
    can be filtered correctly."""
    section("8. Year Ranges  (client-side — verifying base fetch + year filter)")
    q = _mfr_query("현대", "Y")
    try:
        cars = fetch_cars(q)
        print(f"  Base Hyundai fetch: {len(cars)} listing(s)")
        presets = catalog["_global_filters"].get("Year", {}).get("presets", [])
        for label_kr, (lo, hi) in presets:
            matched = [c for c in cars if lo <= (c.get("Year") or 0) <= hi]
            icon = "✓" if matched else "⚠"
            print(f"  {icon}  {label_kr} client-side: {len(matched)}/{len(cars)} match")
    except Exception as e:
        print(f"  ✗ ERROR: {e}")


def test_mileage_ranges(catalog: dict) -> None:
    section("9. Mileage Ranges  (tested with Hyundai 현대)")
    presets = catalog["_global_filters"].get("Mileage", {}).get("presets", [])
    for label_kr, (lo, hi) in presets:
        q = _combined("현대", "Y", mileage=(lo, hi))
        _run(f"up to {hi:,} km  ({label_kr})", q)
        time.sleep(0.15)


def test_combined_filters() -> None:
    section("10. Combined Filter Examples")

    cases = [
        (
            "Kia EV6 · Electric · Seoul",
            _combined("기아", "Y", model="EV6", fuel="전기", region="서울"),
        ),
        (
            "Hyundai Sonata · Gasoline · Gyeonggi · up to 50,000 km",
            _combined("현대", "Y", model="쏘나타", fuel="가솔린",
                       region="경기", mileage=(0, 50000)),
        ),
        (
            "Genesis G80 · up to 3,000만 KRW  (year applied client-side)",
            _combined("제네시스", "Y", model="G80", price=(0, 3000)),
        ),
        (
            "BMW 5-Series · Diesel · Automatic",
            _combined("BMW", "N", model="5시리즈", fuel="디젤",
                       transmission="오토"),
        ),
        (
            "Lamborghini Huracán · any filter",
            _mfr_query("람보르기니", "N"),
        ),
        (
            "Kia Sportage 5th Gen Hybrid  (year applied client-side)",
            _combined("기아", "Y", model="스포티지 5세대 하이브리드"),
        ),
    ]

    for label, query in cases:
        _run(label, query)
        time.sleep(0.2)


def test_models_sample(catalog: dict, max_per_brand: int = 5) -> None:
    section(f"11. Model Spot-Check  (first {max_per_brand} models per brand)")
    mfrs = catalog.get("manufacturers", {})
    for mfr, data in mfrs.items():
        car_type = data.get("car_type", "Y") if isinstance(data, dict) else "Y"
        models = data.get("models", []) if isinstance(data, dict) else []
        if not models:
            continue
        en_mfr = MANUFACTURER_EN.get(mfr, mfr)
        print(f"\n  [{en_mfr}]")
        for model in models[:max_per_brand]:
            q = _combined(mfr, car_type, model=model)
            en_model = translate_model(model)
            _run(f"{en_model}  ({model})", q, indent=4)
            time.sleep(0.15)


# ── Entry point ────────────────────────────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser(description="Encar filter test suite")
    parser.add_argument(
        "--fast",
        action="store_true",
        help="Skip per-model spot-check (section 11) for a quicker run",
    )
    args = parser.parse_args()

    print("=" * 60)
    print("  ENCAR FILTER TEST SUITE")
    print("  Verifying all catalog filters against the live API")
    print("=" * 60)

    catalog = load_catalog()

    test_manufacturers(catalog)
    test_fuel_types(catalog)
    test_ev_types(catalog)
    test_regions(catalog)
    test_sell_types(catalog)
    test_transmissions(catalog)
    test_price_ranges(catalog)
    test_year_ranges(catalog)
    test_mileage_ranges(catalog)
    test_combined_filters()

    if not args.fast:
        test_models_sample(catalog, max_per_brand=5)
    else:
        print("\n  (Skipping model spot-check — use without --fast to enable)")

    print("\n" + "=" * 60)
    print("  Done.")
    print("=" * 60)


if __name__ == "__main__":
    main()
