"""Escaping for SQL LIKE pattern metacharacters."""


def escape_like_pattern(value: str) -> str:
    """Escape special LIKE pattern characters (% and _) in a search value."""
    return value.replace("%", r"\%").replace("_", r"\_")
