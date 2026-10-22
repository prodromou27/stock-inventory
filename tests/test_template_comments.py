"""Django's {# ... #} comment syntax is single-line only — a {# that isn't
closed on the same line is rendered as literal text, which previously leaked
developer notes onto the asset list, the operations hub, the balance picker,
and into generated PDF documents. Multi-line notes must use
{% comment %}...{% endcomment %} instead.
"""

from pathlib import Path

from django.conf import settings

TEMPLATE_ROOTS = [Path(settings.BASE_DIR) / "templates", Path(settings.BASE_DIR) / "apps"]


def test_no_template_has_an_unclosed_single_line_comment():
    offenders = []
    for root in TEMPLATE_ROOTS:
        for path in root.rglob("*.html"):
            for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
                # Everything after the last "#}" on the line must not open a new comment.
                if "{#" in line.rsplit("#}", 1)[-1]:
                    offenders.append(f"{path.relative_to(settings.BASE_DIR)}:{number}")
    message = "Multi-line {# #} comments render as visible text: "
    assert not offenders, message + ", ".join(offenders)
