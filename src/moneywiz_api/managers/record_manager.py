from abc import ABC, abstractmethod
import logging
from typing import Callable, Dict, Generic, TypeVar

from moneywiz_api.database_accessor import DatabaseAccessor
from moneywiz_api.model.record import Record
from moneywiz_api.read_result import (
    LoadErrorKind,
    ManagerLoadReport,
    SkippedRecord,
)
from moneywiz_api.types import ID, GID


T = TypeVar("T", bound=Record)
logger = logging.getLogger(__name__)


class DuplicateRecordIdError(ValueError):
    """Raised when two source rows use the same primary key."""


class DuplicateRecordGidError(ValueError):
    """Raised when two source rows use the same global identifier."""


class RecordManager(ABC, Generic[T]):
    def __init__(self):
        self._records: Dict[ID, T] = {}
        self._gid_to_id: Dict[GID, ID] = {}
        self._load_report = ManagerLoadReport.unloaded()

    @property
    @abstractmethod
    def ents(self) -> Dict[str, Callable]:
        raise NotImplementedError()

    @property
    def entity_roots(self) -> tuple[str, ...]:
        """Return schema roots whose unknown descendants must be reported."""
        return ()

    def load(self, db_accessor: DatabaseAccessor) -> ManagerLoadReport:
        self._discard_incomplete_load()
        try:
            with db_accessor.read_transaction():
                report = self._load_in_transaction(db_accessor)
        except BaseException:
            self._discard_incomplete_load()
            raise
        self._load_report = report
        return report

    def _load_in_transaction(self, db_accessor: DatabaseAccessor) -> ManagerLoadReport:
        """Load all manager state inside the caller-owned read snapshot."""
        return self._load_records(db_accessor)

    def _load_records(self, db_accessor: DatabaseAccessor) -> ManagerLoadReport:
        """Read rows while keeping the public report unloaded until publication."""
        typenames = list(self.ents)
        if self.entity_roots:
            discovered = db_accessor.descendant_typenames(self.entity_roots)
            typenames = list(dict.fromkeys([*typenames, *discovered]))
        records = db_accessor.query_objects(typenames)

        source_ids: list[ID | None] = []
        parsed_ids: list[ID] = []
        skipped: list[SkippedRecord] = []

        for record in records:
            raw_record_id = record.get("Z_PK")
            record_id = raw_record_id if type(raw_record_id) is int else None
            source_ids.append(record_id)
            typename = None
            try:
                typename = db_accessor.typename_for(record["Z_ENT"])
                if typename not in self.ents:
                    raise UnknownEntityError()
                obj = self.construct_record(self.ents[typename], record, db_accessor)
                validate = getattr(obj, "validate", None)
                if validate is not None:
                    validate()
                self.add(obj)
            except Exception as exc:
                error = self._error_kind(exc)
                skipped.append(
                    SkippedRecord(
                        record_id=record_id,
                        entity=typename,
                        error=error,
                        exception_type=type(exc).__name__,
                    )
                )
                logger.debug(
                    "Skipping unreadable %s record %s: %s",
                    typename,
                    record_id,
                    error.value,
                )
                continue
            parsed_ids.append(obj.id)

        return ManagerLoadReport(
            source_ids=tuple(source_ids),
            parsed_ids=tuple(parsed_ids),
            skipped=tuple(skipped),
        )

    def _discard_incomplete_load(self) -> None:
        """Clear state when a direct manager load did not finish."""
        self._records = {}
        self._gid_to_id = {}
        self._load_report = ManagerLoadReport.unloaded()

    def _adopt_loaded_state(self, staged: "RecordManager[T]") -> None:
        """Publish a successfully staged load without replacing this manager."""
        self._records = staged._records
        self._gid_to_id = staged._gid_to_id
        self._load_report = staged._load_report

    @staticmethod
    def _error_kind(exc: Exception) -> LoadErrorKind:
        if isinstance(exc, UnknownEntityError):
            return LoadErrorKind.UNKNOWN_ENTITY
        if isinstance(exc, DuplicateRecordIdError):
            return LoadErrorKind.DUPLICATE_ID
        if isinstance(exc, DuplicateRecordGidError):
            return LoadErrorKind.DUPLICATE_GID
        if isinstance(exc, KeyError):
            return LoadErrorKind.MISSING_FIELD
        if isinstance(exc, AssertionError):
            return LoadErrorKind.VALIDATION
        if isinstance(exc, ValueError):
            return LoadErrorKind.INVALID_VALUE
        return LoadErrorKind.CONSTRUCTION

    def construct_record(
        self, constructor: Callable, record, db_accessor: DatabaseAccessor
    ):
        """Construct a record; subclasses can supply schema-specific context."""
        return constructor(record)

    def add(self, record: T) -> None:
        if record.id in self._records:
            raise DuplicateRecordIdError()
        if record.gid in self._gid_to_id:
            raise DuplicateRecordGidError()

        self._records[record.id] = record
        self._gid_to_id[record.gid] = record.id

    def get(self, record_id: ID) -> T | None:
        return self._records.get(record_id)

    def get_by_gid(self, gid: GID) -> T | None:
        return self._records.get(self._gid_to_id.get(gid))

    def records(self) -> Dict[ID, T]:
        return self._records

    @property
    def load_errors(self) -> list[tuple[ID | None, str | None, str]]:
        """Return records skipped during best-effort read parsing."""
        return [
            (item.record_id, item.entity, item.exception_type)
            for item in self._load_report.skipped
        ]

    @property
    def load_report(self) -> ManagerLoadReport:
        """Return structured completeness evidence from the latest load."""
        return self._load_report

    def __repr__(self):
        return "\n".join(f"{key}: {value}" for key, value in self.records().items())


class UnknownEntityError(ValueError):
    """Raised when a source row belongs to an unsupported subtype."""
