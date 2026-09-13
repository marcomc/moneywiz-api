def require_valid(condition: object, message: str) -> None:
    """Raise the historical validation exception even under optimized Python."""
    if not condition:
        raise AssertionError(message)


def require_integer_identity(
    value: object, message: str, *, optional: bool = False
) -> None:
    """Require an uncoerced integer identity, optionally allowing ``None``."""
    if optional and value is None:
        return
    if type(value) is not int:
        raise AssertionError(message)
