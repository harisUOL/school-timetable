from django import forms
from django.conf import settings

from .models import GenerationRun


class GenerationForm(forms.Form):
    keep_mode = forms.ChoiceField(
        choices=GenerationRun.KeepMode.choices,
        initial=GenerationRun.KeepMode.KEEP_ALL,
        widget=forms.RadioSelect,
        label="Existing lessons",
    )
    mode = forms.ChoiceField(
        choices=GenerationRun.Mode.choices,
        initial=GenerationRun.Mode.COMPLETE,
        widget=forms.RadioSelect,
        label="Goal",
    )
    time_limit = forms.IntegerField(
        min_value=1,
        max_value=settings.GENERATOR["MAX_TIME_LIMIT"],
        initial=settings.GENERATOR["DEFAULT_TIME_LIMIT"],
        label="Time limit (seconds)",
        help_text="The solver stops at this limit and returns the best timetable found so far.",
    )
    random_seed = forms.IntegerField(
        min_value=0,
        initial=0,
        label="Random seed",
        help_text="Change it to get a different timetable of similar quality.",
    )
