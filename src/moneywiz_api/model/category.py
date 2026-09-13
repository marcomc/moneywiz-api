from dataclasses import dataclass
from typing import Optional

from moneywiz_api.model.record import Record
from moneywiz_api.types import CategoryType, ID
from moneywiz_api.validation import require_valid


@dataclass
class Category(Record):
    """
    ENT: 19
    """

    name: str
    parent_id: Optional[int]
    type: CategoryType
    user: ID

    def __init__(self, row):
        super().__init__(row)
        self.name = row["ZNAME2"]
        self.parent_id = row["ZPARENTCATEGORY"]
        self.type = self._convert_type(row["ZTYPE2"])
        self.user = row["ZUSER3"]

        # Fixes

        # Validate
        require_valid(self.name is not None, "category name is required")
        require_valid(self.type is not None, "category type is required")
        require_valid(self.user is not None, "category owner is required")

    @staticmethod
    def _convert_type(type_: Optional[int]) -> CategoryType:
        if type_ and type_ in [1, 2]:
            return "Expenses" if type_ == 1 else "Income"
        raise ValueError("unsupported category type")
