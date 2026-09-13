"""Structured results for bounded MoneyWiz reads."""

from __future__ import annotations

from copy import deepcopy
from dataclasses import asdict, dataclass, field, is_dataclass
from datetime import date, datetime
from decimal import Decimal
from enum import Enum
import math
from pathlib import Path
from typing import Any, Mapping

from moneywiz_api.types import ID


class _FrozenMapping(dict):
    """Recursively own published mapping values and refuse ordinary mutation."""

    def __init__(self, values=()):
        if getattr(self, "_initialized", False):
            raise TypeError("published result mappings are immutable")
        dict.__init__(
            self,
            ((key, _freeze_value(value)) for key, value in dict(values).items()),
        )
        self._initialized = True

    @staticmethod
    def _immutable(*_args, **_kwargs):
        raise TypeError("published result mappings are immutable")

    __setitem__ = _immutable
    __delitem__ = _immutable
    __ior__ = _immutable
    clear = _immutable
    pop = _immutable
    popitem = _immutable
    setdefault = _immutable
    update = _immutable

    def __deepcopy__(self, memo):
        return type(self)(
            (deepcopy(key, memo), deepcopy(value, memo)) for key, value in self.items()
        )


def _freeze_value(value: Any) -> Any:
    """Recursively freeze mappings and sequences at a result boundary."""
    if isinstance(value, _FrozenMapping):
        return value
    if isinstance(value, Mapping):
        return _FrozenMapping(value)
    if isinstance(value, (tuple, list)):
        return tuple(_freeze_value(item) for item in value)
    return value


class LoadErrorKind(str, Enum):
    """Bounded classifications for records omitted from a read."""

    MISSING_FIELD = "missing_field"
    INVALID_VALUE = "invalid_value"
    VALIDATION = "validation"
    DUPLICATE_ID = "duplicate_id"
    DUPLICATE_GID = "duplicate_gid"
    UNKNOWN_ENTITY = "unknown_entity"
    CONSTRUCTION = "construction"


class RelationshipStorage(str, Enum):
    """Whether relationship storage is available and understood."""

    PRESENT = "present"
    ABSENT = "absent"
    UNKNOWN = "unknown"


@dataclass(frozen=True)
class SkippedRecord:
    """Identity-only diagnostic for one source row that was not parsed."""

    record_id: ID | str | None
    entity: str | None
    error: LoadErrorKind
    exception_type: str

    def as_dict(self) -> dict[str, Any]:
        return {
            "record_id": self.record_id,
            "entity": self.entity,
            "error": self.error.value,
            "exception_type": self.exception_type,
        }


@dataclass(frozen=True)
class RelationshipLoadReport:
    """Completeness evidence for one relationship storage layout."""

    storage: RelationshipStorage
    storage_name: str | None = None
    source_ids: tuple[ID | str | None, ...] = ()
    parsed_ids: tuple[ID | str, ...] = ()
    skipped: tuple[SkippedRecord, ...] = ()

    def __post_init__(self) -> None:
        object.__setattr__(self, "source_ids", tuple(self.source_ids))
        object.__setattr__(self, "parsed_ids", tuple(self.parsed_ids))
        object.__setattr__(self, "skipped", tuple(self.skipped))

    @property
    def source_count(self) -> int:
        return len(self.source_ids)

    @property
    def parsed_count(self) -> int:
        return len(self.parsed_ids)

    @property
    def complete(self) -> bool:
        return (
            self.storage != RelationshipStorage.UNKNOWN
            and self.source_count == self.parsed_count
            and not self.skipped
        )

    @property
    def status(self) -> str:
        if self.storage == RelationshipStorage.ABSENT:
            return "absent"
        if self.complete:
            return "complete"
        if self.parsed_count:
            return "partial"
        return "error"

    def as_dict(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "complete": self.complete,
            "storage": self.storage.value,
            "storage_name": self.storage_name,
            "source_count": self.source_count,
            "parsed_count": self.parsed_count,
            "source_ids": list(self.source_ids),
            "parsed_ids": list(self.parsed_ids),
            "skipped": [item.as_dict() for item in self.skipped],
        }


@dataclass(frozen=True)
class ManagerLoadReport:
    """Completeness evidence for one manager load."""

    source_ids: tuple[ID | None, ...] = ()
    parsed_ids: tuple[ID, ...] = ()
    skipped: tuple[SkippedRecord, ...] = ()
    relationships: Mapping[str, RelationshipLoadReport] = field(default_factory=dict)
    observed: bool = True

    def __post_init__(self) -> None:
        object.__setattr__(self, "source_ids", tuple(self.source_ids))
        object.__setattr__(self, "parsed_ids", tuple(self.parsed_ids))
        object.__setattr__(self, "skipped", tuple(self.skipped))
        object.__setattr__(self, "relationships", _FrozenMapping(self.relationships))

    @classmethod
    def unloaded(cls) -> "ManagerLoadReport":
        """Return evidence that no complete manager observation was published."""
        return cls(observed=False)

    @property
    def source_count(self) -> int:
        return len(self.source_ids)

    @property
    def parsed_count(self) -> int:
        return len(self.parsed_ids)

    @property
    def complete(self) -> bool:
        return (
            self.observed
            and self.source_count == self.parsed_count
            and not self.skipped
            and all(report.complete for report in self.relationships.values())
        )

    @property
    def status(self) -> str:
        if not self.observed:
            return "unloaded"
        if self.complete:
            return "complete"
        if self.parsed_count or any(
            report.parsed_count for report in self.relationships.values()
        ):
            return "partial"
        return "error"

    def as_dict(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "complete": self.complete,
            "source_count": self.source_count,
            "parsed_count": self.parsed_count,
            "source_ids": list(self.source_ids),
            "parsed_ids": list(self.parsed_ids),
            "skipped": [item.as_dict() for item in self.skipped],
            "relationships": {
                name: report.as_dict() for name, report in self.relationships.items()
            },
        }


@dataclass(frozen=True)
class ApiCompleteness:
    """Aggregate completeness for the managers included in a read."""

    managers: Mapping[str, ManagerLoadReport]

    def __post_init__(self) -> None:
        object.__setattr__(self, "managers", _FrozenMapping(self.managers))

    @property
    def complete(self) -> bool:
        return bool(self.managers) and all(
            report.complete for report in self.managers.values()
        )

    @property
    def status(self) -> str:
        if self.complete:
            return "complete"
        if self.managers and all(
            report.status == "error" for report in self.managers.values()
        ):
            return "error"
        return "partial"

    def as_dict(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "complete": self.complete,
            "managers": {
                name: report.as_dict() for name, report in self.managers.items()
            },
        }


@dataclass(frozen=True)
class ReadSnapshot:
    """JSON-safe records and completeness metadata from one API instance."""

    records: Mapping[str, tuple[Mapping[str, Any], ...]]
    completeness: ApiCompleteness
    schema_profile: Mapping[str, Any]

    def __post_init__(self) -> None:
        records = json_safe(self.records)
        schema_profile = json_safe(self.schema_profile)
        json_safe(self.completeness)
        object.__setattr__(self, "records", _FrozenMapping(records))
        object.__setattr__(self, "schema_profile", _FrozenMapping(schema_profile))

    def as_dict(self) -> dict[str, Any]:
        return {
            "schema_profile": json_safe(self.schema_profile),
            "completeness": self.completeness.as_dict(),
            "records": json_safe(self.records),
        }


def json_safe(value: Any) -> Any:
    """Convert public model values into deterministic JSON-compatible values."""
    if value is None or type(value) in (bool, int, str):
        return value
    if type(value) is float:
        if not math.isfinite(value):
            raise TypeError("snapshot float values must be finite")
        return value
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, Enum):
        return json_safe(value.value)
    if is_dataclass(value) and not isinstance(value, type):
        return json_safe(asdict(value))
    if isinstance(value, Mapping):
        converted = {}
        for key, item in value.items():
            normalized_key = _json_safe_key(key)
            if normalized_key in converted:
                raise TypeError("snapshot mapping keys collide after normalization")
            converted[normalized_key] = json_safe(item)
        return converted
    if isinstance(value, (tuple, list)):
        return [json_safe(item) for item in value]
    raise TypeError("snapshot contains an unsupported value")


def _json_safe_key(value: Any) -> str:
    if value is None or type(value) in (bool, int, str):
        return str(value)
    if type(value) is float:
        if not math.isfinite(value):
            raise TypeError("snapshot mapping keys must be finite")
        return str(value)
    raise TypeError("snapshot contains an unsupported mapping key")
