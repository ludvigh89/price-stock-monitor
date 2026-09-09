"""
discover.py — розвідка структури сторінки перед написанням скрейпера.

Нічого не парсить "начисто". Задача одна: подивитись, як влаштована
сторінка, і показати, за які класи чіплятись. Запускається один раз.

Запуск:  py discover.py
"""

import collections
import pathlib
import re
import sys

import requests
from bs4 import BeautifulSoup

URL = "https://belok.ua/ua/sportivnoye-pitaniye/protein/"

# Чесний User-Agent: не прикидаємось Googlebot і не ховаємось.
# Так роблять у нормальних проєктах — сайт бачить, хто до нього ходить.
HEADERS = {
    "User-Agent": "PriceMonitor/1.0 (portfolio demo project)",
    "Accept-Language": "uk-UA,uk;q=0.9,en;q=0.8",
}


def chain(el, depth: int = 4) -> str:
    """Ланцюжок тег.клас від елемента вгору до батьків."""
    parts = []
    for _ in range(depth):
        if el is None or getattr(el, "name", None) is None:
            break
        classes = el.get("class") or []
        parts.append(el.name + ("." + ".".join(classes) if classes else ""))
        el = el.parent
    return "  <  ".join(parts)


def main() -> int:
    try:
        resp = requests.get(URL, headers=HEADERS, timeout=30)
    except requests.RequestException as exc:
        print(f"ЗАПИТ НЕ ПРОЙШОВ: {exc}")
        return 1

    print(f"HTTP {resp.status_code}   |   {len(resp.content):,} байт")
    if resp.status_code != 200:
        print("Сайт віддав не 200 — далі йти нема сенсу, покажіть цей вивід.")
        return 1

    out = pathlib.Path("page.html")
    out.write_bytes(resp.content)
    print(f"HTML збережено у {out.resolve()}")

    soup = BeautifulSoup(resp.text, "lxml")
    print(f"TITLE: {soup.title.get_text(strip=True) if soup.title else '—'}")

    # 1. Чи є ціни просто в HTML, чи їх домальовує JavaScript
    price_strings = soup.find_all(string=re.compile("₴"))
    print(f"\n[1] Текстових вузлів із символом ₴: {len(price_strings)}")
    if not price_strings:
        print("    Нуль. Ціни підвантажує JS — тоді знадобиться Selenium.")
        return 0

    print("\n[2] Найчастіші ланцюжки навколо ціни:")
    counter = collections.Counter(chain(s.parent) for s in price_strings)
    for path, num in counter.most_common(8):
        print(f"    {num:3}x  {path}")

    # 3. Кандидати на «картку товару»
    print("\n[3] Класи, схожі на контейнер товару:")
    candidates = collections.Counter()
    for el in soup.find_all(attrs={"class": True}):
        for cls in el.get("class"):
            low = cls.lower()
            if any(k in low for k in ("product", "item", "card", "thumb")):
                candidates[f"{el.name}.{cls}"] += 1
    for cls, num in candidates.most_common(12):
        print(f"    {num:3}x  {cls}")

    # 4. Сира розмітка першої картки — щоб побачити назву, ціну, наявність
    print("\n[4] Перша знайдена картка товару (обрізано до 1500 символів):")
    first = None
    for cls, _ in candidates.most_common(12):
        tag, _, name = cls.partition(".")
        found = soup.find(tag, class_=name)
        if found and "₴" in found.get_text():
            first = found
            break
    print(first.prettify()[:1500] if first else "    не знайшлось — покажіть вивід вище")

    # 5. Пагінація
    print("\n[5] Посилання, схожі на пагінацію:")
    seen = set()
    for a in soup.find_all("a", href=True):
        if "page=" in a["href"] and a["href"] not in seen:
            seen.add(a["href"])
            print(f"    {a.get_text(strip=True)!r:>8}  ->  {a['href']}")
    if not seen:
        print("    не знайдено — можливо, кнопка 'Показати ще' на JS")

    return 0


if __name__ == "__main__":
    sys.exit(main())
