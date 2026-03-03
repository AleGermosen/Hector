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
    def __init__(self, token, google_client, user_mapping):
        self.token = token
        self.client = google_client
        # user_mapping: {"TELEGRAM_ID": "SPREADSHEET_NAME_OR_ID"}
        self.user_mapping = user_mapping
        
        # Initialize the Telegram Application
        self.application = ApplicationBuilder().token(self.token).build()

        # Register Handlers
        self._register_handlers()

    def _register_handlers(self):
        """Registers all command and message handlers for this bot."""
        self.application.add_handler(CommandHandler('start', self.start_cmd))
        self.application.add_handler(CommandHandler('balance', self.calculate_balance))
        self.application.add_handler(CommandHandler('calc', self.calculator))
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

    async def start_cmd(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Handler for /start command."""
        user_id = update.message.from_user.id
        if str(user_id) in self.user_mapping:
            await update.message.reply_text(f"Welcome back! Your ID is `{user_id}` and your sheet is linked.",
                                            parse_mode='Markdown')
        else:
            await update.message.reply_text(f"Hello! You are not authorized. Send your ID to the admin: `{user_id}`",
                                            parse_mode='Markdown')

    async def handle_finance(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Parses text messages to log income/expenses."""
        user_id = update.message.from_user.id
        sheet = self.get_user_sheet(user_id)

        if not sheet:
            await update.message.reply_text("❌ Error: You are not authorized or your sheet wasn't found.")
            return

        text = update.message.text
        try:
            # Expected format: [Type] [Account] [Amount] [Category] [Description]
            parts = text.split(maxsplit=4)
            if len(parts) < 4: raise ValueError("Missing arguments")

            trans_type = parts[0].capitalize()
            if trans_type.lower() not in ['income', 'expense']:
                raise ValueError("Start with Income or Expense")

            account, amount, category = parts[1].capitalize(), float(parts[2]), parts[3]
            description = parts[4] if len(parts) > 4 else ""

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
                    trans_type, account, amount = row[1].capitalize(), row[2].capitalize(), float(row[3])
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
PRODUCT_NAME, BATCH_CODE, INGREDIENT_NAME, INGREDIENT_AMOUNT_UNIT, MORE_INGREDIENTS, \
    TOTAL_GALLONS, WEIGHED_BY, RECEIVED_BY = range(8)

class ProductionBot:
    """
    Bot 2: Handles logging for production line activities.
    """
    def __init__(self, token, google_client, production_sheet_id):
        self.token = token
        self.client = google_client
        self.production_sheet_id = production_sheet_id
        
        self.application = ApplicationBuilder().token(self.token).build()
        self._register_handlers()

    def _register_handlers(self):
        """Registers all command and message handlers for this bot."""
        # Conversation Handler for new production log
        conv_handler = ConversationHandler(
            entry_points=[CommandHandler('newlog', self.start_newlog)],
            states={
                PRODUCT_NAME: [MessageHandler(filters.TEXT & ~filters.COMMAND, self.get_product_name)],
                BATCH_CODE: [MessageHandler(filters.TEXT & ~filters.COMMAND, self.get_batch_code)],
                INGREDIENT_NAME: [MessageHandler(filters.TEXT & ~filters.COMMAND, self.get_ingredient_name)],
                INGREDIENT_AMOUNT_UNIT: [MessageHandler(filters.TEXT & ~filters.COMMAND, self.get_ingredient_amount_unit)],
                MORE_INGREDIENTS: [CallbackQueryHandler(self.more_ingredients_callback)],
                TOTAL_GALLONS: [MessageHandler(filters.TEXT & ~filters.COMMAND, self.get_total_gallons)],
                WEIGHED_BY: [MessageHandler(filters.TEXT & ~filters.COMMAND, self.get_weighed_by)],
                RECEIVED_BY: [MessageHandler(filters.TEXT & ~filters.COMMAND, self.get_received_by)],
            },
            fallbacks=[CommandHandler('cancel', self.cancel)],
            allow_reentry=True # Allows users to restart conversation if they get stuck
        )

        self.application.add_handler(CommandHandler('start', self.start_cmd))
        self.application.add_handler(conv_handler)

    def get_production_sheet(self):
        """Helper to get the production log sheet."""
        if not self.client:
            logger.error("ProductionBot: Google client is not available.")
            return None
        if not self.production_sheet_id:
            logger.error("PRODUCTION_SHEET_ID is not set.")
            return None
        try:
            # Assuming the first worksheet is the one for logs
            return self.client.open_by_key(self.production_sheet_id).sheet1
        except Exception as e:
            logger.error(f"Could not open production sheet {self.production_sheet_id}: {e}")
            return None

    async def start_cmd(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Handler for the /start command."""
        await update.message.reply_text(
            "Welcome to the Production Bot! I can help you log production runs.\n\n"
            "Use the /newlog command to start logging a new production batch."
        )

    async def start_newlog(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Starts the conversation to log a new production run."""
        context.user_data['log_data'] = {
            'date': datetime.now().strftime("%Y-%m-%d"),
            'ingredients': []
        }
        await update.message.reply_text("Starting a new production log. What is the **Product Name**?")
        return PRODUCT_NAME

    async def get_product_name(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Stores product name and asks for batch code."""
        context.user_data['log_data']['product_name'] = update.message.text.strip()
        await update.message.reply_text("Got it. What is the **Batch Code**?")
        return BATCH_CODE

    async def get_batch_code(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Stores batch code and asks for the first ingredient."""
        context.user_data['log_data']['batch_code'] = update.message.text.strip()
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
        """Stores who weighed the production and asks for who received it."""
        context.user_data['log_data']['weighed_by'] = update.message.text.strip()
        await update.message.reply_text("Weighed by recorded. Who **received** the production?")
        return RECEIVED_BY

    async def get_received_by(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Stores who received the production and finalizes the log."""
        context.user_data['log_data']['received_by'] = update.message.text.strip()
        
        # Finalize and log to Google Sheet
        await update.message.reply_text("All details collected. Logging to Google Sheet...")
        
        sheet = self.get_production_sheet()
        if not sheet:
            await update.message.reply_text("❌ Error: Could not access the production sheet. Log not saved.")
            context.user_data.pop('log_data', None)
            return ConversationHandler.END

        log_data = context.user_data['log_data']
        date = log_data['date']
        product_name = log_data['product_name']
        batch_code = log_data['batch_code']
        total_gallons = log_data['total_gallons']
        weighed_by = log_data['weighed_by']
        received_by = log_data['received_by']

        rows_to_insert = []
        if not log_data['ingredients']:
            # If no ingredients were added, log a single row with empty ingredient fields
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
        
        try:
            loop = asyncio.get_running_loop()
            await loop.run_in_executor(None, lambda: self._insert_production_rows(sheet, rows_to_insert))
            await update.message.reply_text("✅ Production log successfully saved to Google Sheet!")
        except Exception as e:
            logger.error(f"Error saving production log to sheet: {e}")
            await update.message.reply_text(f"❌ Error saving production log: {str(e)}")
        
        context.user_data.pop('log_data', None) # Clear user data
        return ConversationHandler.END

    def _insert_production_rows(self, sheet, rows_data):
        """Helper to insert multiple rows into the production sheet (blocking)."""
        existing_data = sheet.col_values(1)
        next_row_index = len(existing_data) + 1
        sheet.insert_rows(rows_data, row=next_row_index, value_input_option='USER_ENTERED')


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

    bots = []

    # 4. Initialize FinanceBot
    if finance_token:
        if not finance_google_client:
            logger.warning("FinanceBot is enabled but GSPREAD_CREDENTIALS are missing or invalid. Google Sheets features will be disabled.")
        finance_bot = FinanceBot(finance_token, finance_google_client, finance_user_mapping)
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
