# Phase 1 — Make the finance reports trustworthy

## Context

`main.py` is a single 1,694-line file running two independent Telegram bots that both write to
Google Sheets: `FinanceBot` (personal expense/income ledger) and `ProductionBot` (factory
production + inventory).

**This task touches `FinanceBot` only.** Do not modify `ProductionBot` or anything it calls.

The ledger sheet has six columns, one transaction per row, with a header row:

```
A: Date (YYYY-MM-DD HH:MM:SS)   B: Type (Income|Expense)   C: Account
D: Amount                        E: Category                F: Description
```

Categories in use: `Needs`, `Wants`, `Savings`, `Debt`, `Transfer`.

Read `main.py` in full before changing anything. Report the plan back before you start editing.

---

## The problem to solve

Money moved into savings is currently recorded as an **expense**, so it counts as spending. When
that money is later spent for real, it counts a second time. Reported spending is inflated and net
worth is understated.

Reproduced — earn 3,000, set aside 500, later pay that 500 as rent:

```
/net       income $3,000   expenses $1,000   net $2,000     <- wrong
/expenses  total $1,000  --  Needs $500 (50%)  Savings $500 (50%)
```

Only $500 actually left. Both reports say $1,000.

### The rule

Treat the `Savings` category exactly the way `Transfer` is already treated: as an **internal
movement**, invisible to both sides of the ledger.

```
NEUTRAL_CATEGORIES = {"transfer", "savings"}     # compared case-insensitively
```

```
Expense  <account>  500  Savings   ->  money set aside       (NOT spending)
Income   <account>  500  Savings   ->  money taken back out  (NOT income)

savings pot = sum(Expense/Savings) - sum(Income/Savings)
```

**The exclusion must be symmetric.** Excluding `Savings` from expenses but not from income is a
bug: the withdrawal row then lands in income and overstates earnings ($3,500 instead of $3,000 in
the case above). Skip neutral-category rows *before* the Income/Expense branch, the same way the
existing `if category == 'Transfer': continue` check does at `main.py:467` and `main.py:588`.

Verified target behaviour — earn 3,000, set aside 500, spend 800 on groceries, pull the 500 back,
pay 500 rent:

```
/net       income $3,000   expenses $1,300   net $1,700
/savings   set aside $500, withdrew $500  ->  pot $0
```

### Where the rule applies — read this table carefully

| Command | Neutral rule? | Notes |
|---|---|---|
| `/net` | **Yes**, both sides | Income and expense totals both skip neutral categories |
| `/expenses` | **Yes** | Pie shows `Needs`, `Wants`, `Debt` only — drop `Savings` from the dict at `main.py:347` |
| `/calcExpenses` | **Yes, for the income base** | See task 3 — this one has a subtlety |
| `/balance` | **No** | A savings row really did leave that account. Balances must not change. |
| `/savings` (new) | n/a | This command exists *because* of the rule |

`Debt` stays a normal expense. Do not make it neutral.

---

## Tasks

### 1. Extract one shared ledger reader (do this first)

`/balance` (:288), `/expenses` (:345), `/net` (:446) and `/calcExpenses` (:565) each re-implement
the same fetch → header-skip → date-filter → parse loop. That duplication is why `/balance` has a
bug the others don't (task 4), and writing the savings rule four times would repeat the mistake.

Split it into a pure function and a thin I/O wrapper so it can be unit-tested without Google:

```python
@dataclass(frozen=True)
class Transaction:
    date: datetime | None
    type: str          # "Income" | "Expense"
    account: str
    amount: float
    category: str
    description: str

    @property
    def is_neutral(self) -> bool: ...

def parse_rows(rows, month=None, year=None) -> list[Transaction]:   # pure, testable
async def load_transactions(sheet, month=None, year=None) -> list[Transaction]:  # I/O wrapper
```

`parse_rows` must:

- Skip any row whose column B is not `Income` or `Expense` (case-insensitive). This drops the
  header and any stray/blank rows generically — do **not** special-case the literal string
  `"date"` the way `main.py:350` does today.
- Parse dates with the existing `_parse_date_robust`, amounts with `_parse_amount_robust`.
- Apply the month/year filter **only** when `month`/`year` are given. When they are not, keep rows
  whose date failed to parse (`/balance` must not silently drop them).

Then rewrite all four commands to call it. Keep everything in `main.py` — splitting the file into
modules is a later phase, not this one.

### 2. Apply the savings rule + add `/savings`

Per the table above. Add a `/savings` command reporting:

- total set aside, total withdrawn, current pot
- accepts the same period argument as the other reports (task 5)

Also update `/help` (`main.py:168`) to document `/savings`, and explain in one line that
`Income … Savings` is how you record taking money back out.

### 3. `/calcExpenses` — keep the 20% bucket, fix the income base

Do **not** change what this command is for: the 50/30/20 view still tracks `Savings` and `Debt`
together in its 20% bucket (`main.py:598`). It never sums a grand total, so it was never wrong.

Two changes only:

- `total_income` must exclude `Income`/`Savings` rows. Otherwise pulling money out of savings
  inflates the base that the 50/30/20 percentages are computed from.
- `actual_savings` becomes **net**: `sum(Expense/Savings) - sum(Income/Savings) + sum(Expense/Debt)`
  within the period.

### 4. `/balance` — stop reporting a phantom account

`/balance` (:288) is the one report that never skips the header row, so the header is parsed as a
transaction and shows up as an account literally named `Account` with a $0.00 balance. Task 1's
row filter fixes this; just confirm it does and add a regression test.

Otherwise `/balance` keeps its current semantics exactly — savings and transfers still move
account balances.

### 5. Amount parsing — same rules on the way in and the way out

`handle_finance` writes with bare `float(parts[2])` (`main.py:235`) while the reports read with
`_parse_amount_robust` (`main.py:147`). So `$50` and `1,500` are accepted when reading and
rejected when logging — and the rejection surfaces as the generic usage message, which reads like
a malformed command.

- Use one parser on both paths.
- **Important:** `_parse_amount_robust` returns `0.0` on failure. That is fine for reports but
  unsafe for a write path — it would silently log a 0. Add a strict variant that raises or returns
  `None`, and use *that* when logging.
- Separate the error messages: an unparseable amount says so; a wrong number of arguments shows
  the usage line.

### 6. Timezone

Every `datetime.now()` uses the server clock, which is UTC on Heroku. At UTC-4 anything logged
after 8pm local is stamped tomorrow, which moves transactions between months at the boundary.

- Add `APP_TIMEZONE` env var, default `America/Santo_Domingo`, read via `zoneinfo.ZoneInfo`.
- Add a `now()` helper and use it everywhere in `FinanceBot` (`:213`, `:250`, `:330`, `:432`, `:551`).
- Leave `ProductionBot`'s `datetime.now()` calls (`:1082`, `:1091`) alone — out of scope.
- Add `tzdata` to `requirements.txt`; slim Python images have no system tz database.

### 7. Period arguments — stop being locked to the current year

All three report commands hard-assign `target_year = datetime.now().year` (`:330`, `:432`, `:551`),
so `/expenses january` can only ever mean January of this year.

Add one shared parser used by every report:

```
(no args)          -> all time
january            -> January, current year
january 2025       -> January 2025
2025               -> all of 2025
last month         -> previous month, rolling the year back correctly at January
this month         -> current month
```

Invalid input gets a clear message naming what was not understood.

### 8. Error handler

Neither bot calls `add_error_handler`, so any unhandled exception is logged and never reaches the
user — the bot simply stops replying. This is what makes the other bugs hard to notice, so it is
in scope even though it is small.

- Register an error handler on `FinanceBot`.
- Reply to the user with a plain apology plus a short correlation id.
- Log the full traceback, and forward it to `ADMIN_CHAT_ID` (new optional env var) when set.

---

## Tests

Add `pytest` (dev dependency only) and `tests/test_finance.py`. The point of task 1's pure
`parse_rows` is that all of this runs without touching Google.

Required cases:

1. **The double-count scenario.** 3,000 income / 500 to savings / 800 groceries / 500 withdrawn /
   500 rent → `/net` income 3,000, expenses 1,300, net 1,700.
2. **Symmetry.** An `Income`/`Savings` row does not increase reported income.
3. **Savings pot.** Same fixture → pot 0. Set-aside-only fixture → pot equals the amount.
4. **`/balance` is unaffected by the rule** and contains no account named `Account`.
5. **`/expenses`** contains no `Savings` slice, and its total equals Needs + Wants + Debt.
6. **`/calcExpenses`** income base ignores savings withdrawals; `actual_savings` is net of them.
7. **Amounts.** `15.50`, `1,500`, `$50` all log identically; garbage is rejected, never logged as 0.
8. **Periods.** Each form in task 7, including the January rollover for `last month`.
9. **Timezone.** A transaction logged at 23:30 local on the last day of a month reports in that
   month, not the next.
10. **Header/junk rows** are dropped by `parse_rows` regardless of their first cell.

---

## Explicitly out of scope

Do not do any of these, even if they look tempting while you are in the file:

- Anything in `ProductionBot` — recipes, inventory, `/newlog`, `/addstock`.
- Changing how `Debt` is treated.
- Migrating, rewriting, or reformatting the Google Sheet. The rule is applied at read time, so
  history recomputes correctly on its own. **No data migration is needed.**
- Splitting `main.py` into modules (later phase).
- Fixing `/calc`'s `eval` (phase 2 — leave it alone for now, it gets its own change).
- New features: `/undo`, `/recent`, trend charts, CSV export, budget config.
- New runtime dependencies beyond `zoneinfo` (stdlib) and `tzdata`.

---

## Acceptance criteria

- [ ] `pytest` passes, covering all ten cases above.
- [ ] The four report commands share one loader; no command re-implements the parse loop.
- [ ] `/net` and `/expenses` ignore `Savings` on both the income and expense sides.
- [ ] `/balance` output is byte-identical to before except the phantom `Account` row is gone.
- [ ] `/calcExpenses` still shows the 50/30/20 buckets with `Savings`+`Debt` in the 20% line.
- [ ] `/savings` reports set aside, withdrawn, and pot.
- [ ] `python -m py_compile main.py` is clean and `ProductionBot`'s diff is empty.
- [ ] New env vars (`APP_TIMEZONE`, `ADMIN_CHAT_ID`) documented in a short `README.md` alongside
      the existing six.

Work on branch `claude/app-improvement-planning-5yyyj9`. Commit in logical chunks — the loader
extraction separate from the savings rule, so the behaviour change is reviewable on its own. Do not
open a pull request unless asked.
