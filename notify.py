"""
notify.py — надсилання повідомлень у Telegram.

Окремий файл, бо це самостійна цеглинка: сьогодні шлемо звіт про ціни,
завтра підключимо цей самий модуль до іншого проєкту без жодної правки.

Перевірка, що все налаштовано:
    py notify.py
"""

from __future__ import annotations

import html
import os
import sys

import requests
from dotenv import load_dotenv

# Читає файл .env і кладе його вміст у змінні оточення
load_dotenv()

TOKEN = os.getenv("TELEGRAM_TOKEN", "").strip()
CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "").strip()

# Базова адреса винесена в змінну, щоб код можна було тестувати
# на локальній заглушці, не смикаючи справжній Telegram
API_BASE = os.getenv("TELEGRAM_API_BASE", "https://api.telegram.org").rstrip("/")

TIMEOUT = 30


def esc(text: str) -> str:
    """
    Екранує символи < > &, які Telegram інакше вважатиме розміткою.

    Потрібно тому, що в назвах товарів трапляється '&' (Bar & Go),
    і без екранування Telegram відмовиться надсилати таке повідомлення.
    """
    return html.escape(str(text), quote=False)


def send(text: str) -> dict:
    """Надсилає одне повідомлення. Кидає помилку, якщо не вийшло."""
    if not TOKEN or not CHAT_ID:
        raise RuntimeError(
            "Немає TELEGRAM_TOKEN або TELEGRAM_CHAT_ID.\n"
            "Перевірте, що поруч зі скриптом лежить файл .env і в ньому обидва рядки."
        )

    url = f"{API_BASE}/bot{TOKEN}/sendMessage"
    payload = {
        "chat_id": CHAT_ID,
        "text": text,
        "parse_mode": "HTML",
        "disable_web_page_preview": True,
    }

    try:
        resp = requests.post(url, json=payload, timeout=TIMEOUT)
    except requests.RequestException as exc:
        raise RuntimeError(f"Не вдалося достукатись до Telegram: {exc}") from exc

    try:
        data = resp.json()
    except ValueError:
        raise RuntimeError(f"Telegram відповів не JSON-ом (HTTP {resp.status_code})")

    if not data.get("ok"):
        raise RuntimeError(
            f"Telegram відмовив: {data.get('description', 'без пояснення')}"
        )

    return data


def main() -> int:
    print("Перевіряю налаштування…")
    print(f"  TELEGRAM_TOKEN:   {'знайдено' if TOKEN else 'ПОРОЖНЬО'}")
    print(f"  TELEGRAM_CHAT_ID: {CHAT_ID or 'ПОРОЖНЬО'}")

    try:
        send(
            "<b>Automation Alerts</b>\n"
            "Зв'язок працює. Це тестове повідомлення від price-monitor."
        )
    except RuntimeError as exc:
        print(f"\nНЕ ВИЙШЛО:\n{exc}")
        return 1

    print("\nПовідомлення надіслано — перевірте Telegram.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
