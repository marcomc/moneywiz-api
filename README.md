# MoneyWiz-API

![Static Badge](https://img.shields.io/badge/Python-3-blue?style=flat&logo=Python)
![PyPI](https://img.shields.io/pypi/v/moneywiz-api)

<a href="https://www.buymeacoffee.com/Ileodo" target="_blank"><img src="https://cdn.buymeacoffee.com/buttons/v2/default-blue.png" alt="Buy Me A Coffee" style="height: 60px !important;width: 217px !important;" ></a>

A Python API to access MoneyWiz Sqlite database.

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

## Using a Test Database

Whether you are building a CLI, a service, or a notebook on top of this API, you will need access to a MoneyWiz SQLite file. Options:

1. **Use your own export** – point `MoneywizApi("/path/to/ipadMoneyWiz.sqlite")` to the file you sync from MoneyWiz.
2. **Work with a copy** – duplicate the file before experimenting so the original remains untouched:
   ```bash
   cp /path/to/ipadMoneyWiz.sqlite /tmp/moneywiz_dev.sqlite
   ```
   Then pass `/tmp/moneywiz_dev.sqlite` to `MoneywizApi` (or to `moneywiz-cli --db ...`).
3. **Ship a fixture with your project** – include a sanitized `.sqlite` under `tests/` (for example `tests/test_db.sqlite`) and load it in tests or samples:
   ```python
   from pathlib import Path
   TEST_DB = Path(__file__).resolve().parents[1] / "tests/test_db.sqlite"
   api = MoneywizApi(TEST_DB)
   ```

The API only reads the database; it does not mutate tables unless your code explicitly uses the write helpers. This makes it safe to check small fixtures into source control for automated testing.

## Contribution

This project is in very early stage, all contributions are welcomed!
