"""Forms used by the school admin's bulk actions."""

from django import forms

from .constants import MAX_PERIODS_PER_DAY
from .models import ClassGroup, Teacher


class AdminActionForm(forms.Form):
    """Base for intermediate admin-action pages. Carries the selected IDs."""

    _selected_action = forms.CharField(widget=forms.MultipleHiddenInput)


class FillAvailabilityForm(AdminActionForm):
    max_periods = forms.IntegerField(
        min_value=0,
        max_value=MAX_PERIODS_PER_DAY,
        label="Maximum periods per day",
        help_text="Applied to every operating day. Capped at each day's number of periods.",
    )
    overwrite = forms.BooleanField(
        required=False,
        label="Overwrite days that are already configured",
        help_text="Unticked: only days with no capacity record are filled.",
    )


class CopyTeacherConfigurationForm(AdminActionForm):
    source = forms.ModelChoiceField(
        queryset=Teacher.objects.all(), label="Copy from teacher"
    )
    copy_subjects = forms.BooleanField(required=False, initial=True, label="Copy subjects")
    copy_availability = forms.BooleanField(
        required=False, initial=True, label="Copy daily capacities"
    )
    overwrite_availability = forms.BooleanField(
        required=False,
        label="Overwrite existing daily capacities",
        help_text="Subjects are always added, never removed.",
    )

    def clean(self):
        data = super().clean()
        if not data.get("copy_subjects") and not data.get("copy_availability"):
            raise forms.ValidationError("Choose at least one thing to copy.")
        return data


class CopyRequirementsForm(AdminActionForm):
    source = forms.ModelChoiceField(
        queryset=ClassGroup.objects.with_structure(), label="Copy from class"
    )
    overwrite = forms.BooleanField(
        required=False,
        label="Overwrite periods for subjects the target already has",
    )
    include_teacher = forms.BooleanField(
        required=False,
        label="Also copy designated teachers",
        help_text="Usually left unticked: each section normally has its own teachers.",
    )
