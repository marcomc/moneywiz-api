"""Schema capability profiles for MoneyWiz SQLite stores."""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass


@dataclass(frozen=True)
class SchemaProfile:
    """Capabilities inferred from the physical store schema."""

    profile_id: str
    number_of_shares_column: str | None
    price_per_share_column: str | None

    @property
    def is_known(self) -> bool:
        return self.profile_id != "unknown"


def detect_schema_profile(connection: sqlite3.Connection) -> SchemaProfile:
    """Detect a read profile from columns, not from Core Data metadata alone."""
    columns = set()
    for row in connection.execute("PRAGMA table_info(ZSYNCOBJECT)").fetchall():
        columns.add(str(row["name"] if isinstance(row, dict) else row[1]))
    has_suffixed_shares = "ZNUMBEROFSHARES1" in columns
    has_unsuffixed_shares = "ZNUMBEROFSHARES" in columns
    has_suffixed_price = "ZPRICEPERSHARE1" in columns
    has_unsuffixed_price = "ZPRICEPERSHARE" in columns

    if has_suffixed_shares and has_suffixed_price:
        profile_id = "suffixed-investment-columns"
        number_of_shares_column = "ZNUMBEROFSHARES1"
        price_per_share_column = "ZPRICEPERSHARE1"
    elif has_unsuffixed_shares and has_unsuffixed_price:
        profile_id = "unsuffixed-investment-columns"
        number_of_shares_column = "ZNUMBEROFSHARES"
        price_per_share_column = "ZPRICEPERSHARE"
    elif (has_suffixed_shares or has_unsuffixed_shares) and (
        has_suffixed_price or has_unsuffixed_price
    ):
        profile_id = "mixed-investment-columns"
        number_of_shares_column = (
            "ZNUMBEROFSHARES1" if has_suffixed_shares else "ZNUMBEROFSHARES"
        )
        price_per_share_column = (
            "ZPRICEPERSHARE1" if has_suffixed_price else "ZPRICEPERSHARE"
        )
    else:
        profile_id = "unknown"
        number_of_shares_column = None
        price_per_share_column = None

    return SchemaProfile(
        profile_id=profile_id,
        number_of_shares_column=number_of_shares_column,
        price_per_share_column=price_per_share_column,
    )
