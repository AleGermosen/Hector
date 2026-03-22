import logging
import gspread
from oauth2client.service_account import ServiceAccountCredentials
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    ApplicationBuilder, ContextTypes, MessageHandler, CommandHandler,
    filters, ConversationHandler, CallbackQueryHandler
)
from datetime import datetime
import os
import json
import asyncio
import re # For ingredient parsing
import matplotlib
matplotlib.use('Agg') # Use non-interactive backend
import matplotlib.pyplot as plt
import io

# ==================================================================================================
# LOGGING CONFIGURATION
# ==================================================================================================
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# ==================================================================================================
# SHARED CONFIGURATION & RESOURCES
# ==================================================================================================
# Define the scope for Google Sheets API
GOOGLE_SCOPES = ["https://spreadsheets.google.com/feeds", "https://www.googleapis.com/auth/drive"]

def get_google_client(creds_json):
    """
    Authenticates with Google and returns the gspread client.
    This can be shared between multiple bots if they both need sheet access.
    """
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

# ==================================================================================================
# BOT 1: FINANCE TRACKER
# ==================================================================================================
class FinanceBot:
    """
    Bot 1: Handles finance tracking, logging expenses/income to Google Sheets.
    """
    def __init__(self, token, google_client, user_mapping, strict_users=None):
        self.token = token
        self.client = google_client
        # user_mapping: {"TELEGRAM_ID": "SPREADSHEET_NAME_OR_ID"}
        self.user_mapping = user_mapping
        self.strict_users = strict_users or []
        
        # Initialize the Telegram Application
        self.application = ApplicationBuilder().token(self.token).build()

        # Register Handlers
        self._register_handlers()

    def _register_handlers(self):
        """Registers all command and message handlers for this bot."""
        self.application.add_handler(CommandHandler('start', self.start_cmd))
        self.application.add_handler(CommandHandler('help', self.help_cmd))
        self.application.add_handler(CommandHandler('balance', self.calculate_balance))
        self.application.add_handler(CommandHandler('calc', self.calculator))
        self.application.add_handler(CommandHandler('expenses', self.generate_expenses_chart))
        self.application.add_handler(CommandHandler('net', self.calculate_net_worth))
        self.application.add_handler(CommandHandler('calcExpenses', self.calc_expenses_budget))
        # Handle text messages that are NOT commands (for logging transactions)
        self.application.add_handler(MessageHandler(filters.TEXT & (~filters.COMMAND), self.handle_finance))

    def get_user_sheet(self, user_id):
        """Helper to find the right sheet for the user ID."""
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
        """Parses a month name or abbreviation into a month number (1-12)."""
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
        """Tries to parse a date string using multiple common formats."""
        if not date_str:
            return None
        date_str = date_str.strip()
        
        # List of formats to try
        formats = [
            "%Y-%m-%d %H:%M:%S", # The format we upload
            "%Y-%m-%d",          # ISO Date
            "%m/%d/%Y %H:%M:%S", # US Locale
            "%m/%d/%Y",          # US Date
            "%d/%m/%Y %H:%M:%S", # EU Locale
            "%d/%m/%Y"           # EU Date
        ]
        
        for fmt in formats:
            try:
                return datetime.strptime(date_str, fmt)
            except ValueError:
                continue
        
        # Fallback: Try slicing the first 10 chars if it looks like ISO
        if len(date_str) >= 10:
            try:
                return datetime.strptime(date_str[:10], "%Y-%m-%d")
            except ValueError:
                pass
                
        return None

    def _parse_amount_robust(self, amount_str):
        """Parses amount string, handling currency symbols and commas."""
        if not amount_str:
            return 0.0
        try:
            # Remove currency symbols and commas
            clean_str = amount_str.replace('$', '').replace(',', '').strip()
            return float(clean_str)
        except ValueError:
            return 0.0

    async def start_cmd(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Handler for /start command."""
        user_id = update.message.from_user.id
        if str(user_id) in self.user_mapping:
            await update.message.reply_text(f"Welcome back! Your ID is `{user_id}` and your sheet is linked.",
                                            parse_mode='Markdown')
        else:
            await update.message.reply_text(f"Hello! You are not authorized. Send your ID to the admin: `{user_id}`",
                                            parse_mode='Markdown')

    async def help_cmd(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Displays a help message with all available commands."""
        help_text = (
            "Here are the commands you can use:\n\n"
            "**Finance Logging:**\n"
            "To log a transaction, send a message in the format:\n"
            "`[Type] [Account] [Amount] [Category] [Description]`\n"
            "Example: `Expense Cash 15.50 Needs Lunch with colleagues`\n\n"
            "**Transfers:**\n"
            "`Transfer [From] to [To] [Amount]`\n"
            "Example: `Transfer Digital to Cash 1500`\n\n"
            "**Commands:**\n"
            "/start - Welcome message and check authorization.\n"
            "/help - Shows this help message.\n"
            "/balance - Calculates and shows your current account balances.\n"
            "/expenses - Generates a pie chart of all your expenses.\n"
            "/expenses [month] - Generates a pie chart for a specific month (e.g., `/expenses january`).\n"
            "/net - Shows your net worth (Income vs Expenses).\n"
            "/calcExpenses - Calculates how much you can spend on Needs (50%) and Wants (30%) based on your net worth.\n"
            "/calc [expression] - A simple calculator (e.g., `/calc 5 * 2`)."
        )
        await update.message.reply_text(help_text, parse_mode='Markdown')

    async def handle_finance(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Parses text messages to log income/expenses or transfers."""
        user_id = update.message.from_user.id
        sheet = self.get_user_sheet(user_id)

        if not sheet:
            await update.message.reply_text("❌ Error: You are not authorized or your sheet wasn't found.")
            return

        text = update.message.text
        try:
            # Check for Transfer command
            # Regex: Transfer <From> to <To> <Amount> <Description?>
            transfer_match = re.match(r'^Transfer\s+(.+?)\s+to\s+(.+?)\s+([\d,]+(?:\.\d+)?)(?:\s+(.*))?$', text, re.IGNORECASE)
            
            if transfer_match:
                from_acc = transfer_match.group(1).strip().capitalize()
                to_acc = transfer_match.group(2).strip().capitalize()
                amount_str = transfer_match.group(3)
                desc = transfer_match.group(4) or ""
                
                amount = self._parse_amount_robust(amount_str)
                date = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                
                # Create two rows: Expense from Source, Income to Dest
                # Using "Transfer" as Category
                row_out = [date, "Expense", from_acc, amount, "Transfer", f"Transfer to {to_acc} {desc}"]
                row_in = [date, "Income", to_acc, amount, "Transfer", f"Transfer from {from_acc} {desc}"]
                
                loop = asyncio.get_running_loop()
                # Insert consecutively
                await loop.run_in_executor(None, lambda: self._insert_rows(sheet, [row_out, row_in]))
                
                await update.message.reply_text(f"✅ Transfer Logged: {from_acc} ➡️ {to_acc} ${amount}")
                return

            # Expected format: [Type] [Account] [Amount] [Category] [Description]
            parts = text.split(maxsplit=4)
            if len(parts) < 4: raise ValueError("Missing arguments")

            trans_type = parts[0].capitalize()
            if trans_type.lower() not in ['income', 'expense']:
                raise ValueError("Start with Income or Expense")

            account, amount, category = parts[1].capitalize(), float(parts[2]), parts[3]
            description = parts[4] if len(parts) > 4 else ""

            if str(user_id) in self.strict_users and trans_type == 'Expense':
                allowed_categories = ['Needs', 'Wants', 'Savings', 'Debt']
                match = next((ac for ac in allowed_categories if ac.lower() == category.lower()), None)
                if match:
                    category = match
                else:
                    await update.message.reply_text(
                        f"❌ Invalid category. Allowed: {', '.join(allowed_categories)}"
                    )
                    return

            # Use full timestamp for precision
            date = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            row_to_insert = [date, trans_type, account, amount, category, description]

            # Run blocking I/O (gspread) in a separate thread
            loop = asyncio.get_running_loop()
            await loop.run_in_executor(None, lambda: self._insert_row(sheet, row_to_insert))

            await update.message.reply_text(
                f"✅ Logged to your sheet: {trans_type} ${amount}")
        except ValueError:
            await update.message.reply_text(f"❌ Usage: [Income/Expense] [Account] [Amount] [Category] [Description]")
        except Exception as e:
            logger.error(f"Sheet Error: {e}")
            await update.message.reply_text(f"❌ Sheet Error: {str(e)}")

    def _insert_row(self, sheet, row_data):
        """Helper to insert row at the first empty line (blocking)."""
        existing_data = sheet.col_values(1)
        next_row_index = len(existing_data) + 1
        sheet.insert_row(row_data, index=next_row_index, value_input_option='USER_ENTERED')

    def _insert_rows(self, sheet, rows_data):
        """Helper to insert multiple rows (blocking)."""
        existing_data = sheet.col_values(1)
        next_row_index = len(existing_data) + 1
        # insert_rows is more efficient than calling insert_row multiple times
        sheet.insert_rows(rows_data, row=next_row_index, value_input_option='USER_ENTERED')

    async def calculate_balance(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Calculates balance based on sheet data."""
        user_id = update.message.from_user.id
        sheet = self.get_user_sheet(user_id)

        if not sheet:
            await update.message.reply_text("❌ Unauthorized.")
            return

        try:
            loop = asyncio.get_running_loop()
            records = await loop.run_in_executor(None, sheet.get_all_values)

            balances = {}
            for row in records:
                if len(row) < 4: continue
                try:
                    # Assuming columns: Date, Type, Account, Amount
                    trans_type = row[1].strip().capitalize()
                    account = row[2].strip().capitalize()
                    amount = self._parse_amount_robust(row[3])
                    
                    balances[account] = balances.get(account, 0.0) + (amount if trans_type == 'Income' else -amount)
                except ValueError:
                    continue

            response = "💰 **Your Account Balances:**\n"
            for acc, bal in balances.items():
                response += f"{acc}: ${bal:.2f}\n"

            await update.message.reply_text(response, parse_mode='Markdown')
        except Exception as e:
            await update.message.reply_text(f"❌ Error: {str(e)}")

    async def calculator(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Simple calculator command."""
        expression = " ".join(context.args)
        try:
            result = eval(expression, {"__builtins__": {}}, {})
            await update.message.reply_text(f"🔢 Result: {result}")
        except:
            await update.message.reply_text("❌ Invalid calculation.")

    async def generate_expenses_chart(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Generates a pie chart of expenses categorized by Needs, Wants, Savings, Debt."""
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
                await update.message.reply_text(f"❌ Invalid month: {arg}")
                return
            # Get month name for title
            month_name = datetime(target_year, target_month, 1).strftime("%B")
            chart_title = f"Expenses Breakdown ({month_name} {target_year})"

        try:
            loop = asyncio.get_running_loop()
            records = await loop.run_in_executor(None, sheet.get_all_values)

            categories = {'Needs': 0.0, 'Wants': 0.0, 'Savings': 0.0, 'Debt': 0.0}
            
            # Skip header row if present, assuming first row is header
            start_index = 1 if len(records) > 0 and records[0][0].lower() == 'date' else 0

            for row in records[start_index:]:
                if len(row) < 5: continue
                try:
                    # Date filtering
                    if target_month:
                        row_date = self._parse_date_robust(row[0])
                        
                        # If parsing failed, or if date doesn't match, skip row
                        if row_date is None or row_date.month != target_month or row_date.year != target_year:
                            continue

                    # Assuming columns: Date, Type, Account, Amount, Category
                    trans_type = row[1].strip().capitalize()
                    if trans_type != 'Expense':
                        continue
                        
                    amount = self._parse_amount_robust(row[3])
                    category = row[4].strip().capitalize()
                    
                    # Normalize category names
                    if category in categories:
                        categories[category] += amount
                    else:
                        # Try to match case-insensitive
                        match = next((k for k in categories.keys() if k.lower() == category.lower()), None)
                        if match:
                            categories[match] += amount
                        # Else ignore or add to 'Other' if desired, but user asked for specific categories
                except ValueError:
                    continue

            # Filter out zero values
            labels = [k for k, v in categories.items() if v > 0]
            sizes = [v for k, v in categories.items() if v > 0]
            total_expenses = sum(sizes)

            if not sizes:
                msg = "No expenses found in the specified categories"
                if target_month:
                    month_name = datetime(target_year, target_month, 1).strftime("%B")
                    msg += f" for {month_name} {target_year}"
                msg += "."
                await update.message.reply_text(msg)
                return

            # Create Pie Chart
            fig, ax = plt.subplots()
            ax.pie(sizes, labels=labels, autopct='%1.1f%%', startangle=90)
            ax.axis('equal')  # Equal aspect ratio ensures that pie is drawn as a circle.
            plt.title(chart_title)

            # Save to buffer
            buf = io.BytesIO()
            plt.savefig(buf, format='png')
            buf.seek(0)
            plt.close(fig)

            # Generate text breakdown
            breakdown_text = f"📊 **{chart_title}:**\n"
            for label, size in zip(labels, sizes):
                percentage = (size / total_expenses) * 100
                breakdown_text += f"- **{label}**: ${size:.2f} ({percentage:.1f}%)\n"
            breakdown_text += f"\n**Total Expenses**: ${total_expenses:.2f}"

            await update.message.reply_photo(photo=buf, caption=breakdown_text, parse_mode='Markdown')

        except Exception as e:
            logger.error(f"Chart Error: {e}")
            await update.message.reply_text(f"❌ Error generating chart: {str(e)}")

    async def calculate_net_worth(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Calculates and displays net worth (Income vs Expenses)."""
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
                await update.message.reply_text(f"❌ Invalid month: {arg}")
                return
            month_name = datetime(target_year, target_month, 1).strftime("%B")
            title_suffix = f"({month_name} {target_year})"

        try:
            loop = asyncio.get_running_loop()
            records = await loop.run_in_executor(None, sheet.get_all_values)

            total_income = 0.0
            total_expenses = 0.0
            
            start_index = 1 if len(records) > 0 and records[0][0].lower() == 'date' else 0

            for row in records[start_index:]:
                if len(row) < 4: continue
                try:
                    # Date filtering
                    if target_month:
                        row_date = self._parse_date_robust(row[0])
                        if row_date is None or row_date.month != target_month or row_date.year != target_year:
                            continue

                    trans_type = row[1].strip().capitalize()
                    amount = self._parse_amount_robust(row[3])
                    category = row[4].strip().capitalize() if len(row) > 4 else ""
                    
                    # Ignore transfers in Net Worth Calculation (since they are both Income and Expense)
                    if category == 'Transfer':
                        continue

                    if trans_type == 'Income':
                        total_income += amount
                    elif trans_type == 'Expense':
                        total_expenses += amount
                except ValueError:
                    continue

            if total_income == 0 and total_expenses == 0:
                await update.message.reply_text(f"No records found {title_suffix.lower()}.")
                return

            net_worth = total_income - total_expenses
            
            # Avoid division by zero
            if total_income > 0:
                expense_percentage = (total_expenses / total_income) * 100
                net_percentage = 100 - expense_percentage
            else:
                expense_percentage = 0.0
                net_percentage = 0.0

            # Create Pie Chart
            labels = ['Expenses', 'Net Worth']
            # If net worth is negative, the pie chart doesn't make sense as 'part of whole' in the same way,
            # but we can still show Expenses vs Income or just skip the chart if weird.
            # Standard approach: Show Expenses and (Income - Expenses) if positive.
            
            chart_generated = False
            buf = None

            if total_income > 0 and net_worth >= 0:
                sizes = [total_expenses, net_worth]
                colors = ['#ff9999','#66b3ff']
                
                fig, ax = plt.subplots()
                ax.pie(sizes, labels=labels, autopct='%1.1f%%', startangle=90, colors=colors)
                ax.axis('equal')
                plt.title(f'Net Worth Breakdown {title_suffix}')

                buf = io.BytesIO()
                plt.savefig(buf, format='png')
                buf.seek(0)
                plt.close(fig)
                chart_generated = True

            # Generate text breakdown
            breakdown_text = (
                f"💰 **Net Worth Summary {title_suffix}:**\n\n"
                f"- **Total Income**: ${total_income:,.2f}\n"
                f"- **Total Expenses**: ${total_expenses:,.2f}"
            )
            
            if total_income > 0:
                breakdown_text += f" ({expense_percentage:.1f}% of income)\n\n"
            else:
                 breakdown_text += "\n\n"

            breakdown_text += f"**Net Worth**: **${net_worth:,.2f}**"
            
            if total_income > 0:
                 breakdown_text += f" ({net_percentage:.1f}% of income remaining)"

            if chart_generated:
                await update.message.reply_photo(photo=buf, caption=breakdown_text, parse_mode='Markdown')
            else:
                await update.message.reply_text(breakdown_text, parse_mode='Markdown')

        except Exception as e:
            logger.error(f"Net Worth Error: {e}")
            await update.message.reply_text(f"❌ Error calculating net worth: {str(e)}")

    async def calc_expenses_budget(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Calculates 50% for Needs and 30% for Wants based on total income and tracks spending."""
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
                await update.message.reply_text(f"❌ Invalid month: {arg}")
                return
            month_name = datetime(target_year, target_month, 1).strftime("%B")
            title_suffix = f"({month_name} {target_year})"

        try:
            loop = asyncio.get_running_loop()
            records = await loop.run_in_executor(None, sheet.get_all_values)

            total_income = 0.0
            actual_needs = 0.0
            actual_wants = 0.0
            actual_savings = 0.0
            
            start_index = 1 if len(records) > 0 and records[0][0].lower() == 'date' else 0

            for row in records[start_index:]:
                if len(row) < 4: continue
                try:
                    # Date filtering
                    if target_month:
                        row_date = self._parse_date_robust(row[0])
                        if row_date is None or row_date.month != target_month or row_date.year != target_year:
                            continue

                    trans_type = row[1].strip().capitalize()
                    amount = self._parse_amount_robust(row[3])
                    category = row[4].strip().capitalize() if len(row) > 4 else "Other"
                    
                    # Ignore transfers in Budget Calculation
                    if category == 'Transfer':
                        continue

                    if trans_type == 'Income':
                        total_income += amount
                    elif trans_type == 'Expense':
                        # Check if category exists
                        category = row[4].strip().capitalize() if len(row) > 4 else "Other"

                        if category == 'Needs':
                            actual_needs += amount
                        elif category == 'Wants':
                            actual_wants += amount
                        elif category in ['Savings', 'Debt']:
                            actual_savings += amount
                except ValueError:
                    continue

            if total_income <= 0:
                await update.message.reply_text(f"No income recorded {title_suffix.lower()}. Cannot calculate budget.")
                return

            budget_needs = total_income * 0.5
            budget_wants = total_income * 0.3
            budget_savings = total_income * 0.2

            def get_status(actual, budget):
                if actual > budget:
                    return f"⚠️ **OVER** by ${actual - budget:,.2f}"
                else:
                    return f"✅ Left: ${budget - actual:,.2f}"

            response = (
                f"💰 **Budget Status {title_suffix}**\n"
                f"(vs Income ${total_income:,.2f}):\n\n"
                f"🏠 **Needs (50%)**: ${budget_needs:,.2f}\n"
                f"   Spent: ${actual_needs:,.2f} -> {get_status(actual_needs, budget_needs)}\n\n"
                f"🎉 **Wants (30%)**: ${budget_wants:,.2f}\n"
                f"   Spent: ${actual_wants:,.2f} -> {get_status(actual_wants, budget_wants)}\n\n"
                f"📈 **Savings/Debt (20%)**: ${budget_savings:,.2f}\n"
                f"   Spent: ${actual_savings:,.2f} -> {get_status(actual_savings, budget_savings)}"
            )

            await update.message.reply_text(response, parse_mode='Markdown')

        except Exception as e:
            logger.error(f"Calc Expenses Error: {e}")
            await update.message.reply_text(f"❌ Error: {str(e)}")

    async def start(self):
        """Starts the bot polling."""
        await self.application.initialize()
        await self.application.start()
        await self.application.updater.start_polling()
        logger.info("FinanceBot started polling.")

    async def stop(self):
        """Stops the bot."""
        await self.application.updater.stop()
        await self.application.stop()
        await self.application.shutdown()


# ==================================================================================================
# BOT 2: PRODUCTION BOT
# ==================================================================================================
# Conversation states for ProductionBot
(
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
    RECEIVED_BY
) = range(11)

class ProductionBot:
    """
    Bot 2: Handles logging for production line activities and inventory tracking.
    """
    def __init__(self, token, google_client, production_sheet_id):
        self.token = token
        self.client = google_client
        self.production_sheet_id = production_sheet_id
        
        # Define base recipes for products (per 55 gallons)
        # You can expand this dictionary as needed.
        self.recipes = {
            'desinfectante lavanda': {
                'base_gallons': 55.0,
                'ingredients': [
                    {'name': 'Fragancia', 'amount': 1.3, 'unit': 'kg'},
                    {'name': 'Amonio 80%', 'amount': 0.5, 'unit': 'kg'},
                    {'name': 'Nonyl phenol', 'amount': 1.5, 'unit': 'kg'},
                    {'name': 'Formol', 'amount': 0.05, 'unit': 'kg'},
                    {'name': 'Color', 'amount': 120, 'unit': 'ml'}
                ]
            },
            'desinfectante bebe': {
                'base_gallons': 55.0,
                'ingredients': [
                    {'name': 'Fragancia', 'amount': 1.3, 'unit': 'kg'},
                    {'name': 'Amonio 80%', 'amount': 0.5, 'unit': 'kg'},
                    {'name': 'Nonyl phenol', 'amount': 1.5, 'unit': 'kg'},
                    {'name': 'Formol', 'amount': 0.05, 'unit': 'kg'},
                    {'name': 'Color', 'amount': 50, 'unit': 'ml'}
                ]
            },
            'desinfectante floral': {
                'base_gallons': 55.0,
                'ingredients': [
                    {'name': 'Fragancia', 'amount': 1.3, 'unit': 'kg'},
                    {'name': 'Amonio 80%', 'amount': 0.5, 'unit': 'kg'},
                    {'name': 'Nonyl phenol', 'amount': 1.5, 'unit': 'kg'},
                    {'name': 'Formol', 'amount': 0.05, 'unit': 'kg'},
                    {'name': 'Color', 'amount': 160, 'unit': 'ml'}
                ]
            },
            'desinfectante frescor marino': {
                'base_gallons': 55.0,
                'ingredients': [
                    {'name': 'Fragancia', 'amount': 1.3, 'unit': 'kg'},
                    {'name': 'Amonio 80%', 'amount': 0.5, 'unit': 'kg'},
                    {'name': 'Nonyl phenol', 'amount': 1.5, 'unit': 'kg'},
                    {'name': 'Formol', 'amount': 0.05, 'unit': 'kg'},
                    {'name': 'Color', 'amount': 120, 'unit': 'ml'}
                ]
            },
            'desinfectante strawberry': {
                'base_gallons': 55.0,
                'ingredients': [
                    {'name': 'Fragancia', 'amount': 1.3, 'unit': 'kg'},
                    {'name': 'Amonio 80%', 'amount': 0.5, 'unit': 'kg'},
                    {'name': 'Nonyl phenol', 'amount': 1.5, 'unit': 'kg'},
                    {'name': 'Formol', 'amount': 0.05, 'unit': 'kg'},
                    {'name': 'Color', 'amount': 120, 'unit': 'ml'}
                ]
            },
            'desinfectante pino': {
                'base_gallons': 55.0,
                'ingredients': [
                    {'name': 'Fragancia', 'amount': 1.3, 'unit': 'kg'},
                    {'name': 'Amonio 80%', 'amount': 0.5, 'unit': 'kg'},
                    {'name': 'Nonyl phenol', 'amount': 1.5, 'unit': 'kg'},
                    {'name': 'Formol', 'amount': 0.05, 'unit': 'kg'},
                    {'name': 'Color', 'amount': 120, 'unit': 'ml'}
                ]
            },
            'desinfectante canela': {
                'base_gallons': 55.0,
                'ingredients': [
                    {'name': 'Fragancia', 'amount': 1.3, 'unit': 'kg'},
                    {'name': 'Amonio 80%', 'amount': 0.5, 'unit': 'kg'},
                    {'name': 'Nonyl phenol', 'amount': 1.5, 'unit': 'kg'},
                    {'name': 'Formol', 'amount': 0.05, 'unit': 'kg'},
                    {'name': 'Canela', 'amount': 100, 'unit': 'ml'},
                    {'name': 'Amarillo', 'amount': 10, 'unit': 'ml'}
                ]
            },
            'desinfectante antitabaco': {
                'base_gallons': 55.0,
                'ingredients': [
                    {'name': 'Alcohol isopropilico', 'amount': 4, 'unit': 'kg'},
                    {'name': 'Amonio 80%', 'amount': 0.5, 'unit': 'kg'},
                    {'name': 'Nonyl phenol', 'amount': 0.8, 'unit': 'kg'},
                    {'name': 'Fragancia prasenta', 'amount': 1.5, 'unit': 'kg'},
                    {'name': 'Amarillo', 'amount': 150, 'unit': 'ml'}
                ]
            }
            # Add more products here
        }

        self.application = ApplicationBuilder().token(self.token).build()
        self._register_handlers()

    def _register_handlers(self):
        """Registers all command and message handlers for this bot."""
        # Conversation Handler for new production log
        conv_handler = ConversationHandler(
            entry_points=[
                CommandHandler('nl', self.start_newlog),
                CommandHandler('kp', self.start_known_product)
            ],
            states={
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
            fallbacks=[CommandHandler('cancel', self.cancel)],
            allow_reentry=True 
        )

        self.application.add_handler(CommandHandler('start', self.start_cmd))
        self.application.add_handler(CommandHandler('help', self.help_cmd))
        self.application.add_handler(CommandHandler('check_sheet', self.check_sheet_cmd))
        self.application.add_handler(CommandHandler('inventory', self.inventory_cmd))
        self.application.add_handler(CommandHandler('addinv', self.add_inventory_cmd))
        
        self.application.add_handler(conv_handler)
        # Add a global /cancel command that works everywhere for this bot
        self.application.add_handler(CommandHandler('cancel', self.cancel_global))

    async def cancel_global(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Global cancel command handler."""
        # This will be handled if not inside a conversation (or if fallbacks fail)
        await update.message.reply_text("No active command to cancel.")

    async def help_cmd(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Displays help for the Production Bot."""
        help_text = (
            "🚀 **Production Bot Help:**\n\n"
            "/newlog - Start a new production batch log by typing the name.\n"
            "/knownproduct - Start a log by selecting from a list of products.\n"
            "/inventory - View current ingredient stock levels.\n"
            "/addinv [Item] [Amount] [Unit] - Add stock to inventory.\n"
            "/check_sheet - Check connection to Google Sheets.\n"
            "/cancel - Cancel the current logging process.\n"
            "/help - Show this help message."
        )
        await update.message.reply_text(help_text, parse_mode='Markdown')

    async def check_sheet_cmd(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Checks connectivity to the Google Sheet with detailed diagnostics."""
        status_msg = []
        
        # 1. Check Client and Email
        if self.client:
            try:
                # Try to get email from credentials
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
            
        # 2. Check Sheet ID format
        if self.production_sheet_id:
            clean_id = self.production_sheet_id.strip()
            status_msg.append(f"🆔 **Target Sheet ID**: `{clean_id}`")
            if len(self.production_sheet_id) != len(clean_id):
                 status_msg.append("⚠️ **WARNING**: ID has leading/trailing whitespace!")
        else:
            status_msg.append("❌ Sheet ID: Not Set")
            
        # 3. Connectivity Tests
        if self.client:
            try:
                loop = asyncio.get_running_loop()
                
                # Test A: List all accessible sheets
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

                # Test B: Open specific sheet
                if self.production_sheet_id:
                    status_msg.append(f"\n🎯 **Testing Target Connection...**")
                    spreadsheet = await loop.run_in_executor(None, lambda: self.client.open_by_key(self.production_sheet_id))
                    status_msg.append(f"✅ Success! Connected to spreadsheet: '{spreadsheet.title}'")
                    
            except Exception as e:
                status_msg.append(f"❌ Connection Failed: {str(e)}")
        
        await update.message.reply_text("\n".join(status_msg), parse_mode='Markdown')

    def _get_or_create_worksheet(self, spreadsheet, title):
        """Gets a worksheet by title, creating it if it doesn't exist."""
        try:
            return spreadsheet.worksheet(title)
        except gspread.WorksheetNotFound:
            return spreadsheet.add_worksheet(title=title, rows="100", cols="20")

    async def start_cmd(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Handler for the /start command."""
        await update.message.reply_text(
            "Welcome to the Production Bot! I can help you log production runs and track inventory.\n\n"
            "Use /newlog or /knownproduct to start a run, or /inventory to see stock levels."
        )

    async def start_newlog(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Starts the conversation to log a new production run by typing name."""
        context.user_data['log_data'] = {
            'date': datetime.now().strftime("%Y/%m/%d"),
            'ingredients': []
        }
        await update.message.reply_text("Starting a new production log. What is the **Product Name**? (e.g., Desifectante lavanda)")
        return PRODUCT_NAME

    async def start_known_product(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Starts the known product selection flow."""
        context.user_data['log_data'] = {
            'date': datetime.now().strftime("%Y/%m/%d"),
            'ingredients': []
        }
        
        recipes = self.recipes.keys()
        if not recipes:
            await update.message.reply_text("No known product recipes found.")
            return ConversationHandler.END

        keyboard = [[InlineKeyboardButton(name.title(), callback_data=name)] for name in recipes]
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        await update.message.reply_text("Please select a product:", reply_markup=reply_markup)
        return SELECT_PRODUCT

    async def select_product_callback(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Handles product selection from the inline keyboard."""
        query = update.callback_query
        await query.answer()
        
        product_name = query.data
        context.user_data['log_data']['product_name'] = product_name
        
        await query.edit_message_text(f"Selected **{product_name.title()}**. How many **gallons** are you producing?", parse_mode='Markdown')
        return TOTAL_GALLONS_INPUT

    async def get_product_name(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Stores product name and checks if recipe exists."""
        product_name = update.message.text.strip()
        context.user_data['log_data']['product_name'] = product_name
        
        # Check if we have a recipe for this product (case-insensitive)
        recipe = self.recipes.get(product_name.lower())
        
        if recipe:
            await update.message.reply_text(f"Found recipe for **{product_name}**. How many **gallons** are you producing?")
            return TOTAL_GALLONS_INPUT
        else:
            # Fallback to manual entry if no recipe found
            await update.message.reply_text(
                f"I don't have a recipe for **{product_name}**. Proceeding with manual entry.\n"
                f"What is the **Batch Code**?"
            )
            return BATCH_CODE

    async def calculate_recipe(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Calculates ingredient amounts based on total gallons."""
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
        """Handles confirmation of the calculated recipe."""
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
        """Stores batch code."""
        context.user_data['log_data']['batch_code'] = update.message.text.strip()
        
        # Determine next step based on whether we have ingredients already (from recipe)
        if context.user_data['log_data']['ingredients']:
             # Recipe path: skip ingredient manual entry, go straight to who weighed
            await update.message.reply_text("Batch code recorded. Who **weighed** the production?")
            return WEIGHED_BY
        else:
             # Manual path: ask for ingredients
            await update.message.reply_text("Batch code recorded. Now, what is the **first ingredient**? (e.g., Water)")
            return INGREDIENT_NAME

    async def get_ingredient_name(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Stores ingredient name and asks for its amount and unit."""
        current_ingredient_name = update.message.text.strip()
        context.user_data['current_ingredient'] = {'name': current_ingredient_name}
        await update.message.reply_text(
            f"How much **{current_ingredient_name}** was used? (e.g., 500 kg or 100 lbs)"
        )
        return INGREDIENT_AMOUNT_UNIT

    async def get_ingredient_amount_unit(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Stores ingredient amount and unit, then asks if there are more ingredients."""
        text = update.message.text.strip()
        match = re.match(r'(\d+(\.\d+)?)\s*(kg|lbs|gallons|liters|g|ml)', text, re.IGNORECASE)
        if match:
            amount = float(match.group(1))
            unit = match.group(3).lower()
            context.user_data['current_ingredient']['amount'] = amount
            context.user_data['current_ingredient']['unit'] = unit
            context.user_data['log_data']['ingredients'].append(context.user_data['current_ingredient'])
            context.user_data.pop('current_ingredient') # Clear current ingredient

            keyboard = [[InlineKeyboardButton("Yes, add another", callback_data='add_more_ingredients')],
                        [InlineKeyboardButton("No, I'm done with ingredients", callback_data='no_more_ingredients')]]
            reply_markup = InlineKeyboardMarkup(keyboard)
            await update.message.reply_text("Ingredient added. Add more ingredients?", reply_markup=reply_markup)
            return MORE_INGREDIENTS
        else:
            await update.message.reply_text(
                "Invalid format. Please provide amount and unit (e.g., 500 kg or 100 lbs)."
            )
            return INGREDIENT_AMOUNT_UNIT # Stay in the same state

    async def more_ingredients_callback(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Handles callback for adding more ingredients or moving to next step."""
        query = update.callback_query
        await query.answer() # Acknowledge the callback query

        if query.data == 'add_more_ingredients':
            await query.edit_message_text("Okay, what is the **next ingredient**? (e.g., Surfactant X)")
            return INGREDIENT_NAME
        elif query.data == 'no_more_ingredients':
            await query.edit_message_text("No more ingredients. What is the **Total Gallons Produced** for this batch?")
            return TOTAL_GALLONS

    async def get_total_gallons(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Stores total gallons and asks for who weighed the production."""
        try:
            total_gallons = float(update.message.text.strip())
            context.user_data['log_data']['total_gallons'] = total_gallons
            await update.message.reply_text("Total gallons recorded. Who **weighed** the production?")
            return WEIGHED_BY
        except ValueError:
            await update.message.reply_text("Please enter a valid number for total gallons.")
            return TOTAL_GALLONS

    async def get_weighed_by(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Stores who weighed and asks for who received."""
        context.user_data['log_data']['weighed_by'] = update.message.text.strip()
        await update.message.reply_text("Weighed by recorded. Who **received** the production?")
        return RECEIVED_BY

    async def get_received_by(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Stores who received the production and finalizes the log."""
        context.user_data['log_data']['received_by'] = update.message.text.strip()
        
        # Finalize and log to Google Sheet
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

        # Sanitize product_name for worksheet title
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
            
            # Update Inventory
            if log_data['ingredients']:
                await loop.run_in_executor(None, lambda: self._update_inventory(spreadsheet, log_data['ingredients']))

            await update.message.reply_text(f"✅ Production log successfully saved to sheet: '{worksheet_title}'!")
        except Exception as e:
            logger.error(f"Error saving production log to sheet: {e}")
            await update.message.reply_text(f"❌ Error saving production log: {str(e)}")
        
        context.user_data.pop('log_data', None) # Clear user data
        return ConversationHandler.END

    def _insert_production_rows(self, sheet, rows_data):
        """Helper to insert multiple rows into the production sheet (blocking)."""
        # Check if sheet is empty to add headers
        existing_data = sheet.get_all_values()
        if not existing_data:
            headers = ["Date", "Product Name", "Batch Code", "Ingredient Name", "Amount", "Unit", "Total Gallons", "Weighed By", "Received By"]
            sheet.insert_row(headers, index=1)
            next_row_index = 2
        else:
            next_row_index = len(existing_data) + 1

        sheet.insert_rows(rows_data, row=next_row_index, value_input_option='USER_ENTERED')

    def _update_inventory(self, spreadsheet, ingredients):
        """Updates the 'Inventory' sheet by subtracting the used ingredients."""
        try:
            try:
                inventory_sheet = spreadsheet.worksheet("Inventory")
            except gspread.WorksheetNotFound:
                inventory_sheet = spreadsheet.add_worksheet(title="Inventory", rows="100", cols="3")
                inventory_sheet.insert_row(["Ingredient", "Quantity", "Unit"], index=1)
            
            data = inventory_sheet.get_all_values()
            if not data:
                headers = ["Ingredient", "Quantity", "Unit"]
                inventory_sheet.insert_row(headers, index=1)
                data = [headers]
            
            headers = data[0]
            try:
                ing_col = headers.index("Ingredient") + 1
                qty_col = headers.index("Quantity") + 1
            except ValueError:
                ing_col, qty_col = 1, 2

            for ing in ingredients:
                name = ing['name'].strip().lower()
                amount = ing['amount']
                
                row_idx = -1
                for i, row in enumerate(data[1:], start=2):
                    if len(row) >= ing_col and row[ing_col-1].strip().lower() == name:
                        row_idx = i
                        break
                
                if row_idx != -1:
                    current_val = data[row_idx-1][qty_col-1]
                    try:
                        current_qty = float(current_val.replace(',', '')) if current_val else 0.0
                    except ValueError:
                        current_qty = 0.0
                    new_qty = current_qty - amount
                    inventory_sheet.update_cell(row_idx, qty_col, new_qty)
                    # Update local data so consecutive identical ingredients in same batch work
                    data[row_idx-1][qty_col-1] = str(new_qty)
                else:
                    inventory_sheet.append_row([ing['name'], -amount, ing['unit']])
                    # Add to local data
                    data.append([ing['name'], str(-amount), ing['unit']])
        except Exception as e:
            logger.error(f"Error updating inventory: {e}")

    async def inventory_cmd(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Displays the current inventory levels."""
        if not self.client or not self.production_sheet_id:
            await update.message.reply_text("❌ Error: Production sheet details are not configured.")
            return

        try:
            loop = asyncio.get_running_loop()
            spreadsheet = await loop.run_in_executor(None, lambda: self.client.open_by_key(self.production_sheet_id))
            try:
                inventory_sheet = await loop.run_in_executor(None, lambda: spreadsheet.worksheet("Inventory"))
            except gspread.WorksheetNotFound:
                await update.message.reply_text("📦 Inventory sheet not found. It will be created when you log a production run.")
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
        """Adds stock to the inventory. Usage: /addinv [Ingredient] [Amount] [Unit]"""
        if not self.client or not self.production_sheet_id:
            await update.message.reply_text("❌ Error: Production sheet details are not configured.")
            return

        try:
            if len(context.args) < 3:
                await update.message.reply_text("❌ Usage: `/addinv [Ingredient] [Amount] [Unit]`\nExample: `/addinv Water 1000 liters`", parse_mode='Markdown')
                return
            
            name = context.args[0].capitalize()
            try:
                amount = float(context.args[1])
            except ValueError:
                await update.message.reply_text("❌ Invalid amount. Please provide a number.")
                return
            unit = context.args[2].lower()

            loop = asyncio.get_running_loop()
            spreadsheet = await loop.run_in_executor(None, lambda: self.client.open_by_key(self.production_sheet_id))
            
            await loop.run_in_executor(None, lambda: self._add_to_inventory(spreadsheet, name, amount, unit))
            await update.message.reply_text(f"✅ Added {amount} {unit} of **{name}** to inventory.", parse_mode='Markdown')
        except Exception as e:
            logger.error(f"Error adding to inventory: {e}")
            await update.message.reply_text(f"❌ Error: {str(e)}")

    def _add_to_inventory(self, spreadsheet, name, amount, unit):
        """Helper to add stock to inventory (blocking)."""
        try:
            try:
                inventory_sheet = spreadsheet.worksheet("Inventory")
            except gspread.WorksheetNotFound:
                inventory_sheet = spreadsheet.add_worksheet(title="Inventory", rows="100", cols="3")
                inventory_sheet.insert_row(["Ingredient", "Quantity", "Unit"], index=1)
            
            data = inventory_sheet.get_all_values()
            if not data:
                inventory_sheet.insert_row(["Ingredient", "Quantity", "Unit"], index=1)
                data = [["Ingredient", "Quantity", "Unit"]]
            
            headers = data[0]
            ing_col = headers.index("Ingredient") + 1 if "Ingredient" in headers else 1
            qty_col = headers.index("Quantity") + 1 if "Quantity" in headers else 2

            row_idx = -1
            for i, row in enumerate(data[1:], start=2):
                if len(row) >= ing_col and row[ing_col-1].strip().lower() == name.lower():
                    row_idx = i
                    break
            
            if row_idx != -1:
                current_val = data[row_idx-1][qty_col-1]
                try:
                    current_qty = float(current_val.replace(',', '')) if current_val else 0.0
                except ValueError:
                    current_qty = 0.0
                new_qty = current_qty + amount
                inventory_sheet.update_cell(row_idx, qty_col, new_qty)
            else:
                inventory_sheet.append_row([name, amount, unit])
        except Exception as e:
            logger.error(f"Error in _add_to_inventory: {e}")
            raise e

    async def cancel(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Cancels the current conversation."""
        context.user_data.pop('log_data', None) # Clear any incomplete data
        context.user_data.pop('current_ingredient', None)
        await update.message.reply_text("Production logging cancelled.")
        return ConversationHandler.END

    async def start(self):
        """Starts the bot polling."""
        await self.application.initialize()
        await self.application.start()
        await self.application.updater.start_polling()
        logger.info("ProductionBot started polling.")

    async def stop(self):
        """Stops the bot."""
        await self.application.updater.stop()
        await self.application.stop()
        await self.application.shutdown()


# ==================================================================================================
# MAIN EXECUTION
# ==================================================================================================
async def main():
    # 1. Load Environment Variables
    finance_token = os.environ.get('TELEGRAM_BOT_TOKEN')
    production_token = os.environ.get('SECOND_BOT_TOKEN')
    
    finance_creds_json = os.environ.get('GSPREAD_CREDENTIALS')
    production_creds_json = os.environ.get('PRODUCTION_GSPREAD_CREDENTIALS')

    finance_user_mapping_str = os.environ.get('USER_SHEET_MAPPING', '{}')
    strict_users_str = os.environ.get('STRICT_MODE_USERS', '[]')
    production_sheet_id = os.environ.get('PRODUCTION_SHEET_ID')

    # 2. Initialize Google Clients for each bot
    finance_google_client = get_google_client(finance_creds_json)
    production_google_client = get_google_client(production_creds_json)

    # 3. Initialize Bot-specific configurations
    try:
        finance_user_mapping = json.loads(finance_user_mapping_str)
    except:
        logger.error("USER_SHEET_MAPPING is not valid JSON")
        finance_user_mapping = {}

    try:
        strict_users = json.loads(strict_users_str)
    except:
        logger.error("STRICT_MODE_USERS is not valid JSON")
        strict_users = []

    bots = []

    # 4. Initialize FinanceBot
    if finance_token:
        if not finance_google_client:
            logger.warning("FinanceBot is enabled but GSPREAD_CREDENTIALS are missing or invalid. Google Sheets features will be disabled.")
        finance_bot = FinanceBot(finance_token, finance_google_client, finance_user_mapping, strict_users)
        bots.append(finance_bot)
    else:
        logger.error("TELEGRAM_BOT_TOKEN missing. FinanceBot will not run.")

    # 5. Initialize ProductionBot
    if production_token:
        if not production_google_client:
            logger.warning("ProductionBot is enabled but PRODUCTION_GSPREAD_CREDENTIALS are missing or invalid. Google Sheets features will be disabled.")
        if not production_sheet_id:
            logger.warning("ProductionBot is enabled but PRODUCTION_SHEET_ID is missing. Google Sheets features will be disabled.")
        
        production_bot = ProductionBot(production_token, production_google_client, production_sheet_id)
        bots.append(production_bot)
    else:
       logger.info("SECOND_BOT_TOKEN missing. ProductionBot is disabled.")

    # 6. Run Bots
    if not bots:
        logger.error("No bots to run. Exiting.")
        return

    # Start all bots
    for bot in bots:
        await bot.start()

    # Keep the main process alive
    stop_signal = asyncio.Event()
    try:
        await stop_signal.wait()
    except asyncio.CancelledError:
        pass
    finally:
        # Graceful shutdown
        logger.info("Shutting down bots...")
        for bot in bots:
            await bot.stop()

if __name__ == '__main__':
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.info("Shutdown signal received.")
        pass
