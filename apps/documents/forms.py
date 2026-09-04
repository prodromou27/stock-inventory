import re

from django import forms

from .models import REPORT_COLUMNS, FontChoice, LogoPosition, PageMargin, PageOrientation, PageSize

_HEX_COLOR_RE = re.compile(r"^#[0-9a-fA-F]{6}$")


class AttachmentUploadForm(forms.Form):
    file = forms.FileField(label="File (PDF, JPEG, or PNG)")


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

    def clean_accent_color(self):
        value = self.cleaned_data["accent_color"]
        if not _HEX_COLOR_RE.match(value):
            raise forms.ValidationError("Enter a color as #rrggbb.")
        return value.lower()

    def layout_config(self):
        """The subset of cleaned_data apps.documents.template_services.
        update_template()/render_preview_pdf() store/preview as
        DocumentTemplate.layout_config — a plain dict, not a nested form, so
        the view doesn't need to know this form's field names individually.
        """
        data = self.cleaned_data
        return {
            "page_size": data["page_size"],
            "orientation": data["orientation"],
            "header_text": data["header_text"],
            "footer_text": data["footer_text"],
            "show_page_numbers": data["show_page_numbers"],
            "show_signature_block": data["show_signature_block"],
            "notes_text": data["notes_text"],
            "terms_text": data["terms_text"],
            "hidden_columns": data["hidden_columns"],
        }
