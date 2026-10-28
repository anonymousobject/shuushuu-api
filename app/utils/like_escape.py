"""Escaping for SQL LIKE pattern metacharacters."""


def escape_like_pattern(value: str) -> str:
    """Escape special LIKE pattern characters (\\, % and _) in a search value.

    Backslash is escaped first so it cannot double-escape the % and _
    escapes added after it, or combine with a wildcard a caller appends to
    the result into an unintended escape sequence.
    """
    return value.replace("\\", "\\\\").replace("%", r"\%").replace("_", r"\_")
