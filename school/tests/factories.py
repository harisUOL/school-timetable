"""Small helpers for building test data. Plain functions, no extra dependency."""

from school.constants import Day
from school.models import (
    ClassGroup,
    Grade,
    Section,
    Subject,
    SubjectRequirement,
    Teacher,
    TeacherDailyAvailability,
    TeacherSubject,
)


def make_class(grade="10", section="A", order=None):
    grade_obj, _ = Grade.objects.get_or_create(
        name=grade, defaults={"order": order if order is not None else int(grade)}
    )
    section_obj, _ = Section.objects.get_or_create(name=section)
    class_group, _ = ClassGroup.objects.get_or_create(grade=grade_obj, section=section_obj)
    return class_group


def make_subject(name="Mathematics", code=None):
    subject, _ = Subject.objects.get_or_create(name=name, defaults={"code": code})
    return subject


def make_teacher(name="Teacher A", subjects=(), capacity=None, **kwargs):
    """
    capacity: None for no availability records, an int for the same maximum
    on Monday–Saturday, or a {day: max} dict.
    """
    teacher = Teacher.objects.create(name=name, **kwargs)
    for subject in subjects:
        TeacherSubject.objects.create(teacher=teacher, subject=subject)
    if isinstance(capacity, int):
        capacity = {day: capacity for day in range(Day.MONDAY, Day.SUNDAY)}
    for day, max_periods in (capacity or {}).items():
        TeacherDailyAvailability.objects.create(teacher=teacher, day=day, max_periods=max_periods)
    return teacher


def make_requirement(class_group, subject, periods=5, teacher=None):
    return SubjectRequirement.objects.create(
        class_group=class_group, subject=subject, periods_per_week=periods, teacher=teacher
    )
