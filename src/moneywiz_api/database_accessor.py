import sqlite3
from collections import defaultdict
from contextlib import contextmanager
from pathlib import Path
from typing import Dict, List, Any, Callable, Tuple
from decimal import Decimal

from moneywiz_api.model.record import Record
from moneywiz_api.model.investment_holding import InvestmentHolding
from moneywiz_api.model.transaction import (
    InvestmentBuyTransaction,
    InvestmentSellTransaction,
)
from moneywiz_api.model.raw_data_handler import RawDataHandler as RDH
from moneywiz_api.read_result import (
    LoadErrorKind,
    RelationshipLoadReport,
    RelationshipStorage,
    SkippedRecord,
)
from moneywiz_api.schema_profile import SchemaProfile, detect_schema_profile
from moneywiz_api.types import ENT_ID, ID, GID


class DatabasePathError(ValueError):
    """Raised when an explicit database path cannot be opened safely."""


class DatabaseSchemaError(ValueError):
    """Raised when a readable SQLite file is not a supported MoneyWiz store."""


class DatabaseAccessor:
    def __init__(self, db_path: Path):
        self._db_path = Path(db_path).expanduser().resolve()
        if not self._db_path.is_file():
            raise DatabasePathError("database path must identify an existing file")

        try:
            self._con = sqlite3.connect(
                f"{self._db_path.as_uri()}?mode=ro",
                uri=True,
            )
        except sqlite3.Error as exc:
            raise DatabasePathError(
                "database file could not be opened read-only"
            ) from exc

        def dict_factory(cursor, row):
            record = {}
            for idx, col in enumerate(cursor.description):
                record[col[0]] = row[idx]
            return record

        try:
            self._con.row_factory = dict_factory
            if not self._table_exists("Z_PRIMARYKEY") or not self._table_exists(
                "ZSYNCOBJECT"
            ):
                raise DatabaseSchemaError(
                    "database is missing required MoneyWiz schema tables"
                )
            required_metadata_columns = {"Z_ENT", "Z_NAME", "Z_SUPER"}
            if not required_metadata_columns.issubset(
                self._table_columns("Z_PRIMARYKEY")
            ):
                raise DatabaseSchemaError(
                    "Z_PRIMARYKEY is missing required entity metadata columns"
                )
            self._schema_profile = detect_schema_profile(self._con)
            self._ent_to_typename, self._ent_to_super = self._load_primarykey()
        except Exception as exc:
            self._con.close()
            if isinstance(exc, DatabaseSchemaError):
                raise
            raise DatabaseSchemaError("database schema could not be read") from exc
        self._typename_to_ent: Dict[str, ENT_ID] = {
            v: k for k, v in self._ent_to_typename.items()
        }

    def _load_primarykey(self) -> tuple[Dict[int, str], Dict[int, int]]:
        cur = self._con.cursor()
        res = cur.execute(
            """
        SELECT Z_ENT, Z_NAME, Z_SUPER
        FROM "Z_PRIMARYKEY"
        ORDER BY Z_ENT
        LIMIT 1000 OFFSET 0;
        """
        )
        ent_to_typename: Dict[int, str] = {}
        ent_to_super: Dict[int, int] = {}
        for row in res.fetchall():
            ent_id = row["Z_ENT"]
            typename = row["Z_NAME"]
            super_id = row["Z_SUPER"]
            if (
                not isinstance(ent_id, int)
                or not isinstance(typename, str)
                or not typename
                or not isinstance(super_id, int)
            ):
                raise DatabaseSchemaError("Z_PRIMARYKEY contains invalid metadata")
            ent_to_typename[ent_id] = typename
            ent_to_super[ent_id] = super_id
        return ent_to_typename, ent_to_super

    def __repr__(self):
        return "\n".join(
            f"{key}: {value}" for key, value in self._ent_to_typename.items()
        )

    @property
    def schema_profile(self) -> SchemaProfile:
        """Return the physical-column compatibility profile for this store."""
        return self._schema_profile

    def typename_for(self, ent_id: ENT_ID) -> str:
        return self._ent_to_typename.get(ent_id)

    def ent_for(self, typename: str) -> ENT_ID:
        return self._typename_to_ent.get(typename)

    def descendant_typenames(self, roots: tuple[str, ...]) -> list[str]:
        """Return physical entity names descended from the requested roots."""
        root_ids = {
            ent_id
            for root in roots
            if (ent_id := self._typename_to_ent.get(root)) is not None
        }
        descendants: list[str] = []
        for ent_id, typename in self._ent_to_typename.items():
            current = ent_id
            visited: set[int] = set()
            while current and current not in visited:
                if current in root_ids:
                    descendants.append(typename)
                    break
                visited.add(current)
                current = self._ent_to_super.get(current, 0)
        return descendants

    def _table_exists(self, table_name: str) -> bool:
        row = self._con.execute(
            "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ?",
            (table_name,),
        ).fetchone()
        return row is not None

    def _table_columns(self, table_name: str) -> set[str]:
        return {
            str(row["name"])
            for row in self._con.execute(
                f'PRAGMA table_info("{table_name}")'
            ).fetchall()
        }

    def _relationship_storage(
        self,
        *,
        entity_names: tuple[str, ...],
        table_name: str,
        required_columns: tuple[str, ...],
    ) -> RelationshipStorage:
        if any(self.ent_for(name) is None for name in entity_names):
            return RelationshipStorage.ABSENT
        if not self._table_exists(table_name):
            return RelationshipStorage.UNKNOWN
        if not set(required_columns).issubset(self._table_columns(table_name)):
            return RelationshipStorage.UNKNOWN
        return RelationshipStorage.PRESENT

    @staticmethod
    def _relationship_error(exc: Exception) -> LoadErrorKind:
        if isinstance(exc, KeyError):
            return LoadErrorKind.MISSING_FIELD
        if isinstance(exc, AssertionError):
            return LoadErrorKind.VALIDATION
        if isinstance(exc, ValueError):
            return LoadErrorKind.INVALID_VALUE
        return LoadErrorKind.CONSTRUCTION

    def query_objects(self, typenames: List[str]) -> List[Any]:
        ent_ids = [self.ent_for(name) for name in typenames]
        ent_ids = [ent_id for ent_id in ent_ids if ent_id is not None]
        if not ent_ids:
            return []
        cur = self._con.cursor()
        res = cur.execute(
            """
        SELECT * FROM ZSYNCOBJECT WHERE Z_ENT in (%s)
        """
            % (",".join("?" * len(ent_ids))),
            ent_ids,
        )
        return res.fetchall()

    def close(self) -> None:
        """Close the read-only SQLite connection."""
        self._con.close()

    @contextmanager
    def read_transaction(self):
        """Keep a selected multi-manager load on one SQLite snapshot."""
        owns_transaction = not self._con.in_transaction
        if owns_transaction:
            self._con.execute("BEGIN")
        try:
            yield
        finally:
            if owns_transaction and self._con.in_transaction:
                self._con.rollback()

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_value, traceback) -> None:
        self.close()

    def _construct_record(self, row, constructor: Callable):
        investment_constructors = {
            InvestmentBuyTransaction,
            InvestmentSellTransaction,
            InvestmentHolding,
        }
        if constructor in investment_constructors:
            record = constructor(row, schema_profile=self.schema_profile)
        else:
            record = constructor(row)
        validate = getattr(record, "validate", None)
        if validate is not None:
            validate()
        return record

    def get_record(self, pk_id: ID, constructor: Callable = Record):
        cur = self._con.cursor()
        res = cur.execute(
            """
        SELECT * FROM ZSYNCOBJECT WHERE Z_PK = ?
        
        """,
            [pk_id],
        )

        return self._construct_record(res.fetchone(), constructor)

    def get_record_by_gid(self, gid: GID, constructor: Callable = Record):
        cur = self._con.cursor()
        res = cur.execute(
            """
        SELECT * FROM ZSYNCOBJECT WHERE ZGID = ?
        
        """,
            [gid],
        )

        return self._construct_record(res.fetchone(), constructor)

    def read_category_assignments(
        self,
    ) -> tuple[Dict[ID, List[Tuple[ID, Decimal]]], RelationshipLoadReport]:
        transaction_map: Dict[ID, List[Tuple[ID, Decimal]]] = defaultdict(list)
        table_name = "ZCATEGORYASSIGMENT"
        columns = ("Z_PK", "ZCATEGORY", "ZTRANSACTION", "ZAMOUNT")
        storage = self._relationship_storage(
            entity_names=("CategoryAssigment",),
            table_name=table_name,
            required_columns=columns,
        )
        if storage != RelationshipStorage.PRESENT:
            return transaction_map, RelationshipLoadReport(
                storage=storage,
                storage_name=table_name,
            )

        source_ids: list[ID | None] = []
        parsed_ids: list[ID] = []
        skipped: list[SkippedRecord] = []
        rows = self._con.execute(
            f'SELECT {", ".join(columns)} FROM "{table_name}" '
            "WHERE ZTRANSACTION IS NOT NULL"
        ).fetchall()
        for row in rows:
            source_id = row.get("Z_PK")
            source_ids.append(source_id)
            try:
                assert source_id is not None
                category_id = row["ZCATEGORY"]
                transaction_id = row["ZTRANSACTION"]
                assert category_id is not None
                assert transaction_id is not None
                amount = RDH.get_decimal(row, "ZAMOUNT")
                transaction_map[transaction_id].append((category_id, amount))
            except Exception as exc:
                skipped.append(
                    SkippedRecord(
                        record_id=source_id,
                        entity="CategoryAssigment",
                        error=self._relationship_error(exc),
                        exception_type=type(exc).__name__,
                    )
                )
                continue
            parsed_ids.append(source_id)
        return transaction_map, RelationshipLoadReport(
            storage=storage,
            storage_name=table_name,
            source_ids=tuple(source_ids),
            parsed_ids=tuple(parsed_ids),
            skipped=tuple(skipped),
        )

    def get_category_assignment(self) -> Dict[ID, List[Tuple[ID, Decimal]]]:
        """Return category assignments without completeness metadata."""
        assignments, _ = self.read_category_assignments()
        return assignments

    def read_refund_maps(
        self,
    ) -> tuple[Dict[ID, ID], RelationshipLoadReport]:
        refund_to_withdraw: Dict[ID, ID] = {}
        table_name = "ZWITHDRAWREFUNDTRANSACTIONLINK"
        columns = ("Z_PK", "ZREFUNDTRANSACTION", "ZWITHDRAWTRANSACTION")
        storage = self._relationship_storage(
            entity_names=("WithdrawRefundTransactionLink",),
            table_name=table_name,
            required_columns=columns,
        )
        if storage != RelationshipStorage.PRESENT:
            return refund_to_withdraw, RelationshipLoadReport(
                storage=storage,
                storage_name=table_name,
            )

        source_ids: list[ID | None] = []
        parsed_ids: list[ID] = []
        skipped: list[SkippedRecord] = []
        rows = self._con.execute(
            f'SELECT {", ".join(columns)} FROM "{table_name}"'
        ).fetchall()
        for row in rows:
            source_id = row.get("Z_PK")
            source_ids.append(source_id)
            try:
                assert source_id is not None
                refund_id = row["ZREFUNDTRANSACTION"]
                withdraw_id = row["ZWITHDRAWTRANSACTION"]
                assert refund_id is not None
                assert withdraw_id is not None
                if refund_id in refund_to_withdraw:
                    raise ValueError()
                refund_to_withdraw[refund_id] = withdraw_id
            except Exception as exc:
                skipped.append(
                    SkippedRecord(
                        record_id=source_id,
                        entity="WithdrawRefundTransactionLink",
                        error=self._relationship_error(exc),
                        exception_type=type(exc).__name__,
                    )
                )
                continue
            parsed_ids.append(source_id)
        return refund_to_withdraw, RelationshipLoadReport(
            storage=storage,
            storage_name=table_name,
            source_ids=tuple(source_ids),
            parsed_ids=tuple(parsed_ids),
            skipped=tuple(skipped),
        )

    def get_refund_maps(self) -> Dict[ID, ID]:
        """Return refund links without completeness metadata."""
        refund_maps, _ = self.read_refund_maps()
        return refund_maps

    def read_tags_map(
        self,
    ) -> tuple[Dict[ID, List[ID]], RelationshipLoadReport]:
        transactions_to_tags: Dict[ID, List[ID]] = defaultdict(list)
        transaction_ent = self.ent_for("Transaction")
        tag_ent = self.ent_for("Tag")
        if transaction_ent is None or tag_ent is None:
            return transactions_to_tags, RelationshipLoadReport(
                storage=RelationshipStorage.ABSENT,
            )

        table_name = f"Z_{transaction_ent}TAGS"
        transaction_column = f"Z_{transaction_ent}TRANSACTIONS"
        tag_column = f"Z_{tag_ent}TAGS"
        columns = (transaction_column, tag_column)
        storage = self._relationship_storage(
            entity_names=("Transaction", "Tag"),
            table_name=table_name,
            required_columns=columns,
        )
        if storage != RelationshipStorage.PRESENT:
            return transactions_to_tags, RelationshipLoadReport(
                storage=storage,
                storage_name=table_name,
            )

        source_ids: list[str] = []
        parsed_ids: list[str] = []
        skipped: list[SkippedRecord] = []
        rows = self._con.execute(
            f'SELECT {", ".join(columns)} FROM "{table_name}"'
        ).fetchall()
        for position, row in enumerate(rows):
            transaction_id = row.get(transaction_column)
            tag_id = row.get(tag_column)
            source_id = (
                f"{transaction_id}:{tag_id}"
                if transaction_id is not None and tag_id is not None
                else f"row:{position}"
            )
            source_ids.append(source_id)
            try:
                assert transaction_id is not None
                assert tag_id is not None
                transactions_to_tags[transaction_id].append(tag_id)
            except Exception as exc:
                skipped.append(
                    SkippedRecord(
                        record_id=source_id,
                        entity="TransactionTag",
                        error=self._relationship_error(exc),
                        exception_type=type(exc).__name__,
                    )
                )
                continue
            parsed_ids.append(source_id)
        return transactions_to_tags, RelationshipLoadReport(
            storage=storage,
            storage_name=table_name,
            source_ids=tuple(source_ids),
            parsed_ids=tuple(parsed_ids),
            skipped=tuple(skipped),
        )

    def get_tags_map(self) -> Dict[ID, List[ID]]:
        """Return transaction tags without completeness metadata."""
        transactions_to_tags, _ = self.read_tags_map()
        return transactions_to_tags

    def get_users(self) -> Dict[ID, str]:
        users_map: Dict[ID, str] = {}
        cur = self._con.cursor()
        res = cur.execute(
            """
        SELECT Z_PK, ZSYNCLOGIN FROM  "ZUSER"
        
        """
        )
        for row in res.fetchall():
            users_map[row["Z_PK"]] = row["ZSYNCLOGIN"]
        return users_map
