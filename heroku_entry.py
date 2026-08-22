import os
import json
import asyncio
import logging

from utils.sheets import get_google_client
from finance.bot import FinanceBot
from production.bot import ProductionBot

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
