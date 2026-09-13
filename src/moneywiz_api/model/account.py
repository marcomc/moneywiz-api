from abc import ABC
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Optional

from moneywiz_api.types import ID
from moneywiz_api.model.raw_data_handler import RawDataHandler as RDH
from moneywiz_api.model.record import Record
from moneywiz_api.validation import require_integer_identity, require_valid


@dataclass
class Account(Record, ABC):
    """
    ENT: 9
    """

    display_order: int = field(repr=False)
    group_id: int = field(repr=False)

    name: str
    currency: str
    opening_balance: Decimal  # might be a tiny number
    info: Optional[str]
    user: ID

    def __init__(self, row):
        super().__init__(row)
        self.display_order = row["ZDISPLAYORDER"]
        self.group_id = row["ZGROUPID"]

        self.name = row["ZNAME"]
        self.currency = row["ZCURRENCYNAME"]
        self.opening_balance = RDH.get_decimal(row, "ZOPENINGBALANCE")
        self.info = row.get("ZINFO")

        self.user = row["ZUSER"]
        Account.validate(self)

    def validate(self) -> None:
        require_valid(
            self.display_order is not None, "account display order is required"
        )
        require_valid(self.group_id is not None, "account group identity is required")
        require_integer_identity(
            self.group_id, "account group identity must be an uncoerced integer"
        )
        require_valid(self.name is not None, "account name is required")
        require_valid(self.currency is not None, "account currency is required")
        require_valid(
            self.opening_balance is not None, "account opening balance is required"
        )
        # info is nullable in valid MoneyWiz stores.
        require_valid(self.user is not None, "account owner is required")
        require_integer_identity(
            self.user, "account owner must be an uncoerced integer"
        )


@dataclass
class BankChequeAccount(Account):
    """
    ENT: 10
    """

    def __init__(self, row):
        super().__init__(row)


@dataclass
class BankSavingAccount(Account):
    """
    ENT: 11
    """

    def __init__(self, row):
        super().__init__(row)


@dataclass
class CashAccount(Account):
    """
    ENT: 12
    """

    def __init__(self, row):
        super().__init__(row)


@dataclass
class CreditCardAccount(Account):
    """
    ENT: 13
    """

    statement_day: int  # day in the month

    def __init__(self, row):
        super().__init__(row)
        self.statement_day = row["ZSTATEMENTENDDAY"]
        CreditCardAccount.validate(self)

    def validate(self) -> None:
        super().validate()
        require_valid(
            self.statement_day is not None, "account statement day is required"
        )


@dataclass
class LoanAccount(CreditCardAccount):
    """
    ENT: 14
    """

    def __init__(self, row):
        super().__init__(row)


@dataclass
class InvestmentAccount(Account):
    """
    ENT: 15
    """

    def __init__(self, row):
        super().__init__(row)


@dataclass
class ForexAccount(InvestmentAccount):
    """
    ENT: 16
    """

    def __init__(self, row):
        super().__init__(row)
