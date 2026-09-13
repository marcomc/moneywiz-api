from pathlib import Path
import sqlite3

import pytest

from moneywiz_api import MoneywizApi
from moneywiz_api.database_accessor import DatabaseAccessor, DatabaseSchemaError
from moneywiz_api.managers.account_manager import AccountManager
from moneywiz_api.managers.category_manager import CategoryManager
from moneywiz_api.managers.investment_holding_manager import (
    InvestmentHoldingManager,
)
from moneywiz_api.managers.payee_manager import PayeeManager
from moneywiz_api.managers.tag_manager import TagManager
from moneywiz_api.managers.transaction_manager import TransactionManager


UNCLASSIFIABLE = "database contains rows with unclassifiable entity ancestry"


def common_row(record_id: int, ent_id: int | None, gid: str) -> dict:
    return {
        "Z_PK": record_id,
        "Z_ENT": ent_id,
        "ZOBJECTCREATIONDATE": 0.0,
        "ZGID": gid,
    }


def account_row() -> dict:
    return {
        **common_row(1, 10, "account-1"),
        "ZDISPLAYORDER": 1,
        "ZGROUPID": 1,
        "ZNAME": "Account",
        "ZCURRENCYNAME": "EUR",
        "ZOPENINGBALANCE": 0.0,
        "ZINFO": None,
        "ZUSER": 1,
    }


def payee_row() -> dict:
    return {
        **common_row(1, 28, "payee-1"),
        "ZNAME5": "Payee",
        "ZUSER7": 1,
    }


def category_row() -> dict:
    return {
        **common_row(1, 19, "category-1"),
        "ZNAME2": "Category",
        "ZPARENTCATEGORY": None,
        "ZTYPE2": 1,
        "ZUSER3": 1,
    }


def holding_row() -> dict:
    return {
        **common_row(1, 24, "holding-1"),
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


def tag_row() -> dict:
    return {
        **common_row(1, 35, "tag-1"),
        "ZNAME6": "Tag",
        "ZUSER8": 1,
    }


def transaction_row() -> dict:
    return {
        **common_row(1, 38, "transaction-1"),
        "ZRECONCILED": 0,
        "ZAMOUNT1": 5.0,
        "ZDESC2": "Deposit",
        "ZDATE1": 0.0,
        "ZNOTES1": None,
        "ZACCOUNT2": 1,
        "ZPAYEE2": None,
        "ZORIGINALCURRENCY": "EUR",
        "ZORIGINALAMOUNT": 5.0,
        "ZORIGINALEXCHANGERATE": 1.0,
    }


MANAGER_CASES = [
    pytest.param(
        AccountManager, "Account", 9, "CashAccount", 10, account_row, id="account"
    ),
    pytest.param(PayeeManager, "Payee", 28, "Payee", 28, payee_row, id="payee"),
    pytest.param(
        CategoryManager, "Category", 19, "Category", 19, category_row, id="category"
    ),
    pytest.param(
        InvestmentHoldingManager,
        "InvestmentHolding",
        24,
        "InvestmentHolding",
        24,
        holding_row,
        id="investment-holding",
    ),
    pytest.param(TagManager, "Tag", 35, "Tag", 35, tag_row, id="tag"),
    pytest.param(
        TransactionManager,
        "Transaction",
        37,
        "DepositTransaction",
        38,
        transaction_row,
        id="transaction",
    ),
]


PARTIALLY_MIGRATED_HIERARCHIES = [
    pytest.param(
        AccountManager,
        "Account",
        "CashAccount",
        10,
        account_row,
        id="account",
    ),
    pytest.param(
        TransactionManager,
        "Transaction",
        "DepositTransaction",
        38,
        transaction_row,
        id="transaction",
    ),
]


def create_store(path: Path, metadata: list[tuple[int, str, int]], rows: list[dict]):
    connection = sqlite3.connect(path)
    connection.execute("PRAGMA journal_mode=WAL")
    connection.execute(
        "CREATE TABLE Z_PRIMARYKEY "
        "(Z_ENT INTEGER, Z_NAME TEXT, Z_SUPER INTEGER, Z_MAX INTEGER)"
    )
    connection.executemany("INSERT INTO Z_PRIMARYKEY VALUES (?, ?, ?, 0)", metadata)
    columns = list(
        dict.fromkeys(
            ["Z_PK", "Z_ENT", "ZOBJECTCREATIONDATE", "ZGID"]
            + [column for row in rows for column in row]
            + ["ZNUMBEROFSHARES", "ZPRICEPERSHARE"]
        )
    )
    definitions = ", ".join(
        f'"{column}" INTEGER' if column in {"Z_PK", "Z_ENT"} else f'"{column}"'
        for column in columns
    )
    connection.execute(f"CREATE TABLE ZSYNCOBJECT ({definitions})")
    for row in rows:
        insert_row(connection, row)
    connection.commit()
    connection.close()


def insert_row(connection: sqlite3.Connection, row: dict) -> None:
    columns = tuple(row)
    names = ", ".join(f'"{column}"' for column in columns)
    placeholders = ", ".join("?" for _ in columns)
    connection.execute(
        f"INSERT INTO ZSYNCOBJECT ({names}) VALUES ({placeholders})",
        tuple(row[column] for column in columns),
    )


@pytest.mark.parametrize(
    ("manager_type", "root_name", "root_id", "known_name", "known_id", "row_factory"),
    MANAGER_CASES,
)
@pytest.mark.parametrize("transitive", [False, True], ids=["direct", "transitive"])
def test_all_managers_count_unknown_descendants(
    tmp_path,
    manager_type,
    root_name,
    root_id,
    known_name,
    known_id,
    row_factory,
    transitive,
) -> None:
    path = tmp_path / f"{root_name}-{transitive}.sqlite"
    metadata = [(8, "SyncObject", 0), (root_id, root_name, 8)]
    if known_id != root_id:
        metadata.append((known_id, known_name, root_id))
    unknown_parent = root_id
    if transitive:
        metadata.append((90, f"Future{root_name}Parent", root_id))
        unknown_parent = 90
    unknown_name = f"Future{root_name}"
    metadata.append((91, unknown_name, unknown_parent))
    create_store(
        path,
        metadata,
        [row_factory(), common_row(2, 91, f"future-{root_name.lower()}")],
    )

    with DatabaseAccessor(path) as accessor:
        report = manager_type().load(accessor)

    assert set(report.source_ids) == {1, 2}
    assert report.parsed_ids == (1,)
    assert len(report.skipped) == 1
    assert report.skipped[0].record_id == 2
    assert report.skipped[0].entity == unknown_name
    assert report.skipped[0].error.value == "unknown_entity"


@pytest.mark.parametrize(
    ("manager_type", "missing_root", "known_name", "known_id", "row_factory"),
    PARTIALLY_MIGRATED_HIERARCHIES,
)
def test_unknown_descendant_is_counted_when_abstract_root_is_absent(
    tmp_path,
    manager_type,
    missing_root,
    known_name,
    known_id,
    row_factory,
) -> None:
    path = tmp_path / f"missing-{missing_root}.sqlite"
    future_name = f"Future{known_name}"
    create_store(
        path,
        [
            (8, "SyncObject", 0),
            (known_id, known_name, 8),
            (91, future_name, known_id),
        ],
        [row_factory(), common_row(2, 91, f"future-{known_name.lower()}")],
    )

    with DatabaseAccessor(path) as accessor:
        assert accessor.ent_for(missing_root) is None
        report = manager_type().load(accessor)

    assert set(report.source_ids) == {1, 2}
    assert report.parsed_ids == (1,)
    assert len(report.skipped) == 1
    assert report.skipped[0].record_id == 2
    assert report.skipped[0].entity == future_name
    assert report.skipped[0].error.value == "unknown_entity"
    assert not report.complete


def orphan_metadata(kind: str) -> tuple[list[tuple[int, str, int]], int | None]:
    metadata = [(8, "SyncObject", 0), (9, "Account", 8), (10, "CashAccount", 9)]
    if kind == "null":
        return metadata, None
    if kind == "unmapped":
        return metadata, 99
    if kind == "missing-parent":
        return [*metadata, (90, "FuturePayee", 99)], 90
    if kind == "cycle":
        return [*metadata, (90, "FuturePayee", 91), (91, "FuturePayeeParent", 90)], 90
    raise AssertionError("unsupported synthetic orphan kind")


@pytest.mark.parametrize("kind", ["null", "unmapped", "missing-parent", "cycle"])
@pytest.mark.parametrize("include_valid", [False, True], ids=["orphan-only", "mixed"])
def test_unclassifiable_observed_entity_refuses_source_read(
    tmp_path, kind, include_valid
) -> None:
    path = tmp_path / f"{kind}-{include_valid}.sqlite"
    metadata, ent_id = orphan_metadata(kind)
    rows = [common_row(2, ent_id, "orphan")]
    if include_valid:
        rows.insert(0, account_row())
    create_store(path, metadata, rows)

    with DatabaseAccessor(path) as accessor:
        with pytest.raises(DatabaseSchemaError, match=f"^{UNCLASSIFIABLE}$"):
            accessor.query_objects(["CashAccount"])


@pytest.mark.parametrize(
    "operation",
    [
        lambda accessor: accessor.query_objects(["CashAccount"]),
        lambda accessor: accessor.get_record(1),
        lambda accessor: accessor.get_record_by_gid("account-1"),
        lambda accessor: accessor.read_category_assignments(),
        lambda accessor: accessor.get_category_assignment(),
        lambda accessor: accessor.read_refund_maps(),
        lambda accessor: accessor.get_refund_maps(),
        lambda accessor: accessor.read_tags_map(),
        lambda accessor: accessor.get_tags_map(),
    ],
)
def test_every_public_cache_dependent_reader_refuses_orphan(
    tmp_path, operation
) -> None:
    path = tmp_path / "reader-orphan.sqlite"
    metadata, ent_id = orphan_metadata("unmapped")
    create_store(path, metadata, [account_row(), common_row(2, ent_id, "orphan")])

    with DatabaseAccessor(path) as accessor:
        with pytest.raises(DatabaseSchemaError, match=f"^{UNCLASSIFIABLE}$"):
            operation(accessor)


def test_unobserved_broken_metadata_and_absent_optional_roots_are_allowed(
    tmp_path,
) -> None:
    path = tmp_path / "unused-broken-metadata.sqlite"
    create_store(path, [(8, "SyncObject", 0), (90, "FutureUnused", 99)], [])

    with DatabaseAccessor(path) as accessor:
        reports = [
            manager_type().load(accessor)
            for manager_type in (
                AccountManager,
                PayeeManager,
                CategoryManager,
                TransactionManager,
                InvestmentHoldingManager,
                TagManager,
            )
        ]

    assert all(report.source_count == 0 and report.complete for report in reports)


def test_known_unrelated_malformed_row_does_not_block_account_scope(tmp_path) -> None:
    path = tmp_path / "known-unrelated.sqlite"
    metadata = [
        (8, "SyncObject", 0),
        (9, "Account", 8),
        (10, "CashAccount", 9),
        (80, "KnownUnrelated", 8),
    ]
    create_store(path, metadata, [account_row(), common_row(2, 80, "unrelated")])

    with DatabaseAccessor(path) as accessor:
        report = AccountManager().load(accessor)

    assert report.complete
    assert report.source_ids == (1,)
    assert report.parsed_ids == (1,)


def test_api_initial_orphan_failure_closes_accessor(tmp_path, monkeypatch) -> None:
    import moneywiz_api.moneywiz_api as api_module

    path = tmp_path / "initial-orphan.sqlite"
    metadata, ent_id = orphan_metadata("unmapped")
    create_store(path, metadata, [common_row(2, ent_id, "orphan")])
    accessor = DatabaseAccessor(path)
    monkeypatch.setattr(api_module, "DatabaseAccessor", lambda _path: accessor)

    with pytest.raises(DatabaseSchemaError, match=f"^{UNCLASSIFIABLE}$"):
        api_module.MoneywizApi(path, managers=("accounts",))

    with pytest.raises(sqlite3.ProgrammingError, match="closed"):
        accessor._con.execute("SELECT 1")


def test_same_schema_orphan_reload_preserves_generation_and_can_retry(tmp_path) -> None:
    path = tmp_path / "reload-orphan.sqlite"
    metadata, _ = orphan_metadata("unmapped")
    create_store(path, metadata, [account_row()])

    with MoneywizApi(path, managers=("accounts",)) as api:
        manager = api.account_manager
        record = manager.get(1)
        report = manager.load_report
        writer = sqlite3.connect(path)
        insert_row(writer, common_row(2, 99, "orphan"))
        writer.commit()
        writer.close()

        with pytest.raises(DatabaseSchemaError, match=f"^{UNCLASSIFIABLE}$"):
            api.load(("accounts",))

        assert api.account_manager is manager
        assert manager.get(1) is record
        assert manager.load_report is report

        writer = sqlite3.connect(path)
        writer.execute("DELETE FROM ZSYNCOBJECT WHERE Z_PK = 2")
        writer.commit()
        writer.close()

        retry = api.load(("accounts",)).managers["accounts"]

        assert retry.complete
        assert retry.source_ids == (1,)


def test_explicit_query_names_do_not_expand_to_other_known_scopes(tmp_path) -> None:
    path = tmp_path / "exact-query.sqlite"
    metadata = [
        (8, "SyncObject", 0),
        (9, "Account", 8),
        (10, "CashAccount", 9),
        (28, "Payee", 8),
    ]
    create_store(path, metadata, [account_row(), payee_row()])

    with DatabaseAccessor(path) as accessor:
        rows = accessor.query_objects(["CashAccount"])

    assert [row["Z_PK"] for row in rows] == [1]
