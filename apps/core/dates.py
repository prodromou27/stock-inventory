from datetime import date


def parse_date_param(value):
    """Parses a plain YYYY-MM-DD querystring value, returning None for
    anything blank or unparseable rather than raising — a malformed date
    filter should degrade to "no filter applied" (matching how
    apps.reporting.report_builder already degrades gracefully on bad
    filter input), not a 500 on an otherwise-working list page.
    """
    if not value:
        return None
    try:
        return date.fromisoformat(value)
    except ValueError:
        return None
