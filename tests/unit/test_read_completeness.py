from contextlib import contextmanager
from decimal import Decimal
import json

import pytest

from moneywiz_api.managers.account_manager import AccountManager
from moneywiz_api.managers.category_manager import CategoryManager
from moneywiz_api.managers.investment_holding_manager import (
    InvestmentHoldingManager,
)
from moneywiz_api.managers.payee_manager import PayeeManager
from moneywiz_api.managers.record_manager import RecordManager
from moneywiz_api.managers.tag_manager import TagManager
from moneywiz_api.managers.transaction_manager import TransactionManager
from moneywiz_api.model.record import Record
from moneywiz_api.read_result import (
    LoadErrorKind,
    ManagerLoadReport,
    RelationshipLoadReport,
    RelationshipStorage,
)
from moneywiz_api.schema_profile import SchemaProfile


class ExampleRecord(Record):
    pass


class ExampleManager(RecordManager):
    @property
    def ents(self):
        return {"ExampleRecord": ExampleRecord}

    @property
    def entity_roots(self):
        return ("ExampleRecord",)


@pytest.mark.parametrize(
    "manager_type",
    [
        AccountManager,
        PayeeManager,
        CategoryManager,
        TransactionManager,
        InvestmentHoldingManager,
        TagManager,
    ],
)
def test_fresh_public_manager_reports_unloaded(manager_type) -> None:
    report = manager_type().load_report

    assert not report.observed
    assert report.status == "unloaded"
    assert not report.complete
    assert report.as_dict()["status"] == "unloaded"


def test_default_report_remains_a_successful_empty_observation() -> None:
    report = ManagerLoadReport()

    assert report.observed
    assert report.complete
    assert report.status == "complete"


def record_row(record_id=1, gid="record-1", ent=1):
    return {
        "Z_ENT": ent,
        "ZOBJECTCREATIONDATE": 0.0,
        "ZGID": gid,
        "Z_PK": record_id,
    }


class RecordAccessor:
    def __init__(self, rows, typenames=None):
        self.rows = rows
        self.typenames = typenames or {1: "ExampleRecord"}

    @contextmanager
    def read_transaction(self):
        yield

    def descendant_typenames(self, _roots):
        return list(self.typenames.values())

    def query_objects(self, _typenames):
        return self.rows

    def typename_for(self, ent_id):
        return self.typenames.get(ent_id)


def test_report_counts_duplicate_and_unknown_rows_without_partial_mutation() -> None:
    manager = ExampleManager()
    rows = [
        record_row(),
        record_row(gid="duplicate-id"),
        record_row(record_id=2),
        record_row(record_id=3, gid="record-3", ent=2),
    ]

    report = manager.load(RecordAccessor(rows, {1: "ExampleRecord", 2: "FutureRecord"}))

    assert report.source_ids == (1, 1, 2, 3)
    assert report.parsed_ids == (1,)
    assert [item.error for item in report.skipped] == [
        LoadErrorKind.DUPLICATE_ID,
        LoadErrorKind.DUPLICATE_GID,
        LoadErrorKind.UNKNOWN_ENTITY,
    ]
    assert report.observed
    assert report.status == "partial"
    assert list(manager.records()) == [1]


def test_each_load_resets_records_and_diagnostics() -> None:
    manager = ExampleManager()
    manager.load(RecordAccessor([record_row(), record_row(gid="duplicate-id")]))

    report = manager.load(RecordAccessor([record_row(record_id=4, gid="record-4")]))

    assert report.complete
    assert report.source_ids == (4,)
    assert report.parsed_ids == (4,)
    assert list(manager.records()) == [4]
    assert manager.load_errors == []


def test_failed_first_load_is_unloaded_and_can_retry() -> None:
    manager = ExampleManager()

    class FailingAccessor(RecordAccessor):
        def query_objects(self, _typenames):
            raise RuntimeError("synthetic query failure")

    with pytest.raises(RuntimeError, match="synthetic query failure"):
        manager.load(FailingAccessor([]))

    assert manager.load_report.status == "unloaded"
    assert not manager.load_report.complete
    assert manager.records() == {}

    report = manager.load(RecordAccessor([]))

    assert report.observed
    assert report.status == "complete"
    assert report.complete


def test_failed_direct_reload_replaces_prior_report_with_unloaded_state() -> None:
    manager = ExampleManager()
    manager.load(RecordAccessor([record_row()]))

    class FailingAccessor(RecordAccessor):
        def query_objects(self, _typenames):
            raise RuntimeError("synthetic reload failure")

    with pytest.raises(RuntimeError, match="synthetic reload failure"):
        manager.load(FailingAccessor([]))

    assert manager.load_report.status == "unloaded"
    assert not manager.load_report.complete
    assert manager.records() == {}


@pytest.mark.parametrize("interruption_type", [KeyboardInterrupt, SystemExit])
def test_interrupted_reload_discards_partial_state_and_reraises(
    interruption_type,
) -> None:
    manager = ExampleManager()
    manager.load(RecordAccessor([record_row(record_id=9, gid="record-9")]))
    interruption = interruption_type("synthetic interruption")

    class InterruptingAccessor(RecordAccessor):
        def __init__(self):
            super().__init__([record_row(), record_row(record_id=2, gid="record-2")])
            self.lookups = 0

        def typename_for(self, ent_id):
            self.lookups += 1
            if self.lookups == 2:
                raise interruption
            return super().typename_for(ent_id)

    with pytest.raises(interruption_type) as error:
        manager.load(InterruptingAccessor())

    assert error.value is interruption
    assert manager.records() == {}
    assert manager._gid_to_id == {}
    assert manager.load_report.status == "unloaded"

    report = manager.load(RecordAccessor([record_row(record_id=3, gid="record-3")]))

    assert report.complete
    assert list(manager.records()) == [3]


def test_completed_load_with_only_invalid_rows_reports_error() -> None:
    manager = ExampleManager()

    report = manager.load(
        RecordAccessor([record_row(ent=2)], {1: "ExampleRecord", 2: "FutureRecord"})
    )

    assert report.observed
    assert report.status == "error"
    assert not report.complete


class FakeAccessor:
    def __init__(self, _db_file):
        self.schema_profile = SchemaProfile(
            "unsuffixed-investment-columns",
            "ZNUMBEROFSHARES",
            "ZNUMBEROFSHARES",
            "ZPRICEPERSHARE",
            "ZPRICEPERSHARE",
        )
        self.queries = []
        self.transaction_entries = 0
        self.transaction_depth = 0
        self.max_transaction_depth = 0
        self.closed = False

    @contextmanager
    def read_transaction(self):
        self.transaction_entries += 1
        self.transaction_depth += 1
        self.max_transaction_depth = max(
            self.max_transaction_depth, self.transaction_depth
        )
        try:
            yield
        finally:
            self.transaction_depth -= 1

    def descendant_typenames(self, _roots):
        return []

    def query_objects(self, typenames):
        assert self.transaction_depth == 2
        self.queries.append(tuple(typenames))
        return []

    def typename_for(self, _ent_id):
        return None

    def read_category_assignments(self):
        assert self.transaction_depth == 2
        return {}, RelationshipLoadReport(RelationshipStorage.ABSENT)

    def read_refund_maps(self):
        assert self.transaction_depth == 2
        return {}, RelationshipLoadReport(RelationshipStorage.ABSENT)

    def read_tags_map(self):
        assert self.transaction_depth == 2
        return {}, RelationshipLoadReport(RelationshipStorage.ABSENT)

    def close(self):
        self.closed = True


def test_api_supports_scoped_loads_and_json_safe_snapshot(monkeypatch) -> None:
    import moneywiz_api.moneywiz_api as api_module

    monkeypatch.setattr(api_module, "DatabaseAccessor", FakeAccessor)

    with api_module.MoneywizApi(
        "unused.sqlite", managers=("accounts", "transactions")
    ) as api:
        report = api.completeness().as_dict()
        snapshot = api.snapshot().as_dict()

        assert list(report["managers"]) == ["accounts", "transactions"]
        assert report["complete"]
        assert snapshot["records"] == {"accounts": [], "transactions": []}
        assert snapshot["schema_profile"]["profile_id"] == (
            "unsuffixed-investment-columns"
        )
        assert api.payee_manager.load_report.status == "unloaded"
        assert api.accessor.transaction_entries == 3
        assert api.accessor.max_transaction_depth == 2
        json.dumps(snapshot)

    assert api.accessor.closed


def test_api_preserves_default_eager_loading(monkeypatch) -> None:
    import moneywiz_api.moneywiz_api as api_module

    monkeypatch.setattr(api_module, "DatabaseAccessor", FakeAccessor)

    with api_module.MoneywizApi("unused.sqlite") as api:
        assert list(api.completeness().managers) == list(api.MANAGER_NAMES)
        assert len(api.accessor.queries) == len(api.MANAGER_NAMES)
        assert api.accessor.transaction_entries == 7
        assert api.accessor.max_transaction_depth == 2


def test_api_rejects_unloaded_or_unknown_manager(monkeypatch) -> None:
    import moneywiz_api.moneywiz_api as api_module

    monkeypatch.setattr(api_module, "DatabaseAccessor", FakeAccessor)
    api = api_module.MoneywizApi("unused.sqlite", managers=())

    assert all(
        api._managers[name].load_report.status == "unloaded"
        for name in api.MANAGER_NAMES
    )
    with pytest.raises(ValueError, match="manager not loaded"):
        api.completeness(("accounts",))
    with pytest.raises(ValueError, match="manager not loaded"):
        api.snapshot(("accounts",))
    with pytest.raises(ValueError, match="unknown manager"):
        api.load(("missing",))

    api.close()


def test_json_safe_snapshot_formats_decimal(monkeypatch) -> None:
    import moneywiz_api.moneywiz_api as api_module

    monkeypatch.setattr(api_module, "DatabaseAccessor", FakeAccessor)
    api = api_module.MoneywizApi("unused.sqlite", managers=())
    api.account_manager.add(
        type(
            "SnapshotRecord",
            (),
            {
                "id": 1,
                "gid": "snapshot-record",
                "display_order": 1,
                "as_dict": lambda self: {"amount": Decimal("1.20")},
            },
        )()
    )
    api._loaded_managers.add("accounts")

    assert api.snapshot().as_dict()["records"]["accounts"][0]["amount"] == "1.20"
    api.close()
