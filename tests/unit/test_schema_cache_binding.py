from contextlib import contextmanager
from pathlib import Path
import sqlite3

import pytest

from moneywiz_api import MoneywizApi
from moneywiz_api.database_accessor import DatabaseAccessor, DatabaseSchemaError
from moneywiz_api.model.investment_holding import InvestmentHolding
from moneywiz_api.schema_profile import detect_schema_profile


SCHEMA_CHANGED = "database schema changed; close and reopen the accessor"


def open_writer(path: Path):
    connection = sqlite3.connect(path)
    connection.execute("PRAGMA journal_mode=WAL")
    return connection


def create_account_store(path: Path, *, extra_metadata=()) -> None:
    connection = open_writer(path)
    connection.execute(
        "CREATE TABLE Z_PRIMARYKEY "
        "(Z_ENT INTEGER, Z_NAME TEXT, Z_SUPER INTEGER, Z_MAX INTEGER)"
    )
    connection.executemany(
        "INSERT INTO Z_PRIMARYKEY VALUES (?, ?, ?, ?)",
        [
            (8, "SyncObject", 0, 0),
            (9, "Account", 8, 0),
            (10, "CashAccount", 9, 1),
            *extra_metadata,
        ],
    )
    connection.execute(
        "CREATE TABLE ZSYNCOBJECT ("
        "Z_PK INTEGER PRIMARY KEY, Z_ENT INTEGER, ZOBJECTCREATIONDATE FLOAT, "
        "ZGID TEXT, ZDISPLAYORDER INTEGER, ZGROUPID INTEGER, ZNAME TEXT, "
        "ZCURRENCYNAME TEXT, ZOPENINGBALANCE FLOAT, ZINFO TEXT, ZUSER INTEGER)"
    )
    connection.execute(
        "INSERT INTO ZSYNCOBJECT VALUES "
        "(1, 10, 0, 'account-1', 1, 1, 'Before', 'EUR', 0, NULL, 1)"
    )
    connection.commit()
    connection.close()


def remap_cash_account(path: Path) -> None:
    connection = open_writer(path)
    connection.execute(
        "UPDATE Z_PRIMARYKEY SET Z_NAME = 'RetiredCashAccount' WHERE Z_ENT = 10"
    )
    connection.execute("INSERT INTO Z_PRIMARYKEY VALUES (11, 'CashAccount', 9, 1)")
    connection.execute(
        "UPDATE ZSYNCOBJECT SET Z_ENT = 11, ZNAME = 'After' WHERE Z_PK = 1"
    )
    connection.commit()
    connection.close()


def create_holding_store(path: Path) -> None:
    connection = open_writer(path)
    connection.execute(
        "CREATE TABLE Z_PRIMARYKEY "
        "(Z_ENT INTEGER, Z_NAME TEXT, Z_SUPER INTEGER, Z_MAX INTEGER)"
    )
    connection.executemany(
        "INSERT INTO Z_PRIMARYKEY VALUES (?, ?, ?, ?)",
        [(8, "SyncObject", 0, 0), (24, "InvestmentHolding", 8, 1)],
    )
    connection.execute(
        "CREATE TABLE ZSYNCOBJECT ("
        "Z_PK INTEGER PRIMARY KEY, Z_ENT INTEGER, ZOBJECTCREATIONDATE FLOAT, "
        "ZGID TEXT, ZINVESTMENTACCOUNT INTEGER, ZOPENNINGNUMBEROFSHARES FLOAT, "
        "ZNUMBEROFSHARES FLOAT, ZPRICEPERSHARE FLOAT, ZSYMBOL TEXT, "
        "ZHOLDINGTYPE TEXT, ZDESC TEXT, ZISPRICEPERSHAREAVAILABLEONLINE INTEGER, "
        "ZINVESTMENTOBJECTTYPE INTEGER, ZCOSTBASISOFMISSINGOBSHARES FLOAT)"
    )
    connection.execute(
        "INSERT INTO ZSYNCOBJECT VALUES "
        "(1, 24, 0, 'holding-1', 5, NULL, 2, 10, 'ACME', NULL, 'Acme', 0, 0, 0)"
    )
    connection.commit()
    connection.close()


def migrate_holding_aliases(path: Path) -> None:
    connection = open_writer(path)
    connection.execute(
        "ALTER TABLE ZSYNCOBJECT RENAME COLUMN ZNUMBEROFSHARES TO ZNUMBEROFSHARES1"
    )
    connection.execute(
        "ALTER TABLE ZSYNCOBJECT RENAME COLUMN ZPRICEPERSHARE TO ZPRICEPERSHARE1"
    )
    connection.commit()
    connection.close()


def create_relationship_store(path: Path) -> None:
    create_account_store(
        path,
        extra_metadata=(
            (2, "CategoryAssigment", 8, 1),
            (36, "Tag", 8, 1),
            (37, "Transaction", 8, 1),
            (50, "WithdrawRefundTransactionLink", 8, 1),
        ),
    )
    connection = open_writer(path)
    connection.execute(
        "CREATE TABLE ZCATEGORYASSIGMENT "
        "(Z_PK INTEGER, ZCATEGORY INTEGER, ZTRANSACTION INTEGER, ZAMOUNT FLOAT)"
    )
    connection.execute("INSERT INTO ZCATEGORYASSIGMENT VALUES (1, 20, 30, 5)")
    connection.execute(
        "CREATE TABLE ZWITHDRAWREFUNDTRANSACTIONLINK "
        "(Z_PK INTEGER, ZREFUNDTRANSACTION INTEGER, ZWITHDRAWTRANSACTION INTEGER)"
    )
    connection.execute("INSERT INTO ZWITHDRAWREFUNDTRANSACTIONLINK VALUES (2, 31, 32)")
    connection.execute(
        "CREATE TABLE Z_37TAGS (Z_37TRANSACTIONS INTEGER, Z_36TAGS INTEGER)"
    )
    connection.execute("INSERT INTO Z_37TAGS VALUES (30, 40)")
    connection.commit()
    connection.close()


def assert_old_account_generation(
    api, manager, record, report, schema_identity
) -> None:
    assert api.account_manager is manager
    assert manager.get(1) is record
    assert manager.get_by_gid("account-1") is record
    assert manager.load_report is report
    assert api.accessor._schema_identity is schema_identity
    assert api._loaded_managers == {"accounts"}
    snapshot = api.snapshot(("accounts",)).as_dict()
    assert snapshot["records"]["accounts"][0]["name"] == "Before"
    assert snapshot["completeness"]["managers"]["accounts"] == report.as_dict()


def test_metadata_remap_refuses_and_preserves_published_generation(tmp_path) -> None:
    path = tmp_path / "metadata-remap.sqlite"
    create_account_store(path)
    with MoneywizApi(path, managers=("accounts",)) as api:
        manager = api.account_manager
        record = manager.get(1)
        records = manager._records
        gid_index = manager._gid_to_id
        report = manager.load_report
        schema_identity = api.accessor._schema_identity
        cached_maps = (
            api.accessor._ent_to_typename,
            api.accessor._ent_to_super,
            api.accessor._typename_to_ent,
        )
        remap_cash_account(path)

        with pytest.raises(DatabaseSchemaError, match=f"^{SCHEMA_CHANGED}$"):
            api.load(("accounts",))

        assert manager._records is records
        assert manager._gid_to_id is gid_index
        assert (
            api.accessor._ent_to_typename,
            api.accessor._ent_to_super,
            api.accessor._typename_to_ent,
        ) == cached_maps
        assert_old_account_generation(api, manager, record, report, schema_identity)
        assert not api.accessor._con.in_transaction

    with MoneywizApi(path, managers=("accounts",)) as reopened:
        assert reopened.account_manager.get(1).name == "After"
        assert reopened.completeness().managers["accounts"].complete


@pytest.mark.parametrize(
    "mutation",
    [
        "UPDATE Z_PRIMARYKEY SET Z_NAME = 'CashAccount2' WHERE Z_ENT = 10",
        "UPDATE Z_PRIMARYKEY SET Z_SUPER = 8 WHERE Z_ENT = 10",
        "INSERT INTO Z_PRIMARYKEY VALUES (11, 'FutureAccount', 9, 0)",
        "DELETE FROM Z_PRIMARYKEY WHERE Z_ENT = 10",
    ],
)
def test_each_entity_metadata_change_refuses_reload(tmp_path, mutation) -> None:
    path = tmp_path / "metadata-change.sqlite"
    create_account_store(path)
    with MoneywizApi(path, managers=("accounts",)) as api:
        connection = open_writer(path)
        connection.execute(mutation)
        connection.commit()
        connection.close()

        with pytest.raises(DatabaseSchemaError, match=f"^{SCHEMA_CHANGED}$"):
            api.load(("accounts",))

        assert api.account_manager.get(1).name == "Before"


def test_complete_metadata_inventory_has_no_thousand_row_cutoff(tmp_path) -> None:
    path = tmp_path / "large-metadata.sqlite"
    filler = tuple((ent_id, f"Entity{ent_id}", 8, 0) for ent_id in range(11, 1011))
    create_account_store(path, extra_metadata=filler)
    connection = open_writer(path)
    connection.execute(
        "UPDATE Z_PRIMARYKEY SET Z_ENT = 1011 WHERE Z_NAME = 'CashAccount'"
    )
    connection.execute("UPDATE ZSYNCOBJECT SET Z_ENT = 1011")
    connection.commit()
    connection.close()

    with MoneywizApi(path, managers=("accounts",)) as api:
        assert len(api.accessor._schema_identity[1]) == 1003
        assert api.accessor.ent_for("CashAccount") == 1011
        assert "CashAccount" in api.accessor.descendant_typenames(("Account",))
        assert api.account_manager.get(1).name == "Before"


@pytest.mark.parametrize("duplicate", ["id", "name"])
def test_duplicate_entity_map_keys_are_rejected(tmp_path, duplicate) -> None:
    path = tmp_path / f"duplicate-{duplicate}.sqlite"
    create_account_store(path)
    connection = open_writer(path)
    if duplicate == "id":
        connection.execute(
            "INSERT INTO Z_PRIMARYKEY VALUES (10, 'OtherCashAccount', 9, 0)"
        )
    else:
        connection.execute("INSERT INTO Z_PRIMARYKEY VALUES (11, 'CashAccount', 9, 0)")
    connection.commit()
    connection.close()

    with pytest.raises(DatabaseSchemaError, match="duplicate entity"):
        DatabaseAccessor(path)


def test_physical_alias_drift_refuses_profiled_getters(tmp_path) -> None:
    path = tmp_path / "holding-aliases.sqlite"
    create_holding_store(path)
    with DatabaseAccessor(path) as accessor:
        baseline_profile = accessor.schema_profile
        assert accessor.get_record(1, InvestmentHolding).number_of_shares == 2
        migrate_holding_aliases(path)

        for operation in (
            lambda: accessor.get_record(1, InvestmentHolding),
            lambda: accessor.get_record_by_gid("holding-1", InvestmentHolding),
        ):
            with pytest.raises(DatabaseSchemaError, match=f"^{SCHEMA_CHANGED}$"):
                operation()
        assert accessor.schema_profile is baseline_profile
        assert not accessor._con.in_transaction

    with DatabaseAccessor(path) as reopened:
        assert reopened.schema_profile.profile_id == "suffixed-investment-columns"
        assert reopened.get_record(1, InvestmentHolding).number_of_shares == 2


@pytest.mark.parametrize(
    "reader",
    [
        "query_objects",
        "read_category_assignments",
        "get_category_assignment",
        "read_refund_maps",
        "get_refund_maps",
        "read_tags_map",
        "get_tags_map",
    ],
)
def test_every_cache_dependent_reader_refuses_physical_drift(tmp_path, reader) -> None:
    path = tmp_path / f"reader-{reader}.sqlite"
    create_relationship_store(path)
    with DatabaseAccessor(path) as accessor:
        initial_profile = accessor.schema_profile
        connection = open_writer(path)
        connection.execute("ALTER TABLE ZCATEGORYASSIGMENT ADD COLUMN ZEXTRA")
        connection.commit()
        connection.close()
        assert detect_schema_profile(accessor._con) == initial_profile

        operation = getattr(accessor, reader)
        with pytest.raises(DatabaseSchemaError, match=f"^{SCHEMA_CHANGED}$"):
            operation(["CashAccount"]) if reader == "query_objects" else operation()


@pytest.mark.parametrize(
    "ddl",
    [
        "CREATE TABLE Z_99TAGS (Z_99TRANSACTIONS INTEGER, Z_36TAGS INTEGER)",
        "DROP TABLE ZCATEGORYASSIGMENT",
        "ALTER TABLE ZCATEGORYASSIGMENT ADD COLUMN ZEXTRA",
    ],
)
def test_relationship_schema_create_drop_and_alter_refuse_with_equal_profile(
    tmp_path, ddl
) -> None:
    path = tmp_path / "relationship-ddl.sqlite"
    create_relationship_store(path)
    with DatabaseAccessor(path) as accessor:
        profile = accessor.schema_profile
        connection = open_writer(path)
        connection.execute(ddl)
        connection.commit()
        connection.close()
        assert detect_schema_profile(accessor._con) == profile

        with pytest.raises(DatabaseSchemaError, match=f"^{SCHEMA_CHANGED}$"):
            accessor.read_tags_map()


def test_schema_refusal_preserves_transaction_relationship_generation(tmp_path) -> None:
    path = tmp_path / "relationship-state.sqlite"
    create_relationship_store(path)
    with MoneywizApi(path, managers=("accounts", "transactions")) as api:
        manager = api.transaction_manager
        report = manager.load_report
        categories = manager.category_assignment
        refunds = manager.refund_maps
        tags = manager.tags_map
        records = manager._records
        gid_index = manager._gid_to_id
        schema_identity = api.accessor._schema_identity
        loaded_managers = set(api._loaded_managers)
        connection = open_writer(path)
        connection.execute("ALTER TABLE ZCATEGORYASSIGMENT ADD COLUMN ZEXTRA")
        connection.commit()
        connection.close()

        with pytest.raises(DatabaseSchemaError, match=f"^{SCHEMA_CHANGED}$"):
            api.load(("transactions",))

        assert api.transaction_manager is manager
        assert manager.load_report is report
        assert manager.category_assignment is categories
        assert manager.refund_maps is refunds
        assert manager.tags_map is tags
        assert manager._records is records
        assert manager._gid_to_id is gid_index
        assert api.accessor._schema_identity is schema_identity
        assert api._loaded_managers == loaded_managers
        assert api.completeness().managers["transactions"] is report


def test_valid_direct_and_nested_reads_share_snapshot_ownership(tmp_path) -> None:
    path = tmp_path / "nested.sqlite"
    create_relationship_store(path)
    with DatabaseAccessor(path) as accessor:
        assert not accessor._con.in_transaction
        with accessor.read_transaction():
            assert accessor._con.in_transaction
            assert accessor.query_objects(["CashAccount"])[0]["Z_PK"] == 1
            assert accessor.get_record(1).gid == "account-1"
            assert accessor.get_record_by_gid("account-1").id == 1
            assert accessor.read_category_assignments()[0] == {30: [(20, 5)]}
            assert accessor.get_category_assignment() == {30: [(20, 5)]}
            assert accessor.read_refund_maps()[0] == {31: 32}
            assert accessor.get_refund_maps() == {31: 32}
            assert accessor.read_tags_map()[0] == {30: [40]}
            assert accessor.get_tags_map() == {30: [40]}
            with accessor.read_transaction():
                assert accessor._con.in_transaction
            assert accessor._con.in_transaction
        assert not accessor._con.in_transaction


def test_normal_data_and_zmax_growth_reload_successfully(tmp_path) -> None:
    path = tmp_path / "growth.sqlite"
    create_account_store(path)
    with MoneywizApi(path, managers=("accounts",)) as api:
        manager = api.account_manager
        connection = open_writer(path)
        connection.execute("UPDATE Z_PRIMARYKEY SET Z_MAX = 2 WHERE Z_ENT = 10")
        connection.execute("UPDATE ZSYNCOBJECT SET ZNAME = 'Edited' WHERE Z_PK = 1")
        connection.execute(
            "INSERT INTO ZSYNCOBJECT VALUES "
            "(2, 10, 0, 'account-2', 2, 1, 'Added', 'EUR', 0, NULL, 1)"
        )
        connection.commit()
        connection.close()

        report = api.load(("accounts",)).managers["accounts"]

        assert api.account_manager is manager
        assert report.complete
        assert report.source_ids == (1, 2)
        assert manager.get(1).name == "Edited"
        assert manager.get(2).name == "Added"


def test_initialization_publishes_binding_only_after_transaction_exit(
    tmp_path, monkeypatch
) -> None:
    path = tmp_path / "initialization-publication.sqlite"
    create_account_store(path)
    original_transaction = DatabaseAccessor._raw_read_transaction
    exit_states = []

    @contextmanager
    def observe_transaction(accessor):
        with original_transaction(accessor):
            yield
        exit_states.append(
            (hasattr(accessor, "_schema_identity"), accessor._con.in_transaction)
        )

    monkeypatch.setattr(DatabaseAccessor, "_raw_read_transaction", observe_transaction)

    with DatabaseAccessor(path) as accessor:
        assert exit_states == [(False, False)]
        assert accessor._schema_identity


def test_missing_schema_binding_cannot_bypass_public_guard() -> None:
    accessor = DatabaseAccessor.__new__(DatabaseAccessor)
    accessor._con = sqlite3.connect(":memory:")

    with pytest.raises(DatabaseSchemaError, match="cache is not initialized"):
        accessor.query_objects([])

    assert not accessor._con.in_transaction
    accessor.close()


def test_initialization_race_binds_one_old_snapshot_then_refuses(tmp_path, monkeypatch):
    import moneywiz_api.database_accessor as accessor_module

    path = tmp_path / "initialization-race.sqlite"
    create_holding_store(path)
    original_detect = accessor_module.detect_schema_profile
    migration_committed = False

    def migrate_then_detect(connection):
        nonlocal migration_committed
        writer = open_writer(path)
        writer.execute(
            "ALTER TABLE ZSYNCOBJECT RENAME COLUMN ZNUMBEROFSHARES TO ZNUMBEROFSHARES1"
        )
        writer.execute(
            "ALTER TABLE ZSYNCOBJECT RENAME COLUMN ZPRICEPERSHARE TO ZPRICEPERSHARE1"
        )
        writer.execute(
            "UPDATE Z_PRIMARYKEY SET Z_NAME = 'RetiredHolding' WHERE Z_ENT = 24"
        )
        writer.execute(
            "INSERT INTO Z_PRIMARYKEY VALUES (25, 'InvestmentHolding', 8, 1)"
        )
        writer.execute("UPDATE ZSYNCOBJECT SET Z_ENT = 25")
        writer.commit()
        writer.close()
        migration_committed = True
        return original_detect(connection)

    monkeypatch.setattr(accessor_module, "detect_schema_profile", migrate_then_detect)
    accessor = DatabaseAccessor(path)

    assert migration_committed
    assert accessor.schema_profile.profile_id == "unsuffixed-investment-columns"
    assert accessor.ent_for("InvestmentHolding") == 24
    assert (24, "InvestmentHolding", 8) in accessor._schema_identity[1]
    assert (25, "InvestmentHolding", 8) not in accessor._schema_identity[1]
    with accessor._raw_read_transaction():
        assert accessor._read_schema_identity() != accessor._schema_identity
    with pytest.raises(DatabaseSchemaError, match=f"^{SCHEMA_CHANGED}$"):
        accessor.query_objects(["InvestmentHolding"])
    accessor.close()

    monkeypatch.setattr(accessor_module, "detect_schema_profile", original_detect)
    with DatabaseAccessor(path) as reopened:
        assert reopened.schema_profile.profile_id == "suffixed-investment-columns"
        assert reopened.ent_for("InvestmentHolding") == 25


def test_first_load_schema_race_closes_constructor_accessor(tmp_path, monkeypatch):
    import moneywiz_api.moneywiz_api as api_module

    path = tmp_path / "first-load.sqlite"
    create_account_store(path)
    accessor = DatabaseAccessor(path)
    remap_cash_account(path)
    monkeypatch.setattr(api_module, "DatabaseAccessor", lambda _path: accessor)

    with pytest.raises(DatabaseSchemaError, match=f"^{SCHEMA_CHANGED}$"):
        api_module.MoneywizApi(path, managers=("accounts",))

    with pytest.raises(sqlite3.ProgrammingError, match="closed"):
        accessor._con.execute("SELECT 1")


def test_reload_race_reads_old_snapshot_then_next_load_refuses(
    tmp_path, monkeypatch
) -> None:
    path = tmp_path / "reload-race.sqlite"
    create_account_store(path)
    with MoneywizApi(path, managers=("accounts",)) as api:
        original_verify = api.accessor._verify_schema_identity
        migration_committed = False

        def verify_then_migrate():
            nonlocal migration_committed
            original_verify()
            if not migration_committed:
                remap_cash_account(path)
                migration_committed = True

        monkeypatch.setattr(
            api.accessor, "_verify_schema_identity", verify_then_migrate
        )

        report = api.load(("accounts",)).managers["accounts"]

        assert migration_committed
        assert report.complete
        assert report.source_ids == (1,)
        assert api.account_manager.get(1).name == "Before"
        with pytest.raises(DatabaseSchemaError, match=f"^{SCHEMA_CHANGED}$"):
            api.load(("accounts",))
        assert api.account_manager.get(1).name == "Before"


def test_transient_signature_query_error_preserves_state_and_retry(
    tmp_path, monkeypatch
) -> None:
    path = tmp_path / "signature-error.sqlite"
    create_account_store(path)
    with MoneywizApi(path, managers=("accounts",)) as api:
        manager = api.account_manager
        record = manager.get(1)
        report = manager.load_report
        schema_identity = api.accessor._schema_identity
        original_reader = api.accessor._read_schema_identity

        def fail_signature_query():
            raise sqlite3.OperationalError("PRIVATE_SCHEMA_QUERY_ERROR")

        monkeypatch.setattr(api.accessor, "_read_schema_identity", fail_signature_query)
        with pytest.raises(DatabaseSchemaError, match="could not be verified") as error:
            api.load(("accounts",))
        assert "PRIVATE_SCHEMA_QUERY_ERROR" not in str(error.value)
        assert_old_account_generation(api, manager, record, report, schema_identity)
        assert not api.accessor._con.in_transaction

        monkeypatch.setattr(api.accessor, "_read_schema_identity", original_reader)
        connection = open_writer(path)
        connection.execute("UPDATE ZSYNCOBJECT SET ZNAME = 'Retried' WHERE Z_PK = 1")
        connection.commit()
        connection.close()
        assert api.load(("accounts",)).complete
        assert manager.get(1).name == "Retried"


def test_invalid_metadata_refusal_can_retry_after_metadata_restore(tmp_path) -> None:
    path = tmp_path / "invalid-metadata.sqlite"
    create_account_store(path)
    with MoneywizApi(path, managers=("accounts",)) as api:
        connection = open_writer(path)
        connection.execute("UPDATE Z_PRIMARYKEY SET Z_NAME = NULL WHERE Z_ENT = 10")
        connection.commit()
        connection.close()

        with pytest.raises(DatabaseSchemaError, match="invalid metadata"):
            api.load(("accounts",))
        assert api.account_manager.get(1).name == "Before"
        assert not api.accessor._con.in_transaction

        connection = open_writer(path)
        connection.execute(
            "UPDATE Z_PRIMARYKEY SET Z_NAME = 'CashAccount' WHERE Z_ENT = 10"
        )
        connection.execute("UPDATE ZSYNCOBJECT SET ZNAME = 'Restored' WHERE Z_PK = 1")
        connection.commit()
        connection.close()
        assert api.load(("accounts",)).complete
        assert api.account_manager.get(1).name == "Restored"


def test_existing_unverified_transaction_is_checked_and_not_rolled_back(
    tmp_path,
) -> None:
    path = tmp_path / "caller-transaction.sqlite"
    create_account_store(path)
    with DatabaseAccessor(path) as accessor:
        remap_cash_account(path)
        accessor._con.execute("BEGIN")

        with pytest.raises(DatabaseSchemaError, match=f"^{SCHEMA_CHANGED}$"):
            accessor.query_objects(["CashAccount"])

        assert accessor._con.in_transaction
        accessor._con.rollback()
