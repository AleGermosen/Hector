import ast as _ast
import html
import httpx
import logging
import asyncio
import random
import re
import io
import calendar
import os
import time as _time
import traceback
import uuid
import zoneinfo
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
from dataclasses import dataclass
from datetime import datetime, time as dt_time
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from typing import Optional

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, BotCommand, ReplyKeyboardMarkup, KeyboardButton
from telegram.ext import Defaults
from telegram.constants import ParseMode
from telegram.ext import (
    ApplicationBuilder, ContextTypes, MessageHandler, CommandHandler,
    filters, ConversationHandler, CallbackQueryHandler
)

from finance.strings import t

logger = logging.getLogger(__name__)

# ── Command registry — single source of truth for menu and /help ──────────────
COMMANDS = [
    # Balance, dashboard, help, cancel first
    ("balance",       "Current account balances"),
    ("dash",          "Full snapshot: balances, budget, savings rate"),
    ("help",          "Show this command list"),
    ("cancel",        "Cancel the current operation"),
    # Logging
    ("log",           "Step-by-step guided transaction entry"),
    ("ql",            "Quick-log shortcuts (list / fire / add / delete)"),
    ("undo",          "Remove the most recently logged transaction"),
    ("recent",        "Show last N transactions (default 10)"),
    ("recurring",     "Manage recurring monthly transactions"),
    # Reports
    ("summary",       "This month's income, expenses, net & trends"),
    ("top",           "Top 5 expenses this month"),
    ("ytd",           "Year-to-date totals and savings rate"),
    ("net",           "Net worth breakdown with chart"),
    ("expenses",      "Expense pie chart (all time or by month)"),
    ("calcexpenses",  "Budget status using the 50/30/20 rule"),
    ("savings",       "Savings pot: set aside, withdrawn, and net"),
    ("trend",         "Income vs expenses line chart (default 6 months)"),
    # Goals
    ("goals",         "Show all savings goals with progress"),
    ("setgoal",       "Create or update a savings goal"),
    ("addtogoal",     "Add savings to a goal"),
    # Misc
    ("exchange",      "USD ↔ RD$ converter — /exchange 100 or /exchange rd 5000"),
    ("calc",          "Calculator — e.g. /calc 5 * 2"),
    ("quiet",         "Toggle monthly summary push notifications"),
    ("lang",          "Switch language — /lang en or /lang es"),
    ("checksheet",    "Verify sheet connection and column layout"),
    ("start",         "Check your authorization"),
]

# ── Neutral categories: internal money movements, not income or spending ──────
NEUTRAL_CATEGORIES = {"transfer", "savings"}

# ── Timezone ──────────────────────────────────────────────────────────────────
_APP_TIMEZONE = zoneinfo.ZoneInfo(os.environ.get("APP_TIMEZONE", "America/Santo_Domingo"))


def now() -> datetime:
    return datetime.now(_APP_TIMEZONE)


# ── Standalone parsers (used by parse_rows so they have no class dependency) ──

def _parse_date(date_str) -> Optional[datetime]:
    if not date_str:
        return None
    date_str = date_str.strip()
    formats = [
        "%Y-%m-%d %H:%M:%S", "%Y-%m-%d",
        "%m/%d/%Y %H:%M:%S", "%m/%d/%Y",
        "%d/%m/%Y %H:%M:%S", "%d/%m/%Y",
    ]
    for fmt in formats:
        try:
            return datetime.strptime(date_str, fmt)
        except ValueError:
            continue
    if len(date_str) >= 10:
        try:
            return datetime.strptime(date_str[:10], "%Y-%m-%d")
        except ValueError:
            pass
    return None


def _parse_amount(amount_str) -> Decimal:
    if not amount_str:
        return Decimal(0)
    try:
        cleaned = str(amount_str).replace('$', '').replace(',', '').strip()
        return Decimal(cleaned)
    except InvalidOperation:
        return Decimal(0)


def _parse_amount_strict(amount_str) -> Optional[Decimal]:
    """Like _parse_amount but returns None on failure (safe for write paths)."""
    if not amount_str:
        return None
    try:
        cleaned = str(amount_str).replace('$', '').replace(',', '').strip()
        return Decimal(cleaned)
    except InvalidOperation:
        return None


# ── Period parser ─────────────────────────────────────────────────────────────

_MONTH_NAMES = {
    'jan': 1, 'january': 1, 'feb': 2, 'february': 2,
    'mar': 3, 'march': 3, 'apr': 4, 'april': 4, 'may': 5,
    'jun': 6, 'june': 6, 'jul': 7, 'july': 7,
    'aug': 8, 'august': 8, 'sep': 9, 'september': 9,
    'oct': 10, 'october': 10, 'nov': 11, 'november': 11,
    'dec': 12, 'december': 12,
}


def parse_period(args):
    """
    Parses period arguments into (month, year, description).
    description is None when input is invalid.
    Accepted forms:
      (no args)        → (None, None, "All Time")
      "this month"     → current month/year
      "last month"     → previous month, rolls year at January
      "2025"           → (None, 2025, "2025")
      "january"        → (1, current_year, "January")
      "january 2025"   → (1, 2025, "January 2025")
    """
    _n = now()
    if not args:
        return None, None, "All Time"

    text = " ".join(args).strip().lower()

    if text == "this month":
        return _n.month, _n.year, _n.strftime("%B %Y")

    if text == "last month":
        if _n.month == 1:
            m, y = 12, _n.year - 1
        else:
            m, y = _n.month - 1, _n.year
        return m, y, datetime(y, m, 1).strftime("%B %Y")

    if text.isdigit() and len(text) == 4:
        y = int(text)
        return None, y, str(y)

    parts = text.split()
    m_num = _MONTH_NAMES.get(parts[0]) or _MONTH_NAMES.get(parts[0][:3])
    if m_num:
        if len(parts) >= 2 and parts[1].isdigit() and len(parts[1]) == 4:
            y = int(parts[1])
            return m_num, y, datetime(y, m_num, 1).strftime("%B %Y")
        return m_num, _n.year, datetime(_n.year, m_num, 1).strftime("%B")

    return None, None, None  # invalid


# ── Transaction dataclass and pure reader ─────────────────────────────────────

@dataclass(frozen=True)
class Transaction:
    date: Optional[datetime]
    type: str        # "Income" | "Expense"
    account: str
    amount: Decimal
    category: str
    description: str

    @property
    def is_neutral(self) -> bool:
        return self.category.lower() in NEUTRAL_CATEGORIES


def parse_rows(rows, month=None, year=None) -> list:
    """
    Pure function: convert raw sheet rows to Transaction objects.
    Skips any row whose column B is not 'Income' or 'Expense'.
    When month/year are given, keeps only rows matching that period;
    rows with unparseable dates are dropped in that case.
    When neither is given, keeps all rows including those with bad dates.
    """
    transactions = []
    for row in rows:
        if len(row) < 4:
            continue
        type_str = row[1].strip().capitalize()
        if type_str not in ('Income', 'Expense'):
            continue
        date = _parse_date(row[0])
        if month is not None and (date is None or date.month != month):
            continue
        if year is not None and (date is None or date.year != year):
            continue
        transactions.append(Transaction(
            date=date,
            type=type_str,
            account=row[2].strip().capitalize(),
            amount=_parse_amount(row[3]),
            category=row[4].strip() if len(row) > 4 else "",
            description=row[5].strip() if len(row) > 5 else "",
        ))
    return transactions


async def load_transactions(sheet, month=None, year=None) -> list:
    """Async I/O wrapper around parse_rows."""
    loop = asyncio.get_running_loop()
    rows = await loop.run_in_executor(None, sheet.get_all_values)
    return parse_rows(rows, month, year)

CONFIRM_TRANSACTION = 0
LOG_TYPE, LOG_ACCOUNT, LOG_AMOUNT, LOG_CATEGORY, LOG_DESCRIPTION = range(1, 6)
QL_AMOUNT = 6

# ── Safe math evaluator (replaces eval) ──────────────────────────────────────

_CALC_MAX_LEN = 200
_CALC_MAX_EXP = 100

_ALLOWED_NODES = (
    _ast.Expression, _ast.BinOp, _ast.UnaryOp,
    _ast.Add, _ast.Sub, _ast.Mult, _ast.Div, _ast.Mod,
    _ast.Pow, _ast.FloorDiv, _ast.USub, _ast.UAdd,
    _ast.Constant,
)


def _safe_eval(expr: str) -> float:
    """Evaluate a math expression safely without eval()."""
    if len(expr) > _CALC_MAX_LEN:
        raise ValueError(f"Expression too long (max {_CALC_MAX_LEN} chars).")
    try:
        tree = _ast.parse(expr.strip(), mode='eval')
    except SyntaxError as e:
        raise ValueError(f"Syntax error: {e}") from e
    for node in _ast.walk(tree):
        if not isinstance(node, _ALLOWED_NODES):
            raise ValueError(f"Disallowed expression type: {type(node).__name__}")
    # Guard against huge exponents like 9**9**9 by rejecting nested Pow on the right
    for node in _ast.walk(tree):
        if isinstance(node, _ast.BinOp) and isinstance(node.op, _ast.Pow):
            right = node.right
            if isinstance(right, _ast.Constant) and right.value > _CALC_MAX_EXP:
                raise ValueError(f"Exponent too large (max {_CALC_MAX_EXP}).")
            if isinstance(right, _ast.BinOp) and isinstance(right.op, _ast.Pow):
                raise ValueError("Chained exponentiation is not allowed.")
    result = eval(compile(tree, '<calc>', 'eval'))  # noqa: S307 — AST-whitelisted
    if not isinstance(result, (int, float)):
        raise ValueError("Result is not a number.")
    return float(result)


# ── Sheet access: caching and retry ──────────────────────────────────────────

_SHEET_CACHE_TTL = 600      # seconds — refetch the worksheet handle every 10 min
_LEDGER_CACHE_TTL = 60      # seconds — re-pull raw rows at most once a minute
_RETRY_BASE = 1.0           # seconds — first backoff interval
_RETRY_MAX_ATTEMPTS = 4


def _backoff_sleep(attempt: int) -> float:
    """Exponential backoff with jitter: 1s, 2s, 4s, 8s ± 20%."""
    delay = _RETRY_BASE * (2 ** attempt)
    return delay * (0.8 + 0.4 * random.random())


async def _with_retry(coro_fn, *, is_write: bool = False):
    """
    Retry a gspread coroutine on transient errors.
    Reads retry on 429, 500, 503.
    Writes retry on 429 only — a 500/503 may have already applied.
    """
    for attempt in range(_RETRY_MAX_ATTEMPTS):
        try:
            return await coro_fn()
        except Exception as exc:
            status = getattr(exc, 'response', None)
            status = getattr(status, 'status_code', None) if status else None
            retryable = status in (429,) if is_write else status in (429, 500, 503)
            if not retryable or attempt == _RETRY_MAX_ATTEMPTS - 1:
                raise
            sleep_for = _backoff_sleep(attempt)
            logger.warning(f"gspread error (status={status}), retry {attempt+1} in {sleep_for:.1f}s: {exc}")
            await asyncio.sleep(sleep_for)


class FinanceBot:
    def __init__(self, token, google_client, user_mapping, strict_users=None):
        self.token = token
        self.client = google_client
        self.user_mapping = user_mapping
        self.strict_users = strict_users or []
        self._sheet_cache: dict[str, tuple[object, float]] = {}   # key → (sheet, expires_at)
        self._ledger_cache: dict[str, tuple[list, float]] = {}    # user_id → (rows, expires_at)
        self.application = (
            ApplicationBuilder()
            .token(self.token)
            .defaults(Defaults(parse_mode=ParseMode.HTML))
            .build()
        )
        self._register_handlers()
        self.application.add_error_handler(self._error_handler)

    def _register_handlers(self):
        self.application.add_handler(CommandHandler('start', self.start_cmd))
        self.application.add_handler(CommandHandler('help', self.help_cmd))
        self.application.add_handler(CommandHandler('balance', self.calculate_balance))
        self.application.add_handler(CommandHandler('calc', self.calculator))
        self.application.add_handler(CommandHandler('exchange', self.exchange_cmd))
        self.application.add_handler(CommandHandler('expenses', self.generate_expenses_chart))
        self.application.add_handler(CommandHandler('net', self.calculate_net_worth))
        self.application.add_handler(CommandHandler('calcexpenses', self.calc_expenses_budget))
        self.application.add_handler(CommandHandler('summary', self.summary_cmd))
        self.application.add_handler(CommandHandler('top', self.top_expenses_cmd))
        self.application.add_handler(CommandHandler('ytd', self.ytd_cmd))
        self.application.add_handler(CommandHandler('dash', self.dashboard_cmd))

        self.application.add_handler(CommandHandler('recurring', self.recurring_cmd))
        self.application.add_handler(CommandHandler('savings', self.savings_cmd))
        self.application.add_handler(CommandHandler('setgoal', self.setgoal_cmd))
        self.application.add_handler(CommandHandler('goals', self.goals_cmd))
        self.application.add_handler(CommandHandler('addtogoal', self.addtogoal_cmd))
        self.application.add_handler(CommandHandler('cancel', self.cancel_transaction))
        self.application.add_handler(CommandHandler('checksheet', self.checksheet_cmd))
        self.application.add_handler(CommandHandler('recent', self.recent_cmd))
        self.application.add_handler(CommandHandler('undo', self.undo_cmd))
        self.application.add_handler(CommandHandler('trend', self.trend_cmd))
        self.application.add_handler(CommandHandler('quiet', self.quiet_cmd))
        self.application.add_handler(CommandHandler('lang', self.lang_cmd))
        self.application.add_handler(CallbackQueryHandler(self.undo_callback, pattern='^undo_last$'))
        self.application.add_handler(CallbackQueryHandler(self.category_button_callback, pattern='^cat_'))

        # Guided logging flow: /log and keyboard "Guided Log" button
        _guided_log_filter = filters.Regex(r'^(📝 Guided Log|📝 Registro Guiado)$')
        log_conv = ConversationHandler(
            entry_points=[
                CommandHandler('log', self.log_start),
                MessageHandler(_guided_log_filter & ~filters.COMMAND, self.log_start),
            ],
            states={
                LOG_TYPE:        [CallbackQueryHandler(self.log_type_callback)],
                LOG_ACCOUNT:     [MessageHandler(filters.TEXT & ~filters.COMMAND, self.log_account),
                                  CallbackQueryHandler(self.log_account_callback)],
                LOG_AMOUNT:      [MessageHandler(filters.TEXT & ~filters.COMMAND, self.log_amount)],
                LOG_CATEGORY:    [CallbackQueryHandler(self.log_category_callback, pattern='^logcat_')],
                LOG_DESCRIPTION: [MessageHandler(filters.TEXT & ~filters.COMMAND, self.log_description),
                                  CallbackQueryHandler(self.log_description_callback)],
                CONFIRM_TRANSACTION: [CallbackQueryHandler(self.confirm_transaction_callback)],
            },
            fallbacks=[CommandHandler('cancel', self.cancel_transaction)],
            allow_reentry=True
        )
        self.application.add_handler(log_conv)

        # QL shortcut with variable amount (? placeholder)
        ql_amount_conv = ConversationHandler(
            entry_points=[
                CommandHandler('ql', self.quicklog_cmd),
                CallbackQueryHandler(self.ql_fire_callback, pattern='^ql_fire:'),
            ],
            states={
                QL_AMOUNT: [MessageHandler(filters.TEXT & ~filters.COMMAND, self.ql_amount_input)],
                CONFIRM_TRANSACTION: [CallbackQueryHandler(self.confirm_transaction_callback)],
            },
            fallbacks=[CommandHandler('cancel', self.cancel_transaction)],
            allow_reentry=True,
        )
        self.application.add_handler(ql_amount_conv)

        # Freetext confirmation flow for logging transactions
        conv_handler = ConversationHandler(
            entry_points=[MessageHandler(filters.TEXT & ~filters.COMMAND, self.preview_transaction)],
            states={
                CONFIRM_TRANSACTION: [CallbackQueryHandler(self.confirm_transaction_callback)]
            },
            fallbacks=[CommandHandler('cancel', self.cancel_transaction)],
            allow_reentry=True
        )
        self.application.add_handler(conv_handler)

    def get_user_sheet(self, user_id):
        if not self.client:
            logger.error("FinanceBot: Google client is not available.")
            return None
        sheet_identifier = self.user_mapping.get(str(user_id))
        if not sheet_identifier:
            return None
        cached, expires_at = self._sheet_cache.get(sheet_identifier, (None, 0))
        if cached is not None and _time.monotonic() < expires_at:
            return cached
        try:
            sheet = self.client.open(sheet_identifier).sheet1
            self._sheet_cache[sheet_identifier] = (sheet, _time.monotonic() + _SHEET_CACHE_TTL)
            return sheet
        except Exception as e:
            self._sheet_cache.pop(sheet_identifier, None)  # force refetch next time
            logger.error(f"Could not open sheet for user {user_id}: {e}")
            return None

    def _user_lang(self, user_id, context) -> str:
        """Return the user's chosen language ('en' or 'es'), defaulting to 'en'."""
        return context.bot_data.get('user_lang', {}).get(str(user_id), 'en')

    async def lang_cmd(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """/lang en|es — switch the bot's reply language for this session."""
        args = context.args
        if not args or args[0].lower() not in ('en', 'es'):
            lang = self._user_lang(update.message.from_user.id, context)
            await update.message.reply_text(t("lang_usage", lang))
            return
        lang = args[0].lower()
        context.bot_data.setdefault('user_lang', {})[str(update.message.from_user.id)] = lang
        key = "lang_set_es" if lang == "es" else "lang_set_en"
        await update.message.reply_text(t(key, lang))

    def _invalidate_ledger_cache(self, user_id):
        self._ledger_cache.pop(str(user_id), None)

    async def _get_all_values(self, sheet, user_id: str) -> list:
        """Fetch (or return cached) raw rows for a user's sheet."""
        uid = str(user_id)
        cached, expires_at = self._ledger_cache.get(uid, (None, 0))
        if cached is not None and _time.monotonic() < expires_at:
            return cached
        loop = asyncio.get_running_loop()
        rows = await _with_retry(
            lambda: loop.run_in_executor(None, sheet.get_all_values),
            is_write=False,
        )
        self._ledger_cache[uid] = (rows, _time.monotonic() + _LEDGER_CACHE_TTL)
        return rows

    def _parse_date_robust(self, date_str):
        return _parse_date(date_str)

    def _parse_amount_robust(self, amount_str):
        return _parse_amount(amount_str)

    def _parse_month(self, text):
        text = text.lower().strip()
        months = {
            'jan': 1, 'january': 1,
            'feb': 2, 'february': 2,
            'mar': 3, 'march': 3,
            'apr': 4, 'april': 4,
            'may': 5,
            'jun': 6, 'june': 6,
            'jul': 7, 'july': 7,
            'aug': 8, 'august': 8,
            'sep': 9, 'september': 9,
            'oct': 10, 'october': 10,
            'nov': 11, 'november': 11,
            'dec': 12, 'december': 12
        }
        return months.get(text) or months.get(text[:3])

    def _parse_date_robust(self, date_str):
        if not date_str:
            return None
        date_str = date_str.strip()
        formats = [
            "%Y-%m-%d %H:%M:%S",
            "%Y-%m-%d",
            "%m/%d/%Y %H:%M:%S",
            "%m/%d/%Y",
            "%d/%m/%Y %H:%M:%S",
            "%d/%m/%Y"
        ]
        for fmt in formats:
            try:
                return datetime.strptime(date_str, fmt)
            except ValueError:
                continue
        if len(date_str) >= 10:
            try:
                return datetime.strptime(date_str[:10], "%Y-%m-%d")
            except ValueError:
                pass
        return None

    def _parse_amount_robust(self, amount_str):
        return _parse_amount(amount_str)

    def _get_data_summary(self, records, target_month=None, target_year=None, year_only=False):
        """
        Aggregates income/expenses from raw sheet records.
        Neutral categories (Transfer, Savings) are excluded from both income and expense totals.
        - target_month + target_year: filter to one month
        - year_only=True + target_year: filter to whole year (used by /ytd)
        - neither: all-time totals
        """
        target_year = target_year or now().year
        summary = {
            'total_income': Decimal(0),
            'total_expenses': Decimal(0),
            'categories': {'Needs': Decimal(0), 'Wants': Decimal(0), 'Debt': Decimal(0)},
            'other_expenses': Decimal(0),
        }

        for row in records:
            if len(row) < 4:
                continue
            try:
                trans_type = row[1].strip().capitalize()
                if trans_type not in ('Income', 'Expense'):
                    continue

                if target_month or year_only:
                    row_date = _parse_date(row[0])
                    if row_date is None:
                        continue
                    if year_only and row_date.year != target_year:
                        continue
                    if target_month and (row_date.month != target_month or row_date.year != target_year):
                        continue

                amount = _parse_amount(row[3])
                category = row[4].strip().capitalize() if len(row) > 4 else ""

                if category.lower() in NEUTRAL_CATEGORIES:
                    continue

                if trans_type == 'Income':
                    summary['total_income'] += amount
                elif trans_type == 'Expense':
                    summary['total_expenses'] += amount
                    if category in summary['categories']:
                        summary['categories'][category] += amount
                    else:
                        match = next((k for k in summary['categories'].keys() if k.lower() == category.lower()), None)
                        if match:
                            summary['categories'][match] += amount
                        else:
                            summary['other_expenses'] += amount
            except Exception:
                continue

        summary['net_worth'] = summary['total_income'] - summary['total_expenses']
        summary['savings_rate'] = (
            round((summary['net_worth'] / summary['total_income']) * 100, 1)
            if summary['total_income'] > 0 else Decimal(0)
        )
        return summary

    def _get_top_expenses(self, records, target_month=None, target_year=None, n=5):
        """Returns the top N expenses grouped by description, sorted by total amount desc."""
        target_year = target_year or now().year
        # key → (total_amount, category, count)
        grouped: dict[str, list] = {}
        for row in records:
            if len(row) < 4:
                continue
            try:
                trans_type = row[1].strip().capitalize()
                if trans_type != 'Expense':
                    continue
                category = row[4].strip().title() if len(row) > 4 else ""
                if category.lower() in NEUTRAL_CATEGORIES:
                    continue
                if target_month:
                    row_date = _parse_date(row[0])
                    if row_date is None or row_date.month != target_month or row_date.year != target_year:
                        continue
                amount = _parse_amount(row[3])
                description = row[5].strip() if len(row) > 5 else ""
                key = description if description else category
                if key in grouped:
                    grouped[key][0] += amount
                    grouped[key][2] += 1
                else:
                    grouped[key] = [amount, category, 1]
            except Exception:
                continue
        expenses = [(total, cat, key, count) for key, (total, cat, count) in grouped.items()]
        expenses.sort(key=lambda x: x[0], reverse=True)
        return expenses[:n]

    @staticmethod
    def _previous_month(month, year):
        """Returns (month, year) of the month before the given one."""
        if month == 1:
            return 12, year - 1
        return month - 1, year

    def _get_category_average(self, records, category, target_month=None, target_year=None):
        """Returns the average expense amount for a category over completed months (excluding current)."""
        _n = now()
        monthly_totals = {}
        for row in records:
            if len(row) < 5:
                continue
            try:
                row_date = _parse_date(row[0])
                if not row_date:
                    continue
                if row_date.year == _n.year and row_date.month == _n.month:
                    continue
                if row[1].strip().capitalize() != 'Expense':
                    continue
                row_cat = row[4].strip().capitalize()
                if row_cat.lower() != category.lower():
                    continue
                key = (row_date.year, row_date.month)
                amount = _parse_amount(row[3])
                monthly_totals[key] = monthly_totals.get(key, Decimal(0)) + amount
            except Exception:
                continue
        if not monthly_totals:
            return Decimal(0)
        return sum(monthly_totals.values()) / len(monthly_totals)

    def _get_known_accounts(self, records):
        """Returns a sorted list of distinct account names from sheet history."""
        accounts = set()
        for row in records:
            if len(row) < 3:
                continue
            type_str = row[1].strip().capitalize() if len(row) > 1 else ""
            if type_str not in ('Income', 'Expense'):
                continue
            acc = row[2].strip().capitalize()
            if acc:
                accounts.add(acc)
        return sorted(accounts)

    def _get_shortcuts_sheet(self, user_id):
        """Gets (or creates) the Shortcuts worksheet for the user."""
        if not self.client:
            return None
        sheet_id = self.user_mapping.get(str(user_id))
        if not sheet_id:
            return None
        try:
            spreadsheet = self.client.open(sheet_id)
            try:
                ws = spreadsheet.worksheet("Shortcuts")
            except Exception:
                ws = spreadsheet.add_worksheet(title="Shortcuts", rows="50", cols="2")
                ws.insert_row(["Name", "Transaction"], index=1)
            return ws
        except Exception as e:
            logger.error(f"Could not get shortcuts sheet: {e}")
            return None

    def _load_shortcuts(self, shortcuts_sheet):
        """Returns a dict of name -> transaction string."""
        try:
            records = shortcuts_sheet.get_all_records()
            return {r['Name'].strip().lower(): r['Transaction'].strip() for r in records if r.get('Name')}
        except Exception:
            return {}

    def _get_recurring_sheet(self, user_id):
        """Gets (or creates) the Recurring worksheet. Columns: Name | Transaction | Day | Last Logged"""
        if not self.client:
            return None
        sheet_id = self.user_mapping.get(str(user_id))
        if not sheet_id:
            return None
        try:
            spreadsheet = self.client.open(sheet_id)
            try:
                return spreadsheet.worksheet("Recurring")
            except Exception:
                ws = spreadsheet.add_worksheet(title="Recurring", rows="50", cols="4")
                ws.insert_row(["Name", "Transaction", "Day", "Last Logged"], index=1)
                return ws
        except Exception as e:
            logger.error(f"Could not get recurring sheet: {e}")
            return None

    def _load_recurring(self, ws):
        """Returns list of dicts with keys: name, transaction, day, last_logged, row_index."""
        try:
            records = ws.get_all_records()
            items = []
            for i, r in enumerate(records, start=2):
                try:
                    items.append({
                        'name': str(r.get('Name', '')).strip(),
                        'transaction': str(r.get('Transaction', '')).strip(),
                        'day': int(r.get('Day', 0)),
                        'last_logged': str(r.get('Last Logged', '')).strip(),
                        'row_index': i
                    })
                except (ValueError, TypeError):
                    continue
            return items
        except Exception:
            return []

    def _get_goals_sheet(self, user_id):
        """Gets (or creates) the Goals worksheet. Columns: Name | Target | Saved"""
        if not self.client:
            return None
        sheet_id = self.user_mapping.get(str(user_id))
        if not sheet_id:
            return None
        try:
            spreadsheet = self.client.open(sheet_id)
            try:
                return spreadsheet.worksheet("Goals")
            except Exception:
                ws = spreadsheet.add_worksheet(title="Goals", rows="50", cols="3")
                ws.insert_row(["Name", "Target", "Saved"], index=1)
                return ws
        except Exception as e:
            logger.error(f"Could not get goals sheet: {e}")
            return None

    def _load_goals(self, ws):
        """Returns list of dicts with keys: name, target, saved, row_index."""
        try:
            records = ws.get_all_records()
            goals = []
            for i, r in enumerate(records, start=2):
                try:
                    goals.append({
                        'name': str(r.get('Name', '')).strip(),
                        'target': float(r.get('Target', 0)),
                        'saved': float(r.get('Saved', 0)),
                        'row_index': i
                    })
                except (ValueError, TypeError):
                    continue
            return goals
        except Exception:
            return []

    def _get_health_score(self, summary, prev_summary, balances):
        """
        Returns (score, max, breakdown) where score is 0-10.
        breakdown is a list of (label, pts_earned, pts_possible, passed).
        """
        breakdown = []

        def check(label, condition, pts):
            earned = pts if condition else 0
            breakdown.append((label, earned, pts, condition))
            return earned

        score = 0
        rate = summary['savings_rate']
        score += check("Savings rate ≥ 20%", rate >= 20, 3)
        if rate < 20:
            score += check("Savings rate ≥ 10%", rate >= 10, 1)
        else:
            breakdown.append(("Savings rate ≥ 10%", 1, 1, True))
            score += 1

        income = summary['total_income']
        score += check("Needs within 50% budget", income > 0 and summary['categories']['Needs'] <= income * Decimal('0.5'), 2)
        score += check("Wants within 30% budget", income > 0 and summary['categories']['Wants'] <= income * Decimal('0.3'), 1)
        score += check("Positive net worth", summary['net_worth'] >= 0, 1)
        score += check("Net worth growing vs last month", summary['net_worth'] > prev_summary['net_worth'], 1)
        score += check("All accounts positive", all(b >= 0 for b in balances.values()) if balances else False, 1)

        return score, 10, breakdown

    @staticmethod
    def _spend_forecast(total_expenses, day_of_month, days_in_month):
        """Projects month-end spend based on daily pace so far."""
        if day_of_month == 0:
            return total_expenses
        daily_rate = total_expenses / day_of_month
        return daily_rate * days_in_month

    def _get_balance_by_account(self, records):
        """Returns a dict of account -> balance from raw sheet records.
        Includes all Income/Expense rows (including neutral categories) since
        savings/transfers genuinely move money between accounts."""
        balances = {}
        for row in records:
            if len(row) < 4:
                continue
            try:
                trans_type = row[1].strip().capitalize()
                if trans_type not in ('Income', 'Expense'):
                    continue
                account = row[2].strip().capitalize()
                amount = _parse_amount(row[3])
                balances[account] = balances.get(account, Decimal(0)) + (amount if trans_type == 'Income' else -amount)
            except Exception:
                continue
        return balances

    def _parse_transaction_text(self, text, user_id):
        """
        Parses a transaction message. Returns (rows_to_insert, preview_text, error_text).
        On success: (rows, preview, None). On failure: (None, None, error_message).
        """
        # Transfer pattern
        transfer_match = re.match(
            r'^Transfer\s+(.+?)\s+to\s+(.+?)\s+([\d,]+(?:\.\d+)?)(?:\s+(.*))?$',
            text, re.IGNORECASE
        )
        if transfer_match:
            from_acc = transfer_match.group(1).strip().capitalize()
            to_acc = transfer_match.group(2).strip().capitalize()
            amount_str = transfer_match.group(3)
            desc = transfer_match.group(4) or ""
            amount = _parse_amount(amount_str)
            amount_s = str(amount.quantize(Decimal('0.01'), rounding=ROUND_HALF_UP))
            date = now().strftime("%Y-%m-%d %H:%M:%S")
            rows = [
                [date, "Expense", from_acc, amount_s, "Transfer", f"Transfer to {to_acc} {desc}".strip()],
                [date, "Income", to_acc, amount_s, "Transfer", f"Transfer from {from_acc} {desc}".strip()]
            ]
            preview = (
                f"💸 <b>Transfer</b>\n"
                f"  From: <b>{from_acc}</b>\n"
                f"  To: <b>{to_acc}</b>\n"
                f"  Amount: <b>${amount:,.2f}</b>"
            )
            if desc:
                preview += f"\n  Note: {desc}"
            return rows, preview, None

        # Income / Expense pattern
        # Format: [Type] [Account] [Category] [Description...] [Amount]
        parts = text.split()

        trans_type = parts[0].capitalize()
        if trans_type not in ('Income', 'Expense'):
            return None, None, (
                f"❌ Unknown type <code>{html.escape(parts[0])}</code>. Must be <code>Income</code> or <code>Expense</code>.\n\n"
                "Example: <code>Expense Cash Needs Groceries 50</code>"
            )

        if len(parts) < 4:
            return None, None, (
                "❌ Too few arguments.\n\n"
                "Format: <code>[Income/Expense] [Account] [Category] [Amount]</code>\n"
                "Example: <code>Expense Cash Needs Groceries 50</code>"
            )

        amount = _parse_amount_strict(parts[-1])
        if amount is None:
            return None, None, (
                f"❌ <code>{html.escape(parts[-1])}</code> is not a valid amount. Use a number like <code>50</code>, <code>1,500</code>, or <code>$50</code>."
            )

        account = parts[1].capitalize()
        category = parts[2]
        description = " ".join(parts[3:-1])

        if str(user_id) in self.strict_users and trans_type == 'Expense':
            allowed = ['Needs', 'Wants', 'Savings', 'Debt']
            matched = next((a for a in allowed if a.lower() == category.lower()), None)
            if matched:
                category = matched
            else:
                return None, None, (
                    f"❌ Category <code>{category}</code> is not allowed.\n"
                    f"Allowed: <code>{', '.join(allowed)}</code>"
                )

        date = now().strftime("%Y-%m-%d %H:%M:%S")
        amount_str = str(amount.quantize(Decimal('0.01'), rounding=ROUND_HALF_UP))
        rows = [[date, trans_type, account, amount_str, category, description]]

        # Savings auto-transfer
        if trans_type == 'Expense' and category.lower() == 'savings':
            rows.append([date, "Income", "Account", amount_str, "Savings",
                         f"Savings from {account} {description}".strip()])

        # Build preview
        emoji = "💰" if trans_type == 'Income' else "💸"
        preview = (
            f"{emoji} <b>{trans_type}</b>\n"
            f"  Account:  <b>{account}</b>\n"
            f"  Amount:   <b>${amount:,.2f}</b>\n"
            f"  Category: <b>{category}</b>"
        )
        if description:
            preview += f"\n  Note:     {description}"
        if trans_type == 'Expense' and category.lower() == 'savings':
            preview += f"\n\n  _(Also auto-logs Income → Account for savings transfer)_"

        return rows, preview, None

    def _insert_row(self, sheet, row_data):
        all_vals = sheet.get_all_values()
        next_row = len(all_vals) + 1
        sheet.update(f'A{next_row}', [row_data])

    def _insert_rows(self, sheet, rows_data):
        all_vals = sheet.get_all_values()
        next_row = len(all_vals) + 1
        sheet.update(f'A{next_row}', rows_data)

    async def _write_rows(self, sheet, rows_data: list, user_id) -> None:
        """Append rows with retry (429 only) and cache invalidation."""
        loop = asyncio.get_running_loop()
        if len(rows_data) == 1:
            await _with_retry(
                lambda: loop.run_in_executor(None, lambda: self._insert_row(sheet, rows_data[0])),
                is_write=True,
            )
        else:
            await _with_retry(
                lambda: loop.run_in_executor(None, lambda: self._insert_rows(sheet, rows_data)),
                is_write=True,
            )
        self._invalidate_ledger_cache(user_id)

    async def _show_preview(self, message, context, rows, preview, sheet, lang: str = "en"):
        """Shared helper: shows the transaction preview with confirm/cancel buttons.
        Checks for anomalies if it's an expense. Called from both freetext and /log flows."""
        context.user_data['pending_rows'] = rows
        context.user_data['preview_lang'] = lang
        warning = ""

        # Anomaly detection — only for single-row expenses
        row = rows[0]
        if row[1] == 'Expense' and row[4] != 'Transfer':
            try:
                user_id = message.from_user.id if hasattr(message, 'from_user') else message.chat_id
                records = await self._get_all_values(sheet, user_id)
                avg = self._get_category_average(records, row[4])
                amount = _parse_amount(row[3])
                if avg > 0 and amount >= avg * 3:
                    warning = t("preview_anomaly_warning", lang,
                                category=html.escape(row[4]), avg=f"{avg:,.2f}")
            except Exception:
                pass

        keyboard = [
            [InlineKeyboardButton(t("btn_log_it", lang), callback_data='tx_confirm'),
             InlineKeyboardButton(t("btn_cancel", lang), callback_data='tx_cancel')]
        ]
        await message.reply_text(
            f"{preview}{warning}\n\n{t('preview_confirm_prompt', lang)}",
            reply_markup=InlineKeyboardMarkup(keyboard)
        )
        return CONFIRM_TRANSACTION

    async def preview_transaction(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Parses freetext transaction message, shows preview with anomaly check."""
        text = update.message.text.strip()
        user_id = update.message.from_user.id
        lang = self._user_lang(user_id, context)

        # Handle persistent keyboard buttons (match either EN or ES label)
        if text in (t("btn_quick_log", "en"), t("btn_quick_log", "es")):
            loop = asyncio.get_running_loop()
            ws = await loop.run_in_executor(None, lambda: self._get_shortcuts_sheet(user_id))
            shortcuts = await loop.run_in_executor(None, lambda: self._load_shortcuts(ws)) if ws else {}
            if shortcuts:
                buttons = [
                    [InlineKeyboardButton(f"⚡ {name}", callback_data=f"ql_fire:{name}")]
                    for name in sorted(shortcuts.keys())
                ]
                await update.message.reply_text(
                    t("ql_header", lang),
                    reply_markup=InlineKeyboardMarkup(buttons)
                )
            else:
                await update.message.reply_text(t("ql_none_saved", lang))
            return ConversationHandler.END


        sheet = self.get_user_sheet(user_id)

        if not sheet:
            await update.message.reply_text(t("unauthorized_detail", lang, user_id=user_id))
            return ConversationHandler.END

        rows, preview, error = self._parse_transaction_text(update.message.text, user_id)
        if error:
            # 5.3: strict-mode users get category buttons instead of a text error
            if str(user_id) in self.strict_users and "category" in error.lower():
                parts = update.message.text.split()
                if len(parts) >= 2 and parts[0].capitalize() in ('Income', 'Expense'):
                    partial_tx = " ".join(parts[:2])  # "Expense Cash"
                    await self._show_category_keyboard(
                        update.message, context, partial_tx, parts[0].capitalize(), lang
                    )
                    return CONFIRM_TRANSACTION
            await update.message.reply_text(error)
            return ConversationHandler.END

        return await self._show_preview(update.message, context, rows, preview, sheet, lang)

    async def confirm_transaction_callback(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Handles the confirm/cancel button after previewing a transaction."""
        query = update.callback_query
        await query.answer()
        lang = context.user_data.get('preview_lang', self._user_lang(query.from_user.id, context))

        if query.data == 'tx_cancel':
            await query.edit_message_text(t("tx_cancelled", lang))
            context.user_data.pop('pending_rows', None)
            return ConversationHandler.END

        rows = context.user_data.pop('pending_rows', None)
        if not rows:
            await query.edit_message_text(t("something_went_wrong", lang, corr_id="n/a"))
            return ConversationHandler.END

        user_id = query.from_user.id
        sheet = self.get_user_sheet(user_id)
        if not sheet:
            await query.edit_message_text(t("could_not_reach_sheet", lang))
            return ConversationHandler.END

        try:
            await self._write_rows(sheet, rows, user_id)

            # Fetch updated records once — used for both balance and budget alert
            records = await self._get_all_values(sheet, user_id)
            balances = self._get_balance_by_account(records)

            row = rows[0]
            trans_type, account, amount, category = row[1], row[2], row[3], row[4]
            description = row[5] if len(row) > 5 else ""
            emoji = "💰" if trans_type == 'Income' else "💸"
            account_balance = balances.get(account, Decimal(0))

            amount_display = _parse_amount(amount)
            acc_label = t("preview_account", lang)
            amt_label = t("preview_amount", lang)
            cat_label = t("preview_category", lang)
            note_label = t("preview_note", lang)
            confirmation = (
                f"{t('logged_ok', lang)}\n\n"
                f"{emoji} <b>{html.escape(trans_type)}</b>\n"
                f"  {acc_label}:  <b>{html.escape(account)}</b>\n"
                f"  {amt_label}:   <b>${amount_display:,.2f}</b>\n"
                f"  {cat_label}: <b>{html.escape(category)}</b>"
            )
            if description:
                confirmation += f"\n  {note_label}:     {html.escape(description)}"
            confirmation += f"\n\n  {t('logged_account_balance', lang, account=html.escape(account), balance=f'{account_balance:,.2f}')}"

            # Budget alert — only for expenses in a tracked category
            budget_pct = {'Needs': 0.5, 'Wants': 0.3, 'Debt': 0.2}
            _n = now()
            if trans_type == 'Expense' and category in budget_pct:
                try:
                    s = self._get_data_summary(records, _n.month, _n.year)
                    if s['total_income'] > 0:
                        budget_limit = s['total_income'] * budget_pct[category]
                        spent = s['categories'].get(category, 0)
                        if spent > budget_limit:
                            over = spent - budget_limit
                            confirmation += t("budget_alert", lang,
                                              category=html.escape(category), over=f"{over:,.2f}")
                except Exception:
                    pass

            # Store last-written rows for /undo (keyed per user in bot_data)
            self.application.bot_data.setdefault('last_rows', {})[str(user_id)] = rows

            undo_keyboard = InlineKeyboardMarkup(
                [[InlineKeyboardButton(t("btn_undo", lang), callback_data='undo_last')]]
            )
            await query.edit_message_text(confirmation, reply_markup=undo_keyboard)

        except Exception as e:
            corr_id = uuid.uuid4().hex[:8]
            logger.error(f"[{corr_id}] Sheet write error: {e}")
            await query.edit_message_text(t("logged_error", lang, detail=corr_id))

        return ConversationHandler.END

    async def cancel_transaction(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        lang = self._user_lang(update.message.from_user.id, context)
        context.user_data.pop('pending_rows', None)
        await update.message.reply_text(t("cancel_reply", lang))
        return ConversationHandler.END

    def _quick_keyboard(self, lang: str) -> ReplyKeyboardMarkup:
        return ReplyKeyboardMarkup(
            [[KeyboardButton(t("btn_quick_log", lang)), KeyboardButton(t("btn_guided_log", lang))]],
            resize_keyboard=True,
        )

    async def checksheet_cmd(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """/checksheet — verify sheet connection and column layout."""
        user_id = update.message.from_user.id
        lang = self._user_lang(user_id, context)
        sheet = self.get_user_sheet(user_id)
        if not sheet:
            await update.message.reply_text("❌ Could not connect to your sheet. Check that your user ID is in USER_SHEET_MAPPING and the sheet name matches exactly." if lang == "en"
                                            else "❌ No se pudo conectar con tu hoja. Verifica que tu ID esté en USER_SHEET_MAPPING y que el nombre coincida exactamente.")
            return

        try:
            loop = asyncio.get_running_loop()
            all_rows = await loop.run_in_executor(None, sheet.get_all_values)
        except Exception as e:
            await update.message.reply_text(f"❌ Connected but failed to read rows: <code>{html.escape(str(e))}</code>")
            return

        EXPECTED = ["A: Date", "B: Type", "C: Account", "D: Amount", "E: Category", "F: Description"]

        lines = ["✅ <b>Sheet connected</b>" if lang == "en" else "✅ <b>Hoja conectada</b>", ""]

        if not all_rows:
            lines.append("⚠️ Sheet is empty — no rows found. It will work once you log a transaction." if lang == "en"
                         else "⚠️ La hoja está vacía. Funcionará en cuanto registres una transacción.")
        else:
            first = all_rows[0]
            lines.append(("<b>First row (used as column reference):</b>" if lang == "en" else "<b>Primera fila (referencia de columnas):</b>"))
            for i, label in enumerate(EXPECTED):
                val = html.escape(first[i].strip()) if i < len(first) else "—"
                lines.append(f"  {label} → <code>{val}</code>")

            total = len(all_rows)
            valid = sum(1 for r in all_rows if len(r) > 1 and r[1].strip().capitalize() in ('Income', 'Expense', 'Transfer'))
            lines += [
                "",
                f"📄 Total rows: <b>{total}</b>",
                f"✅ Readable transactions: <b>{valid}</b>",
            ]

            if total > 0 and valid == 0:
                lines.append("")
                lines.append("⚠️ No valid transactions found. Make sure column B contains <b>Income</b>, <b>Expense</b>, or <b>Transfer</b>." if lang == "en"
                             else "⚠️ No se encontraron transacciones válidas. Asegúrate de que la columna B tenga <b>Income</b>, <b>Expense</b> o <b>Transfer</b>.")

        await update.message.reply_text("\n".join(lines))

    async def start_cmd(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        user_id = update.message.from_user.id
        lang = self._user_lang(user_id, context)
        if str(user_id) in self.user_mapping:
            await update.message.reply_text(
                t("welcome_back", lang, user_id=user_id),
                reply_markup=self._quick_keyboard(lang),
            )
        else:
            await update.message.reply_text(
                t("welcome_unauthorized", lang, user_id=user_id)
            )

    async def help_cmd(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        logger.info(f"FinanceBot: Help command triggered by user {update.effective_user.id}")
        user_id = update.effective_user.id
        lang = self._user_lang(user_id, context)
        sections = {
            t("help_section_logging", lang): ["log", "ql", "undo", "recent", "recurring"],
            t("help_section_reports", lang): ["dash", "summary", "balance", "top", "ytd", "net", "expenses", "calcexpenses", "savings", "trend"],
            t("help_section_goals", lang): ["goals", "setgoal", "addtogoal"],
            t("help_section_misc", lang): ["exchange", "calc", "quiet", "lang", "start", "help", "cancel"],
        }
        cmd_desc_map = {
            name: t(f"cmd_desc_{name}", lang)
            for name in ["log", "ql", "undo", "recent", "recurring", "dash", "summary", "balance",
                         "top", "ytd", "net", "expenses", "calcexpenses", "savings", "trend",
                         "goals", "setgoal", "addtogoal", "exchange", "calc", "quiet", "lang",
                         "start", "help", "cancel"]
        }
        ql_header = "⚡ <b>Quick Log shortcuts</b>" if lang == "en" else "⚡ <b>Atajos de registro rápido</b>"
        ql_lines = [
            ql_header,
            ("  Save a shortcut:" if lang == "en" else "  Guardar un atajo:"),
            "  <code>/ql add lunch Expense Cash Wants Lunch 15</code>",
            ("  Use <code>?</code> as a variable amount:" if lang == "en" else "  Usar <code>?</code> como monto variable:"),
            "  <code>/ql add gas Expense Cash Needs Gas ?</code>",
            ("  Fire it: tap ⚡ in <code>/ql</code>, or type <code>/ql lunch</code>" if lang == "en"
             else "  Ejecutarlo: toca ⚡ en <code>/ql</code>, o escribe <code>/ql almuerzo</code>"),
            ("  Delete: <code>/ql delete lunch</code>" if lang == "en" else "  Eliminar: <code>/ql delete almuerzo</code>"),
            "",
        ]
        if lang == "en":
            accounts_lines = [
                "🏦 <b>Accounts</b>",
                "  Accounts are created automatically the first time you use a new name in a transaction.",
                "  <code>Expense Cash Needs Coffee 3</code>  ← creates <b>Cash</b> if new",
                "  To remove an account, delete its transactions from your Google Sheet.",
                "",
                "🏷️ <b>Categories</b>",
                "  Fixed expense categories: <b>Needs</b>, <b>Wants</b>, <b>Savings</b>, <b>Debt</b>",
                "  Income uses <b>Salary</b> by default. Add a description for more detail.",
                "",
            ]
        else:
            accounts_lines = [
                "🏦 <b>Cuentas</b>",
                "  Las cuentas se crean automáticamente la primera vez que usas un nombre nuevo en una transacción.",
                "  <code>Expense Efectivo Needs Café 3</code>  ← crea <b>Efectivo</b> si es nuevo",
                "  Para eliminar una cuenta, borra sus transacciones en tu Google Sheet.",
                "",
                "🏷️ <b>Categorías</b>",
                "  Categorías de gasto fijas: <b>Needs</b>, <b>Wants</b>, <b>Savings</b>, <b>Debt</b>",
                "  Los ingresos usan <b>Salary</b> por defecto. Agrega una descripción para más detalle.",
                "",
            ]

        lines = [
            t("help_format_header", lang),
            "<code>Expense Cash Needs Lunch 15.50</code>",
            "<code>Income Digital Salary 2000</code>",
            "<code>Transfer Digital to Cash 1500</code>",
            "",
        ] + ql_lines + accounts_lines
        for section, names in sections.items():
            lines.append(f"<b>{section}</b>")
            for name in names:
                desc = cmd_desc_map.get(name, "")
                lines.append(f"  /{name} — {html.escape(desc)}")
            lines.append("")
        lines.append(t("help_footer", lang))
        await update.message.reply_text("\n".join(lines))

    async def calculate_balance(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        user_id = update.message.from_user.id
        lang = self._user_lang(user_id, context)
        sheet = self.get_user_sheet(user_id)

        if not sheet:
            await update.message.reply_text(t("unauthorized", lang))
            return

        try:
            records = await self._get_all_values(sheet, user_id)
            balances = self._get_balance_by_account(records)

            if not balances:
                await update.message.reply_text(t("balance_no_transactions", lang))
                return

            response = t("balance_header", lang) + "\n"
            for acc, bal in balances.items():
                indicator = "🟢" if bal >= 0 else "🔴"
                response += f"{indicator} {html.escape(acc)}: <code>${bal:,.2f}</code>\n"

            await update.message.reply_text(response)
        except Exception as e:
            corr_id = uuid.uuid4().hex[:8]
            logger.error(f"[{corr_id}] Balance error: {e}")
            await update.message.reply_text(t("sheet_error", lang, corr_id=corr_id))

    async def calculator(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        lang = self._user_lang(update.message.from_user.id, context)
        expression = " ".join(context.args)
        if not expression:
            await update.message.reply_text(t("calc_usage", lang))
            return
        try:
            result = _safe_eval(expression)
            await update.message.reply_text(t("calc_result", lang, result=f"{result:g}"))
        except (ValueError, ZeroDivisionError) as e:
            await update.message.reply_text(f"❌ {html.escape(str(e))}")
        except Exception:
            await update.message.reply_text(t("calc_invalid", lang))

    async def exchange_cmd(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """
        /exchange [amount]       → $amount USD → RD$  (default)
        /exchange rd [amount]    → RD$amount  → USD
        Amount is always last.
        """
        lang = self._user_lang(update.message.from_user.id, context)
        args = context.args
        if not args:
            await update.message.reply_text(t("exchange_usage", lang))
            return

        RD_FLAGS = ('rd', 'rdp', 'dop', 'peso', 'pesos')
        to_rd = args[0].lower() not in RD_FLAGS

        try:
            amount = Decimal(args[-1].replace(',', ''))
        except InvalidOperation:
            await update.message.reply_text(t("exchange_bad_amount", lang, value=html.escape(args[-1])))
            return

        try:
            async with httpx.AsyncClient(timeout=8) as client:
                resp = await client.get("https://open.er-api.com/v6/latest/USD")
                resp.raise_for_status()
                data = resp.json()
            rate = Decimal(str(data['rates']['DOP']))
        except Exception:
            await update.message.reply_text(t("exchange_rate_error", lang))
            return

        if to_rd:
            result = amount * rate
            await update.message.reply_text(
                t("exchange_usd_to_rd", lang, usd=f"{amount:,.2f}", rd=f"{result:,.2f}", rate=f"{rate:,.4f}")
            )
        else:
            result = amount / rate
            await update.message.reply_text(
                t("exchange_rd_to_usd", lang, rd=f"{amount:,.2f}", usd=f"{result:,.2f}", rate=f"{rate:,.4f}")
            )

    async def generate_expenses_chart(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        user_id = update.message.from_user.id
        lang = self._user_lang(user_id, context)
        sheet = self.get_user_sheet(user_id)

        if not sheet:
            await update.message.reply_text(t("unauthorized", lang))
            return

        target_month = None
        target_year = now().year
        period_suffix = ""

        if context.args:
            arg = context.args[0]
            target_month = self._parse_month(arg)
            if not target_month:
                await update.message.reply_text(t("expenses_bad_month", lang, arg=html.escape(arg)))
                return
            month_name = datetime(target_year, target_month, 1).strftime("%B")
            period_suffix = f" ({month_name} {target_year})"

        chart_title = t("expenses_chart_title", lang, period=period_suffix)

        try:
            records = await self._get_all_values(sheet, user_id)
            summary = self._get_data_summary(records, target_month, target_year)
            categories = summary['categories']

            labels = [k for k, v in categories.items() if v > 0]
            sizes = [v for k, v in categories.items() if v > 0]
            total_categorized = sum(sizes)

            if not sizes and summary['other_expenses'] == 0:
                period_str = f" for {datetime(target_year, target_month, 1).strftime('%B %Y')}" if target_month else ""
                await update.message.reply_text(t("expenses_none", lang, period=period_str))
                return

            buf = None
            if sizes:
                fig, ax = plt.subplots()
                ax.pie([float(s) for s in sizes], labels=labels, autopct='%1.1f%%', startangle=90)
                ax.axis('equal')
                plt.title(chart_title)
                buf = io.BytesIO()
                plt.savefig(buf, format='png')
                buf.seek(0)
                plt.close(fig)

            breakdown_text = f"📊 <b>{html.escape(chart_title)}:</b>\n"
            for label, size in zip(labels, sizes):
                pct = (size / total_categorized) * 100 if total_categorized > 0 else 0
                breakdown_text += f"  • <b>{html.escape(label)}</b>: ${size:,.2f} ({pct:.1f}%)\n"
            if summary['other_expenses'] > 0:
                other_label = t("expenses_other", lang)
                breakdown_text += f"  • <b>{other_label}</b>: ${summary['other_expenses']:,.2f}\n"
            total_fmt = f"{summary['total_expenses']:,.2f}"
            net_fmt = f"{summary['net_worth']:,.2f}"
            breakdown_text += f"\n{t('expenses_total', lang, total=total_fmt)}"
            breakdown_text += f"\n{t('expenses_net', lang, net=net_fmt)}"

            if buf:
                await update.message.reply_photo(photo=buf, caption=breakdown_text)
            else:
                await update.message.reply_text(breakdown_text)

        except Exception as e:
            corr_id = uuid.uuid4().hex[:8]
            logger.error(f"[{corr_id}] Chart Error: {e}")
            await update.message.reply_text(t("sheet_error", lang, corr_id=corr_id))

    async def calculate_net_worth(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        user_id = update.message.from_user.id
        lang = self._user_lang(user_id, context)
        sheet = self.get_user_sheet(user_id)

        if not sheet:
            await update.message.reply_text(t("unauthorized", lang))
            return

        target_month = None
        target_year = now().year
        title_suffix = "(All Time)" if lang == "en" else "(Todo el Tiempo)"

        if context.args:
            arg = context.args[0]
            target_month = self._parse_month(arg)
            if not target_month:
                await update.message.reply_text(t("net_bad_month", lang, arg=html.escape(arg)))
                return
            month_name = datetime(target_year, target_month, 1).strftime("%B")
            title_suffix = f"({month_name} {target_year})"

        try:
            records = await self._get_all_values(sheet, user_id)
            summary = self._get_data_summary(records, target_month, target_year)
            total_income = summary['total_income']
            total_expenses = summary['total_expenses']
            net_worth = summary['net_worth']

            if total_income == 0 and total_expenses == 0:
                await update.message.reply_text(t("net_no_records", lang, period=title_suffix.lower()))
                return

            if total_income > 0:
                expense_pct = (total_expenses / total_income) * 100
                net_pct = 100 - expense_pct
            else:
                expense_pct = net_pct = 0.0

            buf = None
            chart_generated = False

            if total_income > 0 and net_worth >= 0:
                fig, ax = plt.subplots()
                ax.pie([float(total_expenses), float(net_worth)],
                       labels=[t("net_chart_label_expenses", lang), t("net_chart_label_net", lang)],
                       autopct='%1.1f%%', startangle=90, colors=['#ff9999', '#66b3ff'])
                ax.axis('equal')
                plt.title(f'{t("net_header", lang, period=title_suffix)}')
                buf = io.BytesIO()
                plt.savefig(buf, format='png')
                buf.seek(0)
                plt.close(fig)
                chart_generated = True

            net_emoji = "📈" if net_worth >= 0 else "📉"
            breakdown_text = (
                f"{t('net_header', lang, period=html.escape(title_suffix))}\n\n"
                f"  {t('net_total_income', lang, amount=f'{total_income:,.2f}')}\n"
                f"  {t('net_total_expenses', lang, amount=f'{total_expenses:,.2f}')}"
            )
            if total_income > 0:
                breakdown_text += t("net_pct_of_income", lang, pct=f"{expense_pct:.1f}")
            breakdown_text += f"\n\n{t('net_worth_line', lang, emoji=net_emoji, amount=f'{net_worth:,.2f}')}"
            if total_income > 0:
                breakdown_text += t("net_pct_remaining", lang, pct=f"{net_pct:.1f}")

            if chart_generated:
                await update.message.reply_photo(photo=buf, caption=breakdown_text)
            else:
                await update.message.reply_text(breakdown_text)

        except Exception as e:
            corr_id = uuid.uuid4().hex[:8]
            logger.error(f"[{corr_id}] Net Worth Error: {e}")
            await update.message.reply_text(t("sheet_error", lang, corr_id=corr_id))

    async def calc_expenses_budget(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        user_id = update.message.from_user.id
        lang = self._user_lang(user_id, context)
        sheet = self.get_user_sheet(user_id)

        if not sheet:
            await update.message.reply_text(t("unauthorized", lang))
            return

        target_month = None
        target_year = now().year
        title_suffix = "(All Time)" if lang == "en" else "(Todo el Tiempo)"

        if context.args:
            arg = context.args[0]
            target_month = self._parse_month(arg)
            if not target_month:
                await update.message.reply_text(t("calcexpenses_bad_month", lang, arg=html.escape(arg)))
                return
            month_name = datetime(target_year, target_month, 1).strftime("%B")
            title_suffix = f"({month_name} {target_year})"

        try:
            records = await self._get_all_values(sheet, user_id)
            summary = self._get_data_summary(records, target_month, target_year)
            total_income = summary['total_income']

            if total_income <= 0:
                await update.message.reply_text(t("calcexpenses_no_income", lang, period=title_suffix.lower()))
                return

            actual_needs = summary['categories']['Needs']
            actual_wants = summary['categories']['Wants']
            # Net savings: money set aside minus money withdrawn, plus debt payments
            txs = parse_rows(records, target_month, target_year)
            expense_savings = sum(tx.amount for tx in txs if tx.type == 'Expense' and tx.category.lower() == 'savings')
            income_savings = sum(tx.amount for tx in txs if tx.type == 'Income' and tx.category.lower() == 'savings')
            actual_savings = expense_savings - income_savings + summary['categories']['Debt']

            budget_needs = total_income * Decimal('0.5')
            budget_wants = total_income * Decimal('0.3')
            budget_savings = total_income * Decimal('0.2')

            def status_line(actual, budget):
                if actual > budget:
                    return t("calcexpenses_over", lang, amount=f"{actual - budget:,.2f}")
                else:
                    return t("calcexpenses_remaining", lang, amount=f"{budget - actual:,.2f}")

            net_fmt = f"{summary['net_worth']:,.2f}"
            response = (
                f"{t('calcexpenses_header', lang, period=html.escape(title_suffix), income=f'{total_income:,.2f}')}\n\n"
                f"{t('calcexpenses_needs', lang, budget=f'{budget_needs:,.2f}')}\n"
                f"{t('calcexpenses_spent', lang, spent=f'{actual_needs:,.2f}', status=status_line(actual_needs, budget_needs))}\n\n"
                f"{t('calcexpenses_wants', lang, budget=f'{budget_wants:,.2f}')}\n"
                f"{t('calcexpenses_spent', lang, spent=f'{actual_wants:,.2f}', status=status_line(actual_wants, budget_wants))}\n\n"
                f"{t('calcexpenses_savings', lang, budget=f'{budget_savings:,.2f}')}\n"
                f"{t('calcexpenses_spent', lang, spent=f'{actual_savings:,.2f}', status=status_line(actual_savings, budget_savings))}\n\n"
                f"{t('calcexpenses_net', lang, amount=net_fmt)}"
            )

            await update.message.reply_text(response)

        except Exception as e:
            corr_id = uuid.uuid4().hex[:8]
            logger.error(f"[{corr_id}] Calc Expenses Error: {e}")
            await update.message.reply_text(t("sheet_error", lang, corr_id=corr_id))

    async def savings_cmd(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """/savings [period] — show money set aside, withdrawn, and current pot."""
        user_id = update.message.from_user.id
        lang = self._user_lang(user_id, context)
        sheet = self.get_user_sheet(user_id)
        if not sheet:
            await update.message.reply_text(t("unauthorized", lang))
            return

        month, year, period_desc = parse_period(context.args)
        if period_desc is None:
            await update.message.reply_text(
                t("savings_bad_period", lang, period=html.escape(' '.join(context.args)))
            )
            return

        try:
            records = await self._get_all_values(sheet, user_id)
            txs = parse_rows(records, month, year)

            set_aside = sum(tx.amount for tx in txs if tx.type == 'Expense' and tx.category.lower() == 'savings')
            withdrew = sum(tx.amount for tx in txs if tx.type == 'Income' and tx.category.lower() == 'savings')
            pot = set_aside - withdrew

            response = (
                f"{t('savings_header', lang, period=html.escape(period_desc))}\n\n"
                f"{t('savings_set_aside', lang, amount=f'{set_aside:,.2f}')}\n"
                f"{t('savings_withdrew', lang, amount=f'{withdrew:,.2f}')}\n"
                f"{t('savings_pot', lang, amount=f'{pot:,.2f}')}\n\n"
                f"{t('savings_withdrawal_tip', lang)}"
            )
            await update.message.reply_text(response)

        except Exception as e:
            corr_id = uuid.uuid4().hex[:8]
            logger.error(f"[{corr_id}] Savings error: {e}")
            await update.message.reply_text(t("sheet_error", lang, corr_id=corr_id))

    async def _error_handler(self, update, context: ContextTypes.DEFAULT_TYPE):
        """Global error handler: logs, notifies admin, replies to user with correlation id."""
        corr_id = uuid.uuid4().hex[:8]
        logger.error(f"[{corr_id}] Unhandled error:", exc_info=context.error)

        admin_chat_id = os.environ.get('ADMIN_CHAT_ID')
        if admin_chat_id:
            try:
                tb = "".join(traceback.format_exception(
                    type(context.error), context.error, context.error.__traceback__
                ))
                await context.bot.send_message(
                    chat_id=admin_chat_id,
                    text=f"[{corr_id}]\n```\n{tb[-3000:]}\n```"
                )
            except Exception:
                pass

        if update and update.effective_message:
            await update.effective_message.reply_text(
                f"😔 Something went wrong. Reference: <code>{corr_id}</code>"
            )

    async def summary_cmd(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Monthly summary: income, expenses, net, savings rate, category breakdown, and trends."""
        user_id = update.message.from_user.id
        lang = self._user_lang(user_id, context)
        sheet = self.get_user_sheet(user_id)
        if not sheet:
            await update.message.reply_text(t("unauthorized", lang))
            return

        now_dt = now()
        target_year = now_dt.year

        if context.args:
            target_month = self._parse_month(context.args[0])
            if not target_month:
                await update.message.reply_text(t("summary_bad_month", lang, arg=html.escape(context.args[0])))
                return
        else:
            target_month = now_dt.month

        month_name = datetime(target_year, target_month, 1).strftime("%B %Y")
        prev_month, prev_year = self._previous_month(target_month, target_year)

        try:
            loop = asyncio.get_running_loop()
            records = await self._get_all_values(sheet, user_id)

            curr = self._get_data_summary(records, target_month, target_year)
            prev = self._get_data_summary(records, prev_month, prev_year)

            def trend(curr_val, prev_val):
                if prev_val == 0:
                    return ""
                diff = curr_val - prev_val
                pct = (diff / prev_val) * 100
                arrow = "↑" if diff > 0 else "↓"
                return f" {arrow}{abs(pct):.0f}%"

            if curr['total_income'] == 0 and curr['total_expenses'] == 0:
                await update.message.reply_text(t("summary_no_records", lang, period=month_name))
                return

            net_emoji = "📈" if curr['net_worth'] >= 0 else "📉"
            savings_emoji = "✅" if curr['savings_rate'] >= 20 else ("⚠️" if curr['savings_rate'] >= 10 else "🔴")

            lines = [f"{t('summary_header', lang, period=html.escape(month_name))}\n"]

            inc_fmt = f"{curr['total_income']:,.2f}"
            exp_fmt = f"{curr['total_expenses']:,.2f}"
            lines.append(f"{t('summary_income', lang, amount=inc_fmt)}{trend(curr['total_income'], prev['total_income'])}")
            lines.append(f"{t('summary_expenses', lang, amount=exp_fmt)}{trend(curr['total_expenses'], prev['total_expenses'])}")
            lines.append(t("summary_net", lang, emoji=net_emoji, amount=f"{curr['net_worth']:,.2f}"))
            lines.append(f"{t('summary_savings_rate', lang, emoji=savings_emoji, rate=curr['savings_rate'])}{trend(curr['savings_rate'], prev['savings_rate'])}\n")

            lines.append(t("summary_by_category", lang))
            for cat, amount in curr['categories'].items():
                if amount > 0 or prev['categories'].get(cat, 0) > 0:
                    tr = trend(amount, prev['categories'].get(cat, 0))
                    lines.append(f"  • {cat}: <code>${amount:,.2f}</code>{tr}")
            if curr['other_expenses'] > 0:
                lines.append(t("summary_other", lang, amount=f"{curr['other_expenses']:,.2f}"))

            # Spend forecast — only for current month
            if target_month == now_dt.month and target_year == now_dt.year and curr['total_expenses'] > 0:
                days_in_month = calendar.monthrange(now_dt.year, now_dt.month)[1]
                projected = self._spend_forecast(curr['total_expenses'], now_dt.day, days_in_month)
                lines.append(t("summary_forecast", lang, amount=f"{projected:,.2f}"))

            # Goals snapshot — show if any goals exist
            try:
                ws = await loop.run_in_executor(None, lambda: self._get_goals_sheet(user_id))
                goals = await loop.run_in_executor(None, lambda: self._load_goals(ws))
                if goals:
                    lines.append(t("summary_goals_header", lang))
                    for goal in goals:
                        pct = min((goal['saved'] / goal['target']) * 100, 100) if goal['target'] > 0 else 0
                        bar = "█" * int(pct // 10) + "░" * (10 - int(pct // 10))
                        lines.append(f"  {goal['name']}: <code>{bar}</code> {pct:.1f}%")
            except Exception:
                pass

            await update.message.reply_text("\n".join(lines))

        except Exception as e:
            corr_id = uuid.uuid4().hex[:8]
            logger.error(f"[{corr_id}] Summary error: {e}")
            await update.message.reply_text(t("sheet_error", lang, corr_id=corr_id))

    async def top_expenses_cmd(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Shows the top 5 individual expenses for the month."""
        user_id = update.message.from_user.id
        lang = self._user_lang(user_id, context)
        sheet = self.get_user_sheet(user_id)
        if not sheet:
            await update.message.reply_text(t("unauthorized", lang))
            return

        now_dt = now()
        target_year = now_dt.year

        if context.args:
            target_month = self._parse_month(context.args[0])
            if not target_month:
                await update.message.reply_text(t("top_bad_month", lang, arg=html.escape(context.args[0])))
                return
        else:
            target_month = now_dt.month

        month_name = datetime(target_year, target_month, 1).strftime("%B %Y")

        try:
            records = await self._get_all_values(sheet, user_id)
            top = self._get_top_expenses(records, target_month, target_year)

            if not top:
                await update.message.reply_text(t("top_none", lang, period=month_name))
                return

            lines = [f"{t('top_header', lang, period=html.escape(month_name))}\n"]
            medals = ["🥇", "🥈", "🥉", "4️⃣", "5️⃣"]
            for i, (amount, category, label, count) in enumerate(top):
                times = f" ×{count}" if count > 1 else ""
                lines.append(
                    f"{medals[i]} <code>${amount:,.2f}</code> — {html.escape(label)}"
                    f"{html.escape(times)} <i>({html.escape(category)})</i>"
                )

            await update.message.reply_text("\n".join(lines))

        except Exception as e:
            corr_id = uuid.uuid4().hex[:8]
            logger.error(f"[{corr_id}] Top expenses error: {e}")
            await update.message.reply_text(t("sheet_error", lang, corr_id=corr_id))

    async def ytd_cmd(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Year-to-date summary: total income, expenses, net worth, and savings rate."""
        user_id = update.message.from_user.id
        lang = self._user_lang(user_id, context)
        sheet = self.get_user_sheet(user_id)
        if not sheet:
            await update.message.reply_text(t("unauthorized", lang))
            return

        year = now().year

        try:
            records = await self._get_all_values(sheet, user_id)
            s = self._get_data_summary(records, year_only=True, target_year=year)

            if s['total_income'] == 0 and s['total_expenses'] == 0:
                await update.message.reply_text(t("ytd_no_records", lang, year=year))
                return

            net_emoji = "📈" if s['net_worth'] >= 0 else "📉"
            savings_emoji = "✅" if s['savings_rate'] >= 20 else ("⚠️" if s['savings_rate'] >= 10 else "🔴")

            lines = [
                f"{t('ytd_header', lang, year=year)}\n",
                t("ytd_total_income", lang, amount=f"{s['total_income']:,.2f}"),
                t("ytd_total_expenses", lang, amount=f"{s['total_expenses']:,.2f}"),
                t("ytd_net_worth", lang, emoji=net_emoji, amount=f"{s['net_worth']:,.2f}"),
                f"{t('ytd_savings_rate', lang, emoji=savings_emoji, rate=s['savings_rate'])}\n",
                t("ytd_by_category", lang),
            ]
            for cat, amount in s['categories'].items():
                if amount > 0:
                    pct = (amount / s['total_expenses'] * 100) if s['total_expenses'] > 0 else 0
                    lines.append(f"  • {cat}: <code>${amount:,.2f}</code> ({pct:.1f}%)")
            if s['other_expenses'] > 0:
                lines.append(t("ytd_other", lang, amount=f"{s['other_expenses']:,.2f}"))

            await update.message.reply_text("\n".join(lines))

        except Exception as e:
            corr_id = uuid.uuid4().hex[:8]
            logger.error(f"[{corr_id}] YTD error: {e}")
            await update.message.reply_text(t("sheet_error", lang, corr_id=corr_id))

    async def dashboard_cmd(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """One-command financial snapshot: balances + this month's budget + net worth + savings rate."""
        user_id = update.message.from_user.id
        lang = self._user_lang(user_id, context)
        sheet = self.get_user_sheet(user_id)
        if not sheet:
            await update.message.reply_text(t("unauthorized", lang))
            return

        now_dt = now()
        month_name = now_dt.strftime("%B %Y")

        try:
            loop = asyncio.get_running_loop()
            records = await self._get_all_values(sheet, user_id)

            balances = self._get_balance_by_account(records)
            s = self._get_data_summary(records, now_dt.month, now_dt.year)
            prev_month, prev_year = self._previous_month(now_dt.month, now_dt.year)
            prev_s = self._get_data_summary(records, prev_month, prev_year)

            score, max_score, breakdown = self._get_health_score(s, prev_s, balances)
            score_bar = "█" * score + "░" * (max_score - score)
            score_emoji = "🟢" if score >= 8 else ("🟡" if score >= 5 else "🔴")

            lines = [
                f"{t('dash_header', lang, period=html.escape(month_name))}\n",
                f"{t('dash_health_score', lang, emoji=score_emoji, score=score, max=max_score, bar=score_bar)}\n"
            ]

            # Account balances
            lines.append(t("dash_balances", lang))
            if balances:
                for acc, bal in balances.items():
                    indicator = "🟢" if bal >= 0 else "🔴"
                    lines.append(f"  {indicator} {html.escape(acc)}: <code>${bal:,.2f}</code>")
            else:
                lines.append(t("dash_no_transactions", lang))

            # This month budget
            lines.append(t("dash_this_month", lang))
            if s['total_income'] > 0 or s['total_expenses'] > 0:
                net_emoji = "📈" if s['net_worth'] >= 0 else "📉"
                savings_emoji = "✅" if s['savings_rate'] >= 20 else ("⚠️" if s['savings_rate'] >= 10 else "🔴")
                lines.append(t("dash_income", lang, amount=f"{s['total_income']:,.2f}"))
                lines.append(t("dash_expenses", lang, amount=f"{s['total_expenses']:,.2f}"))
                lines.append(t("dash_net", lang, emoji=net_emoji, amount=f"{s['net_worth']:,.2f}"))
                lines.append(t("dash_savings_rate", lang, emoji=savings_emoji, rate=s['savings_rate']))

                # Budget status (50/30/20)
                if s['total_income'] > 0:
                    lines.append(t("dash_budget_header", lang))
                    _txs = parse_rows(records, now_dt.month, now_dt.year)
                    _exp_sav = sum(tx.amount for tx in _txs if tx.type == 'Expense' and tx.category.lower() == 'savings')
                    _inc_sav = sum(tx.amount for tx in _txs if tx.type == 'Income' and tx.category.lower() == 'savings')
                    net_savings_debt = _exp_sav - _inc_sav + s['categories']['Debt']
                    budgets = {
                        t("dash_needs", lang): (s['categories']['Needs'], s['total_income'] * Decimal('0.5')),
                        t("dash_wants", lang): (s['categories']['Wants'], s['total_income'] * Decimal('0.3')),
                        t("dash_savings_debt", lang): (net_savings_debt, s['total_income'] * Decimal('0.2')),
                    }
                    for label, (actual, budget) in budgets.items():
                        if actual > budget:
                            status = t("dash_budget_over", lang, amount=f"{actual - budget:,.2f}")
                        else:
                            status = t("dash_budget_left", lang, amount=f"{budget - actual:,.2f}")
                        lines.append(f"  {label}: {status}")

                # Spend forecast
                days_in_month = calendar.monthrange(now_dt.year, now_dt.month)[1]
                day = now_dt.day
                if day > 0 and s['total_expenses'] > 0:
                    projected = self._spend_forecast(s['total_expenses'], day, days_in_month)
                    lines.append(t("dash_forecast_header", lang))
                    lines.append(t("dash_forecast_pace", lang, amount=f"{projected:,.2f}"))
                    if s['total_income'] > 0 and projected > s['total_income']:
                        lines.append(t("dash_forecast_exceed", lang, amount=f"{projected - s['total_income']:,.2f}"))
            else:
                lines.append(t("dash_no_month_tx", lang))

            await update.message.reply_text("\n".join(lines))

        except Exception as e:
            corr_id = uuid.uuid4().hex[:8]
            logger.error(f"[{corr_id}] Dashboard error: {e}")
            await update.message.reply_text(t("sheet_error", lang, corr_id=corr_id))

    # ── Quick-log shortcuts (/ql) ─────────────────────────────────────────────

    async def quicklog_cmd(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """
        /ql                          → list all shortcuts
        /ql <name>                   → fire that shortcut through the preview flow
        /ql add <name> <transaction> → save a new shortcut
        /ql delete <name>            → remove a shortcut
        """
        user_id = update.message.from_user.id
        lang = self._user_lang(user_id, context)
        sheet = self.get_user_sheet(user_id)
        if not sheet:
            await update.message.reply_text(t("unauthorized", lang))
            return

        args = context.args

        # No args — show shortcut buttons
        if not args:
            loop = asyncio.get_running_loop()
            ws = await loop.run_in_executor(None, lambda: self._get_shortcuts_sheet(user_id))
            if not ws:
                await update.message.reply_text(t("ql_no_sheet", lang))
                return ConversationHandler.END
            shortcuts = await loop.run_in_executor(None, lambda: self._load_shortcuts(ws))
            if not shortcuts:
                await update.message.reply_text(t("ql_none_saved", lang))
                return ConversationHandler.END
            buttons = [
                [InlineKeyboardButton(f"⚡ {name}", callback_data=f"ql_fire:{name}")]
                for name in sorted(shortcuts.keys())
            ]
            await update.message.reply_text(
                t("ql_header", lang),
                reply_markup=InlineKeyboardMarkup(buttons)
            )
            return ConversationHandler.END

        subcommand = args[0].lower()

        # Add shortcut
        if subcommand == 'add':
            if len(args) < 3:
                await update.message.reply_text(t("ql_add_usage", lang))
                return
            name = args[1].lower()
            transaction = " ".join(args[2:])
            # Validate — skip parse check if ? placeholder is present (amount unknown at save time)
            if '?' not in transaction:
                rows, _, error = self._parse_transaction_text(transaction, user_id)
                if error:
                    await update.message.reply_text(t("ql_add_parse_error", lang, error=error))
                    return
            loop = asyncio.get_running_loop()
            ws = await loop.run_in_executor(None, lambda: self._get_shortcuts_sheet(user_id))
            await loop.run_in_executor(None, lambda: self._load_shortcuts(ws))
            # Update or append
            records = await loop.run_in_executor(None, ws.get_all_records)
            for i, r in enumerate(records, start=2):
                if r.get('Name', '').strip().lower() == name:
                    await loop.run_in_executor(None, lambda: ws.update(f'B{i}', [[transaction]]))
                    await update.message.reply_text(t("ql_updated", lang, name=name))
                    return
            await loop.run_in_executor(None, lambda: ws.append_row([name, transaction]))
            await update.message.reply_text(t("ql_saved", lang, name=name, transaction=transaction))
            return

        # Delete shortcut
        if subcommand == 'delete':
            if len(args) < 2:
                await update.message.reply_text(t("ql_delete_usage", lang))
                return
            name = args[1].lower()
            loop = asyncio.get_running_loop()
            ws = await loop.run_in_executor(None, lambda: self._get_shortcuts_sheet(user_id))
            records = await loop.run_in_executor(None, ws.get_all_records)
            for i, r in enumerate(records, start=2):
                if r.get('Name', '').strip().lower() == name:
                    await loop.run_in_executor(None, lambda: ws.delete_rows(i))
                    await update.message.reply_text(t("ql_deleted", lang, name=name))
                    return
            await update.message.reply_text(t("ql_not_found", lang, name=name))
            return

        # Fire a shortcut by name
        name = subcommand
        loop = asyncio.get_running_loop()
        ws = await loop.run_in_executor(None, lambda: self._get_shortcuts_sheet(user_id))
        shortcuts = await loop.run_in_executor(None, lambda: self._load_shortcuts(ws))
        transaction = shortcuts.get(name)
        if not transaction:
            await update.message.reply_text(t("ql_not_found_with_hint", lang, name=name))
            return ConversationHandler.END

        # If the template has a ? placeholder, ask for the amount
        if '?' in transaction:
            context.user_data['ql_template'] = transaction
            context.user_data['ql_user_id'] = user_id
            await update.message.reply_text(t("ql_enter_amount", lang, name=name))
            return QL_AMOUNT

        rows, preview, error = self._parse_transaction_text(transaction, user_id)
        if error:
            await update.message.reply_text(t("ql_shortcut_broken", lang, error=error))
            return ConversationHandler.END
        await self._show_preview(update.message, context, rows, preview, sheet, lang)
        return ConversationHandler.END

    async def ql_amount_input(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Receives the amount for a ? ql shortcut and shows the preview."""
        user_id = update.message.from_user.id
        lang = self._user_lang(user_id, context)
        text = update.message.text.strip()
        amount = _parse_amount_strict(text)
        if amount is None:
            await update.message.reply_text(t("ql_bad_amount", lang, value=html.escape(text)))
            return QL_AMOUNT

        template = context.user_data.get('ql_template', '')
        user_id = context.user_data.get('ql_user_id', user_id)
        transaction = template.replace('?', str(amount), 1)

        sheet = self.get_user_sheet(user_id)
        rows, preview, error = self._parse_transaction_text(transaction, user_id)
        if error:
            await update.message.reply_text(f"❌ {error}")
            return ConversationHandler.END
        await self._show_preview(update.message, context, rows, preview, sheet, lang)
        return CONFIRM_TRANSACTION

    async def ql_fire_callback(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Handles inline button taps from the /ql shortcut list."""
        query = update.callback_query
        await query.answer()
        name = query.data.replace('ql_fire:', '', 1)
        user_id = query.from_user.id
        lang = self._user_lang(user_id, context)
        sheet = self.get_user_sheet(user_id)
        if not sheet:
            await query.edit_message_text(t("unauthorized", lang))
            return

        loop = asyncio.get_running_loop()
        ws = await loop.run_in_executor(None, lambda: self._get_shortcuts_sheet(user_id))
        shortcuts = await loop.run_in_executor(None, lambda: self._load_shortcuts(ws))
        transaction = shortcuts.get(name)
        if not transaction:
            await query.edit_message_text(t("ql_not_found", lang, name=html.escape(name)))
            return

        if '?' in transaction:
            context.user_data['ql_template'] = transaction
            context.user_data['ql_user_id'] = user_id
            await query.edit_message_text(t("ql_enter_amount", lang, name=html.escape(name)))
            return QL_AMOUNT

        rows, preview, error = self._parse_transaction_text(transaction, user_id)
        if error:
            await query.edit_message_text(t("ql_shortcut_broken", lang, error=error))
            return ConversationHandler.END
        await self._show_preview(query.message, context, rows, preview, sheet, lang)
        return CONFIRM_TRANSACTION

    # ── Guided /log flow ──────────────────────────────────────────────────────

    async def log_start(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Entry point for the guided /log flow."""
        user_id = update.message.from_user.id
        lang = self._user_lang(user_id, context)
        if not self.get_user_sheet(user_id):
            await update.message.reply_text(t("unauthorized", lang))
            return ConversationHandler.END
        context.user_data['log_entry'] = {}
        keyboard = [
            [InlineKeyboardButton(t("log_btn_expense", lang), callback_data='log_Expense'),
             InlineKeyboardButton(t("log_btn_income", lang), callback_data='log_Income')],
            [InlineKeyboardButton(t("log_btn_transfer", lang), callback_data='log_Transfer')]
        ]
        await update.message.reply_text(
            t("log_what_type", lang),
            reply_markup=InlineKeyboardMarkup(keyboard)
        )
        return LOG_TYPE

    async def log_type_callback(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        query = update.callback_query
        await query.answer()
        trans_type = query.data.replace('log_', '')
        lang = self._user_lang(query.from_user.id, context)
        context.user_data['log_entry']['type'] = trans_type

        # Fetch known accounts to offer as quick buttons
        user_id = query.from_user.id
        sheet = self.get_user_sheet(user_id)
        known_accounts = []
        if sheet:
            try:
                records = await self._get_all_values(sheet, user_id)
                known_accounts = self._get_known_accounts(records)
            except Exception:
                pass

        if known_accounts:
            buttons = [[InlineKeyboardButton(a, callback_data=f'acc_{a}')] for a in known_accounts[:6]]
            await query.edit_message_text(
                t("log_which_account_buttons", lang, type=html.escape(trans_type)),
                reply_markup=InlineKeyboardMarkup(buttons)
            )
        else:
            await query.edit_message_text(t("log_which_account_text", lang, type=html.escape(trans_type)))
        return LOG_ACCOUNT

    async def log_account_callback(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        query = update.callback_query
        await query.answer()
        account = query.data.replace('acc_', '')
        lang = self._user_lang(query.from_user.id, context)
        e = context.user_data['log_entry']
        if e.get('transfer_step') == 'to_account':
            e['to_account'] = account
            e.pop('transfer_step', None)
            await query.edit_message_text(
                t("log_transfer_confirmed", lang, from_acc=html.escape(e['account']), to_acc=html.escape(account)),
                reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton(t("btn_skip", lang), callback_data='desc_skip')]])
            )
            return LOG_DESCRIPTION
        e['account'] = account
        trans_type = e['type']
        if trans_type == 'Transfer':
            e['transfer_step'] = 'to_account'
            user_id = query.from_user.id
            sheet = self.get_user_sheet(user_id)
            known_accounts = []
            if sheet:
                try:
                    records = await self._get_all_values(sheet, user_id)
                    known_accounts = self._get_known_accounts(records)
                except Exception:
                    pass
            if known_accounts:
                buttons = [[InlineKeyboardButton(a, callback_data=f'acc_{a}')] for a in known_accounts[:6]]
                await query.edit_message_text(t("log_transfer_to_account", lang), reply_markup=InlineKeyboardMarkup(buttons))
            else:
                await query.edit_message_text(t("log_transfer_to_account", lang))
            return LOG_ACCOUNT
        elif trans_type == 'Expense':
            keyboard = [
                [InlineKeyboardButton(t("btn_needs", lang), callback_data='logcat_Needs'),
                 InlineKeyboardButton(t("btn_wants", lang), callback_data='logcat_Wants')],
                [InlineKeyboardButton(t("btn_savings", lang), callback_data='logcat_Savings'),
                 InlineKeyboardButton(t("btn_debt", lang), callback_data='logcat_Debt')]
            ]
            await query.edit_message_text(t("log_select_category", lang), reply_markup=InlineKeyboardMarkup(keyboard))
            return LOG_CATEGORY
        else:
            e['category'] = 'Income'
            skip_btn = InlineKeyboardMarkup([[InlineKeyboardButton(t("btn_skip", lang), callback_data='desc_skip')]])
            await query.edit_message_text(t("log_ask_description", lang), reply_markup=skip_btn)
            return LOG_DESCRIPTION

    async def log_account(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        account = update.message.text.strip().capitalize()
        lang = self._user_lang(update.message.from_user.id, context)
        e = context.user_data['log_entry']
        if e.get('transfer_step') == 'to_account':
            e['to_account'] = account
            e.pop('transfer_step', None)
            await update.message.reply_text(
                t("log_transfer_confirmed", lang, from_acc=html.escape(e['account']), to_acc=html.escape(account)),
                reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton(t("btn_skip", lang), callback_data='desc_skip')]])
            )
            return LOG_DESCRIPTION
        e['account'] = account
        trans_type = e['type']
        if trans_type == 'Transfer':
            e['transfer_step'] = 'to_account'
            user_id = update.message.from_user.id
            sheet = self.get_user_sheet(user_id)
            known_accounts = []
            if sheet:
                try:
                    records = await self._get_all_values(sheet, user_id)
                    known_accounts = self._get_known_accounts(records)
                except Exception:
                    pass
            if known_accounts:
                buttons = [[InlineKeyboardButton(a, callback_data=f'acc_{a}')] for a in known_accounts[:6]]
                await update.message.reply_text(t("log_transfer_to_account", lang), reply_markup=InlineKeyboardMarkup(buttons))
            else:
                await update.message.reply_text(t("log_transfer_to_account", lang))
            return LOG_ACCOUNT
        elif trans_type == 'Expense':
            keyboard = [
                [InlineKeyboardButton(t("btn_needs", lang), callback_data='logcat_Needs'),
                 InlineKeyboardButton(t("btn_wants", lang), callback_data='logcat_Wants')],
                [InlineKeyboardButton(t("btn_savings", lang), callback_data='logcat_Savings'),
                 InlineKeyboardButton(t("btn_debt", lang), callback_data='logcat_Debt')]
            ]
            await update.message.reply_text(t("log_select_category", lang), reply_markup=InlineKeyboardMarkup(keyboard))
            return LOG_CATEGORY
        else:
            e['category'] = 'Income'
            skip_btn = InlineKeyboardMarkup([[InlineKeyboardButton(t("btn_skip", lang), callback_data='desc_skip')]])
            await update.message.reply_text(t("log_ask_description", lang), reply_markup=skip_btn)
            return LOG_DESCRIPTION

    async def log_amount(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        text = update.message.text.strip()
        lang = self._user_lang(update.message.from_user.id, context)
        amount = _parse_amount_strict(text)
        if amount is None:
            await update.message.reply_text(t("log_bad_amount", lang, value=html.escape(text)))
            return LOG_AMOUNT

        context.user_data['log_entry']['amount'] = amount
        return await self._log_build_and_preview(update.message, context, lang)

    async def log_category_callback(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        query = update.callback_query
        await query.answer()
        category = query.data.replace('logcat_', '')
        lang = self._user_lang(query.from_user.id, context)
        context.user_data['log_entry']['category'] = category
        await query.edit_message_text(
            t("log_category_confirmed", lang, category=html.escape(category)),
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton(t("btn_skip", lang), callback_data='desc_skip')]])
        )
        return LOG_DESCRIPTION

    async def _log_ask_description(self, message, context, lang: str = "en"):
        await message.reply_text(
            t("log_ask_description", lang),
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton(t("btn_skip", lang), callback_data='desc_skip')]])
        )
        return LOG_DESCRIPTION

    async def _log_ask_amount(self, message_or_query, lang: str = "en"):
        text = t("log_ask_amount", lang)
        if hasattr(message_or_query, 'edit_message_text'):
            await message_or_query.edit_message_text(text)
        else:
            await message_or_query.reply_text(text)
        return LOG_AMOUNT

    async def log_description_callback(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Handles the Skip button for description."""
        query = update.callback_query
        await query.answer()
        lang = self._user_lang(query.from_user.id, context)
        context.user_data['log_entry']['description'] = ''
        return await self._log_ask_amount(query, lang)

    async def log_description(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        lang = self._user_lang(update.message.from_user.id, context)
        context.user_data['log_entry']['description'] = update.message.text.strip()
        return await self._log_ask_amount(update.message, lang)

    async def _log_build_and_preview(self, message_or_query, context, lang: str = "en"):
        """Assembles the transaction string from collected log_entry data and shows preview."""
        e = context.user_data['log_entry']
        trans_type = e['type']
        user_id = message_or_query.from_user.id

        if trans_type == 'Transfer':
            tx = f"Transfer {e['account']} to {e.get('to_account', '')} {e['amount']}"
            if e.get('description'):
                tx += f" {e['description']}"
        elif trans_type == 'Income':
            category = 'Salary'
            tx = f"Income {e['account']} {category} {e.get('description', '')} {e['amount']}".strip()
        else:
            tx = f"Expense {e['account']} {e['category']} {e.get('description', '')} {e['amount']}".strip()

        rows, preview, error = self._parse_transaction_text(tx, user_id)
        if error:
            if hasattr(message_or_query, 'edit_message_text'):
                await message_or_query.edit_message_text(f"❌ {error}")
            else:
                await message_or_query.reply_text(f"❌ {error}")
            return ConversationHandler.END

        sheet = self.get_user_sheet(user_id)
        if hasattr(message_or_query, 'message'):
            msg = message_or_query.message
        else:
            msg = message_or_query
        return await self._show_preview(msg, context, rows, preview, sheet, lang)

    # ── Recurring transactions ────────────────────────────────────────────────

    async def recurring_cmd(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """
        /recurring                         → list all recurring transactions
        /recurring add <name> <day> <tx>   → add a recurring transaction
        /recurring delete <name>           → remove one
        """
        user_id = update.message.from_user.id
        lang = self._user_lang(user_id, context)
        if not self.get_user_sheet(user_id):
            await update.message.reply_text(t("unauthorized", lang))
            return

        args = context.args
        loop = asyncio.get_running_loop()

        if not args:
            ws = await loop.run_in_executor(None, lambda: self._get_recurring_sheet(user_id))
            items = await loop.run_in_executor(None, lambda: self._load_recurring(ws))
            if not items:
                await update.message.reply_text(t("recurring_none", lang))
                return
            lines = [f"{t('recurring_header', lang)}\n"]
            for item in items:
                day_str = t("recurring_day", lang, day=item['day'])
                last = t("recurring_last", lang, date=item['last_logged']) if item['last_logged'] else t("recurring_never", lang)
                lines.append(f"  • <b>{html.escape(item['name'])}</b> — {day_str} — <code>{html.escape(item['transaction'])}</code> _({last})_")
            await update.message.reply_text("\n".join(lines))
            return

        subcommand = args[0].lower()

        if subcommand == 'add':
            if len(args) < 4:
                await update.message.reply_text(t("recurring_add_usage", lang))
                return
            name = args[1]
            try:
                day = int(args[2])
                if not 1 <= day <= 31:
                    raise ValueError
            except ValueError:
                await update.message.reply_text(t("recurring_bad_day", lang))
                return
            transaction = " ".join(args[3:])
            _, _, error = self._parse_transaction_text(transaction, user_id)
            if error:
                await update.message.reply_text(t("recurring_bad_tx", lang, error=error))
                return
            ws = await loop.run_in_executor(None, lambda: self._get_recurring_sheet(user_id))
            await loop.run_in_executor(None, lambda: ws.append_row([name, transaction, day, ""]))
            await update.message.reply_text(t("recurring_added", lang, name=html.escape(name), day=day))
            return

        if subcommand == 'delete':
            if len(args) < 2:
                await update.message.reply_text(t("recurring_delete_usage", lang))
                return
            name = args[1].lower()
            ws = await loop.run_in_executor(None, lambda: self._get_recurring_sheet(user_id))
            items = await loop.run_in_executor(None, lambda: self._load_recurring(ws))
            for item in items:
                if item['name'].lower() == name:
                    await loop.run_in_executor(None, lambda: ws.delete_rows(item['row_index']))
                    await update.message.reply_text(t("recurring_deleted", lang, name=html.escape(item['name'])))
                    return
            await update.message.reply_text(t("recurring_not_found", lang, name=html.escape(args[1])))

    async def _recurring_job(self, context):
        """Runs daily at 9am: logs any recurring transactions due today for all users."""
        today = now()
        today_str = today.strftime("%Y-%m-%d")
        logger.info(f"Running recurring job for {today_str}")

        for user_id_str in self.user_mapping:
            try:
                user_id = int(user_id_str)
                sheet = self.get_user_sheet(user_id)
                if not sheet:
                    continue

                loop = asyncio.get_running_loop()
                ws = await loop.run_in_executor(None, lambda: self._get_recurring_sheet(user_id))
                if not ws:
                    continue
                items = await loop.run_in_executor(None, lambda: self._load_recurring(ws))

                for item in items:
                    if item['day'] != today.day:
                        continue
                    if item['last_logged'] == today_str:
                        continue

                    rows, _, error = self._parse_transaction_text(item['transaction'], user_id)
                    if error or not rows:
                        continue

                    # Update the date to today in the rows
                    now_str = today.strftime("%Y-%m-%d %H:%M:%S")
                    for row in rows:
                        row[0] = now_str

                    await self._write_rows(sheet, rows, user_id)

                    # Mark as logged today
                    await loop.run_in_executor(None, lambda: ws.update_cell(item['row_index'], 4, today_str))

                    row = rows[0]
                    lang = context.bot_data.get('user_lang', {}).get(str(user_id), 'en')
                    await context.bot.send_message(
                        chat_id=user_id,
                        text=t("recurring_auto_logged", lang,
                               name=item['name'], transaction=item['transaction'])
                    )
                    logger.info(f"Logged recurring '{item['name']}' for user {user_id}")

            except Exception as e:
                logger.error(f"Recurring job error for user {user_id_str}: {e}")

    # ── Goals ─────────────────────────────────────────────────────────────────

    async def setgoal_cmd(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """/setgoal <name> <amount> — create or update a savings goal."""
        user_id = update.message.from_user.id
        lang = self._user_lang(user_id, context)
        if not self.get_user_sheet(user_id):
            await update.message.reply_text(t("unauthorized", lang))
            return
        if len(context.args) < 2:
            await update.message.reply_text(t("setgoal_usage", lang))
            return
        name = context.args[0]
        target = _parse_amount_strict(context.args[1])
        if target is None:
            await update.message.reply_text(t("setgoal_bad_amount", lang, value=html.escape(context.args[1])))
            return

        loop = asyncio.get_running_loop()
        ws = await loop.run_in_executor(None, lambda: self._get_goals_sheet(user_id))
        goals = await loop.run_in_executor(None, lambda: self._load_goals(ws))

        for goal in goals:
            if goal['name'].lower() == name.lower():
                await loop.run_in_executor(None, lambda: ws.update_cell(goal['row_index'], 2, target))
                await update.message.reply_text(
                    t("setgoal_updated", lang, name=html.escape(goal['name']), amount=f"{target:,.2f}")
                )
                return

        await loop.run_in_executor(None, lambda: ws.append_row([name, target, 0]))
        await update.message.reply_text(t("setgoal_created", lang, name=html.escape(name), amount=f"{target:,.2f}"))

    async def addtogoal_cmd(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """/addtogoal <name> <amount> — add savings to a goal."""
        user_id = update.message.from_user.id
        lang = self._user_lang(user_id, context)
        if not self.get_user_sheet(user_id):
            await update.message.reply_text(t("unauthorized", lang))
            return
        if len(context.args) < 2:
            await update.message.reply_text(t("addtogoal_usage", lang))
            return
        name = context.args[0]
        amount = _parse_amount_strict(context.args[1])
        if amount is None:
            await update.message.reply_text(t("addtogoal_bad_amount", lang, value=html.escape(context.args[1])))
            return

        loop = asyncio.get_running_loop()
        ws = await loop.run_in_executor(None, lambda: self._get_goals_sheet(user_id))
        goals = await loop.run_in_executor(None, lambda: self._load_goals(ws))

        for goal in goals:
            if goal['name'].lower() == name.lower():
                new_saved = goal['saved'] + amount
                await loop.run_in_executor(None, lambda: ws.update_cell(goal['row_index'], 3, new_saved))
                pct = min((new_saved / goal['target']) * 100, 100) if goal['target'] > 0 else 0
                bar = "█" * int(pct // 10) + "░" * (10 - int(pct // 10))
                done = t("addtogoal_reached", lang) if new_saved >= goal['target'] else ""
                target_fmt = f"{goal['target']:,.2f}"
                await update.message.reply_text(
                    f"{t('addtogoal_success', lang, amount=f'{amount:,.2f}', name=html.escape(goal['name']))}\n"
                    f"<code>{bar}</code> {pct:.1f}%\n"
                    f"{t('addtogoal_saved_of', lang, saved=f'{new_saved:,.2f}', target=target_fmt)}\n{done}"
                )
                return

        await update.message.reply_text(t("addtogoal_not_found", lang, name=html.escape(name)))

    async def goals_cmd(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """/goals — show all savings goals with progress."""
        user_id = update.message.from_user.id
        lang = self._user_lang(user_id, context)
        if not self.get_user_sheet(user_id):
            await update.message.reply_text(t("unauthorized", lang))
            return

        loop = asyncio.get_running_loop()
        ws = await loop.run_in_executor(None, lambda: self._get_goals_sheet(user_id))
        goals = await loop.run_in_executor(None, lambda: self._load_goals(ws))

        if not goals:
            await update.message.reply_text(t("goals_none", lang))
            return

        lines = [f"{t('goals_header', lang)}\n"]
        for goal in goals:
            pct = min((goal['saved'] / goal['target']) * 100, 100) if goal['target'] > 0 else 0
            bar = "█" * int(pct // 10) + "░" * (10 - int(pct // 10))
            remaining = max(goal['target'] - goal['saved'], 0)
            status = t("goals_done", lang) if goal['saved'] >= goal['target'] else t("goals_to_go", lang, amount=f"{remaining:,.2f}")
            lines.append(
                f"<b>{html.escape(goal['name'])}</b>\n"
                f"<code>{bar}</code> {pct:.1f}%\n"
                f"  <code>${goal['saved']:,.2f}</code> / <code>${goal['target']:,.2f}</code> — {status}\n"
            )
        await update.message.reply_text("\n".join(lines))

    # ── 5.1 /undo ──────────────────────────────────────────────────────────────

    async def undo_cmd(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """/undo — remove the most recently logged transaction."""
        user_id = update.message.from_user.id
        lang = self._user_lang(user_id, context)
        sheet = self.get_user_sheet(user_id)
        if not sheet:
            await update.message.reply_text(t("unauthorized", lang))
            return
        await self._do_undo(update.message, user_id, sheet, lang)

    async def undo_callback(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Handles the ↩️ Undo button on the confirmation message."""
        query = update.callback_query
        await query.answer()
        user_id = query.from_user.id
        lang = self._user_lang(user_id, context)
        sheet = self.get_user_sheet(user_id)
        if not sheet:
            await query.edit_message_text(t("undo_could_not_reach", lang))
            return
        await self._do_undo(query, user_id, sheet, lang)

    async def _do_undo(self, message_or_query, user_id, sheet, lang: str = "en"):
        """Core undo: scans from bottom for an exact-value match and deletes that row."""
        last_rows = self.application.bot_data.get('last_rows', {}).get(str(user_id))
        if not last_rows:
            text = t("undo_nothing", lang)
            if hasattr(message_or_query, 'edit_message_text'):
                await message_or_query.edit_message_text(text)
            else:
                await message_or_query.reply_text(text)
            return

        loop = asyncio.get_running_loop()
        try:
            all_rows = await loop.run_in_executor(None, sheet.get_all_values)
            target = last_rows[0]  # match on the first row (transfers: row 0 is the expense side)
            row_idx = None
            for i in range(len(all_rows) - 1, -1, -1):
                if all_rows[i] == target or all_rows[i][:len(target)] == target:
                    row_idx = i + 1  # gspread is 1-indexed
                    break

            if row_idx is None:
                text = t("undo_gone", lang)
                if hasattr(message_or_query, 'edit_message_text'):
                    await message_or_query.edit_message_text(text)
                else:
                    await message_or_query.reply_text(text)
                return

            # Delete all rows of this transaction (transfers have 2)
            # Delete from bottom up so indices don't shift
            rows_to_delete = [row_idx]
            if len(last_rows) > 1:
                target2 = last_rows[1]
                for i in range(len(all_rows) - 1, -1, -1):
                    if i + 1 != row_idx and (all_rows[i] == target2 or all_rows[i][:len(target2)] == target2):
                        rows_to_delete.append(i + 1)
                        break
            for idx in sorted(rows_to_delete, reverse=True):
                await loop.run_in_executor(None, lambda r=idx: sheet.delete_rows(r))

            self._invalidate_ledger_cache(user_id)
            self.application.bot_data.setdefault('last_rows', {}).pop(str(user_id), None)

            row = last_rows[0]
            text = t("undo_success", lang,
                     type=html.escape(row[1]), account=html.escape(row[2]),
                     amount=f"{_parse_amount(row[3]):,.2f}", category=html.escape(row[4]))
            if hasattr(message_or_query, 'edit_message_text'):
                await message_or_query.edit_message_text(text)
            else:
                await message_or_query.reply_text(text)
        except Exception as e:
            corr_id = uuid.uuid4().hex[:8]
            logger.error(f"[{corr_id}] Undo error: {e}", exc_info=True)
            text = t("undo_failed", lang, corr_id=corr_id)
            if hasattr(message_or_query, 'edit_message_text'):
                await message_or_query.edit_message_text(text)
            else:
                await message_or_query.reply_text(text)

    # ── 5.2 /recent ────────────────────────────────────────────────────────────

    async def recent_cmd(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """/recent [n] — show the last n transactions (default 10)."""
        user_id = update.message.from_user.id
        lang = self._user_lang(user_id, context)
        sheet = self.get_user_sheet(user_id)
        if not sheet:
            await update.message.reply_text(t("unauthorized", lang))
            return

        n = 10
        if context.args:
            try:
                n = max(1, min(int(context.args[0]), 50))
            except ValueError:
                pass

        try:
            records = await self._get_all_values(sheet, user_id)
            # Filter to real transaction rows
            txs = [r for r in records if len(r) >= 4 and r[1].strip().capitalize() in ('Income', 'Expense')]
            recent = txs[-n:][::-1]  # last n, newest first

            if not recent:
                await update.message.reply_text(t("recent_none", lang))
                return

            lines = [f"{t('recent_header', lang, n=len(recent))}\n"]
            for row in recent:
                date_obj = _parse_date(row[0])
                date_s = date_obj.strftime("%b %d") if date_obj else row[0][:10]
                tx_type = row[1].strip().capitalize()
                acct = html.escape(row[2].strip().capitalize())
                amt = _parse_amount(row[3])
                cat = html.escape(row[4].strip() if len(row) > 4 else "")
                desc = html.escape(row[5].strip() if len(row) > 5 else "")
                emoji = "💰" if tx_type == 'Income' else "💸"
                line = f"{emoji} <b>{date_s}</b> {acct} <code>${amt:,.2f}</code> {cat}"
                if desc:
                    line += f" — {desc}"
                lines.append(line)

            await update.message.reply_text("\n".join(lines))
        except Exception as e:
            await self._reply_error(update.message, e, t("recent_error", lang))

    # ── 5.3 Category buttons (used by _parse_transaction_text fallback) ────────
    # (Implemented inline in preview_transaction — see _show_category_keyboard)

    async def _show_category_keyboard(self, message, context, partial_tx, trans_type, lang: str = "en"):
        """Show inline category buttons when strict user omits/misspells category."""
        keyboard = [
            [InlineKeyboardButton(t("btn_needs", lang), callback_data='cat_Needs'),
             InlineKeyboardButton(t("btn_wants", lang), callback_data='cat_Wants')],
            [InlineKeyboardButton(t("btn_savings", lang), callback_data='cat_Savings'),
             InlineKeyboardButton(t("btn_debt", lang), callback_data='cat_Debt')],
        ]
        context.user_data['pending_cat_tx'] = partial_tx
        await message.reply_text(
            t("log_category_which", lang, type=html.escape(trans_type)),
            reply_markup=InlineKeyboardMarkup(keyboard)
        )

    async def category_button_callback(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Handles category button press from 5.3 keyboard — completes the transaction."""
        query = update.callback_query
        await query.answer()
        category = query.data.replace('cat_', '')
        user_id = query.from_user.id
        lang = self._user_lang(user_id, context)
        partial_tx = context.user_data.pop('pending_cat_tx', None)
        if not partial_tx:
            await query.edit_message_text(t("session_expired", lang))
            return ConversationHandler.END

        full_tx = f"{partial_tx} {category}"
        rows, preview, error = self._parse_transaction_text(full_tx, user_id)
        if error:
            await query.edit_message_text(f"❌ {error}")
            return ConversationHandler.END

        sheet = self.get_user_sheet(user_id)
        if not sheet:
            await query.edit_message_text(t("could_not_reach_sheet", lang))
            return ConversationHandler.END

        await query.edit_message_text(preview)
        return await self._show_preview(query.message, context, rows, preview, sheet, lang)

    # ── 5.4 Monthly summary push ────────────────────────────────────────────────

    async def quiet_cmd(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """/quiet — toggle monthly push notifications on/off."""
        user_id = str(update.message.from_user.id)
        lang = self._user_lang(user_id, context)
        quiet_set = context.bot_data.setdefault('quiet_users', set())
        if user_id in quiet_set:
            quiet_set.discard(user_id)
            await update.message.reply_text(t("quiet_disabled", lang))
        else:
            quiet_set.add(user_id)
            await update.message.reply_text(t("quiet_enabled", lang))

    async def _monthly_summary_job(self, context):
        """Job: send each user last month's summary on the 1st at 09:00."""
        _n = now()
        # Last month
        if _n.month == 1:
            month, year = 12, _n.year - 1
        else:
            month, year = _n.month - 1, _n.year
        month_name = datetime(year, month, 1).strftime("%B %Y")

        quiet_set = context.bot_data.get('quiet_users', set())
        loop = asyncio.get_running_loop()

        for user_id_str, sheet_id in self.user_mapping.items():
            if user_id_str in quiet_set:
                continue
            try:
                sheet = self.get_user_sheet(int(user_id_str))
                if not sheet:
                    continue
                records = await loop.run_in_executor(None, sheet.get_all_values)
                s = self._get_data_summary(records, month, year)
                if s['total_income'] == 0 and s['total_expenses'] == 0:
                    continue

                # Savings pot
                txs = parse_rows(records, month, year)
                set_aside = sum(t.amount for t in txs if t.type == 'Expense' and t.category.lower() == 'savings')
                withdrew = sum(t.amount for t in txs if t.type == 'Income' and t.category.lower() == 'savings')
                pot = set_aside - withdrew

                lang = context.bot_data.get('user_lang', {}).get(user_id_str, 'en')
                net_emoji = "📈" if s['net_worth'] >= 0 else "📉"
                savings_emoji = "✅" if s['savings_rate'] >= 20 else ("⚠️" if s['savings_rate'] >= 10 else "🔴")
                push_inc = f"{s['total_income']:,.2f}"
                push_exp = f"{s['total_expenses']:,.2f}"
                push_net = f"{s['net_worth']:,.2f}"
                msg = (
                    f"{t('monthly_push_header', lang, period=html.escape(month_name))}\n\n"
                    f"{t('monthly_push_income', lang, amount=push_inc)}\n"
                    f"{t('monthly_push_expenses', lang, amount=push_exp)}\n"
                    f"{t('monthly_push_net', lang, emoji=net_emoji, amount=push_net)}\n"
                    f"{t('monthly_push_rate', lang, emoji=savings_emoji, rate=s['savings_rate'])}\n\n"
                    f"{t('monthly_push_pot', lang, amount=f'{pot:,.2f}')}\n\n"
                    f"{t('monthly_push_footer', lang)}"
                )
                await context.bot.send_message(chat_id=int(user_id_str), text=msg)
            except Exception as e:
                logger.error(f"Monthly summary error for user {user_id_str}: {e}")

    # ── 5.5 /trend ─────────────────────────────────────────────────────────────

    async def trend_cmd(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """/trend [months] — income vs expenses line chart for last n months."""
        user_id = update.message.from_user.id
        lang = self._user_lang(user_id, context)
        sheet = self.get_user_sheet(user_id)
        if not sheet:
            await update.message.reply_text(t("unauthorized", lang))
            return

        n_months = 6
        if context.args:
            try:
                n_months = max(2, min(int(context.args[0]), 24))
            except ValueError:
                pass

        try:
            records = await self._get_all_values(sheet, user_id)
            _n = now()

            # Build list of (year, month) for the last n_months
            periods = []
            y, m = _n.year, _n.month
            for _ in range(n_months):
                periods.append((y, m))
                m -= 1
                if m == 0:
                    m, y = 12, y - 1
            periods.reverse()

            incomes, expenses, savings_pots = [], [], []
            labels = []
            for y, m in periods:
                s = self._get_data_summary(records, m, y)
                txs = parse_rows(records, m, y)
                set_aside = sum(tx.amount for tx in txs if tx.type == 'Expense' and tx.category.lower() == 'savings')
                withdrew = sum(tx.amount for tx in txs if tx.type == 'Income' and tx.category.lower() == 'savings')
                incomes.append(float(s['total_income']))
                expenses.append(float(s['total_expenses']))
                savings_pots.append(float(set_aside - withdrew))
                labels.append(datetime(y, m, 1).strftime("%b %y"))

            fig, ax = plt.subplots(figsize=(8, 4))
            ax.plot(labels, incomes, marker='o', label=t("trend_chart_income", lang), color='#66b3ff')
            ax.plot(labels, expenses, marker='o', label=t("trend_chart_expenses", lang), color='#ff9999')
            ax.plot(labels, savings_pots, marker='s', linestyle='--', label=t("trend_chart_savings", lang), color='#99ff99')
            ax.set_title(t("trend_chart_title", lang, n=n_months))
            ax.legend()
            ax.tick_params(axis='x', rotation=45)
            plt.tight_layout()

            buf = io.BytesIO()
            plt.savefig(buf, format='png')
            buf.seek(0)
            plt.close(fig)

            await update.message.reply_photo(photo=buf, caption=t("trend_caption", lang, n=n_months))
        except Exception as e:
            await self._reply_error(update.message, e, t("trend_error", lang))

    async def start(self):
        await self.application.initialize()
        await self.application.start()
        # Register Telegram command menu (the / list users see when they type /)
        try:
            await self.application.bot.set_my_commands(
                [BotCommand(name, desc) for name, desc in COMMANDS]
            )
        except Exception as e:
            logger.warning(f"Could not register command menu: {e}")
        if self.application.job_queue:
            # Recurring transactions: daily at 09:00
            self.application.job_queue.run_daily(
                self._recurring_job,
                time=dt_time(9, 0, 0)
            )
            # Monthly summary: 1st of each month at 09:00
            self.application.job_queue.run_monthly(
                self._monthly_summary_job,
                when=dt_time(9, 0, 0),
                day=1,
            )
        else:
            logger.warning("JobQueue not available — recurring transactions and monthly summaries will not run.")
        await self.application.updater.start_polling()
        logger.info("FinanceBot started polling.")

    async def stop(self):
        await self.application.updater.stop()
        await self.application.stop()
        await self.application.shutdown()
