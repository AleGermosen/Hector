"""
User-facing strings for FinanceBot, keyed by a short slug.

Usage:
    from finance.strings import t
    text = t("unauthorized", lang)
    text = t("balance_header", lang)

For strings with placeholders use named kwargs:
    text = t("welcome_back", lang, user_id=123)

Falls back to English when a translation is missing.
"""
from __future__ import annotations

STRINGS: dict[str, dict[str, str]] = {

    # ── Language command ──────────────────────────────────────────────────────
    "lang_set_en": {
        "en": "🇺🇸 Language set to <b>English</b>.",
        "es": "🇺🇸 Idioma cambiado a <b>inglés</b>.",
    },
    "lang_set_es": {
        "en": "🇩🇴 Idioma establecido a <b>Español</b>.",
        "es": "🇩🇴 Idioma establecido a <b>Español</b>.",
    },
    "lang_usage": {
        "en": "Usage: <code>/lang en</code> or <code>/lang es</code>",
        "es": "Uso: <code>/lang en</code> o <code>/lang es</code>",
    },

    # ── Common errors ─────────────────────────────────────────────────────────
    "unauthorized": {
        "en": "❌ Unauthorized.",
        "es": "❌ No autorizado.",
    },
    "unauthorized_detail": {
        "en": "❌ You are not authorized. Ask the admin to link your account.\nYour Telegram ID is: <code>{user_id}</code>",
        "es": "❌ No tienes autorización. Pide al administrador que vincule tu cuenta.\nTu ID de Telegram es: <code>{user_id}</code>",
    },
    "something_went_wrong": {
        "en": "😔 Something went wrong. Reference: <code>{corr_id}</code>",
        "es": "😔 Algo salió mal. Referencia: <code>{corr_id}</code>",
    },
    "sheet_error": {
        "en": "❌ Something went wrong. Ref: <code>{corr_id}</code>",
        "es": "❌ Algo salió mal. Ref: <code>{corr_id}</code>",
    },
    "could_not_reach_sheet": {
        "en": "❌ Could not reach your sheet. Try again.",
        "es": "❌ No se pudo acceder a tu hoja. Intenta de nuevo.",
    },
    "generic_error": {
        "en": "❌ Error: {detail}",
        "es": "❌ Error: {detail}",
    },
    "session_expired": {
        "en": "❌ Session expired. Please re-enter the transaction.",
        "es": "❌ Sesión expirada. Por favor ingresa la transacción de nuevo.",
    },

    # ── start / welcome ───────────────────────────────────────────────────────
    "welcome_back": {
        "en": "Welcome back! Your ID is <code>{user_id}</code> and your sheet is linked.",
        "es": "¡Bienvenido de nuevo! Tu ID es <code>{user_id}</code> y tu hoja está vinculada.",
    },
    "welcome_unauthorized": {
        "en": "Hello! You are not authorized. Send your ID to the admin: <code>{user_id}</code>",
        "es": "¡Hola! No estás autorizado. Envíale tu ID al administrador: <code>{user_id}</code>",
    },

    # ── Persistent keyboard buttons ───────────────────────────────────────────
    "btn_quick_log": {
        "en": "⚡ Quick Log",
        "es": "⚡ Registro Rápido",
    },
    "btn_guided_log": {
        "en": "📝 Guided Log",
        "es": "📝 Registro Guiado",
    },

    # ── help ──────────────────────────────────────────────────────────────────
    "help_format_header": {
        "en": "<b>Quick-log format:</b>",
        "es": "<b>Formato de registro rápido:</b>",
    },
    "help_footer": {
        "en": "<i>Every transaction shows a preview before being saved.</i>",
        "es": "<i>Cada transacción muestra una vista previa antes de guardarse.</i>",
    },
    "help_section_logging": {
        "en": "📝 Logging",
        "es": "📝 Registro",
    },
    "help_section_reports": {
        "en": "📊 Reports",
        "es": "📊 Reportes",
    },
    "help_section_goals": {
        "en": "🎯 Goals",
        "es": "🎯 Metas",
    },
    "help_section_misc": {
        "en": "⚙️ Misc",
        "es": "⚙️ Otros",
    },
    # Spanish descriptions for each command (English comes from COMMANDS list)
    "cmd_desc_log": {
        "en": "Step-by-step guided transaction entry",
        "es": "Registro guiado paso a paso",
    },
    "cmd_desc_ql": {
        "en": "Quick-log shortcuts (list / fire / add / delete)",
        "es": "Atajos de registro rápido (lista / ejecutar / añadir / eliminar)",
    },
    "cmd_desc_undo": {
        "en": "Remove the most recently logged transaction",
        "es": "Eliminar la última transacción registrada",
    },
    "cmd_desc_recent": {
        "en": "Show last N transactions (default 10)",
        "es": "Mostrar las últimas N transacciones (por defecto 10)",
    },
    "cmd_desc_recurring": {
        "en": "Manage recurring monthly transactions",
        "es": "Administrar transacciones recurrentes mensuales",
    },
    "cmd_desc_dash": {
        "en": "Full snapshot: balances, budget, savings rate",
        "es": "Vista completa: saldos, presupuesto, tasa de ahorro",
    },
    "cmd_desc_summary": {
        "en": "This month's income, expenses, net & trends",
        "es": "Ingresos, gastos, neto y tendencias del mes",
    },
    "cmd_desc_balance": {
        "en": "Current account balances",
        "es": "Saldos actuales de cuentas",
    },
    "cmd_desc_top": {
        "en": "Top 5 expenses this month",
        "es": "Los 5 mayores gastos del mes",
    },
    "cmd_desc_ytd": {
        "en": "Year-to-date totals and savings rate",
        "es": "Totales del año y tasa de ahorro",
    },
    "cmd_desc_net": {
        "en": "Net worth breakdown with chart",
        "es": "Desglose de patrimonio neto con gráfico",
    },
    "cmd_desc_expenses": {
        "en": "Expense pie chart (all time or by month)",
        "es": "Gráfico de gastos (todo el tiempo o por mes)",
    },
    "cmd_desc_calcexpenses": {
        "en": "Budget status using the 50/30/20 rule",
        "es": "Estado del presupuesto usando la regla 50/30/20",
    },
    "cmd_desc_savings": {
        "en": "Savings pot: set aside, withdrawn, and net",
        "es": "Ahorros: apartado, retirado y neto",
    },
    "cmd_desc_trend": {
        "en": "Income vs expenses line chart (default 6 months)",
        "es": "Gráfico de ingresos vs gastos (por defecto 6 meses)",
    },
    "cmd_desc_goals": {
        "en": "Show all savings goals with progress",
        "es": "Mostrar todas las metas de ahorro con progreso",
    },
    "cmd_desc_setgoal": {
        "en": "Create or update a savings goal",
        "es": "Crear o actualizar una meta de ahorro",
    },
    "cmd_desc_addtogoal": {
        "en": "Add savings to a goal",
        "es": "Añadir ahorros a una meta",
    },
    "cmd_desc_exchange": {
        "en": "USD ↔ RD$ converter",
        "es": "Conversor USD ↔ RD$",
    },
    "cmd_desc_calc": {
        "en": "Calculator — e.g. /calc 5 * 2",
        "es": "Calculadora — ej. /calc 5 * 2",
    },
    "cmd_desc_quiet": {
        "en": "Toggle monthly summary push notifications",
        "es": "Activar/desactivar resumen mensual automático",
    },
    "cmd_desc_start": {
        "en": "Check your authorization",
        "es": "Verificar tu autorización",
    },
    "cmd_desc_help": {
        "en": "Show this command list",
        "es": "Mostrar esta lista de comandos",
    },
    "cmd_desc_cancel": {
        "en": "Cancel the current operation",
        "es": "Cancelar la operación actual",
    },
    "cmd_desc_lang": {
        "en": "Switch language — /lang en or /lang es",
        "es": "Cambiar idioma — /lang en o /lang es",
    },

    # ── Transaction parsing errors ────────────────────────────────────────────
    "parse_unknown_type": {
        "en": "❌ Unknown type <code>{type}</code>. Must be <code>Income</code> or <code>Expense</code>.\n\nExample: <code>Expense Cash Needs Groceries 50</code>",
        "es": "❌ Tipo desconocido <code>{type}</code>. Debe ser <code>Income</code> (ingreso) o <code>Expense</code> (gasto).\n\nEjemplo: <code>Expense Cash Needs Groceries 50</code>",
    },
    "parse_too_few_args": {
        "en": "❌ Too few arguments.\n\nFormat: <code>[Income/Expense] [Account] [Category] [Amount]</code>\nExample: <code>Expense Cash Needs Groceries 50</code>",
        "es": "❌ Muy pocos argumentos.\n\nFormato: <code>[Income/Expense] [Cuenta] [Categoría] [Monto]</code>\nEjemplo: <code>Expense Cash Needs Groceries 50</code>",
    },
    "parse_bad_amount": {
        "en": "❌ <code>{value}</code> is not a valid amount. Use a number like <code>50</code>, <code>1,500</code>, or <code>$50</code>.",
        "es": "❌ <code>{value}</code> no es un monto válido. Usa un número como <code>50</code>, <code>1,500</code> o <code>$50</code>.",
    },
    "parse_bad_category": {
        "en": "❌ Category <code>{category}</code> is not allowed.\nAllowed: <code>{allowed}</code>",
        "es": "❌ La categoría <code>{category}</code> no está permitida.\nPermitidas: <code>{allowed}</code>",
    },

    # ── Transaction preview ───────────────────────────────────────────────────
    "preview_transfer_header": {
        "en": "💸 <b>Transfer</b>",
        "es": "💸 <b>Transferencia</b>",
    },
    "preview_from": {
        "en": "From",
        "es": "De",
    },
    "preview_to": {
        "en": "To",
        "es": "A",
    },
    "preview_amount": {
        "en": "Amount",
        "es": "Monto",
    },
    "preview_note": {
        "en": "Note",
        "es": "Nota",
    },
    "preview_account": {
        "en": "Account",
        "es": "Cuenta",
    },
    "preview_category": {
        "en": "Category",
        "es": "Categoría",
    },
    "preview_confirm_prompt": {
        "en": "Log this transaction?",
        "es": "¿Registrar esta transacción?",
    },
    "preview_savings_note": {
        "en": "_(Also auto-logs Income → Account for savings transfer)_",
        "es": "_(También registra automáticamente Ingreso → Cuenta para el traslado de ahorro)_",
    },
    "preview_anomaly_warning": {
        "en": "\n\n⚠️ <b>Heads up:</b> This is unusually high for <b>{category}</b> (your avg is <code>${avg}</code>).",
        "es": "\n\n⚠️ <b>Atención:</b> Esto es inusualmente alto para <b>{category}</b> (tu promedio es <code>${avg}</code>).",
    },

    # ── Confirm / cancel buttons ──────────────────────────────────────────────
    "btn_log_it": {
        "en": "✅ Log it",
        "es": "✅ Registrar",
    },
    "btn_cancel": {
        "en": "❌ Cancel",
        "es": "❌ Cancelar",
    },
    "btn_skip": {
        "en": "⏭ Skip",
        "es": "⏭ Omitir",
    },
    "btn_undo": {
        "en": "↩️ Undo",
        "es": "↩️ Deshacer",
    },
    "tx_cancelled": {
        "en": "❌ Transaction cancelled.",
        "es": "❌ Transacción cancelada.",
    },

    # ── Log confirmation ──────────────────────────────────────────────────────
    "logged_ok": {
        "en": "✅ <b>Logged successfully!</b>",
        "es": "✅ <b>¡Registrado exitosamente!</b>",
    },
    "logged_account_balance": {
        "en": "📊 <b>{account} balance</b>: <code>${balance}</code>",
        "es": "📊 <b>Saldo de {account}</b>: <code>${balance}</code>",
    },
    "budget_alert": {
        "en": "\n\n⚠️ <b>Budget alert:</b> You've exceeded your <b>{category}</b> budget by <code>${over}</code> this month.",
        "es": "\n\n⚠️ <b>Alerta de presupuesto:</b> Excediste tu presupuesto de <b>{category}</b> en <code>${over}</code> este mes.",
    },
    "logged_error": {
        "en": "❌ Failed to save: {detail}",
        "es": "❌ Error al guardar: {detail}",
    },

    # ── /balance ──────────────────────────────────────────────────────────────
    "balance_header": {
        "en": "💰 <b>Your Account Balances:</b>",
        "es": "💰 <b>Saldos de tus cuentas:</b>",
    },
    "balance_no_transactions": {
        "en": "No transactions found to calculate balance.",
        "es": "No se encontraron transacciones para calcular el saldo.",
    },

    # ── /calc ─────────────────────────────────────────────────────────────────
    "calc_usage": {
        "en": "Usage: /calc <expression>  e.g. <code>/calc 5 * 2</code>",
        "es": "Uso: /calc <expresión>  ej. <code>/calc 5 * 2</code>",
    },
    "calc_result": {
        "en": "🔢 Result: <code>{result}</code>",
        "es": "🔢 Resultado: <code>{result}</code>",
    },
    "calc_invalid": {
        "en": "❌ Invalid expression.",
        "es": "❌ Expresión inválida.",
    },

    # ── /exchange ─────────────────────────────────────────────────────────────
    "exchange_usage": {
        "en": "Usage:\n  <code>/exchange 100</code> — convert $100 USD → RD$\n  <code>/exchange rd 5000</code> — convert RD$5000 → USD",
        "es": "Uso:\n  <code>/exchange 100</code> — convertir $100 USD → RD$\n  <code>/exchange rd 5000</code> — convertir RD$5000 → USD",
    },
    "exchange_bad_amount": {
        "en": "❌ <code>{value}</code> is not a valid amount.",
        "es": "❌ <code>{value}</code> no es un monto válido.",
    },
    "exchange_rate_error": {
        "en": "❌ Could not fetch exchange rate. Try again in a moment.",
        "es": "❌ No se pudo obtener el tipo de cambio. Intenta en un momento.",
    },
    "exchange_usd_to_rd": {
        "en": "💱 <b>${usd} USD → RD${rd}</b>\n<i>Live rate: RD${rate} per $1 USD</i>",
        "es": "💱 <b>${usd} USD → RD${rd}</b>\n<i>Tasa en vivo: RD${rate} por $1 USD</i>",
    },
    "exchange_rd_to_usd": {
        "en": "💱 <b>RD${rd} → ${usd} USD</b>\n<i>Live rate: RD${rate} per $1 USD</i>",
        "es": "💱 <b>RD${rd} → ${usd} USD</b>\n<i>Tasa en vivo: RD${rate} por $1 USD</i>",
    },

    # ── /expenses ─────────────────────────────────────────────────────────────
    "expenses_bad_month": {
        "en": "❌ <code>{arg}</code> is not a valid month. Try <code>/expenses january</code> or <code>/expenses jan</code>.",
        "es": "❌ <code>{arg}</code> no es un mes válido. Intenta <code>/expenses enero</code> o <code>/expenses ene</code>.",
    },
    "expenses_none": {
        "en": "No expenses found{period}.",
        "es": "No se encontraron gastos{period}.",
    },
    "expenses_chart_title": {
        "en": "Expenses Breakdown{period}",
        "es": "Desglose de Gastos{period}",
    },
    "expenses_total": {
        "en": "<b>Total Expenses</b>: ${total}",
        "es": "<b>Total de Gastos</b>: ${total}",
    },
    "expenses_net": {
        "en": "<b>Net Worth</b>: <code>${net}</code>",
        "es": "<b>Patrimonio Neto</b>: <code>${net}</code>",
    },
    "expenses_other": {
        "en": "Other",
        "es": "Otros",
    },

    # ── /net ──────────────────────────────────────────────────────────────────
    "net_bad_month": {
        "en": "❌ <code>{arg}</code> is not a valid month. Try <code>/net january</code>.",
        "es": "❌ <code>{arg}</code> no es un mes válido. Intenta <code>/net enero</code>.",
    },
    "net_no_records": {
        "en": "No records found {period}.",
        "es": "No se encontraron registros {period}.",
    },
    "net_header": {
        "en": "💰 <b>Net Worth Summary {period}:</b>",
        "es": "💰 <b>Resumen de Patrimonio Neto {period}:</b>",
    },
    "net_total_income": {
        "en": "• <b>Total Income</b>:   <code>${amount}</code>",
        "es": "• <b>Total Ingresos</b>:  <code>${amount}</code>",
    },
    "net_total_expenses": {
        "en": "• <b>Total Expenses</b>: <code>${amount}</code>",
        "es": "• <b>Total Gastos</b>:   <code>${amount}</code>",
    },
    "net_pct_of_income": {
        "en": " ({pct}% of income)",
        "es": " ({pct}% de ingresos)",
    },
    "net_worth_line": {
        "en": "{emoji} <b>Net Worth</b>: <code>${amount}</code>",
        "es": "{emoji} <b>Patrimonio Neto</b>: <code>${amount}</code>",
    },
    "net_pct_remaining": {
        "en": " ({pct}% of income remaining)",
        "es": " ({pct}% de ingresos restante)",
    },
    "net_chart_label_expenses": {
        "en": "Expenses",
        "es": "Gastos",
    },
    "net_chart_label_net": {
        "en": "Net Worth",
        "es": "Patrimonio",
    },

    # ── /calcexpenses ─────────────────────────────────────────────────────────
    "calcexpenses_bad_month": {
        "en": "❌ <code>{arg}</code> is not a valid month. Try <code>/calcexpenses january</code>.",
        "es": "❌ <code>{arg}</code> no es un mes válido. Intenta <code>/calcexpenses enero</code>.",
    },
    "calcexpenses_no_income": {
        "en": "No income recorded {period}. Cannot calculate budget.",
        "es": "No se registraron ingresos {period}. No se puede calcular el presupuesto.",
    },
    "calcexpenses_header": {
        "en": "💰 <b>Budget Status {period}</b>\n  Income: <code>${income}</code>",
        "es": "💰 <b>Estado del Presupuesto {period}</b>\n  Ingresos: <code>${income}</code>",
    },
    "calcexpenses_needs": {
        "en": "🏠 <b>Needs (50%)</b> — budget <code>${budget}</code>",
        "es": "🏠 <b>Necesidades (50%)</b> — presupuesto <code>${budget}</code>",
    },
    "calcexpenses_wants": {
        "en": "🎉 <b>Wants (30%)</b> — budget <code>${budget}</code>",
        "es": "🎉 <b>Deseos (30%)</b> — presupuesto <code>${budget}</code>",
    },
    "calcexpenses_savings": {
        "en": "📈 <b>Savings/Debt (20%)</b> — budget <code>${budget}</code>",
        "es": "📈 <b>Ahorro/Deuda (20%)</b> — presupuesto <code>${budget}</code>",
    },
    "calcexpenses_spent": {
        "en": "  Spent: <code>${spent}</code> → {status}",
        "es": "  Gastado: <code>${spent}</code> → {status}",
    },
    "calcexpenses_over": {
        "en": "⚠️ <b>OVER</b> by <code>${amount}</code>",
        "es": "⚠️ <b>EXCEDIDO</b> por <code>${amount}</code>",
    },
    "calcexpenses_remaining": {
        "en": "✅ <code>${amount}</code> remaining",
        "es": "✅ <code>${amount}</code> restante",
    },
    "calcexpenses_net": {
        "en": "<b>Net Worth</b>: <code>${amount}</code>",
        "es": "<b>Patrimonio Neto</b>: <code>${amount}</code>",
    },

    # ── /savings ──────────────────────────────────────────────────────────────
    "savings_bad_period": {
        "en": "❌ Could not understand period: <code>{period}</code>\nTry: <code>this month</code>, <code>last month</code>, <code>january</code>, <code>january 2025</code>, <code>2025</code>.",
        "es": "❌ No se pudo entender el período: <code>{period}</code>\nIntenta: <code>this month</code>, <code>last month</code>, <code>enero</code>, <code>enero 2025</code>, <code>2025</code>.",
    },
    "savings_header": {
        "en": "📈 <b>Savings — {period}:</b>",
        "es": "📈 <b>Ahorros — {period}:</b>",
    },
    "savings_set_aside": {
        "en": "  Set aside: <code>${amount}</code>",
        "es": "  Apartado:  <code>${amount}</code>",
    },
    "savings_withdrew": {
        "en": "  Withdrew:  <code>${amount}</code>",
        "es": "  Retirado:  <code>${amount}</code>",
    },
    "savings_pot": {
        "en": "  Pot:       <code>${amount}</code>",
        "es": "  Total:     <code>${amount}</code>",
    },
    "savings_withdrawal_tip": {
        "en": "_To record taking money back out: <code>Income [Account] [Amount] Savings</code>_",
        "es": "_Para registrar un retiro de ahorros: <code>Income [Cuenta] [Monto] Savings</code>_",
    },

    # ── /summary ──────────────────────────────────────────────────────────────
    "summary_bad_month": {
        "en": "❌ <code>{arg}</code> is not a valid month. Try <code>/summary august</code>.",
        "es": "❌ <code>{arg}</code> no es un mes válido. Intenta <code>/summary agosto</code>.",
    },
    "summary_no_records": {
        "en": "No records found for {period}.",
        "es": "No se encontraron registros para {period}.",
    },
    "summary_header": {
        "en": "📋 <b>Summary — {period}</b>",
        "es": "📋 <b>Resumen — {period}</b>",
    },
    "summary_income": {
        "en": "💰 <b>Income</b>: <code>${amount}</code>",
        "es": "💰 <b>Ingresos</b>: <code>${amount}</code>",
    },
    "summary_expenses": {
        "en": "💸 <b>Expenses</b>: <code>${amount}</code>",
        "es": "💸 <b>Gastos</b>: <code>${amount}</code>",
    },
    "summary_net": {
        "en": "{emoji} <b>Net</b>: <code>${amount}</code>",
        "es": "{emoji} <b>Neto</b>: <code>${amount}</code>",
    },
    "summary_savings_rate": {
        "en": "{emoji} <b>Savings rate</b>: <code>{rate}%</code>",
        "es": "{emoji} <b>Tasa de ahorro</b>: <code>{rate}%</code>",
    },
    "summary_by_category": {
        "en": "<b>By category:</b>",
        "es": "<b>Por categoría:</b>",
    },
    "summary_other": {
        "en": "  • Other: <code>${amount}</code>",
        "es": "  • Otros: <code>${amount}</code>",
    },
    "summary_forecast": {
        "en": "\n📉 <b>Forecast</b>: <code>${amount}</code> by month-end",
        "es": "\n📉 <b>Proyección</b>: <code>${amount}</code> al cierre del mes",
    },
    "summary_goals_header": {
        "en": "\n🎯 <b>Goals:</b>",
        "es": "\n🎯 <b>Metas:</b>",
    },

    # ── /top ──────────────────────────────────────────────────────────────────
    "top_bad_month": {
        "en": "❌ <code>{arg}</code> is not a valid month. Try <code>/top august</code>.",
        "es": "❌ <code>{arg}</code> no es un mes válido. Intenta <code>/top agosto</code>.",
    },
    "top_none": {
        "en": "No expenses found for {period}.",
        "es": "No se encontraron gastos para {period}.",
    },
    "top_header": {
        "en": "🏆 <b>Top Expenses — {period}</b>",
        "es": "🏆 <b>Mayores Gastos — {period}</b>",
    },

    # ── /ytd ──────────────────────────────────────────────────────────────────
    "ytd_no_records": {
        "en": "No records found for {year}.",
        "es": "No se encontraron registros para {year}.",
    },
    "ytd_header": {
        "en": "📅 <b>Year-to-Date — {year}</b>",
        "es": "📅 <b>Año en Curso — {year}</b>",
    },
    "ytd_total_income": {
        "en": "💰 <b>Total Income</b>:   <code>${amount}</code>",
        "es": "💰 <b>Total Ingresos</b>:  <code>${amount}</code>",
    },
    "ytd_total_expenses": {
        "en": "💸 <b>Total Expenses</b>: <code>${amount}</code>",
        "es": "💸 <b>Total Gastos</b>:   <code>${amount}</code>",
    },
    "ytd_net_worth": {
        "en": "{emoji} <b>Net Worth</b>:      <code>${amount}</code>",
        "es": "{emoji} <b>Patrimonio Neto</b>: <code>${amount}</code>",
    },
    "ytd_savings_rate": {
        "en": "{emoji} <b>Savings Rate</b>:   <code>{rate}%</code>",
        "es": "{emoji} <b>Tasa de Ahorro</b>: <code>{rate}%</code>",
    },
    "ytd_by_category": {
        "en": "<b>Expenses by category:</b>",
        "es": "<b>Gastos por categoría:</b>",
    },
    "ytd_other": {
        "en": "  • Other: <code>${amount}</code>",
        "es": "  • Otros: <code>${amount}</code>",
    },

    # ── /dash ─────────────────────────────────────────────────────────────────
    "dash_header": {
        "en": "📊 <b>Dashboard — {period}</b>",
        "es": "📊 <b>Panel — {period}</b>",
    },
    "dash_health_score": {
        "en": "{emoji} <b>Health Score: {score}/{max}</b> <code>{bar}</code>",
        "es": "{emoji} <b>Puntaje Financiero: {score}/{max}</b> <code>{bar}</code>",
    },
    "dash_balances": {
        "en": "<b>💳 Balances:</b>",
        "es": "<b>💳 Saldos:</b>",
    },
    "dash_no_transactions": {
        "en": "  No transactions yet.",
        "es": "  Sin transacciones aún.",
    },
    "dash_this_month": {
        "en": "\n<b>📆 This Month:</b>",
        "es": "\n<b>📆 Este Mes:</b>",
    },
    "dash_income": {
        "en": "  💰 Income:   <code>${amount}</code>",
        "es": "  💰 Ingresos: <code>${amount}</code>",
    },
    "dash_expenses": {
        "en": "  💸 Expenses: <code>${amount}</code>",
        "es": "  💸 Gastos:   <code>${amount}</code>",
    },
    "dash_net": {
        "en": "  {emoji} Net:      <code>${amount}</code>",
        "es": "  {emoji} Neto:     <code>${amount}</code>",
    },
    "dash_savings_rate": {
        "en": "  {emoji} Savings rate: <code>{rate}%</code>",
        "es": "  {emoji} Tasa de ahorro: <code>{rate}%</code>",
    },
    "dash_budget_header": {
        "en": "\n<b>📏 50/30/20 Budget:</b>",
        "es": "\n<b>📏 Presupuesto 50/30/20:</b>",
    },
    "dash_budget_over": {
        "en": "⚠️ over by <code>${amount}</code>",
        "es": "⚠️ excedido por <code>${amount}</code>",
    },
    "dash_budget_left": {
        "en": "✅ <code>${amount}</code> left",
        "es": "✅ <code>${amount}</code> restante",
    },
    "dash_needs": {
        "en": "Needs",
        "es": "Necesidades",
    },
    "dash_wants": {
        "en": "Wants",
        "es": "Deseos",
    },
    "dash_savings_debt": {
        "en": "Savings/Debt",
        "es": "Ahorro/Deuda",
    },
    "dash_forecast_header": {
        "en": "\n<b>📉 Forecast:</b>",
        "es": "\n<b>📉 Proyección:</b>",
    },
    "dash_forecast_pace": {
        "en": "  At current pace: <code>${amount}</code> by month-end",
        "es": "  Al ritmo actual: <code>${amount}</code> al cierre del mes",
    },
    "dash_forecast_exceed": {
        "en": "  ⚠️ Projected to exceed income by <code>${amount}</code>",
        "es": "  ⚠️ Se proyecta superar los ingresos en <code>${amount}</code>",
    },
    "dash_no_month_tx": {
        "en": "  No transactions this month yet.",
        "es": "  Sin transacciones este mes aún.",
    },

    # ── /ql shortcuts ─────────────────────────────────────────────────────────
    "ql_no_sheet": {
        "en": "❌ Could not open shortcuts sheet.",
        "es": "❌ No se pudo abrir la hoja de atajos.",
    },
    "ql_none_saved": {
        "en": "No shortcuts saved yet.\nAdd one: <code>/ql add lunch Expense Cash Wants Lunch 15</code>",
        "es": "No hay atajos guardados aún.\nAgrega uno: <code>/ql add almuerzo Expense Cash Wants Almuerzo 15</code>",
    },
    "ql_header": {
        "en": "<b>⚡ Quick Log</b> — tap a shortcut:",
        "es": "<b>⚡ Registro Rápido</b> — selecciona un atajo:",
    },
    "ql_add_usage": {
        "en": "Usage: <code>/ql add <name> <transaction></code>\nExample: <code>/ql add lunch Expense Cash Wants Lunch 15</code>",
        "es": "Uso: <code>/ql add <nombre> <transacción></code>\nEjemplo: <code>/ql add almuerzo Expense Cash Wants Almuerzo 15</code>",
    },
    "ql_add_parse_error": {
        "en": "❌ That transaction doesn't parse correctly:\n{error}",
        "es": "❌ La transacción no tiene el formato correcto:\n{error}",
    },
    "ql_updated": {
        "en": "✅ Updated shortcut <code>{name}</code>.",
        "es": "✅ Atajo <code>{name}</code> actualizado.",
    },
    "ql_saved": {
        "en": "✅ Shortcut saved: <code>/ql {name}</code> → <code>{transaction}</code>",
        "es": "✅ Atajo guardado: <code>/ql {name}</code> → <code>{transaction}</code>",
    },
    "ql_delete_usage": {
        "en": "Usage: <code>/ql delete <name></code>",
        "es": "Uso: <code>/ql delete <nombre></code>",
    },
    "ql_deleted": {
        "en": "✅ Deleted shortcut <code>{name}</code>.",
        "es": "✅ Atajo <code>{name}</code> eliminado.",
    },
    "ql_not_found": {
        "en": "❌ No shortcut named <code>{name}</code>.",
        "es": "❌ No existe un atajo llamado <code>{name}</code>.",
    },
    "ql_not_found_with_hint": {
        "en": "❌ No shortcut named <code>{name}</code>. Use <code>/ql</code> to see your list.",
        "es": "❌ No existe un atajo llamado <code>{name}</code>. Usa <code>/ql</code> para ver tu lista.",
    },
    "ql_enter_amount": {
        "en": "⚡ <b>{name}</b> — enter the amount:",
        "es": "⚡ <b>{name}</b> — ingresa el monto:",
    },
    "ql_bad_amount": {
        "en": "❌ <code>{value}</code> is not a valid amount. Try again.",
        "es": "❌ <code>{value}</code> no es un monto válido. Intenta de nuevo.",
    },
    "ql_shortcut_broken": {
        "en": "❌ Shortcut is broken: {error}",
        "es": "❌ El atajo tiene un error: {error}",
    },

    # ── /log guided flow ──────────────────────────────────────────────────────
    "log_what_type": {
        "en": "What type of transaction?",
        "es": "¿Qué tipo de transacción?",
    },
    "log_btn_expense": {
        "en": "💸 Expense",
        "es": "💸 Gasto",
    },
    "log_btn_income": {
        "en": "💰 Income",
        "es": "💰 Ingreso",
    },
    "log_btn_transfer": {
        "en": "↔️ Transfer",
        "es": "↔️ Transferencia",
    },
    "log_which_account_buttons": {
        "en": "<b>{type}</b> — Which account?",
        "es": "<b>{type}</b> — ¿Qué cuenta?",
    },
    "log_which_account_text": {
        "en": "<b>{type}</b> — Type the account name:",
        "es": "<b>{type}</b> — Escribe el nombre de la cuenta:",
    },
    "log_transfer_to_account": {
        "en": "Transfer to which account?",
        "es": "¿A qué cuenta transferir?",
    },
    "log_account_confirmed": {
        "en": "Account: <b>{account}</b>\n\nEnter the amount:",
        "es": "Cuenta: <b>{account}</b>\n\nIngresa el monto:",
    },
    "log_transfer_confirmed": {
        "en": "From: <b>{from_acc}</b> → To: <b>{to_acc}</b>\n\nAdd a description? (or tap Skip)",
        "es": "De: <b>{from_acc}</b> → A: <b>{to_acc}</b>\n\nAgregar descripción? (o toca Omitir)",
    },
    "log_bad_amount": {
        "en": "❌ <code>{value}</code> is not a valid amount. Try again.",
        "es": "❌ <code>{value}</code> no es un monto válido. Intenta de nuevo.",
    },
    "log_select_category": {
        "en": "Amount: *${amount}*\n\nSelect a category:",
        "es": "Monto: *${amount}*\n\nSelecciona una categoría:",
    },
    "log_category_confirmed": {
        "en": "Category: <b>{category}</b>\n\nAdd a description? (or tap Skip)",
        "es": "Categoría: <b>{category}</b>\n\nAgregar descripción? (o toca Omitir)",
    },
    "log_ask_description": {
        "en": "Amount: <b>${amount}</b>\n\nAdd a description? (or tap Skip)",
        "es": "Monto: <b>${amount}</b>\n\nAgregar descripción? (o toca Omitir)",
    },
    "log_category_which": {
        "en": "Which category? ({type})",
        "es": "¿Qué categoría? ({type})",
    },
    "btn_needs": {
        "en": "🏠 Needs",
        "es": "🏠 Necesidades",
    },
    "btn_wants": {
        "en": "🎉 Wants",
        "es": "🎉 Deseos",
    },
    "btn_savings": {
        "en": "📈 Savings",
        "es": "📈 Ahorros",
    },
    "btn_debt": {
        "en": "💳 Debt",
        "es": "💳 Deuda",
    },

    # ── /undo ─────────────────────────────────────────────────────────────────
    "undo_nothing": {
        "en": "Nothing to undo — no recent transaction found for this session.",
        "es": "Nada que deshacer — no se encontró transacción reciente en esta sesión.",
    },
    "undo_gone": {
        "en": "↩️ The transaction no longer exists in the sheet (already deleted?).",
        "es": "↩️ La transacción ya no existe en la hoja (¿ya fue eliminada?).",
    },
    "undo_success": {
        "en": "↩️ Undone: {type} <b>{account}</b> <code>${amount}</code> {category}",
        "es": "↩️ Deshecho: {type} <b>{account}</b> <code>${amount}</code> {category}",
    },
    "undo_failed": {
        "en": "❌ Undo failed. Reference: <code>{corr_id}</code>",
        "es": "❌ No se pudo deshacer. Referencia: <code>{corr_id}</code>",
    },
    "undo_could_not_reach": {
        "en": "❌ Could not reach your sheet.",
        "es": "❌ No se pudo acceder a tu hoja.",
    },

    # ── /recent ───────────────────────────────────────────────────────────────
    "recent_none": {
        "en": "No transactions found.",
        "es": "No se encontraron transacciones.",
    },
    "recent_header": {
        "en": "🕐 <b>Last {n} transactions:</b>",
        "es": "🕐 <b>Últimas {n} transacciones:</b>",
    },
    "recent_error": {
        "en": "Could not fetch recent transactions",
        "es": "No se pudieron obtener las transacciones recientes",
    },

    # ── /recurring ────────────────────────────────────────────────────────────
    "recurring_none": {
        "en": "No recurring transactions set up.\n\nAdd one: <code>/recurring add rent 1 Expense Cash Needs Rent 1500</code>\n<i>(This would log rent on the 1st of each month)</i>",
        "es": "No hay transacciones recurrentes configuradas.\n\nAgrega una: <code>/recurring add alquiler 1 Expense Cash Needs Alquiler 1500</code>\n<i>(Esto registraría el alquiler el 1ro de cada mes)</i>",
    },
    "recurring_header": {
        "en": "🔄 <b>Recurring Transactions:</b>",
        "es": "🔄 <b>Transacciones Recurrentes:</b>",
    },
    "recurring_day": {
        "en": "Day {day}",
        "es": "Día {day}",
    },
    "recurring_last": {
        "en": "last: {date}",
        "es": "último: {date}",
    },
    "recurring_never": {
        "en": "never logged",
        "es": "nunca registrada",
    },
    "recurring_add_usage": {
        "en": "Usage: <code>/recurring add <name> <day> <transaction></code>\nExample: <code>/recurring add rent 1 Expense Cash Needs Rent 1500</code>",
        "es": "Uso: <code>/recurring add <nombre> <día> <transacción></code>\nEjemplo: <code>/recurring add alquiler 1 Expense Cash Needs Alquiler 1500</code>",
    },
    "recurring_bad_day": {
        "en": "❌ Day must be a number between 1 and 31.",
        "es": "❌ El día debe ser un número entre 1 y 31.",
    },
    "recurring_bad_tx": {
        "en": "❌ Transaction doesn't parse:\n{error}",
        "es": "❌ La transacción no tiene el formato correcto:\n{error}",
    },
    "recurring_added": {
        "en": "✅ Added: <b>{name}</b> will auto-log on day <b>{day}</b> of each month.",
        "es": "✅ Agregada: <b>{name}</b> se registrará automáticamente el día <b>{day}</b> de cada mes.",
    },
    "recurring_delete_usage": {
        "en": "Usage: <code>/recurring delete <name></code>",
        "es": "Uso: <code>/recurring delete <nombre></code>",
    },
    "recurring_deleted": {
        "en": "✅ Deleted recurring: <b>{name}</b>.",
        "es": "✅ Recurrente eliminada: <b>{name}</b>.",
    },
    "recurring_not_found": {
        "en": "❌ No recurring transaction named <code>{name}</code>.",
        "es": "❌ No existe una transacción recurrente llamada <code>{name}</code>.",
    },
    "recurring_auto_logged": {
        "en": "🔄 <b>Recurring logged:</b> <i>{name}</i>\n  <code>{transaction}</code>",
        "es": "🔄 <b>Recurrente registrada:</b> <i>{name}</i>\n  <code>{transaction}</code>",
    },

    # ── Goals ─────────────────────────────────────────────────────────────────
    "setgoal_usage": {
        "en": "Usage: <code>/setgoal <name> <amount></code>\nExample: <code>/setgoal vacation 2000</code>",
        "es": "Uso: <code>/setgoal <nombre> <monto></code>\nEjemplo: <code>/setgoal vacaciones 2000</code>",
    },
    "setgoal_bad_amount": {
        "en": "❌ <code>{value}</code> is not a valid amount.",
        "es": "❌ <code>{value}</code> no es un monto válido.",
    },
    "setgoal_updated": {
        "en": "✅ Updated goal <b>{name}</b>: target set to <code>${amount}</code>.",
        "es": "✅ Meta <b>{name}</b> actualizada: objetivo <code>${amount}</code>.",
    },
    "setgoal_created": {
        "en": "🎯 Goal created: <b>{name}</b> — <code>${amount}</code>\nUse <code>/addtogoal {name} <amount></code> to track your progress.",
        "es": "🎯 Meta creada: <b>{name}</b> — <code>${amount}</code>\nUsa <code>/addtogoal {name} <monto></code> para registrar tu progreso.",
    },
    "addtogoal_usage": {
        "en": "Usage: <code>/addtogoal <name> <amount></code>\nExample: <code>/addtogoal vacation 200</code>",
        "es": "Uso: <code>/addtogoal <nombre> <monto></code>\nEjemplo: <code>/addtogoal vacaciones 200</code>",
    },
    "addtogoal_bad_amount": {
        "en": "❌ <code>{value}</code> is not a valid amount.",
        "es": "❌ <code>{value}</code> no es un monto válido.",
    },
    "addtogoal_success": {
        "en": "✅ Added <code>${amount}</code> to <b>{name}</b>",
        "es": "✅ Agregado <code>${amount}</code> a <b>{name}</b>",
    },
    "addtogoal_saved_of": {
        "en": "Saved: <code>${saved}</code> / <code>${target}</code>",
        "es": "Ahorrado: <code>${saved}</code> / <code>${target}</code>",
    },
    "addtogoal_reached": {
        "en": "🎉 <b>Goal reached!</b>",
        "es": "🎉 <b>¡Meta alcanzada!</b>",
    },
    "addtogoal_not_found": {
        "en": "❌ No goal named <code>{name}</code>. Create one with <code>/setgoal {name} <amount></code>.",
        "es": "❌ No existe una meta llamada <code>{name}</code>. Crea una con <code>/setgoal {name} <monto></code>.",
    },
    "goals_none": {
        "en": "No goals yet. Create one: <code>/setgoal vacation 2000</code>",
        "es": "Sin metas aún. Crea una: <code>/setgoal vacaciones 2000</code>",
    },
    "goals_header": {
        "en": "🎯 <b>Your Goals:</b>",
        "es": "🎯 <b>Tus Metas:</b>",
    },
    "goals_done": {
        "en": "✅ Done!",
        "es": "✅ ¡Completada!",
    },
    "goals_to_go": {
        "en": "<code>${amount}</code> to go",
        "es": "<code>${amount}</code> por alcanzar",
    },

    # ── /quiet ────────────────────────────────────────────────────────────────
    "quiet_enabled": {
        "en": "🔕 Monthly summaries disabled. Use /quiet again to re-enable.",
        "es": "🔕 Resúmenes mensuales desactivados. Usa /quiet de nuevo para activarlos.",
    },
    "quiet_disabled": {
        "en": "🔔 Monthly summaries re-enabled. Use /quiet again to turn them off.",
        "es": "🔔 Resúmenes mensuales activados. Usa /quiet de nuevo para desactivarlos.",
    },

    # ── Monthly summary push ──────────────────────────────────────────────────
    "monthly_push_header": {
        "en": "📅 <b>Monthly Summary — {period}</b>",
        "es": "📅 <b>Resumen Mensual — {period}</b>",
    },
    "monthly_push_income": {
        "en": "💰 Income:   <code>${amount}</code>",
        "es": "💰 Ingresos: <code>${amount}</code>",
    },
    "monthly_push_expenses": {
        "en": "💸 Expenses: <code>${amount}</code>",
        "es": "💸 Gastos:   <code>${amount}</code>",
    },
    "monthly_push_net": {
        "en": "{emoji} Net:      <code>${amount}</code>",
        "es": "{emoji} Neto:     <code>${amount}</code>",
    },
    "monthly_push_rate": {
        "en": "{emoji} Savings rate: <code>{rate}%</code>",
        "es": "{emoji} Tasa de ahorro: <code>{rate}%</code>",
    },
    "monthly_push_pot": {
        "en": "🏦 Savings pot: <code>${amount}</code>",
        "es": "🏦 Ahorro total: <code>${amount}</code>",
    },
    "monthly_push_footer": {
        "en": "<i>Use /quiet to turn off these monthly summaries.</i>",
        "es": "<i>Usa /quiet para desactivar estos resúmenes mensuales.</i>",
    },

    # ── /trend ────────────────────────────────────────────────────────────────
    "trend_chart_title": {
        "en": "Trend — last {n} months",
        "es": "Tendencia — últimos {n} meses",
    },
    "trend_chart_income": {
        "en": "Income",
        "es": "Ingresos",
    },
    "trend_chart_expenses": {
        "en": "Expenses",
        "es": "Gastos",
    },
    "trend_chart_savings": {
        "en": "Savings pot",
        "es": "Ahorro neto",
    },
    "trend_caption": {
        "en": "📈 Trend — last {n} months",
        "es": "📈 Tendencia — últimos {n} meses",
    },
    "trend_error": {
        "en": "Could not generate trend chart",
        "es": "No se pudo generar el gráfico de tendencia",
    },

    # ── /cancel ───────────────────────────────────────────────────────────────
    "cancel_reply": {
        "en": "❌ Transaction cancelled.",
        "es": "❌ Transacción cancelada.",
    },
}


def t(key: str, lang: str, **kwargs) -> str:
    """Return the string for `key` in `lang`, falling back to English."""
    entry = STRINGS.get(key, {})
    text = entry.get(lang) or entry.get("en", f"[missing:{key}]")
    return text.format(**kwargs) if kwargs else text
