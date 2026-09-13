"""Public MoneyWiz read API."""

from dataclasses import asdict
from pathlib import Path
from typing import Iterable

from moneywiz_api.database_accessor import DatabaseAccessor
from moneywiz_api.managers.account_manager import AccountManager
from moneywiz_api.managers.category_manager import CategoryManager
from moneywiz_api.managers.investment_holding_manager import (
    InvestmentHoldingManager,
)
from moneywiz_api.managers.payee_manager import PayeeManager
from moneywiz_api.managers.transaction_manager import TransactionManager
from moneywiz_api.managers.tag_manager import TagManager
from moneywiz_api.read_result import ApiCompleteness, ReadSnapshot, json_safe


class MoneywizApi:
    """Read a MoneyWiz database with explicit completeness evidence."""

    MANAGER_NAMES = (
        "accounts",
        "payees",
        "categories",
        "transactions",
        "investment_holdings",
        "tags",
    )

    def __init__(
        self,
        db_file: Path,
        managers: Iterable[str] | None = None,
    ):
        self.accessor = DatabaseAccessor(db_file)
        self.account_manager = AccountManager()
        self.payee_manager = PayeeManager()
        self.category_manager = CategoryManager()
        self.transaction_manager = TransactionManager()
        self.investment_holding_manager = InvestmentHoldingManager()
        self.tag_manager = TagManager()
        self._managers = {
            "accounts": self.account_manager,
            "payees": self.payee_manager,
            "categories": self.category_manager,
            "transactions": self.transaction_manager,
            "investment_holdings": self.investment_holding_manager,
            "tags": self.tag_manager,
        }
        self._loaded_managers: set[str] = set()

        try:
            self.load(managers)
        except BaseException:
            self.close()
            raise

    def _manager_names(self, managers: Iterable[str] | None) -> tuple[str, ...]:
        if managers is None:
            return self.MANAGER_NAMES
        if isinstance(managers, str):
            raise TypeError("managers must be an iterable of manager names")
        names = tuple(dict.fromkeys(managers))
        unknown = [name for name in names if name not in self._managers]
        if unknown:
            raise ValueError(f"unknown manager names: {', '.join(unknown)}")
        return names

    def load(self, managers: Iterable[str] | None = None) -> ApiCompleteness:
        """Atomically reload requested managers and every retained manager."""
        requested = self._manager_names(managers)
        names = tuple(
            name
            for name in self.MANAGER_NAMES
            if name in self._loaded_managers or name in requested
        )
        staged = {name: type(self._managers[name])() for name in names}
        with self.accessor.read_transaction():
            for name in names:
                staged[name].load(self.accessor)
        for name in names:
            self._managers[name]._adopt_loaded_state(staged[name])
        self._loaded_managers.update(names)
        return self.completeness(names)

    def completeness(self, managers: Iterable[str] | None = None) -> ApiCompleteness:
        """Return completeness evidence without reloading the database."""
        names = (
            tuple(name for name in self.MANAGER_NAMES if name in self._loaded_managers)
            if managers is None
            else self._manager_names(managers)
        )
        unloaded = [name for name in names if name not in self._loaded_managers]
        if unloaded:
            raise ValueError(f"manager not loaded: {', '.join(unloaded)}")
        return ApiCompleteness(
            managers={name: self._managers[name].load_report for name in names}
        )

    def snapshot(self, managers: Iterable[str] | None = None) -> ReadSnapshot:
        """Return loaded records and diagnostics in a JSON-safe structure."""
        names = (
            tuple(name for name in self.MANAGER_NAMES if name in self._loaded_managers)
            if managers is None
            else self._manager_names(managers)
        )
        completeness = self.completeness(names)
        records = {
            name: tuple(
                json_safe(record.as_dict())
                for record in self._managers[name].records().values()
            )
            for name in names
        }
        return ReadSnapshot(
            records=records,
            completeness=completeness,
            schema_profile=json_safe(asdict(self.accessor.schema_profile)),
        )

    def close(self) -> None:
        """Close the underlying read-only database connection."""
        self.accessor.close()

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_value, traceback) -> None:
        self.close()
