from datetime import datetime

from pathlib import Path


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


# Use repository test database for integration tests
TEST_DB_PATH = _resolve_test_db()

BALANCE_AS_OF_DATE = datetime(2023, 5, 19, 0, 0, 0)
CASH_BALANCES = [
    # (ACCOUNT_PK, BALANCE)
    (1001, -100.00),
    (1002, -202.33),
]

HOLDINGS_BALANCES = [
    # (ACCOUNT_PK, {
    #     HOLDINGS_PK: HOLDINGS_BALANCE)
    # })
    (
        2001,
        {
            3001: 15,
        },
    ),
]
