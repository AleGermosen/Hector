import logging
import signal
import sys
import gspread
import os
import json
import asyncio

from finance.bot import FinanceBot
from production.bot import ProductionBot

logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)


def get_google_client(creds_json):
    """Authenticate with Google and return a gspread client (google-auth)."""
    if not creds_json:
        logger.warning("Credentials JSON is empty. Cannot create Google client.")
        return None
    try:
        creds_dict = json.loads(creds_json)
        return gspread.service_account_from_dict(creds_dict)
    except Exception as e:
        logger.error(f"Failed to authorize Google Client: {e}")
        return None


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

    stop_signal = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGTERM, signal.SIGINT):
        try:
            loop.add_signal_handler(sig, stop_signal.set)
        except NotImplementedError:
            pass  # Windows / restricted environments

    for bot in bots:
        await bot.start()

    # Run each bot as a supervised task; exit non-zero if any dies unexpectedly
    tasks = {asyncio.create_task(stop_signal.wait(), name="stop")}
    for bot in bots:
        tasks.add(asyncio.create_task(_poll_forever(bot), name=type(bot).__name__))

    try:
        done, pending = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
        for task in done:
            name = task.get_name()
            if name != "stop":
                exc = task.exception()
                logger.critical(f"Bot task '{name}' exited unexpectedly: {exc}")
                stop_signal.set()
    except asyncio.CancelledError:
        pass
    finally:
        for task in tasks:
            task.cancel()
        logger.info("Shutting down bots...")
        for bot in bots:
            try:
                await bot.stop()
            except Exception as e:
                logger.error(f"Error stopping bot: {e}")

    if any(t.get_name() != "stop" and t.done() and not t.cancelled() for t in tasks):
        sys.exit(1)


async def _poll_forever(bot):
    """Keep a bot polling; the task ends only when the updater stops."""
    while bot.application.updater.running:
        await asyncio.sleep(5)
    logger.warning(f"{type(bot).__name__} updater stopped unexpectedly.")


if __name__ == '__main__':
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass
