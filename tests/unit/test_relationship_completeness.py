import sqlite3

import pytest

from moneywiz_api.database_accessor import DatabaseAccessor
from moneywiz_api.managers.transaction_manager import TransactionManager
from moneywiz_api.read_result import RelationshipStorage


SCHEDULED_TAG_TABLE = (
    "CREATE TABLE Z_31TAGS (Z_31SCHEDULEDTRANSACTIONS1 INTEGER, Z_35TAGS2 INTEGER)"
)
INFO_CARD_TAG_TABLE = (
    "CREATE TABLE Z_23TAGS (Z_23INFOCARDS5 INTEGER, Z_35TAGS1 INTEGER)"
)


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


def create_custom_relationship_schema(path, metadata=(), tables=()) -> None:
    connection = sqlite3.connect(path)
    connection.execute(
        "CREATE TABLE Z_PRIMARYKEY "
        "(Z_ENT INTEGER PRIMARY KEY, Z_NAME TEXT, Z_SUPER INTEGER, Z_MAX INTEGER)"
    )
    connection.execute("CREATE TABLE ZSYNCOBJECT (Z_PK INTEGER, Z_ENT INTEGER)")
    connection.executemany("INSERT INTO Z_PRIMARYKEY VALUES (?, ?, ?, ?)", metadata)
    for statement in tables:
        connection.execute(statement)
    connection.commit()
    connection.close()


def read_tags_and_aggregate(path):
    with DatabaseAccessor(path) as accessor:
        tags, report = accessor.read_tags_map()
        manager_report = TransactionManager().load(accessor)
    return tags, report, manager_report


@pytest.mark.parametrize("populated", [False, True], ids=["empty", "populated"])
@pytest.mark.parametrize(
    "metadata",
    [
        (),
        ((37, "Transaction", 0, 0),),
        ((36, "Tag", 0, 0),),
        ((36, "Tag", 0, 0), (37, "Transaction", 0, 0)),
    ],
    ids=["absent", "transaction-only", "tag-only", "full"],
)
@pytest.mark.parametrize(
    ("table_name", "include_expected"),
    [("Z_37TAGS", False), ("Z_99TAGS", False), ("Z_99TAGS", True)],
    ids=["expected", "alternate", "alternate-beside-direct"],
)
@pytest.mark.parametrize(
    "columns",
    [
        ("Z_37TRANSACTIONS",),
        ("Z_36TAGS",),
        ("Z_37TRANSACTIONS", "Z_36TAGS", "Z_37SCHEDULEDTRANSACTIONS1"),
        ("Z_37TRANSACTIONS", "Z_36TAGS", "Z_37INFOCARDS5"),
        ("Z_37TRANSACTIONS", "Z_36TAGS", "Z_99TRANSACTIONS"),
        ("Z_37TRANSACTIONS", "Z_36TAGS", "Z_99TAGS"),
        ("Z_37TRANSACTIONS", "Z_36TAGS", "Z_PK"),
    ],
    ids=[
        "missing-tag",
        "missing-transaction",
        "extra-scheduled",
        "extra-info-card",
        "extra-transaction",
        "extra-tag",
        "extra-auxiliary",
    ],
)
def test_ambiguous_direct_tag_shape_is_unknown(
    tmp_path, populated, metadata, table_name, include_expected, columns
) -> None:
    path = tmp_path / "ambiguous-direct-tags.sqlite"
    definition = ", ".join(f"{column} INTEGER" for column in columns)
    tables = [f"CREATE TABLE {table_name} ({definition})"]
    if include_expected:
        tables.append(
            "CREATE TABLE Z_37TAGS (Z_37TRANSACTIONS INTEGER, Z_36TAGS INTEGER)"
        )
    create_custom_relationship_schema(path, metadata=metadata, tables=tables)
    if populated:
        with sqlite3.connect(path) as connection:
            connection.execute(
                f"INSERT INTO {table_name} VALUES ({', '.join('?' for _ in columns)})",
                tuple(100 * (index + 1) for index in range(len(columns))),
            )

    tags, report, manager_report = read_tags_and_aggregate(path)

    assert report.storage == RelationshipStorage.UNKNOWN
    assert tags == {}
    assert not report.complete
    assert not manager_report.complete
    assert (
        manager_report.relationships["transaction_tags"].storage
        == RelationshipStorage.UNKNOWN
    )


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


def test_category_counts_exclude_supported_nontransaction_owners(tmp_path) -> None:
    path = tmp_path / "nontransaction-category-owners.sqlite"
    create_custom_relationship_schema(
        path,
        metadata=((2, "CategoryAssigment", 0, 0),),
        tables=(
            "CREATE TABLE ZCATEGORYASSIGMENT "
            "(Z_PK INTEGER, ZCATEGORY INTEGER, ZAMOUNT FLOAT, "
            "ZTRANSACTION INTEGER, ZBUDGET INTEGER, "
            "ZSCHEDULEDTRANSACITION INTEGER, ZSTRINGHISTORYITEM INTEGER)",
        ),
    )
    with sqlite3.connect(path) as connection:
        connection.executemany(
            "INSERT INTO ZCATEGORYASSIGMENT VALUES (?, ?, ?, ?, ?, ?, ?)",
            [
                (1, 10, 5.0, 100, None, None, None),
                (2, 11, 2.0, None, 200, None, None),
                (3, 12, 3.0, None, None, 300, None),
                (4, 13, 4.0, None, None, None, 400),
            ],
        )

    with DatabaseAccessor(path) as accessor:
        categories, report = accessor.read_category_assignments()

    assert categories == {100: [(10, 5)]}
    assert report.source_ids == (1,)
    assert report.parsed_ids == (1,)
    assert report.complete


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


@pytest.mark.parametrize(
    ("reader_name", "table_statement"),
    [
        (
            "read_category_assignments",
            "CREATE TABLE ZCATEGORYASSIGMENT "
            "(Z_PK INTEGER, ZCATEGORY INTEGER, ZTRANSACTION INTEGER, ZAMOUNT FLOAT)",
        ),
        (
            "read_refund_maps",
            "CREATE TABLE ZWITHDRAWREFUNDTRANSACTIONLINK "
            "(Z_PK INTEGER, ZREFUNDTRANSACTION INTEGER, "
            "ZWITHDRAWTRANSACTION INTEGER)",
        ),
        (
            "read_tags_map",
            "CREATE TABLE Z_37TAGS (Z_37TRANSACTIONS INTEGER, Z_36TAGS INTEGER)",
        ),
    ],
)
def test_physical_storage_without_metadata_is_unknown(
    tmp_path, reader_name, table_statement
) -> None:
    path = tmp_path / f"{reader_name}-physical-only.sqlite"
    create_custom_relationship_schema(path, tables=(table_statement,))

    with DatabaseAccessor(path) as accessor:
        data, report = getattr(accessor, reader_name)()

    assert data == {}
    assert report.storage == RelationshipStorage.UNKNOWN
    assert report.status == "error"
    assert not report.complete


@pytest.mark.parametrize(
    ("reader_name", "metadata", "table_statement"),
    [
        (
            "read_category_assignments",
            ((2, "CategoryAssigment", 0, 0),),
            "CREATE TABLE ZCATEGORYASSIGMENT "
            "(Z_PK INTEGER, ZCATEGORY INTEGER, ZTRANSACTION INTEGER, ZAMOUNT FLOAT)",
        ),
        (
            "read_refund_maps",
            ((50, "WithdrawRefundTransactionLink", 0, 0),),
            "CREATE TABLE ZWITHDRAWREFUNDTRANSACTIONLINK "
            "(Z_PK INTEGER, ZREFUNDTRANSACTION INTEGER, "
            "ZWITHDRAWTRANSACTION INTEGER)",
        ),
        (
            "read_tags_map",
            ((36, "Tag", 0, 0), (37, "Transaction", 0, 0)),
            "CREATE TABLE Z_37TAGS (Z_37TRANSACTIONS INTEGER, Z_36TAGS INTEGER)",
        ),
    ],
)
def test_valid_empty_relationship_storage_is_present(
    tmp_path, reader_name, metadata, table_statement
) -> None:
    path = tmp_path / f"{reader_name}-empty.sqlite"
    create_custom_relationship_schema(
        path, metadata=metadata, tables=(table_statement,)
    )

    with DatabaseAccessor(path) as accessor:
        data, report = getattr(accessor, reader_name)()

    assert data == {}
    assert report.storage == RelationshipStorage.PRESENT
    assert report.complete
    assert report.status == "complete"


@pytest.mark.parametrize(
    ("reader_name", "metadata", "table_statement"),
    [
        (
            "read_category_assignments",
            ((2, "CategoryAssigment", 0, 0),),
            "CREATE TABLE ZCATEGORYASSIGMENT "
            "(Z_PK INTEGER, ZCATEGORY INTEGER, ZTRANSACTION INTEGER)",
        ),
        (
            "read_refund_maps",
            ((50, "WithdrawRefundTransactionLink", 0, 0),),
            "CREATE TABLE ZWITHDRAWREFUNDTRANSACTIONLINK "
            "(Z_PK INTEGER, ZREFUNDTRANSACTION INTEGER)",
        ),
        (
            "read_tags_map",
            ((36, "Tag", 0, 0), (37, "Transaction", 0, 0)),
            "CREATE TABLE Z_37TAGS (Z_37TRANSACTIONS INTEGER)",
        ),
    ],
)
def test_relationship_storage_with_missing_columns_is_unknown(
    tmp_path, reader_name, metadata, table_statement
) -> None:
    path = tmp_path / f"{reader_name}-missing-column.sqlite"
    create_custom_relationship_schema(
        path, metadata=metadata, tables=(table_statement,)
    )

    with DatabaseAccessor(path) as accessor:
        data, report = getattr(accessor, reader_name)()

    assert data == {}
    assert report.storage == RelationshipStorage.UNKNOWN
    assert not report.complete


@pytest.mark.parametrize(
    "metadata",
    [
        ((37, "Transaction", 0, 0),),
        ((36, "Tag", 0, 0),),
    ],
)
def test_partial_transaction_tag_metadata_is_unknown(tmp_path, metadata) -> None:
    path = tmp_path / "partial-tag-metadata.sqlite"
    create_custom_relationship_schema(path, metadata=metadata)

    with DatabaseAccessor(path) as accessor:
        tags, report = accessor.read_tags_map()

    assert tags == {}
    assert report.storage == RelationshipStorage.UNKNOWN
    assert not report.complete


def test_scheduled_tag_storage_is_not_a_transaction_tag_candidate(tmp_path) -> None:
    path = tmp_path / "scheduled-tags.sqlite"
    create_custom_relationship_schema(path, tables=(SCHEDULED_TAG_TABLE,))

    with DatabaseAccessor(path) as accessor:
        tags, absent_report = accessor.read_tags_map()

    assert tags == {}
    assert absent_report.storage == RelationshipStorage.ABSENT
    assert absent_report.complete

    path_with_metadata = tmp_path / "scheduled-and-direct-tags.sqlite"
    direct_table = "CREATE TABLE Z_37TAGS (Z_37TRANSACTIONS INTEGER, Z_36TAGS INTEGER)"
    create_custom_relationship_schema(
        path_with_metadata,
        metadata=((36, "Tag", 0, 0), (37, "Transaction", 0, 0)),
        tables=(SCHEDULED_TAG_TABLE, direct_table),
    )

    with DatabaseAccessor(path_with_metadata) as accessor:
        _, present_report = accessor.read_tags_map()

    assert present_report.storage == RelationshipStorage.PRESENT
    assert present_report.complete


@pytest.mark.parametrize(
    ("alternate_table", "include_expected"),
    [
        (
            "CREATE TABLE Z_36TAGS (Z_36TRANSACTIONS INTEGER, Z_35TAGS INTEGER)",
            False,
        ),
        (
            "CREATE TABLE Z_99TAGS (Z_99TRANSACTIONS INTEGER, Z_36TAGS INTEGER)",
            True,
        ),
    ],
)
def test_wrong_or_conflicting_transaction_tag_layout_is_unknown(
    tmp_path, alternate_table, include_expected
) -> None:
    path = tmp_path / "conflicting-tags.sqlite"
    expected_table = (
        "CREATE TABLE Z_37TAGS (Z_37TRANSACTIONS INTEGER, Z_36TAGS INTEGER)"
    )
    tables = (
        (expected_table, alternate_table) if include_expected else (alternate_table,)
    )
    create_custom_relationship_schema(
        path,
        metadata=((36, "Tag", 0, 0), (37, "Transaction", 0, 0)),
        tables=tables,
    )

    with DatabaseAccessor(path) as accessor:
        tags, report = accessor.read_tags_map()
        manager_report = TransactionManager().load(accessor)

    assert tags == {}
    assert report.storage == RelationshipStorage.UNKNOWN
    assert not report.complete
    assert not manager_report.complete
    assert (
        manager_report.relationships["transaction_tags"].storage
        == RelationshipStorage.UNKNOWN
    )


@pytest.mark.parametrize("populated", [False, True])
@pytest.mark.parametrize(
    "metadata",
    [
        (),
        ((36, "Tag", 0, 0), (37, "Transaction", 0, 0)),
        ((37, "Transaction", 0, 0),),
        ((36, "Tag", 0, 0),),
    ],
)
def test_malformed_numbered_tag_table_is_always_unknown(
    tmp_path, populated, metadata
) -> None:
    path = tmp_path / "malformed-numbered-tags.sqlite"
    create_custom_relationship_schema(
        path,
        metadata=metadata,
        tables=("CREATE TABLE Z_37TAGS (Z_PK INTEGER)",),
    )
    if populated:
        connection = sqlite3.connect(path)
        connection.execute("INSERT INTO Z_37TAGS VALUES (1)")
        connection.commit()
        connection.close()

    tags, report, manager_report = read_tags_and_aggregate(path)

    assert tags == {}
    assert report.storage == RelationshipStorage.UNKNOWN
    assert not report.complete
    assert not manager_report.complete
    assert (
        manager_report.relationships["transaction_tags"].storage
        == RelationshipStorage.UNKNOWN
    )


def test_valid_direct_tags_with_malformed_alternate_are_unknown(tmp_path) -> None:
    path = tmp_path / "valid-and-malformed-tags.sqlite"
    create_custom_relationship_schema(
        path,
        metadata=((36, "Tag", 0, 0), (37, "Transaction", 0, 0)),
        tables=(
            "CREATE TABLE Z_37TAGS (Z_37TRANSACTIONS INTEGER, Z_36TAGS INTEGER)",
            "CREATE TABLE Z_99TAGS (Z_PK INTEGER)",
        ),
    )

    tags, report, manager_report = read_tags_and_aggregate(path)

    assert tags == {}
    assert report.storage == RelationshipStorage.UNKNOWN
    assert not report.complete
    assert not manager_report.complete


@pytest.mark.parametrize("populated", [False, True])
@pytest.mark.parametrize(
    "metadata",
    [
        (),
        ((37, "Transaction", 0, 0),),
        ((36, "Tag", 0, 0),),
        ((36, "Tag", 0, 0), (37, "Transaction", 0, 0)),
    ],
)
@pytest.mark.parametrize("unrelated_table", [SCHEDULED_TAG_TABLE, INFO_CARD_TAG_TABLE])
def test_complete_unrelated_tag_shape_establishes_absence(
    tmp_path, unrelated_table, metadata, populated
) -> None:
    path = tmp_path / "unrelated-tags.sqlite"
    create_custom_relationship_schema(
        path, metadata=metadata, tables=(unrelated_table,)
    )
    if populated:
        table_name = unrelated_table.split()[2]
        with sqlite3.connect(path) as connection:
            connection.execute(f"INSERT INTO {table_name} VALUES (100, 200)")

    tags, report, manager_report = read_tags_and_aggregate(path)

    assert tags == {}
    expected_storage = (
        RelationshipStorage.UNKNOWN if metadata else RelationshipStorage.ABSENT
    )
    assert report.storage == expected_storage
    assert report.complete == (not metadata)
    assert manager_report.complete == (not metadata)
    assert manager_report.relationships["transaction_tags"].storage == expected_storage


@pytest.mark.parametrize("populated", [False, True])
@pytest.mark.parametrize("include_unrelated", [False, True])
@pytest.mark.parametrize(("transaction_ent", "tag_ent"), [(36, 35), (37, 36), (61, 58)])
def test_unrelated_controls_do_not_conflict_with_valid_direct_tags(
    tmp_path, populated, include_unrelated, transaction_ent, tag_ent
) -> None:
    path = tmp_path / "unrelated-controls-and-direct-tags.sqlite"
    table_name = f"Z_{transaction_ent}TAGS"
    tables = [
        f"CREATE TABLE {table_name} (Z_{transaction_ent}TRANSACTIONS INTEGER, Z_{tag_ent}TAGS INTEGER)"
    ]
    if include_unrelated:
        tables.extend((INFO_CARD_TAG_TABLE, SCHEDULED_TAG_TABLE))
    create_custom_relationship_schema(
        path,
        metadata=((tag_ent, "Tag", 0, 0), (transaction_ent, "Transaction", 0, 0)),
        tables=tables,
    )
    if populated:
        with sqlite3.connect(path) as connection:
            connection.execute(f"INSERT INTO {table_name} VALUES (100, 200)")
            if include_unrelated:
                connection.execute("INSERT INTO Z_23TAGS VALUES (101, 201)")
                connection.execute("INSERT INTO Z_31TAGS VALUES (102, 202)")

    tags, report, manager_report = read_tags_and_aggregate(path)

    assert tags == ({100: [200]} if populated else {})
    assert report.storage == RelationshipStorage.PRESENT
    assert report.storage_name == table_name
    assert report.complete
    assert manager_report.complete
    assert (
        manager_report.relationships["transaction_tags"].storage
        == RelationshipStorage.PRESENT
    )


@pytest.mark.parametrize("populated", [False, True])
@pytest.mark.parametrize(
    "metadata",
    [
        (),
        ((36, "Tag", 0, 0), (37, "Transaction", 0, 0)),
        ((37, "Transaction", 0, 0),),
        ((36, "Tag", 0, 0),),
    ],
)
@pytest.mark.parametrize(
    "ambiguous_table",
    [
        "CREATE TABLE Z_31TAGS (Z_31SCHEDULEDTRANSACTIONS1 INTEGER)",
        "CREATE TABLE Z_31TAGS "
        "(Z_31SCHEDULEDTRANSACTIONS1 INTEGER, Z_35TAGS2 INTEGER, "
        "Z_31TRANSACTIONS INTEGER)",
        "CREATE TABLE Z_23TAGS (Z_23INFOCARDS5 INTEGER)",
        "CREATE TABLE Z_23TAGS "
        "(Z_23INFOCARDS5 INTEGER, Z_35TAGS1 INTEGER, "
        "Z_23TRANSACTIONS INTEGER)",
    ],
)
def test_incomplete_or_hybrid_unrelated_tag_shape_is_unknown(
    tmp_path, metadata, ambiguous_table, populated
) -> None:
    path = tmp_path / "ambiguous-unrelated-tags.sqlite"
    create_custom_relationship_schema(
        path, metadata=metadata, tables=(ambiguous_table,)
    )
    if populated:
        table_name = ambiguous_table.split()[2]
        columns_count = ambiguous_table.count("INTEGER")
        with sqlite3.connect(path) as connection:
            connection.execute(
                f"INSERT INTO {table_name} VALUES ({', '.join('?' for _ in range(columns_count))})",
                (1,) * columns_count,
            )

    tags, report, manager_report = read_tags_and_aggregate(path)

    assert tags == {}
    assert report.storage == RelationshipStorage.UNKNOWN
    assert not report.complete
    assert not manager_report.complete
    assert (
        manager_report.relationships["transaction_tags"].storage
        == RelationshipStorage.UNKNOWN
    )
