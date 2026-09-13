from copy import deepcopy
from dataclasses import asdict, fields, replace
from datetime import date
from decimal import Decimal

import pytest

from moneywiz_api.managers.tag_manager import TagManager
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
