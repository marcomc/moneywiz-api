import sqlite3

import pytest

from moneywiz_api.database_accessor import DatabaseAccessor
from moneywiz_api.managers.transaction_manager import TransactionManager
from moneywiz_api.read_result import RelationshipStorage


def create_relationship_schema(path, *, include_tables=True) -> None:
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
            (8, "SyncObject", 0, 0),
            (36, "Tag", 8, 0),
            (37, "Transaction", 8, 0),
            (50, "WithdrawRefundTransactionLink", 0, 0),
        ],
    )
    if include_tables:
        connection.execute(
            "CREATE TABLE ZCATEGORYASSIGMENT "
            "(Z_PK INTEGER PRIMARY KEY, ZCATEGORY INTEGER, ZAMOUNT FLOAT, "
            "ZTRANSACTION INTEGER)"
        )
        connection.executemany(
            "INSERT INTO ZCATEGORYASSIGMENT VALUES (?, ?, ?, ?)",
            [(1, 10, 5.0, 100), (2, 11, None, 101), (3, 12, 2.0, None)],
        )
        connection.execute(
            "CREATE TABLE ZWITHDRAWREFUNDTRANSACTIONLINK "
            "(Z_PK INTEGER PRIMARY KEY, ZREFUNDTRANSACTION INTEGER, "
            "ZWITHDRAWTRANSACTION INTEGER)"
        )
        connection.executemany(
            "INSERT INTO ZWITHDRAWREFUNDTRANSACTIONLINK VALUES (?, ?, ?)",
            [(4, 102, 103), (5, 104, None)],
        )
        connection.execute(
            "CREATE TABLE Z_37TAGS (Z_37TRANSACTIONS INTEGER, Z_36TAGS INTEGER)"
        )
        connection.executemany(
            "INSERT INTO Z_37TAGS VALUES (?, ?)",
            [(100, 200), (101, None)],
        )
    connection.commit()
    connection.close()


def test_shifted_relationship_layouts_report_valid_and_skipped_rows(tmp_path) -> None:
    path = tmp_path / "relationships.sqlite"
    create_relationship_schema(path)

    with DatabaseAccessor(path) as accessor:
        categories, category_report = accessor.read_category_assignments()
        refunds, refund_report = accessor.read_refund_maps()
        tags, tag_report = accessor.read_tags_map()

        assert categories == {100: [(10, 5)]}
        assert category_report.source_ids == (1, 2)
        assert category_report.parsed_ids == (1,)
        assert category_report.status == "partial"
        assert refunds == {102: 103}
        assert refund_report.source_ids == (4, 5)
        assert refund_report.parsed_ids == (4,)
        assert refund_report.status == "partial"
        assert tags == {100: [200]}
        assert tag_report.storage_name == "Z_37TAGS"
        assert tag_report.source_ids == ("100:200", "row:1")
        assert tag_report.parsed_ids == ("100:200",)
        assert tag_report.status == "partial"

        manager_report = TransactionManager().load(accessor)

    assert manager_report.status == "partial"
    assert not manager_report.complete
    assert set(manager_report.relationships) == {
        "category_assignments",
        "refund_links",
        "transaction_tags",
    }
    assert (
        manager_report.as_dict()["relationships"]["transaction_tags"]["storage_name"]
        == "Z_37TAGS"
    )


@pytest.mark.parametrize(
    "reader_name",
    ["read_category_assignments", "read_refund_maps", "read_tags_map"],
)
def test_known_entity_with_missing_storage_is_unknown(tmp_path, reader_name) -> None:
    path = tmp_path / "missing-storage.sqlite"
    create_relationship_schema(path, include_tables=False)

    with DatabaseAccessor(path) as accessor:
        data, report = getattr(accessor, reader_name)()

    assert data == {}
    assert report.storage == RelationshipStorage.UNKNOWN
    assert report.status == "error"
    assert not report.complete


def test_missing_optional_relationship_entities_are_absent(tmp_path) -> None:
    path = tmp_path / "absent-relationships.sqlite"
    connection = sqlite3.connect(path)
    connection.execute(
        "CREATE TABLE Z_PRIMARYKEY "
        "(Z_ENT INTEGER PRIMARY KEY, Z_NAME TEXT, Z_SUPER INTEGER, Z_MAX INTEGER)"
    )
    connection.execute("CREATE TABLE ZSYNCOBJECT (Z_PK INTEGER, Z_ENT INTEGER)")
    connection.commit()
    connection.close()

    with DatabaseAccessor(path) as accessor:
        reports = [
            accessor.read_category_assignments()[1],
            accessor.read_refund_maps()[1],
            accessor.read_tags_map()[1],
        ]
        manager_report = TransactionManager().load(accessor)

    assert all(report.storage == RelationshipStorage.ABSENT for report in reports)
    assert all(report.complete for report in reports)
    assert manager_report.complete


def test_unknown_relationship_storage_propagates_to_manager(tmp_path) -> None:
    path = tmp_path / "unknown-relationships.sqlite"
    create_relationship_schema(path, include_tables=False)

    with DatabaseAccessor(path) as accessor:
        manager_report = TransactionManager().load(accessor)

    assert not manager_report.complete
    assert manager_report.status == "error"
    assert all(
        report.storage == RelationshipStorage.UNKNOWN
        for report in manager_report.relationships.values()
    )
