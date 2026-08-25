from django import forms


class AttachmentUploadForm(forms.Form):
    file = forms.FileField(label="File (PDF, JPEG, or PNG)")


class DocumentTemplateForm(forms.Form):
    html_source = forms.CharField(
        label="Template HTML",
        widget=forms.Textarea(
            attrs={"rows": 30, "spellcheck": "false", "class": "template-editor"}
        ),
    )
    logo = forms.FileField(
        required=False, label="Logo (PNG or JPEG) — leave blank to keep the current one"
    )
    remove_logo = forms.BooleanField(required=False, label="Remove the current logo")
