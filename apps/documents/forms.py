import re

from django import forms

from .layout import LAYOUT_DEFAULTS
from .models import (
    REPORT_COLUMNS,
    FontChoice,
    LogoPosition,
    PageMargin,
    PageOrientation,
    PageSize,
    SectionSpacing,
)
from .pdf import sanitize_css_content_text

_HEX_COLOR_RE = re.compile(r"^#[0-9a-fA-F]{6}$")
_VALID_REPORT_COLUMN_KEYS = {key for key, _ in REPORT_COLUMNS}


class AttachmentUploadForm(forms.Form):
    file = forms.FileField(label="File (PDF, JPEG, or PNG)")


class CountryBrandingForm(forms.Form):
    """One legal entity's branding overrides (apps.documents.branding) —
    every field optional; blank always means "inherit from the document
    type's own template", never "render blank".
    """

    logo = forms.FileField(
        required=False, label="Logo (PNG or JPEG) — leave blank to keep the current one"
    )
    remove_logo = forms.BooleanField(required=False, label="Remove the current logo")
    company_name = forms.CharField(max_length=200, required=False, label="Company name")
    company_address = forms.CharField(
        required=False, widget=forms.Textarea(attrs={"rows": 2}), label="Company address"
    )
    company_tax_id = forms.CharField(
        max_length=60, required=False, label="Company tax/registration ID"
    )
    terms_text = forms.CharField(
        required=False,
        widget=forms.Textarea(attrs={"rows": 3}),
        label="Terms and conditions override",
        help_text="Overrides every document type's own terms wording when set.",
    )
    signature_left_label = forms.CharField(max_length=120, required=False)
    signature_right_label = forms.CharField(max_length=120, required=False)


class DocumentTemplateStyleForm(forms.Form):
    """The whole document-template editor (apps.documents.views.
    DocumentTemplateEditView) — no HTML/template-syntax field on purpose.
    Everything an Administrator can change is branding; the report's actual
    data fields (document number, line items, signatures, ...) are always
    placed by the packaged skeleton (apps.documents.pdf.
    render_styleable_source()), never typed or positioned by hand.
    """

    logo = forms.FileField(
        required=False, label="Logo (PNG or JPEG) — leave blank to keep the current one"
    )
    remove_logo = forms.BooleanField(required=False, label="Remove the current logo")
    logo_intentionally_omitted = forms.BooleanField(
        required=False, label="This template doesn't need a logo"
    )
    custom_html_enabled = forms.BooleanField(
        required=False,
        label="Edit the raw HTML/CSS source instead of the structured layout below",
        help_text="Full control over layout beyond the structured fields' fixed options. The "
        "structured fields below still populate the data placeholders available to your HTML "
        "(document title, company details, colors, ...) but no longer auto-generate the markup "
        "itself.",
    )
    custom_html_source = forms.CharField(
        required=False,
        widget=forms.Textarea(attrs={"rows": 24, "class": "code-editor", "spellcheck": "false"}),
        label="Template HTML/CSS source",
        help_text="Django template syntax. External resources (images, fonts, stylesheets) "
        "can't be fetched from a URL — embed a logo via the Logo field above, not an <img src>.",
    )
    logo_position = forms.ChoiceField(
        choices=LogoPosition.choices, label="Logo position", initial=LogoPosition.LEFT
    )
    accent_color = forms.CharField(
        max_length=7,
        label="Accent color",
        widget=forms.TextInput(attrs={"type": "color"}),
        initial="#444444",
    )
    font_choice = forms.ChoiceField(
        choices=FontChoice.choices, label="Font", initial=FontChoice.SANS
    )
    page_margin = forms.ChoiceField(
        choices=PageMargin.choices, label="Page margins", initial=PageMargin.NORMAL
    )
    section_spacing = forms.ChoiceField(
        choices=SectionSpacing.choices, label="Section spacing", initial=SectionSpacing.NORMAL
    )
    heading_text_color = forms.CharField(
        max_length=7,
        required=False,
        label="Heading text color (optional — defaults to the accent color)",
        widget=forms.TextInput(attrs={"placeholder": "Default accent color or #rrggbb"}),
    )
    table_header_bg_color = forms.CharField(
        max_length=7,
        required=False,
        label="Table header background (optional — defaults to light grey)",
        widget=forms.TextInput(attrs={"placeholder": "Default light grey or #rrggbb"}),
    )
    document_title = forms.CharField(
        max_length=120,
        required=False,
        label="Document title override (optional — defaults to the transaction type, e.g. "
        "'Customer delivery')",
    )
    company_name = forms.CharField(max_length=200, required=False, label="Company name (optional)")
    company_address = forms.CharField(
        required=False,
        widget=forms.Textarea(attrs={"rows": 2}),
        label="Company address (optional)",
    )
    company_tax_id = forms.CharField(
        max_length=60, required=False, label="Company tax/registration ID (optional)"
    )
    page_size = forms.ChoiceField(choices=PageSize.choices, label="Page size", initial=PageSize.A4)
    orientation = forms.ChoiceField(
        choices=PageOrientation.choices, label="Orientation", initial=PageOrientation.PORTRAIT
    )
    header_text = forms.CharField(
        max_length=200, required=False, label="Running header text (optional)"
    )
    footer_text = forms.CharField(
        max_length=200, required=False, label="Running footer text (optional)"
    )
    show_page_numbers = forms.BooleanField(required=False, label="Show page numbers", initial=False)
    show_signature_block = forms.BooleanField(
        required=False, label="Show signature block", initial=True
    )
    notes_text = forms.CharField(
        required=False,
        widget=forms.Textarea(attrs={"rows": 3}),
        label="Standing notes (shown on every document, in addition to that delivery's own notes)",
    )
    terms_text = forms.CharField(
        required=False,
        widget=forms.Textarea(attrs={"rows": 3}),
        label="Terms and conditions (optional)",
    )
    hidden_columns = forms.MultipleChoiceField(
        choices=REPORT_COLUMNS,
        required=False,
        widget=forms.CheckboxSelectMultiple,
        label="Hide these line-item columns",
    )
    column_order = forms.CharField(
        max_length=200,
        required=False,
        label="Column order (optional)",
        help_text='Comma-separated column keys, e.g. "serial,brand,model,quantity" — '
        f"valid keys: {', '.join(key for key, _ in REPORT_COLUMNS)}. Leave blank for the default "
        "order. Use Arrange & columns in the visual designer to change this without typing keys.",
    )
    column_labels = forms.CharField(
        required=False,
        widget=forms.Textarea(attrs={"rows": 3}),
        label="Custom column labels (optional)",
        help_text='One override per line as "key:Label", e.g. "sku:Part Number".',
    )

    section_order = forms.CharField(required=False, label="Section order")
    acceptance_signature_label = forms.CharField(
        max_length=120, required=False, initial="Signature"
    )
    acceptance_name_label = forms.CharField(max_length=120, required=False, initial="Name")
    acceptance_position_label = forms.CharField(max_length=120, required=False, initial="Position")
    acceptance_date_label = forms.CharField(max_length=120, required=False, initial="Date")
    layout_variant = forms.ChoiceField(
        choices=[("standard", "Standard document"), ("acceptance", "Acceptance / sign-off")],
        initial="standard",
        required=False,
    )
    reference_caption = forms.CharField(
        max_length=40, required=False, initial="P.D", label="Document reference box caption"
    )
    custom_blocks = forms.JSONField(required=False, widget=forms.HiddenInput(), initial=list)
    body_font_size = forms.IntegerField(required=False, min_value=8, max_value=16, initial=10)
    heading_font_size = forms.IntegerField(required=False, min_value=12, max_value=32, initial=16)
    table_font_size = forms.IntegerField(required=False, min_value=7, max_value=14, initial=9)
    table_cell_padding = forms.IntegerField(required=False, min_value=2, max_value=12, initial=4)
    signature_left_label = forms.CharField(required=False, max_length=120)
    signature_right_label = forms.CharField(required=False, max_length=120)
    preview_confirmed = forms.BooleanField(
        required=False,
        label="I have reviewed the PDF preview and it looks correct",
        help_text="Required (alongside the rest of the completeness checklist) before this "
        "template can be published — resets on every save, so re-check after each change.",
    )

    def clean(self):
        cleaned_data = super().clean()
        if cleaned_data.get("custom_html_enabled") and not cleaned_data.get("custom_html_source"):
            self.add_error(
                "custom_html_source", "Enter the template HTML/CSS, or turn off custom HTML."
            )
        return cleaned_data

    def clean_section_order(self):
        return [key.strip() for key in self.cleaned_data["section_order"].split(",") if key.strip()]

    def clean_accent_color(self):
        value = self.cleaned_data["accent_color"]
        if not _HEX_COLOR_RE.match(value):
            raise forms.ValidationError("Enter a color as #rrggbb.")
        return value.lower()

    def _clean_optional_color(self, field_name):
        value = self.cleaned_data[field_name]
        if value and not _HEX_COLOR_RE.match(value):
            raise forms.ValidationError("Enter a color as #rrggbb.")
        return value.lower() if value else value

    def clean_heading_text_color(self):
        return self._clean_optional_color("heading_text_color")

    def clean_table_header_bg_color(self):
        return self._clean_optional_color("table_header_bg_color")

    def clean_header_text(self):
        return sanitize_css_content_text(self.cleaned_data["header_text"])

    def clean_footer_text(self):
        return sanitize_css_content_text(self.cleaned_data["footer_text"])

    def clean_column_order(self):
        raw = self.cleaned_data["column_order"]
        if not raw:
            return []
        keys = [key.strip() for key in raw.split(",") if key.strip()]
        unknown = [key for key in keys if key not in _VALID_REPORT_COLUMN_KEYS]
        if unknown:
            raise forms.ValidationError(f"Unknown column key(s): {', '.join(unknown)}.")
        return keys

    def clean_column_labels(self):
        raw = self.cleaned_data["column_labels"]
        labels = {}
        for line in raw.splitlines():
            line = line.strip()
            if not line:
                continue
            if ":" not in line:
                raise forms.ValidationError(f'"{line}" must be in "key:Label" form.')
            key, _, label = line.partition(":")
            key = key.strip()
            label = label.strip()
            if key not in _VALID_REPORT_COLUMN_KEYS:
                raise forms.ValidationError(f'Unknown column key "{key}".')
            if label:
                labels[key] = label
        return labels

    def layout_config(self):
        """The subset of cleaned_data apps.documents.template_services.
        update_template()/render_preview_pdf() store/preview as
        DocumentTemplate.layout_config — a plain dict, not a nested form, so
        the view doesn't need to know this form's field names individually.
        Keyed off models.LAYOUT_CONFIG_DEFAULTS so adding a new layout_config
        key only ever requires a matching form field of the same name.
        """
        from .models import LAYOUT_CONFIG_DEFAULTS

        return {
            key: self.cleaned_data.get(key, default)
            for key, default in {**LAYOUT_CONFIG_DEFAULTS, **LAYOUT_DEFAULTS}.items()
        }
