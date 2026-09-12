def require_valid(condition: object, message: str) -> None:
    """Raise the historical validation exception even under optimized Python."""
    if not condition:
        raise AssertionError(message)
