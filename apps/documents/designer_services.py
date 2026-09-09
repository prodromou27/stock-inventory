"""Visual document design validation, rendering and persistence services.

Only allow-listed fields become template expressions. User text is never Django
source; scripts, remote resources and CSS functions are deliberately rejected.
"""

import re
from html import escape
from html.parser import HTMLParser

from django.core.exceptions import ValidationError

from apps.core.authorization import ADMINISTRATOR, require_role

FIELDS = {
    "document_number": "Document number",
    "transaction_number": "Transaction number",
    "document_date_display": "Date",
    "final_customer": "Customer",
    "employee_name": "Employee",
    "project_reference": "Project reference",
    "prepared_by": "Prepared by",
    "notes": "Transaction notes",
    "witness_name": "Witness",
    "wipe_method_display": "Disposal method",
    "company_name": "Company name",
    "company_address": "Company address",
}
LINE_FIELDS = {
    key: key.replace("_", " ").title()
    for key in (
        "brand",
        "model",
        "sku",
        "type",
        "description",
        "serial",
        "quantity",
        "condition",
        "accessories",
    )
}
TAGS = set(
    (
        "body div span p h1 h2 h3 h4 strong b em i u s br hr table thead tbody "
        "tfoot tr td th ul ol li img section"
    ).split()
)
VOID = {"br", "hr", "img"}
PROPERTIES = set(
    (
        "color background-color font-family font-size font-weight font-style text-decoration "
        "text-align line-height letter-spacing width height min-height max-width min-width "
        "max-height margin margin-top margin-right margin-bottom margin-left "
        "padding padding-top padding-right "
        "padding-bottom padding-left border border-width border-style border-color border-top "
        "border-bottom border-left border-right border-collapse border-radius display "
        "vertical-align box-sizing float clear break-before break-after break-inside "
        "page-break-before page-break-after "
        "page-break-inside table-layout"
    ).split()
)


PROPERTIES.update(
    f"border-{side}-{part}"
    for side in ("top", "right", "bottom", "left")
    for part in ("color", "width", "style")
)
PROPERTIES.update(
    (
        "border-image-source border-image-slice border-image-width "
        "border-image-outset border-image-repeat"
    ).split()
)


def literal(value):
    return escape(value, quote=True).replace("{", "&#123;").replace("}", "&#125;")


def declarations(value):
    result = []
    for declaration in value.split(";"):
        if not declaration.strip():
            continue
        key, sep, val = declaration.partition(":")
        key, val = key.strip().lower(), val.strip()
        # CSSOM expands colours to rgb()/rgba(); no resource or script functions.
        checked_value = re.sub(r"(?:rgba?|hsla?)\([0-9.,%\s]+\)", "color", val)
        if (
            not sep
            or key not in PROPERTIES
            or not re.fullmatch(r"[a-zA-Z0-9#.,%\s'\"+!\-]+", checked_value)
        ):
            raise ValidationError(f"Unsupported style: {key}. Use the formatting controls.")
        result.append(f"{key}:{val}")
    return ";".join(result)


def clean_css(css):
    if not isinstance(css, str) or len(css) > 100000:
        raise ValidationError("Styles are too large.")
    css = re.sub(r"/\*.*?\*/", "", css, flags=re.S)
    rules = []
    while css.strip():
        match = re.match(r"\s*([^{}]+)\{([^{}]*)\}", css)
        if not match:
            raise ValidationError("Invalid stylesheet.")
        selector, values = match.groups()
        if not re.fullmatch(r"[a-zA-Z0-9_#. ,>+*:\-]+", selector) or "@" in selector:
            raise ValidationError("Unsupported style selector.")
        rules.append(selector + "{" + declarations(values) + "}")
        css = css[match.end() :]
    return "".join(rules)


class DesignerParser(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.output = []
        self.stack = []
        self.skip = 0
        self.count = 0

    def handle_starttag(self, tag, attributes):
        self.count += 1
        if self.count > 1500 or len(self.stack) > 40:
            raise ValidationError("Document is too complex.")
        if tag not in TAGS:
            raise ValidationError(f"Unsupported element: {tag}.")
        attrs = dict(attributes)
        if len(attrs) != len(attributes):
            raise ValidationError("Duplicate attributes are not supported.")
        field = attrs.get("data-stock-field")
        repeat = attrs.get("data-stock-row") == "true"
        if repeat and (tag != "tr" or any(row for _, row, _ in self.stack)):
            raise ValidationError("The repeating asset row must be a single table row.")
        if field and field not in FIELDS and field not in {"line." + k for k in LINE_FIELDS}:
            raise ValidationError("Unknown inventory field.")
        if field and field.startswith("line.") and not any(row for _, row, _ in self.stack):
            raise ValidationError("Asset fields must be inside the repeating asset row.")
        if field and tag in VOID:
            raise ValidationError("Inventory fields require a text element.")
        safe = []
        for key, value in attributes:
            value = value or ""
            if key in ("data-stock-field", "data-stock-row"):
                continue
            if key == "style":
                value = declarations(value)
            elif key in ("class", "id"):
                if not re.fullmatch(r"[\w\- ]{0,200}", value):
                    raise ValidationError("Invalid element identifier.")
            elif key in ("colspan", "rowspan"):
                if not value.isdigit() or not 1 <= int(value) <= 20:
                    raise ValidationError("Invalid table span.")
            elif key == "src" and tag == "img":
                if value != "stock-logo":
                    raise ValidationError(
                        "Use the Company logo block; remote images are not allowed."
                    )
                safe.append('src="{{ logo_data_uri }}"')
                continue
            elif key not in ("title", "alt"):
                raise ValidationError(f"Unsupported attribute: {key}.")
            safe.append(f'{key}="{literal(value)}"')
        parent_skip = self.skip
        if not parent_skip:
            if repeat:
                self.output.append("{% for line in lines %}")
            output_tag = "div" if tag == "body" else tag
            self.output.append("<" + output_tag + (" " + " ".join(safe) if safe else "") + ">")
            if field:
                self.output.append("{{ " + field + " }}")
        if tag not in VOID:
            self.stack.append((tag, repeat, parent_skip))
            if field or parent_skip:
                self.skip += 1

    def handle_endtag(self, tag):
        if tag in VOID:
            return
        if not self.stack or self.stack[-1][0] != tag:
            raise ValidationError("Unbalanced document elements.")
        _, repeat, parent_skip = self.stack.pop()
        if self.skip:
            self.skip -= 1
        if not parent_skip:
            self.output.append("</" + ("div" if tag == "body" else tag) + ">")
            if repeat:
                self.output.append("{% endfor %}")

    def handle_data(self, data):
        if not self.skip:
            self.output.append(literal(data))


def compile_design(design):
    if not isinstance(design, dict) or set(design) != {"html", "css"}:
        raise ValidationError("Invalid designer document.")
    html = design["html"]
    if not isinstance(html, str) or len(html) > 200000:
        raise ValidationError("Document is too large.")
    parser = DesignerParser()
    parser.feed(html)
    parser.close()
    if parser.stack:
        raise ValidationError("Unclosed document element.")
    css = clean_css(design["css"])
    return (
        '<!doctype html><html><head><meta charset="utf-8"><style>'
        "@page{size:A4;margin:20mm}body{font-family:Arial;font-size:10pt;}"
        "table{width:100%;border-collapse:collapse}td,th{border:1px solid #555;padding:5px}"
        "thead{display:table-header-group}tr{break-inside:avoid}"
        + css
        + "</style></head><body>"
        + "".join(parser.output)
        + "</body></html>"
    )


def preview_design(*, user, document_type, design, output_format, logo=None):
    from .template_services import _STYLE_FIELDS, get_template, render_preview_pdf

    require_role(user, ADMINISTRATOR)
    current = get_template(document_type)
    return render_preview_pdf(
        document_type=document_type,
        html_source=compile_design(design),
        logo_file=logo,
        output_format=output_format,
        **{field: getattr(current, field) for field in _STYLE_FIELDS} if current else {},
    )


def save_design(*, user, document_type, design, version, preview_confirmed=False, logo=None):
    from django.db import transaction

    from .models import DocumentTemplate
    from .template_services import get_template, update_template

    require_role(user, ADMINISTRATOR)
    source = compile_design(design)
    with transaction.atomic():
        # Serialize saves to existing templates and reject stale browser tabs.
        current = get_template(document_type)
        if current:
            current = DocumentTemplate.objects.select_for_update().get(pk=current.pk)
        if (current.version if current else 0) != version:
            raise ValidationError("This template changed in another tab. Reload before saving.")
        config = dict(current.layout_config) if current else {}
        config["visual_design"] = design
        return update_template(
            user=user,
            document_type=document_type,
            html_source=source,
            layout_config=config,
            custom_html_enabled=True,
            preview_confirmed=preview_confirmed,
            logo=logo,
        )
