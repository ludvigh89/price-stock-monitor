"""
run.py — головний файл. Складає докупи збір цін і Telegram.

Що робить за один запуск:
  1. збирає поточні ціни (scrape.py)
  2. порівнює з тим, що було минулого разу (snapshot.json)
  3. дописує рядки в history.csv — це наш архів для таблиці
  4. надсилає звіт у Telegram (notify.py)

Запуск:
    py run.py                  — по-справжньому
    py run.py --file page.html — офлайн, на збереженій сторінці
    py run.py --no-telegram    — порахувати й показати, але не надсилати
"""

from __future__ import annotations

import argparse
import csv
import datetime as dt
import json
import pathlib
import sys
from dataclasses import asdict, fields

import notify
import scrape

SNAPSHOT = pathlib.Path("snapshot.json")   # пам'ять між запусками
HISTORY = pathlib.Path("history.csv")      # архів усіх запусків
MIN_CHANGE_PCT = 1.0                       # зміни дрібніші за це — шум, ігноруємо
MAX_LINES = 10                             # скільки позицій показувати в кожному блоці
TG_LIMIT = 4000                            # ліміт довжини повідомлення в Telegram

# Скільки запусків поспіль товару має не бути у видачі, перш ніж
# оголосити його зниклим. Магазин тасує товари між сторінками, тому
# один-два промахи — це збій збору, а не подія в каталозі.
CONFIRM_RUNS = 5


# ── Пам'ять між запусками ────────────────────────────────────────────────
def load_snapshot() -> dict:
    """
    Повертає {"products": {pid: дані}, "missing": {pid: скільки разів поспіль
    не бачили}, "runs": лічильник}. Старий формат читається й оновлюється.
    """
    empty = {"products": {}, "missing": {}, "runs": 0}
    if not SNAPSHOT.exists():
        return empty

    try:
        data = json.loads(SNAPSHOT.read_text(encoding="utf-8"))
    except (ValueError, OSError) as exc:
        print(f"Не вдалося прочитати {SNAPSHOT} ({exc}) — рахуємо як перший запуск")
        return empty

    if "products" in data and isinstance(data.get("products"), dict):
        data.setdefault("missing", {})
        data.setdefault("runs", 0)
        return data

    # Файл зі старої версії: там був просто {pid: дані}
    print("Знайдено снапшот старого формату — оновлюю структуру.")
    return {"products": data, "missing": {}, "runs": 1}


def save_snapshot(state: dict) -> None:
    SNAPSHOT.write_text(
        json.dumps(state, ensure_ascii=False, indent=1), encoding="utf-8"
    )


def append_history(products: list[scrape.Product]) -> None:
    """Дописує поточний зріз у кінець архіву, не стираючи попередні."""
    new_file = not HISTORY.exists()
    with open(HISTORY, "a", newline="", encoding="utf-8-sig") as fh:
        writer = csv.DictWriter(fh, fieldnames=[f.name for f in fields(scrape.Product)])
        if new_file:
            writer.writeheader()
        for p in products:
            writer.writerow(asdict(p))


# ── Порівняння ───────────────────────────────────────────────────────────
def compare(state: dict, new: list[scrape.Product]) -> tuple[dict, dict]:
    """
    Порівнює новий зріз із пам'яттю. Повертає (зміни, оновлена пам'ять).

    Ключ звіряння — pid. Товар, якого немає у видачі, НЕ вважається
    зниклим одразу: спершу він накопичує промахи, і лише після
    CONFIRM_RUNS поспіль потрапляє у звіт.
    """
    result = {
        "back_in_stock": [],
        "out_of_stock": [],
        "price_down": [],
        "price_up": [],
        "added": [],
        "removed": [],
    }

    known: dict[str, dict] = dict(state["products"])
    missing: dict[str, int] = dict(state["missing"])
    new_by_pid = {p.pid: p for p in new}

    for pid, product in new_by_pid.items():
        before = known.get(pid)
        missing.pop(pid, None)          # знайшовся — лічильник промахів скидаємо

        if before is None:
            # Жодного разу не бачили за весь час — справді нова позиція
            result["added"].append(product)
            known[pid] = asdict(product)
            continue

        was_in_stock = bool(before.get("in_stock"))
        if product.in_stock and not was_in_stock:
            result["back_in_stock"].append(product)
        elif was_in_stock and not product.in_stock:
            result["out_of_stock"].append(product)

        old_price = before.get("price")
        if old_price and product.price:
            delta = product.price - old_price
            pct = delta / old_price * 100
            if abs(pct) >= MIN_CHANGE_PCT:
                row = (product, old_price, pct)
                (result["price_down"] if delta < 0 else result["price_up"]).append(row)

        known[pid] = asdict(product)

    # Товари, яких цього разу не було у видачі
    for pid in list(known):
        if pid in new_by_pid:
            continue
        streak = missing.get(pid, 0) + 1
        if streak >= CONFIRM_RUNS:
            result["removed"].append(known.pop(pid))
            missing.pop(pid, None)
        else:
            missing[pid] = streak       # мовчки чекаємо наступного разу

    result["price_down"].sort(key=lambda r: r[2])
    result["price_up"].sort(key=lambda r: -r[2])

    updated = {"products": known, "missing": missing, "runs": state["runs"] + 1}
    return result, updated


# ── Текст повідомлення ───────────────────────────────────────────────────
def money(value: float | None) -> str:
    return f"{value:,.0f}".replace(",", " ") if value else "?"


def block(title: str, rows: list[str]) -> str:
    if not rows:
        return ""
    shown = rows[:MAX_LINES]
    tail = f"\n… ще {len(rows) - len(shown)}" if len(rows) > len(shown) else ""
    return f"\n\n<b>{title} ({len(rows)})</b>\n" + "\n".join(shown) + tail


def build_message(products: list[scrape.Product], changes: dict | None) -> str:
    stamp = dt.datetime.now().strftime("%d.%m %H:%M")
    in_stock = sum(p.in_stock for p in products)

    head = (
        f"<b>Моніторинг цін · Протеїн</b>\n"
        f"{stamp} · {len(products)} товарів · {in_stock} в наявності"
    )

    if changes is None:
        return head + "\n\nПерший запуск — запам'ятав поточні ціни. Порівнювати буду з наступного разу."

    body = ""
    body += block("✓ Знову в продажу", [
        f"• {notify.esc(p.name)} — {money(p.price)} ₴" for p in changes["back_in_stock"]
    ])
    body += block("▼ Ціни впали", [
        f"• {notify.esc(p.name)}\n   {money(old)} → {money(p.price)} ₴  ({pct:+.1f}%)"
        for p, old, pct in changes["price_down"]
    ])
    body += block("▲ Ціни зросли", [
        f"• {notify.esc(p.name)}\n   {money(old)} → {money(p.price)} ₴  ({pct:+.1f}%)"
        for p, old, pct in changes["price_up"]
    ])
    body += block("✕ Зникли з наявності", [
        f"• {notify.esc(p.name)}" for p in changes["out_of_stock"]
    ])
    body += block("+ Нові позиції", [
        f"• {notify.esc(p.name)} — {money(p.price)} ₴" for p in changes["added"]
    ])
    body += block("− Зникли з каталогу", [
        f"• {notify.esc(str(row.get('name', '?')))}" for row in changes["removed"]
    ])

    if not body:
        body = "\n\nЗмін немає — ціни й наявність такі самі, як минулого разу."

    message = head + body
    return message[:TG_LIMIT] + "\n…" if len(message) > TG_LIMIT else message


# ── Точка входу ──────────────────────────────────────────────────────────
def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--file", help="офлайн-режим: розпарсити збережений HTML")
    ap.add_argument("--no-telegram", action="store_true", help="не надсилати, лише показати")
    args = ap.parse_args()

    if args.file:
        path = pathlib.Path(args.file)
        if not path.exists():
            print(f"Файл не знайдено: {path}")
            return 1
        stamp = dt.datetime.now().strftime("%Y-%m-%d %H:%M")
        products = scrape.parse_page(path.read_text(encoding="utf-8"), stamp)
    else:
        products = scrape.scrape_all()

    if not products:
        print("Нічого не зібрано — звіт не надсилаю.")
        return 1

    state = load_snapshot()
    first_run = not state["products"]
    changes, state = compare(state, products)
    if first_run:
        changes = None              # порівнювати не було з чим

    scrape.report(products)
    append_history(products)
    save_snapshot(state)

    pending = len(state["missing"])
    if pending:
        print(f"\nЧекають підтвердження зникнення: {pending} (мовчки)")

    message = build_message(products, changes)
    print("\n─── Текст повідомлення ───")
    print(message.replace("<b>", "").replace("</b>", ""))

    if args.no_telegram:
        print("\n(--no-telegram: надсилання пропущено)")
        return 0

    try:
        notify.send(message)
    except RuntimeError as exc:
        print(f"\nTelegram не прийняв:\n{exc}")
        return 1

    print("\nЗвіт надіслано в Telegram.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
