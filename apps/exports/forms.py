from django import forms

from .models import ExportSchedule


class ExportSettingsForm(forms.Form):
    export_path = forms.CharField(
        max_length=500,
        required=False,
        label="Export path (local or network path)",
        help_text=(
            r"e.g. \\fileserver\backups\stock-inventory or /mnt/backups. Must already be reachable "
            "from wherever the app runs — mount/share it there first."
        ),
    )
    schedule = forms.ChoiceField(choices=ExportSchedule.choices, label="Run")
    weekly_weekday = forms.ChoiceField(
        choices=[
            (0, "Monday"),
            (1, "Tuesday"),
            (2, "Wednesday"),
            (3, "Thursday"),
            (4, "Friday"),
            (5, "Saturday"),
            (6, "Sunday"),
        ],
        label="Weekly on",
        help_text="Only used when Run = Weekly.",
    )
