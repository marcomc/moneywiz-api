import sqlite3
import re
from collections import defaultdict
from contextlib import contextmanager
from pathlib import Path
from typing import Dict, List, Any, Callable, Tuple
from decimal import Decimal
from enum import Enum

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
from moneywiz_api.validation import require_valid


TRANSACTION_TAG_TABLE_RE = re.compile(r"Z_\d+TAGS")
SUPPORTED_UNRELATED_TAG_TABLES = {
    "Z_23TAGS": frozenset({"Z_23INFOCARDS5", "Z_35TAGS1"}),
    "Z_31TAGS": frozenset({"Z_31SCHEDULEDTRANSACTIONS1", "Z_35TAGS2"}),
}


class _TagTableShape(Enum):
    DIRECT = "direct"
    UNRELATED = "unrelated"
    AMBIGUOUS = "ambiguous"


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
            self._initialize_schema_cache()
        except Exception as exc:
            self._con.close()
            if isinstance(exc, DatabaseSchemaError):
                raise
            raise DatabaseSchemaError("database schema could not be read") from exc

    def _initialize_schema_cache(self) -> None:
        """Bind all schema-dependent caches from one SQLite snapshot."""
        with self._raw_read_transaction():
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
            schema_identity = self._read_schema_identity()
            schema_profile = detect_schema_profile(self._con)
            ent_to_typename, ent_to_super, typename_to_ent = self._entity_maps(
                schema_identity[1]
            )

        self._schema_identity = schema_identity
        self._schema_profile = schema_profile
        self._ent_to_typename = ent_to_typename
        self._ent_to_super = ent_to_super
        self._typename_to_ent = typename_to_ent

    def _read_entity_metadata(self) -> tuple[tuple[int, str, int], ...]:
        cur = self._con.cursor()
        res = cur.execute(
            """
        SELECT Z_ENT, Z_NAME, Z_SUPER
        FROM "Z_PRIMARYKEY"
        ORDER BY Z_ENT
        """
        )
        rows: list[tuple[int, str, int]] = []
        ent_ids: set[int] = set()
        typenames: set[str] = set()
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
            if ent_id in ent_ids:
                raise DatabaseSchemaError("Z_PRIMARYKEY contains duplicate entity IDs")
            if typename in typenames:
                raise DatabaseSchemaError(
                    "Z_PRIMARYKEY contains duplicate entity names"
                )
            ent_ids.add(ent_id)
            typenames.add(typename)
            rows.append((ent_id, typename, super_id))
        return tuple(rows)

    @staticmethod
    def _entity_maps(
        rows: tuple[tuple[int, str, int], ...],
    ) -> tuple[Dict[int, str], Dict[int, int], Dict[str, int]]:
        ent_to_typename = {ent_id: typename for ent_id, typename, _ in rows}
        ent_to_super = {ent_id: super_id for ent_id, _, super_id in rows}
        typename_to_ent = {typename: ent_id for ent_id, typename, _ in rows}
        return ent_to_typename, ent_to_super, typename_to_ent

    def _read_schema_identity(
        self,
    ) -> tuple[
        tuple[tuple[str, str, str, str | None], ...],
        tuple[tuple[int, str, int], ...],
    ]:
        schema_rows = self._con.execute(
            """
            SELECT type, name, tbl_name, sql
            FROM sqlite_schema
            WHERE name NOT LIKE 'sqlite_%'
            ORDER BY type, name, tbl_name
            """
        ).fetchall()
        physical_schema = tuple(
            (row["type"], row["name"], row["tbl_name"], row["sql"])
            for row in schema_rows
        )
        return physical_schema, self._read_entity_metadata()

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
        metadata_present = all(self.ent_for(name) is not None for name in entity_names)
        table_present = self._table_exists(table_name)
        if not metadata_present and not table_present:
            return RelationshipStorage.ABSENT
        if not metadata_present or not table_present:
            return RelationshipStorage.UNKNOWN
        if not set(required_columns).issubset(self._table_columns(table_name)):
            return RelationshipStorage.UNKNOWN
        return RelationshipStorage.PRESENT

    def _classify_tag_table(self, table_name: str) -> _TagTableShape:
        """Classify an exact evidenced shape; a matching name alone is insufficient."""
        columns = frozenset(self._table_columns(table_name))
        if columns == SUPPORTED_UNRELATED_TAG_TABLES.get(table_name):
            return _TagTableShape.UNRELATED
        transaction_ent = self.ent_for("Transaction")
        tag_ent = self.ent_for("Tag")
        if (
            transaction_ent is not None
            and tag_ent is not None
            and table_name == f"Z_{transaction_ent}TAGS"
            and columns == {f"Z_{transaction_ent}TRANSACTIONS", f"Z_{tag_ent}TAGS"}
        ):
            return _TagTableShape.DIRECT
        return _TagTableShape.AMBIGUOUS

    def _transaction_tag_candidates(self) -> tuple[str, ...]:
        """Return bounded tables that could store direct transaction-tag links."""
        rows = self._con.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table' ORDER BY name"
        ).fetchall()
        candidates: list[str] = []
        for row in rows:
            table_name = row["name"]
            if not isinstance(
                table_name, str
            ) or not TRANSACTION_TAG_TABLE_RE.fullmatch(table_name):
                continue
            if self._classify_tag_table(table_name) == _TagTableShape.UNRELATED:
                continue
            candidates.append(table_name)
        return tuple(candidates)

    def _transaction_tag_storage(
        self,
    ) -> tuple[RelationshipStorage, str | None, str | None, str | None]:
        transaction_ent = self.ent_for("Transaction")
        tag_ent = self.ent_for("Tag")
        candidates = self._transaction_tag_candidates()
        metadata_count = sum(ent is not None for ent in (transaction_ent, tag_ent))
        if metadata_count == 0:
            storage = (
                RelationshipStorage.UNKNOWN
                if candidates
                else RelationshipStorage.ABSENT
            )
            return storage, candidates[0] if len(candidates) == 1 else None, None, None
        if metadata_count == 1:
            return RelationshipStorage.UNKNOWN, None, None, None

        table_name = f"Z_{transaction_ent}TAGS"
        transaction_column = f"Z_{transaction_ent}TRANSACTIONS"
        tag_column = f"Z_{tag_ent}TAGS"
        if (
            candidates == (table_name,)
            and self._classify_tag_table(table_name) == _TagTableShape.DIRECT
        ):
            storage = RelationshipStorage.PRESENT
        else:
            storage = RelationshipStorage.UNKNOWN
        return storage, table_name, transaction_column, tag_column

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
        """Query live rows against the verified cached entity mapping."""
        with self.read_transaction():
            return self._query_objects(typenames)

    def _query_objects(self, typenames: List[str]) -> List[Any]:
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
    def _raw_read_transaction(self):
        """Provide snapshot ownership without consulting schema caches."""
        owns_transaction = not self._con.in_transaction
        if owns_transaction:
            self._con.execute("BEGIN")
        try:
            yield
        finally:
            if owns_transaction and self._con.in_transaction:
                self._con.rollback()

    def _verify_schema_identity(self) -> None:
        try:
            baseline = self._schema_identity
        except AttributeError as exc:
            raise DatabaseSchemaError(
                "database schema cache is not initialized; close and reopen the accessor"
            ) from exc
        try:
            current = self._read_schema_identity()
        except DatabaseSchemaError:
            raise
        except sqlite3.Error as exc:
            raise DatabaseSchemaError(
                "database schema could not be verified; close and reopen the accessor"
            ) from exc
        if current != baseline:
            raise DatabaseSchemaError(
                "database schema changed; close and reopen the accessor"
            )

    @contextmanager
    def read_transaction(self):
        """Keep cache-dependent reads on one verified SQLite snapshot."""
        with self._raw_read_transaction():
            self._verify_schema_identity()
            yield

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
        with self.read_transaction():
            return self._get_record(pk_id, constructor)

    def _get_record(self, pk_id: ID, constructor: Callable):
        cur = self._con.cursor()
        res = cur.execute(
            """
        SELECT * FROM ZSYNCOBJECT WHERE Z_PK = ?
        
        """,
            [pk_id],
        )

        return self._construct_record(res.fetchone(), constructor)

    def get_record_by_gid(self, gid: GID, constructor: Callable = Record):
        with self.read_transaction():
            return self._get_record_by_gid(gid, constructor)

    def _get_record_by_gid(self, gid: GID, constructor: Callable):
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
        with self.read_transaction():
            return self._read_category_assignments()

    def _read_category_assignments(
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
                require_valid(
                    source_id is not None, "category assignment ID is required"
                )
                category_id = row["ZCATEGORY"]
                transaction_id = row["ZTRANSACTION"]
                require_valid(
                    category_id is not None,
                    "category assignment category endpoint is required",
                )
                require_valid(
                    transaction_id is not None,
                    "category assignment transaction endpoint is required",
                )
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
        with self.read_transaction():
            assignments, _ = self._read_category_assignments()
            return assignments

    def read_refund_maps(
        self,
    ) -> tuple[Dict[ID, ID], RelationshipLoadReport]:
        with self.read_transaction():
            return self._read_refund_maps()

    def _read_refund_maps(
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
                require_valid(source_id is not None, "refund link ID is required")
                refund_id = row["ZREFUNDTRANSACTION"]
                withdraw_id = row["ZWITHDRAWTRANSACTION"]
                require_valid(
                    refund_id is not None, "refund transaction endpoint is required"
                )
                require_valid(
                    withdraw_id is not None, "withdraw transaction endpoint is required"
                )
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
        with self.read_transaction():
            refund_maps, _ = self._read_refund_maps()
            return refund_maps

    def read_tags_map(
        self,
    ) -> tuple[Dict[ID, List[ID]], RelationshipLoadReport]:
        with self.read_transaction():
            return self._read_tags_map()

    def _read_tags_map(
        self,
    ) -> tuple[Dict[ID, List[ID]], RelationshipLoadReport]:
        transactions_to_tags: Dict[ID, List[ID]] = defaultdict(list)
        storage, table_name, transaction_column, tag_column = (
            self._transaction_tag_storage()
        )
        if storage != RelationshipStorage.PRESENT:
            return transactions_to_tags, RelationshipLoadReport(
                storage=storage,
                storage_name=table_name,
            )
        require_valid(table_name is not None, "transaction-tag table is required")
        require_valid(
            transaction_column is not None,
            "transaction-tag transaction column is required",
        )
        require_valid(tag_column is not None, "transaction-tag tag column is required")
        columns = (transaction_column, tag_column)

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
                require_valid(
                    transaction_id is not None,
                    "transaction-tag transaction endpoint is required",
                )
                require_valid(
                    tag_id is not None, "transaction-tag tag endpoint is required"
                )
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
        with self.read_transaction():
            transactions_to_tags, _ = self._read_tags_map()
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
