# Price & Stock Monitor

Tracks prices and stock across an e-commerce category and sends a Telegram alert
**only when something actually changes**. Runs unattended on GitHub Actions —
no server, no hosting cost.

Built against a live Ukrainian sports-nutrition retailer: 83 products across
4 paginated pages.

## Sample alert

```
Моніторинг цін · Протеїн
09.09 07:00 · 83 товарів · 40 в наявності

✓ Знову в продажу (1)
• 100% Standard Whey - 2 кг — 2 999 ₴

▼ Ціни впали (1)
• 100% Whey Gold Standard - 2,27 кг
   5 555 → 4 999 ₴  (-10.0%)
```

## What it does

Each run:

1. crawls the category (twice — see below)
2. compares against the previous run
3. appends the snapshot to `history.csv`
4. sends a Telegram message if, and only if, something changed

Tracked events: price drops, price rises, back in stock, out of stock,
new products, discontinued products.

## Engineering notes

The interesting part of this project was not the parsing. It was making the
alerts trustworthy.

### robots.txt is read before the first line of code

The target disallows `?sort=`, `?limit=` and `?filter=`. That rules out the
obvious shortcut — pulling the whole catalogue in one request with
`?limit=100`. The crawler paginates with `?page=N` only, identifies itself
honestly in the `User-Agent`, and sleeps 2 s between requests.

### Records are keyed on the store's product ID, never on the name

Every card carries `data-pid`, the shop's internal database ID. Product names
get edited; IDs don't. Keying the history on a name would break the price
series on every rename — and silently suppress the price-drop alert, because a
renamed product reads as a brand-new one with no history.

The URL is no better: seven products in this catalogue share a single URL, being
size variants of one product page. The ID is the only stable key.

### The catalogue is crawled twice per run

The shop reshuffles products between pages between requests. A single pass sees
about 86 % of the catalogue (71 of 83) — a different subset each time. Two
passes merged by product ID reach 99 % (82 of 83).

The drift is confined to the first two pages, where ordering depends on live
sales data; the tail of the catalogue is stable.

### Disappearance is confirmed over five runs

A product missing from one crawl has not necessarily left the shop. Products
accumulate a miss counter and are reported as discontinued only after five
consecutive absences.

Without this the monitor produced roughly one false "discontinued" alert per
week — the kind of noise that gets a tool switched off.

### Silence is a feature

Price moves under 1 % are dropped as rounding noise. When nothing changed, the
message says so in one line. A monitor that fires every morning regardless of
events gets muted within a week.

## Stack

Python 3 · requests · BeautifulSoup · GitHub Actions · Telegram Bot API

## Files

| File | Role |
|---|---|
| `scrape.py` | crawls the category, parses cards into `Product` records |
| `notify.py` | Telegram delivery |
| `run.py` | orchestration: compare, archive, report |
| `discover.py` | one-off tool used to map the page structure |
| `snapshot.json` | last known state — the monitor's memory |
| `history.csv` | append-only archive of every run |

## Running it locally

```bash
python -m venv .venv
source .venv/bin/activate        # Windows: .venv\Scripts\Activate.ps1
pip install -r requirements.txt
```

Create a `.env` file:

```
TELEGRAM_TOKEN=your_bot_token
TELEGRAM_CHAT_ID=your_chat_id
```

Then:

```bash
python run.py                  # crawl, compare, notify
python run.py --no-telegram    # crawl and compare, print only
python run.py --file page.html # parse a saved page, no network
```

## Scheduling

`.github/workflows/monitor.yml` runs the monitor daily and commits the updated
`snapshot.json` and `history.csv` back to the repository, so the price history
lives in the git log.

Credentials are stored as repository secrets, never in the code.
