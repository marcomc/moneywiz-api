from __future__ import annotations

import sqlite3
import uuid
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable, Sequence
from datetime import datetime

from moneywiz_api.utils import get_date


@dataclass
class PlannedSQL:
    sql: str
    params: Sequence[Any] | None


class WriteSession:
    def __init__(self, db_path: Path, dry_run: bool = True):
        self.db_path = Path(db_path)
        self.dry_run = dry_run
        self._con = sqlite3.connect(str(self.db_path))
        self._planned: list[PlannedSQL] = []

    def close(self):
        self._con.close()

    def begin(self):
        self._exec("BEGIN")

    def commit(self):
        self._exec("COMMIT")

    def rollback(self):
        self._exec("ROLLBACK")

    @contextmanager
    def transaction(self):
        try:
            self.begin()
            yield
            self.commit()
        except Exception:
            self.rollback()
            raise

    @property
    def planned(self) -> list[PlannedSQL]:
        return self._planned

    def _exec(self, sql: str, params: Sequence[Any] | None = None):
        self._planned.append(PlannedSQL(sql, params))
        if self.dry_run:
            return
        cur = self._con.cursor()
        if params is None:
            cur.execute(sql)
        else:
            cur.execute(sql, params)
        self._con.commit()

    # ---------------------------
    # Schema / reference helpers
    # ---------------------------

    def _tables(self) -> list[str]:
        cur = self._con.cursor()
        rows = cur.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name LIKE 'Z%'"
        ).fetchall()
        return [r[0] for r in rows if r[0] not in {"Z_PRIMARYKEY"}]

    def _table_integer_columns(self, table: str) -> list[str]:
        cur = self._con.cursor()
        cols = []
        for cid, name, ctype, notnull, dflt, pk in cur.execute(
            f"PRAGMA table_info({table})"
        ).fetchall():
            ctype_u = (ctype or "").upper()
            if "INT" in ctype_u:
                if name.upper() in {"Z_ENT", "Z_OPT"}:
                    continue
                # Avoid primary key columns when searching references
                if pk == 1:
                    continue
                cols.append(name)
        return cols

    @dataclass
    class ReferenceHit:
        table: str
        column: str
        count: int
        sample_ids: list[int]

    def find_references(self, pk: int) -> list["WriteSession.ReferenceHit"]:
        hits: list[WriteSession.ReferenceHit] = []
        cur = self._con.cursor()
        for table in self._tables():
            int_cols = self._table_integer_columns(table)
            if not int_cols:
                continue
            # Prefer a meaningful id column to show in samples
            id_col = None
            for candidate in ("Z_PK", "ZID", "Z_ID"):
                try:
                    cur.execute(f"SELECT {candidate} FROM {table} LIMIT 0")
                    id_col = candidate
                    break
                except Exception:
                    continue
            for col in int_cols:
                try:
                    cnt_row = cur.execute(
                        f"SELECT COUNT(*) FROM {table} WHERE {col} = ?",
                        (pk,),
                    ).fetchone()
                    count = int(cnt_row[0]) if cnt_row else 0
                    if count > 0:
                        samples: list[int] = []
                        if id_col is not None:
                            samples = [
                                int(r[0])
                                for r in cur.execute(
                                    f"SELECT {id_col} FROM {table} WHERE {col} = ? LIMIT 5",
                                    (pk,),
                                ).fetchall()
                            ]
                        hits.append(
                            WriteSession.ReferenceHit(
                                table=table, column=col, count=count, sample_ids=samples
                            )
                        )
                except Exception:
                    # Skip columns we can't query cleanly
                    continue
        return hits

    def _ent_for(self, typename: str) -> int:
        cur = self._con.cursor()
        res = cur.execute(
            'SELECT Z_ENT FROM Z_PRIMARYKEY WHERE Z_NAME = ? LIMIT 1', (typename,)
        ).fetchone()
        if not res:
            raise ValueError(f"Unknown typename '{typename}' in Z_PRIMARYKEY")
        return int(res[0])

    def insert_syncobject(self, typename: str, fields: dict[str, Any]) -> int | None:
        ent = self._ent_for(typename)
        data = dict(fields)
        data.setdefault("Z_ENT", ent)
        data.setdefault("Z_OPT", 1)
        data.setdefault("ZGID", str(uuid.uuid4()).upper())
        # Provide a creation timestamp if missing (Apple epoch float)
        data.setdefault("ZOBJECTCREATIONDATE", get_date(datetime.now()))

        cols = ", ".join(data.keys())
        placeholders = ", ".join(["?"] * len(data))
        sql = f"INSERT INTO ZSYNCOBJECT ({cols}) VALUES ({placeholders})"
        if self.dry_run:
            self._exec(sql, list(data.values()))
            return None
        cur = self._con.cursor()
        cur.execute(sql, list(data.values()))
        self._con.commit()
        return int(cur.lastrowid)

    def update_syncobject(self, pk: int, fields: dict[str, Any]) -> None:
        if not fields:
            return
        sets = ", ".join([f"{k} = ?" for k in fields.keys()])
        params = list(fields.values()) + [pk]
        sql = f"UPDATE ZSYNCOBJECT SET {sets} WHERE Z_PK = ?"
        self._exec(sql, params)

    def delete_syncobject(self, pk: int) -> None:
        self._exec("DELETE FROM ZSYNCOBJECT WHERE Z_PK = ?", (pk,))

    def safe_delete(self, pk: int) -> list["WriteSession.ReferenceHit"]:
        """Return references if any; if none, plan a delete of the row.

        The caller may decide whether to apply or show the plan.
        """
        refs = self.find_references(pk)
        if not refs:
            self.delete_syncobject(pk)
        return refs

    def rename_entity(self, pk: int, new_name: str, name_field: str | None = None) -> None:
        """Rename an entity row in ZSYNCOBJECT.

        If name_field is not provided, try common name-bearing columns in order.
        """
        field = name_field
        if field is None:
            # Heuristics: try ZNAME, then ZDESC2 (description), then ZTITLE2
            for candidate in ("ZNAME", "ZDESC2", "ZTITLE2"):
                try:
                    cur = self._con.cursor()
                    cur.execute(f"SELECT {candidate} FROM ZSYNCOBJECT LIMIT 0")
                    field = candidate
                    break
                except Exception:
                    continue
        if not field:
            raise ValueError(
                "Could not determine a name field. Provide --name-field explicitly."
            )
        self.update_syncobject(pk, {field: new_name})

    def assign_categories(self, tx_id: int, splits: Iterable[tuple[int, Any]]):
        for cat_id, amount in splits:
            sql = (
                "INSERT INTO ZCATEGORYASSIGMENT (ZTRANSACTION, ZCATEGORY, ZAMOUNT) "
                "VALUES (?, ?, ?)"
            )
            self._exec(sql, (tx_id, cat_id, amount))

    def assign_tags(self, tx_id: int, tag_ids: Iterable[int]):
        for tag_id in tag_ids:
            sql = "INSERT INTO Z_36TAGS (Z_36TRANSACTIONS, Z_35TAGS) VALUES (?, ?)"
            self._exec(sql, (tx_id, tag_id))

    def link_refund(self, refund_tx_id: int, original_withdraw_id: int):
        sql = (
            "INSERT INTO ZWITHDRAWREFUNDTRANSACTIONLINK (ZREFUNDTRANSACTION, ZWITHDRAWTRANSACTION) "
            "VALUES (?, ?)"
        )
        self._exec(sql, (refund_tx_id, original_withdraw_id))
