"""
Discovers all available filter options from Encar API.
Run: python discover_filters.py
Saves full catalog to catalog.json
"""

import json
import os
import time
from collections import defaultdict

import requests

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/120.0.0.0 Safari/537.36"
    ),
    "Referer": "https://www.encar.com/",
}
BASE_URL = "https://api.encar.com/search/car/list/general"
CATALOG_FILE = os.path.join(os.path.dirname(__file__), "catalog.json")
DELAY = 0.4

# CarType.Y = 국산 / CarType.N = 수입
# Note: old brand names still used in Encar DB
DOMESTIC = [
    "기아", "현대", "제네시스",
    "르노코리아(삼성)",  # Renault Korea (formerly Samsung)
    "KG모빌리티(쌍용)", # KG Mobility (formerly SsangYong)
    "기타",             # Other domestic manufacturers
]

IMPORTED = [
    # 독일
    "BMW",
    "벤츠",             # Mercedes-Benz (Encar uses 벤츠)
    "아우디",
    "폭스바겐",
    "포르쉐",
    "미니",             # MINI (Encar uses 미니)
    "스마트",           # Smart
    "마이바흐",         # Maybach
    "오펠",             # Opel
    # 스웨덴
    "볼보",
    "폴스타",
    "사브",             # Saab
    # 영국
    "랜드로버",
    "재규어",
    "벤틀리",
    "롤스로이스",
    "애스턴마틴",       # Aston Martin
    "맥라렌",           # McLaren
    "로터스",           # Lotus
    "이네오스",         # Ineos
    "MG",               # MG Rover
    # 일본
    "도요타",           # Toyota (Encar uses 도요타)
    "렉서스",
    "혼다",
    "닛산",
    "인피니티",
    "마쯔다",
    "스바루",
    "미쓰비시",
    "스즈키",           # Suzuki
    "다이하츠",         # Daihatsu
    "이스즈",           # Isuzu
    "미쓰오카",         # Mitsuoka
    "아큐라",           # Acura
    # 미국
    "쉐보레",           # Chevrolet (CarType.N on Encar despite being made in Korea)
    "포드",
    "지프",
    "캐딜락",
    "링컨",
    "닷지",
    "GMC",              # GMC
    "뷰익",             # Buick
    "크라이슬러",       # Chrysler
    "허머",             # Hummer
    "폰티악",           # Pontiac
    "새턴",             # Saturn
    "올즈모빌",         # Oldsmobile
    "머큐리",           # Mercury
    "테슬라",           # Tesla
    # 이탈리아
    "페라리",
    "람보르기니",
    "마세라티",
    "알파 로메오",      # Alfa Romeo (Encar uses space)
    "피아트",
    "파가니",           # Pagani
    # 프랑스
    "푸조",
    "시트로엥",
    "르노",
    # 스페인/기타 유럽
    # 초고가 / 하이퍼카
    "부가티",           # Bugatti
    "쾨니그세그",       # Koenigsegg
    # 중국
    "BYD",
    "동풍소콘",         # Dongfeng Sokon
    "지리",             # Geely
    "포톤",             # Photon (Foton)
    "베이징은하",       # BAIC Silver Coin
    "신원",             # Xin Yuan
]

# Hardcoded filter values known from Encar
KNOWN_FILTERS = {
    "Transmission": {
        "label": "변속기",
        "values": ["오토", "수동", "세미오토"],
        "type": "categorical",
    },
    "OfficeCityState_extra": {
        "label": "지역(추가)",
        "values": ["제주", "세종"],  # often missing from small samples
        "type": "categorical",
    },
    "Color": {
        "label": "색상",
        "values": [
            "흰색", "검정색", "회색", "은색", "빨간색", "파란색",
            "갈색", "금색", "하늘색", "주황색", "녹색", "보라색",
            "분홍색", "노란색", "기타",
        ],
        "type": "categorical",
    },
    "Displacement": {
        "label": "배기량",
        "values": [
            (0, 1000), (1000, 1600), (1600, 2000),
            (2000, 2500), (2500, 3000), (3000, 99999),
        ],
        "type": "range",
        "unit": "cc",
    },
    "Year": {
        "label": "연식",
        "type": "range",
        "unit": "YYYYMM",
        "presets": [
            ("2020년~", (202001, 999912)),
            ("2022년~", (202201, 999912)),
            ("2023년~", (202301, 999912)),
            ("2018~2022", (201801, 202212)),
            ("2015~2020", (201501, 202012)),
        ],
    },
    "Price": {
        "label": "가격",
        "type": "range",
        "unit": "만원",
        "presets": [
            ("~1000만원", (0, 1000)),
            ("~2000만원", (0, 2000)),
            ("~3000만원", (0, 3000)),
            ("~5000만원", (0, 5000)),
            ("1000~3000만원", (1000, 3000)),
            ("2000~5000만원", (2000, 5000)),
            ("5000만원~", (5000, 99999)),
        ],
    },
    "Mileage": {
        "label": "주행거리",
        "type": "range",
        "unit": "km",
        "presets": [
            ("~3만km", (0, 30000)),
            ("~5만km", (0, 50000)),
            ("~10만km", (0, 100000)),
            ("~15만km", (0, 150000)),
        ],
    },
}

# Fields to collect unique values from API response
COLLECT_FIELDS = {
    "FuelType": "연료",
    "EvType": "친환경차종류",
    "OfficeCityState": "지역",
    "SellType": "판매유형",
    "Badge": "세부모델",
    "Model": "모델",
}


def fetch(query: str, offset: int = 0, size: int = 100) -> tuple[int, list[dict]]:
    params = {
        "count": "true",
        "q": query,
        "sr": f"|ModifiedDate|{offset}|{size}",
    }
    r = requests.get(BASE_URL, params=params, headers=HEADERS, timeout=15)
    r.raise_for_status()
    data = r.json()
    return data.get("Count", 0), data.get("SearchResults", [])


def scan_manufacturer(manufacturer: str, car_type: str, pages: int = None) -> dict:
    """Fetch multiple pages for a manufacturer and collect all unique field values.

    Pages are auto-scaled by listing count so rare models aren't missed:
      < 1 000 listings  → 5 pages  (500 cars)
      < 5 000 listings  → 10 pages (1 000 cars)
      < 20 000 listings → 20 pages (2 000 cars)
      ≥ 20 000 listings → 40 pages (4 000 cars)
    """
    query = f"(And.(And.Hidden.N._.(C.CarType.{car_type}._.Manufacturer.{manufacturer}.)))"

    collected: dict[str, set] = defaultdict(set)
    count = 0

    # First fetch to get total count, then decide depth
    if pages is None:
        try:
            total, first_cars = fetch(query, offset=0, size=100)
            count = total
            for car in first_cars:
                for field in COLLECT_FIELDS:
                    val = car.get(field)
                    if val and isinstance(val, str) and val.strip():
                        collected[field].add(val.strip())
            if total < 1_000:
                pages = 5
            elif total < 5_000:
                pages = 10
            elif total < 20_000:
                pages = 20
            else:
                pages = 40
            start_page = 1  # already fetched page 0
        except Exception as e:
            print(f"  [warn] initial fetch: {e}")
            return {"count": 0, "data": {}}
    else:
        start_page = 0

    for page in range(start_page, pages):
        try:
            total, cars = fetch(query, offset=page * 100, size=100)
            if page == 0:
                count = total
            if not cars:
                break
            for car in cars:
                for field in COLLECT_FIELDS:
                    val = car.get(field)
                    if val and isinstance(val, str) and val.strip():
                        collected[field].add(val.strip())
            if (page + 1) * 100 >= total:
                break
            time.sleep(DELAY)
        except Exception as e:
            print(f"  [warn] page {page}: {e}")
            break

    return {
        "count": count,
        "data": {field: sorted(vals) for field, vals in collected.items()},
    }


def main() -> None:
    print("=" * 60)
    print("Encar Full Filter Discovery")
    print("=" * 60)

    all_items = [("Y", m) for m in DOMESTIC] + [("N", m) for m in IMPORTED]
    print(f"Scanning {len(all_items)} manufacturers (3 pages each)...\n")

    # Per-manufacturer data
    manufacturers: dict = {}

    # Global aggregated values
    global_values: dict[str, set] = defaultdict(set)

    for i, (car_type, manufacturer) in enumerate(all_items, 1):
        tag = "국산" if car_type == "Y" else "수입"
        print(f"[{i:02d}/{len(all_items)}] {manufacturer} ({tag}) ...", end=" ", flush=True)

        try:
            result = scan_manufacturer(manufacturer, car_type)
            count = result["count"]
            data = result["data"]

            if count == 0:
                print("0 listings — skipped")
                time.sleep(DELAY)
                continue

            manufacturers[manufacturer] = {
                "car_type": car_type,
                "count": count,
                "models": data.get("Model", []),
                "badges": data.get("Badge", []),
                "fuel_types": data.get("FuelType", []),
                "ev_types": data.get("EvType", []),
                "sell_types": data.get("SellType", []),
                "regions": data.get("OfficeCityState", []),
            }

            # Aggregate global values
            for field in ["FuelType", "EvType", "OfficeCityState", "SellType"]:
                global_values[field].update(data.get(field, []))

            models_count = len(data.get("Model", []))
            badges_count = len(data.get("Badge", []))
            print(f"{count:,} listings | {models_count} models | {badges_count} badges")

        except Exception as e:
            print(f"ERROR: {e}")

        time.sleep(DELAY)

    # Build final catalog
    catalog = {
        "_meta": {
            "description": "Encar filter catalog. Global filters apply to all searches.",
            "fields": {
                "FuelType": "연료 (filter: FuelType.값.)",
                "EvType": "친환경 종류 (filter: EvType.값.)",
                "OfficeCityState": "지역 (filter: OfficeCityState.값.)",
                "SellType": "판매유형 (filter: SellType.값.)",
                "Transmission": "변속기 (filter: Transmission.값.)",
                "Color": "색상 (filter: Color.값.)",
                "Displacement": "배기량 cc (filter: Displacement.min|max.)",
                "Year": "연식 YYYYMM (filter: Year.min|max.)",
                "Price": "가격 만원 (filter: Price.min|max.)",
                "Mileage": "주행거리 km (filter: Mileage.min|max.)",
            },
        },
        "_global_filters": {
            "FuelType": {
                "label": "연료",
                "type": "categorical",
                "values": sorted(global_values.get("FuelType", [])),
            },
            "EvType": {
                "label": "친환경차종류",
                "type": "categorical",
                "values": sorted(v for v in global_values.get("EvType", []) if v),
            },
            "OfficeCityState": {
                "label": "지역",
                "type": "categorical",
                "values": sorted(global_values.get("OfficeCityState", [])),
            },
            "SellType": {
                "label": "판매유형",
                "type": "categorical",
                "values": sorted(global_values.get("SellType", [])),
            },
            **KNOWN_FILTERS,
        },
        "manufacturers": manufacturers,
    }

    with open(CATALOG_FILE, "w", encoding="utf-8") as f:
        json.dump(catalog, f, ensure_ascii=False, indent=2)

    # Summary
    print("\n" + "=" * 60)
    total_mfr = len(manufacturers)
    total_models = sum(len(v.get("models", [])) for v in manufacturers.values())
    total_badges = sum(len(v.get("badges", [])) for v in manufacturers.values())
    fuel_types = catalog["_global_filters"]["FuelType"]["values"]
    regions = catalog["_global_filters"]["OfficeCityState"]["values"]

    print(f"Manufacturers : {total_mfr}")
    print(f"Models        : {total_models}")
    print(f"Badges/Trims  : {total_badges}")
    print(f"Fuel types    : {fuel_types}")
    print(f"Regions ({len(regions)}): {regions}")
    print(f"\nFilter categories in catalog:")
    for key, val in catalog["_global_filters"].items():
        print(f"  {key:25s} — {val['label']}")
    print(f"\nSaved to {CATALOG_FILE}")


if __name__ == "__main__":
    main()
