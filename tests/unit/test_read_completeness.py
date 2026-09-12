from contextlib import contextmanager
from decimal import Decimal
import json

import pytest

from moneywiz_api.managers.record_manager import RecordManager
from moneywiz_api.model.record import Record
from moneywiz_api.read_result import (
    LoadErrorKind,
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
        self.closed = False

    @contextmanager
    def read_transaction(self):
        self.transaction_entries += 1
        yield

    def descendant_typenames(self, _roots):
        return []

    def query_objects(self, typenames):
        self.queries.append(tuple(typenames))
        return []

    def typename_for(self, _ent_id):
        return None

    def read_category_assignments(self):
        return {}, RelationshipLoadReport(RelationshipStorage.ABSENT)

    def read_refund_maps(self):
        return {}, RelationshipLoadReport(RelationshipStorage.ABSENT)

    def read_tags_map(self):
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
        assert api.accessor.transaction_entries == 1
        json.dumps(snapshot)

    assert api.accessor.closed


def test_api_preserves_default_eager_loading(monkeypatch) -> None:
    import moneywiz_api.moneywiz_api as api_module

    monkeypatch.setattr(api_module, "DatabaseAccessor", FakeAccessor)

    with api_module.MoneywizApi("unused.sqlite") as api:
        assert list(api.completeness().managers) == list(api.MANAGER_NAMES)
        assert len(api.accessor.queries) == len(api.MANAGER_NAMES)
        assert api.accessor.transaction_entries == 1


def test_api_rejects_unloaded_or_unknown_manager(monkeypatch) -> None:
    import moneywiz_api.moneywiz_api as api_module

    monkeypatch.setattr(api_module, "DatabaseAccessor", FakeAccessor)
    api = api_module.MoneywizApi("unused.sqlite", managers=())

    with pytest.raises(ValueError, match="manager not loaded"):
        api.completeness(("accounts",))
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
