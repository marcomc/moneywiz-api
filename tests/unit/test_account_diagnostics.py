import pytest

from moneywiz_api.database_accessor import DatabaseAccessor
from moneywiz_api.managers.account_manager import AccountManager
from moneywiz_api.model.account import CreditCardAccount


def account_row(**overrides):
    row = {
        "Z_ENT": 13,
        "ZOBJECTCREATIONDATE": 0.0,
        "ZGID": "account-1",
        "Z_PK": 1,
        "ZDISPLAYORDER": 1,
        "ZGROUPID": 1,
        "ZNAME": "Card",
        "ZCURRENCYNAME": "EUR",
        "ZOPENINGBALANCE": 0.0,
        "ZINFO": None,
        "ZUSER": 1,
        "ZSTATEMENTENDDAY": 15,
    }
    row.update(overrides)
    return row


class AccountAccessor:
    def __init__(self, rows):
        self.rows = rows

    def descendant_typenames(self, _roots):
        return ["CreditCardAccount"]

    def query_objects(self, _typenames):
        return self.rows

    def typename_for(self, _ent_id):
        return "CreditCardAccount"


def test_nullable_info_does_not_mask_complete_credit_card() -> None:
    manager = AccountManager()

    report = manager.load(AccountAccessor([account_row()]))

    assert report.complete
    assert manager.get(1).info is None
    assert manager.get(1).statement_day == 15


def test_missing_optional_info_column_is_nullable() -> None:
    manager = AccountManager()
    row = account_row()
    del row["ZINFO"]

    report = manager.load(AccountAccessor([row]))

    assert report.complete
    assert manager.get(1).info is None


def test_required_account_identity_remains_required() -> None:
    manager = AccountManager()

    report = manager.load(AccountAccessor([account_row(ZNAME=None)]))

    assert not report.complete
    assert report.skipped[0].error.value == "validation"
    assert report.skipped[0].exception_type == "AssertionError"


class StaticCursor:
    def __init__(self, row):
        self.row = row

    def execute(self, _query, _parameters):
        return self

    def fetchone(self):
        return self.row


class StaticConnection:
    def __init__(self, row):
        self.row = row

    def cursor(self):
        return StaticCursor(self.row)


def accessor_for(row):
    accessor = DatabaseAccessor.__new__(DatabaseAccessor)
    accessor._con = StaticConnection(row)
    return accessor


def test_public_accessor_validates_nullable_info_after_construction() -> None:
    account = accessor_for(account_row()).get_record(1, CreditCardAccount)
    account_by_gid = accessor_for(account_row()).get_record_by_gid(
        "account-1", CreditCardAccount
    )

    assert account.info is None
    assert account_by_gid.statement_day == 15


@pytest.mark.parametrize(
    "row",
    [
        account_row(ZNAME=None),
        account_row(ZSTATEMENTENDDAY=None),
    ],
)
@pytest.mark.parametrize(
    ("method_name", "lookup"),
    [("get_record", 1), ("get_record_by_gid", "account-1")],
)
def test_public_accessor_rejects_invalid_account_metadata(
    row, method_name, lookup
) -> None:
    with pytest.raises(AssertionError):
        getattr(accessor_for(row), method_name)(lookup, CreditCardAccount)


def test_public_accessor_missing_subclass_field_is_not_masked() -> None:
    row = account_row()
    del row["ZSTATEMENTENDDAY"]

    with pytest.raises(KeyError, match="ZSTATEMENTENDDAY"):
        accessor_for(row).get_record_by_gid("account-1", CreditCardAccount)
