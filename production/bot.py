import logging
import asyncio
import re
from datetime import datetime

import gspread
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    ApplicationBuilder, ContextTypes, MessageHandler, CommandHandler,
    filters, ConversationHandler, CallbackQueryHandler
)

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
