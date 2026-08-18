# Hector — implementation prompts

Five phases, sequenced so each one makes the next cheaper. Every phase below is a **standalone
prompt**: copy the shared context plus one phase, paste it into Claude Code, and it has everything
it needs. Run them in order — later phases assume earlier ones landed.

| Phase | Goal | Effort |
|---|---|---|
| 1 | Reports you can trust — savings stops counting as spending | ~1 day |
| 2 | Don't lose it, don't leak it — writes, `eval`, escaping, errors | ~half day |
| 3 | Survive the platform — restarts, rate limits, deploys | ~half day |
| 4 | Foundations — modern auth, module split, CI, command menu | ~1 day |
| 5 | Features — the backlog, each independently runnable | ongoing |

---

## Shared context

Include this section with whichever phase you are running.

`main.py` is a single 1,694-line file running two independent Telegram bots that both write to
Google Sheets: `FinanceBot` (personal expense/income ledger) and `ProductionBot` (factory
production + inventory).

**Every phase in this document touches `FinanceBot` only.** Do not modify `ProductionBot` or
anything it calls unless a task says so explicitly — one task in phase 4 does, and it is flagged.

The ledger sheet has six columns, one transaction per row, with a header row:

```
A: Date (YYYY-MM-DD HH:MM:SS)   B: Type (Income|Expense)   C: Account
D: Amount                        E: Category                F: Description
```

Categories in use: `Needs`, `Wants`, `Savings`, `Debt`, `Transfer`.

`Savings` and `Transfer` are **neutral categories** — internal movements of your own money, not
income and not spending. Phase 1 establishes this rule; later phases assume it.

### Working rules

- Read `main.py` in full before changing anything. Report the plan back before you start editing.
- Work on branch `claude/app-improvement-planning-5yyyj9`.
- Commit in logical chunks — refactors separate from behaviour changes, so each is reviewable.
- Do not open a pull request unless asked.
- Never migrate, rewrite, or reformat the Google Sheet. Reports are recomputed from raw rows on
  every call, so behaviour changes apply to history automatically. **No data migration, ever.**
- No new runtime dependencies beyond what a task names explicitly.
- `python -m py_compile main.py` must be clean, and `git diff` on `ProductionBot` must be empty.

---

## Phase 1 — Make the finance reports trustworthy

**Goal:** money set aside stops being counted as spending, and the reporting code stops being four
copies of the same loop.

### The problem

Money moved into savings is recorded as an **expense**, so it counts as spending. When that money
is later spent for real, it counts a second time. Reported spending is inflated and net worth is
understated.

Reproduced — earn 3,000, set aside 500, later pay that 500 as rent:

```
/net       income $3,000   expenses $1,000   net $2,000     <- wrong
/expenses  total $1,000  --  Needs $500 (50%)  Savings $500 (50%)
```

Only $500 actually left. Both reports say $1,000.

### The rule

Treat the `Savings` category exactly the way `Transfer` is already treated: as an internal
movement, invisible to both sides of the ledger.

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

### Task 1 — Extract one shared ledger reader (do this first)

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

Then rewrite all four commands to call it. Keep everything in `main.py` — the module split is
phase 4, not this one.

### Task 2 — Apply the savings rule and add `/savings`

Per the table above. Add a `/savings` command reporting total set aside, total withdrawn, and the
current pot, accepting the same period argument as the other reports (task 7).

Update `/help` (`main.py:168`) to document `/savings`, and explain in one line that
`Income … Savings` is how you record taking money back out.

### Task 3 — `/calcExpenses`: keep the 20% bucket, fix the income base

Do **not** change what this command is for: the 50/30/20 view still tracks `Savings` and `Debt`
together in its 20% bucket (`main.py:598`). It never sums a grand total, so it was never wrong.

Two changes only:

- `total_income` must exclude `Income`/`Savings` rows. Otherwise pulling money out of savings
  inflates the base the percentages are computed from.
- `actual_savings` becomes **net**: `sum(Expense/Savings) - sum(Income/Savings) + sum(Expense/Debt)`
  within the period.

### Task 4 — `/balance`: stop reporting a phantom account

`/balance` (:288) is the one report that never skips the header row, so the header is parsed as a
transaction and shows up as an account literally named `Account` with a $0.00 balance. Task 1's row
filter fixes this; confirm it does and add a regression test. Otherwise `/balance` keeps its current
semantics exactly — savings and transfers still move account balances.

### Task 5 — Amount parsing: same rules in and out

`handle_finance` writes with bare `float(parts[2])` (`main.py:235`) while the reports read with
`_parse_amount_robust` (`main.py:147`). So `$50` and `1,500` are accepted when reading and rejected
when logging — and the rejection surfaces as the generic usage message, which reads like a
malformed command.

- Use one parser on both paths.
- **Important:** `_parse_amount_robust` returns `0.0` on failure. Fine for reports, unsafe for a
  write path — it would silently log a 0. Add a strict variant that raises or returns `None`, and
  use *that* when logging.
- Separate the error messages: an unparseable amount says so; a wrong number of arguments shows the
  usage line.

### Task 6 — Timezone

Every `datetime.now()` uses the server clock, which is UTC on Heroku. At UTC-4 anything logged after
8pm local is stamped tomorrow, which moves transactions between months at the boundary.

- Add `APP_TIMEZONE` env var, default `America/Santo_Domingo`, read via `zoneinfo.ZoneInfo`.
- Add a `now()` helper and use it everywhere in `FinanceBot` (`:213`, `:250`, `:330`, `:432`, `:551`).
- Leave `ProductionBot`'s `datetime.now()` calls (`:1082`, `:1091`) alone — out of scope.
- Add `tzdata` to `requirements.txt`; slim Python images have no system tz database.

### Task 7 — Period arguments

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

### Task 8 — Error handler

Neither bot calls `add_error_handler`, so any unhandled exception is logged and never reaches the
user — the bot simply stops replying. This is what makes the other bugs hard to notice, so it is in
scope even though it is small.

- Register an error handler on `FinanceBot`.
- Reply with a plain apology plus a short correlation id.
- Log the full traceback, and forward it to `ADMIN_CHAT_ID` (new optional env var) when set.

### Tests

Add `pytest` (dev dependency only) and `tests/test_finance.py`. The point of task 1's pure
`parse_rows` is that all of this runs without touching Google.

1. **The double-count scenario.** 3,000 income / 500 to savings / 800 groceries / 500 withdrawn /
   500 rent → `/net` income 3,000, expenses 1,300, net 1,700.
2. **Symmetry.** An `Income`/`Savings` row does not increase reported income.
3. **Savings pot.** Same fixture → pot 0. Set-aside-only fixture → pot equals the amount.
4. **`/balance` is unaffected by the rule** and contains no account named `Account`.
5. **`/expenses`** has no `Savings` slice; total equals Needs + Wants + Debt.
6. **`/calcExpenses`** income base ignores savings withdrawals; `actual_savings` is net of them.
7. **Amounts.** `15.50`, `1,500`, `$50` all log identically; garbage is rejected, never logged as 0.
8. **Periods.** Each form above, including the January rollover for `last month`.
9. **Timezone.** A transaction logged at 23:30 local on the last day of a month reports in that
   month, not the next.
10. **Header/junk rows** are dropped by `parse_rows` regardless of their first cell.

### Out of scope for phase 1

`/calc`'s `eval` (phase 2), the row-insert bug (phase 2), Markdown escaping (phase 2), module split
(phase 4), `Decimal` (phase 4), and every new feature (phase 5).

### Acceptance criteria

- [ ] `pytest` passes, covering all ten cases above.
- [ ] The four report commands share one loader; no command re-implements the parse loop.
- [ ] `/net` and `/expenses` ignore `Savings` on both the income and expense sides.
- [ ] `/balance` output is identical to before except the phantom `Account` row is gone.
- [ ] `/calcExpenses` still shows 50/30/20 with `Savings`+`Debt` in the 20% line.
- [ ] `/savings` reports set aside, withdrawn, and pot.
- [ ] New env vars (`APP_TIMEZONE`, `ADMIN_CHAT_ID`) documented in a short `README.md` alongside the
      existing six.

---

## Phase 2 — Don't lose it, don't leak it

**Goal:** stop writes from corrupting the ledger, close the `eval` hole, and make failures legible.
**Prerequisite:** phase 1.

### Task 1 — Writes must append, not compute an index

`_insert_row` (:265) and `_insert_rows` (:271) compute the target row as
`len(sheet.col_values(1)) + 1`. gspread trims trailing empty cells from a column, so if the last
rows have an empty column A — a note typed into column B, a hand-entered row without a date — the
computed index lands **inside** existing data and `insert_row` pushes real transactions down.

- Replace both with `append_row` / `append_rows` using `table_range='A1'` and
  `value_input_option='USER_ENTERED'`. No index arithmetic anywhere.
- This also halves the API calls per log, since `col_values` was a separate round trip.
- The two rows of a transfer must stay adjacent: use a single `append_rows` call, not two
  `append_row` calls.

Test with a fake sheet object that records calls: assert `append_rows` is used for transfers, that
no code path reads `col_values`, and that a sheet whose last column-A cell is blank still appends
past the true final row.

### Task 2 — Replace `/calc`'s `eval`

`main.py:314` runs `eval(expression, {"__builtins__": {}}, {})`. Emptying builtins does not stop
attribute traversal off a literal — `().__class__.__mro__[1].__subclasses__()` reaches 148 classes,
which is the standard route to `subprocess`. `/calc` is also the only finance command that never
checks the user mapping, so it is reachable by anyone who finds the bot.

- Write an AST-walking evaluator. Parse with `ast.parse(expr, mode='eval')` and walk the tree,
  permitting only: `Expression`, `BinOp` with `Add|Sub|Mult|Div|FloorDiv|Mod|Pow`, `UnaryOp` with
  `UAdd|USub`, and `Constant` holding an `int` or `float`. Reject every other node type by class
  name in the error message.
- Cap exponentiation before evaluating: reject `Pow` whose right operand is a constant above ~100,
  so `/calc 9**9**9` cannot pin the CPU. Also cap the input string length.
- Handle `ZeroDivisionError` with a friendly message rather than the generic failure.
- Add the same authorization check the other finance commands perform.

Tests: `5 * 2` → 10; `2 ** 10` → 1024; `(3+4)/2` → 3.5; `9**9**9` rejected; `().__class__` rejected;
`__import__('os')` rejected; `1/0` gives a friendly error. Assert the string `eval(` no longer
appears in the file.

### Task 3 — HTML parse mode with escaping

Reports interpolate your own text — account names, categories, descriptions — into messages sent
with `parse_mode='Markdown'` and no escaping (`:305`, `:402`, `:532`, `:623`). An account named
`BHD_ahorro`, or a description containing `*`, makes Telegram reject the whole message with a 400.

- Set `parse_mode=ParseMode.HTML` once via `Defaults` on `FinanceBot`'s `ApplicationBuilder`, and
  drop the per-call `parse_mode` arguments.
- Convert `**bold**` to `<b>bold</b>` throughout `FinanceBot`.
- Run every interpolated value through `html.escape` — account, category, description, and any
  error text.
- `ProductionBot` builds its own `Application`, so this does not touch it. Confirm that.

Test that a fixture containing an account `BHD_ahorro` and a description `50% off <b>sale</b> & more`
produces a body with no unescaped `<`, `>` or `&`.

### Task 4 — Stop echoing raw API errors

`❌ Sheet Error: {str(e)}` (`:262`, `:308`, `:419`, `:537`) forwards whatever gspread raised —
spreadsheet ids, service-account addresses, internal URLs.

- Generate a short correlation id (`uuid4().hex[:8]`), log the full exception against it, and show
  the user a plain sentence plus that id.
- Apply to every user-facing `except` block in `FinanceBot`, including the ones phase 1 added.

### Acceptance criteria

- [ ] `pytest` passes, including the new cases above.
- [ ] `eval(` appears nowhere in `main.py`.
- [ ] No sheet write computes a row index; all writes append.
- [ ] No `parse_mode='Markdown'` remains in `FinanceBot`.
- [ ] No user-facing message contains `str(e)`.
- [ ] `/calc` rejects an unauthorized user.

---

## Phase 3 — Survive the platform

**Goal:** restarts, rate limits and deploys stop causing silent breakage.
**Prerequisite:** phase 2.

### Task 1 — Handle SIGTERM

`main()` (:1617) creates `stop_signal = asyncio.Event()` and awaits it forever, but nothing ever
sets it and no signal handlers are installed. Heroku sends SIGTERM and then `SIGKILL`s, so the
graceful `bot.stop()` in the `finally` block never runs — including mid-write to the sheet.

- Install `loop.add_signal_handler` for `SIGTERM` and `SIGINT` to set the event.
- Wrap in `try/except NotImplementedError` so the app still starts on platforms without it.

### Task 2 — Supervise the pollers

Nothing watches the updater tasks. If one bot's polling stops permanently, the dyno keeps running,
no alert fires, and the other bot masks the outage.

- Run each bot as a task and wait with `asyncio.wait(..., return_when=FIRST_COMPLETED)`.
- If any task finishes unexpectedly, log the reason, stop the others, and exit **non-zero** so the
  platform restarts the process.

### Task 3 — Cache the sheet handle, retry on rate limits

`get_user_sheet` (:82) calls `client.open()` on every single message — an extra API round trip each
time. Sheets allows 60 reads per minute; a burst of logging plus a couple of reports hits a 429,
which currently reaches the user as a raw error with no retry.

- Cache the worksheet per user with a TTL (~10 minutes), invalidated on any `APIError`.
- Add a retry helper with exponential backoff and jitter (1s, 2s, 4s, 8s).

**Retry reads and writes differently — this matters.** A 429 means the request was rejected before
it applied, so retrying is safe for reads and writes alike. A 500/503 is ambiguous: the write may
have landed, and retrying would duplicate a transaction. So:

```
reads   -> retry on 429, 500, 503
writes  -> retry on 429 only; surface 500/503 to the user with the correlation id
```

### Task 4 — Cache the parsed ledger

`get_all_values()` pulls all history to compute one month, and four commands each do it
independently — so running `/net` then `/expenses` downloads everything twice.

- Cache the parsed `list[Transaction]` per user with a short TTL (~60s).
- Invalidate that user's entry immediately after any successful write, so a freshly logged
  transaction shows up in the next report.

### Task 5 — Pin the dependencies

`requirements.txt` is four bare package names, and `python-telegram-bot` makes breaking changes
across majors — so a deploy can fail for reasons unrelated to anything you changed.

- Pin every package with a compatible-release specifier against the versions currently installed.
- Add `runtime.txt` naming the Python version.
- Ensure the file ends with a newline.

### Tests

Use a fake gspread client that raises `APIError` with a settable status code, and a monkeypatched
clock:

- A read retried on 503 eventually succeeds; the backoff sleeps grow.
- A **write** that gets 503 is **not** retried; a write that gets 429 is.
- Retries give up after the cap rather than looping forever.
- The sheet cache returns the same handle within the TTL and refetches after it.
- The ledger cache is dropped after a write.

### Acceptance criteria

- [ ] `pytest` passes.
- [ ] SIGTERM triggers the graceful shutdown path.
- [ ] A dead poller exits the process non-zero.
- [ ] Logging a transaction then running `/net` reflects it immediately.
- [ ] `requirements.txt` is fully pinned and `runtime.txt` exists.

---

## Phase 4 — Foundations

**Goal:** lock in phases 1–3 so they cannot regress, and get off end-of-life auth.
**Prerequisite:** phase 3.

### Task 1 — Replace oauth2client

`get_google_client` (:34) uses `oauth2client`, which Google deprecated in 2018 and no longer
patches. Modern gspread does not need it.

- Swap `ServiceAccountCredentials.from_json_keyfile_dict` for
  `gspread.service_account_from_dict(creds_dict)` on `google-auth`.
- Remove `oauth2client` from `requirements.txt`.

**This is the one change that reaches `ProductionBot`,** and it is permitted for this task only:
`check_sheet_cmd` (:1008) introspects `self.client.auth.service_account_email` and
`signer_email`. Verify `/check_sheet` still reports the bot email under `google-auth`, and if the
attribute name differs, fix that line — nothing else in `ProductionBot`.

### Task 2 — Split the file and add CI

`main.py` is 1,694 lines holding two unrelated products.

```
main.py                     thin entrypoint: env loading, wiring, run
hector/shared/google.py     get_google_client, retry helpers
hector/shared/telegram.py   error handler, escaping, Defaults
hector/finance/ledger.py    Transaction, parse_rows, period parsing, neutral rule
hector/finance/reports.py   balance / expenses / net / calcExpenses / savings
hector/finance/bot.py       FinanceBot: handlers and wiring
hector/production/bot.py    ProductionBot, moved verbatim
```

Move `ProductionBot` **wholesale, without editing its logic** — imports and indentation only. Its
diff should be a pure relocation.

Add `.github/workflows/ci.yml` running `pytest` and `python -m compileall` on push and pull request.

### Task 3 — Command menu and generated help

`set_my_commands` is never called, so Telegram shows no command list and every command has to be
remembered. `/cancel` is filtered out by the catch-all handler and produces no reply at all.

- Define one `COMMANDS` list of `(name, description)`.
- Register it via `set_my_commands` on startup, and generate `/help` from the same list so the two
  cannot drift.
- Give `/cancel` a real reply.

### Task 4 — Decimal for money

Balances accumulate binary float error across thousands of rows.

- Parse amounts to `Decimal`; quantize to two places at display time only.
- Write `str(decimal)` to the sheet.
- matplotlib needs floats — convert at the chart boundary, not before.

### Acceptance criteria

- [ ] `pytest` and CI both pass.
- [ ] No module exceeds ~400 lines.
- [ ] `ProductionBot`'s diff is relocation plus the one permitted `check_sheet_cmd` line.
- [ ] `oauth2client` is gone from the codebase and `requirements.txt`.
- [ ] Typing `/` in Telegram shows the full command list.

---

## Phase 5 — Features

**Goal:** the backlog. **Prerequisite:** phase 4 (or phase 1 at minimum — each item says).

Unlike phases 1–4, these are independent. Run one at a time: paste the shared context, this phase
header, and the single item you want. ★ marks where I would start.

### ★ 5.1 — `/undo` and an Undo button

Needs phase 2 (append-only writes). The biggest gap in the bot: a mistyped amount currently means
opening Sheets on your phone.

- Attach an inline "Undo" button to the confirmation message after each successful log.
- `/undo` removes that user's most recent transaction.

**The race matters.** Row indices shift as rows are added, so do not store an index in
`callback_data` (which is also capped at 64 bytes). Instead store the row's exact values, then
scan from the bottom of the sheet for an exact match and delete that row. If no match is found the
row is already gone — say so rather than deleting the wrong one.

### ★ 5.2 — `/recent [n]`

Needs phase 1. Show the last *n* transactions (default 10) with date, type, amount, category and
description. You need this to know what there is to undo.

### ★ 5.3 — Category buttons

Needs phase 1. Strict-mode users must type `Needs`/`Wants`/`Savings`/`Debt` exactly and are
rejected otherwise (`main.py:238`). When a strict user's message is missing or misspells the
category, reply with an inline keyboard of the four valid ones instead of an error, and complete
the log from the button press.

### ★ 5.4 — Monthly summary push

Needs phase 1. PTB's `JobQueue` is available and completely unused.

On the 1st of each month at 09:00 in `APP_TIMEZONE`, send each user in the mapping last month's
net worth, budget status and savings pot, unprompted. For private chats the Telegram user id is
the chat id, so the existing mapping is enough. Make it opt-out with a `/quiet` toggle stored in
`bot_data`.

### ★ 5.5 — `/trend [months]`

Needs phase 1. matplotlib is already a dependency and only ever draws pie charts. Draw a line
chart of income vs expenses by month for the last *n* months (default 6), with the savings pot as
a third series. Reuse the same buffer-and-`reply_photo` pattern the existing charts use.

### 5.6 — Backdating

Needs phase 1. Every entry is stamped now, so you cannot catch up on a weekend of receipts. Accept
an optional leading date token — `yesterday`, `2026-08-14`, `14/08` — before the type keyword,
reusing phase 1's date parsing. Reject future dates.

### 5.7 — `/edit last`

Needs 5.1's row-matching helper. Change the category, amount or description of the most recent
transaction without deleting and re-entering it.

### 5.8 — Recurring transactions

Needs 5.4's JobQueue setup. `/recurring add monthly 1 Expense Digital 15000 Needs Rent` posts the
transaction automatically on schedule. Store definitions in a `Recurring` worksheet. Post a
confirmation with an Undo button rather than logging silently.

### 5.9 — Account normalization

Needs phase 1. `Cash`, `cash` and `Efectivo` silently become three separate accounts in
`/balance`. Keep a per-user alias map in a worksheet, normalize on write, and add
`/accounts merge <from> <to>` to fix history.

### 5.10 — `/top [period] [n]`

Needs phase 1. The *n* largest individual expenses in a period (default 10), excluding neutral
categories.

### 5.11 — `/find <text>`

Needs phase 1. Case-insensitive substring search across descriptions, newest first, capped at 25
results with a total.

### 5.12 — Configurable budget split

Needs phase 1. The 50/30/20 ratios are hardcoded (`main.py:604`). Move them to a per-user setting
with `/budget 60 20 20`, validating that the three add to 100.

### 5.13 — `/export [period]`

Needs phase 1. Send the period's transactions as a CSV file attachment via `reply_document`, with
a header row and amounts unformatted.

### 5.14 — Savings goals

Needs phase 1's `/savings`. Set a target with `/goal 100000`, and show progress against the pot as
a percentage and a text bar. Include it in the 5.4 monthly summary.

### Acceptance criteria (per item)

- [ ] A test covering the item's core logic, using the pure helpers from phase 1.
- [ ] `/help` and the phase 4 `COMMANDS` list updated.
- [ ] No regression in `pytest`.
