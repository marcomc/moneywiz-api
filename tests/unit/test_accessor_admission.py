from contextlib import contextmanager
import sqlite3

import pytest

from moneywiz_api import MoneywizApi
from moneywiz_api.database_accessor import DatabaseAccessor, DatabaseSchemaError
from moneywiz_api.managers.account_manager import AccountManager
from tests.unit.test_schema_cache_binding import (
    create_account_store,
    create_relationship_store,
    open_writer,
)


def count_admissions(monkeypatch):
    counts = {"schema": 0, "source": 0}
    original_schema = DatabaseAccessor._verify_schema_identity
    original_source = DatabaseAccessor._verify_source_eligibility

    def verify_schema(accessor):
        counts["schema"] += 1
        return original_schema(accessor)

    def verify_source(accessor):
        counts["source"] += 1
        return original_source(accessor)

    monkeypatch.setattr(DatabaseAccessor, "_verify_schema_identity", verify_schema)
    monkeypatch.setattr(DatabaseAccessor, "_verify_source_eligibility", verify_source)
    return counts


def assert_admission_reset(accessor) -> None:
    assert accessor._admission_depth == 0
    assert accessor._admission_broken is False
    assert not accessor._con.in_transaction


def test_eager_api_re_load_uses_one_admission(tmp_path, monkeypatch) -> None:
    path = tmp_path / "eager.sqlite"
    create_relationship_store(path)
    counts = count_admissions(monkeypatch)

    with MoneywizApi(path) as api:
        assert api.completeness().complete
        assert counts == {"schema": 1, "source": 1}


def test_direct_manager_and_independent_getters_admit_per_outer_scope(
    tmp_path, monkeypatch
) -> None:
    path = tmp_path / "direct.sqlite"
    create_account_store(path)
    counts = count_admissions(monkeypatch)

    with DatabaseAccessor(path) as accessor:
        assert AccountManager().load(accessor).complete
        assert counts == {"schema": 1, "source": 1}
        assert accessor.get_record(1).id == 1
        assert accessor.get_record_by_gid("account-1").id == 1
        assert counts == {"schema": 3, "source": 3}


def test_explicit_nested_scope_reuses_one_admission(tmp_path, monkeypatch) -> None:
    path = tmp_path / "nested.sqlite"
    create_account_store(path)
    counts = count_admissions(monkeypatch)

    with DatabaseAccessor(path) as accessor:
        with accessor.read_transaction():
            assert accessor.query_objects(["CashAccount"])
            assert accessor.get_record(1).id == 1
            assert accessor.get_record_by_gid("account-1").id == 1
            assert counts == {"schema": 1, "source": 1}
        assert_admission_reset(accessor)


@pytest.mark.parametrize("failure", ["schema", "source"])
def test_failed_admission_resets_and_allows_retry(
    tmp_path, monkeypatch, failure
) -> None:
    path = tmp_path / f"failed-{failure}.sqlite"
    create_account_store(path)
    accessor = DatabaseAccessor(path)
    method_name = (
        f"_verify_{failure}_eligibility"
        if failure == "source"
        else ("_verify_schema_identity")
    )
    original = getattr(accessor, method_name)

    def refuse():
        raise DatabaseSchemaError("synthetic admission refusal")

    monkeypatch.setattr(accessor, method_name, refuse)
    with pytest.raises(DatabaseSchemaError, match="synthetic admission refusal"):
        accessor.query_objects(["CashAccount"])
    assert_admission_reset(accessor)

    monkeypatch.setattr(accessor, method_name, original)
    assert len(accessor.query_objects(["CashAccount"])) == 1
    accessor.close()


def test_nested_body_failure_does_not_poison_active_or_later_scope(
    tmp_path, monkeypatch
) -> None:
    path = tmp_path / "body-error.sqlite"
    create_account_store(path)
    counts = count_admissions(monkeypatch)

    with DatabaseAccessor(path) as accessor:
        with accessor.read_transaction():
            with pytest.raises(RuntimeError, match="synthetic body failure"):
                with accessor.read_transaction():
                    raise RuntimeError("synthetic body failure")
            assert accessor.get_record(1).id == 1
            assert counts == {"schema": 1, "source": 1}
        assert accessor.query_objects(["CashAccount"])
        assert counts == {"schema": 2, "source": 2}


def test_outer_exit_failure_resets_admission_and_allows_retry(
    tmp_path, monkeypatch
) -> None:
    path = tmp_path / "exit-error.sqlite"
    create_account_store(path)
    accessor = DatabaseAccessor(path)
    original = accessor._raw_read_transaction

    @contextmanager
    def fail_after_exit():
        with original():
            yield
        raise DatabaseSchemaError("synthetic outer exit failure")

    monkeypatch.setattr(accessor, "_raw_read_transaction", fail_after_exit)
    with pytest.raises(DatabaseSchemaError, match="synthetic outer exit failure"):
        accessor.query_objects(["CashAccount"])
    assert_admission_reset(accessor)

    monkeypatch.setattr(accessor, "_raw_read_transaction", original)
    assert accessor.query_objects(["CashAccount"])
    accessor.close()


def test_public_close_inside_scope_refuses_interrupted_snapshot_and_cleans_depth(
    tmp_path,
) -> None:
    path = tmp_path / "closed.sqlite"
    create_account_store(path)
    accessor = DatabaseAccessor(path)

    with pytest.raises(DatabaseSchemaError, match="read transaction was interrupted"):
        with accessor.read_transaction():
            accessor.close()

    assert accessor._admission_depth == 0
    assert accessor._admission_broken is False


def test_simulated_transaction_loss_latches_until_outer_scope_unwinds(tmp_path) -> None:
    path = tmp_path / "simulated-loss.sqlite"
    create_account_store(path)

    with DatabaseAccessor(path) as accessor:
        with pytest.raises(
            DatabaseSchemaError, match="read transaction was interrupted"
        ):
            with accessor.read_transaction():
                # Simulate a SQLite statement aborting the transaction. This does not
                # assert that an interrupted SELECT itself performs a rollback.
                accessor._con.rollback()
                with accessor.read_transaction():
                    pytest.fail("lost outer snapshot must not admit a nested read")
        assert_admission_reset(accessor)
        assert accessor.query_objects(["CashAccount"])


def test_caller_owned_transaction_is_verified_per_public_outer_scope(
    tmp_path, monkeypatch
) -> None:
    path = tmp_path / "caller-owned.sqlite"
    create_account_store(path)
    counts = count_admissions(monkeypatch)

    with DatabaseAccessor(path) as accessor:
        accessor._con.execute("BEGIN")
        with accessor.read_transaction():
            assert accessor.get_record(1).id == 1
            assert counts == {"schema": 1, "source": 1}
        assert accessor._con.in_transaction
        assert accessor.query_objects(["CashAccount"])
        assert counts == {"schema": 2, "source": 2}
        assert accessor._con.in_transaction
        accessor._con.rollback()


def test_wal_orphan_remains_outside_pinned_generation_then_next_scope_refuses(
    tmp_path, monkeypatch
) -> None:
    path = tmp_path / "wal-orphan.sqlite"
    create_account_store(path)
    counts = count_admissions(monkeypatch)

    with DatabaseAccessor(path) as accessor:
        with accessor.read_transaction():
            assert len(accessor.query_objects(["CashAccount"])) == 1
            writer = open_writer(path)
            writer.execute(
                "INSERT INTO ZSYNCOBJECT VALUES "
                "(2, 999, 0, 'orphan', 2, 1, 'Orphan', 'EUR', 0, NULL, 1)"
            )
            writer.commit()
            writer.close()
            assert len(accessor.query_objects(["CashAccount"])) == 1
            assert counts == {"schema": 1, "source": 1}

        with pytest.raises(DatabaseSchemaError, match="unclassifiable entity ancestry"):
            accessor.query_objects(["CashAccount"])
        assert counts == {"schema": 2, "source": 2}

        writer = open_writer(path)
        writer.execute("DELETE FROM ZSYNCOBJECT WHERE Z_PK = 2")
        writer.commit()
        writer.close()
        assert len(accessor.query_objects(["CashAccount"])) == 1
        assert counts == {"schema": 3, "source": 3}


def test_nested_query_error_with_intact_transaction_reuses_admission(
    tmp_path, monkeypatch
) -> None:
    path = tmp_path / "query-error.sqlite"
    create_account_store(path)
    counts = count_admissions(monkeypatch)

    with DatabaseAccessor(path) as accessor:
        original = accessor._query_objects

        def fail_query(_typenames):
            raise sqlite3.OperationalError("synthetic query error")

        with accessor.read_transaction():
            monkeypatch.setattr(accessor, "_query_objects", fail_query)
            with pytest.raises(sqlite3.OperationalError, match="synthetic query error"):
                accessor.query_objects(["CashAccount"])
            monkeypatch.setattr(accessor, "_query_objects", original)
            assert accessor.query_objects(["CashAccount"])
            assert counts == {"schema": 1, "source": 1}
