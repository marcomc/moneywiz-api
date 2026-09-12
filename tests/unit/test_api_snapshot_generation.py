import sqlite3

import pytest

from moneywiz_api import MoneywizApi


def create_read_schema(path) -> None:
    connection = sqlite3.connect(path)
    connection.execute(
        "CREATE TABLE Z_PRIMARYKEY "
        "(Z_ENT INTEGER PRIMARY KEY, Z_NAME TEXT, Z_SUPER INTEGER, Z_MAX INTEGER)"
    )
    connection.executemany(
        "INSERT INTO Z_PRIMARYKEY VALUES (?, ?, ?, ?)",
        [
            (8, "SyncObject", 0, 0),
            (9, "Account", 8, 0),
            (10, "CashAccount", 9, 0),
            (36, "Transaction", 8, 0),
            (37, "DepositTransaction", 36, 0),
        ],
    )
    connection.execute(
        "CREATE TABLE ZSYNCOBJECT ("
        "Z_PK INTEGER PRIMARY KEY, Z_ENT INTEGER, ZOBJECTCREATIONDATE FLOAT, "
        "ZGID TEXT, ZDISPLAYORDER INTEGER, ZGROUPID INTEGER, ZNAME TEXT, "
        "ZCURRENCYNAME TEXT, ZOPENINGBALANCE FLOAT, ZINFO TEXT, ZUSER INTEGER, "
        "ZRECONCILED INTEGER, ZAMOUNT1 FLOAT, ZDESC2 TEXT, ZDATE1 FLOAT, "
        "ZNOTES1 TEXT, ZACCOUNT2 INTEGER, ZPAYEE2 INTEGER, "
        "ZORIGINALCURRENCY TEXT, ZORIGINALAMOUNT FLOAT, "
        "ZORIGINALEXCHANGERATE FLOAT)"
    )
    connection.execute(
        "INSERT INTO ZSYNCOBJECT "
        "(Z_PK, Z_ENT, ZOBJECTCREATIONDATE, ZGID, ZDISPLAYORDER, ZGROUPID, "
        "ZNAME, ZCURRENCYNAME, ZOPENINGBALANCE, ZINFO, ZUSER) "
        "VALUES (1, 10, 0, 'account-1', 1, 1, 'Before', 'EUR', 0, NULL, 1)"
    )
    connection.commit()
    connection.close()


def mutate_between_loads(path) -> None:
    connection = sqlite3.connect(path)
    connection.execute("UPDATE ZSYNCOBJECT SET ZNAME = 'After' WHERE Z_PK = 1")
    connection.execute(
        "INSERT INTO ZSYNCOBJECT "
        "(Z_PK, Z_ENT, ZOBJECTCREATIONDATE, ZGID, ZRECONCILED, ZAMOUNT1, "
        "ZDESC2, ZDATE1, ZNOTES1, ZACCOUNT2, ZPAYEE2, ZORIGINALCURRENCY, "
        "ZORIGINALAMOUNT, ZORIGINALEXCHANGERATE) "
        "VALUES (2, 37, 0, 'transaction-2', 0, 5, 'Income', 0, NULL, 1, "
        "NULL, 'EUR', 5, 1)"
    )
    connection.commit()
    connection.close()


@pytest.mark.parametrize(
    ("initial_managers", "expected_managers"),
    [
        (("accounts",), ("accounts", "transactions")),
        (None, MoneywizApi.MANAGER_NAMES),
    ],
)
def test_scoped_reload_refreshes_loaded_union_atomically(
    tmp_path, initial_managers, expected_managers
) -> None:
    path = tmp_path / "generation.sqlite"
    create_read_schema(path)

    with MoneywizApi(path, managers=initial_managers) as api:
        assert api.account_manager.get(1).name == "Before"
        mutate_between_loads(path)

        completeness = api.load(("transactions",))
        snapshot = api.snapshot().as_dict()

        assert tuple(completeness.managers) == expected_managers
        assert api.account_manager.get(1).name == "After"
        assert api.transaction_manager.get(2).description == "Income"
        assert tuple(snapshot["completeness"]["managers"]) == expected_managers
        assert snapshot["records"]["accounts"][0]["name"] == "After"
