
# ==============================================================================
# SOURCE: utils/sheets.py
# ==============================================================================
import json
import logging
import gspread
from oauth2client.service_account import ServiceAccountCredentials

logger = logging.getLogger(__name__)

GOOGLE_SCOPES = ["https://spreadsheets.google.com/feeds", "https://www.googleapis.com/auth/drive"]


def get_google_client(creds_json):
    if not creds_json:
        logger.warning("Credentials JSON is empty. Cannot create Google client.")
        return None
    try:
        creds_dict = json.loads(creds_json)
        creds = ServiceAccountCredentials.from_json_keyfile_dict(creds_dict, GOOGLE_SCOPES)
        return gspread.authorize(creds)
    except Exception as e:
        logger.error(f"Failed to authorize Google Client: {e}")
        return None

# ==============================================================================
# SOURCE: finance/bot.py
# ==============================================================================
import asyncio
import re
import io
import calendar
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
from datetime import datetime, time as dt_time

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    ApplicationBuilder, ContextTypes, MessageHandler, CommandHandler,
    filters, ConversationHandler, CallbackQueryHandler
)

logger = logging.getLogger(__name__)

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
        - target_month + target_year: filter to one month
        - year_only=True + target_year: filter to whole year (used by /ytd)
        - neither: all-time totals
        """
        target_year = target_year or datetime.now().year
        summary = {
            'total_income': 0.0,
            'total_expenses': 0.0,
            'categories': {'Needs': 0.0, 'Wants': 0.0, 'Savings': 0.0, 'Debt': 0.0},
            'other_expenses': 0.0
        }

        start_index = 1 if len(records) > 0 and (records[0][0].lower() == 'date' or records[0][0] == '') else 0

        for row in records[start_index:]:
            if len(row) < 4:
                continue
            try:
                if target_month or year_only:
                    row_date = self._parse_date_robust(row[0])
                    if row_date is None:
                        continue
                    if year_only and row_date.year != target_year:
                        continue
                    if target_month and (row_date.month != target_month or row_date.year != target_year):
                        continue

                trans_type = row[1].strip().capitalize()
                amount = self._parse_amount_robust(row[3])
                category = row[4].strip().capitalize() if len(row) > 4 else ""

                if category == 'Transfer' or (trans_type == 'Income' and category == 'Savings'):
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
        target_year = target_year or datetime.now().year
        start_index = 1 if len(records) > 0 and (records[0][0].lower() == 'date' or records[0][0] == '') else 0
        expenses = []
        for row in records[start_index:]:
            if len(row) < 4:
                continue
            try:
                if target_month:
                    row_date = self._parse_date_robust(row[0])
                    if row_date is None or row_date.month != target_month or row_date.year != target_year:
                        continue
                trans_type = row[1].strip().capitalize()
                category = row[4].strip().capitalize() if len(row) > 4 else ""
                if trans_type != 'Expense' or category == 'Transfer':
                    continue
                amount = self._parse_amount_robust(row[3])
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
        target_year = target_year or datetime.now().year
        now = datetime.now()
        monthly_totals = {}
        start_index = 1 if records and (records[0][0].lower() == 'date' or records[0][0] == '') else 0
        for row in records[start_index:]:
            if len(row) < 5:
                continue
            try:
                row_date = self._parse_date_robust(row[0])
                if not row_date:
                    continue
                # Only look at past months, not the current in-progress month
                if row_date.year == now.year and row_date.month == now.month:
                    continue
                if row[1].strip().capitalize() != 'Expense':
                    continue
                row_cat = row[4].strip().capitalize()
                if row_cat.lower() != category.lower():
                    continue
                key = (row_date.year, row_date.month)
                amount = self._parse_amount_robust(row[3])
                monthly_totals[key] = monthly_totals.get(key, 0.0) + amount
            except Exception:
                continue
        if not monthly_totals:
            return 0.0
        return sum(monthly_totals.values()) / len(monthly_totals)

    def _get_known_accounts(self, records):
        """Returns a sorted list of distinct account names from sheet history."""
        accounts = set()
        start_index = 1 if records and (records[0][0].lower() == 'date' or records[0][0] == '') else 0
        for row in records[start_index:]:
            if len(row) >= 3:
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
        """Returns a dict of account -> balance from raw sheet records."""
        balances = {}
        start_index = 1 if len(records) > 0 and (records[0][0].lower() == 'date' or records[0][0] == '') else 0
        for row in records[start_index:]:
            if len(row) < 4:
                continue
            try:
                trans_type = row[1].strip().capitalize()
                account = row[2].strip().capitalize()
                amount = self._parse_amount_robust(row[3])
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
            amount = self._parse_amount_robust(amount_str)
            date = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
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

        try:
            amount = float(parts[2].replace(',', ''))
        except ValueError:
            return None, None, (
                f"❌ `{parts[2]}` is not a valid amount. Use a number like `50` or `1500.99`."
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

        date = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
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
            budget_pct = {'Needs': 0.5, 'Wants': 0.3, 'Savings': 0.2, 'Debt': 0.2}
            now = datetime.now()
            if trans_type == 'Expense' and category in budget_pct:
                try:
                    s = self._get_data_summary(records, now.month, now.year)
                    if s['total_income'] > 0:
                        budget_limit = s['total_income'] * budget_pct[category]
                        cat_key = 'Savings' if category == 'Debt' else category
                        spent = s['categories'].get(cat_key, 0.0)
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
            "/balance - Current account balances.\n\n"
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
        target_year = datetime.now().year
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
        target_year = datetime.now().year
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
        target_year = datetime.now().year
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
            actual_savings = summary['categories']['Savings'] + summary['categories']['Debt']

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

    async def summary_cmd(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Monthly summary: income, expenses, net, savings rate, category breakdown, and trends."""
        user_id = update.message.from_user.id
        sheet = self.get_user_sheet(user_id)
        if not sheet:
            await update.message.reply_text("❌ Unauthorized.")
            return

        now = datetime.now()
        target_year = now.year

        if context.args:
            target_month = self._parse_month(context.args[0])
            if not target_month:
                await update.message.reply_text(
                    f"❌ `{context.args[0]}` is not a valid month. Try `/summary august`.",
                    parse_mode='Markdown'
                )
                return
        else:
            target_month = now.month

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
            if target_month == now.month and target_year == now.year and curr['total_expenses'] > 0:
                days_in_month = calendar.monthrange(now.year, now.month)[1]
                projected = self._spend_forecast(curr['total_expenses'], now.day, days_in_month)
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

        now = datetime.now()
        target_year = now.year

        if context.args:
            target_month = self._parse_month(context.args[0])
            if not target_month:
                await update.message.reply_text(
                    f"❌ `{context.args[0]}` is not a valid month. Try `/top august`.",
                    parse_mode='Markdown'
                )
                return
        else:
            target_month = now.month

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

        year = datetime.now().year

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

        now = datetime.now()
        month_name = now.strftime("%B %Y")

        try:
            loop = asyncio.get_running_loop()
            records = await loop.run_in_executor(None, sheet.get_all_values)

            balances = self._get_balance_by_account(records)
            s = self._get_data_summary(records, now.month, now.year)
            prev_month, prev_year = self._previous_month(now.month, now.year)
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
                    budgets = {
                        'Needs': (s['categories']['Needs'], s['total_income'] * 0.5),
                        'Wants': (s['categories']['Wants'], s['total_income'] * 0.3),
                        'Savings/Debt': (
                            s['categories']['Savings'] + s['categories']['Debt'],
                            s['total_income'] * 0.2
                        ),
                    }
                    for label, (actual, budget) in budgets.items():
                        if actual > budget:
                            status = f"⚠️ over by `${actual - budget:,.2f}`"
                        else:
                            status = f"✅ `${budget - actual:,.2f}` left"
                        lines.append(f"  {label}: {status}")

                # Spend forecast
                days_in_month = calendar.monthrange(now.year, now.month)[1]
                day = now.day
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
        today = datetime.now()
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

# ==============================================================================
# SOURCE: production/bot.py
# ==============================================================================
from datetime import datetime


logger = logging.getLogger(__name__)

(
    SELECT_CATEGORY,
    SELECT_PRODUCT,
    PRODUCT_NAME,
    TOTAL_GALLONS_INPUT,
    CONFIRM_RECIPE,
    BATCH_CODE,
    INGREDIENT_NAME,
    INGREDIENT_AMOUNT_UNIT,
    MORE_INGREDIENTS,
    TOTAL_GALLONS,
    WEIGHED_BY,
    RECEIVED_BY,
    ADD_STOCK_SELECT_TYPE,
    ADD_STOCK_SELECT_KNOWN_INGREDIENT,
    ADD_STOCK_ENTER_CUSTOM_NAME,
    ADD_STOCK_ENTER_AMOUNT_UNIT,
    ADD_STOCK_CONFIRM
) = range(17)


class ProductionBot:
    def __init__(self, token, google_client, production_sheet_id):
        self.token = token
        self.client = google_client
        self.production_sheet_id = production_sheet_id

        self.product_categories = {}
        self.recipes = {}
        self.known_ingredients = {}

        try:
            self._load_recipes_from_sheet()
        except Exception as e:
            logger.error(f"Initial recipe load failed for ProductionBot: {e}")

        self.known_ingredients = self._get_unique_ingredients_with_units()

        self.application = ApplicationBuilder().token(self.token).build()
        self._register_handlers()

    def _load_recipes_from_sheet(self):
        if not self.client or not self.production_sheet_id:
            logger.warning("ProductionBot: Google Client or Sheet ID missing. Skipping recipe load.")
            return

        try:
            sheet = self.client.open_by_key(self.production_sheet_id).sheet1
            records = sheet.get_all_records()

            for row in records:
                cat = row.get('Category')
                prod = row.get('Product')
                if not cat or not prod:
                    continue

                try:
                    bg = float(row.get('Base Gallons', 0))
                    amount = float(row.get('Amount', 0))
                except (ValueError, TypeError):
                    continue

                if cat not in self.product_categories:
                    self.product_categories[cat] = {}

                if prod not in self.product_categories[cat]:
                    self.product_categories[cat][prod] = {
                        'base_gallons': bg,
                        'ingredients': []
                    }

                self.product_categories[cat][prod]['ingredients'].append({
                    'name': row.get('Ingredient', 'Unknown'),
                    'amount': amount,
                    'unit': row.get('Unit', '')
                })

            self.recipes = {}
            for category, products in self.product_categories.items():
                self.recipes.update(products)

            logger.info(f"Successfully loaded {len(self.recipes)} recipes from Google Sheets.")
        except Exception as e:
            logger.error(f"Error loading recipes from Google Sheets: {e}")

    def _get_unique_ingredients_with_units(self):
        unique_ingredients = {}
        if not self.recipes:
            return unique_ingredients
        for product_name, recipe_data in self.recipes.items():
            for ingredient in recipe_data.get('ingredients', []):
                name = ingredient['name'].strip().lower()
                unit = ingredient['unit'].strip().lower()
                if name not in unique_ingredients:
                    unique_ingredients[name] = unit
        return unique_ingredients

    def _get_or_create_worksheet(self, spreadsheet, title):
        try:
            return spreadsheet.worksheet(title)
        except gspread.WorksheetNotFound:
            return spreadsheet.add_worksheet(title=title, rows="100", cols="20")

    def _insert_production_rows(self, sheet, rows_data):
        existing_data = sheet.get_all_values()
        if not existing_data:
            headers = ["Date", "Product Name", "Batch Code", "Ingredient Name", "Amount", "Unit", "Total Gallons", "Weighed By", "Received By"]
            sheet.insert_row(headers, index=1)
            next_row_index = 2
        else:
            next_row_index = len(existing_data) + 1
        sheet.insert_rows(rows_data, row=next_row_index, value_input_option='USER_ENTERED')

    def _update_inventory(self, spreadsheet, ingredients, subtract=False):
        """Updates inventory quantities. Returns list of (name, qty, unit) for low-stock items."""
        low_stock = []
        try:
            try:
                inventory_sheet = spreadsheet.worksheet("Inventory")
            except gspread.WorksheetNotFound:
                inventory_sheet = spreadsheet.add_worksheet(title="Inventory", rows="100", cols="4")
                inventory_sheet.insert_row(["Ingredient", "Quantity", "Unit", "Min Stock"], index=1)

            data = inventory_sheet.get_all_values()
            if not data or not data[0] or data[0][0].lower() != 'ingredient':
                headers = ["Ingredient", "Quantity", "Unit", "Min Stock"]
                inventory_sheet.clear()
                inventory_sheet.insert_row(headers, index=1)
                data = [headers]

            headers_lower = [h.lower() for h in data[0]]
            ing_col_idx = headers_lower.index("ingredient") if "ingredient" in headers_lower else 0
            qty_col_idx = headers_lower.index("quantity") if "quantity" in headers_lower else 1
            unit_col_idx = headers_lower.index("unit") if "unit" in headers_lower else 2
            min_col_idx = headers_lower.index("min stock") if "min stock" in headers_lower else -1

            for ing in ingredients:
                name = ing['name'].strip().lower()
                amount = ing['amount']
                if subtract:
                    amount = -amount

                row_idx_in_sheet = -1
                for i, row in enumerate(data[1:], start=2):
                    if len(row) > ing_col_idx and row[ing_col_idx].strip().lower() == name:
                        row_idx_in_sheet = i
                        break

                if row_idx_in_sheet != -1:
                    current_val = data[row_idx_in_sheet - 1][qty_col_idx]
                    try:
                        current_qty = float(current_val.replace(',', '')) if current_val else 0.0
                    except ValueError:
                        current_qty = 0.0
                    new_qty = current_qty + amount
                    inventory_sheet.update_cell(row_idx_in_sheet, qty_col_idx + 1, new_qty)
                    data[row_idx_in_sheet - 1][qty_col_idx] = str(new_qty)

                    # Check low-stock threshold
                    if subtract and min_col_idx != -1:
                        row_data = data[row_idx_in_sheet - 1]
                        if len(row_data) > min_col_idx and row_data[min_col_idx]:
                            try:
                                min_qty = float(str(row_data[min_col_idx]).replace(',', ''))
                                unit = row_data[unit_col_idx] if len(row_data) > unit_col_idx else ''
                                if new_qty <= min_qty:
                                    low_stock.append((ing['name'], new_qty, unit, min_qty))
                            except ValueError:
                                pass
                else:
                    new_row = [""] * max(len(headers_lower), 4)
                    new_row[ing_col_idx] = ing['name']
                    new_row[qty_col_idx] = amount
                    new_row[unit_col_idx] = ing['unit']
                    inventory_sheet.append_row(new_row)
                    data.append(new_row)
        except Exception as e:
            logger.error(f"Error updating inventory: {e}")
        return low_stock

    def _get_inventory_thresholds_hint(self):
        return (
            "\n\n💡 _Tip: Add a 'Min Stock' column to your Inventory sheet "
            "to get low-stock alerts after production runs._"
        )

    def _register_handlers(self):
        self.application.add_handler(CommandHandler('start', self.start_cmd))
        self.application.add_handler(CommandHandler('help', self.help_cmd))
        self.application.add_handler(CommandHandler('cancel', self.cancel_global))
        self.application.add_handler(CommandHandler("reload", self.reload_recipes))
        self.application.add_handler(CommandHandler('check_sheet', self.check_sheet_cmd))
        self.application.add_handler(CommandHandler('inventory', self.inventory_cmd))
        self.application.add_handler(CommandHandler('addinv', self.add_inventory_cmd))

        conv_handler = ConversationHandler(
            entry_points=[
                CommandHandler('newlog', self.start_newlog),
                CommandHandler('knownproduct', self.start_known_product),
                CommandHandler('nl', self.start_newlog),
                CommandHandler('kp', self.start_known_product)
            ],
            states={
                SELECT_CATEGORY: [CallbackQueryHandler(self.select_category_callback)],
                SELECT_PRODUCT: [CallbackQueryHandler(self.select_product_callback)],
                PRODUCT_NAME: [MessageHandler(filters.TEXT & ~filters.COMMAND, self.get_product_name)],
                TOTAL_GALLONS_INPUT: [MessageHandler(filters.TEXT & ~filters.COMMAND, self.calculate_recipe)],
                CONFIRM_RECIPE: [CallbackQueryHandler(self.confirm_recipe_callback)],
                BATCH_CODE: [MessageHandler(filters.TEXT & ~filters.COMMAND, self.get_batch_code)],
                INGREDIENT_NAME: [MessageHandler(filters.TEXT & ~filters.COMMAND, self.get_ingredient_name)],
                INGREDIENT_AMOUNT_UNIT: [MessageHandler(filters.TEXT & ~filters.COMMAND, self.get_ingredient_amount_unit)],
                MORE_INGREDIENTS: [CallbackQueryHandler(self.more_ingredients_callback)],
                TOTAL_GALLONS: [MessageHandler(filters.TEXT & ~filters.COMMAND, self.get_total_gallons)],
                WEIGHED_BY: [MessageHandler(filters.TEXT & ~filters.COMMAND, self.get_weighed_by)],
                RECEIVED_BY: [MessageHandler(filters.TEXT & ~filters.COMMAND, self.get_received_by)],
            },
            fallbacks=[
                CommandHandler('cancel', self.cancel),
                CommandHandler('help', self.help_cmd)
            ],
            allow_reentry=True
        )

        add_stock_conv_handler = ConversationHandler(
            entry_points=[CommandHandler('addstock', self.start_add_stock)],
            states={
                ADD_STOCK_SELECT_TYPE: [CallbackQueryHandler(self.select_ingredient_type_callback)],
                ADD_STOCK_SELECT_KNOWN_INGREDIENT: [CallbackQueryHandler(self.select_known_ingredient_callback)],
                ADD_STOCK_ENTER_CUSTOM_NAME: [MessageHandler(filters.TEXT & ~filters.COMMAND, self.get_custom_ingredient_name)],
                ADD_STOCK_ENTER_AMOUNT_UNIT: [MessageHandler(filters.TEXT & ~filters.COMMAND, self.get_ingredient_amount_for_stock)],
                ADD_STOCK_CONFIRM: [CallbackQueryHandler(self.confirm_add_stock_callback)]
            },
            fallbacks=[
                CommandHandler('cancel', self.cancel),
                CommandHandler('help', self.help_cmd)
            ],
            allow_reentry=True
        )

        self.application.add_handler(conv_handler)
        self.application.add_handler(add_stock_conv_handler)

    async def start_cmd(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        await update.message.reply_text(
            "Welcome to the Production Bot! I can help you log production runs and track inventory.\n\n"
            "Use /newlog or /knownproduct to start a run, or /inventory to see stock levels.\n"
            "Type /help to see all available commands."
        )

    async def help_cmd(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        try:
            logger.info(f"ProductionBot: Help command triggered by user {update.effective_user.id}")
            help_text = (
                "🚀 **Production Bot Help:**\n\n"
                "**Logging Production:**\n"
                "/newlog (or /nl) - Start a log by typing product name.\n"
                "/knownproduct (or /kp) - Select product from a list.\n"
                "/cancel - Cancel the current logging process.\n\n"
                "**Inventory Management:**\n"
                "/inventory - View current ingredient stock levels.\n"
                "/addstock - Add stock to an ingredient.\n\n"
                "**System Commands:**\n"
                "/reload - Refresh recipes from Google Sheets.\n"
                "/check_sheet - Check connection to Google Sheets.\n"
                "/help - Show this help message."
            )
            if update.effective_message:
                await update.effective_message.reply_text(help_text, parse_mode='Markdown')
            else:
                await context.bot.send_message(chat_id=update.effective_chat.id, text=help_text, parse_mode='Markdown')
        except Exception as e:
            logger.error(f"ProductionBot Error in help_cmd: {e}")
            error_details = f"❌ **Error in /help command:**\n`{str(e)}`"
            try:
                if update.effective_message:
                    await update.effective_message.reply_text(error_details, parse_mode='Markdown')
                else:
                    await context.bot.send_message(chat_id=update.effective_chat.id, text=error_details, parse_mode='Markdown')
            except:
                pass

    async def cancel_global(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        await update.message.reply_text("No active process to cancel. Use /newlog to start one.")

    async def reload_recipes(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        await update.message.reply_text("🔄 Refreshing recipes from Google Sheets...")
        try:
            self._load_recipes_from_sheet()
            self.known_ingredients = self._get_unique_ingredients_with_units()
            await update.message.reply_text(f"✅ Success! Loaded {len(self.recipes)} recipes.")
        except Exception as e:
            logger.error(f"Error reloading recipes: {e}")
            await update.message.reply_text(f"❌ Error reloading: {e}")

    async def check_sheet_cmd(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        status_msg = []

        if self.client:
            try:
                email = "Unknown"
                if hasattr(self.client.auth, 'service_account_email'):
                    email = self.client.auth.service_account_email
                elif hasattr(self.client.auth, 'signer_email'):
                    email = self.client.auth.signer_email
                status_msg.append(f"🤖 **Bot Email**: `{email}`")
            except Exception as e:
                status_msg.append(f"✅ Client: Initialized (Email fetch error: {e})")
        else:
            status_msg.append("❌ Client: Not Initialized")

        if self.production_sheet_id:
            clean_id = self.production_sheet_id.strip()
            status_msg.append(f"🆔 **Target Sheet ID**: `{clean_id}`")
        else:
            status_msg.append("❌ Sheet ID: Not Set")

        if self.client:
            try:
                loop = asyncio.get_running_loop()
                status_msg.append("\n🔍 **Scanning accessible sheets...**")
                try:
                    spreadsheets = await loop.run_in_executor(None, self.client.openall)
                    if spreadsheets:
                        titles = [s.title for s in spreadsheets]
                        status_msg.append(f"📚 Found {len(spreadsheets)} sheets: {', '.join(titles)}")
                    else:
                        status_msg.append("📭 No sheets found. (Did you share the sheet with the Bot Email?)")
                except Exception as e:
                    status_msg.append(f"❌ List Error: {str(e)}")

                if self.production_sheet_id:
                    status_msg.append(f"\n🎯 **Testing Target Connection...**")
                    spreadsheet = await loop.run_in_executor(None, lambda: self.client.open_by_key(self.production_sheet_id))
                    status_msg.append(f"✅ Success! Connected to spreadsheet: '{spreadsheet.title}'")
            except Exception as e:
                status_msg.append(f"❌ Connection Failed: {str(e)}")

        await update.message.reply_text("\n".join(status_msg), parse_mode='Markdown')

    async def inventory_cmd(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        if not self.client or not self.production_sheet_id:
            await update.message.reply_text("❌ Error: Production sheet details are not configured.")
            return

        try:
            loop = asyncio.get_running_loop()
            spreadsheet = await loop.run_in_executor(None, lambda: self.client.open_by_key(self.production_sheet_id))
            try:
                inventory_sheet = await loop.run_in_executor(None, lambda: spreadsheet.worksheet("Inventory"))
            except gspread.WorksheetNotFound:
                await update.message.reply_text("📦 Inventory sheet not found.")
                return

            records = await loop.run_in_executor(None, inventory_sheet.get_all_records)
            if not records:
                await update.message.reply_text("📦 Inventory is currently empty.")
                return

            response = "📦 **Current Inventory:**\n\n"
            for row in records:
                name = row.get('Ingredient') or row.get('ingredient') or "Unknown"
                qty = row.get('Quantity') or row.get('quantity') or 0
                unit = row.get('Unit') or row.get('unit') or ""
                response += f"- **{name}**: {qty} {unit}\n"
            await update.message.reply_text(response, parse_mode='Markdown')
        except Exception as e:
            logger.error(f"Error fetching inventory: {e}")
            await update.message.reply_text(f"❌ Error fetching inventory: {str(e)}")

    async def add_inventory_cmd(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        await update.message.reply_text("Please use /addstock to add items to inventory.")

    async def start_newlog(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        context.user_data['log_data'] = {
            'date': datetime.now().strftime("%Y/%m/%d"),
            'ingredients': []
        }
        await update.message.reply_text("Starting a new production log. What is the **Product Name**? (e.g., Desifectante lavanda)")
        return PRODUCT_NAME

    async def start_known_product(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        context.user_data['log_data'] = {
            'date': datetime.now().strftime("%Y/%m/%d"),
            'ingredients': []
        }

        categories = list(self.product_categories.keys())
        if not categories:
            await update.message.reply_text("No known product categories found.")
            return ConversationHandler.END

        keyboard = [[InlineKeyboardButton(name, callback_data=name)] for name in categories]
        reply_markup = InlineKeyboardMarkup(keyboard)

        await update.message.reply_text("Please select a category:", reply_markup=reply_markup)
        return SELECT_CATEGORY

    async def select_category_callback(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        query = update.callback_query
        await query.answer()

        category = query.data
        context.user_data['selected_category'] = category

        products = self.product_categories.get(category, {})
        if not products:
            await query.edit_message_text(f"No products found in **{category}**.", parse_mode='Markdown')
            return ConversationHandler.END

        keyboard = [[InlineKeyboardButton(name.title(), callback_data=name)] for name in products.keys()]
        reply_markup = InlineKeyboardMarkup(keyboard)

        await query.edit_message_text(f"Category: **{category}**\nPlease select a product:", reply_markup=reply_markup, parse_mode='Markdown')
        return SELECT_PRODUCT

    async def select_product_callback(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        query = update.callback_query
        await query.answer()

        product_name = query.data
        context.user_data['log_data']['product_name'] = product_name

        await query.edit_message_text(f"Selected **{product_name.title()}**. How many **gallons** are you producing?", parse_mode='Markdown')
        return TOTAL_GALLONS_INPUT

    async def get_product_name(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        product_name = update.message.text.strip()
        context.user_data['log_data']['product_name'] = product_name

        recipe = self.recipes.get(product_name.lower())

        if recipe:
            await update.message.reply_text(f"Found recipe for **{product_name}**. How many **gallons** are you producing?")
            return TOTAL_GALLONS_INPUT
        else:
            await update.message.reply_text(
                f"I don't have a recipe for **{product_name}**. Proceeding with manual entry.\n"
                f"What is the **Batch Code**?"
            )
            return BATCH_CODE

    async def calculate_recipe(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        try:
            total_gallons = float(update.message.text.strip())
        except ValueError:
            await update.message.reply_text("Please enter a valid number for gallons.")
            return TOTAL_GALLONS_INPUT

        product_name = context.user_data['log_data']['product_name']
        recipe = self.recipes.get(product_name.lower())

        if not recipe:
            await update.message.reply_text("Error: Recipe lost. Please restart /newlog.")
            return ConversationHandler.END

        base_gallons = recipe['base_gallons']
        ratio = total_gallons / base_gallons

        calculated_ingredients = []
        msg = f"🧪 **Recipe Calculation for {total_gallons} gallons of {product_name}:**\n\n"

        for ing in recipe['ingredients']:
            new_amount = ing['amount'] * ratio
            calculated_ingredients.append({
                'name': ing['name'],
                'amount': round(new_amount, 3),
                'unit': ing['unit']
            })
            msg += f"- **{ing['name']}**: {new_amount:.3f} {ing['unit']}\n"

        context.user_data['log_data']['total_gallons'] = total_gallons
        context.user_data['log_data']['ingredients'] = calculated_ingredients

        msg += "\nIs this correct?"

        keyboard = [
            [InlineKeyboardButton("✅ Yes, proceed", callback_data='confirm_yes')],
            [InlineKeyboardButton("❌ No, cancel", callback_data='confirm_no')]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)

        await update.message.reply_text(msg, reply_markup=reply_markup, parse_mode='Markdown')
        return CONFIRM_RECIPE

    async def confirm_recipe_callback(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        query = update.callback_query
        await query.answer()

        if query.data == 'confirm_yes':
            await query.edit_message_text("✅ Recipe confirmed. What is the **Batch Code**?")
            return BATCH_CODE
        else:
            await query.edit_message_text("❌ Production log cancelled.")
            context.user_data.pop('log_data', None)
            return ConversationHandler.END

    async def get_batch_code(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        context.user_data['log_data']['batch_code'] = update.message.text.strip()

        if context.user_data['log_data']['ingredients']:
            await update.message.reply_text("Batch code recorded. Who **weighed** the production?")
            return WEIGHED_BY
        else:
            await update.message.reply_text("Batch code recorded. Now, what is the **first ingredient**? (e.g., Water)")
            return INGREDIENT_NAME

    async def get_ingredient_name(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        current_ingredient_name = update.message.text.strip()
        context.user_data['current_ingredient'] = {'name': current_ingredient_name}
        await update.message.reply_text(
            f"How much **{current_ingredient_name}** was used? (e.g., 500 kg or 100 lbs)"
        )
        return INGREDIENT_AMOUNT_UNIT

    async def get_ingredient_amount_unit(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        text = update.message.text.strip()
        match = re.match(r'(\d+(\.\d+)?)\s*(kg|lbs|gallons|liters|g|ml)', text, re.IGNORECASE)
        if match:
            amount = float(match.group(1))
            unit = match.group(3).lower()
            context.user_data['current_ingredient']['amount'] = amount
            context.user_data['current_ingredient']['unit'] = unit
            context.user_data['log_data']['ingredients'].append(context.user_data['current_ingredient'])
            context.user_data.pop('current_ingredient')

            keyboard = [[InlineKeyboardButton("Yes, add another", callback_data='add_more_ingredients')],
                        [InlineKeyboardButton("No, I'm done with ingredients", callback_data='no_more_ingredients')]]
            reply_markup = InlineKeyboardMarkup(keyboard)
            await update.message.reply_text("Ingredient added. Add more ingredients?", reply_markup=reply_markup)
            return MORE_INGREDIENTS
        else:
            await update.message.reply_text(
                "Invalid format. Please provide amount and unit (e.g., 500 kg or 100 lbs)."
            )
            return INGREDIENT_AMOUNT_UNIT

    async def more_ingredients_callback(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        query = update.callback_query
        await query.answer()

        if query.data == 'add_more_ingredients':
            await query.edit_message_text("Okay, what is the **next ingredient**? (e.g., Surfactant X)")
            return INGREDIENT_NAME
        elif query.data == 'no_more_ingredients':
            await query.edit_message_text("No more ingredients. What is the **Total Gallons Produced** for this batch?")
            return TOTAL_GALLONS

    async def get_total_gallons(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        try:
            total_gallons = float(update.message.text.strip())
            context.user_data['log_data']['total_gallons'] = total_gallons
            await update.message.reply_text("Total gallons recorded. Who **weighed** the production?")
            return WEIGHED_BY
        except ValueError:
            await update.message.reply_text("Please enter a valid number for total gallons.")
            return TOTAL_GALLONS

    async def get_weighed_by(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        context.user_data['log_data']['weighed_by'] = update.message.text.strip()
        await update.message.reply_text("Weighed by recorded. Who **received** the production?")
        return RECEIVED_BY

    async def get_received_by(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        context.user_data['log_data']['received_by'] = update.message.text.strip()

        await update.message.reply_text("All details collected. Logging to Google Sheet...")

        if not self.client or not self.production_sheet_id:
            await update.message.reply_text("❌ Error: Production sheet details are not configured. Log not saved.")
            context.user_data.pop('log_data', None)
            return ConversationHandler.END

        log_data = context.user_data['log_data']
        date = log_data['date']
        product_name = log_data['product_name']
        batch_code = log_data['batch_code']
        total_gallons = log_data['total_gallons']
        weighed_by = log_data['weighed_by']
        received_by = log_data['received_by']

        sanitized_product_name = re.sub(r'[^\w\s-]', '', product_name)
        worksheet_title = f"{date} - {sanitized_product_name}"

        try:
            loop = asyncio.get_running_loop()
            spreadsheet = await loop.run_in_executor(None, lambda: self.client.open_by_key(self.production_sheet_id))
            sheet = await loop.run_in_executor(None, lambda: self._get_or_create_worksheet(spreadsheet, worksheet_title))

            rows_to_insert = []
            if not log_data['ingredients']:
                rows_to_insert.append([
                    date, product_name, batch_code, "", "", "",
                    total_gallons, weighed_by, received_by
                ])
            else:
                for ingredient in log_data['ingredients']:
                    rows_to_insert.append([
                        date, product_name, batch_code,
                        ingredient['name'], ingredient['amount'], ingredient['unit'],
                        total_gallons, weighed_by, received_by
                    ])

            await loop.run_in_executor(None, lambda: self._insert_production_rows(sheet, rows_to_insert))

            low_stock = []
            if log_data['ingredients']:
                low_stock = await loop.run_in_executor(
                    None, lambda: self._update_inventory(spreadsheet, log_data['ingredients'], subtract=True)
                )

            msg = f"✅ Production log saved: *'{worksheet_title}'*"
            if low_stock:
                msg += "\n\n⚠️ *Low stock alert:*"
                for name, qty, unit, minimum in low_stock:
                    msg += f"\n  • *{name}*: `{qty} {unit}` (min: {minimum} {unit})"
            await update.message.reply_text(msg, parse_mode='Markdown')
        except Exception as e:
            logger.error(f"Error saving production log to sheet: {e}")
            await update.message.reply_text(f"❌ Error saving production log: {str(e)}")

        context.user_data.pop('log_data', None)
        return ConversationHandler.END

    async def start_add_stock(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        context.user_data['add_stock_data'] = {}
        keyboard = [
            [InlineKeyboardButton("Select Known Ingredient", callback_data='known_ingredient')],
            [InlineKeyboardButton("Add New/Custom Ingredient", callback_data='custom_ingredient')]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        await update.message.reply_text("How would you like to add stock?", reply_markup=reply_markup)
        return ADD_STOCK_SELECT_TYPE

    async def select_ingredient_type_callback(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        query = update.callback_query
        await query.answer()
        if query.data == 'known_ingredient':
            if not self.known_ingredients:
                await query.edit_message_text("No known ingredients found. Please add a custom one.")
                return ConversationHandler.END
            ingredient_names = sorted(list(self.known_ingredients.keys()))
            keyboard = [[InlineKeyboardButton(name.title(), callback_data=name)] for name in ingredient_names]
            reply_markup = InlineKeyboardMarkup(keyboard)
            await query.edit_message_text("Please select an ingredient:", reply_markup=reply_markup)
            return ADD_STOCK_SELECT_KNOWN_INGREDIENT
        elif query.data == 'custom_ingredient':
            await query.edit_message_text("Please enter the **name** of the new ingredient:")
            return ADD_STOCK_ENTER_CUSTOM_NAME

    async def select_known_ingredient_callback(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        query = update.callback_query
        await query.answer()
        ingredient_name = query.data
        unit = self.known_ingredients.get(ingredient_name, 'unit')
        context.user_data['add_stock_data']['name'] = ingredient_name.title()
        context.user_data['add_stock_data']['unit'] = unit
        await query.edit_message_text(f"Selected **{ingredient_name.title()}** ({unit}). Enter **amount** to add:")
        return ADD_STOCK_ENTER_AMOUNT_UNIT

    async def get_custom_ingredient_name(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        ingredient_name = update.message.text.strip().title()
        context.user_data['add_stock_data']['name'] = ingredient_name
        await update.message.reply_text(f"What is the **unit** for **{ingredient_name}**?")
        return ADD_STOCK_ENTER_AMOUNT_UNIT

    async def get_ingredient_amount_for_stock(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        text = update.message.text.strip()
        if 'unit' not in context.user_data['add_stock_data']:
            match = re.match(r'(\d+(\.\d+)?)\s*(kg|lbs|gallons|liters|g|ml|pcs|units)', text, re.IGNORECASE)
            if match:
                context.user_data['add_stock_data']['amount'] = float(match.group(1))
                context.user_data['add_stock_data']['unit'] = match.group(3).lower()
            else:
                await update.message.reply_text("Format: [amount] [unit] (e.g., 500 kg)")
                return ADD_STOCK_ENTER_AMOUNT_UNIT
        else:
            try:
                context.user_data['add_stock_data']['amount'] = float(text)
            except ValueError:
                await update.message.reply_text("Please enter a valid number.")
                return ADD_STOCK_ENTER_AMOUNT_UNIT

        name = context.user_data['add_stock_data']['name']
        amount = context.user_data['add_stock_data']['amount']
        unit = context.user_data['add_stock_data']['unit']
        msg = f"Confirm adding **{amount} {unit}** of **{name}** to inventory?"
        keyboard = [[InlineKeyboardButton("✅ Yes", callback_data='add_stock_confirm_yes')],
                    [InlineKeyboardButton("❌ No", callback_data='add_stock_confirm_no')]]
        await update.message.reply_text(msg, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode='Markdown')
        return ADD_STOCK_CONFIRM

    async def confirm_add_stock_callback(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        query = update.callback_query
        await query.answer()
        if query.data == 'add_stock_confirm_yes':
            stock_data = context.user_data['add_stock_data']
            try:
                loop = asyncio.get_running_loop()
                spreadsheet = await loop.run_in_executor(None, lambda: self.client.open_by_key(self.production_sheet_id))
                low_stock = await loop.run_in_executor(None, lambda: self._update_inventory(spreadsheet, [stock_data]))
                msg = f"✅ Added *{stock_data['amount']} {stock_data['unit']}* of *{stock_data['name']}*."
                # Adding stock shouldn't trigger low-stock (we're adding), but show if still low after add
                if low_stock:
                    msg += "\n\n⚠️ *Still below minimum stock level after addition.*"
                await query.edit_message_text(msg, parse_mode='Markdown')
            except Exception as e:
                logger.error(f"Error adding stock: {e}")
                await query.edit_message_text(f"❌ Error: {e}")
        else:
            await query.edit_message_text("❌ Cancelled.")
        context.user_data.pop('add_stock_data', None)
        return ConversationHandler.END

    async def cancel(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        context.user_data.clear()
        await update.message.reply_text("Operation cancelled.")
        return ConversationHandler.END

    async def start(self):
        await self.application.initialize()
        await self.application.start()
        await self.application.updater.start_polling()
        logger.info("ProductionBot started polling.")

    async def stop(self):
        await self.application.updater.stop()
        await self.application.stop()
        await self.application.shutdown()

# ==============================================================================
# SOURCE: heroku_entry.py
# ==============================================================================
import os


logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)


async def main():
    finance_token = os.environ.get('TELEGRAM_BOT_TOKEN')
    production_token = os.environ.get('SECOND_BOT_TOKEN')
    finance_creds_json = os.environ.get('GSPREAD_CREDENTIALS')
    production_creds_json = os.environ.get('PRODUCTION_GSPREAD_CREDENTIALS')
    production_sheet_id = os.environ.get('PRODUCTION_SHEET_ID')

    finance_google_client = get_google_client(finance_creds_json)
    production_google_client = get_google_client(production_creds_json)

    bots = []

    if finance_token:
        try:
            finance_user_mapping = json.loads(os.environ.get('USER_SHEET_MAPPING', '{}'))
            strict_users = json.loads(os.environ.get('STRICT_MODE_USERS', '[]'))
            finance_bot = FinanceBot(finance_token, finance_google_client, finance_user_mapping, strict_users)
            bots.append(finance_bot)
        except Exception as e:
            logger.error(f"Failed to initialize FinanceBot: {e}")

    if production_token:
        try:
            production_bot = ProductionBot(production_token, production_google_client, production_sheet_id)
            bots.append(production_bot)
        except Exception as e:
            logger.error(f"Failed to initialize ProductionBot: {e}")

    if not bots:
        logger.error("No bots to run. Exiting.")
        return

    for bot in bots:
        await bot.start()

    stop_signal = asyncio.Event()
    try:
        await stop_signal.wait()
    except asyncio.CancelledError:
        pass
    finally:
        logger.info("Shutting down bots...")
        for bot in bots:
            await bot.stop()


if __name__ == '__main__':
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass
