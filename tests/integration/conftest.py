from pathlib import Path

from moneywiz_api import MoneywizApi
from datetime import datetime


# Use repository test database for integration tests
TEST_DB_PATH = Path(__file__).resolve().parents[2] / "tests/test_db.sqlite"

moneywizApi = MoneywizApi(TEST_DB_PATH)

accessor = moneywizApi.accessor
account_manager = moneywizApi.account_manager
payee_manager = moneywizApi.payee_manager
category_manager = moneywizApi.category_manager
transaction_manager = moneywizApi.transaction_manager
investment_holding_manager = moneywizApi.investment_holding_manager

# Optional integration datasets; empty by default for generic test DB
CASH_BALANCES = []
HOLDINGS_BALANCES = []
BALANCE_AS_OF_DATE = datetime(2025, 1, 1, 0, 0, 0)
