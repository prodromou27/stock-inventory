from django import forms


class ReferenceCorrectionForm(forms.Form):
    """apps.inventory.services.corrections.correct_reference_fields() — the
    one finding type (customer stock missing customer/project reference)
    with no pre-existing admin-correction screen to link out to.
    """

    project_reference = forms.CharField(max_length=120, required=False)
    final_customer = forms.CharField(max_length=120, required=False, label="Final customer")
    reason = forms.CharField(widget=forms.Textarea, label="Reason")

    def clean(self):
        cleaned = super().clean()
        if not cleaned.get("project_reference") and not cleaned.get("final_customer"):
            raise forms.ValidationError("Enter a project reference and/or a final customer.")
        return cleaned


class ResolutionForm(forms.Form):
    resolution_note = forms.CharField(
        required=False, widget=forms.Textarea, label="Note (optional)"
    )
