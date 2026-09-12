import sqlite3

import pytest

from moneywiz_api.database_accessor import (
    DatabaseAccessor,
    DatabasePathError,
    DatabaseSchemaError,
)


def create_schema(path) -> None:
    connection = sqlite3.connect(path)
    connection.execute(
        "CREATE TABLE Z_PRIMARYKEY "
        "(Z_ENT INTEGER PRIMARY KEY, Z_NAME TEXT, Z_SUPER INTEGER, Z_MAX INTEGER)"
    )
    connection.execute("CREATE TABLE ZSYNCOBJECT (Z_PK INTEGER, Z_ENT INTEGER)")
    connection.executemany(
        "INSERT INTO Z_PRIMARYKEY VALUES (?, ?, ?, ?)",
        [
            (8, "SyncObject", 0, 0),
            (9, "Account", 8, 0),
            (10, "CashAccount", 9, 0),
            (11, "FutureAccount", 9, 0),
            (12, "OnlineAccount", 8, 0),
        ],
    )
    connection.commit()
    connection.close()


def test_accessor_requires_an_existing_database(tmp_path) -> None:
    with pytest.raises(DatabasePathError):
        DatabaseAccessor(tmp_path / "missing.sqlite")


def test_accessor_rejects_non_moneywiz_sqlite(tmp_path) -> None:
    path = tmp_path / "other.sqlite"
    sqlite3.connect(path).close()

    with pytest.raises(DatabaseSchemaError):
        DatabaseAccessor(path)


def test_accessor_is_read_only_and_discovers_true_descendants(tmp_path) -> None:
    path = tmp_path / "moneywiz.sqlite"
    create_schema(path)

    with DatabaseAccessor(path) as accessor:
        assert accessor.descendant_typenames(("Account",)) == [
            "Account",
            "CashAccount",
            "FutureAccount",
        ]
        with pytest.raises(sqlite3.OperationalError, match="readonly"):
            accessor._con.execute("CREATE TABLE mutation (id INTEGER)")


class TrackingConnection(sqlite3.Connection):
    was_closed = False

    def close(self):
        self.was_closed = True
        super().close()


@pytest.mark.parametrize("super_definition", ["", ", Z_SUPER INTEGER"])
def test_metadata_failures_close_connection_and_raise_schema_error(
    tmp_path, monkeypatch, super_definition
) -> None:
    path = tmp_path / "malformed.sqlite"
    original_connect = sqlite3.connect
    setup_connection = original_connect(path)
    setup_connection.execute(
        "CREATE TABLE Z_PRIMARYKEY "
        f"(Z_ENT INTEGER PRIMARY KEY, Z_NAME TEXT{super_definition})"
    )
    setup_connection.execute("CREATE TABLE ZSYNCOBJECT (Z_PK INTEGER, Z_ENT INTEGER)")
    if super_definition:
        setup_connection.execute(
            "INSERT INTO Z_PRIMARYKEY (Z_ENT, Z_NAME, Z_SUPER) "
            "VALUES (8, 'SyncObject', NULL)"
        )
    setup_connection.commit()
    setup_connection.close()
    opened = []

    def tracked_connect(*args, **kwargs):
        connection = original_connect(*args, factory=TrackingConnection, **kwargs)
        opened.append(connection)
        return connection

    monkeypatch.setattr(sqlite3, "connect", tracked_connect)

    with pytest.raises(DatabaseSchemaError):
        DatabaseAccessor(path)

    assert len(opened) == 1
    assert opened[0].was_closed
