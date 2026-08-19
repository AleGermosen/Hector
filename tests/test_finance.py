import html
import pytest
from datetime import datetime as real_datetime
from finance.bot import FinanceBot, parse_rows, parse_period, _parse_amount, _parse_amount_strict, now, Transaction, _safe_eval

# Minimal bot instance with no real credentials — enough to test pure methods
bot = FinanceBot.__new__(FinanceBot)
bot.strict_users = []
bot.user_mapping = {}


# ── _parse_month ─────────────────────────────────────────────────────────────

def test_parse_month_full_name():
    assert bot._parse_month("january") == 1
    assert bot._parse_month("December") == 12

def test_parse_month_abbreviation():
    assert bot._parse_month("jan") == 1
    assert bot._parse_month("Aug") == 8

def test_parse_month_invalid():
    assert bot._parse_month("notamonth") is None
    assert bot._parse_month("") is None


# ── _parse_date_robust ────────────────────────────────────────────────────────

def test_parse_date_iso():
    d = bot._parse_date_robust("2024-08-15")
    assert d.year == 2024 and d.month == 8 and d.day == 15

def test_parse_date_with_time():
    d = bot._parse_date_robust("2024-08-15 14:30:00")
    assert d.hour == 14 and d.minute == 30

def test_parse_date_us_format():
    d = bot._parse_date_robust("08/15/2024")
    assert d.month == 8 and d.day == 15

def test_parse_date_eu_format():
    d = bot._parse_date_robust("15/08/2024")
    assert d.day == 15 and d.month == 8

def test_parse_date_iso_long_string():
    d = bot._parse_date_robust("2024-08-15T14:30:00.000Z")
    assert d.year == 2024 and d.month == 8

def test_parse_date_invalid():
    assert bot._parse_date_robust("not-a-date") is None
    assert bot._parse_date_robust("") is None
    assert bot._parse_date_robust(None) is None


# ── _parse_amount_robust ──────────────────────────────────────────────────────

def test_parse_amount_plain():
    assert bot._parse_amount_robust("150") == 150.0

def test_parse_amount_with_dollar_sign():
    assert bot._parse_amount_robust("$1500.50") == 1500.50

def test_parse_amount_with_comma():
    assert bot._parse_amount_robust("1,500.99") == 1500.99

def test_parse_amount_with_dollar_and_comma():
    assert bot._parse_amount_robust("$1,200") == 1200.0

def test_parse_amount_invalid():
    assert bot._parse_amount_robust("abc") == 0.0
    assert bot._parse_amount_robust("") == 0.0
    assert bot._parse_amount_robust(None) == 0.0


# ── _get_data_summary ─────────────────────────────────────────────────────────

SAMPLE_RECORDS = [
    ["Date", "Type", "Account", "Amount", "Category", "Description"],
    ["2024-08-01", "Income", "Cash", "3000", "Salary", "August salary"],
    ["2024-08-05", "Expense", "Cash", "500", "Needs", "Rent"],
    ["2024-08-10", "Expense", "Cash", "200", "Wants", "Dining out"],
    ["2024-08-15", "Expense", "Cash", "100", "Savings", "Emergency fund"],
    ["2024-07-01", "Income", "Cash", "3000", "Salary", "July salary"],
    ["2024-07-10", "Expense", "Cash", "400", "Needs", "Groceries"],
]

def test_summary_all_time():
    summary = bot._get_data_summary(SAMPLE_RECORDS)
    assert summary['total_income'] == 6000.0
    # Savings expense (100) is now neutral — excluded from total_expenses
    assert summary['total_expenses'] == 1100.0
    assert summary['categories']['Needs'] == 900.0
    assert summary['categories']['Wants'] == 200.0
    assert summary['net_worth'] == 4900.0

def test_summary_filtered_by_month():
    summary = bot._get_data_summary(SAMPLE_RECORDS, target_month=8, target_year=2024)
    assert summary['total_income'] == 3000.0
    # Savings expense (100) is neutral — excluded
    assert summary['total_expenses'] == 700.0
    assert summary['categories']['Needs'] == 500.0
    assert summary['categories']['Wants'] == 200.0
    # Savings is no longer tracked in categories (it's neutral)
    assert 'Savings' not in summary['categories'] or summary['categories'].get('Savings', 0) == 0

def test_summary_empty_month():
    summary = bot._get_data_summary(SAMPLE_RECORDS, target_month=1, target_year=2024)
    assert summary['total_income'] == 0.0
    assert summary['total_expenses'] == 0.0
    assert summary['net_worth'] == 0.0

def test_summary_excludes_transfers():
    records = [
        ["Date", "Type", "Account", "Amount", "Category"],
        ["2024-08-01", "Income", "Cash", "1000", "Salary"],
        ["2024-08-02", "Expense", "Cash", "200", "Transfer"],
        ["2024-08-02", "Income", "Digital", "200", "Transfer"],
    ]
    summary = bot._get_data_summary(records)
    assert summary['total_income'] == 1000.0
    assert summary['total_expenses'] == 0.0


# ── _parse_transaction_text ───────────────────────────────────────────────────

def test_parse_expense():
    rows, preview, error = bot._parse_transaction_text("Expense Cash 50 Needs Groceries", "123")
    assert error is None
    assert rows[0][1] == "Expense"
    assert rows[0][2] == "Cash"
    assert rows[0][3] == 50.0
    assert rows[0][4] == "Needs"
    assert rows[0][5] == "Groceries"

def test_parse_income():
    rows, preview, error = bot._parse_transaction_text("Income Digital 3000 Salary Monthly pay", "123")
    assert error is None
    assert rows[0][1] == "Income"
    assert rows[0][3] == 3000.0

def test_parse_transfer():
    rows, preview, error = bot._parse_transaction_text("Transfer Digital to Cash 1500", "123")
    assert error is None
    assert len(rows) == 2
    assert rows[0][1] == "Expense"
    assert rows[1][1] == "Income"
    assert rows[0][3] == 1500.0

def test_parse_savings_auto_transfer():
    rows, preview, error = bot._parse_transaction_text("Expense Cash 200 Savings Emergency", "123")
    assert error is None
    assert len(rows) == 2
    assert rows[1][1] == "Income"
    assert rows[1][2] == "Account"

def test_parse_invalid_type():
    rows, preview, error = bot._parse_transaction_text("Buy Cash 50 Needs stuff", "123")
    assert error is not None
    assert rows is None

def test_parse_invalid_amount():
    rows, preview, error = bot._parse_transaction_text("Expense Cash abc Needs stuff", "123")
    assert error is not None
    assert "not a valid amount" in error

def test_parse_missing_category():
    rows, preview, error = bot._parse_transaction_text("Expense Cash 50", "123")
    assert error is not None
    assert "category" in error.lower()

def test_parse_too_few_args():
    rows, preview, error = bot._parse_transaction_text("Expense Cash", "123")
    assert error is not None


# ── savings_rate in summary ───────────────────────────────────────────────────

def test_savings_rate_calculated():
    summary = bot._get_data_summary(SAMPLE_RECORDS, target_month=8, target_year=2024)
    # income=3000, expenses=700 (Savings excluded), net=2300 → rate = 2300/3000 * 100 = 76.7%
    assert summary['savings_rate'] == round((2300 / 3000) * 100, 1)

def test_savings_rate_zero_income():
    records = [["Date", "Type", "Account", "Amount", "Category"],
               ["2024-08-01", "Expense", "Cash", "100", "Needs"]]
    summary = bot._get_data_summary(records)
    assert summary['savings_rate'] == 0.0


# ── year_only filter (/ytd) ───────────────────────────────────────────────────

def test_ytd_year_filter():
    summary = bot._get_data_summary(SAMPLE_RECORDS, year_only=True, target_year=2024)
    # Both July and August 2024 records should be included
    assert summary['total_income'] == 6000.0
    assert summary['categories']['Needs'] == 900.0

def test_ytd_excludes_other_years():
    records = [
        ["Date", "Type", "Account", "Amount", "Category"],
        ["2023-01-01", "Income", "Cash", "5000", "Salary"],
        ["2024-03-01", "Income", "Cash", "3000", "Salary"],
    ]
    summary = bot._get_data_summary(records, year_only=True, target_year=2024)
    assert summary['total_income'] == 3000.0


# ── _get_top_expenses ─────────────────────────────────────────────────────────

def test_top_expenses_sorted():
    top = bot._get_top_expenses(SAMPLE_RECORDS, target_month=8, target_year=2024)
    assert top[0][0] >= top[-1][0]  # sorted descending by amount

def test_top_expenses_excludes_income():
    top = bot._get_top_expenses(SAMPLE_RECORDS, target_month=8, target_year=2024)
    for amount, category, desc, date in top:
        assert category != "Salary"

def test_top_expenses_limit():
    records = [["Date", "Type", "Account", "Amount", "Category"]]
    for i in range(10):
        records.append([f"2024-08-0{i+1}", "Expense", "Cash", str(i * 10 + 10), "Needs"])
    top = bot._get_top_expenses(records, target_month=8, target_year=2024, n=5)
    assert len(top) == 5


# ── _previous_month ───────────────────────────────────────────────────────────

def test_previous_month_normal():
    assert bot._previous_month(8, 2024) == (7, 2024)

def test_previous_month_january_wraps():
    assert bot._previous_month(1, 2024) == (12, 2023)


# ── _get_category_average ────────────────────────────────────────────────────

from unittest.mock import patch

MULTI_MONTH_RECORDS = [
    ["Date", "Type", "Account", "Amount", "Category", "Description"],
    # June — $300 Needs
    ["2024-06-05", "Expense", "Cash", "300", "Needs", "June rent"],
    # July — $400 Needs
    ["2024-07-10", "Expense", "Cash", "400", "Needs", "July rent"],
    # August (current month — should be excluded from average)
    ["2024-08-01", "Expense", "Cash", "500", "Needs", "August rent"],
]

def test_category_average_excludes_current_month():
    with patch('finance.bot.now') as mock_now:
        mock_now.return_value = real_datetime(2024, 8, 15)
        avg = bot._get_category_average(MULTI_MONTH_RECORDS, 'Needs')
    # Average of June ($300) and July ($400) = $350; August excluded
    assert avg == 350.0

def test_category_average_no_history():
    avg = bot._get_category_average([], 'Needs')
    assert avg == 0.0


# ── _get_known_accounts ───────────────────────────────────────────────────────

def test_known_accounts_distinct_sorted():
    records = [
        ["Date", "Type", "Account", "Amount", "Category"],
        ["2024-08-01", "Income", "Digital", "1000", "Salary"],
        ["2024-08-02", "Expense", "Cash", "50", "Needs"],
        ["2024-08-03", "Expense", "Cash", "30", "Wants"],
    ]
    accounts = bot._get_known_accounts(records)
    assert accounts == ["Cash", "Digital"]

def test_known_accounts_empty():
    assert bot._get_known_accounts([]) == []


# ── _spend_forecast ───────────────────────────────────────────────────────────

def test_spend_forecast_midmonth():
    # $500 spent in 15 days of a 30-day month → project $1000
    projected = bot._spend_forecast(500, 15, 30)
    assert projected == pytest.approx(1000.0)

def test_spend_forecast_day_zero():
    projected = bot._spend_forecast(0, 0, 30)
    assert projected == 0.0


# ── _get_balance_by_account ───────────────────────────────────────────────────

def test_balance_calculation():
    records = [
        ["Date", "Type", "Account", "Amount", "Category"],
        ["2024-08-01", "Income", "Cash", "1000", "Salary"],
        ["2024-08-02", "Expense", "Cash", "200", "Needs"],
        ["2024-08-03", "Income", "Digital", "500", "Salary"],
    ]
    balances = bot._get_balance_by_account(records)
    assert balances["Cash"] == 800.0
    assert balances["Digital"] == 500.0


# ── _get_health_score ─────────────────────────────────────────────────────────

def _make_summary(income, needs, wants, savings=0, debt=0, prev_net=None):
    expenses = needs + wants + savings + debt
    net = income - expenses
    rate = round((net / income) * 100, 1) if income > 0 else 0.0
    s = {
        'total_income': income,
        'total_expenses': expenses,
        'net_worth': net,
        'savings_rate': rate,
        'categories': {'Needs': needs, 'Wants': wants, 'Savings': savings, 'Debt': debt},
        'other_expenses': 0.0,
    }
    prev = {'net_worth': prev_net if prev_net is not None else net - 1}
    return s, prev

def test_health_score_perfect():
    s, prev = _make_summary(income=4000, needs=1800, wants=1000, savings=400, prev_net=500)
    balances = {'Cash': 500, 'Digital': 200}
    score, max_score, _ = bot._get_health_score(s, prev, balances)
    assert score == max_score == 10

def test_health_score_low():
    # Over on Needs, no savings rate, negative net worth
    s, prev = _make_summary(income=1000, needs=2500, wants=500, prev_net=0)
    balances = {'Cash': -100}
    score, _, _ = bot._get_health_score(s, prev, balances)
    assert score < 5

def test_health_score_no_balances():
    s, prev = _make_summary(income=3000, needs=1000, wants=500, prev_net=1000)
    score, _, _ = bot._get_health_score(s, prev, {})
    assert isinstance(score, int)


# ── _load_goals / _load_recurring ─────────────────────────────────────────────

def test_load_goals_parses_correctly():
    from unittest.mock import MagicMock
    ws = MagicMock()
    ws.get_all_records.return_value = [
        {'Name': 'vacation', 'Target': '2000', 'Saved': '500'},
        {'Name': 'car', 'Target': '10000', 'Saved': '3000'},
    ]
    goals = bot._load_goals(ws)
    assert len(goals) == 2
    assert goals[0]['name'] == 'vacation'
    assert goals[0]['target'] == 2000.0
    assert goals[1]['saved'] == 3000.0

def test_load_recurring_parses_correctly():
    from unittest.mock import MagicMock
    ws = MagicMock()
    ws.get_all_records.return_value = [
        {'Name': 'rent', 'Transaction': 'Expense Cash 1500 Needs Rent', 'Day': '1', 'Last Logged': '2024-08-01'},
    ]
    items = bot._load_recurring(ws)
    assert len(items) == 1
    assert items[0]['day'] == 1
    assert items[0]['last_logged'] == '2024-08-01'


# ═══════════════════════════════════════════════════════════════════════════════
# Phase 1 — ten required test cases
# ═══════════════════════════════════════════════════════════════════════════════

# Fixture: earn 3000, set aside 500, spend 800 groceries, withdraw 500, pay 500 rent
PHASE1_RECORDS = [
    ["Date", "Type", "Account", "Amount", "Category", "Description"],
    ["2024-08-01", "Income",  "Checking", "3000", "Salary",   "August pay"],
    ["2024-08-05", "Expense", "Checking",  "500", "Savings",  "Emergency fund"],
    ["2024-08-10", "Expense", "Checking",  "800", "Needs",    "Groceries"],
    ["2024-08-15", "Income",  "Checking",  "500", "Savings",  "Savings withdrawal"],
    ["2024-08-20", "Expense", "Checking",  "500", "Needs",    "Rent"],
]


# 1. Double-count scenario
def test_phase1_double_count():
    txs = parse_rows(PHASE1_RECORDS)
    income   = sum(t.amount for t in txs if t.type == 'Income'  and not t.is_neutral)
    expenses = sum(t.amount for t in txs if t.type == 'Expense' and not t.is_neutral)
    assert income   == 3000.0
    assert expenses == 1300.0   # 800 + 500, Savings row excluded
    assert income - expenses == 1700.0


# 2. Symmetry: Income/Savings withdrawal doesn't appear in reported income
def test_phase1_symmetry():
    txs = parse_rows(PHASE1_RECORDS)
    income = sum(t.amount for t in txs if t.type == 'Income' and not t.is_neutral)
    assert income == 3000.0     # the 500 Savings withdrawal is excluded


# 3. Savings pot
def test_phase1_savings_pot_zero():
    txs = parse_rows(PHASE1_RECORDS)
    set_aside = sum(t.amount for t in txs if t.type == 'Expense' and t.category.lower() == 'savings')
    withdrew  = sum(t.amount for t in txs if t.type == 'Income'  and t.category.lower() == 'savings')
    assert set_aside - withdrew == 0.0      # 500 in, 500 out

def test_phase1_savings_pot_set_aside_only():
    records = [["2024-08-01", "Expense", "Cash", "300", "Savings", ""]]
    txs = parse_rows(records)
    set_aside = sum(t.amount for t in txs if t.type == 'Expense' and t.category.lower() == 'savings')
    withdrew  = sum(t.amount for t in txs if t.type == 'Income'  and t.category.lower() == 'savings')
    assert set_aside - withdrew == 300.0


# 4. /balance unaffected: savings rows still move account balances, no phantom header row
def test_phase1_balance_unaffected():
    balances = bot._get_balance_by_account(PHASE1_RECORDS)
    assert 'Account' not in balances    # no phantom header
    assert 'Date' not in balances
    # Savings rows DO affect balance (money really left the account)
    assert balances['Checking'] == pytest.approx(3000 - 500 - 800 + 500 - 500)

def test_phase1_balance_no_phantom():
    records = [
        ["Date", "Type", "Account", "Amount", "Category"],
        ["2024-08-01", "Income", "Cash", "1000", "Salary"],
    ]
    balances = bot._get_balance_by_account(records)
    assert 'Account' not in balances
    assert 'Date' not in balances
    assert balances == {"Cash": 1000.0}


# 5. /expenses has no Savings slice; total = Needs + Wants + Debt
def test_phase1_expenses_no_savings_slice():
    summary = bot._get_data_summary(PHASE1_RECORDS)
    assert 'Savings' not in summary['categories'] or summary['categories'].get('Savings', 0) == 0
    cat_total = sum(summary['categories'].values()) + summary['other_expenses']
    assert cat_total == pytest.approx(summary['total_expenses'])


# 6. /calcExpenses income base ignores savings withdrawals; actual_savings is net
def test_phase1_calcexpenses_income_base():
    summary = bot._get_data_summary(PHASE1_RECORDS)
    assert summary['total_income'] == 3000.0    # 500 withdrawal excluded

def test_phase1_calcexpenses_savings_net():
    txs = parse_rows(PHASE1_RECORDS)
    expense_sav = sum(t.amount for t in txs if t.type == 'Expense' and t.category.lower() == 'savings')
    income_sav  = sum(t.amount for t in txs if t.type == 'Income'  and t.category.lower() == 'savings')
    actual_debt = sum(t.amount for t in txs if t.type == 'Expense' and t.category.lower() == 'debt')
    net_savings = expense_sav - income_sav + actual_debt
    assert net_savings == 0.0   # 500 set aside - 500 withdrew + 0 debt


# 7. Amount parsing: $50, 1,500, 15.50 all parse the same; garbage is rejected
def test_phase1_amounts_parse():
    assert _parse_amount("15.50")  == 15.50
    assert _parse_amount("1,500")  == 1500.0
    assert _parse_amount("$50")    == 50.0

def test_phase1_amount_strict_rejects():
    assert _parse_amount_strict("abc") is None
    assert _parse_amount_strict("")    is None
    assert _parse_amount_strict(None)  is None

def test_phase1_amount_strict_accepts():
    assert _parse_amount_strict("$1,500.50") == 1500.50
    assert _parse_amount_strict("15.50")     == 15.50


# 8. Period parsing — each form, including January rollover
def test_phase1_period_no_args():
    m, y, desc = parse_period([])
    assert m is None and y is None
    assert desc == "All Time"

def test_phase1_period_this_month():
    with patch('finance.bot.now') as mock:
        mock.return_value = real_datetime(2024, 8, 15)
        m, y, _ = parse_period(['this', 'month'])
    assert m == 8 and y == 2024

def test_phase1_period_last_month_normal():
    with patch('finance.bot.now') as mock:
        mock.return_value = real_datetime(2024, 8, 15)
        m, y, _ = parse_period(['last', 'month'])
    assert m == 7 and y == 2024

def test_phase1_period_last_month_january():
    with patch('finance.bot.now') as mock:
        mock.return_value = real_datetime(2024, 1, 15)
        m, y, _ = parse_period(['last', 'month'])
    assert m == 12 and y == 2023

def test_phase1_period_month_name():
    with patch('finance.bot.now') as mock:
        mock.return_value = real_datetime(2024, 8, 15)
        m, y, _ = parse_period(['january'])
    assert m == 1 and y == 2024

def test_phase1_period_month_and_year():
    m, y, _ = parse_period(['january', '2025'])
    assert m == 1 and y == 2025

def test_phase1_period_year_only():
    m, y, _ = parse_period(['2025'])
    assert m is None and y == 2025

def test_phase1_period_invalid():
    _, _, desc = parse_period(['foobar'])
    assert desc is None


# 9. Timezone: now() returns timezone-aware datetime
def test_phase1_timezone_aware():
    import zoneinfo
    n = now()
    assert n.tzinfo is not None

def test_phase1_month_filter_uses_date():
    """parse_rows month filter matches the date stamped on the transaction."""
    records = [["2024-08-31 23:30:00", "Expense", "Cash", "100", "Needs", ""]]
    assert len(parse_rows(records, month=8, year=2024)) == 1
    assert len(parse_rows(records, month=9, year=2024)) == 0


# 10. Header / junk rows dropped by parse_rows regardless of first cell
def test_phase1_header_dropped():
    records = [
        ["Date", "Type", "Account", "Amount", "Category", "Description"],
        ["2024-08-01", "Income", "Cash", "1000", "Salary", ""],
    ]
    txs = parse_rows(records)
    assert len(txs) == 1
    assert txs[0].type == 'Income'

def test_phase1_junk_rows_dropped():
    records = [
        ["garbage", "blah", "stuff", "123", "", ""],
        ["", "", "", "", "", ""],
        ["2024-08-01", "Expense", "Cash", "50", "Needs", ""],
    ]
    txs = parse_rows(records)
    assert len(txs) == 1

def test_phase1_checks_column_b_not_column_a():
    """Row filtering relies on column B (type), not column A (date/label)."""
    records = [
        ["Date", "Expense", "Cash", "50", "Needs", ""],    # col A = 'Date', col B = 'Expense' → valid
        ["2024-08-01", "Date", "Cash", "50", "Needs", ""], # col B = 'Date' → junk, skip
    ]
    txs = parse_rows(records)
    assert len(txs) == 1
    assert txs[0].category == "Needs"


# ── Phase 2 tests ─────────────────────────────────────────────────────────────

# Task 1: append-only writes ──────────────────────────────────────────────────

class FakeSheet:
    def __init__(self):
        self.appended_rows = []
        self.get_all_values_calls = 0

    def append_row(self, row, table_range=None):
        self.appended_rows.append(row)

    def append_rows(self, rows, table_range=None):
        self.appended_rows.extend(rows)

    def get_all_values(self):
        self.get_all_values_calls += 1
        return []

def test_insert_row_uses_append_not_index(monkeypatch):
    """_insert_row must call append_row, not compute a row index."""
    sheet = FakeSheet()
    monkeypatch.setattr(sheet, 'append_row', sheet.append_row)
    row = ["2024-01-01", "Expense", "Cash", "50", "Needs", "Groceries"]
    bot._insert_row(sheet, row)
    assert sheet.appended_rows == [row]

def test_insert_rows_uses_append_not_index(monkeypatch):
    """_insert_rows must call append_rows, not compute row indices."""
    sheet = FakeSheet()
    rows = [
        ["2024-01-01", "Expense", "Cash", "50", "Needs", "Groceries"],
        ["2024-01-01", "Income", "Cash", "50", "Savings", "Transfer"],
    ]
    bot._insert_rows(sheet, rows)
    assert sheet.appended_rows == rows

def test_insert_row_passes_table_range():
    """append_row must be called with table_range='A1' to always append."""
    calls = []
    sheet = FakeSheet()

    def recording_append_row(row, table_range=None):
        calls.append(table_range)
    sheet.append_row = recording_append_row

    bot._insert_row(sheet, ["2024-01-01", "Expense", "Cash", "10", "Needs", ""])
    assert calls == ["A1"]

# Task 2: safe AST evaluator ─────────────────────────────────────────────────

def test_safe_eval_basic_arithmetic():
    assert _safe_eval("2 + 3") == 5
    assert _safe_eval("10 - 4") == 6
    assert _safe_eval("3 * 7") == 21
    assert _safe_eval("20 / 4") == 5.0

def test_safe_eval_exponentiation():
    assert _safe_eval("2 ** 10") == 1024

def test_safe_eval_nested_parens():
    assert _safe_eval("(2 + 3) * 4") == 20

def test_safe_eval_float():
    assert abs(_safe_eval("1.5 * 2") - 3.0) < 1e-9

def test_safe_eval_rejects_huge_exponent():
    with pytest.raises((ValueError, OverflowError)):
        _safe_eval("9 ** 9 ** 9")

def test_safe_eval_rejects_dunder_access():
    with pytest.raises((ValueError, SyntaxError)):
        _safe_eval("().__class__")

def test_safe_eval_rejects_import():
    with pytest.raises((ValueError, SyntaxError)):
        _safe_eval("__import__('os')")

def test_safe_eval_division_by_zero_raises():
    with pytest.raises(ZeroDivisionError):
        _safe_eval("1 / 0")

def test_safe_eval_rejects_too_long_expression():
    with pytest.raises(ValueError, match="too long"):
        _safe_eval("1 + " * 55 + "1")  # 4*55+1 = 221 chars, over the 200-char limit

def test_safe_eval_rejects_string_literal():
    with pytest.raises(ValueError):
        _safe_eval("'hello'")

# Task 3: HTML escaping ───────────────────────────────────────────────────────

def test_html_escape_in_transfer_preview():
    """Account names with HTML special chars must be escaped in preview text."""
    account = "<Savings & Loan>"
    escaped = html.escape(account)
    preview = f"💸 <b>Transfer</b>\n  From: <b>{escaped}</b>\n  To: <b>Cash</b>"
    assert "<Savings & Loan>" not in preview
    assert "&lt;Savings &amp; Loan&gt;" in preview

def test_html_escape_in_confirmation():
    """Category names with special chars must be escaped in confirmation."""
    category = "Needs & Wants <high>"
    escaped = html.escape(category)
    msg = f"<b>Category:</b> {escaped}"
    assert "<high>" not in msg
    assert "&lt;high&gt;" in msg

def test_html_escape_ampersand():
    assert html.escape("Fish & Chips") == "Fish &amp; Chips"

def test_html_escape_angle_brackets():
    assert html.escape("<script>") == "&lt;script&gt;"


# ── Phase 3 tests ─────────────────────────────────────────────────────────────

import time as _time_mod
from finance.bot import _with_retry, _SHEET_CACHE_TTL, _LEDGER_CACHE_TTL, _RETRY_MAX_ATTEMPTS

# Task 3: retry helper ────────────────────────────────────────────────────────

class FakeAPIError(Exception):
    def __init__(self, status_code):
        self.response = type('R', (), {'status_code': status_code})()
        super().__init__(f"HTTP {status_code}")

@pytest.mark.asyncio
async def test_retry_read_succeeds_after_503():
    """Reads should retry on 503 and eventually succeed."""
    calls = []
    async def flaky():
        calls.append(1)
        if len(calls) < 3:
            raise FakeAPIError(503)
        return "ok"
    result = await _with_retry(flaky, is_write=False)
    assert result == "ok"
    assert len(calls) == 3

@pytest.mark.asyncio
async def test_retry_write_does_not_retry_on_503():
    """Writes must NOT retry on 503 — the write may have already applied."""
    calls = []
    async def flaky():
        calls.append(1)
        raise FakeAPIError(503)
    with pytest.raises(FakeAPIError):
        await _with_retry(flaky, is_write=True)
    assert len(calls) == 1

@pytest.mark.asyncio
async def test_retry_write_retries_on_429():
    """Writes should retry on 429 (rate-limited, not applied)."""
    calls = []
    async def flaky():
        calls.append(1)
        if len(calls) < 2:
            raise FakeAPIError(429)
        return "wrote"
    result = await _with_retry(flaky, is_write=True)
    assert result == "wrote"
    assert len(calls) == 2

@pytest.mark.asyncio
async def test_retry_gives_up_after_max_attempts():
    """Retry must not loop forever — gives up after _RETRY_MAX_ATTEMPTS."""
    calls = []
    async def always_fails():
        calls.append(1)
        raise FakeAPIError(503)
    with pytest.raises(FakeAPIError):
        await _with_retry(always_fails, is_write=False)
    assert len(calls) == _RETRY_MAX_ATTEMPTS

# Task 3: sheet cache ─────────────────────────────────────────────────────────

def make_bot_with_fake_client(sheet_obj):
    """Build a FinanceBot instance with a fake Google client."""
    b = FinanceBot.__new__(FinanceBot)
    b.strict_users = []
    b.user_mapping = {"99": "MySheet"}
    b._sheet_cache = {}
    b._ledger_cache = {}
    b.client = type('Client', (), {'open': lambda self, name: type('SS', (), {'sheet1': sheet_obj})()})()
    return b

def test_sheet_cache_returns_same_handle():
    sheet_obj = object()
    b = make_bot_with_fake_client(sheet_obj)
    s1 = b.get_user_sheet(99)
    s2 = b.get_user_sheet(99)
    assert s1 is s2

def test_sheet_cache_refetches_after_expiry(monkeypatch):
    fetches = []
    sentinel1 = object()
    sentinel2 = object()
    sheets = [sentinel1, sentinel2]

    class FakeClient:
        def open(self, name):
            fetches.append(name)
            return type('SS', (), {'sheet1': sheets.pop(0)})()

    b = FinanceBot.__new__(FinanceBot)
    b.strict_users = []
    b.user_mapping = {"99": "MySheet"}
    b._sheet_cache = {}
    b._ledger_cache = {}
    b.client = FakeClient()

    # First fetch
    s1 = b.get_user_sheet(99)
    # Expire the cache
    b._sheet_cache["MySheet"] = (s1, _time_mod.monotonic() - 1)
    # Second fetch should call open() again
    s2 = b.get_user_sheet(99)
    assert s1 is not s2
    assert len(fetches) == 2

# Task 4: ledger cache ────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_ledger_cache_avoids_second_network_call():
    """After a read, the same rows are returned without hitting the sheet again."""
    calls = []
    class FakeSheet:
        def get_all_values(self):
            calls.append(1)
            return [["2024-01-01", "Income", "Cash", "100", "Salary", ""]]

    b = FinanceBot.__new__(FinanceBot)
    b._ledger_cache = {}
    b._sheet_cache = {}
    sheet = FakeSheet()

    r1 = await b._get_all_values(sheet, "99")
    r2 = await b._get_all_values(sheet, "99")
    assert r1 == r2
    assert len(calls) == 1  # only one actual network call

@pytest.mark.asyncio
async def test_ledger_cache_invalidated_after_write():
    """After _invalidate_ledger_cache, the next read goes to the sheet."""
    calls = []
    class FakeSheet:
        def get_all_values(self):
            calls.append(1)
            return []
        def append_row(self, row, table_range=None):
            pass

    b = FinanceBot.__new__(FinanceBot)
    b._ledger_cache = {}
    b._sheet_cache = {}
    sheet = FakeSheet()

    await b._get_all_values(sheet, "99")
    b._invalidate_ledger_cache("99")
    await b._get_all_values(sheet, "99")
    assert len(calls) == 2  # fetched twice because cache was cleared
