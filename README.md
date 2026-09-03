# Hector

Two independent Telegram bots that use Google Sheets as their database, run from a single
worker process:

- **FinanceBot** — a personal income/expense ledger with reports, charts, budgets, savings
  goals and recurring transactions. Bilingual (English / Spanish).
- **ProductionBot** — factory production logging and ingredient inventory, driven by recipes
  stored in a spreadsheet.

Each bot starts only if its token is present, so you can run one, the other, or both.

---

## Requirements

- Python 3.12 (see `.python-version`)
- A Google Cloud **service account** with the Sheets and Drive APIs enabled
- One Telegram bot token per bot you want to run ([@BotFather](https://t.me/BotFather))
- Each spreadsheet shared with the service account's email address

Dependencies (`requirements.txt`):

```
python-telegram-bot[job-queue]~=22.6
gspread~=6.2
matplotlib~=3.10
tzdata~=2026.1
```

The `[job-queue]` extra is required — without it, recurring transactions and monthly
summaries silently do not run.

---

## Quick start

```bash
git clone https://github.com/AleGermosen/Hector.git
cd Hector

python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt

# export the environment variables below, then:
python main.py
```

`main.py` builds whichever bots are configured, starts long polling, and shuts them down
cleanly on `SIGINT`/`SIGTERM`. If a bot's polling loop dies unexpectedly the process exits
non-zero so the platform restarts it.

---

## Configuration

All configuration comes from environment variables — no credentials are ever read from disk.
`config.py` and `credentials.json` are gitignored.

| Variable | Required for | Description |
|---|---|---|
| `TELEGRAM_BOT_TOKEN` | FinanceBot | Telegram token; FinanceBot starts only if this is set |
| `GSPREAD_CREDENTIALS` | FinanceBot | Service-account JSON key, as a single-line JSON string |
| `USER_SHEET_MAPPING` | FinanceBot | JSON object mapping Telegram user IDs to spreadsheet names, e.g. `{"12345678": "My Finances"}` |
| `STRICT_MODE_USERS` | optional | JSON array of user IDs restricted to the fixed expense categories, e.g. `["12345678"]` |
| `SECOND_BOT_TOKEN` | ProductionBot | Telegram token; ProductionBot starts only if this is set |
| `PRODUCTION_GSPREAD_CREDENTIALS` | ProductionBot | Service-account JSON key for the production spreadsheet |
| `PRODUCTION_SHEET_ID` | ProductionBot | Spreadsheet key (the ID from its URL) holding the recipes |
| `APP_TIMEZONE` | optional | IANA timezone for dates and scheduled jobs (default `America/Santo_Domingo`) |
| `ADMIN_CHAT_ID` | optional | Chat that receives FinanceBot error reports |

Users not listed in `USER_SHEET_MAPPING` are refused; `/start` replies with their Telegram ID
so an admin can add them.

---

## Spreadsheet layout

### FinanceBot

The first worksheet is the ledger — one transaction per row, six columns:

| A | B | C | D | E | F |
|---|---|---|---|---|---|
| Date | Type | Account | Amount | Category | Description |

`Type` must be `Income`, `Expense` or `Transfer`; rows with anything else are ignored by the
reports. Accounts are created implicitly the first time a name is used. Fixed expense
categories are **Needs**, **Wants**, **Savings** and **Debt**; `Transfer` and `Savings` are
treated as internal movements and excluded from income/expense totals. Run `/checksheet` to
verify the connection and column layout.

Three helper tabs are created automatically on first use:

| Tab | Columns |
|---|---|
| `Shortcuts` | Name, Transaction |
| `Recurring` | Name, Transaction, Day, Last Logged |
| `Goals` | Name, Target, Saved |

### ProductionBot

The first worksheet of `PRODUCTION_SHEET_ID` holds the recipes, one ingredient per row:

| Category | Product | Base Gallons | Ingredient | Amount | Unit |
|---|---|---|---|---|---|

Production runs are appended to a per-run worksheet with the columns Date, Product Name, Batch
Code, Ingredient Name, Amount, Unit, Total Gallons, Weighed By, Received By. An `Inventory` tab
(Ingredient, Quantity, Unit, Min Stock) is created on demand; add a `Min Stock` value to get
low-stock alerts after a run.

---

## Using FinanceBot

Log a transaction as free text — the bot shows a parsed preview with confirm/cancel buttons
before anything is written:

```
[Income|Expense] [Account] [Category] [Description...] [Amount]

Expense Cash Needs Groceries 50
Income Bank Salary October 2500
Transfer Cash to Bank 500 rent money
```

A `Savings` expense also auto-logs the matching income row, and a transfer writes both legs.

### Commands

**Logging** — `/log` (guided, step-by-step), `/ql` (quick-log shortcuts), `/undo`, `/recent`,
`/recurring`

**Reports** — `/dash`, `/summary`, `/balance`, `/top`, `/ytd`, `/net`, `/expenses`,
`/calcexpenses` (50/30/20 budget), `/savings`, `/trend`

**Goals** — `/goals`, `/setgoal`, `/addtogoal`

**Misc** — `/exchange` (USD ↔ RD$), `/calc`, `/quiet` (mute monthly summaries), `/lang en|es`,
`/checksheet`, `/start`, `/help`, `/cancel`

Reports accept a period: no argument (all time), `this month`, `last month`, `2025`, `january`,
or `january 2025`.

### Quick-log shortcuts

```
/ql add lunch Expense Cash Wants Lunch 15    # save a shortcut
/ql add gas Expense Cash Needs Gas ?         # ? = ask for the amount
/ql lunch                                    # fire it
/ql delete lunch                             # remove it
```

### Scheduled jobs

When the job queue is available, FinanceBot runs recurring transactions daily at 09:00 and
sends a monthly summary on the 1st at 09:00, both in `APP_TIMEZONE`.

---

## Using ProductionBot

**Logging** — `/newlog` (`/nl`) to type a product name, `/knownproduct` (`/kp`) to pick from a
list, `/cancel` to abort

**Inventory** — `/inventory` to view stock levels, `/addstock` to add stock

**System** — `/reload` to re-read recipes from the sheet, `/check_sheet` to test the
connection, `/help`

Recipes are scaled from `Base Gallons` to the batch size you enter, and confirmed ingredient
amounts are subtracted from the `Inventory` tab.

---

## Project layout

```
main.py                  Entry point — builds and supervises both bots
finance/bot.py           FinanceBot: parsing, reports, charts, goals, recurring
finance/strings.py       User-facing strings (EN/ES) keyed by slug
production/bot.py        ProductionBot: production runs and inventory
utils/sheets.py          Google client helper used by the Heroku bundle
tests/test_finance.py    Unit tests for the pure parsing/calculation logic
build_heroku.py          Concatenates the modules into main_heroku.py
deploy_heroku.sh         Builds the bundle and pushes it to Heroku
heroku_entry.py          Entry point used inside the bundle
docs/                    Implementation prompts
tasks.md                 Roadmap and status
```

---

## Tests

The suite covers the pure logic — date and amount parsing, period parsing, transaction text
parsing, report aggregation and the safe calculator — with no network calls or credentials
needed:

```bash
pip install pytest pytest-asyncio
python -m pytest tests/ -v
```

`pytest-asyncio` is needed for the retry and cache tests, which are `async`. Some tests
currently fail because they were written against an older transaction format (amount before
category, rather than last) and an older sheet-write API; they need updating to match the
current code.

---

## Deployment (Heroku)

The `Procfile` declares a single worker: `worker: python main.py`.

Heroku runs one file, so `build_heroku.py` concatenates the modules into `main_heroku.py`
(de-duplicating imports and dropping the local cross-module ones). `deploy_heroku.sh` does the
whole dance — build the bundle, commit it to a throwaway `heroku-deploy-tmp` branch as
`main.py`, force-push it to Heroku, then clean up and return to `master`:

```bash
bash deploy_heroku.sh
heroku logs --tail -a hector-finance-tracker
```

Set every environment variable from the table above as Heroku config vars, and scale the worker
with `heroku ps:scale worker=1`.
