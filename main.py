import logging
import gspread
from oauth2client.service_account import ServiceAccountCredentials
from telegram import Update
from telegram.ext import ApplicationBuilder, ContextTypes, MessageHandler, CommandHandler, filters
from datetime import datetime
import os
import json

# Enable logging
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)

# 1. Setup Google Sheets using Environment Variables
scope = ["https://spreadsheets.google.com/feeds", "https://www.googleapis.com/auth/drive"]

# Get the credentials from the environment variable
gspread_creds_json = os.environ.get('GSPREAD_CREDENTIALS')
if not gspread_creds_json:
    raise ValueError("The GSPREAD_CREDENTIALS environment variable is not set.")

# Parse the JSON string into a dictionary
creds_dict = json.loads(gspread_creds_json)

# Authorize the client
creds = ServiceAccountCredentials.from_json_keyfile_dict(creds_dict, scope)
client = gspread.authorize(creds)
sheet = client.open("Personal Finances v2").sheet1  # Make sure the name matches exactly


# 2. Financial Logic
async def handle_finance(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text
    try:
        # Parse input: Type Account Amount Category [Description]
        parts = text.split(maxsplit=4)
        
        if len(parts) < 4:
            raise ValueError("Missing arguments")

        trans_type = parts[0].capitalize()
        if trans_type.lower() not in ['income', 'expense']:
             raise ValueError("Start with Income or Expense")

        account = parts[1].capitalize()
        
        amount = float(parts[2])
        category = parts[3]
        description = parts[4] if len(parts) > 4 else ""
        
        date = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

        # Append to Google Sheet
        sheet.append_row([date, trans_type, account, amount, category, description])

        await update.message.reply_text(f"✅ Logged: {trans_type} ({account}) ${amount} - {category} ({description})")
    except ValueError:
        await update.message.reply_text("❌ Usage: [Income/Expense] [Account] [Amount] [Category] [Description]\nExample: Expense Cash 50 Medicine Cough Syrup")
    except Exception as e:
        await update.message.reply_text(f"❌ Error: {str(e)}")

async def calculate_balance(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        # Fetch all records
        records = sheet.get_all_values()
        
        balances = {}
        
        # Start from the second row if there's a header, otherwise first
        # For safety, we'll check if the amount is a valid number
        for row in records:
            if len(row) < 4: continue
            
            trans_type = row[1].capitalize()
            account = row[2].capitalize()
            try:
                amount = float(row[3])
            except ValueError:
                continue # Skip header or invalid amount row
            
            if account not in balances:
                balances[account] = 0.0
            
            if trans_type == 'Income':
                balances[account] += amount
            elif trans_type == 'Expense':
                balances[account] -= amount
                
        response = "💰 **Account Balances:**\n"
        total = 0
        for acc, bal in balances.items():
            response += f"{acc}: ${bal:.2f}\n"
            total += bal
            
        response += f"\n**Total Net Worth:** ${total:.2f}"
        
        await update.message.reply_text(response, parse_mode='Markdown')
        
    except Exception as e:
        await update.message.reply_text(f"❌ Error calculating balance: {str(e)}")

# 3. Bot Initialization
if __name__ == '__main__':
    # Get the bot token from the environment variable
    TELEGRAM_BOT_TOKEN = os.environ.get('TELEGRAM_BOT_TOKEN')
    if not TELEGRAM_BOT_TOKEN:
        raise ValueError("The TELEGRAM_BOT_TOKEN environment variable is not set.")

    application = ApplicationBuilder().token(TELEGRAM_BOT_TOKEN).build()

    # Add handlers
    balance_handler = CommandHandler('balance', calculate_balance)
    finance_handler = MessageHandler(filters.TEXT & (~filters.COMMAND), handle_finance)
    
    application.add_handler(balance_handler)
    application.add_handler(finance_handler)

    print("Bot is running...")
    application.run_polling()