import logging
import gspread
from oauth2client.service_account import ServiceAccountCredentials
from telegram import Update
from telegram.ext import ApplicationBuilder, ContextTypes, MessageHandler, CommandHandler, filters
from datetime import datetime
import os
import json
import asyncio

# Enable logging
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)

scope = ["https://spreadsheets.google.com/feeds", "https://www.googleapis.com/auth/drive"]


class FinanceBot:
    def __init__(self, token, google_creds_json, user_mapping):
        self.token = token
        creds_dict = json.loads(google_creds_json)
        self.creds = ServiceAccountCredentials.from_json_keyfile_dict(creds_dict, scope)
        self.client = gspread.authorize(self.creds)

        # user_mapping is a dict: {"TELEGRAM_ID": "SPREADSHEET_NAME_OR_ID"}
        self.user_mapping = user_mapping

        self.application = ApplicationBuilder().token(self.token).build()

        # Add handlers
        self.application.add_handler(CommandHandler('start', self.start_cmd))
        self.application.add_handler(CommandHandler('balance', self.calculate_balance))
        self.application.add_handler(MessageHandler(filters.TEXT & (~filters.COMMAND), self.handle_finance))
        self.application.add_handler(CommandHandler('calc', self.calculator))

    def get_user_sheet(self, user_id):
        """Helper to find the right sheet for the user ID"""
        sheet_identifier = self.user_mapping.get(str(user_id))
        if not sheet_identifier:
            return None

        # This opens the sheet dynamically.
        # For better performance with many users, you could cache these objects.
        try:
            return self.client.open(sheet_identifier).sheet1
        except Exception as e:
            logging.error(f"Could not open sheet for user {user_id}: {e}")
            return None

    async def start_cmd(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        user_id = update.message.from_user.id
        if str(user_id) in self.user_mapping:
            await update.message.reply_text(f"Welcome back! Your ID is `{user_id}` and your sheet is linked.",
                                            parse_mode='Markdown')
        else:
            await update.message.reply_text(f"Hello! You are not authorized. Send your ID to the admin: `{user_id}`",
                                            parse_mode='Markdown')

    async def handle_finance(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        user_id = update.message.from_user.id
        sheet = self.get_user_sheet(user_id)

        if not sheet:
            await update.message.reply_text("❌ Error: You are not authorized or your sheet wasn't found.")
            return

        text = update.message.text
        try:
            parts = text.split(maxsplit=4)
            if len(parts) < 4: raise ValueError("Missing arguments")

            trans_type = parts[0].capitalize()
            if trans_type.lower() not in ['income', 'expense']:
                raise ValueError("Start with Income or Expense")

            account, amount, category = parts[1].capitalize(), float(parts[2]), parts[3]
            description = parts[4] if len(parts) > 4 else ""
            date = datetime.now().strftime("%m/%d/%Y %H:%M:%S")

            row_to_insert = [date, trans_type, account, amount, category, description]

            # --- NEW LOGIC TO FIND THE REAL LAST ROW ---
            loop = asyncio.get_running_loop()

            def safe_append():
                # Get all values in Column A to find the true end of the list
                col_a = sheet.col_values(1)
                next_row = len(col_a) + 1
                # Update the specific range instead of using append_row
                # This ignores charts/data in other columns
                sheet.insert_row(row_to_insert, index=next_row)

            # Change this part in your handle_finance method
            await loop.run_in_executor(None, lambda: sheet.append_row(
                [date, trans_type, account, amount, category, description],
                value_input_option='USER_ENTERED'  # This is the magic line
            ))

            await update.message.reply_text(f"✅ Logged to your sheet: {trans_type} ${amount}")
        except ValueError as e:
            await update.message.reply_text(f"❌ Usage: [Income/Expense] [Account] [Amount] [Category] [Description]")
        except Exception as e:
            logging.error(f"Sheet Error: {e}")
            await update.message.reply_text(f"❌ Sheet Error: {str(e)}")

    async def calculate_balance(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
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
                    trans_type, account, amount = row[1].capitalize(), row[2].capitalize(), float(row[3])
                except ValueError:
                    continue

                balances[account] = balances.get(account, 0.0) + (amount if trans_type == 'Income' else -amount)

            response = "💰 **Your Account Balances:**\n"
            for acc, bal in balances.items():
                response += f"{acc}: ${bal:.2f}\n"

            await update.message.reply_text(response, parse_mode='Markdown')
        except Exception as e:
            await update.message.reply_text(f"❌ Error: {str(e)}")

    async def calculator(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        expression = " ".join(context.args)
        try:
            result = eval(expression, {"__builtins__": {}}, {})  # Basic sandbox
            await update.message.reply_text(f"🔢 Result: {result}")
        except:
            await update.message.reply_text("❌ Invalid calculation.")

    async def run(self):
        await self.application.initialize()
        await self.application.start()
        await self.application.updater.start_polling()
        # Keep running
        stop_signal = asyncio.Event()
        await stop_signal.wait()


async def main():
    token = os.environ.get('TELEGRAM_BOT_TOKEN')
    creds = os.environ.get('GSPREAD_CREDENTIALS')
    # Format: {"123456": "My Sheet Name", "789012": "Friend Sheet Name"}
    mapping_str = os.environ.get('USER_SHEET_MAPPING', '{}')

    try:
        user_mapping = json.loads(mapping_str)
    except:
        logging.error("USER_SHEET_MAPPING is not valid JSON")
        user_mapping = {}

    if token and creds:
        bot = FinanceBot(token, creds, user_mapping)
        logging.info("Bot starting...")
        await bot.run()
    else:
        logging.error("Missing TELEGRAM_BOT_TOKEN or GSPREAD_CREDENTIALS")


if __name__ == '__main__':
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass