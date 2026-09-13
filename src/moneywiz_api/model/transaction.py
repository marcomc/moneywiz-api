from abc import ABC
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from typing import Optional

import pytest

from moneywiz_api.model.raw_data_handler import RawDataHandler as RDH
from moneywiz_api.model.record import Record
from moneywiz_api.schema_profile import SchemaProfile
from moneywiz_api.types import ID
from moneywiz_api.validation import require_integer_identity, require_valid

ABS_TOLERANCE = 0.001


@dataclass
class Transaction(Record, ABC):
    """
    ENT: 36
    """

    reconciled: bool

    amount: Decimal
    description: str
    datetime: datetime
    notes: Optional[str]

    def __init__(self, row):
        super().__init__(row)
        raw_reconciled = row["ZRECONCILED"]
        require_integer_identity(
            raw_reconciled,
            "transaction reconciled state must be an uncoerced integer",
        )
        require_valid(
            raw_reconciled in (0, 1),
            "transaction reconciled state must be zero or one",
        )
        self.reconciled = raw_reconciled == 1
        self.amount = RDH.get_decimal(row, "ZAMOUNT1")
        self.description = row["ZDESC2"]
        self.datetime = RDH.get_datetime(row, "ZDATE1")
        self.notes = row["ZNOTES1"]

        # Fixes

        # Validate
        require_valid(self.reconciled is not None, "transaction state is required")
        require_valid(self.amount is not None, "transaction amount is required")
        require_valid(
            self.description is not None, "transaction description is required"
        )
        require_valid(self.datetime is not None, "transaction date is required")
        # self.notes can be None


@dataclass
class DepositTransaction(Transaction):
    """
    ENT: 37
    """

    account: ID
    amount: Decimal  # neg: expense, pos: income
    payee: Optional[ID]

    # FX
    original_currency: str
    original_amount: Decimal  # neg: expense, pos: income
    original_exchange_rate: Optional[Decimal]

    def __init__(self, row):
        super().__init__(row)
        self.account = row["ZACCOUNT2"]
        self.amount = RDH.get_decimal(row, "ZAMOUNT1")
        self.payee = row["ZPAYEE2"]
        self.original_currency = row["ZORIGINALCURRENCY"]
        self.original_amount = RDH.get_decimal(row, "ZORIGINALAMOUNT")
        self.original_exchange_rate = RDH.get_nullable_decimal(
            row, "ZORIGINALEXCHANGERATE"
        )

        # Fixes
        if self.original_exchange_rate == Decimal(0):
            self.original_exchange_rate = None

        # Validate
        self.validate()

    def validate(self):
        require_valid(self.account is not None, "deposit account is required")
        require_integer_identity(
            self.account, "deposit account must be an uncoerced integer"
        )
        require_valid(self.amount is not None, "deposit amount is required")
        # self.payee can be None
        require_integer_identity(
            self.payee,
            "deposit payee must be an uncoerced integer",
            optional=True,
        )
        require_valid(
            self.original_currency is not None, "deposit original currency is required"
        )
        require_valid(
            self.original_amount is not None, "deposit original amount is required"
        )

        require_valid(
            self.amount * self.original_amount > 0,
            "deposit amounts must have the same sign",
        )
        if self.original_exchange_rate is not None:
            require_valid(
                self.amount
                == pytest.approx(
                    self.original_amount * self.original_exchange_rate,
                    abs=ABS_TOLERANCE,
                ),
                "deposit exchange rate must match the amounts",
            )


@dataclass
class InvestmentExchangeTransaction(Transaction):
    """
    ENT: 38
    """

    account: ID

    from_investment_holding: ID
    from_symbol: str
    to_investment_holding: ID
    to_symbol: str
    from_number_of_shares: Decimal  # neg
    to_number_of_shares: Decimal  # pos

    original_fee: Decimal  # pos: fee, neg: income?
    original_fee_currency: str

    def __init__(self, row):
        super().__init__(row)

        self.account = row["ZACCOUNT2"]
        self.from_investment_holding = row["ZFROMINVESTMENTHOLDING"]
        self.from_symbol = row["ZFROMSYMBOL"]
        self.to_investment_holding = row["ZTOINVESTMENTHOLDING"]
        self.to_symbol = row["ZTOSYMBOL"]
        self.from_number_of_shares = row["ZFROMNUMBEROFSHARES"]
        self.to_number_of_shares = row["ZTONUMBEROFSHARES"]

        self.original_fee = row["ZORIGINALFEE"]
        self.original_fee_currency = row["ZORIGINALFEECURRENCY"]

        # Fixes
        if self.original_fee_currency == self.from_symbol:
            self.from_number_of_shares += self.original_fee
        elif self.original_fee_currency == self.to_symbol:
            self.to_number_of_shares += self.original_fee

        # Validate
        self.validate()

    def validate(self):
        require_valid(self.account is not None, "exchange account is required")
        require_integer_identity(
            self.account, "exchange account must be an uncoerced integer"
        )
        require_valid(
            self.from_investment_holding is not None,
            "exchange source holding is required",
        )
        require_integer_identity(
            self.from_investment_holding,
            "exchange source holding must be an uncoerced integer",
        )
        require_valid(self.from_symbol, "exchange source symbol is required")
        require_valid(
            self.to_investment_holding is not None,
            "exchange destination holding is required",
        )
        require_integer_identity(
            self.to_investment_holding,
            "exchange destination holding must be an uncoerced integer",
        )
        require_valid(self.to_symbol, "exchange destination symbol is required")
        require_valid(
            self.from_number_of_shares <= 0,
            "exchange source quantity must not be positive",
        )
        require_valid(
            self.to_number_of_shares >= 0,
            "exchange destination quantity must not be negative",
        )
        require_valid(self.original_fee is not None, "exchange fee is required")
        require_valid(
            self.original_fee_currency in [self.from_symbol, self.to_symbol],
            "exchange fee currency must match one holding",
        )


@dataclass
class InvestmentTransaction(Transaction, ABC):
    """
    ENT: 39
    """

    def __init__(self, row):
        super().__init__(row)


@dataclass
class InvestmentBuyTransaction(InvestmentTransaction):
    """
    ENT: 40
    """

    account: ID
    amount: Decimal

    fee: Decimal

    investment_holding: ID
    number_of_shares: Decimal
    price_per_share: Decimal

    def __init__(self, row, schema_profile: SchemaProfile | None = None):
        super().__init__(row)
        if schema_profile is not None and not schema_profile.is_known:
            raise ValueError("unsupported investment schema profile")
        self.account = row["ZACCOUNT2"]
        self.amount = RDH.get_decimal(row, "ZAMOUNT1")

        self.fee = RDH.get_decimal(row, "ZFEE2")

        self.investment_holding = row["ZINVESTMENTHOLDING"]
        self.number_of_shares = RDH.get_profile_decimal(
            row,
            schema_profile.transaction_number_of_shares_column
            if schema_profile
            else None,
            "ZNUMBEROFSHARES1",
            "ZNUMBEROFSHARES",
        )
        self.price_per_share = RDH.get_profile_decimal(
            row,
            schema_profile.transaction_price_per_share_column
            if schema_profile
            else None,
            "ZPRICEPERSHARE1",
            "ZPRICEPERSHARE",
        )

        # Fixes
        self.fee = max(self.fee, 0)

        # Validate
        self.validate()

    def validate(self):
        require_valid(self.account is not None, "investment buy account is required")
        require_integer_identity(
            self.account, "investment buy account must be an uncoerced integer"
        )
        require_valid(self.amount is not None, "investment buy amount is required")
        require_valid(self.amount <= 0, "investment buy amount must not be positive")
        require_valid(self.fee is not None, "investment buy fee is required")
        require_valid(self.fee >= 0, "investment buy fee must not be negative")
        # Either tiny (close to 0) or positive
        require_valid(
            abs(self.fee) == pytest.approx(0, abs=ABS_TOLERANCE)
            or self.fee > ABS_TOLERANCE,
            "investment buy fee must be tiny or positive",
        )
        require_valid(
            self.investment_holding is not None,
            "investment buy holding is required",
        )
        require_integer_identity(
            self.investment_holding,
            "investment buy holding must be an uncoerced integer",
        )
        require_valid(
            self.number_of_shares is not None,
            "investment buy quantity is required",
        )
        require_valid(
            self.number_of_shares > 0, "investment buy quantity must be positive"
        )
        require_valid(
            self.price_per_share is not None,
            "investment buy share price is required",
        )
        require_valid(
            self.price_per_share >= 0,
            "investment buy share price must not be negative",
        )
        require_valid(
            -(self.number_of_shares * self.price_per_share + self.fee)
            == pytest.approx(self.amount, abs=ABS_TOLERANCE),
            "investment buy total must match amount",
        )


@dataclass
class InvestmentSellTransaction(InvestmentTransaction):
    """
    ENT: 41
    """

    account: ID
    amount: Decimal  # neg: loss after fees, pos: income

    fee: Decimal

    investment_holding: ID
    number_of_shares: Decimal
    price_per_share: Decimal

    def __init__(self, row, schema_profile: SchemaProfile | None = None):
        super().__init__(row)
        if schema_profile is not None and not schema_profile.is_known:
            raise ValueError("unsupported investment schema profile")
        self.account = row["ZACCOUNT2"]
        self.amount = RDH.get_decimal(row, "ZAMOUNT1")

        self.fee = RDH.get_decimal(row, "ZFEE2")

        self.investment_holding = row["ZINVESTMENTHOLDING"]
        self.number_of_shares = RDH.get_profile_decimal(
            row,
            schema_profile.transaction_number_of_shares_column
            if schema_profile
            else None,
            "ZNUMBEROFSHARES1",
            "ZNUMBEROFSHARES",
        )
        self.price_per_share = RDH.get_profile_decimal(
            row,
            schema_profile.transaction_price_per_share_column
            if schema_profile
            else None,
            "ZPRICEPERSHARE1",
            "ZPRICEPERSHARE",
        )

        # Fixes
        self.fee = max(self.fee, 0)

        # Validate
        self.validate()

    def validate(self):
        require_valid(self.account is not None, "investment sell account is required")
        require_integer_identity(
            self.account, "investment sell account must be an uncoerced integer"
        )
        require_valid(self.amount is not None, "investment sell amount is required")

        require_valid(self.fee is not None, "investment sell fee is required")
        require_valid(self.fee >= 0, "investment sell fee must not be negative")
        # Either tiny (close to 0) or positive
        require_valid(
            abs(self.fee) == pytest.approx(0, abs=ABS_TOLERANCE)
            or self.fee > ABS_TOLERANCE,
            "investment sell fee must be tiny or positive",
        )

        require_valid(
            self.investment_holding is not None,
            "investment sell holding is required",
        )
        require_integer_identity(
            self.investment_holding,
            "investment sell holding must be an uncoerced integer",
        )
        require_valid(
            self.number_of_shares is not None,
            "investment sell quantity is required",
        )
        require_valid(
            self.number_of_shares > 0, "investment sell quantity must be positive"
        )
        require_valid(
            self.price_per_share is not None,
            "investment sell share price is required",
        )
        require_valid(
            self.price_per_share >= 0,
            "investment sell share price must not be negative",
        )
        require_valid(
            self.number_of_shares * self.price_per_share - self.fee
            == pytest.approx(self.amount, abs=ABS_TOLERANCE),
            "investment sell total must match amount",
        )


@dataclass
class ReconcileTransaction(Transaction):
    """
    ENT: 42
    """

    account: ID

    reconcile_amount: Decimal | None  # new balance
    reconcile_number_of_shares: Decimal | None  # new balance

    def __init__(self, row):
        super().__init__(row)
        self.account = row["ZACCOUNT2"]
        self.reconcile_amount = RDH.get_nullable_decimal(row, "ZRECONCILEAMOUNT")
        self.reconcile_number_of_shares = RDH.get_nullable_decimal(
            row, "ZRECONCILENUMBEROFSHARES"
        )

        # Validate
        self.validate()

    def validate(self):
        require_valid(self.account is not None, "reconcile account is required")
        require_integer_identity(
            self.account, "reconcile account must be an uncoerced integer"
        )
        require_valid(
            self.reconcile_amount is not None
            or self.reconcile_number_of_shares is not None,
            "reconcile amount or quantity is required",
        )


@dataclass
class RefundTransaction(Transaction):
    """
    ENT: 43
    """

    account: ID
    amount: Decimal
    payee: Optional[ID]

    # FX
    original_currency: str
    original_amount: Decimal
    original_exchange_rate: Optional[Decimal]

    def __init__(self, row):
        super().__init__(row)
        self.account = row["ZACCOUNT2"]
        self.amount = RDH.get_decimal(row, "ZAMOUNT1")
        self.payee = row["ZPAYEE2"]

        self.original_currency = row["ZORIGINALCURRENCY"]
        self.original_amount = RDH.get_decimal(row, "ZORIGINALAMOUNT")
        self.original_exchange_rate = RDH.get_nullable_decimal(
            row, "ZORIGINALEXCHANGERATE"
        )

        # Fixes
        if self.original_exchange_rate == Decimal(0):
            self.original_exchange_rate = None

        # Validate
        self.validate()

    def validate(self):
        require_valid(self.account is not None, "refund account is required")
        require_integer_identity(
            self.account, "refund account must be an uncoerced integer"
        )
        require_integer_identity(
            self.payee,
            "refund payee must be an uncoerced integer",
            optional=True,
        )
        require_valid(self.amount is not None, "refund amount is required")
        require_valid(self.amount > 0, "refund amount must be positive")

        require_valid(
            self.original_currency is not None, "refund original currency is required"
        )
        require_valid(
            self.original_amount is not None, "refund original amount is required"
        )
        require_valid(
            self.original_amount > 0, "refund original amount must be positive"
        )

        if self.original_exchange_rate is not None:
            require_valid(
                self.amount
                == pytest.approx(
                    self.original_amount * self.original_exchange_rate,
                    abs=ABS_TOLERANCE,
                ),
                "refund exchange rate must match the amounts",
            )


@dataclass
class TransferBudgetTransaction(Transaction):
    """
    ENT: 44
    """

    def __init__(self, row):
        super().__init__(row)
        # TODO: Not Implemented


@dataclass
class TransferDepositTransaction(Transaction):
    """
    ENT: 45
    """

    account: ID
    amount: Decimal  # pos: in

    sender_account: ID
    sender_transaction: ID

    original_amount: Decimal  # ATTENTION: sign got fixed
    original_currency: str

    sender_amount: Decimal
    sender_currency: str

    original_fee: Optional[Decimal]
    original_fee_currency: Optional[str]

    original_exchange_rate: Decimal

    def __init__(self, row):
        super().__init__(row)
        self.account = row["ZACCOUNT2"]
        self.amount = RDH.get_decimal(row, "ZAMOUNT1")

        self.sender_account = row["ZSENDERACCOUNT"]
        self.sender_transaction = row["ZSENDERTRANSACTION"]

        self.original_amount = RDH.get_decimal(row, "ZORIGINALAMOUNT")
        self.original_currency = row["ZORIGINALCURRENCY"] or ""
        self.sender_amount = RDH.get_decimal(row, "ZORIGINALSENDERAMOUNT")
        self.sender_currency = row["ZORIGINALSENDERCURRENCY"] or ""

        self.original_fee = RDH.get_nullable_decimal(row, "ZORIGINALFEE")
        self.original_fee_currency = row["ZORIGINALFEECURRENCY"]

        self.original_exchange_rate = RDH.get_decimal(row, "ZORIGINALEXCHANGERATE")

        # Fixes
        self.original_amount = abs(self.original_amount)
        if self.original_amount == 0 and self.sender_amount != 0:
            self.original_amount = abs(
                -self.sender_amount * self.original_exchange_rate
                - (self.original_fee or 0)
            )

        # Validate
        self.validate()

    def validate(self):
        require_valid(self.account is not None, "transfer deposit account is required")
        require_integer_identity(
            self.account, "transfer deposit account must be an uncoerced integer"
        )
        require_valid(self.amount is not None, "transfer deposit amount is required")
        require_valid(self.amount > 0, "transfer deposit amount must be positive")
        require_valid(
            self.sender_account is not None,
            "transfer deposit sender account is required",
        )
        require_integer_identity(
            self.sender_account,
            "transfer deposit sender account must be an uncoerced integer",
        )
        require_valid(
            self.sender_transaction is not None,
            "transfer deposit sender transaction is required",
        )
        require_integer_identity(
            self.sender_transaction,
            "transfer deposit sender transaction must be an uncoerced integer",
        )
        require_valid(
            self.original_amount is not None,
            "transfer deposit original amount is required",
        )
        require_valid(
            self.original_amount > 0,
            "transfer deposit original amount must be positive",
        )
        require_valid(
            self.original_currency is not None,
            "transfer deposit original currency is required",
        )
        require_valid(
            self.sender_amount is not None,
            "transfer deposit sender amount is required",
        )
        require_valid(
            self.sender_amount <= 0,
            "transfer deposit sender amount must not be positive",
        )
        require_valid(
            self.sender_currency is not None,
            "transfer deposit sender currency is required",
        )

        if self.original_fee is not None and self.original_fee != 0:
            require_valid(
                self.original_fee_currency is not None,
                "transfer deposit fee currency is required",
            )

        require_valid(
            self.original_exchange_rate is not None,
            "transfer deposit exchange rate is required",
        )

        # assert self.amount ==  self.original_amount # original_amount could be different with amount ZCURRENCYEXCHANGERATE is playing up
        require_valid(
            self.original_amount
            == pytest.approx(
                -self.sender_amount * self.original_exchange_rate
                - (self.original_fee or 0),
                abs=ABS_TOLERANCE,
            ),
            "transfer deposit exchange rate must match the amounts",
        )


@dataclass
class TransferWithdrawTransaction(Transaction):
    """
    ENT: 46
    """

    account: ID
    amount: Decimal  # neg: out

    recipient_account: ID
    recipient_transaction: ID

    original_amount: Decimal  # always neg
    original_currency: str

    recipient_amount: Decimal  # ATTENTION: sign got fixed
    recipient_currency: str

    original_fee: Optional[Decimal]
    original_fee_currency: Optional[str]

    original_exchange_rate: Decimal

    def __init__(self, row):
        super().__init__(row)
        self.account = row["ZACCOUNT2"]
        self.amount = RDH.get_decimal(row, "ZAMOUNT1")

        self.recipient_account = row["ZRECIPIENTACCOUNT1"]
        self.recipient_transaction = row["ZRECIPIENTTRANSACTION"]

        self.original_amount = RDH.get_decimal(row, "ZORIGINALAMOUNT")
        self.original_currency = row["ZORIGINALCURRENCY"] or ""
        self.recipient_amount = RDH.get_decimal(row, "ZORIGINALRECIPIENTAMOUNT")
        self.recipient_currency = row["ZORIGINALRECIPIENTCURRENCY"] or ""

        self.original_fee = RDH.get_nullable_decimal(row, "ZORIGINALFEE")
        self.original_fee_currency = row["ZORIGINALFEECURRENCY"]

        self.original_exchange_rate = RDH.get_decimal(row, "ZORIGINALEXCHANGERATE")

        # Fixes
        self.recipient_amount = abs(self.recipient_amount)
        if self.recipient_amount == 0 and self.original_amount != 0:
            self.recipient_amount = abs(
                self.original_amount * self.original_exchange_rate
            )
        if self.original_amount == 0 and self.recipient_amount != 0:
            if self.original_exchange_rate == 0:
                raise ValueError(
                    "cannot reconstruct a transfer amount with a zero exchange rate"
                )
            self.original_amount = self.amount

        # Validate
        self.validate()

    def validate(self):
        require_valid(
            self.account is not None, "transfer withdrawal account is required"
        )
        require_integer_identity(
            self.account, "transfer withdrawal account must be an uncoerced integer"
        )
        require_valid(self.amount is not None, "transfer withdrawal amount is required")
        require_valid(self.amount < 0, "transfer withdrawal amount must be negative")
        require_valid(
            self.recipient_account is not None,
            "transfer withdrawal recipient account is required",
        )
        require_integer_identity(
            self.recipient_account,
            "transfer withdrawal recipient account must be an uncoerced integer",
        )
        require_valid(
            self.recipient_transaction is not None,
            "transfer withdrawal recipient transaction is required",
        )
        require_integer_identity(
            self.recipient_transaction,
            "transfer withdrawal recipient transaction must be an uncoerced integer",
        )
        require_valid(
            self.original_amount is not None,
            "transfer withdrawal original amount is required",
        )
        require_valid(
            self.original_amount < 0,
            "transfer withdrawal original amount must be negative",
        )
        require_valid(
            self.original_currency is not None,
            "transfer withdrawal original currency is required",
        )
        require_valid(
            self.recipient_amount is not None,
            "transfer withdrawal recipient amount is required",
        )
        require_valid(
            self.recipient_amount > 0,
            "transfer withdrawal recipient amount must be positive",
        )
        require_valid(
            self.recipient_currency is not None,
            "transfer withdrawal recipient currency is required",
        )

        if self.original_fee is not None and self.original_fee != 0:
            require_valid(
                self.original_fee_currency is not None,
                "transfer withdrawal fee currency is required",
            )

        require_valid(
            self.original_exchange_rate is not None,
            "transfer withdrawal exchange rate is required",
        )

        require_valid(
            self.amount == self.original_amount,
            "transfer withdrawal amount must match original amount",
        )
        require_valid(
            self.amount
            == pytest.approx(
                -self.recipient_amount / self.original_exchange_rate,
                abs=ABS_TOLERANCE,
            ),
            "transfer withdrawal exchange rate must match the amounts",
        )


@dataclass
class WithdrawTransaction(Transaction):
    """
    ENT: 47
    """

    account: ID
    amount: Decimal  # neg: expense, pos: income
    payee: Optional[ID]

    # FX
    original_currency: str
    original_amount: Decimal  # neg: expense, pos: income ATTENTION: sign got fixed
    original_exchange_rate: Optional[Decimal]

    def __init__(self, row):
        super().__init__(row)
        self.account = row["ZACCOUNT2"]
        self.amount = RDH.get_decimal(row, "ZAMOUNT1")
        self.payee = row["ZPAYEE2"]

        self.original_currency = row["ZORIGINALCURRENCY"]
        self.original_amount = RDH.get_decimal(row, "ZORIGINALAMOUNT")
        self.original_exchange_rate = RDH.get_nullable_decimal(
            row, "ZORIGINALEXCHANGERATE"
        )

        # Fixes
        if self.amount * self.original_amount < 0:
            self.original_amount = -self.original_amount

        if self.original_exchange_rate == Decimal(0):
            self.original_exchange_rate = None

        # Validate
        self.validate()

    def validate(self):
        require_valid(self.account is not None, "withdrawal account is required")
        require_integer_identity(
            self.account, "withdrawal account must be an uncoerced integer"
        )
        require_valid(self.amount is not None, "withdrawal amount is required")
        # self.payee can be None
        require_integer_identity(
            self.payee,
            "withdrawal payee must be an uncoerced integer",
            optional=True,
        )
        require_valid(
            self.original_currency is not None,
            "withdrawal original currency is required",
        )
        require_valid(
            self.original_amount is not None, "withdrawal original amount is required"
        )

        require_valid(
            self.amount * self.original_amount > 0,
            "withdrawal amounts must have the same sign",
        )

        if self.original_exchange_rate is not None:
            require_valid(
                self.amount
                == pytest.approx(
                    self.original_amount * self.original_exchange_rate,
                    abs=ABS_TOLERANCE,
                ),
                "withdrawal exchange rate must match the amounts",
            )
