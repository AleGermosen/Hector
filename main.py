import logging
import gspread
from oauth2client.service_account import ServiceAccountCredentials
from telegram import Update
from telegram.ext import ApplicationBuilder, ContextTypes, MessageHandler, CommandHandler, filters
from datetime import datetime
import os
import json
import asyncio

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
# BOT 2: [NAME TBD] (PLACEHOLDER)
# ==================================================================================================
class SecondBot:
    """
    Bot 2: Placeholder for the new bot.
    This bot can share the 'google_client' or 'user_mapping' if needed.
    """
    def __init__(self, token, google_client, shared_data=None):
        self.token = token
        self.client = google_client
        self.shared_data = shared_data
        
        self.application = ApplicationBuilder().token(self.token).build()
        
        # Register Handlers
        self.application.add_handler(CommandHandler('start', self.start_cmd))
        # Add more handlers here...

    async def start_cmd(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        await update.message.reply_text("Hello from Bot 2!")

    async def start(self):
        await self.application.initialize()
        await self.application.start()
        await self.application.updater.start_polling()
        logger.info("SecondBot started polling.")

    async def stop(self):
        await self.application.updater.stop()
        await self.application.stop()
        await self.application.shutdown()


# ==================================================================================================
# MAIN EXECUTION
# ==================================================================================================
async def main():
    # 1. Load Environment Variables
    finance_token = os.environ.get('TELEGRAM_BOT_TOKEN')
    second_bot_token = os.environ.get('SECOND_BOT_TOKEN') # Placeholder env var
    creds_json = os.environ.get('GSPREAD_CREDENTIALS')
    mapping_str = os.environ.get('USER_SHEET_MAPPING', '{}')

    # 2. Initialize Shared Resources
    try:
        user_mapping = json.loads(mapping_str)
    except:
        logger.error("USER_SHEET_MAPPING is not valid JSON")
        user_mapping = {}

    google_client = None
    if creds_json:
        google_client = get_google_client(creds_json)
    else:
        logger.warning("GSPREAD_CREDENTIALS not found. Google Sheets features will fail.")

    bots = []

    # 3. Initialize FinanceBot
    if finance_token:
        finance_bot = FinanceBot(finance_token, google_client, user_mapping)
        bots.append(finance_bot)
    else:
        logger.error("TELEGRAM_BOT_TOKEN missing. FinanceBot will not run.")

    # 4. Initialize SecondBot (Uncomment when you have a token)
    if second_bot_token:
       second_bot = SecondBot(second_bot_token, google_client, shared_data=user_mapping)
       bots.append(second_bot)
    else:
       logger.info("SECOND_BOT_TOKEN missing. SecondBot is disabled.")

    # 5. Run Bots
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
        for bot in bots:
            await bot.stop()

if __name__ == '__main__':
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass
