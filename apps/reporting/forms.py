from django import forms

from .models import ReportBaseModel
from .report_builder import ALLOWED_FILTER_OPS, field_choices


class ReportBaseModelForm(forms.Form):
    """Step 1 of the report builder — which model to report on. A separate
    step (not folded into ReportBuilderForm) because the field/filter
    choices on step 2 depend on this choice and this codebase has no JS to
    refresh a dropdown's options without a page load.
    """

    base_model = forms.ChoiceField(choices=ReportBaseModel.choices, label="Report on")


class ReportBuilderForm(forms.Form):
    name = forms.CharField(max_length=120)
    selected_fields = forms.MultipleChoiceField(
        choices=(), widget=forms.CheckboxSelectMultiple, label="Columns"
    )
    is_shared = forms.BooleanField(
        required=False, label="Share with everyone (Administrators only)"
    )

    def __init__(self, *args, base_model=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["selected_fields"].choices = field_choices(base_model)


class ReportFilterRowForm(forms.Form):
    """One optional filter row — a fixed number of slots
    (apps.reporting.forms.ReportFilterFormSet's extra=) rather than a
    JS-driven "add another filter" button, matching this codebase's
    established fixed-slot pattern for bulk/optional rows (see
    apps.catalog.forms.QuickAddProductRowForm). A row with no field chosen,
    or no value entered, is silently skipped — not an error.
    """

    field_key = forms.ChoiceField(choices=(), required=False, label="Field")
    op = forms.ChoiceField(
        choices=[(op, op) for op in ALLOWED_FILTER_OPS], required=False, label="Is"
    )
    value = forms.CharField(required=False, max_length=200, label="Value")

    def __init__(self, *args, base_model=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["field_key"].choices = [("", "—")] + field_choices(base_model)

    def clean(self):
        cleaned = super().clean()
        if not cleaned.get("field_key") or not cleaned.get("value"):
            return cleaned  # nothing entered on this row — silently skipped
        if not cleaned.get("op"):
            cleaned["op"] = "exact"
        return cleaned


ReportFilterFormSet = forms.formset_factory(ReportFilterRowForm, extra=3)
