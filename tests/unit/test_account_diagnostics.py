import pytest

from moneywiz_api.managers.account_manager import AccountManager
from moneywiz_api.model.account import (
    Account,
    BankChequeAccount,
    BankSavingAccount,
    CashAccount,
    CreditCardAccount,
    ForexAccount,
    InvestmentAccount,
    LoanAccount,
)
from tests.unit.accessor_test_support import initialized_memory_accessor


ACCOUNT_CONSTRUCTORS = (
    Account,
    BankChequeAccount,
    BankSavingAccount,
    CashAccount,
    CreditCardAccount,
    LoanAccount,
    InvestmentAccount,
    ForexAccount,
)


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


@pytest.mark.parametrize("constructor", ACCOUNT_CONSTRUCTORS)
@pytest.mark.parametrize("info_present", [True, False])
def test_direct_account_constructors_accept_nullable_or_absent_info(
    constructor, info_present
) -> None:
    row = account_row()
    if not info_present:
        del row["ZINFO"]

    account = constructor(row)

    assert account.info is None
    assert account.name == "Card"


@pytest.mark.parametrize("constructor", ACCOUNT_CONSTRUCTORS)
def test_direct_account_constructors_reject_missing_required_name(constructor) -> None:
    with pytest.raises(AssertionError, match="account name is required"):
        constructor(account_row(ZNAME=None))


@pytest.mark.parametrize("constructor", [CreditCardAccount, LoanAccount])
def test_direct_credit_constructors_reject_null_statement_day(constructor) -> None:
    with pytest.raises(AssertionError, match="account statement day is required"):
        constructor(account_row(ZSTATEMENTENDDAY=None))


@pytest.mark.parametrize("constructor", [CreditCardAccount, LoanAccount])
def test_direct_credit_constructors_preserve_missing_statement_key(constructor) -> None:
    row = account_row()
    del row["ZSTATEMENTENDDAY"]

    with pytest.raises(KeyError, match="ZSTATEMENTENDDAY"):
        constructor(row)


@pytest.mark.parametrize(
    ("field", "value", "exception"),
    [
        ("ZGID", "", AssertionError),
        ("Z_PK", 0, AssertionError),
        ("ZGID", None, AssertionError),
    ],
)
def test_direct_account_preserves_record_identity_validation(
    field, value, exception
) -> None:
    with pytest.raises(exception):
        CashAccount(account_row(**{field: value}))


def test_direct_account_preserves_missing_record_identity_field() -> None:
    row = account_row()
    del row["ZGID"]

    with pytest.raises(KeyError, match="ZGID"):
        CashAccount(row)


def test_direct_account_validation_does_not_serialize_source(monkeypatch) -> None:
    def reject_serialization(_self):
        raise RuntimeError("PRIVATE_PAYLOAD_SERIALIZED")

    monkeypatch.setattr(CashAccount, "as_dict", reject_serialization)
    row = account_row(ZNAME=None, ZINFO="PRIVATE_PAYLOAD")

    with pytest.raises(AssertionError, match="account name is required") as error:
        CashAccount(row)

    assert "PRIVATE_PAYLOAD" not in str(error.value)


def accessor_for(row):
    return initialized_memory_accessor([row], [(13, "CreditCardAccount", 0)])


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
