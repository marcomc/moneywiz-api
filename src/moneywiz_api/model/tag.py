from dataclasses import dataclass

from moneywiz_api.model.record import Record
from moneywiz_api.types import ID
from moneywiz_api.validation import require_valid


@dataclass
class Tag(Record):
    """
    ENT: 35
    """

    name: str
    user: ID

    def __init__(self, row):
        super().__init__(row)
        self.name = row["ZNAME6"]
        self.user = row["ZUSER8"]

        # Fixes

        # Validate
        require_valid(self.name is not None, "tag name is required")
        require_valid(self.user is not None, "tag owner is required")
