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
    "custom_blocks": [],
    "layout_variant": "standard",
    "reference_caption": "P.D",
    "acceptance_signature_label": "Signature",
    "acceptance_name_label": "Name",
    "acceptance_position_label": "Position",
    "acceptance_date_label": "Date",
}


def clean_presentation(config):
    result = {key: config.get(key, default) for key, default in LAYOUT_DEFAULTS.items()}
    result["layout_variant"] = result["layout_variant"] or "standard"
    if result["layout_variant"] not in ("standard", "acceptance"):
        raise ValidationError("Choose a supported document layout.")
    caption = result["reference_caption"] or "P.D"
    if not isinstance(caption, str) or len(caption) > 40:
        raise ValidationError("Reference caption must be 40 characters or fewer.")
    result["reference_caption"] = caption
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
    for key in (
        "signature_left_label",
        "signature_right_label",
        "acceptance_signature_label",
        "acceptance_name_label",
        "acceptance_position_label",
        "acceptance_date_label",
    ):
        value = result[key] or ""
        if not isinstance(value, str) or len(value) > 120:
            raise ValidationError("Signature labels must be 120 characters or fewer.")
        result[key] = value
    blocks = result["custom_blocks"] or []
    if not isinstance(blocks, list) or len(blocks) > 12:
        raise ValidationError("Use at most 12 custom text blocks.")
    cleaned_blocks = []
    for block in blocks:
        if not isinstance(block, dict):
            raise ValidationError("Invalid text block.")
        text = block.get("text", "")
        before = block.get("before", "signatures")
        alignment = block.get("alignment", "left")
        if not isinstance(text, str) or len(text) > 5000:
            raise ValidationError("Each text block must be 5,000 characters or fewer.")
        if before not in dict(SECTIONS) or alignment not in ("left", "center", "right"):
            raise ValidationError("Choose a valid text block position and alignment.")
        cleaned_blocks.append({"text": text, "before": before, "alignment": alignment})
    result["custom_blocks"] = cleaned_blocks
    return result
