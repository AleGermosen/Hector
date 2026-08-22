# FinanceTracker — Master Plan

---

## Status

### ✅ Done
- Audit & fix matplotlib removal vs `/expenses` command
- Split `main.py` into modules (`finance/`, `production/`, `utils/`)

---

## Phase 1 — Foundations (do first, everything else builds on this)

These are quick wins and cleanup that make the codebase safe to extend.

- [ ] **Better transaction error messages** — when format is wrong, tell the user exactly what's missing and show the correct format inline
- [ ] **Rich confirmation messages** — after logging, show full parsed details (type, account, amount, category) + updated running balance instead of just "✅ Logged"
- [ ] **Confirmation before logging** — show a parsed preview with ✅/❌ buttons before writing to the sheet; catches typos before they hit the data
- [ ] **Clean up credential files** — remove unused credential file, load all credentials exclusively from env vars (no disk files)
- [ ] **Unit tests for parsing & calculation logic** — `_parse_date_robust`, `_parse_amount_robust`, `_get_data_summary`, `_update_inventory`

---

## Phase 2 — Core Analysis (the meat of making FinanceBot actually useful)

Build out the financial insight commands. These all read from the existing sheet data — no new data structures needed.

- [ ] **`/summary [month]`** — single command showing: income, expenses, net worth, savings rate, and per-category breakdown. The go-to daily command.
- [ ] **Savings rate tracking** — calculate and display % of income kept; include in `/summary` and `/net`
- [ ] **Spending trends** — in `/summary`, show month-over-month change per category (e.g. "Needs: $800 ↑23% vs last month")
- [ ] **`/top [month]`** — top 5 individual expenses for the period; shows where money actually went
- [ ] **`/ytd`** — year-to-date: income, expenses, net, savings rate since Jan 1
- [ ] **`/dash`** — dashboard: current balances + this month's budget status + savings rate + net worth, all in one message. The "at a glance" command.

---

## Phase 3 — Smarter Logging (make the input experience better)

- [ ] **Anomaly detection** — when a new expense is 3x above the user's average for that category, flag it before logging: "⚠️ That's unusually high for Needs (avg $45). Confirm?"
- [ ] **Budget alerts** — after logging an expense, if it pushes a category over the monthly budget, immediately notify: "⚠️ You've exceeded your Wants budget by $120 this month"
- [ ] **Spend forecast** — in `/summary` or `/dash`, show projected month-end spend per category based on current daily pace
- [ ] **Quick-log shortcuts `/ql`** — user-defined aliases stored in a sheet tab. `/ql lunch` → `Expense Cash 15 Wants Lunch`. Saves repetitive typing.
- [ ] **Inline keyboard logging flow** — optional guided mode: bot walks through entry with buttons (Income/Expense → Account → Amount → Category). Better for mobile, fewer format errors.

---

## Phase 4 — Power Features (longer term)

- [x] **Recurring transactions** — `/recurring add|delete`; daily 9am job auto-logs and notifies
- [x] **Goal tracking** — `/setgoal`, `/goals`, `/addtogoal`; progress bars in `/summary`
- [x] **Account health score** — 0–10 score at top of `/dash` with emoji indicator
- [x] **Inventory low-stock alerts** (ProductionBot) — checks "Min Stock" column after each production run

---

## Implementation Notes

- All analysis features (`/summary`, `/dash`, `/top`, `/ytd`, trends) reuse the existing `_get_data_summary()` method — extend it rather than rewrite
- Confirmation flow (Phase 1) will require a `ConversationHandler` for the finance logging path, or use inline keyboard callbacks
- Quick-log shortcuts and recurring transactions both need a new sheet tab as their data store — keep it simple, one tab each
- Anomaly detection needs to compute per-category averages on the fly from sheet history — same data already fetched for summaries
- Build phases in order — Phase 2 analysis is independent of Phase 3 logging changes, so they can overlap
