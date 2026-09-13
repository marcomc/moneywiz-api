from contextlib import contextmanager
from decimal import Decimal
import json

import pytest

from moneywiz_api.managers.transaction_manager import TransactionManager
from moneywiz_api.managers.account_manager import AccountManager
from moneywiz_api.managers.category_manager import CategoryManager
from moneywiz_api.managers.investment_holding_manager import InvestmentHoldingManager
from moneywiz_api.managers.payee_manager import PayeeManager
from moneywiz_api.managers.tag_manager import TagManager
from moneywiz_api.model.account import CashAccount
from moneywiz_api.model.category import Category
from moneywiz_api.model.investment_holding import InvestmentHolding
from moneywiz_api.model.payee import Payee
from moneywiz_api.model.record import Record
from moneywiz_api.model.tag import Tag
from moneywiz_api.model.transaction import (
    DepositTransaction,
    InvestmentBuyTransaction,
    InvestmentExchangeTransaction,
    InvestmentSellTransaction,
    ReconcileTransaction,
    RefundTransaction,
    TransferBudgetTransaction,
    TransferDepositTransaction,
    TransferWithdrawTransaction,
    WithdrawTransaction,
)
from moneywiz_api.read_result import RelationshipLoadReport, RelationshipStorage
from moneywiz_api.schema_profile import SchemaProfile
from tests.unit.accessor_test_support import initialized_memory_accessor


INVALID_IDENTITIES = (True, 1.0, "PRIVATE_PAYLOAD", Decimal("1"))
PROFILE = SchemaProfile(
    "unsuffixed-investment-columns",
    "ZNUMBEROFSHARES",
    "ZNUMBEROFSHARES",
    "ZPRICEPERSHARE",
    "ZPRICEPERSHARE",
)


def record_row(**overrides):
    row = {
        "Z_ENT": 37,
        "ZOBJECTCREATIONDATE": 0.0,
        "ZGID": "record-1",
        "Z_PK": 1,
    }
    row.update(overrides)
    return row


def account_row(**overrides):
    row = {
        **record_row(Z_ENT=10, ZGID="account-1"),
        "ZDISPLAYORDER": 0,
        "ZGROUPID": 1,
        "ZNAME": "Cash",
        "ZCURRENCYNAME": "EUR",
        "ZOPENINGBALANCE": 0.0,
        "ZINFO": None,
        "ZUSER": 2,
    }
    row.update(overrides)
    return row


def payee_row(**overrides):
    row = {
        **record_row(Z_ENT=28, ZGID="payee-1"),
        "ZNAME5": "Payee",
        "ZUSER7": 2,
    }
    row.update(overrides)
    return row


def category_row(**overrides):
    row = {
        **record_row(Z_ENT=19, ZGID="category-1"),
        "ZNAME2": "Category",
        "ZPARENTCATEGORY": None,
        "ZTYPE2": 1,
        "ZUSER3": 2,
    }
    row.update(overrides)
    return row


def tag_row(**overrides):
    row = {
        **record_row(Z_ENT=35, ZGID="tag-1"),
        "ZNAME6": "Tag",
        "ZUSER8": 2,
    }
    row.update(overrides)
    return row


def holding_row(**overrides):
    row = {
        **record_row(Z_ENT=24, ZGID="holding-1"),
        "ZINVESTMENTACCOUNT": 2,
        "ZOPENNINGNUMBEROFSHARES": None,
        "ZNUMBEROFSHARES": 2.0,
        "ZPRICEPERSHARE": 10.0,
        "ZSYMBOL": "ACME",
        "ZHOLDINGTYPE": None,
        "ZDESC": "Acme",
        "ZISPRICEPERSHAREAVAILABLEONLINE": 0,
        "ZINVESTMENTOBJECTTYPE": 0,
        "ZCOSTBASISOFMISSINGOBSHARES": 0.0,
    }
    row.update(overrides)
    return row


def transaction_row(**overrides):
    row = {
        **record_row(Z_ENT=37, ZGID="transaction-1"),
        "ZRECONCILED": 0,
        "ZAMOUNT1": 10.0,
        "ZDESC2": "Transaction",
        "ZDATE1": 0.0,
        "ZNOTES1": None,
        "ZACCOUNT2": 2,
        "ZPAYEE2": None,
        "ZORIGINALCURRENCY": "EUR",
        "ZORIGINALAMOUNT": 10.0,
        "ZORIGINALEXCHANGERATE": 1.0,
    }
    row.update(overrides)
    return row


def exchange_row(**overrides):
    row = {
        **transaction_row(Z_ENT=38, ZAMOUNT1=0.0),
        "ZFROMINVESTMENTHOLDING": 3,
        "ZFROMSYMBOL": "AAA",
        "ZTOINVESTMENTHOLDING": 4,
        "ZTOSYMBOL": "BBB",
        "ZFROMNUMBEROFSHARES": -2.0,
        "ZTONUMBEROFSHARES": 1.0,
        "ZORIGINALFEE": 0.0,
        "ZORIGINALFEECURRENCY": "AAA",
    }
    row.update(overrides)
    return row


def buy_row(**overrides):
    row = {
        **transaction_row(Z_ENT=40, ZAMOUNT1=-20.0),
        "ZFEE2": 0.0,
        "ZINVESTMENTHOLDING": 3,
        "ZNUMBEROFSHARES1": 2.0,
        "ZPRICEPERSHARE1": 10.0,
    }
    row.update(overrides)
    return row


def sell_row(**overrides):
    row = {
        **buy_row(Z_ENT=41, ZAMOUNT1=20.0),
    }
    row.update(overrides)
    return row


def reconcile_row(**overrides):
    row = {
        **transaction_row(Z_ENT=42, ZAMOUNT1=0.0),
        "ZRECONCILEAMOUNT": 0.0,
        "ZRECONCILENUMBEROFSHARES": None,
    }
    row.update(overrides)
    return row


def refund_row(**overrides):
    row = {**transaction_row(Z_ENT=43), "ZPAYEE2": 5}
    row.update(overrides)
    return row


def transfer_budget_row(**overrides):
    row = transaction_row(Z_ENT=44)
    row.update(overrides)
    return row


def transfer_deposit_row(**overrides):
    row = {
        **transaction_row(Z_ENT=45),
        "ZSENDERACCOUNT": 3,
        "ZSENDERTRANSACTION": 4,
        "ZORIGINALSENDERAMOUNT": -10.0,
        "ZORIGINALSENDERCURRENCY": "EUR",
        "ZORIGINALFEE": None,
        "ZORIGINALFEECURRENCY": None,
    }
    row.update(overrides)
    return row


def transfer_withdraw_row(**overrides):
    row = {
        **transaction_row(Z_ENT=46, ZAMOUNT1=-10.0, ZORIGINALAMOUNT=-10.0),
        "ZRECIPIENTACCOUNT1": 3,
        "ZRECIPIENTTRANSACTION": 4,
        "ZORIGINALRECIPIENTAMOUNT": 10.0,
        "ZORIGINALRECIPIENTCURRENCY": "EUR",
        "ZORIGINALFEE": None,
        "ZORIGINALFEECURRENCY": None,
    }
    row.update(overrides)
    return row


def withdraw_row(**overrides):
    row = transaction_row(Z_ENT=47, ZAMOUNT1=-10.0, ZORIGINALAMOUNT=-10.0)
    row.update(overrides)
    return row


IDENTITY_FIELDS = (
    (Record, record_row, "Z_PK"),
    (Record, record_row, "Z_ENT"),
    (CashAccount, account_row, "ZGROUPID"),
    (CashAccount, account_row, "ZUSER"),
    (Payee, payee_row, "ZUSER7"),
    (Category, category_row, "ZPARENTCATEGORY"),
    (Category, category_row, "ZUSER3"),
    (Tag, tag_row, "ZUSER8"),
    (InvestmentHolding, holding_row, "ZINVESTMENTACCOUNT"),
    (DepositTransaction, transaction_row, "ZACCOUNT2"),
    (DepositTransaction, transaction_row, "ZPAYEE2"),
    (InvestmentExchangeTransaction, exchange_row, "ZACCOUNT2"),
    (InvestmentExchangeTransaction, exchange_row, "ZFROMINVESTMENTHOLDING"),
    (InvestmentExchangeTransaction, exchange_row, "ZTOINVESTMENTHOLDING"),
    (InvestmentBuyTransaction, buy_row, "ZACCOUNT2"),
    (InvestmentBuyTransaction, buy_row, "ZINVESTMENTHOLDING"),
    (InvestmentSellTransaction, sell_row, "ZACCOUNT2"),
    (InvestmentSellTransaction, sell_row, "ZINVESTMENTHOLDING"),
    (ReconcileTransaction, reconcile_row, "ZACCOUNT2"),
    (RefundTransaction, refund_row, "ZACCOUNT2"),
    (RefundTransaction, refund_row, "ZPAYEE2"),
    (TransferDepositTransaction, transfer_deposit_row, "ZACCOUNT2"),
    (TransferDepositTransaction, transfer_deposit_row, "ZSENDERACCOUNT"),
    (TransferDepositTransaction, transfer_deposit_row, "ZSENDERTRANSACTION"),
    (TransferWithdrawTransaction, transfer_withdraw_row, "ZACCOUNT2"),
    (TransferWithdrawTransaction, transfer_withdraw_row, "ZRECIPIENTACCOUNT1"),
    (TransferWithdrawTransaction, transfer_withdraw_row, "ZRECIPIENTTRANSACTION"),
    (WithdrawTransaction, withdraw_row, "ZACCOUNT2"),
    (WithdrawTransaction, withdraw_row, "ZPAYEE2"),
)


@pytest.mark.parametrize(("constructor", "row_factory", "field"), IDENTITY_FIELDS)
@pytest.mark.parametrize("invalid_identity", INVALID_IDENTITIES)
def test_model_identity_fields_reject_coerced_domains(
    constructor, row_factory, field, invalid_identity
) -> None:
    with pytest.raises(AssertionError, match="uncoerced integer"):
        constructor(row_factory(**{field: invalid_identity}))


@pytest.mark.parametrize(("constructor", "row_factory", "field"), IDENTITY_FIELDS)
def test_model_identity_fields_preserve_valid_integers(
    constructor, row_factory, field
) -> None:
    constructor(row_factory(**{field: 7}))


@pytest.mark.parametrize("gid", ["record-1", "Δοκιμή", "   ", "id:/?!"])
def test_record_gid_preserves_nonempty_exact_strings(gid) -> None:
    assert Record(record_row(ZGID=gid)).gid == gid


@pytest.mark.parametrize("gid", [None, "", b"PRIVATE_PAYLOAD", 1, True, object()])
def test_record_gid_refuses_empty_or_coerced_values_without_payload(gid) -> None:
    with pytest.raises(AssertionError) as error:
        Record(record_row(ZGID=gid))

    assert "PRIVATE_PAYLOAD" not in str(error.value)


@pytest.mark.parametrize(
    ("constructor", "row_factory", "field"),
    [
        (CashAccount, account_row, "ZNAME"),
        (Payee, payee_row, "ZNAME5"),
        (Category, category_row, "ZNAME2"),
        (Tag, tag_row, "ZNAME6"),
    ],
)
def test_native_named_entities_preserve_empty_strings_and_reject_blobs(
    constructor, row_factory, field
) -> None:
    assert constructor(row_factory(**{field: ""})).name == ""
    with pytest.raises(AssertionError, match="uncoerced string"):
        constructor(row_factory(**{field: b"PRIVATE_PAYLOAD"}))


def test_account_info_preserves_nullable_and_empty_text_contract() -> None:
    assert CashAccount(account_row(ZINFO=None)).info is None
    assert CashAccount(account_row(ZINFO="")).info == ""
    with pytest.raises(AssertionError, match="uncoerced string"):
        CashAccount(account_row(ZINFO=b"PRIVATE_PAYLOAD"))


@pytest.mark.parametrize(
    ("constructor", "row_factory", "field"),
    [
        (CashAccount, account_row, "ZGROUPID"),
        (CashAccount, account_row, "ZUSER"),
        (Payee, payee_row, "ZUSER7"),
        (Category, category_row, "ZPARENTCATEGORY"),
        (Tag, tag_row, "ZUSER8"),
        (InvestmentHolding, holding_row, "ZINVESTMENTACCOUNT"),
        (DepositTransaction, transaction_row, "ZACCOUNT2"),
        (DepositTransaction, transaction_row, "ZPAYEE2"),
    ],
)
def test_non_record_identity_fields_preserve_integer_zero(
    constructor, row_factory, field
) -> None:
    constructor(row_factory(**{field: 0}))


@pytest.mark.parametrize(
    ("constructor", "row_factory", "field"),
    [
        (Category, category_row, "ZPARENTCATEGORY"),
        (DepositTransaction, transaction_row, "ZPAYEE2"),
        (RefundTransaction, refund_row, "ZPAYEE2"),
        (WithdrawTransaction, withdraw_row, "ZPAYEE2"),
    ],
)
def test_optional_identity_fields_accept_none(constructor, row_factory, field) -> None:
    constructor(row_factory(**{field: None}))


TRANSACTION_ROWS = (
    (DepositTransaction, transaction_row),
    (InvestmentExchangeTransaction, exchange_row),
    (InvestmentBuyTransaction, buy_row),
    (InvestmentSellTransaction, sell_row),
    (ReconcileTransaction, reconcile_row),
    (RefundTransaction, refund_row),
    (TransferBudgetTransaction, transfer_budget_row),
    (TransferDepositTransaction, transfer_deposit_row),
    (TransferWithdrawTransaction, transfer_withdraw_row),
    (WithdrawTransaction, withdraw_row),
)


@pytest.mark.parametrize(("constructor", "row_factory"), TRANSACTION_ROWS)
@pytest.mark.parametrize(("raw_value", "expected"), [(0, False), (1, True)])
def test_reconciled_raw_integer_domain_is_preserved(
    constructor, row_factory, raw_value, expected
) -> None:
    assert constructor(row_factory(ZRECONCILED=raw_value)).reconciled is expected


@pytest.mark.parametrize(("constructor", "row_factory"), TRANSACTION_ROWS)
@pytest.mark.parametrize("raw_value", [None, True, 2, 1.0, "PRIVATE_PAYLOAD"])
def test_reconciled_refuses_non_binary_or_coerced_values(
    constructor, row_factory, raw_value
) -> None:
    with pytest.raises(AssertionError) as error:
        constructor(row_factory(ZRECONCILED=raw_value))
    assert "PRIVATE_PAYLOAD" not in str(error.value)


def test_manager_skips_invalid_reconciled_row_and_keeps_valid_row() -> None:
    rows = [
        transaction_row(ZRECONCILED=2),
        transaction_row(Z_PK=2, ZGID="transaction-2", ZRECONCILED=1),
    ]

    report = TransactionManager().load(TransactionAccessor(rows))

    assert report.source_ids == (1, 2)
    assert report.parsed_ids == (2,)
    assert report.skipped[0].error.value == "validation"
    assert report.status == "partial"


@pytest.mark.parametrize(("raw_value", "expected"), [(0, False), (1, True)])
def test_accessor_preserves_binary_reconciled_domain(raw_value, expected) -> None:
    accessor = initialized_memory_accessor(
        [transaction_row(ZRECONCILED=raw_value)],
        [(37, "DepositTransaction", 0)],
    )

    transaction = accessor.get_record(1, DepositTransaction)

    assert transaction.reconciled is expected


@pytest.mark.parametrize("raw_value", [None, 2, 1.0, "PRIVATE_PAYLOAD"])
def test_accessor_refuses_invalid_reconciled_domain(raw_value) -> None:
    accessor = initialized_memory_accessor(
        [transaction_row(ZRECONCILED=raw_value)],
        [(37, "DepositTransaction", 0)],
    )

    with pytest.raises(AssertionError):
        accessor.get_record(1, DepositTransaction)


def test_accessor_enforces_exact_string_gid_domain() -> None:
    accessor = initialized_memory_accessor(
        [record_row(ZGID=b"PRIVATE_PAYLOAD")],
        [(37, "Record", 0)],
    )

    with pytest.raises(AssertionError, match="uncoerced string"):
        accessor.get_record(1)
    accessor.close()


class TransactionAccessor:
    def __init__(self, rows):
        self.rows = rows

    @contextmanager
    def read_transaction(self):
        yield

    def descendant_typenames(self, _roots):
        return ["DepositTransaction"]

    def query_objects(self, _typenames):
        return self.rows

    def typename_for(self, ent_id):
        return {37: "DepositTransaction"}.get(ent_id)

    def read_category_assignments(self):
        return {}, RelationshipLoadReport(RelationshipStorage.ABSENT)

    def read_refund_maps(self):
        return {}, RelationshipLoadReport(RelationshipStorage.ABSENT)

    def read_tags_map(self):
        return {}, RelationshipLoadReport(RelationshipStorage.ABSENT)


class ManagerAccessor(TransactionAccessor):
    def __init__(self, rows, typenames):
        super().__init__(rows)
        self.typenames = typenames
        self.schema_profile = PROFILE

    def descendant_typenames(self, _roots):
        return list(self.typenames.values())

    def typename_for(self, ent_id):
        return self.typenames.get(ent_id)


@pytest.mark.parametrize(
    ("manager_type", "row_factory", "entity", "entity_id"),
    [
        (AccountManager, account_row, "CashAccount", 10),
        (PayeeManager, payee_row, "Payee", 28),
        (CategoryManager, category_row, "Category", 19),
        (InvestmentHoldingManager, holding_row, "InvestmentHolding", 24),
        (TransactionManager, transaction_row, "DepositTransaction", 37),
        (TagManager, tag_row, "Tag", 35),
    ],
)
def test_all_managers_skip_wrong_type_gid_and_keep_valid_row(
    manager_type, row_factory, entity, entity_id
) -> None:
    invalid = row_factory(ZGID=b"PRIVATE_PAYLOAD")
    valid = row_factory(Z_PK=2, ZGID="valid-2")

    report = manager_type().load(ManagerAccessor([invalid, valid], {entity_id: entity}))

    assert report.source_ids == (1, 2)
    assert report.parsed_ids == (2,)
    assert report.skipped[0].error.value == "validation"
    assert report.status == "partial"
    assert "PRIVATE_PAYLOAD" not in json.dumps(report.as_dict(), sort_keys=True)


@pytest.mark.parametrize(
    ("manager_type", "row_factory", "field", "entity", "entity_id"),
    [
        (AccountManager, account_row, "ZNAME", "CashAccount", 10),
        (PayeeManager, payee_row, "ZNAME5", "Payee", 28),
        (CategoryManager, category_row, "ZNAME2", "Category", 19),
        (TagManager, tag_row, "ZNAME6", "Tag", 35),
    ],
)
def test_named_entity_managers_report_blob_names_as_validation(
    manager_type, row_factory, field, entity, entity_id
) -> None:
    report = manager_type().load(
        ManagerAccessor(
            [
                row_factory(**{field: b"PRIVATE_PAYLOAD"}),
                row_factory(Z_PK=2, ZGID="valid-2", **{field: ""}),
            ],
            {entity_id: entity},
        )
    )

    assert report.source_count == 2
    assert report.parsed_ids == (2,)
    assert report.skipped[0].error.value == "validation"


@pytest.mark.parametrize(
    "invalid_row",
    [
        transaction_row(Z_PK="PRIVATE_PAYLOAD"),
        transaction_row(Z_ENT="PRIVATE_PAYLOAD"),
        transaction_row(ZACCOUNT2="PRIVATE_PAYLOAD"),
        transaction_row(ZRECONCILED="PRIVATE_PAYLOAD"),
    ],
)
def test_manager_diagnostics_do_not_publish_invalid_identity_payloads(
    invalid_row, caplog
) -> None:
    valid_row = transaction_row(Z_PK=2, ZGID="transaction-2")
    caplog.set_level("DEBUG")

    report = TransactionManager().load(TransactionAccessor([invalid_row, valid_row]))
    serialized = json.dumps(report.as_dict(), sort_keys=True)

    assert report.source_count == 2
    assert report.parsed_ids == (2,)
    assert len(report.skipped) == 1
    assert "PRIVATE_PAYLOAD" not in serialized
    assert "PRIVATE_PAYLOAD" not in caplog.text
