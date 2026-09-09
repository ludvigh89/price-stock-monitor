"""
scrape.py — збирає назви, ціни та наявність з категорії belok.ua.

Обмеження, які беремо з robots.txt сайту:
  * не використовуємо ?limit=, ?sort=, ?filter= — вони заборонені
  * ходимо тільки через ?page=N
  * робимо паузу між запитами і не приховуємо, хто ми

Запуск:
    py scrape.py                 — сходити на сайт і зібрати всі сторінки
    py scrape.py --file page.html — розпарсити збережену сторінку, без мережі
"""

from __future__ import annotations

import argparse
import csv
import datetime as dt
import pathlib
import re
import sys
import time
from dataclasses import dataclass, asdict, fields

import requests
from bs4 import BeautifulSoup

# ── Налаштування ──────────────────────────────────────────────────────────
CATEGORY_URL = "https://belok.ua/ua/sportivnoye-pitaniye/protein/"
MAX_PAGES = 10          # запобіжник від нескінченного циклу
DELAY_SECONDS = 2.0     # пауза між запитами — ввічливість до чужого сервера
PASSES = 2              # проходів по каталогу за запуск (магазин тасує товари)
TIMEOUT = 30
OUT_CSV = "prices.csv"

HEADERS = {
    "User-Agent": "PriceMonitor/1.0 (portfolio demo project)",
    "Accept-Language": "uk-UA,uk;q=0.9,en;q=0.8",
}


@dataclass
class Product:
    """Один товар у каталозі. pid — ключ, до якого прив'язана історія."""
    pid: str
    name: str
    brand: str
    url: str
    price: float | None
    old_price: float | None
    in_stock: bool
    labels: str
    scraped_at: str


# ── Парсинг ───────────────────────────────────────────────────────────────
def parse_money(text: str | None) -> float | None:
    """'4999₴' -> 4999.0 ; '1 234,50 ₴' -> 1234.5 ; сміття -> None"""
    if not text:
        return None
    cleaned = re.sub(r"[^\d,.]", "", text.replace("\xa0", ""))
    if not cleaned:
        return None
    # якщо є і крапка, і кома — кома це роздільник тисяч
    if "," in cleaned and "." in cleaned:
        cleaned = cleaned.replace(",", "")
    else:
        cleaned = cleaned.replace(",", ".")
    try:
        return float(cleaned)
    except ValueError:
        return None


def parse_card(card, stamp: str) -> Product | None:
    """Витягує один товар. Повертає None, якщо картка неповна."""
    pid = card.get("data-pid")
    link = card.select_one(".sc-module-img a[href]")
    price_el = card.select_one("span.sc-module-price")

    if not (pid and link and price_el):
        return None

    # Назва лежить в title посилання — надійніше, ніж текст під картинкою
    name = (link.get("title") or "").strip()
    if not name:
        img = link.select_one("img[alt]")
        name = (img.get("alt") if img else "").strip()

    labels = [s.get_text(strip=True) for s in card.select("div.sc-module-sticker")]

    # Наявність. Порядок перевірок критичний: рядок "не в наявності"
    # МІСТИТЬ у собі "в наявності", тож заперечення шукаємо ПЕРШИМ.
    # Дивимось на весь текст картки, бо магазин показує статус то стікером
    # на фото ("В наявності"), то текстом на кнопці ("Не в наявності").
    blob = card.get_text(" ", strip=True).lower()
    if "не в наявності" in blob:
        in_stock = False
    elif "в наявності" in blob:
        in_stock = True
    else:
        in_stock = False  # статусу немає — вважаємо, що купити не можна

    brand_el = card.select_one("div.dop-title span")
    brand = brand_el.get_text(strip=True) if brand_el else ""

    return Product(
        pid=pid,
        name=name,
        brand=brand,
        url=link["href"].split("#")[0],
        price=parse_money(price_el.get_text(strip=True)),
        old_price=parse_money(
            el.get_text(strip=True)
            if (el := card.select_one("span.sc-module-price-old")) else None
        ),
        in_stock=in_stock,
        labels=" | ".join(labels),
        scraped_at=stamp,
    )


def parse_page(html: str, stamp: str) -> list[Product]:
    soup = BeautifulSoup(html, "lxml")
    found, skipped = [], 0
    for card in soup.select("div.product-layout"):
        product = parse_card(card, stamp)
        if product:
            found.append(product)
        else:
            skipped += 1
    if skipped:
        print(f"    увага: пропущено неповних карток — {skipped}")
    return found


def last_page_number(html: str) -> int:
    """Скільки всього сторінок у категорії — читаємо з пагінації."""
    soup = BeautifulSoup(html, "lxml")
    numbers = [
        int(m.group(1))
        for a in soup.select("a[href*='page=']")
        if (m := re.search(r"[?&]page=(\d+)", a["href"]))
    ]
    return max(numbers) if numbers else 1


# ── Мережа ────────────────────────────────────────────────────────────────
def fetch(session: requests.Session, url: str) -> str | None:
    try:
        resp = session.get(url, headers=HEADERS, timeout=TIMEOUT)
    except requests.RequestException as exc:
        print(f"    запит не пройшов: {exc}")
        return None
    if resp.status_code != 200:
        print(f"    HTTP {resp.status_code} — сторінку пропускаємо")
        return None
    return resp.text


def scrape_once(session, stamp: str, seen: set[str]) -> list[Product]:
    """Один прохід по всіх сторінках категорії."""
    products: list[Product] = []

    html = fetch(session, CATEGORY_URL)
    if html is None:
        return []

    total = min(last_page_number(html), MAX_PAGES)

    for page in range(1, total + 1):
        if page > 1:
            time.sleep(DELAY_SECONDS)
            html = fetch(session, f"{CATEGORY_URL}?page={page}")
            if html is None:
                continue

        batch = parse_page(html, stamp)
        new = [p for p in batch if p.pid not in seen]
        seen.update(p.pid for p in new)
        products.extend(new)
        print(f"    стор. {page}: {len(batch)} товарів, з них уперше — {len(new)}")

    return products


def scrape_all(passes: int = PASSES) -> list[Product]:
    """
    Збирає категорію за кілька проходів і об'єднує результат.

    Магазин тасує товари між сторінками, тому один прохід бачить
    не весь каталог. Другий прохід добирає тих, хто сховався
    від першого.
    """
    stamp = dt.datetime.now().strftime("%Y-%m-%d %H:%M")
    session = requests.Session()
    products: list[Product] = []
    seen: set[str] = set()

    for attempt in range(1, passes + 1):
        print(f"Прохід {attempt} з {passes}:")
        before = len(seen)
        products.extend(scrape_once(session, stamp, seen))
        print(f"    разом унікальних: {len(seen)} (+{len(seen) - before})")
        if attempt < passes:
            time.sleep(DELAY_SECONDS)

    return products


# ── Збереження ────────────────────────────────────────────────────────────
def save_csv(products: list[Product], path: str) -> None:
    # utf-8-sig — щоб Excel на Windows не показав кирилицю кракозябрами
    with open(path, "w", newline="", encoding="utf-8-sig") as fh:
        writer = csv.DictWriter(fh, fieldnames=[f.name for f in fields(Product)])
        writer.writeheader()
        for p in products:
            writer.writerow(asdict(p))


def report(products: list[Product]) -> None:
    if not products:
        print("\nНічого не зібрано.")
        return

    in_stock = sum(p.in_stock for p in products)
    discounted = [p for p in products if p.old_price]
    priced = [p.price for p in products if p.price]

    print(f"\n{'─' * 62}")
    print(f"Зібрано товарів:      {len(products)}")
    print(f"В наявності:          {in_stock}")
    print(f"Зі знижкою:           {len(discounted)}")
    if priced:
        print(f"Ціновий діапазон:     {min(priced):,.0f} — {max(priced):,.0f} ₴")
    print(f"{'─' * 62}")

    print(f"\n{'ID':<8}{'Ціна':>9}{'Було':>9}  Назва")
    for p in products[:10]:
        old = f"{p.old_price:,.0f}" if p.old_price else "—"
        price = f"{p.price:,.0f}" if p.price else "?"
        mark = "" if p.in_stock else "  [немає]"
        print(f"{p.pid:<8}{price:>9}{old:>9}  {p.name[:38]}{mark}")
    if len(products) > 10:
        print(f"{'':<26}… ще {len(products) - 10}")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--file", help="розпарсити збережений HTML замість запиту в мережу")
    args = ap.parse_args()

    if args.file:
        path = pathlib.Path(args.file)
        if not path.exists():
            print(f"Файл не знайдено: {path}")
            return 1
        stamp = dt.datetime.now().strftime("%Y-%m-%d %H:%M")
        products = parse_page(path.read_text(encoding="utf-8"), stamp)
    else:
        products = scrape_all()

    report(products)
    if products:
        save_csv(products, OUT_CSV)
        print(f"\nЗбережено у {pathlib.Path(OUT_CSV).resolve()}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
