"""
Forms behind the Settings page: the school week, and timing groups.

Both validate the calendar they are about to save (`Calendar.with_changes()` +
`calendar_errors()`), against the new values rather than the stored ones, and
save in one transaction or not at all. Lessons that would fall outside their
class's day always block the save.

Teachers' daily capacities must fit the longest day any class has. With
`fit_capacities` ticked, a capacity equal to the old longest day follows the
new one (up or down) and any other capacity above it is lowered to it, so a
change such as "Friday 9 → 6, Saturday 6 → 9" is one save. Unticked, a
shortened day is refused while capacities exceed it, as in the admin.
"""

import copy

from django import forms
from django.db import transaction

from .constants import (
    MAX_PERIOD_MINUTES,
    MAX_PERIODS_PER_DAY,
    MAX_RECESS_MINUTES,
    MIN_PERIOD_MINUTES,
    Day,
)
from .day_schedule import Calendar, calendar_errors, fit_capacities
from .models import Grade, SchoolDayConfiguration, TimingGroup, TimingGroupDay

TIME_WIDGET = forms.TimeInput(attrs={"type": "time"}, format="%H:%M")
LAYOUT_FIELDS = ["periods", "recess_after_period", "start_time", "period_minutes", "recess_minutes"]


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
    """One weekday's layout. Blank fields inherit (the school default, or the school's day)."""

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

    def __init__(self, *args, day, row=None, with_open=True, placeholders=None, **kwargs):
        self.day = day
        self.row = row
        initial = {f: getattr(row, f) for f in (["is_operating"] if with_open else []) + LAYOUT_FIELDS} if row else {}
        super().__init__(*args, initial=initial, **kwargs)
        if not with_open:
            del self.fields["is_operating"]
        for name, field in self.fields.items():
            attrs = {"aria-label": f"{self.label}: {field.label}"}
            if placeholders and placeholders.get(name) is not None:
                attrs["placeholder"] = placeholders[name]
            if isinstance(field, forms.IntegerField):
                attrs["class"] = "input-num"
            field.widget.attrs.update(attrs)

    @property
    def label(self):
        return Day(self.day).label

    def layout(self):
        return {f: self.cleaned_data[f] for f in LAYOUT_FIELDS}


def _placeholders(schedule):
    return {
        "periods": schedule.periods,
        "recess_after_period": schedule.recess_after,
        "start_time": f"{schedule.start_time:%H:%M}",
        "period_minutes": schedule.period_minutes,
        "recess_minutes": schedule.recess_minutes,
    }


def _bind_all(*forms_):
    """is_valid() on every form, so each one shows its own errors."""
    return all([form.is_valid() for form in forms_])


# --------------------------------------------------------------------------
# The school week
# --------------------------------------------------------------------------


class WeekSettingsForm:
    def __init__(self, data=None):
        self.calendar = Calendar.load()
        self.settings = self.calendar.settings
        self.defaults = DefaultsForm(
            data,
            prefix="school",
            initial={name: getattr(self.settings, name) for name in DefaultsForm.base_fields},
        )
        for field in self.defaults.fields.values():
            if isinstance(field, forms.IntegerField):
                field.widget.attrs["class"] = "input-num"
        s = self.settings
        defaults = {
            "periods": s.default_periods_per_day,
            "recess_after_period": s.default_recess_after_period,
            "start_time": f"{s.day_start_time:%H:%M}",
            "period_minutes": s.period_minutes,
            "recess_minutes": s.recess_minutes,
        }
        self.days = [
            DayForm(data, prefix=f"day-{day}", day=day, row=self.calendar.configs.get(day), placeholders=defaults)
            for day in Day.values
        ]
        self.fit_capacities = True if data is None else data.get("fit_capacities") == "on"
        self.errors = []

    def is_valid(self):
        if not _bind_all(self.defaults, *self.days):
            self.errors.append("Some fields need attention.")
            return False
        settings = copy.copy(self.settings)
        for name, value in self.defaults.cleaned_data.items():
            setattr(settings, name, value)
        configs = {}
        for form in self.days:
            config = copy.copy(form.row) if form.row else SchoolDayConfiguration(day=form.day)
            config.is_operating = form.cleaned_data["is_operating"]
            for name, value in form.layout().items():
                setattr(config, name, value)
            configs[form.day] = config
        self.new_settings, self.new_configs = settings, configs
        self.new_calendar = self.calendar.with_changes(settings=settings, configs=configs)
        self.errors = calendar_errors(self.new_calendar, self.calendar, check_capacities=not self.fit_capacities)
        return not self.errors

    @transaction.atomic
    def save(self):
        """Save the week. Returns the number of teacher capacities that changed."""
        changed = fit_capacities(self.calendar, self.new_calendar) if self.fit_capacities else 0
        self.new_settings.save()
        for config in self.new_configs.values():
            config.save()
        return changed


# --------------------------------------------------------------------------
# Timing groups
# --------------------------------------------------------------------------

NEW_GROUP = "new"


class GroupDetailsForm(forms.Form):
    name = forms.CharField(max_length=100, help_text="e.g. “Class I–V”.")
    order = forms.IntegerField(min_value=0, initial=0, help_text="Lower numbers are listed first.")
    grades = forms.ModelMultipleChoiceField(
        queryset=Grade.objects.select_related("timing_group").order_by("order", "name"),
        widget=forms.CheckboxSelectMultiple,
        required=False,
        help_text="A grade belongs to one group. Ticking a grade from another group moves it here.",
    )

    def __init__(self, *args, group=None, **kwargs):
        self.group = group
        super().__init__(*args, **kwargs)
        self.fields["grades"].label_from_instance = self._grade_label
        self.fields["order"].widget.attrs["class"] = "input-num"

    def _grade_label(self, grade):
        other = grade.timing_group if grade.timing_group_id and grade.timing_group != self.group else None
        return f"{grade.name} (now in {other})" if other else grade.name

    def clean_name(self):
        name = self.cleaned_data["name"].strip()
        clash = TimingGroup.objects.filter(name__iexact=name)
        if self.group is not None:
            clash = clash.exclude(pk=self.group.pk)
        if clash.exists():
            raise forms.ValidationError(f"There is already a timing group called “{name}”.")
        return name


class TimingGroupForm:
    def __init__(self, data=None, *, group=None):
        self.group = group
        self.calendar = Calendar.load()
        initial = {}
        if group is not None:
            initial = {"name": group.name, "order": group.order, "grades": list(group.grades.all())}
        self.details = GroupDetailsForm(data, prefix="group", group=group, initial=initial)
        rows = self.calendar.group_days.get(group.pk, {}) if group else {}
        self.days = [
            DayForm(
                data,
                prefix=f"day-{s.day}",
                day=s.day,
                row=rows.get(s.day),
                with_open=False,
                placeholders=_placeholders(s),
            )
            for s in self.calendar.school_week
        ]
        self.fit_capacities = True if data is None else data.get("fit_capacities") == "on"
        self.errors = []

    @property
    def key(self):
        return self.group.pk if self.group else NEW_GROUP

    def is_valid(self):
        if not _bind_all(self.details, *self.days):
            self.errors.append("Some fields need attention.")
            return False
        self.rows = {}
        for form in self.days:
            layout = form.layout()
            if any(value is not None for value in layout.values()):
                self.rows[form.day] = TimingGroupDay(day=form.day, **layout)
        grades = {g.pk for g in self.details.cleaned_data["grades"]}
        grade_group = {g: self.key for g in grades}
        for grade, group in self.calendar.grade_group.items():
            if group == self.key and grade not in grades:
                grade_group[grade] = None
        self.grade_ids = grades
        self.new_calendar = self.calendar.with_changes(
            groups={self.key: self.details.cleaned_data["name"]},
            group_days={self.key: self.rows},
            grade_group=grade_group,
        )
        self.errors = calendar_errors(self.new_calendar, self.calendar, check_capacities=not self.fit_capacities)
        return not self.errors

    @transaction.atomic
    def save(self):
        """Save the group. Returns (group, number of teacher capacities changed)."""
        changed = fit_capacities(self.calendar, self.new_calendar) if self.fit_capacities else 0
        group = self.group or TimingGroup()
        group.name = self.details.cleaned_data["name"]
        group.order = self.details.cleaned_data["order"]
        group.save()
        Grade.objects.filter(timing_group=group).exclude(pk__in=self.grade_ids).update(timing_group=None)
        Grade.objects.filter(pk__in=self.grade_ids).update(timing_group=group)
        group.days.exclude(day__in=self.rows).delete()
        for day, row in self.rows.items():
            TimingGroupDay.objects.update_or_create(
                group=group, day=day, defaults={f: getattr(row, f) for f in LAYOUT_FIELDS}
            )
        return group, changed


def delete_group_errors(group):
    calendar = Calendar.load()
    return calendar_errors(calendar.with_changes(removed_groups={group.pk}), calendar, check_capacities=False)


@transaction.atomic
def delete_group(group):
    """Remove a group; its grades go back to the school week. Returns capacities changed."""
    calendar = Calendar.load()
    changed = fit_capacities(calendar, calendar.with_changes(removed_groups={group.pk}))
    group.delete()
    return changed
