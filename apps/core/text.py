def normalize_whitespace(value):
    """Trim and collapse internal whitespace. Shared by every app that
    normalizes a user-entered name/label for duplicate detection or display
    consistency (docs/architecture/05-tracking-and-duplicates.md).
    """
    return " ".join((value or "").split())
