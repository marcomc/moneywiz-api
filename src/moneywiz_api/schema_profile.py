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

    if has_suffixed_shares:
        profile_id = "suffixed-investment-columns"
    elif has_unsuffixed_shares:
        profile_id = "unsuffixed-investment-columns"
    else:
        profile_id = "unknown"

    return SchemaProfile(
        profile_id=profile_id,
        number_of_shares_column=(
            "ZNUMBEROFSHARES1"
            if has_suffixed_shares
            else "ZNUMBEROFSHARES"
            if has_unsuffixed_shares
            else None
        ),
        price_per_share_column=(
            "ZPRICEPERSHARE1"
            if has_suffixed_shares and has_suffixed_price
            else "ZPRICEPERSHARE"
            if has_unsuffixed_shares and has_unsuffixed_price
            else None
        ),
    )
