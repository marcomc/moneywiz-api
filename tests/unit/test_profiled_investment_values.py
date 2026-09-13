from contextlib import contextmanager
from decimal import Decimal

import pytest

from moneywiz_api.managers.investment_holding_manager import InvestmentHoldingManager
from moneywiz_api.managers.transaction_manager import TransactionManager
from moneywiz_api.model.investment_holding import InvestmentHolding
from moneywiz_api.model.transaction import (
    InvestmentBuyTransaction,
    InvestmentSellTransaction,
)
from moneywiz_api.schema_profile import SchemaProfile
from moneywiz_api.read_result import RelationshipLoadReport, RelationshipStorage
from tests.unit.accessor_test_support import initialized_memory_accessor


UNSUFFIXED_PROFILE = SchemaProfile(
    profile_id="unsuffixed-investment-columns",
    holding_number_of_shares_column="ZNUMBEROFSHARES",
    transaction_number_of_shares_column="ZNUMBEROFSHARES",
    holding_price_per_share_column="ZPRICEPERSHARE",
    transaction_price_per_share_column="ZPRICEPERSHARE",
)
MIXED_PROFILE = SchemaProfile(
    profile_id="mixed-investment-columns",
    holding_number_of_shares_column="ZNUMBEROFSHARES",
    transaction_number_of_shares_column="ZNUMBEROFSHARES1",
    holding_price_per_share_column="ZPRICEPERSHARE",
    transaction_price_per_share_column="ZPRICEPERSHARE1",
)
UNKNOWN_PROFILE = SchemaProfile("unknown", None, None, None, None)


def investment_transaction_row(ent: int, amount: float) -> dict:
    return {
        "Z_ENT": ent,
        "ZOBJECTCREATIONDATE": 0.0,
        "ZGID": f"investment-{ent}",
        "Z_PK": ent,
        "ZRECONCILED": 0,
        "ZAMOUNT1": amount,
        "ZDESC2": "Investment",
        "ZDATE1": 0.0,
        "ZNOTES1": None,
        "ZACCOUNT2": 1,
        "ZFEE2": 0.0,
        "ZINVESTMENTHOLDING": 2,
        "ZNUMBEROFSHARES": 2.0,
        "ZNUMBEROFSHARES1": 9.0,
        "ZPRICEPERSHARE": 10.0,
        "ZPRICEPERSHARE1": 1.0,
    }


def investment_holding_row() -> dict:
    return {
        "Z_ENT": 24,
        "ZOBJECTCREATIONDATE": 0.0,
        "ZGID": "holding",
        "Z_PK": 24,
        "ZINVESTMENTACCOUNT": 1,
        "ZOPENNINGNUMBEROFSHARES": None,
        "ZNUMBEROFSHARES": 2.0,
        "ZNUMBEROFSHARES1": 9.0,
        "ZPRICEPERSHARE": 10.0,
        "ZPRICEPERSHARE1": 1.0,
        "ZSYMBOL": "ACME",
        "ZHOLDINGTYPE": None,
        "ZDESC": "Acme Corp.",
        "ZISPRICEPERSHAREAVAILABLEONLINE": 0,
        "ZINVESTMENTOBJECTTYPE": 0,
        "ZCOSTBASISOFMISSINGOBSHARES": 0.0,
    }


@pytest.mark.parametrize(
    ("constructor", "row"),
    [
        (InvestmentBuyTransaction, investment_transaction_row(40, -20.0)),
        (InvestmentSellTransaction, investment_transaction_row(41, 20.0)),
    ],
)
def test_transactions_use_selected_profile_alias(constructor, row) -> None:
    transaction = constructor(row, schema_profile=UNSUFFIXED_PROFILE)

    assert transaction.number_of_shares == Decimal("2.0")
    assert transaction.price_per_share == Decimal("10.0")


def test_holding_uses_selected_profile_alias() -> None:
    holding = InvestmentHolding(investment_holding_row(), UNSUFFIXED_PROFILE)

    assert holding.number_of_shares == Decimal("2.0")
    assert holding.price_per_share == Decimal("10.0")


@pytest.mark.parametrize(
    ("raw_value", "expected"),
    [(0, False), (1, True)],
)
def test_holding_preserves_binary_online_price_domain(raw_value, expected) -> None:
    row = investment_holding_row()
    row["ZISPRICEPERSHAREAVAILABLEONLINE"] = raw_value

    assert (
        InvestmentHolding(row, UNSUFFIXED_PROFILE).price_per_share_available_online
        is expected
    )


@pytest.mark.parametrize(
    "raw_value",
    [None, False, True, -1, 2, 1.0, "PRIVATE_PAYLOAD"],
)
def test_holding_rejects_non_binary_or_coerced_online_price_state(
    raw_value,
) -> None:
    row = investment_holding_row()
    row["ZISPRICEPERSHAREAVAILABLEONLINE"] = raw_value

    with pytest.raises(AssertionError) as error:
        InvestmentHolding(row, UNSUFFIXED_PROFILE)

    assert "PRIVATE_PAYLOAD" not in str(error.value)


def test_holding_manager_reports_invalid_online_price_state_as_incomplete() -> None:
    invalid = investment_holding_row()
    invalid["ZISPRICEPERSHAREAVAILABLEONLINE"] = 2
    valid = investment_holding_row()
    valid.update(
        {
            "Z_PK": 25,
            "ZGID": "holding-valid",
            "ZISPRICEPERSHAREAVAILABLEONLINE": 1,
        }
    )

    manager = InvestmentHoldingManager()
    report = manager.load(ManagerAccessor("InvestmentHolding", invalid))

    assert not report.complete
    assert report.parsed_ids == ()
    assert report.skipped[0].error.value == "validation"
    assert manager.records() == {}

    report = manager.load(ManagerAccessor("InvestmentHolding", valid))

    assert report.complete
    assert manager.get(25).price_per_share_available_online is True


@pytest.mark.parametrize(
    ("constructor", "transaction_row"),
    [
        (InvestmentBuyTransaction, investment_transaction_row(40, -9.0)),
        (InvestmentSellTransaction, investment_transaction_row(41, 9.0)),
    ],
)
def test_mixed_profile_uses_consumer_specific_share_aliases(
    constructor, transaction_row
) -> None:
    holding_row = investment_holding_row()

    transaction = constructor(transaction_row, MIXED_PROFILE)
    holding = InvestmentHolding(holding_row, MIXED_PROFILE)

    assert transaction.number_of_shares == Decimal("9.0")
    assert transaction.price_per_share == Decimal("1.0")
    assert holding.number_of_shares == Decimal("2.0")
    assert holding.price_per_share == Decimal("10.0")


class ProfileAccessor:
    schema_profile = UNSUFFIXED_PROFILE


def test_managers_pass_profile_to_investment_constructors() -> None:
    accessor = ProfileAccessor()
    transaction = TransactionManager().construct_record(
        InvestmentBuyTransaction, investment_transaction_row(40, -20.0), accessor
    )
    holding = InvestmentHoldingManager().construct_record(
        InvestmentHolding, investment_holding_row(), accessor
    )

    assert transaction.price_per_share == Decimal("10.0")
    assert holding.number_of_shares == Decimal("2.0")


class ManagerAccessor(ProfileAccessor):
    def __init__(self, typename: str, row: dict):
        self.typename = typename
        self.row = row

    @contextmanager
    def read_transaction(self):
        yield

    def query_objects(self, _typenames):
        return [self.row]

    def descendant_typenames(self, _roots):
        return [self.typename]

    def typename_for(self, _ent_id):
        return self.typename

    def read_category_assignments(self):
        return {}, RelationshipLoadReport(RelationshipStorage.ABSENT)

    def read_refund_maps(self):
        return {}, RelationshipLoadReport(RelationshipStorage.ABSENT)

    def read_tags_map(self):
        return {}, RelationshipLoadReport(RelationshipStorage.ABSENT)


def test_managers_load_profiled_investment_records() -> None:
    transaction_manager = TransactionManager()
    transaction_manager.load(
        ManagerAccessor(
            "InvestmentBuyTransaction", investment_transaction_row(40, -20.0)
        )
    )
    holding_manager = InvestmentHoldingManager()
    holding_manager.load(ManagerAccessor("InvestmentHolding", investment_holding_row()))

    assert transaction_manager.get(40).price_per_share == Decimal("10.0")
    assert holding_manager.get(24).number_of_shares == Decimal("2.0")


def test_accessor_public_constructors_receive_schema_profile() -> None:
    transaction_row = investment_transaction_row(40, -20.0)
    transaction_row.pop("ZNUMBEROFSHARES1")
    transaction_row.pop("ZPRICEPERSHARE1")
    holding_row = investment_holding_row()
    holding_row.pop("ZNUMBEROFSHARES1")
    holding_row.pop("ZPRICEPERSHARE1")
    transaction_accessor = initialized_memory_accessor(
        [transaction_row],
        [(40, "InvestmentBuyTransaction", 0)],
    )
    holding_accessor = initialized_memory_accessor(
        [holding_row],
        [(24, "InvestmentHolding", 0)],
    )

    transaction = transaction_accessor.get_record(40, InvestmentBuyTransaction)
    holding = holding_accessor.get_record_by_gid("holding", InvestmentHolding)

    assert transaction.price_per_share == Decimal("10.0")
    assert holding.number_of_shares == Decimal("2.0")


@pytest.mark.parametrize(
    ("constructor", "row"),
    [
        (InvestmentBuyTransaction, investment_transaction_row(40, -20.0)),
        (InvestmentSellTransaction, investment_transaction_row(41, 20.0)),
        (InvestmentHolding, investment_holding_row()),
    ],
)
def test_investment_models_reject_ambiguous_profile(constructor, row) -> None:
    with pytest.raises(ValueError, match="unsupported investment schema profile"):
        constructor(row, UNKNOWN_PROFILE)
