import json

import pytest

from moneywiz_api.managers.category_manager import CategoryManager
from moneywiz_api.model.category import Category
from moneywiz_api.moneywiz_api import MoneywizApi


def category_row(**overrides):
    row = {
        "Z_ENT": 19,
        "ZOBJECTCREATIONDATE": 0.0,
        "ZGID": "category-1",
        "Z_PK": 1,
        "ZNAME2": "Category",
        "ZPARENTCATEGORY": None,
        "ZTYPE2": 1,
        "ZUSER3": 1,
    }
    row.update(overrides)
    return row


class CategoryAccessor:
    def __init__(self, rows):
        self.rows = rows

    def query_objects(self, _typenames):
        return self.rows

    def typename_for(self, _ent_id):
        return "Category"


@pytest.mark.parametrize(
    ("raw_type", "expected"),
    [(1, "Expenses"), (2, "Income")],
)
def test_supported_category_types_are_unchanged(raw_type, expected) -> None:
    assert Category(category_row(ZTYPE2=raw_type)).type == expected


@pytest.mark.parametrize("raw_type", [None, 0, 3, "PRIVATE_PAYLOAD"])
def test_unsupported_category_type_is_invalid_value(raw_type, caplog) -> None:
    manager = CategoryManager()
    caplog.set_level("DEBUG")

    report = manager.load(CategoryAccessor([category_row(ZTYPE2=raw_type)]))

    assert report.source_ids == (1,)
    assert report.parsed_ids == ()
    assert len(report.skipped) == 1
    assert report.skipped[0].record_id == 1
    assert report.skipped[0].error.value == "invalid_value"
    assert report.skipped[0].exception_type == "ValueError"
    serialized = json.dumps(report.as_dict(), sort_keys=True)
    assert "PRIVATE_PAYLOAD" not in serialized
    assert "PRIVATE_PAYLOAD" not in caplog.text


def test_unsupported_category_direct_error_has_static_message() -> None:
    with pytest.raises(ValueError, match="^unsupported category type$") as error:
        Category(category_row(ZTYPE2="PRIVATE_PAYLOAD"))

    assert "PRIVATE_PAYLOAD" not in str(error.value)


def test_missing_category_type_remains_missing_field() -> None:
    row = category_row()
    del row["ZTYPE2"]

    report = CategoryManager().load(CategoryAccessor([row]))

    assert report.skipped[0].error.value == "missing_field"
    assert report.skipped[0].exception_type == "KeyError"


def test_invalid_category_owner_remains_validation() -> None:
    report = CategoryManager().load(CategoryAccessor([category_row(ZUSER3=None)]))

    assert report.skipped[0].error.value == "validation"
    assert report.skipped[0].exception_type == "AssertionError"


def test_unexpected_runtime_error_remains_construction() -> None:
    def unexpected_constructor(_row):
        raise RuntimeError("synthetic unexpected constructor failure")

    class UnexpectedCategoryManager(CategoryManager):
        @property
        def ents(self):
            return {"Category": unexpected_constructor}

    report = UnexpectedCategoryManager().load(CategoryAccessor([category_row()]))

    assert report.skipped[0].error.value == "construction"
    assert report.skipped[0].exception_type == "RuntimeError"


def test_category_diagnostic_is_exposed_by_api_completeness() -> None:
    manager = CategoryManager()
    manager.load(CategoryAccessor([category_row(ZTYPE2=3)]))
    api = MoneywizApi.__new__(MoneywizApi)
    api.category_manager = manager
    api._managers = {"categories": manager}
    api._loaded_managers = {"categories"}

    report = api.completeness(("categories",)).as_dict()

    category_report = report["managers"]["categories"]
    assert report["status"] == "error"
    assert not report["complete"]
    assert category_report["source_count"] == 1
    assert category_report["parsed_count"] == 0
    assert category_report["skipped"][0]["error"] == "invalid_value"
