from abc import ABC, abstractmethod
import logging
from typing import Callable, Dict, Generic, TypeVar

from moneywiz_api.database_accessor import DatabaseAccessor
from moneywiz_api.model.record import Record
from moneywiz_api.model.schema_mapped_row import mapped_row
from moneywiz_api.types import GID, ID

T = TypeVar("T", bound=Record)
logger = logging.getLogger(__name__)


class RecordManager(ABC, Generic[T]):
    def __init__(self) -> None:
        self._records: Dict[ID, T] = {}
        self._gid_to_id: Dict[GID, ID] = {}
        self._load_errors: list[tuple[ID, str, str]] = []

    @property
    @abstractmethod
    def ents(self) -> Dict[str, type[T]]:
        raise NotImplementedError()

    def load(self, db_accessor: DatabaseAccessor) -> None:
        records = db_accessor.query_objects(list(self.ents.keys()))

        for record in records:
            typename = db_accessor.typename_for(record["Z_ENT"])
            if typename in self.ents:
                try:
                    obj = self.construct_record(self.ents[typename], record, db_accessor)
                    obj.validate()
                except (AssertionError, KeyError, ValueError) as exc:
                    record_id = record.get("Z_PK")
                    detail = type(exc).__name__
                    self._load_errors.append((record_id, typename, detail))
                    logger.debug(
                        "Skipping unreadable %s record %s: %s",
                        typename,
                        record_id,
                        detail,
                    )
                    continue
                self.add(obj)

    def construct_record(
        self, constructor: Callable, record, db_accessor: DatabaseAccessor
    ):
        """Construct a record; subclasses can supply schema-specific context."""
        return constructor(mapped_row(record, constructor))

    def add(self, record: T) -> None:
        self._records[record.id] = record
        if record.gid in self._gid_to_id:
            raise RuntimeError(
                f"Duplicate gid for {record}, existing record Id {self._gid_to_id[record.gid]}"
            )

        self._gid_to_id[record.gid] = record.id

    def get(self, record_id: ID) -> T | None:
        return self._records.get(record_id)

    def get_by_gid(self, gid: GID) -> T | None:
        record_id = self._gid_to_id.get(gid)
        if record_id is None:
            return None
        return self._records.get(record_id)

    def records(self) -> Dict[ID, T]:
        return self._records

    @property
    def load_errors(self) -> list[tuple[ID, str, str]]:
        """Return records skipped during best-effort read parsing."""
        return list(self._load_errors)

    def __repr__(self):
        return "\n".join(f"{key}: {value}" for key, value in self.records().items())
