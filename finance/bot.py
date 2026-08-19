import logging
import asyncio
import re
import io
import calendar
import os
import traceback
import uuid
import zoneinfo
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
from dataclasses import dataclass
from datetime import datetime, time as dt_time
from typing import Optional

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    ApplicationBuilder, ContextTypes, MessageHandler, CommandHandler,
    filters, ConversationHandler, CallbackQueryHandler
)

logger = logging.getLogger(__name__)

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


def _parse_amount(amount_str) -> float:
    if not amount_str:
        return 0.0
    try:
        return float(str(amount_str).replace('$', '').replace(',', '').strip())
    except ValueError:
        return 0.0


def _parse_amount_strict(amount_str) -> Optional[float]:
    """Like _parse_amount but returns None on failure (safe for write paths)."""
    if not amount_str:
        return None
    try:
        return float(str(amount_str).replace('$', '').replace(',', '').strip())
    except ValueError:
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
    amount: float
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


class FinanceBot:
    def __init__(self, token, google_client, user_mapping, strict_users=None):
        self.token = token
        self.client = google_client
        self.user_mapping = user_mapping
        self.strict_users = strict_users or []
        self.application = ApplicationBuilder().token(self.token).build()
        self._register_handlers()
        self.application.add_error_handler(self._error_handler)

    def _register_handlers(self):
        self.application.add_handler(CommandHandler('start', self.start_cmd))
        self.application.add_handler(CommandHandler('help', self.help_cmd))
        self.application.add_handler(CommandHandler('balance', self.calculate_balance))
        self.application.add_handler(CommandHandler('calc', self.calculator))
        self.application.add_handler(CommandHandler('expenses', self.generate_expenses_chart))
        self.application.add_handler(CommandHandler('net', self.calculate_net_worth))
        self.application.add_handler(CommandHandler('calcExpenses', self.calc_expenses_budget))
        self.application.add_handler(CommandHandler('summary', self.summary_cmd))
        self.application.add_handler(CommandHandler('top', self.top_expenses_cmd))
        self.application.add_handler(CommandHandler('ytd', self.ytd_cmd))
        self.application.add_handler(CommandHandler('dash', self.dashboard_cmd))
        self.application.add_handler(CommandHandler('ql', self.quicklog_cmd))
        self.application.add_handler(CommandHandler('recurring', self.recurring_cmd))
        self.application.add_handler(CommandHandler('savings', self.savings_cmd))
        self.application.add_handler(CommandHandler('setgoal', self.setgoal_cmd))
        self.application.add_handler(CommandHandler('goals', self.goals_cmd))
        self.application.add_handler(CommandHandler('addtogoal', self.addtogoal_cmd))

        # Guided logging flow: /log
        log_conv = ConversationHandler(
            entry_points=[CommandHandler('log', self.log_start)],
            states={
                LOG_TYPE:        [CallbackQueryHandler(self.log_type_callback)],
                LOG_ACCOUNT:     [MessageHandler(filters.TEXT & ~filters.COMMAND, self.log_account),
                                  CallbackQueryHandler(self.log_account_callback)],
                LOG_AMOUNT:      [MessageHandler(filters.TEXT & ~filters.COMMAND, self.log_amount)],
                LOG_CATEGORY:    [CallbackQueryHandler(self.log_category_callback)],
                LOG_DESCRIPTION: [MessageHandler(filters.TEXT & ~filters.COMMAND, self.log_description),
                                  CallbackQueryHandler(self.log_description_callback)],
                CONFIRM_TRANSACTION: [CallbackQueryHandler(self.confirm_transaction_callback)],
            },
            fallbacks=[CommandHandler('cancel', self.cancel_transaction)],
            allow_reentry=True
        )
        self.application.add_handler(log_conv)

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
        try:
            return self.client.open(sheet_identifier).sheet1
        except Exception as e:
            logger.error(f"Could not open sheet for user {user_id}: {e}")
            return None

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
        if not amount_str:
            return 0.0
        try:
            clean_str = str(amount_str).replace('$', '').replace(',', '').strip()
            return float(clean_str)
        except ValueError:
            return 0.0

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
            'total_income': 0.0,
            'total_expenses': 0.0,
            'categories': {'Needs': 0.0, 'Wants': 0.0, 'Debt': 0.0},
            'other_expenses': 0.0
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
            if summary['total_income'] > 0 else 0.0
        )
        return summary

    def _get_top_expenses(self, records, target_month=None, target_year=None, n=5):
        """Returns the top N individual expense rows for the period, sorted by amount desc."""
        target_year = target_year or now().year
        expenses = []
        for row in records:
            if len(row) < 4:
                continue
            try:
                trans_type = row[1].strip().capitalize()
                if trans_type != 'Expense':
                    continue
                category = row[4].strip().capitalize() if len(row) > 4 else ""
                if category.lower() in NEUTRAL_CATEGORIES:
                    continue
                if target_month:
                    row_date = _parse_date(row[0])
                    if row_date is None or row_date.month != target_month or row_date.year != target_year:
                        continue
                amount = _parse_amount(row[3])
                description = row[5].strip() if len(row) > 5 else ""
                expenses.append((amount, category, description, row[0]))
            except Exception:
                continue
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
                monthly_totals[key] = monthly_totals.get(key, 0.0) + amount
            except Exception:
                continue
        if not monthly_totals:
            return 0.0
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
        score += check("Needs within 50% budget", income > 0 and summary['categories']['Needs'] <= income * 0.5, 2)
        score += check("Wants within 30% budget", income > 0 and summary['categories']['Wants'] <= income * 0.3, 1)
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
                balances[account] = balances.get(account, 0.0) + (amount if trans_type == 'Income' else -amount)
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
            date = now().strftime("%Y-%m-%d %H:%M:%S")
            rows = [
                [date, "Expense", from_acc, amount, "Transfer", f"Transfer to {to_acc} {desc}".strip()],
                [date, "Income", to_acc, amount, "Transfer", f"Transfer from {from_acc} {desc}".strip()]
            ]
            preview = (
                f"💸 *Transfer*\n"
                f"  From: *{from_acc}*\n"
                f"  To: *{to_acc}*\n"
                f"  Amount: *${amount:,.2f}*"
            )
            if desc:
                preview += f"\n  Note: {desc}"
            return rows, preview, None

        # Income / Expense pattern
        parts = text.split(maxsplit=4)

        if len(parts) < 3:
            return None, None, (
                "❌ Too few arguments.\n\n"
                "Format: `[Income/Expense] [Account] [Amount] [Category] [Description]`\n"
                "Example: `Expense Cash 50 Needs Groceries`"
            )

        trans_type = parts[0].capitalize()
        if trans_type not in ('Income', 'Expense'):
            return None, None, (
                f"❌ Unknown type `{parts[0]}`. Must be `Income` or `Expense`.\n\n"
                "Example: `Expense Cash 50 Needs Groceries`"
            )

        if len(parts) < 3:
            return None, None, "❌ Missing amount. Format: `[Income/Expense] [Account] [Amount] [Category]`"

        amount = _parse_amount_strict(parts[2])
        if amount is None:
            return None, None, (
                f"❌ `{parts[2]}` is not a valid amount. Use a number like `50`, `1,500`, or `$50`."
            )

        if len(parts) < 4:
            return None, None, (
                "❌ Missing category. Allowed: `Needs`, `Wants`, `Savings`, `Debt`\n"
                "Example: `Expense Cash 50 Needs Groceries`"
            )

        account = parts[1].capitalize()
        category = parts[3]
        description = parts[4] if len(parts) > 4 else ""

        if str(user_id) in self.strict_users and trans_type == 'Expense':
            allowed = ['Needs', 'Wants', 'Savings', 'Debt']
            matched = next((a for a in allowed if a.lower() == category.lower()), None)
            if matched:
                category = matched
            else:
                return None, None, (
                    f"❌ Category `{category}` is not allowed.\n"
                    f"Allowed: `{', '.join(allowed)}`"
                )

        date = now().strftime("%Y-%m-%d %H:%M:%S")
        rows = [[date, trans_type, account, amount, category, description]]

        # Savings auto-transfer
        if trans_type == 'Expense' and category.lower() == 'savings':
            rows.append([date, "Income", "Account", amount, "Savings",
                         f"Savings from {account} {description}".strip()])

        # Build preview
        emoji = "💰" if trans_type == 'Income' else "💸"
        preview = (
            f"{emoji} *{trans_type}*\n"
            f"  Account:  *{account}*\n"
            f"  Amount:   *${amount:,.2f}*\n"
            f"  Category: *{category}*"
        )
        if description:
            preview += f"\n  Note:     {description}"
        if trans_type == 'Expense' and category.lower() == 'savings':
            preview += f"\n\n  _(Also auto-logs Income → Account for savings transfer)_"

        return rows, preview, None

    def _insert_row(self, sheet, row_data):
        existing_data = sheet.col_values(1)
        next_row_index = len(existing_data) + 1
        sheet.insert_row(row_data, index=next_row_index, value_input_option='USER_ENTERED')

    def _insert_rows(self, sheet, rows_data):
        existing_data = sheet.col_values(1)
        next_row_index = len(existing_data) + 1
        sheet.insert_rows(rows_data, row=next_row_index, value_input_option='USER_ENTERED')

    async def _show_preview(self, message, context, rows, preview, sheet):
        """Shared helper: shows the transaction preview with confirm/cancel buttons.
        Checks for anomalies if it's an expense. Called from both freetext and /log flows."""
        context.user_data['pending_rows'] = rows
        warning = ""

        # Anomaly detection — only for single-row expenses
        row = rows[0]
        if row[1] == 'Expense' and row[4] != 'Transfer':
            try:
                loop = asyncio.get_running_loop()
                records = await loop.run_in_executor(None, sheet.get_all_values)
                avg = self._get_category_average(records, row[4])
                amount = float(row[3])
                if avg > 0 and amount >= avg * 3:
                    warning = f"\n\n⚠️ *Heads up:* This is unusually high for *{row[4]}* (your avg is `${avg:,.2f}`)."
            except Exception:
                pass

        keyboard = [
            [InlineKeyboardButton("✅ Log it", callback_data='tx_confirm'),
             InlineKeyboardButton("❌ Cancel", callback_data='tx_cancel')]
        ]
        await message.reply_text(
            f"{preview}{warning}\n\nLog this transaction?",
            reply_markup=InlineKeyboardMarkup(keyboard),
            parse_mode='Markdown'
        )
        return CONFIRM_TRANSACTION

    async def preview_transaction(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Parses freetext transaction message, shows preview with anomaly check."""
        user_id = update.message.from_user.id
        sheet = self.get_user_sheet(user_id)

        if not sheet:
            await update.message.reply_text(
                "❌ You are not authorized. Ask the admin to link your account.\n"
                f"Your Telegram ID is: `{user_id}`",
                parse_mode='Markdown'
            )
            return ConversationHandler.END

        rows, preview, error = self._parse_transaction_text(update.message.text, user_id)
        if error:
            await update.message.reply_text(error, parse_mode='Markdown')
            return ConversationHandler.END

        return await self._show_preview(update.message, context, rows, preview, sheet)

    async def confirm_transaction_callback(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Handles the confirm/cancel button after previewing a transaction."""
        query = update.callback_query
        await query.answer()

        if query.data == 'tx_cancel':
            await query.edit_message_text("❌ Transaction cancelled.")
            context.user_data.pop('pending_rows', None)
            return ConversationHandler.END

        rows = context.user_data.pop('pending_rows', None)
        if not rows:
            await query.edit_message_text("❌ Something went wrong. Please try again.")
            return ConversationHandler.END

        user_id = query.from_user.id
        sheet = self.get_user_sheet(user_id)
        if not sheet:
            await query.edit_message_text("❌ Could not reach your sheet. Try again.")
            return ConversationHandler.END

        try:
            loop = asyncio.get_running_loop()
            if len(rows) > 1:
                await loop.run_in_executor(None, lambda: self._insert_rows(sheet, rows))
            else:
                await loop.run_in_executor(None, lambda: self._insert_row(sheet, rows[0]))

            # Fetch updated records once — used for both balance and budget alert
            records = await loop.run_in_executor(None, sheet.get_all_values)
            balances = self._get_balance_by_account(records)

            row = rows[0]
            trans_type, account, amount, category = row[1], row[2], row[3], row[4]
            description = row[5] if len(row) > 5 else ""
            emoji = "💰" if trans_type == 'Income' else "💸"
            account_balance = balances.get(account, 0.0)

            confirmation = (
                f"✅ *Logged successfully!*\n\n"
                f"{emoji} *{trans_type}*\n"
                f"  Account:  *{account}*\n"
                f"  Amount:   *${float(amount):,.2f}*\n"
                f"  Category: *{category}*"
            )
            if description:
                confirmation += f"\n  Note:     {description}"
            confirmation += f"\n\n  📊 *{account} balance*: `${account_balance:,.2f}`"

            # Budget alert — only for expenses in a tracked category
            budget_pct = {'Needs': 0.5, 'Wants': 0.3, 'Debt': 0.2}
            _n = now()
            if trans_type == 'Expense' and category in budget_pct:
                try:
                    s = self._get_data_summary(records, _n.month, _n.year)
                    if s['total_income'] > 0:
                        budget_limit = s['total_income'] * budget_pct[category]
                        spent = s['categories'].get(category, 0.0)
                        if spent > budget_limit:
                            over = spent - budget_limit
                            confirmation += (
                                f"\n\n⚠️ *Budget alert:* You've exceeded your "
                                f"*{category}* budget by `${over:,.2f}` this month."
                            )
                except Exception:
                    pass

            await query.edit_message_text(confirmation, parse_mode='Markdown')

        except Exception as e:
            logger.error(f"Sheet write error: {e}")
            await query.edit_message_text(f"❌ Failed to save: {str(e)}")

        return ConversationHandler.END

    async def cancel_transaction(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        context.user_data.pop('pending_rows', None)
        await update.message.reply_text("❌ Transaction cancelled.")
        return ConversationHandler.END

    async def start_cmd(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        user_id = update.message.from_user.id
        if str(user_id) in self.user_mapping:
            await update.message.reply_text(f"Welcome back! Your ID is `{user_id}` and your sheet is linked.",
                                            parse_mode='Markdown')
        else:
            await update.message.reply_text(f"Hello! You are not authorized. Send your ID to the admin: `{user_id}`",
                                            parse_mode='Markdown')

    async def help_cmd(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        logger.info(f"FinanceBot: Help command triggered by user {update.effective_user.id}")
        help_text = (
            "*Finance Logging:*\n"
            "`[Income/Expense] [Account] [Amount] [Category] [Description]`\n"
            "Example: `Expense Cash 15.50 Needs Lunch`\n\n"
            "*Transfers:*\n"
            "`Transfer [From] to [To] [Amount]`\n"
            "Example: `Transfer Digital to Cash 1500`\n\n"
            "*Analysis:*\n"
            "/dash - Full snapshot: balances, budget, savings rate.\n"
            "/summary - This month's income, expenses, net & trends.\n"
            "/summary [month] - Summary for a specific month.\n"
            "/top - Top 5 expenses this month.\n"
            "/top [month] - Top 5 expenses for a specific month.\n"
            "/ytd - Year-to-date totals and savings rate.\n"
            "/net - Net worth breakdown with chart.\n"
            "/expenses - Expense pie chart (all time or by month).\n"
            "/calcExpenses - Budget status using the 50/30/20 rule.\n"
            "/balance - Current account balances.\n"
            "/savings - Savings pot: set aside, withdrawn, and net. Log `Income [Acct] [Amt] Savings` to record a withdrawal.\n\n"
            "*Quick-log shortcuts:*\n"
            "/ql - List your shortcuts.\n"
            "/ql <name> - Fire a shortcut.\n"
            "/ql add <name> <tx> - Save a new shortcut.\n"
            "/ql delete <name> - Remove a shortcut.\n\n"
            "*Guided entry:*\n"
            "/log - Step-by-step transaction entry with buttons.\n\n"
            "*Recurring transactions:*\n"
            "/recurring - List recurring transactions.\n"
            "/recurring add <name> <day> <tx> - Auto-log monthly on a given day.\n"
            "/recurring delete <name> - Remove a recurring transaction.\n\n"
            "*Goals:*\n"
            "/goals - Show all savings goals with progress.\n"
            "/setgoal <name> <amount> - Create or update a goal.\n"
            "/addtogoal <name> <amount> - Add savings to a goal.\n\n"
            "*Utilities:*\n"
            "/calc [expression] - Calculator (e.g. `/calc 5 * 2`).\n"
            "/start - Check your authorization.\n"
            "/help - This message.\n\n"
            "_Tip: Transactions show a preview and anomaly warnings before logging._"
        )
        await update.message.reply_text(help_text, parse_mode='Markdown')

    async def calculate_balance(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        user_id = update.message.from_user.id
        sheet = self.get_user_sheet(user_id)

        if not sheet:
            await update.message.reply_text("❌ Unauthorized.")
            return

        try:
            loop = asyncio.get_running_loop()
            records = await loop.run_in_executor(None, sheet.get_all_values)
            balances = self._get_balance_by_account(records)

            if not balances:
                await update.message.reply_text("No transactions found to calculate balance.")
                return

            response = "💰 *Your Account Balances:*\n"
            for acc, bal in balances.items():
                indicator = "🟢" if bal >= 0 else "🔴"
                response += f"{indicator} {acc}: `${bal:,.2f}`\n"

            await update.message.reply_text(response, parse_mode='Markdown')
        except Exception as e:
            await update.message.reply_text(f"❌ Error: {str(e)}")

    async def calculator(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        expression = " ".join(context.args)
        try:
            result = eval(expression, {"__builtins__": {}}, {})
            await update.message.reply_text(f"🔢 Result: `{result}`", parse_mode='Markdown')
        except:
            await update.message.reply_text("❌ Invalid calculation.")

    async def generate_expenses_chart(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        user_id = update.message.from_user.id
        sheet = self.get_user_sheet(user_id)

        if not sheet:
            await update.message.reply_text("❌ Unauthorized.")
            return

        target_month = None
        target_year = now().year
        chart_title = "Expenses Breakdown (All Time)"

        if context.args:
            arg = context.args[0]
            target_month = self._parse_month(arg)
            if not target_month:
                await update.message.reply_text(
                    f"❌ `{arg}` is not a valid month. Try `/expenses january` or `/expenses jan`.",
                    parse_mode='Markdown'
                )
                return
            month_name = datetime(target_year, target_month, 1).strftime("%B")
            chart_title = f"Expenses Breakdown ({month_name} {target_year})"

        try:
            loop = asyncio.get_running_loop()
            records = await loop.run_in_executor(None, sheet.get_all_values)
            summary = self._get_data_summary(records, target_month, target_year)
            categories = summary['categories']

            labels = [k for k, v in categories.items() if v > 0]
            sizes = [v for k, v in categories.items() if v > 0]
            total_categorized = sum(sizes)

            if not sizes and summary['other_expenses'] == 0:
                period = f" for {datetime(target_year, target_month, 1).strftime('%B %Y')}" if target_month else ""
                await update.message.reply_text(f"No expenses found{period}.")
                return

            buf = None
            if sizes:
                fig, ax = plt.subplots()
                ax.pie(sizes, labels=labels, autopct='%1.1f%%', startangle=90)
                ax.axis('equal')
                plt.title(chart_title)
                buf = io.BytesIO()
                plt.savefig(buf, format='png')
                buf.seek(0)
                plt.close(fig)

            breakdown_text = f"📊 *{chart_title}:*\n"
            for label, size in zip(labels, sizes):
                pct = (size / total_categorized) * 100 if total_categorized > 0 else 0
                breakdown_text += f"  • *{label}*: ${size:,.2f} ({pct:.1f}%)\n"
            if summary['other_expenses'] > 0:
                breakdown_text += f"  • *Other*: ${summary['other_expenses']:,.2f}\n"
            breakdown_text += f"\n*Total Expenses*: ${summary['total_expenses']:,.2f}"
            breakdown_text += f"\n*Net Worth*: `${summary['net_worth']:,.2f}`"

            if buf:
                await update.message.reply_photo(photo=buf, caption=breakdown_text, parse_mode='Markdown')
            else:
                await update.message.reply_text(breakdown_text, parse_mode='Markdown')

        except Exception as e:
            logger.error(f"Chart Error: {e}")
            await update.message.reply_text(f"❌ Error generating chart: {str(e)}")

    async def calculate_net_worth(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        user_id = update.message.from_user.id
        sheet = self.get_user_sheet(user_id)

        if not sheet:
            await update.message.reply_text("❌ Unauthorized.")
            return

        target_month = None
        target_year = now().year
        title_suffix = "(All Time)"

        if context.args:
            arg = context.args[0]
            target_month = self._parse_month(arg)
            if not target_month:
                await update.message.reply_text(
                    f"❌ `{arg}` is not a valid month. Try `/net january`.",
                    parse_mode='Markdown'
                )
                return
            month_name = datetime(target_year, target_month, 1).strftime("%B")
            title_suffix = f"({month_name} {target_year})"

        try:
            loop = asyncio.get_running_loop()
            records = await loop.run_in_executor(None, sheet.get_all_values)
            summary = self._get_data_summary(records, target_month, target_year)
            total_income = summary['total_income']
            total_expenses = summary['total_expenses']
            net_worth = summary['net_worth']

            if total_income == 0 and total_expenses == 0:
                await update.message.reply_text(f"No records found {title_suffix.lower()}.")
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
                ax.pie([total_expenses, net_worth], labels=['Expenses', 'Net Worth'],
                       autopct='%1.1f%%', startangle=90, colors=['#ff9999', '#66b3ff'])
                ax.axis('equal')
                plt.title(f'Net Worth Breakdown {title_suffix}')
                buf = io.BytesIO()
                plt.savefig(buf, format='png')
                buf.seek(0)
                plt.close(fig)
                chart_generated = True

            net_emoji = "📈" if net_worth >= 0 else "📉"
            breakdown_text = (
                f"💰 *Net Worth Summary {title_suffix}:*\n\n"
                f"  • *Total Income*:   `${total_income:,.2f}`\n"
                f"  • *Total Expenses*: `${total_expenses:,.2f}`"
            )
            if total_income > 0:
                breakdown_text += f" ({expense_pct:.1f}% of income)"
            breakdown_text += f"\n\n{net_emoji} *Net Worth*: `${net_worth:,.2f}`"
            if total_income > 0:
                breakdown_text += f" ({net_pct:.1f}% of income remaining)"

            if chart_generated:
                await update.message.reply_photo(photo=buf, caption=breakdown_text, parse_mode='Markdown')
            else:
                await update.message.reply_text(breakdown_text, parse_mode='Markdown')

        except Exception as e:
            logger.error(f"Net Worth Error: {e}")
            await update.message.reply_text(f"❌ Error calculating net worth: {str(e)}")

    async def calc_expenses_budget(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        user_id = update.message.from_user.id
        sheet = self.get_user_sheet(user_id)

        if not sheet:
            await update.message.reply_text("❌ Unauthorized.")
            return

        target_month = None
        target_year = now().year
        title_suffix = "(All Time)"

        if context.args:
            arg = context.args[0]
            target_month = self._parse_month(arg)
            if not target_month:
                await update.message.reply_text(
                    f"❌ `{arg}` is not a valid month. Try `/calcExpenses january`.",
                    parse_mode='Markdown'
                )
                return
            month_name = datetime(target_year, target_month, 1).strftime("%B")
            title_suffix = f"({month_name} {target_year})"

        try:
            loop = asyncio.get_running_loop()
            records = await loop.run_in_executor(None, sheet.get_all_values)
            summary = self._get_data_summary(records, target_month, target_year)
            total_income = summary['total_income']

            if total_income <= 0:
                await update.message.reply_text(
                    f"No income recorded {title_suffix.lower()}. Cannot calculate budget."
                )
                return

            actual_needs = summary['categories']['Needs']
            actual_wants = summary['categories']['Wants']
            # Net savings: money set aside minus money withdrawn, plus debt payments
            txs = parse_rows(records, target_month, target_year)
            expense_savings = sum(t.amount for t in txs if t.type == 'Expense' and t.category.lower() == 'savings')
            income_savings = sum(t.amount for t in txs if t.type == 'Income' and t.category.lower() == 'savings')
            actual_savings = expense_savings - income_savings + summary['categories']['Debt']

            budget_needs = total_income * 0.5
            budget_wants = total_income * 0.3
            budget_savings = total_income * 0.2

            def status_line(actual, budget):
                if actual > budget:
                    return f"⚠️ *OVER* by `${actual - budget:,.2f}`"
                else:
                    return f"✅ `${budget - actual:,.2f}` remaining"

            response = (
                f"💰 *Budget Status {title_suffix}*\n"
                f"  Income: `${total_income:,.2f}`\n\n"
                f"🏠 *Needs (50%)* — budget `${budget_needs:,.2f}`\n"
                f"  Spent: `${actual_needs:,.2f}` → {status_line(actual_needs, budget_needs)}\n\n"
                f"🎉 *Wants (30%)* — budget `${budget_wants:,.2f}`\n"
                f"  Spent: `${actual_wants:,.2f}` → {status_line(actual_wants, budget_wants)}\n\n"
                f"📈 *Savings/Debt (20%)* — budget `${budget_savings:,.2f}`\n"
                f"  Spent: `${actual_savings:,.2f}` → {status_line(actual_savings, budget_savings)}\n\n"
                f"*Net Worth*: `${summary['net_worth']:,.2f}`"
            )

            await update.message.reply_text(response, parse_mode='Markdown')

        except Exception as e:
            logger.error(f"Calc Expenses Error: {e}")
            await update.message.reply_text(f"❌ Error: {str(e)}")

    async def savings_cmd(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """/savings [period] — show money set aside, withdrawn, and current pot."""
        user_id = update.message.from_user.id
        sheet = self.get_user_sheet(user_id)
        if not sheet:
            await update.message.reply_text("❌ Unauthorized.")
            return

        month, year, period_desc = parse_period(context.args)
        if period_desc is None:
            await update.message.reply_text(
                f"❌ Could not understand period: `{' '.join(context.args)}`\n"
                "Try: `this month`, `last month`, `january`, `january 2025`, `2025`.",
                parse_mode='Markdown'
            )
            return

        try:
            loop = asyncio.get_running_loop()
            records = await loop.run_in_executor(None, sheet.get_all_values)
            txs = parse_rows(records, month, year)

            set_aside = sum(t.amount for t in txs if t.type == 'Expense' and t.category.lower() == 'savings')
            withdrew = sum(t.amount for t in txs if t.type == 'Income' and t.category.lower() == 'savings')
            pot = set_aside - withdrew

            response = (
                f"📈 *Savings — {period_desc}:*\n\n"
                f"  Set aside: `${set_aside:,.2f}`\n"
                f"  Withdrew:  `${withdrew:,.2f}`\n"
                f"  Pot:       `${pot:,.2f}`\n\n"
                f"_To record taking money back out: `Income [Account] [Amount] Savings`_"
            )
            await update.message.reply_text(response, parse_mode='Markdown')

        except Exception as e:
            logger.error(f"Savings error: {e}")
            corr_id = uuid.uuid4().hex[:8]
            await update.message.reply_text(f"❌ Something went wrong. Ref: `{corr_id}`", parse_mode='Markdown')

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
                    text=f"[{corr_id}]\n```\n{tb[-3000:]}\n```",
                    parse_mode='Markdown'
                )
            except Exception:
                pass

        if update and update.effective_message:
            await update.effective_message.reply_text(
                f"😔 Something went wrong. Reference: `{corr_id}`",
                parse_mode='Markdown'
            )

    async def summary_cmd(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Monthly summary: income, expenses, net, savings rate, category breakdown, and trends."""
        user_id = update.message.from_user.id
        sheet = self.get_user_sheet(user_id)
        if not sheet:
            await update.message.reply_text("❌ Unauthorized.")
            return

        now_dt = now()
        target_year = now_dt.year

        if context.args:
            target_month = self._parse_month(context.args[0])
            if not target_month:
                await update.message.reply_text(
                    f"❌ `{context.args[0]}` is not a valid month. Try `/summary august`.",
                    parse_mode='Markdown'
                )
                return
        else:
            target_month = now_dt.month

        month_name = datetime(target_year, target_month, 1).strftime("%B %Y")
        prev_month, prev_year = self._previous_month(target_month, target_year)

        try:
            loop = asyncio.get_running_loop()
            records = await loop.run_in_executor(None, sheet.get_all_values)

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
                await update.message.reply_text(f"No records found for {month_name}.")
                return

            net_emoji = "📈" if curr['net_worth'] >= 0 else "📉"
            savings_emoji = "✅" if curr['savings_rate'] >= 20 else ("⚠️" if curr['savings_rate'] >= 10 else "🔴")

            lines = [f"📋 *Summary — {month_name}*\n"]

            lines.append(f"💰 *Income*: `${curr['total_income']:,.2f}`{trend(curr['total_income'], prev['total_income'])}")
            lines.append(f"💸 *Expenses*: `${curr['total_expenses']:,.2f}`{trend(curr['total_expenses'], prev['total_expenses'])}")
            lines.append(f"{net_emoji} *Net*: `${curr['net_worth']:,.2f}`")
            lines.append(f"{savings_emoji} *Savings rate*: `{curr['savings_rate']}%`{trend(curr['savings_rate'], prev['savings_rate'])}\n")

            lines.append("*By category:*")
            for cat, amount in curr['categories'].items():
                if amount > 0 or prev['categories'].get(cat, 0) > 0:
                    t = trend(amount, prev['categories'].get(cat, 0))
                    lines.append(f"  • {cat}: `${amount:,.2f}`{t}")
            if curr['other_expenses'] > 0:
                lines.append(f"  • Other: `${curr['other_expenses']:,.2f}`")

            # Spend forecast — only for current month
            if target_month == now_dt.month and target_year == now_dt.year and curr['total_expenses'] > 0:
                days_in_month = calendar.monthrange(now_dt.year, now_dt.month)[1]
                projected = self._spend_forecast(curr['total_expenses'], now_dt.day, days_in_month)
                lines.append(f"\n📉 *Forecast*: `${projected:,.2f}` by month-end")

            # Goals snapshot — show if any goals exist
            try:
                ws = await loop.run_in_executor(None, lambda: self._get_goals_sheet(user_id))
                goals = await loop.run_in_executor(None, lambda: self._load_goals(ws))
                if goals:
                    lines.append("\n🎯 *Goals:*")
                    for goal in goals:
                        pct = min((goal['saved'] / goal['target']) * 100, 100) if goal['target'] > 0 else 0
                        bar = "█" * int(pct // 10) + "░" * (10 - int(pct // 10))
                        lines.append(f"  {goal['name']}: `{bar}` {pct:.1f}%")
            except Exception:
                pass

            await update.message.reply_text("\n".join(lines), parse_mode='Markdown')

        except Exception as e:
            logger.error(f"Summary error: {e}")
            await update.message.reply_text(f"❌ Error: {str(e)}")

    async def top_expenses_cmd(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Shows the top 5 individual expenses for the month."""
        user_id = update.message.from_user.id
        sheet = self.get_user_sheet(user_id)
        if not sheet:
            await update.message.reply_text("❌ Unauthorized.")
            return

        now_dt = now()
        target_year = now_dt.year

        if context.args:
            target_month = self._parse_month(context.args[0])
            if not target_month:
                await update.message.reply_text(
                    f"❌ `{context.args[0]}` is not a valid month. Try `/top august`.",
                    parse_mode='Markdown'
                )
                return
        else:
            target_month = now_dt.month

        month_name = datetime(target_year, target_month, 1).strftime("%B %Y")

        try:
            loop = asyncio.get_running_loop()
            records = await loop.run_in_executor(None, sheet.get_all_values)
            top = self._get_top_expenses(records, target_month, target_year)

            if not top:
                await update.message.reply_text(f"No expenses found for {month_name}.")
                return

            lines = [f"🏆 *Top Expenses — {month_name}*\n"]
            medals = ["🥇", "🥈", "🥉", "4️⃣", "5️⃣"]
            for i, (amount, category, description, date) in enumerate(top):
                label = description if description else category
                lines.append(f"{medals[i]} `${amount:,.2f}` — {label} _({category})_")

            await update.message.reply_text("\n".join(lines), parse_mode='Markdown')

        except Exception as e:
            logger.error(f"Top expenses error: {e}")
            await update.message.reply_text(f"❌ Error: {str(e)}")

    async def ytd_cmd(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Year-to-date summary: total income, expenses, net worth, and savings rate."""
        user_id = update.message.from_user.id
        sheet = self.get_user_sheet(user_id)
        if not sheet:
            await update.message.reply_text("❌ Unauthorized.")
            return

        year = now().year

        try:
            loop = asyncio.get_running_loop()
            records = await loop.run_in_executor(None, sheet.get_all_values)
            s = self._get_data_summary(records, year_only=True, target_year=year)

            if s['total_income'] == 0 and s['total_expenses'] == 0:
                await update.message.reply_text(f"No records found for {year}.")
                return

            net_emoji = "📈" if s['net_worth'] >= 0 else "📉"
            savings_emoji = "✅" if s['savings_rate'] >= 20 else ("⚠️" if s['savings_rate'] >= 10 else "🔴")

            lines = [
                f"📅 *Year-to-Date — {year}*\n",
                f"💰 *Total Income*:   `${s['total_income']:,.2f}`",
                f"💸 *Total Expenses*: `${s['total_expenses']:,.2f}`",
                f"{net_emoji} *Net Worth*:      `${s['net_worth']:,.2f}`",
                f"{savings_emoji} *Savings Rate*:   `{s['savings_rate']}%`\n",
                "*Expenses by category:*",
            ]
            for cat, amount in s['categories'].items():
                if amount > 0:
                    pct = (amount / s['total_expenses'] * 100) if s['total_expenses'] > 0 else 0
                    lines.append(f"  • {cat}: `${amount:,.2f}` ({pct:.1f}%)")
            if s['other_expenses'] > 0:
                lines.append(f"  • Other: `${s['other_expenses']:,.2f}`")

            await update.message.reply_text("\n".join(lines), parse_mode='Markdown')

        except Exception as e:
            logger.error(f"YTD error: {e}")
            await update.message.reply_text(f"❌ Error: {str(e)}")

    async def dashboard_cmd(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """One-command financial snapshot: balances + this month's budget + net worth + savings rate."""
        user_id = update.message.from_user.id
        sheet = self.get_user_sheet(user_id)
        if not sheet:
            await update.message.reply_text("❌ Unauthorized.")
            return

        now_dt = now()
        month_name = now_dt.strftime("%B %Y")

        try:
            loop = asyncio.get_running_loop()
            records = await loop.run_in_executor(None, sheet.get_all_values)

            balances = self._get_balance_by_account(records)
            s = self._get_data_summary(records, now_dt.month, now_dt.year)
            prev_month, prev_year = self._previous_month(now_dt.month, now_dt.year)
            prev_s = self._get_data_summary(records, prev_month, prev_year)

            score, max_score, breakdown = self._get_health_score(s, prev_s, balances)
            score_bar = "█" * score + "░" * (max_score - score)
            score_emoji = "🟢" if score >= 8 else ("🟡" if score >= 5 else "🔴")

            lines = [
                f"📊 *Dashboard — {month_name}*\n",
                f"{score_emoji} *Health Score: {score}/{max_score}* `{score_bar}`\n"
            ]

            # Account balances
            lines.append("*💳 Balances:*")
            if balances:
                for acc, bal in balances.items():
                    indicator = "🟢" if bal >= 0 else "🔴"
                    lines.append(f"  {indicator} {acc}: `${bal:,.2f}`")
            else:
                lines.append("  No transactions yet.")

            # This month budget
            lines.append("\n*📆 This Month:*")
            if s['total_income'] > 0 or s['total_expenses'] > 0:
                net_emoji = "📈" if s['net_worth'] >= 0 else "📉"
                savings_emoji = "✅" if s['savings_rate'] >= 20 else ("⚠️" if s['savings_rate'] >= 10 else "🔴")
                lines.append(f"  💰 Income:   `${s['total_income']:,.2f}`")
                lines.append(f"  💸 Expenses: `${s['total_expenses']:,.2f}`")
                lines.append(f"  {net_emoji} Net:      `${s['net_worth']:,.2f}`")
                lines.append(f"  {savings_emoji} Savings rate: `{s['savings_rate']}%`")

                # Budget status (50/30/20)
                if s['total_income'] > 0:
                    lines.append("\n*📏 50/30/20 Budget:*")
                    _txs = parse_rows(records, now_dt.month, now_dt.year)
                    _exp_sav = sum(t.amount for t in _txs if t.type == 'Expense' and t.category.lower() == 'savings')
                    _inc_sav = sum(t.amount for t in _txs if t.type == 'Income' and t.category.lower() == 'savings')
                    net_savings_debt = _exp_sav - _inc_sav + s['categories']['Debt']
                    budgets = {
                        'Needs': (s['categories']['Needs'], s['total_income'] * 0.5),
                        'Wants': (s['categories']['Wants'], s['total_income'] * 0.3),
                        'Savings/Debt': (net_savings_debt, s['total_income'] * 0.2),
                    }
                    for label, (actual, budget) in budgets.items():
                        if actual > budget:
                            status = f"⚠️ over by `${actual - budget:,.2f}`"
                        else:
                            status = f"✅ `${budget - actual:,.2f}` left"
                        lines.append(f"  {label}: {status}")

                # Spend forecast
                days_in_month = calendar.monthrange(now_dt.year, now_dt.month)[1]
                day = now_dt.day
                if day > 0 and s['total_expenses'] > 0:
                    projected = self._spend_forecast(s['total_expenses'], day, days_in_month)
                    lines.append(f"\n*📉 Forecast:*")
                    lines.append(f"  At current pace: `${projected:,.2f}` by month-end")
                    if s['total_income'] > 0 and projected > s['total_income']:
                        lines.append(f"  ⚠️ Projected to exceed income by `${projected - s['total_income']:,.2f}`")
            else:
                lines.append("  No transactions this month yet.")

            await update.message.reply_text("\n".join(lines), parse_mode='Markdown')

        except Exception as e:
            logger.error(f"Dashboard error: {e}")
            await update.message.reply_text(f"❌ Error: {str(e)}")

    # ── Quick-log shortcuts (/ql) ─────────────────────────────────────────────

    async def quicklog_cmd(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """
        /ql                          → list all shortcuts
        /ql <name>                   → fire that shortcut through the preview flow
        /ql add <name> <transaction> → save a new shortcut
        /ql delete <name>            → remove a shortcut
        """
        user_id = update.message.from_user.id
        sheet = self.get_user_sheet(user_id)
        if not sheet:
            await update.message.reply_text("❌ Unauthorized.")
            return

        args = context.args

        # No args — list shortcuts
        if not args:
            loop = asyncio.get_running_loop()
            ws = await loop.run_in_executor(None, lambda: self._get_shortcuts_sheet(user_id))
            if not ws:
                await update.message.reply_text("❌ Could not open shortcuts sheet.")
                return
            shortcuts = await loop.run_in_executor(None, lambda: self._load_shortcuts(ws))
            if not shortcuts:
                await update.message.reply_text(
                    "No shortcuts saved yet.\n"
                    "Add one: `/ql add lunch Expense Cash 15 Wants Lunch`",
                    parse_mode='Markdown'
                )
                return
            lines = ["*⚡ Your shortcuts:*\n"]
            for name, tx in sorted(shortcuts.items()):
                lines.append(f"  `/ql {name}` → `{tx}`")
            await update.message.reply_text("\n".join(lines), parse_mode='Markdown')
            return

        subcommand = args[0].lower()

        # Add shortcut
        if subcommand == 'add':
            if len(args) < 3:
                await update.message.reply_text(
                    "Usage: `/ql add <name> <transaction>`\n"
                    "Example: `/ql add lunch Expense Cash 15 Wants Lunch`",
                    parse_mode='Markdown'
                )
                return
            name = args[1].lower()
            transaction = " ".join(args[2:])
            # Validate the transaction parses correctly before saving
            rows, _, error = self._parse_transaction_text(transaction, user_id)
            if error:
                await update.message.reply_text(
                    f"❌ That transaction doesn't parse correctly:\n{error}",
                    parse_mode='Markdown'
                )
                return
            loop = asyncio.get_running_loop()
            ws = await loop.run_in_executor(None, lambda: self._get_shortcuts_sheet(user_id))
            shortcuts = await loop.run_in_executor(None, lambda: self._load_shortcuts(ws))
            # Update or append
            records = await loop.run_in_executor(None, ws.get_all_records)
            for i, r in enumerate(records, start=2):
                if r.get('Name', '').strip().lower() == name:
                    await loop.run_in_executor(None, lambda: ws.update(f'B{i}', [[transaction]]))
                    await update.message.reply_text(f"✅ Updated shortcut `{name}`.", parse_mode='Markdown')
                    return
            await loop.run_in_executor(None, lambda: ws.append_row([name, transaction]))
            await update.message.reply_text(
                f"✅ Shortcut saved: `/ql {name}` → `{transaction}`",
                parse_mode='Markdown'
            )
            return

        # Delete shortcut
        if subcommand == 'delete':
            if len(args) < 2:
                await update.message.reply_text("Usage: `/ql delete <name>`", parse_mode='Markdown')
                return
            name = args[1].lower()
            loop = asyncio.get_running_loop()
            ws = await loop.run_in_executor(None, lambda: self._get_shortcuts_sheet(user_id))
            records = await loop.run_in_executor(None, ws.get_all_records)
            for i, r in enumerate(records, start=2):
                if r.get('Name', '').strip().lower() == name:
                    await loop.run_in_executor(None, lambda: ws.delete_rows(i))
                    await update.message.reply_text(f"✅ Deleted shortcut `{name}`.", parse_mode='Markdown')
                    return
            await update.message.reply_text(f"❌ No shortcut named `{name}`.", parse_mode='Markdown')
            return

        # Fire a shortcut by name
        name = subcommand
        loop = asyncio.get_running_loop()
        ws = await loop.run_in_executor(None, lambda: self._get_shortcuts_sheet(user_id))
        shortcuts = await loop.run_in_executor(None, lambda: self._load_shortcuts(ws))
        transaction = shortcuts.get(name)
        if not transaction:
            await update.message.reply_text(
                f"❌ No shortcut named `{name}`. Use `/ql` to see your list.",
                parse_mode='Markdown'
            )
            return
        rows, preview, error = self._parse_transaction_text(transaction, user_id)
        if error:
            await update.message.reply_text(f"❌ Shortcut is broken: {error}", parse_mode='Markdown')
            return
        await self._show_preview(update.message, context, rows, preview, sheet)

    # ── Guided /log flow ──────────────────────────────────────────────────────

    async def log_start(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Entry point for the guided /log flow."""
        user_id = update.message.from_user.id
        if not self.get_user_sheet(user_id):
            await update.message.reply_text("❌ Unauthorized.")
            return ConversationHandler.END
        context.user_data['log_entry'] = {}
        keyboard = [
            [InlineKeyboardButton("💸 Expense", callback_data='log_Expense'),
             InlineKeyboardButton("💰 Income", callback_data='log_Income')],
            [InlineKeyboardButton("↔️ Transfer", callback_data='log_Transfer')]
        ]
        await update.message.reply_text(
            "What type of transaction?",
            reply_markup=InlineKeyboardMarkup(keyboard)
        )
        return LOG_TYPE

    async def log_type_callback(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        query = update.callback_query
        await query.answer()
        trans_type = query.data.replace('log_', '')
        context.user_data['log_entry']['type'] = trans_type

        # Fetch known accounts to offer as quick buttons
        user_id = query.from_user.id
        sheet = self.get_user_sheet(user_id)
        known_accounts = []
        if sheet:
            try:
                loop = asyncio.get_running_loop()
                records = await loop.run_in_executor(None, sheet.get_all_values)
                known_accounts = self._get_known_accounts(records)
            except Exception:
                pass

        if known_accounts:
            buttons = [[InlineKeyboardButton(a, callback_data=f'acc_{a}')] for a in known_accounts[:6]]
            await query.edit_message_text(
                f"*{trans_type}* — Which account?",
                reply_markup=InlineKeyboardMarkup(buttons),
                parse_mode='Markdown'
            )
        else:
            await query.edit_message_text(f"*{trans_type}* — Type the account name:", parse_mode='Markdown')
        return LOG_ACCOUNT

    async def log_account_callback(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        query = update.callback_query
        await query.answer()
        account = query.data.replace('acc_', '')
        context.user_data['log_entry']['account'] = account
        await query.edit_message_text(f"Account: *{account}*\n\nEnter the amount:", parse_mode='Markdown')
        return LOG_AMOUNT

    async def log_account(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        account = update.message.text.strip().capitalize()
        context.user_data['log_entry']['account'] = account
        await update.message.reply_text(f"Account: *{account}*\n\nEnter the amount:", parse_mode='Markdown')
        return LOG_AMOUNT

    async def log_amount(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        text = update.message.text.strip()
        try:
            amount = float(text.replace(',', '').replace('$', ''))
        except ValueError:
            await update.message.reply_text(f"❌ `{text}` is not a valid amount. Try again.", parse_mode='Markdown')
            return LOG_AMOUNT

        context.user_data['log_entry']['amount'] = amount
        trans_type = context.user_data['log_entry']['type']

        if trans_type == 'Transfer':
            # For transfers, ask for destination account
            context.user_data['log_entry']['transfer_step'] = 'to_account'
            await update.message.reply_text("Transfer to which account?")
            return LOG_ACCOUNT

        if trans_type == 'Expense':
            keyboard = [
                [InlineKeyboardButton("🏠 Needs", callback_data='cat_Needs'),
                 InlineKeyboardButton("🎉 Wants", callback_data='cat_Wants')],
                [InlineKeyboardButton("📈 Savings", callback_data='cat_Savings'),
                 InlineKeyboardButton("💳 Debt", callback_data='cat_Debt')]
            ]
            await update.message.reply_text(
                f"Amount: *${amount:,.2f}*\n\nSelect a category:",
                reply_markup=InlineKeyboardMarkup(keyboard),
                parse_mode='Markdown'
            )
            return LOG_CATEGORY
        else:
            # Income — skip category, go to description
            context.user_data['log_entry']['category'] = 'Income'
            return await self._log_ask_description(update.message, context, amount)

    async def log_category_callback(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        query = update.callback_query
        await query.answer()
        category = query.data.replace('cat_', '')
        context.user_data['log_entry']['category'] = category
        amount = context.user_data['log_entry']['amount']
        await query.edit_message_text(
            f"Category: *{category}*\n\nAdd a description? (or tap Skip)",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("⏭ Skip", callback_data='desc_skip')]]),
            parse_mode='Markdown'
        )
        return LOG_DESCRIPTION

    async def _log_ask_description(self, message, context, amount):
        await message.reply_text(
            f"Amount: *${amount:,.2f}*\n\nAdd a description? (or tap Skip)",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("⏭ Skip", callback_data='desc_skip')]]),
            parse_mode='Markdown'
        )
        return LOG_DESCRIPTION

    async def log_description_callback(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Handles the Skip button for description."""
        query = update.callback_query
        await query.answer()
        context.user_data['log_entry']['description'] = ''
        return await self._log_build_and_preview(query, context)

    async def log_description(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        context.user_data['log_entry']['description'] = update.message.text.strip()
        return await self._log_build_and_preview(update.message, context)

    async def _log_build_and_preview(self, message_or_query, context):
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
            tx = f"Income {e['account']} {e['amount']} {category} {e.get('description', '')}".strip()
        else:
            tx = f"Expense {e['account']} {e['amount']} {e['category']} {e.get('description', '')}".strip()

        rows, preview, error = self._parse_transaction_text(tx, user_id)
        if error:
            if hasattr(message_or_query, 'edit_message_text'):
                await message_or_query.edit_message_text(f"❌ {error}", parse_mode='Markdown')
            else:
                await message_or_query.reply_text(f"❌ {error}", parse_mode='Markdown')
            return ConversationHandler.END

        sheet = self.get_user_sheet(user_id)
        if hasattr(message_or_query, 'message'):
            msg = message_or_query.message
        else:
            msg = message_or_query
        return await self._show_preview(msg, context, rows, preview, sheet)

    # ── Recurring transactions ────────────────────────────────────────────────

    async def recurring_cmd(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """
        /recurring                         → list all recurring transactions
        /recurring add <name> <day> <tx>   → add a recurring transaction
        /recurring delete <name>           → remove one
        """
        user_id = update.message.from_user.id
        if not self.get_user_sheet(user_id):
            await update.message.reply_text("❌ Unauthorized.")
            return

        args = context.args
        loop = asyncio.get_running_loop()

        if not args:
            ws = await loop.run_in_executor(None, lambda: self._get_recurring_sheet(user_id))
            items = await loop.run_in_executor(None, lambda: self._load_recurring(ws))
            if not items:
                await update.message.reply_text(
                    "No recurring transactions set up.\n\n"
                    "Add one: `/recurring add rent 1 Expense Cash 1500 Needs Rent`\n"
                    "_(This would log rent on the 1st of each month)_",
                    parse_mode='Markdown'
                )
                return
            lines = ["🔄 *Recurring Transactions:*\n"]
            for item in items:
                day_str = f"Day {item['day']}"
                last = f"last: {item['last_logged']}" if item['last_logged'] else "never logged"
                lines.append(f"  • *{item['name']}* — {day_str} — `{item['transaction']}` _({last})_")
            await update.message.reply_text("\n".join(lines), parse_mode='Markdown')
            return

        subcommand = args[0].lower()

        if subcommand == 'add':
            if len(args) < 4:
                await update.message.reply_text(
                    "Usage: `/recurring add <name> <day> <transaction>`\n"
                    "Example: `/recurring add rent 1 Expense Cash 1500 Needs Rent`",
                    parse_mode='Markdown'
                )
                return
            name = args[1]
            try:
                day = int(args[2])
                if not 1 <= day <= 31:
                    raise ValueError
            except ValueError:
                await update.message.reply_text("❌ Day must be a number between 1 and 31.")
                return
            transaction = " ".join(args[3:])
            _, _, error = self._parse_transaction_text(transaction, user_id)
            if error:
                await update.message.reply_text(f"❌ Transaction doesn't parse:\n{error}", parse_mode='Markdown')
                return
            ws = await loop.run_in_executor(None, lambda: self._get_recurring_sheet(user_id))
            await loop.run_in_executor(None, lambda: ws.append_row([name, transaction, day, ""]))
            await update.message.reply_text(
                f"✅ Added: *{name}* will auto-log on day *{day}* of each month.",
                parse_mode='Markdown'
            )
            return

        if subcommand == 'delete':
            if len(args) < 2:
                await update.message.reply_text("Usage: `/recurring delete <name>`", parse_mode='Markdown')
                return
            name = args[1].lower()
            ws = await loop.run_in_executor(None, lambda: self._get_recurring_sheet(user_id))
            items = await loop.run_in_executor(None, lambda: self._load_recurring(ws))
            for item in items:
                if item['name'].lower() == name:
                    await loop.run_in_executor(None, lambda: ws.delete_rows(item['row_index']))
                    await update.message.reply_text(f"✅ Deleted recurring: *{item['name']}*.", parse_mode='Markdown')
                    return
            await update.message.reply_text(f"❌ No recurring transaction named `{args[1]}`.", parse_mode='Markdown')

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

                    if len(rows) > 1:
                        await loop.run_in_executor(None, lambda: self._insert_rows(sheet, rows))
                    else:
                        await loop.run_in_executor(None, lambda: self._insert_row(sheet, rows[0]))

                    # Mark as logged today
                    await loop.run_in_executor(None, lambda: ws.update_cell(item['row_index'], 4, today_str))

                    row = rows[0]
                    await context.bot.send_message(
                        chat_id=user_id,
                        text=(
                            f"🔄 *Recurring logged:* _{item['name']}_\n"
                            f"  `{item['transaction']}`"
                        ),
                        parse_mode='Markdown'
                    )
                    logger.info(f"Logged recurring '{item['name']}' for user {user_id}")

            except Exception as e:
                logger.error(f"Recurring job error for user {user_id_str}: {e}")

    # ── Goals ─────────────────────────────────────────────────────────────────

    async def setgoal_cmd(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """/setgoal <name> <amount> — create or update a savings goal."""
        user_id = update.message.from_user.id
        if not self.get_user_sheet(user_id):
            await update.message.reply_text("❌ Unauthorized.")
            return
        if len(context.args) < 2:
            await update.message.reply_text(
                "Usage: `/setgoal <name> <amount>`\n"
                "Example: `/setgoal vacation 2000`",
                parse_mode='Markdown'
            )
            return
        name = context.args[0]
        try:
            target = float(context.args[1].replace(',', ''))
        except ValueError:
            await update.message.reply_text(f"❌ `{context.args[1]}` is not a valid amount.", parse_mode='Markdown')
            return

        loop = asyncio.get_running_loop()
        ws = await loop.run_in_executor(None, lambda: self._get_goals_sheet(user_id))
        goals = await loop.run_in_executor(None, lambda: self._load_goals(ws))

        for goal in goals:
            if goal['name'].lower() == name.lower():
                await loop.run_in_executor(None, lambda: ws.update_cell(goal['row_index'], 2, target))
                await update.message.reply_text(
                    f"✅ Updated goal *{goal['name']}*: target set to `${target:,.2f}`.",
                    parse_mode='Markdown'
                )
                return

        await loop.run_in_executor(None, lambda: ws.append_row([name, target, 0]))
        await update.message.reply_text(
            f"🎯 Goal created: *{name}* — `${target:,.2f}`\n"
            f"Use `/addtogoal {name} <amount>` to track your progress.",
            parse_mode='Markdown'
        )

    async def addtogoal_cmd(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """/addtogoal <name> <amount> — add savings to a goal."""
        user_id = update.message.from_user.id
        if not self.get_user_sheet(user_id):
            await update.message.reply_text("❌ Unauthorized.")
            return
        if len(context.args) < 2:
            await update.message.reply_text(
                "Usage: `/addtogoal <name> <amount>`\n"
                "Example: `/addtogoal vacation 200`",
                parse_mode='Markdown'
            )
            return
        name = context.args[0]
        try:
            amount = float(context.args[1].replace(',', ''))
        except ValueError:
            await update.message.reply_text(f"❌ `{context.args[1]}` is not a valid amount.", parse_mode='Markdown')
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
                done = "🎉 *Goal reached!*" if new_saved >= goal['target'] else ""
                await update.message.reply_text(
                    f"✅ Added `${amount:,.2f}` to *{goal['name']}*\n"
                    f"`{bar}` {pct:.1f}%\n"
                    f"Saved: `${new_saved:,.2f}` / `${goal['target']:,.2f}`\n{done}",
                    parse_mode='Markdown'
                )
                return

        await update.message.reply_text(
            f"❌ No goal named `{name}`. Create one with `/setgoal {name} <amount>`.",
            parse_mode='Markdown'
        )

    async def goals_cmd(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """/goals — show all savings goals with progress."""
        user_id = update.message.from_user.id
        if not self.get_user_sheet(user_id):
            await update.message.reply_text("❌ Unauthorized.")
            return

        loop = asyncio.get_running_loop()
        ws = await loop.run_in_executor(None, lambda: self._get_goals_sheet(user_id))
        goals = await loop.run_in_executor(None, lambda: self._load_goals(ws))

        if not goals:
            await update.message.reply_text(
                "No goals yet. Create one: `/setgoal vacation 2000`",
                parse_mode='Markdown'
            )
            return

        lines = ["🎯 *Your Goals:*\n"]
        for goal in goals:
            pct = min((goal['saved'] / goal['target']) * 100, 100) if goal['target'] > 0 else 0
            bar = "█" * int(pct // 10) + "░" * (10 - int(pct // 10))
            remaining = max(goal['target'] - goal['saved'], 0)
            status = "✅ Done!" if goal['saved'] >= goal['target'] else f"`${remaining:,.2f}` to go"
            lines.append(
                f"*{goal['name']}*\n"
                f"`{bar}` {pct:.1f}%\n"
                f"  `${goal['saved']:,.2f}` / `${goal['target']:,.2f}` — {status}\n"
            )
        await update.message.reply_text("\n".join(lines), parse_mode='Markdown')

    async def start(self):
        await self.application.initialize()
        await self.application.start()
        # Schedule recurring transaction check daily at 9:00am
        if self.application.job_queue:
            self.application.job_queue.run_daily(
                self._recurring_job,
                time=dt_time(9, 0, 0)
            )
        else:
            logger.warning("JobQueue not available — recurring transactions will not run. Install python-telegram-bot[job-queue].")
        await self.application.updater.start_polling()
        logger.info("FinanceBot started polling.")

    async def stop(self):
        await self.application.updater.stop()
        await self.application.stop()
        await self.application.shutdown()
