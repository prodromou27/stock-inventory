"""Bounded presentation configuration shared by services and the visual editor."""

from django.core.exceptions import ValidationError

SECTIONS = [
    ("company", "Company details"),
    ("heading", "Logo and document title"),
    ("details", "Transaction details"),
    ("items", "Line items"),
    ("disposal", "Disposal certificate"),
    ("notes", "Notes and prepared by"),
    ("signatures", "Signatures"),
    ("terms", "Terms and conditions"),
]

LAYOUT_DEFAULTS = {
    "section_order": [key for key, _ in SECTIONS],
    "body_font_size": 10,
    "heading_font_size": 16,
    "table_font_size": 9,
    "table_cell_padding": 4,
    "signature_left_label": "",
    "signature_right_label": "",
}


def clean_presentation(config):
    result = {key: config.get(key, default) for key, default in LAYOUT_DEFAULTS.items()}
    order = result["section_order"] or LAYOUT_DEFAULTS["section_order"]
    if not isinstance(order, list) or any(key not in dict(SECTIONS) for key in order):
        raise ValidationError("Unknown document section.")
    if len(order) != len(set(order)):
        raise ValidationError("Each document section may appear only once.")
    result["section_order"] = order + [key for key, _ in SECTIONS if key not in order]
    for key, minimum, maximum in [
        ("body_font_size", 8, 16),
        ("heading_font_size", 12, 32),
        ("table_font_size", 7, 14),
        ("table_cell_padding", 2, 12),
    ]:
        value = result[key]
        if value is None or value == "":
            value = LAYOUT_DEFAULTS[key]
        if isinstance(value, bool) or not isinstance(value, int) or not minimum <= value <= maximum:
            raise ValidationError(f"{key} must be between {minimum} and {maximum}.")
        result[key] = value
    for key in ("signature_left_label", "signature_right_label"):
        value = result[key] or ""
        if not isinstance(value, str) or len(value) > 120:
            raise ValidationError("Signature labels must be 120 characters or fewer.")
        result[key] = value
    return result
