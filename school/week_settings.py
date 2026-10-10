"""
The school week editor on the Settings page: default day layout and bell
times, plus per-day overrides (blank inherits the default).

The whole week is validated as one change, against the new values rather than
the stored ones, and saved in one transaction or not at all. The same rules as
the models apply (`validate_day_layout`, `validate_day_times`); lessons beyond
a shortened day always block the save.

Teachers' daily capacities must fit the day. With `fit_capacities` ticked,
a capacity equal to the old full day follows the day's new length (up or
down) and any other capacity above the new length is lowered to it, so a
change such as "Friday 9 → 6, Saturday 6 → 9" is one save. Unticked, a
shortened day is refused while capacities exceed it, as in the admin.
"""

from dataclasses import dataclass

from django import forms
from django.db import transaction

from .constants import (
    MAX_PERIOD_MINUTES,
    MAX_PERIODS_PER_DAY,
    MAX_RECESS_MINUTES,
    MIN_PERIOD_MINUTES,
    Day,
)
from .models import (
    SchoolDayConfiguration,
    SchoolSettings,
    TeacherDailyAvailability,
    validate_day_layout,
    validate_day_times,
)

TIME_WIDGET = forms.TimeInput(attrs={"type": "time"}, format="%H:%M")


class DefaultsForm(forms.Form):
    school_name = forms.CharField(max_length=200)
    day_start_time = forms.TimeField(label="School starts", widget=TIME_WIDGET)
    period_minutes = forms.IntegerField(
        label="Period length (minutes)", min_value=MIN_PERIOD_MINUTES, max_value=MAX_PERIOD_MINUTES
    )
    default_periods_per_day = forms.IntegerField(label="Periods per day", min_value=1, max_value=MAX_PERIODS_PER_DAY)
    default_recess_after_period = forms.IntegerField(
        label="Recess after period (0 = none)", min_value=0, max_value=MAX_PERIODS_PER_DAY - 1
    )
    recess_minutes = forms.IntegerField(label="Recess length (minutes)", min_value=0, max_value=MAX_RECESS_MINUTES)


class DayForm(forms.Form):
    """One weekday. Blank fields inherit the school default."""

    is_operating = forms.BooleanField(required=False, label="Open")
    periods = forms.IntegerField(required=False, min_value=1, max_value=MAX_PERIODS_PER_DAY, label="Periods")
    recess_after_period = forms.IntegerField(
        required=False, min_value=0, max_value=MAX_PERIODS_PER_DAY - 1, label="Recess after"
    )
    start_time = forms.TimeField(required=False, label="Starts", widget=TIME_WIDGET)
    period_minutes = forms.IntegerField(
        required=False, min_value=MIN_PERIOD_MINUTES, max_value=MAX_PERIOD_MINUTES, label="Period (min)"
    )
    recess_minutes = forms.IntegerField(required=False, min_value=0, max_value=MAX_RECESS_MINUTES, label="Recess (min)")

    def __init__(self, *args, day, config, **kwargs):
        self.day = day
        self.config = config
        super().__init__(*args, **kwargs)

    @property
    def label(self):
        return Day(self.day).label


@dataclass
class Layout:
    """A day's effective layout, from stored or submitted values."""

    is_operating: bool
    periods: int
    recess_after: int
    start_time: object
    period_minutes: int
    recess_minutes: int

    @property
    def length(self):
        return self.periods if self.is_operating else 0


def stored_layout(config, settings):
    if config is None:
        return Layout(False, 0, 0, None, 0, 0)
    return Layout(
        config.is_operating,
        config.effective_periods(settings),
        config.effective_recess_after(settings),
        config.effective_start_time(settings),
        config.effective_period_minutes(settings),
        config.effective_recess_minutes(settings),
    )


def _inherit(value, default):
    return default if value is None else value


class WeekSettingsForm:
    FIELDS = ["is_operating", "periods", "recess_after_period", "start_time", "period_minutes", "recess_minutes"]

    def __init__(self, data=None):
        self.settings = SchoolSettings.load()
        self.configs = {c.day: c for c in SchoolDayConfiguration.objects.all()}
        self.defaults = DefaultsForm(
            data,
            prefix="school",
            initial={
                "school_name": self.settings.school_name,
                "day_start_time": self.settings.day_start_time,
                "period_minutes": self.settings.period_minutes,
                "default_periods_per_day": self.settings.default_periods_per_day,
                "default_recess_after_period": self.settings.default_recess_after_period,
                "recess_minutes": self.settings.recess_minutes,
            },
        )
        self.days = [
            DayForm(
                data,
                prefix=f"day-{day}",
                day=day,
                config=self.configs.get(day),
                initial={f: getattr(self.configs[day], f) for f in self.FIELDS} if day in self.configs else {},
            )
            for day in Day.values
        ]
        self._decorate()
        self.fit_capacities = True if data is None else data.get("fit_capacities") == "on"
        self.errors = []
        self.capacity_changes = 0

    def _decorate(self):
        """Accessible names for the inputs, and the inherited default as each blank field's placeholder."""
        s = self.settings
        defaults = {
            "periods": s.default_periods_per_day,
            "recess_after_period": s.default_recess_after_period,
            "start_time": f"{s.day_start_time:%H:%M}",
            "period_minutes": s.period_minutes,
            "recess_minutes": s.recess_minutes,
        }
        for form in self.days:
            for name, field in form.fields.items():
                attrs = {"aria-label": f"{form.label}: {field.label}"}
                if name in defaults:
                    attrs["placeholder"] = defaults[name]
                if isinstance(field, forms.IntegerField):
                    attrs["class"] = "input-num"
                field.widget.attrs.update(attrs)
        for name, field in self.defaults.fields.items():
            if isinstance(field, forms.IntegerField):
                field.widget.attrs["class"] = "input-num"

    def new_layout(self, form):
        d, s = form.cleaned_data, self.defaults.cleaned_data
        return Layout(
            d["is_operating"],
            _inherit(d["periods"], s["default_periods_per_day"]),
            _inherit(d["recess_after_period"], s["default_recess_after_period"]),
            _inherit(d["start_time"], s["day_start_time"]),
            _inherit(d["period_minutes"], s["period_minutes"]),
            _inherit(d["recess_minutes"], s["recess_minutes"]),
        )

    def is_valid(self):
        valid = self.defaults.is_valid()
        valid = all([form.is_valid() for form in self.days]) and valid
        if not valid:
            self.errors.append("Some fields need attention.")
            return False
        if not any(form.cleaned_data["is_operating"] for form in self.days):
            self.errors.append("At least one day must be open.")
        for form in self.days:
            new = self.new_layout(form)
            # Capacities are brought into line on save when fit_capacities is ticked.
            self.errors += validate_day_layout(
                form.day, new.periods, new.recess_after, new.is_operating, check_capacities=not self.fit_capacities
            )
            if new.is_operating:
                self.errors += validate_day_times(
                    form.day, new.periods, new.recess_after, new.start_time, new.period_minutes, new.recess_minutes
                )
        return not self.errors

    @transaction.atomic
    def save(self):
        """Save the week. Returns the number of teacher capacities that changed."""
        if self.fit_capacities:
            for form in self.days:
                old = stored_layout(self.configs.get(form.day), self.settings).length
                new = self.new_layout(form).length
                if old != new:
                    self.capacity_changes += fit_capacities(form.day, old, new)

        for name, value in self.defaults.cleaned_data.items():
            setattr(self.settings, name, value)
        self.settings.save()
        for form in self.days:
            config = form.config or SchoolDayConfiguration(day=form.day)
            for name in self.FIELDS:
                setattr(config, name, form.cleaned_data[name])
            config.save()
        return self.capacity_changes


def fit_capacities(day, old_length, new_length):
    """
    Bring one day's capacities into line with its new length. A full-day
    capacity (equal to the old length) follows the new length; anything else
    above the new length is lowered to it. Returns the number of records changed.
    """
    records = TeacherDailyAvailability.objects.filter(day=day)
    changed = 0
    if old_length and new_length:
        changed += records.filter(max_periods=old_length).update(max_periods=new_length)
    changed += records.filter(max_periods__gt=new_length).update(max_periods=new_length)
    return changed
