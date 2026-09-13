from dataclasses import dataclass

from moneywiz_api.model.record import Record
from moneywiz_api.types import ID
from moneywiz_api.validation import (
    require_integer_identity,
    require_text,
    require_valid,
)


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
        require_text(self.name, "tag name must be an uncoerced string")
        require_valid(self.user is not None, "tag owner is required")
        require_integer_identity(self.user, "tag owner must be an uncoerced integer")
