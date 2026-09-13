"""Emit runtime-validation behavior for normal and optimized Python processes."""

from __future__ import annotations

import json
import sqlite3
import sys
import tempfile
from contextlib import contextmanager
from decimal import Decimal
from pathlib import Path

from moneywiz_api.database_accessor import DatabaseAccessor
from moneywiz_api.managers.category_manager import CategoryManager
from moneywiz_api.managers.payee_manager import PayeeManager
from moneywiz_api.managers.transaction_manager import TransactionManager
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
from moneywiz_api.model.category import Category
from moneywiz_api.model.investment_holding import InvestmentHolding
from moneywiz_api.model.payee import Payee
from moneywiz_api.model.raw_data_handler import RawDataHandler
from moneywiz_api.model.record import Record
from moneywiz_api.model.tag import Tag
from moneywiz_api.model.transaction import (
    DepositTransaction,
    InvestmentBuyTransaction,
    TransferWithdrawTransaction,
    WithdrawTransaction,
)
from moneywiz_api.schema_profile import SchemaProfile
from accessor_test_support import initialized_memory_accessor


PROFILE = SchemaProfile(
    "unsuffixed-investment-columns",
    "ZNUMBEROFSHARES",
    "ZNUMBEROFSHARES",
    "ZPRICEPERSHARE",
    "ZPRICEPERSHARE",
)


def normalize(value):
    if isinstance(value, Decimal):
        return str(value)
    if hasattr(value, "isoformat"):
        return value.isoformat()
    if isinstance(value, dict):
        return {str(key): normalize(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [normalize(item) for item in value]
    return value


def capture(operation):
    try:
        value = operation()
    except Exception as exc:
        return {
            "status": "error",
            "type": type(exc).__name__,
            "message": str(exc),
        }
    return {"status": "ok", "value": normalize(value)}


def common_row(**overrides):
    row = {
        "Z_ENT": 1,
        "ZOBJECTCREATIONDATE": 0.0,
        "ZGID": "record-1",
        "Z_PK": 1,
    }
    row.update(overrides)
    return row


def account_row(**overrides):
    row = {
        **common_row(Z_ENT=13, ZGID="account-1"),
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


def transaction_row(**overrides):
    row = {
        **common_row(Z_ENT=37, ZGID="transaction-1"),
        "ZRECONCILED": 0,
        "ZAMOUNT1": 20.0,
        "ZDESC2": "Transaction",
        "ZDATE1": 0.0,
        "ZNOTES1": None,
        "ZACCOUNT2": 1,
        "ZPAYEE2": None,
        "ZORIGINALCURRENCY": "EUR",
        "ZORIGINALAMOUNT": 10.0,
        "ZORIGINALEXCHANGERATE": 2.0,
    }
    row.update(overrides)
    return row


def holding_row(**overrides):
    row = {
        **common_row(Z_ENT=24, ZGID="holding-1"),
        "ZINVESTMENTACCOUNT": 1,
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


def buy_row(**overrides):
    row = {
        **transaction_row(Z_ENT=40, ZGID="buy-1", ZAMOUNT1=-21.0),
        "ZFEE2": 1.0,
        "ZINVESTMENTHOLDING": 2,
        "ZNUMBEROFSHARES": 2.0,
        "ZPRICEPERSHARE": 10.0,
    }
    row.update(overrides)
    return row


def transfer_withdraw_row(**overrides):
    row = {
        **transaction_row(Z_ENT=46, ZGID="transfer-withdraw-1", ZAMOUNT1=-10.0),
        "ZRECIPIENTACCOUNT1": 2,
        "ZRECIPIENTTRANSACTION": 3,
        "ZORIGINALAMOUNT": 0.0,
        "ZORIGINALRECIPIENTAMOUNT": 10.0,
        "ZORIGINALRECIPIENTCURRENCY": "USD",
        "ZORIGINALFEE": None,
        "ZORIGINALFEECURRENCY": None,
        "ZORIGINALEXCHANGERATE": 0.0,
    }
    row.update(overrides)
    return row


def accessor_for(row):
    return initialized_memory_accessor([row], [(13, "CreditCardAccount", 0)])


class PayeeAccessor:
    @contextmanager
    def read_transaction(self):
        yield

    def descendant_typenames(self, _roots):
        return ["Payee"]

    def query_objects(self, _typenames):
        return [
            {
                **common_row(Z_ENT=28, ZGID="payee-valid", Z_PK=10),
                "ZNAME5": "A",
                "ZUSER7": 1,
            },
            {
                **common_row(Z_ENT=28, ZGID="payee-invalid", Z_PK=11),
                "ZNAME5": "B",
                "ZUSER7": None,
            },
            {
                **common_row(
                    Z_ENT=28,
                    ZGID="payee-invalid-identity",
                    Z_PK="PRIVATE_PAYLOAD",
                ),
                "ZNAME5": "C",
                "ZUSER7": 1,
            },
        ]

    def typename_for(self, _ent_id):
        return "Payee"


class CategoryAccessor:
    def __init__(self, rows):
        self.rows = rows

    @contextmanager
    def read_transaction(self):
        yield

    def descendant_typenames(self, _roots):
        return ["Category"]

    def query_objects(self, _typenames):
        return self.rows

    def typename_for(self, _ent_id):
        return "Category"


def direct_account_probe():
    constructors = (
        Account,
        BankChequeAccount,
        BankSavingAccount,
        CashAccount,
        CreditCardAccount,
        LoanAccount,
        InvestmentAccount,
        ForexAccount,
    )
    return {
        constructor.__name__: {
            "valid": capture(
                lambda constructor=constructor: constructor(account_row()).info
            ),
            "invalid_name": capture(
                lambda constructor=constructor: constructor(account_row(ZNAME=None))
            ),
            "invalid_statement_day": (
                capture(
                    lambda constructor=constructor: constructor(
                        account_row(ZSTATEMENTENDDAY=None)
                    )
                )
                if issubclass(constructor, CreditCardAccount)
                else None
            ),
        }
        for constructor in constructors
    }


def category_type_probe():
    def row(value=1, **overrides):
        category = {
            **common_row(Z_ENT=19),
            "ZNAME2": "Category",
            "ZPARENTCATEGORY": None,
            "ZTYPE2": value,
            "ZUSER3": 1,
        }
        category.update(overrides)
        return category

    manager_reports = {}
    for label, value in (
        ("null", None),
        ("zero", 0),
        ("integer", 3),
        ("text", "PRIVATE_PAYLOAD"),
    ):
        report = CategoryManager().load(CategoryAccessor([row(value)]))
        manager_reports[label] = report.as_dict()
    missing = row()
    del missing["ZTYPE2"]
    missing_report = CategoryManager().load(CategoryAccessor([missing]))
    owner_report = CategoryManager().load(CategoryAccessor([row(ZUSER3=None)]))

    def unexpected_constructor(_row):
        raise RuntimeError("synthetic unexpected constructor failure")

    class UnexpectedCategoryManager(CategoryManager):
        @property
        def ents(self):
            return {"Category": unexpected_constructor}

    unexpected_report = UnexpectedCategoryManager().load(CategoryAccessor([row()]))
    return {
        "supported": {
            "1": capture(lambda: Category(row(1)).type),
            "2": capture(lambda: Category(row(2)).type),
        },
        "unsupported": manager_reports,
        "missing": missing_report.as_dict(),
        "invalid_owner": owner_report.as_dict(),
        "unexpected": unexpected_report.as_dict(),
    }


def named_entities_probe():
    rows = {
        "payee_valid": (
            Payee,
            {**common_row(Z_ENT=28), "ZNAME5": "Payee", "ZUSER7": 1},
        ),
        "payee_invalid": (
            Payee,
            {**common_row(Z_ENT=28), "ZNAME5": "Payee", "ZUSER7": None},
        ),
        "payee_blob_name": (
            Payee,
            {**common_row(Z_ENT=28), "ZNAME5": b"PRIVATE_PAYLOAD", "ZUSER7": 1},
        ),
        "tag_valid": (
            Tag,
            {**common_row(Z_ENT=35), "ZNAME6": "Tag", "ZUSER8": 1},
        ),
        "tag_invalid": (
            Tag,
            {**common_row(Z_ENT=35), "ZNAME6": "Tag", "ZUSER8": None},
        ),
        "tag_blob_name": (
            Tag,
            {**common_row(Z_ENT=35), "ZNAME6": b"PRIVATE_PAYLOAD", "ZUSER8": 1},
        ),
        "category_valid": (
            Category,
            {
                **common_row(Z_ENT=19),
                "ZNAME2": "Category",
                "ZPARENTCATEGORY": None,
                "ZTYPE2": 1,
                "ZUSER3": 1,
            },
        ),
        "category_invalid": (
            Category,
            {
                **common_row(Z_ENT=19),
                "ZNAME2": "Category",
                "ZPARENTCATEGORY": None,
                "ZTYPE2": 1,
                "ZUSER3": None,
            },
        ),
        "category_blob_name": (
            Category,
            {
                **common_row(Z_ENT=19),
                "ZNAME2": b"PRIVATE_PAYLOAD",
                "ZPARENTCATEGORY": None,
                "ZTYPE2": 1,
                "ZUSER3": 1,
            },
        ),
    }
    return {
        name: capture(lambda constructor=constructor, row=row: constructor(row).name)
        for name, (constructor, row) in rows.items()
    }


def relationship_probe():
    with tempfile.TemporaryDirectory() as directory:
        path = Path(directory) / "relationships.sqlite"
        connection = sqlite3.connect(path)
        connection.execute(
            "CREATE TABLE Z_PRIMARYKEY "
            "(Z_ENT INTEGER PRIMARY KEY, Z_NAME TEXT, Z_SUPER INTEGER, Z_MAX INTEGER)"
        )
        connection.execute("CREATE TABLE ZSYNCOBJECT (Z_PK INTEGER, Z_ENT INTEGER)")
        connection.executemany(
            "INSERT INTO Z_PRIMARYKEY VALUES (?, ?, ?, ?)",
            [
                (2, "CategoryAssigment", 0, 0),
                (36, "Tag", 0, 0),
                (37, "Transaction", 0, 0),
                (50, "WithdrawRefundTransactionLink", 0, 0),
            ],
        )
        connection.execute(
            "CREATE TABLE ZCATEGORYASSIGMENT "
            "(Z_PK INTEGER, ZCATEGORY INTEGER, ZTRANSACTION INTEGER, ZAMOUNT)"
        )
        connection.executemany(
            "INSERT INTO ZCATEGORYASSIGMENT VALUES (?, ?, ?, ?)",
            [
                (1, 10, 100, 5.0),
                (2, None, 101, 2.0),
                (3, 12, 102, "PRIVATE_PAYLOAD"),
                (4, 13, None, 4.0),
                (5, "PRIVATE_PAYLOAD", 103, 1.0),
                ("PRIVATE_PAYLOAD", 14, 104, 1.0),
            ],
        )
        connection.execute(
            "CREATE TABLE ZWITHDRAWREFUNDTRANSACTIONLINK "
            "(Z_PK INTEGER, ZREFUNDTRANSACTION INTEGER, ZWITHDRAWTRANSACTION INTEGER)"
        )
        connection.executemany(
            "INSERT INTO ZWITHDRAWREFUNDTRANSACTIONLINK VALUES (?, ?, ?)",
            [
                (5, 103, 104),
                (6, 105, None),
                (7, "PRIVATE_PAYLOAD", 106),
                ("PRIVATE_PAYLOAD", 107, 108),
            ],
        )
        connection.execute(
            "CREATE TABLE Z_37TAGS (Z_37TRANSACTIONS INTEGER, Z_36TAGS INTEGER)"
        )
        connection.executemany(
            "INSERT INTO Z_37TAGS VALUES (?, ?)",
            [(100, 200), (101, None), ("PRIVATE_PAYLOAD", 201)],
        )
        connection.commit()
        connection.close()

        with DatabaseAccessor(path) as accessor:
            categories, category_report = accessor.read_category_assignments()
            refunds, refund_report = accessor.read_refund_maps()
            tags, tag_report = accessor.read_tags_map()
            aggregate_report = TransactionManager().load(accessor)
        return normalize(
            {
                "categories": categories,
                "category_report": category_report.as_dict(),
                "refunds": refunds,
                "refund_report": refund_report.as_dict(),
                "tags": tags,
                "tag_report": tag_report.as_dict(),
                "aggregate": aggregate_report.as_dict(),
            }
        )


def sentinel_probe():
    original_payee_as_dict = Payee.as_dict
    original_deposit_as_dict = DepositTransaction.as_dict
    original_account_as_dict = CashAccount.as_dict

    def reject_serialization(_self):
        raise RuntimeError("PRIVATE_PAYLOAD_SERIALIZED")

    Payee.as_dict = reject_serialization
    DepositTransaction.as_dict = reject_serialization
    CashAccount.as_dict = reject_serialization
    try:
        payee = capture(
            lambda: Payee(
                {
                    **common_row(Z_ENT=28),
                    "ZNAME5": "Payee",
                    "ZUSER7": None,
                }
            )
        )
        deposit = capture(lambda: DepositTransaction(transaction_row(ZACCOUNT2=None)))
        account = capture(lambda: CashAccount(account_row(ZNAME=None)))
    finally:
        Payee.as_dict = original_payee_as_dict
        DepositTransaction.as_dict = original_deposit_as_dict
        CashAccount.as_dict = original_account_as_dict
    return {"payee": payee, "deposit": deposit, "account": account}


def main():
    manager = PayeeManager()
    manager_report = manager.load(PayeeAccessor())
    valid_account = accessor_for(account_row()).get_record(1, CreditCardAccount)
    valid_deposit = DepositTransaction(transaction_row())
    zero_rate_deposit = DepositTransaction(
        transaction_row(ZAMOUNT1=10.0, ZORIGINALAMOUNT=10.0, ZORIGINALEXCHANGERATE=0.0)
    )
    corrected_withdrawal = WithdrawTransaction(
        transaction_row(
            Z_ENT=47,
            ZGID="withdraw-1",
            ZAMOUNT1=-10.0,
            ZORIGINALAMOUNT=10.0,
            ZORIGINALEXCHANGERATE=0.0,
        )
    )
    output = {
        "optimize": sys.flags.optimize,
        "cases": {
            "decimal_valid": capture(
                lambda: RawDataHandler.get_decimal({"value": 1.25}, "value")
            ),
            "decimal_string": capture(
                lambda: RawDataHandler.get_decimal(
                    {"value": "PRIVATE_PAYLOAD"}, "value"
                )
            ),
            "date_valid": capture(
                lambda: RawDataHandler.get_datetime({"value": 0.0}, "value")
            ),
            "date_string": capture(
                lambda: RawDataHandler.get_datetime(
                    {"value": "PRIVATE_PAYLOAD"}, "value"
                )
            ),
            "record_valid": capture(lambda: Record(common_row()).gid),
            "record_invalid_identity": capture(lambda: Record(common_row(ZGID=""))),
            "record_coerced_gid": capture(
                lambda: Record(common_row(ZGID=b"PRIVATE_PAYLOAD"))
            ),
            "record_coerced_identity": capture(lambda: Record(common_row(Z_PK=True))),
            "account_nullable_info": capture(
                lambda: {
                    "info": valid_account.info,
                    "statement_day": valid_account.statement_day,
                }
            ),
            "account_invalid_name_public": capture(
                lambda: accessor_for(account_row(ZNAME=None)).get_record_by_gid(
                    "account-1", CreditCardAccount
                )
            ),
            "account_blob_name": capture(
                lambda: CashAccount(account_row(ZNAME=b"PRIVATE_PAYLOAD"))
            ),
            "account_blob_info": capture(
                lambda: CashAccount(account_row(ZINFO=b"PRIVATE_PAYLOAD"))
            ),
            "direct_accounts": direct_account_probe(),
            "category_types": category_type_probe(),
            "named_entities": named_entities_probe(),
            "manager": manager_report.as_dict(),
            "holding_valid": capture(
                lambda: InvestmentHolding(holding_row(), PROFILE).number_of_shares
            ),
            "holding_missing_quantity": capture(
                lambda: InvestmentHolding(holding_row(ZNUMBEROFSHARES=None), PROFILE)
            ),
            "deposit_valid": capture(
                lambda: {
                    "amount": valid_deposit.amount,
                    "rate": valid_deposit.original_exchange_rate,
                }
            ),
            "deposit_invalid_sign": capture(
                lambda: DepositTransaction(transaction_row(ZORIGINALAMOUNT=-10.0))
            ),
            "deposit_coerced_account": capture(
                lambda: DepositTransaction(transaction_row(ZACCOUNT2="PRIVATE_PAYLOAD"))
            ),
            "reconciled": {
                label: capture(
                    lambda value=value: (
                        DepositTransaction(
                            transaction_row(ZRECONCILED=value)
                        ).reconciled
                    )
                )
                for label, value in (
                    ("none", None),
                    ("false_boolean", False),
                    ("true_boolean", True),
                    ("zero", 0),
                    ("one", 1),
                    ("two", 2),
                    ("float", 1.0),
                    ("text", "PRIVATE_PAYLOAD"),
                )
            },
            "deposit_invalid_fx": capture(
                lambda: DepositTransaction(transaction_row(ZAMOUNT1=20.01))
            ),
            "deposit_zero_rate": capture(
                lambda: zero_rate_deposit.original_exchange_rate
            ),
            "investment_buy_valid_fee": capture(
                lambda: {
                    "amount": InvestmentBuyTransaction(buy_row(), PROFILE).amount,
                    "fee": InvestmentBuyTransaction(buy_row(), PROFILE).fee,
                }
            ),
            "investment_buy_invalid_quantity": capture(
                lambda: InvestmentBuyTransaction(buy_row(ZNUMBEROFSHARES=0.0), PROFILE)
            ),
            "investment_buy_invalid_total": capture(
                lambda: InvestmentBuyTransaction(buy_row(ZAMOUNT1=-20.0), PROFILE)
            ),
            "withdrawal_sign_fix": capture(
                lambda: corrected_withdrawal.original_amount
            ),
            "transfer_zero_rate": capture(
                lambda: TransferWithdrawTransaction(transfer_withdraw_row())
            ),
            "relationships": relationship_probe(),
            "sentinel": sentinel_probe(),
        },
    }
    print(json.dumps(normalize(output), sort_keys=True))


if __name__ == "__main__":
    main()
