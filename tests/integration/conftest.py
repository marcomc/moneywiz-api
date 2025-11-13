from pathlib import Path

from moneywiz_api import MoneywizApi
from datetime import datetime


def _resolve_test_db() -> Path:
    base = Path(__file__).resolve()
    candidates = [base.parents[2]]
    if len(base.parents) > 3:
        candidates.append(base.parents[3])
    for root in candidates:
        candidate = root / "tests/test_db.sqlite"
        if candidate.exists():
            return candidate
    joined = ", ".join(str(root / "tests/test_db.sqlite") for root in candidates)
    raise FileNotFoundError(
        f"tests/test_db.sqlite not found in any of: {joined}."
        " Please place a MoneyWiz fixture at tests/test_db.sqlite."
    )


TEST_DB_PATH = _resolve_test_db()

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
