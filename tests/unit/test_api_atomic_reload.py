from contextlib import contextmanager
from decimal import Decimal

import pytest

from moneywiz_api.managers.account_manager import AccountManager
from moneywiz_api.managers.transaction_manager import TransactionManager
from moneywiz_api.read_result import RelationshipLoadReport, RelationshipStorage
from moneywiz_api.schema_profile import SchemaProfile


def account_row(record_id, name):
    return {
        "Z_ENT": 10,
        "ZOBJECTCREATIONDATE": 0.0,
        "ZGID": f"account-{record_id}",
        "Z_PK": record_id,
        "ZDISPLAYORDER": record_id,
        "ZGROUPID": 1,
        "ZNAME": name,
        "ZCURRENCYNAME": "EUR",
        "ZOPENINGBALANCE": 0.0,
        "ZINFO": None,
        "ZUSER": 1,
    }


def transaction_row(record_id, account_id):
    return {
        "Z_ENT": 37,
        "ZOBJECTCREATIONDATE": 0.0,
        "ZGID": f"transaction-{record_id}",
        "Z_PK": record_id,
        "ZRECONCILED": 0,
        "ZAMOUNT1": 5.0,
        "ZDESC2": f"Transaction {record_id}",
        "ZDATE1": 0.0,
        "ZNOTES1": None,
        "ZACCOUNT2": account_id,
        "ZPAYEE2": None,
        "ZORIGINALCURRENCY": "EUR",
        "ZORIGINALAMOUNT": 5.0,
        "ZORIGINALEXCHANGERATE": 1.0,
    }


class SyntheticAccessor:
    def __init__(self):
        self.schema_profile = SchemaProfile(
            "unsuffixed-investment-columns",
            "ZNUMBEROFSHARES",
            "ZNUMBEROFSHARES",
            "ZPRICEPERSHARE",
            "ZPRICEPERSHARE",
        )
        self.generation = "old"
        self.fail_at = None
        self.extra_invalid_account = False
        self.in_transaction = False
        self.transaction_entries = 0
        self.transaction_exits = 0

    @contextmanager
    def read_transaction(self):
        if self.fail_at == "enter":
            raise RuntimeError("synthetic transaction enter failure")
        self.transaction_entries += 1
        self.in_transaction = True
        try:
            yield
        finally:
            self.in_transaction = False
            self.transaction_exits += 1
        if self.fail_at == "exit":
            raise RuntimeError("synthetic transaction exit failure")

    def descendant_typenames(self, _roots):
        return []

    def query_objects(self, typenames):
        if self.fail_at == "transaction_query" and "DepositTransaction" in typenames:
            raise RuntimeError("synthetic transaction query failure")
        if "CashAccount" in typenames:
            if self.generation == "old":
                return [account_row(1, "Old")]
            rows = [account_row(2, "New")]
            if self.extra_invalid_account:
                rows.append(account_row(3, None))
            return rows
        if "DepositTransaction" in typenames:
            record_id, account_id = (10, 1) if self.generation == "old" else (20, 2)
            return [transaction_row(record_id, account_id)]
        return []

    def typename_for(self, ent_id):
        return {10: "CashAccount", 37: "DepositTransaction"}.get(ent_id)

    def _relationship(self, name):
        if self.fail_at == name:
            raise RuntimeError(f"synthetic {name} failure")
        transaction_id = 10 if self.generation == "old" else 20
        if name == "category":
            value = {transaction_id: [(30, Decimal("5"))]}
        elif name == "refund":
            value = {transaction_id: 40}
        else:
            value = {transaction_id: [50]}
        report = RelationshipLoadReport(
            storage=RelationshipStorage.PRESENT,
            storage_name=name,
            source_ids=(f"{name}-{transaction_id}",),
            parsed_ids=(f"{name}-{transaction_id}",),
        )
        return value, report

    def read_category_assignments(self):
        return self._relationship("category")

    def read_refund_maps(self):
        return self._relationship("refund")

    def read_tags_map(self):
        return self._relationship("tags")

    def close(self):
        pass


def api_for(monkeypatch, accessor, managers):
    import moneywiz_api.moneywiz_api as api_module

    monkeypatch.setattr(api_module, "DatabaseAccessor", lambda _path: accessor)
    return api_module.MoneywizApi("unused.sqlite", managers=managers)


@pytest.mark.parametrize(
    "failure",
    ["transaction_query", "category", "refund", "tags"],
)
def test_failed_later_load_keeps_published_union_and_relationships(
    monkeypatch, failure
) -> None:
    accessor = SyntheticAccessor()
    api = api_for(monkeypatch, accessor, ("accounts", "transactions"))
    account_manager = api.account_manager
    transaction_manager = api.transaction_manager
    old_state = {
        "account_records": account_manager._records,
        "account_gid_index": account_manager._gid_to_id,
        "account_report": account_manager.load_report,
        "account": account_manager.get(1),
        "transaction_records": transaction_manager._records,
        "transaction_gid_index": transaction_manager._gid_to_id,
        "transaction_report": transaction_manager.load_report,
        "transaction": transaction_manager.get(10),
        "categories": transaction_manager.category_assignment,
        "refunds": transaction_manager.refund_maps,
        "tags": transaction_manager.tags_map,
    }
    accessor.generation = "new"
    accessor.fail_at = failure

    with pytest.raises(RuntimeError, match="synthetic"):
        api.load(("transactions",))

    assert api.account_manager is account_manager
    assert api.transaction_manager is transaction_manager
    assert api._loaded_managers == {"accounts", "transactions"}
    assert account_manager._records is old_state["account_records"]
    assert account_manager._gid_to_id is old_state["account_gid_index"]
    assert account_manager.load_report is old_state["account_report"]
    assert account_manager.load_report.status == "complete"
    assert account_manager.get(1) is old_state["account"]
    assert account_manager.get_by_gid("account-1") is old_state["account"]
    assert transaction_manager._records is old_state["transaction_records"]
    assert transaction_manager._gid_to_id is old_state["transaction_gid_index"]
    assert transaction_manager.load_report is old_state["transaction_report"]
    assert transaction_manager.load_report.status == "complete"
    assert transaction_manager.get(10) is old_state["transaction"]
    assert transaction_manager.get_by_gid("transaction-10") is old_state["transaction"]
    assert transaction_manager.category_assignment is old_state["categories"]
    assert transaction_manager.refund_maps is old_state["refunds"]
    assert transaction_manager.tags_map is old_state["tags"]
    assert api.completeness().managers["accounts"] is old_state["account_report"]
    assert (
        api.completeness().managers["transactions"] is old_state["transaction_report"]
    )
    assert not accessor.in_transaction


@pytest.mark.parametrize("failure", ["enter", "exit"])
def test_transaction_context_failure_does_not_publish(monkeypatch, failure) -> None:
    accessor = SyntheticAccessor()
    api = api_for(monkeypatch, accessor, ("accounts",))
    records = api.account_manager._records
    report = api.account_manager.load_report
    account = api.account_manager.get(1)
    accessor.generation = "new"
    accessor.fail_at = failure

    with pytest.raises(RuntimeError, match="transaction .* failure"):
        api.load(("accounts",))

    assert api.account_manager._records is records
    assert api.account_manager.load_report is report
    assert api.account_manager.get(1) is account
    assert api._loaded_managers == {"accounts"}
    assert not accessor.in_transaction


def test_failed_new_selection_remains_unloaded_then_successful_retry_publishes(
    monkeypatch,
) -> None:
    accessor = SyntheticAccessor()
    api = api_for(monkeypatch, accessor, ("accounts",))
    account_manager = api.account_manager
    transaction_manager = api.transaction_manager
    old_account = account_manager.get(1)
    accessor.generation = "new"
    accessor.fail_at = "transaction_query"

    with pytest.raises(RuntimeError, match="transaction query"):
        api.load(("transactions",))

    assert api._loaded_managers == {"accounts"}
    assert account_manager.get(1) is old_account
    assert transaction_manager.records() == {}
    assert transaction_manager.load_report.status == "unloaded"
    with pytest.raises(ValueError, match="manager not loaded: transactions"):
        api.completeness(("transactions",))

    accessor.fail_at = None
    report = api.load(("transactions",))

    assert api.account_manager is account_manager
    assert api.transaction_manager is transaction_manager
    assert api._loaded_managers == {"accounts", "transactions"}
    assert account_manager.get(1) is None
    assert account_manager.get(2).name == "New"
    assert account_manager.get_by_gid("account-2") is account_manager.get(2)
    assert transaction_manager.get(20).description == "Transaction 20"
    assert transaction_manager.category_assignment == {20: [(30, Decimal("5"))]}
    assert transaction_manager.refund_maps == {20: 40}
    assert transaction_manager.tags_map == {20: [50]}
    assert report.complete


@pytest.mark.parametrize("failure", ["category", "refund", "tags"])
def test_direct_transaction_relationship_failure_stays_unloaded_then_retries(
    failure,
) -> None:
    accessor = SyntheticAccessor()
    accessor.fail_at = failure
    manager = TransactionManager()

    with pytest.raises(RuntimeError, match=f"synthetic {failure} failure"):
        manager.load(accessor)

    assert manager.load_report.status == "unloaded"
    assert not manager.load_report.complete
    assert manager.records() == {}
    assert manager.category_assignment == {}
    assert manager.refund_maps == {}
    assert manager.tags_map == {}

    accessor.fail_at = None
    report = manager.load(accessor)

    assert report.status == "complete"
    assert report.complete
    assert manager.get(10) is not None

    accessor.fail_at = failure
    with pytest.raises(RuntimeError, match=f"synthetic {failure} failure"):
        manager.load(accessor)

    assert manager.load_report.status == "unloaded"
    assert not manager.load_report.complete
    assert manager.records() == {}


def test_successful_publication_occurs_after_transaction_exit(monkeypatch) -> None:
    accessor = SyntheticAccessor()
    adoption_states = []
    original_adopt = AccountManager._adopt_loaded_state

    def observe_adoption(self, staged):
        adoption_states.append(accessor.in_transaction)
        original_adopt(self, staged)

    monkeypatch.setattr(AccountManager, "_adopt_loaded_state", observe_adoption)
    api = api_for(monkeypatch, accessor, ("accounts",))
    accessor.generation = "new"

    api.load(("accounts",))

    assert adoption_states == [False, False]
    assert accessor.transaction_entries == accessor.transaction_exits == 2


def test_partial_staged_generation_is_published(monkeypatch) -> None:
    accessor = SyntheticAccessor()
    api = api_for(monkeypatch, accessor, ("accounts",))
    accessor.generation = "new"
    accessor.extra_invalid_account = True

    report = api.load(("accounts",)).managers["accounts"]

    assert report.source_ids == (2, 3)
    assert report.parsed_ids == (2,)
    assert report.skipped[0].record_id == 3
    assert report.skipped[0].error.value == "validation"
    assert api.account_manager.get(2).name == "New"
    assert api.account_manager.get(3) is None
