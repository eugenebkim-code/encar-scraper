"""Korean → English display translations for the Encar bot.

All Korean values are kept intact for API requests; this module provides
human-readable English labels shown in the Telegram UI.
"""

import re

# ── Manufacturers ──────────────────────────────────────────────────────────────

MANUFACTURER_EN: dict[str, str] = {
    # 국산 (Domestic)
    "기아": "Kia",
    "현대": "Hyundai",
    "제네시스": "Genesis",
    "르노코리아(삼성)": "Renault Korea",
    "KG모빌리티(쌍용)": "KG Mobility",
    "쉐보레": "Chevrolet",
    "기타": "Other",
    # 독일 (German)
    "BMW": "BMW",
    "벤츠": "Mercedes-Benz",
    "아우디": "Audi",
    "폭스바겐": "Volkswagen",
    "포르쉐": "Porsche",
    "미니": "MINI",
    "스마트": "Smart",
    "마이바흐": "Maybach",
    "오펠": "Opel",
    # 스웨덴 (Swedish)
    "볼보": "Volvo",
    "폴스타": "Polestar",
    "사브": "Saab",
    # 영국 (British)
    "랜드로버": "Land Rover",
    "재규어": "Jaguar",
    "벤틀리": "Bentley",
    "롤스로이스": "Rolls-Royce",
    "애스턴마틴": "Aston Martin",
    "맥라렌": "McLaren",
    "로터스": "Lotus",
    "이네오스": "Ineos",
    "MG": "MG",
    # 일본 (Japanese)
    "도요타": "Toyota",
    "렉서스": "Lexus",
    "혼다": "Honda",
    "닛산": "Nissan",
    "인피니티": "Infiniti",
    "마쯔다": "Mazda",
    "스바루": "Subaru",
    "미쓰비시": "Mitsubishi",
    "스즈키": "Suzuki",
    "다이하츠": "Daihatsu",
    "이스즈": "Isuzu",
    "미쓰오카": "Mitsuoka",
    "아큐라": "Acura",
    # 미국 (American)
    "포드": "Ford",
    "지프": "Jeep",
    "캐딜락": "Cadillac",
    "링컨": "Lincoln",
    "닷지": "Dodge",
    "GMC": "GMC",
    "뷰익": "Buick",
    "크라이슬러": "Chrysler",
    "허머": "Hummer",
    "폰티악": "Pontiac",
    "새턴": "Saturn",
    "올즈모빌": "Oldsmobile",
    "머큐리": "Mercury",
    "테슬라": "Tesla",
    # 이탈리아 (Italian)
    "페라리": "Ferrari",
    "람보르기니": "Lamborghini",
    "마세라티": "Maserati",
    "알파 로메오": "Alfa Romeo",
    "피아트": "Fiat",
    "파가니": "Pagani",
    # 프랑스 (French)
    "푸조": "Peugeot",
    "시트로엥": "Citroën",
    "르노": "Renault",
    # 초고가 / 하이퍼카
    "부가티": "Bugatti",
    "쾨니그세그": "Koenigsegg",
    # 중국 (Chinese)
    "BYD": "BYD",
    "동풍소콘": "Dongfeng Sokon",
    "지리": "Geely",
    "포톤": "Foton",
    "베이징은하": "BAIC",
    "신원": "Xin Yuan",
    # Legacy fallback entries (old Encar names)
    "메르세데스-벤츠": "Mercedes-Benz",
    "토요타": "Toyota",
    "르노코리아": "Renault Korea",
    "KG모빌리티": "KG Mobility",
    "알파로메오": "Alfa Romeo",
    "MINI": "MINI",
}

# ── Global filter values ───────────────────────────────────────────────────────

FUEL_TYPE_EN: dict[str, str] = {
    "CNG": "CNG",
    "LPG(일반인 구입)": "LPG (Public)",
    "가솔린": "Gasoline",
    "가솔린+LPG": "Gasoline + LPG",
    "가솔린+전기": "Gasoline + Electric",
    "디젤": "Diesel",
    "전기": "Electric",
}

EV_TYPE_EN: dict[str, str] = {
    "LPG": "LPG",
    "전기차": "Electric Vehicle",
    "플러그인 하이브리드": "Plug-in Hybrid",
    "하이브리드": "Hybrid",
}

REGION_EN: dict[str, str] = {
    "강원": "Gangwon",
    "경기": "Gyeonggi",
    "경남": "South Gyeongsang",
    "경북": "North Gyeongsang",
    "광주": "Gwangju",
    "대구": "Daegu",
    "대전": "Daejeon",
    "부산": "Busan",
    "서울": "Seoul",
    "울산": "Ulsan",
    "인천": "Incheon",
    "전남": "South Jeolla",
    "전북": "North Jeolla",
    "충남": "South Chungcheong",
    "충북": "North Chungcheong",
    "제주": "Jeju",
    "세종": "Sejong",
}

SELL_TYPE_EN: dict[str, str] = {
    "렌트": "Rental",
    "리스": "Lease",
    "일반": "Regular",
}

TRANSMISSION_EN: dict[str, str] = {
    "오토": "Automatic",
    "수동": "Manual",
    "세미오토": "Semi-Automatic",
}

COLOR_EN: dict[str, str] = {
    "흰색": "White",
    "검정색": "Black",
    "회색": "Gray",
    "은색": "Silver",
    "빨간색": "Red",
    "파란색": "Blue",
    "갈색": "Brown",
    "금색": "Gold",
    "하늘색": "Sky Blue",
    "주황색": "Orange",
    "녹색": "Green",
    "보라색": "Purple",
    "분홍색": "Pink",
    "노란색": "Yellow",
    "기타": "Other",
}

# ── Model name translation ─────────────────────────────────────────────────────

# Multi-word prefix phrases — applied first, longest match wins
_PREFIXES: list[tuple[str, str]] = [
    ("더 뉴 ", "The New "),
    ("올 뉴 ", "All New "),
    ("뉴 ", "New "),
]

# Korean car model codenames → English marketing names
_MODEL_NAMES: dict[str, str] = {
    # Hyundai
    "쏘나타": "Sonata",
    "그랜저": "Grandeur",
    "투싼": "Tucson",
    "싼타페": "Santa Fe",
    "아반떼": "Avante",
    "벨로스터": "Veloster",
    "스타렉스": "Starex",
    "팰리세이드": "Palisade",
    "아이오닉": "Ioniq",
    "코나": "Kona",
    "베뉴": "Venue",
    "넥쏘": "Nexo",
    "캐스퍼": "Casper",
    "포터": "Porter",
    "스타리아": "Staria",
    # Kia
    "스포티지": "Sportage",
    "쏘렌토": "Sorento",
    "쏘울": "Soul",
    "셀토스": "Seltos",
    "스토닉": "Stonic",
    "스팅어": "Stinger",
    "카니발": "Carnival",
    "모닝": "Morning",
    "레이": "Ray",
    "니로": "Niro",
    "오피러스": "Opirus",
    "프라이드": "Pride",
    "카렌스": "Carens",
    "모하비": "Mohave",
    "뉴카렌스": "New Carens",
    # Genesis
    "에쿠스": "Equus",
}

# Descriptor words inside model names → English
_DESCRIPTORS: list[tuple[str, str]] = [
    ("플러그인 하이브리드", "Plug-in Hybrid"),  # must be before 하이브리드
    ("하이브리드", "Hybrid"),
    ("마이스터", "Meister"),
    ("프리미어", "Premier"),
    ("마스터즈", "Masters"),
    ("마스터", "Master"),
    ("프레스티지", "Prestige"),
    ("노블레스", "Noblesse"),
    ("시그니처", "Signature"),
    ("그래비티", "Gravity"),
    ("볼드", "Bold"),
    ("더 볼드", "The Bold"),
]


def _ordinal(n: int) -> str:
    return {1: "1st", 2: "2nd", 3: "3rd"}.get(n, f"{n}th")


def translate_model(name: str) -> str:
    """Translate common Korean descriptors in a model name to English.

    Korean-exclusive proper names (e.g. 쏘렌토) are mapped to their
    official English marketing names. Alphanumeric model codes are
    left unchanged.
    """
    # 1. Prefix phrases (더 뉴, 올 뉴, 뉴)
    for kr, en in _PREFIXES:
        name = name.replace(kr, en)

    # 2. Korean car names (longest first to avoid partial matches)
    for kr, en in sorted(_MODEL_NAMES.items(), key=lambda x: -len(x[0])):
        name = name.replace(kr, en)

    # 3. Descriptor words (longest first)
    for kr, en in _DESCRIPTORS:
        name = name.replace(kr, en)

    # 4. "N세대" → "Nth Gen"
    name = re.sub(r"(\d+)세대", lambda m: f"{_ordinal(int(m.group(1)))} Gen", name)

    return name.strip()
