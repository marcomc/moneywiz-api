from copy import deepcopy
from dataclasses import asdict, dataclass, fields, replace
from datetime import date, datetime
from decimal import Decimal
from enum import Enum
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from moneywiz_api.managers.tag_manager import TagManager
from moneywiz_api.moneywiz_api import MoneywizApi
from moneywiz_api.model.record import Record
from moneywiz_api.read_result import (
    ApiCompleteness,
    LoadErrorKind,
    ManagerLoadReport,
    ReadSnapshot,
    RelationshipLoadReport,
    RelationshipStorage,
    SkippedRecord,
    json_safe,
)
from moneywiz_api.schema_profile import SchemaProfile


def incomplete_relationship() -> RelationshipLoadReport:
    return RelationshipLoadReport(
        storage=RelationshipStorage.UNKNOWN,
        source_ids=["source-1"],
        skipped=[
            SkippedRecord(
                record_id="source-1",
                entity="Tag",
                error=LoadErrorKind.MISSING_FIELD,
                exception_type="KeyError",
            )
        ],
    )


def test_report_constructors_copy_sequences_and_relationship_mapping() -> None:
    source_ids = [1]
    parsed_ids = []
    skipped = [
        SkippedRecord(1, "FutureRecord", LoadErrorKind.UNKNOWN_ENTITY, "ValueError")
    ]
    relationships = {"transaction_tags": incomplete_relationship()}
    report = ManagerLoadReport(
        source_ids=source_ids,
        parsed_ids=parsed_ids,
        skipped=skipped,
        relationships=relationships,
    )

    source_ids.append(2)
    parsed_ids.append(1)
    skipped.clear()
    relationships.clear()

    assert report.source_ids == (1,)
    assert report.parsed_ids == ()
    assert len(report.skipped) == 1
    assert set(report.relationships) == {"transaction_tags"}
    assert not report.complete
    with pytest.raises(TypeError, match="immutable"):
        report.relationships.clear()


def test_aggregate_owns_manager_mapping_and_cannot_become_complete() -> None:
    managers = {
        "incomplete": ManagerLoadReport(
            relationships={"transaction_tags": incomplete_relationship()}
        ),
        "complete": ManagerLoadReport(),
    }
    completeness = ApiCompleteness(managers)

    del managers["incomplete"]

    assert set(completeness.managers) == {"incomplete", "complete"}
    assert not completeness.complete
    with pytest.raises(TypeError, match="immutable"):
        completeness.managers.update({"replacement": ManagerLoadReport()})


def test_snapshot_owns_nested_records_and_schema_profile() -> None:
    nested_values = [1]
    row = {"amount": Decimal("1.20"), "nested": {"values": nested_values}}
    records = {"accounts": [row]}
    schema_profile = {"aliases": {"columns": ["ZVALUE"]}}
    snapshot = ReadSnapshot(
        records=records,
        completeness=ApiCompleteness({"accounts": ManagerLoadReport()}),
        schema_profile=schema_profile,
    )

    nested_values.append(2)
    row["nested"]["extra"] = True
    records.clear()
    schema_profile["aliases"]["columns"].append("ZVALUE1")

    retained_row = snapshot.records["accounts"][0]
    assert retained_row["nested"] == {"values": (1,)}
    assert snapshot.schema_profile == {"aliases": {"columns": ("ZVALUE",)}}
    with pytest.raises(TypeError, match="immutable"):
        retained_row["nested"]["extra"] = True
    with pytest.raises(AttributeError, match="append"):
        retained_row["nested"]["values"].append(3)


def test_result_outputs_are_mutable_detached_json_trees() -> None:
    report = ManagerLoadReport(
        relationships={"transaction_tags": incomplete_relationship()}
    )
    snapshot = ReadSnapshot(
        records={
            "accounts": (
                {
                    "amount": Decimal("1.20"),
                    "date": date(2026, 9, 13),
                    "nested": {"values": [1]},
                },
            )
        },
        completeness=ApiCompleteness({"accounts": report}),
        schema_profile={"aliases": {"columns": ["ZVALUE"]}},
    )

    report_output = report.as_dict()
    snapshot_output = snapshot.as_dict()
    safe_output = json_safe(snapshot)
    report_output["relationships"].clear()
    snapshot_output["records"]["accounts"][0]["nested"]["values"].append(2)
    snapshot_output["schema_profile"]["aliases"]["columns"].append("ZVALUE1")
    safe_output["records"]["accounts"][0]["nested"]["values"].append(3)

    assert not report.complete
    assert snapshot.records["accounts"][0]["nested"]["values"] == (1,)
    assert snapshot.schema_profile["aliases"]["columns"] == ("ZVALUE",)
    assert snapshot_output["records"]["accounts"][0]["amount"] == "1.20"
    assert snapshot_output["records"]["accounts"][0]["date"] == "2026-09-13"


def test_dataclass_asdict_replace_and_constructor_contracts_remain_supported() -> None:
    unloaded = ManagerLoadReport.unloaded()
    default = ManagerLoadReport()
    report = replace(
        default,
        relationships={"transaction_tags": incomplete_relationship()},
    )
    snapshot = ReadSnapshot(
        records={"accounts": ({"nested": [1]},)},
        completeness=ApiCompleteness({"accounts": report}),
        schema_profile={"aliases": ["ZVALUE"]},
    )

    report_dict = asdict(report)
    snapshot_dict = asdict(snapshot)
    copied_records = deepcopy(snapshot.records)
    replaced_snapshot = replace(snapshot, schema_profile={"aliases": ["ZVALUE1"]})

    assert [item.name for item in fields(ManagerLoadReport)] == [
        "source_ids",
        "parsed_ids",
        "skipped",
        "relationships",
        "observed",
    ]
    assert unloaded.status == "unloaded"
    assert default.complete
    assert report_dict["relationships"]["transaction_tags"]["storage"] == (
        RelationshipStorage.UNKNOWN
    )
    assert snapshot_dict["records"]["accounts"][0]["nested"] == (1,)
    assert copied_records == snapshot.records
    assert copied_records is not snapshot.records
    assert replaced_snapshot.schema_profile["aliases"] == ("ZVALUE1",)


def test_relationship_sequences_are_copied_and_as_dict_is_detached() -> None:
    source_ids = ["source-1"]
    parsed_ids = ["source-1"]
    report = RelationshipLoadReport(
        RelationshipStorage.PRESENT,
        source_ids=source_ids,
        parsed_ids=parsed_ids,
    )

    source_ids.append("source-2")
    parsed_ids.clear()
    output = report.as_dict()
    output["source_ids"].append("source-3")
    output["parsed_ids"].clear()

    assert report.source_ids == ("source-1",)
    assert report.parsed_ids == ("source-1",)
    assert report.complete


def test_live_manager_and_model_remain_mutable_after_snapshot_publication() -> None:
    record = Record(
        {
            "Z_PK": 1,
            "Z_ENT": 35,
            "ZOBJECTCREATIONDATE": 0.0,
            "ZGID": "record-1",
        }
    )
    manager = TagManager()
    manager.records()[record.id] = record
    snapshot = ReadSnapshot(
        records={"tags": (record.as_dict(),)},
        completeness=ApiCompleteness({"tags": ManagerLoadReport()}),
        schema_profile={},
    )

    record.gid = "changed-record"
    manager.records()[2] = record

    assert manager.get(2) is record
    assert record.gid == "changed-record"
    assert snapshot.records["tags"][0]["gid"] == "record-1"


class TextEnum(Enum):
    VALUE = "value"


class BinaryEnum(Enum):
    VALUE = b"PRIVATE_PAYLOAD"


@dataclass
class NestedValue:
    value: object


class PrivatePayload:
    def __repr__(self):
        raise RuntimeError("PRIVATE_REPR")

    def __str__(self):
        raise RuntimeError("PRIVATE_STR")


def test_json_safe_preserves_supported_scalar_and_converter_contract() -> None:
    value = {
        "none": None,
        "bool": True,
        "int": 3,
        "float": -0.25,
        "decimal": Decimal("NaN"),
        "datetime": datetime(2026, 9, 13, 10, 30),
        "date": date(2026, 9, 13),
        "path": Path("relative/path"),
        "enum": TextEnum.VALUE,
        "dataclass": NestedValue(["nested"]),
    }

    converted = json_safe(value)

    assert converted == {
        "none": None,
        "bool": True,
        "int": 3,
        "float": -0.25,
        "decimal": "NaN",
        "datetime": "2026-09-13T10:30:00",
        "date": "2026-09-13",
        "path": "relative/path",
        "enum": "value",
        "dataclass": {"value": ["nested"]},
    }
    json.dumps(converted, allow_nan=False)


@pytest.mark.parametrize("value", [float("nan"), float("inf"), float("-inf")])
def test_json_safe_rejects_nonfinite_builtin_floats(value) -> None:
    with pytest.raises(TypeError, match="float values must be finite"):
        json_safe(value)


@pytest.mark.parametrize(
    "value",
    [b"PRIVATE_PAYLOAD", PrivatePayload(), BinaryEnum.VALUE, NestedValue(b"x")],
)
def test_json_safe_rejects_nested_unsupported_values_without_rendering(value) -> None:
    with pytest.raises(TypeError) as error:
        json_safe({"nested": [value]})

    assert "PRIVATE" not in str(error.value)


def test_json_safe_key_domain_and_collision_are_deterministic() -> None:
    assert json_safe({None: 0, True: 1, 2: 2, 1.5: 3, "name": 4}) == {
        "None": 0,
        "True": 1,
        "2": 2,
        "1.5": 3,
        "name": 4,
    }
    with pytest.raises(TypeError, match="keys collide"):
        json_safe({1: "integer", "1": "string"})


@pytest.mark.parametrize(
    "key",
    [
        Decimal("1"),
        TextEnum.VALUE,
        Path("key"),
        b"PRIVATE_PAYLOAD",
        ("tuple",),
        PrivatePayload(),
        float("nan"),
    ],
)
def test_json_safe_rejects_unsupported_keys_without_rendering(key) -> None:
    with pytest.raises(TypeError) as error:
        json_safe({key: "value"})

    assert "PRIVATE" not in str(error.value)


def test_snapshot_refuses_unsupported_nested_values_before_publication() -> None:
    with pytest.raises(TypeError, match="unsupported value"):
        ReadSnapshot(
            records={"accounts": ({"nested": [b"PRIVATE_PAYLOAD"]},)},
            completeness=ApiCompleteness({"accounts": ManagerLoadReport()}),
            schema_profile={},
        )


def test_api_snapshot_refuses_live_model_mutation_but_prior_snapshot_is_detached() -> (
    None
):
    record = Record(
        {
            "Z_PK": 1,
            "Z_ENT": 35,
            "ZOBJECTCREATIONDATE": 0.0,
            "ZGID": "record-1",
        }
    )
    manager = TagManager()
    manager.records()[record.id] = record
    manager._load_report = ManagerLoadReport(source_ids=(1,), parsed_ids=(1,))
    api = MoneywizApi.__new__(MoneywizApi)
    api._managers = {"tags": manager}
    api._loaded_managers = {"tags"}
    api.accessor = SimpleNamespace(
        schema_profile=SchemaProfile(
            "unsuffixed-investment-columns",
            "ZNUMBEROFSHARES",
            "ZNUMBEROFSHARES",
            "ZPRICEPERSHARE",
            "ZPRICEPERSHARE",
        )
    )

    published = api.snapshot(("tags",))
    record.gid = b"PRIVATE_PAYLOAD"

    assert published.records["tags"][0]["gid"] == "record-1"
    json.dumps(published.as_dict(), allow_nan=False)
    assert record.as_dict()["gid"] == b"PRIVATE_PAYLOAD"
    with pytest.raises(TypeError, match="unsupported value"):
        api.snapshot(("tags",))
