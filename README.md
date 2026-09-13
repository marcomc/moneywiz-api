# MoneyWiz-API

![Static Badge](https://img.shields.io/badge/Python-3-blue?style=flat&logo=Python)
![PyPI](https://img.shields.io/pypi/v/moneywiz-api)

Support the original author on
[Buy Me a Coffee](https://www.buymeacoffee.com/Ileodo).

A Python API to access MoneyWiz Sqlite database.

## Table of Contents

- [Get Started](#get-started)
- [Bounded reads](#bounded-reads)
- [Contribution](#contribution)

## Get Started

```bash
pip install moneywiz-api
```

```python
from moneywiz_api import MoneywizApi

moneywizApi = MoneywizApi("<path_to_your_sqlite_file>")

(
    accessor,
    account_manager,
    payee_manager,
    category_manager,
    transaction_manager,
    investment_holding_manager,
) = (
    moneywizApi.accessor,
    moneywizApi.account_manager,
    moneywizApi.payee_manager,
    moneywizApi.category_manager,
    moneywizApi.transaction_manager,
    moneywizApi.investment_holding_manager,
)

record = accessor.get_record(record_id)
print(record)
```

It also offers a interactive shell `moneywiz-cli`.

## Tests

Run unit tests without a database:

```bash
uv run pytest tests/unit
```

Integration tests are opt-in and never read a CLI default database path. Point
`MONEYWIZ_TEST_DB_PATH` at a disposable MoneyWiz SQLite test database:

```bash
MONEYWIZ_TEST_DB_PATH=/absolute/path/to/test.sqlite uv run pytest tests
```

## Bounded reads

The default remains an eager load of every manager. Use an explicit manager list
to isolate a read from unrelated malformed records:

```python
from pathlib import Path

from moneywiz_api import MoneywizApi

with MoneywizApi(
    Path("/absolute/path/to/moneywiz.sqlite"),
    managers=("accounts", "transactions"),
) as api:
    completeness = api.completeness().as_dict()
    snapshot = api.snapshot().as_dict()
```

Supported manager names are `accounts`, `payees`, `categories`, `transactions`,
`investment_holdings`, and `tags`. `completeness` contains ordered source and
parsed IDs plus identity-only diagnostics for every skipped row. Transaction
completeness also reports category, refund, and tag relationship storage as
`present`, `absent`, or `unknown`; unknown or malformed storage makes the read
partial. Category relationship counts cover transaction assignments only; rows
owned by budgets, scheduled transactions, or history items are outside that
map. `snapshot` contains JSON-safe records, completeness, and the selected
schema profile.

A public manager that has not completed a load exposes a `load_report` with
status `unloaded` and `complete == False`. A successful zero-row load is instead
`complete`, so an empty result is distinguishable from an unobserved manager.

Database paths must identify existing files. Connections are opened read-only,
and each multi-manager load uses one consistent SQLite read transaction. A
later scoped load atomically refreshes the union of requested and already loaded
managers so one snapshot never mixes database generations. If a reload fails,
the previously published records and completeness evidence remain available.
Schema metadata and physical table definitions are bound when the accessor is
opened; if either changes, cache-dependent reads require closing and reopening
the API instead of combining current rows with stale schema information.

## Contribution

This project is in very early stage, all contributions are welcomed!
