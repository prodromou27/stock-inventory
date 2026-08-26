import re

from django import forms

from .models import FontChoice, LogoPosition, PageMargin

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

    def clean_accent_color(self):
        value = self.cleaned_data["accent_color"]
        if not _HEX_COLOR_RE.match(value):
            raise forms.ValidationError("Enter a color as #rrggbb.")
        return value.lower()
