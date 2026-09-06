"""A small set of packaged starter layouts (spec: "start from approved
company layouts rather than configuring every delivery/assignment template
from scratch") — plain Python data, never database rows an Administrator
could accidentally edit or delete out from under everyone else picking the
same starting point. Applying one (apps.documents.template_services.
apply_starter_template()) behaves exactly like duplicate_template(): it
creates a brand-new Draft row an Administrator can then customize freely:
picking a starter never edits, and is never itself editable.
"""

STARTER_TEMPLATES = {
    "classic": {
        "label": "Classic",
        "description": "Left logo, a restrained blue accent, serif heading — a traditional, "
        "letterhead-style layout.",
        "fields": {
            "logo_position": "left",
            "accent_color": "#1d4ed8",
            "font_choice": "serif",
            "page_margin": "normal",
            "section_spacing": "normal",
        },
    },
    "minimal": {
        "label": "Minimal",
        "description": "Centered logo, monospace type, compact spacing, and a neutral grey "
        "palette — a plain, no-frills layout.",
        "fields": {
            "logo_position": "center",
            "accent_color": "#111827",
            "font_choice": "mono",
            "page_margin": "compact",
            "section_spacing": "compact",
            "heading_text_color": "#111827",
            "table_header_bg_color": "#f3f4f6",
        },
    },
    "bold_header": {
        "label": "Bold header",
        "description": "Right-aligned logo, a strong red accent and heading color, spacious "
        "margins — a layout that leads with the header.",
        "fields": {
            "logo_position": "right",
            "accent_color": "#b91c1c",
            "font_choice": "sans",
            "page_margin": "spacious",
            "section_spacing": "spacious",
            "heading_text_color": "#b91c1c",
            "table_header_bg_color": "#fee2e2",
        },
    },
}
