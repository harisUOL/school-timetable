from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
from django.test import TestCase

from school.constants import Day
from school.models import (
    ClassGroup,
    Grade,
    SchoolDayConfiguration,
    Section,
    Subject,
    SubjectRequirement,
    TeacherDailyAvailability,
    TeacherSubject,
)

from .factories import make_class, make_requirement, make_subject, make_teacher


class StructureTests(TestCase):
    def test_class_group_label(self):
        self.assertEqual(str(make_class("10", "A")), "10A")

    def test_class_group_is_unique(self):
        class_group = make_class("10", "A")
        duplicate = ClassGroup(grade=class_group.grade, section=class_group.section)
        with self.assertRaises(ValidationError):
            duplicate.full_clean()
        with self.assertRaises(IntegrityError), transaction.atomic():
            duplicate.save()

    def test_names_unique_case_insensitively(self):
        Grade.objects.create(name="Ten")
        Section.objects.create(name="A")
        Subject.objects.create(name="Mathematics")
        for duplicate in (Grade(name="ten"), Section(name="a"), Subject(name="MATHEMATICS")):
            with self.assertRaises(ValidationError):
                duplicate.full_clean()

    def test_blank_subject_codes_are_stored_as_null(self):
        a = Subject.objects.create(name="Music", code="")
        b = Subject.objects.create(name="Art", code="  ")
        self.assertIsNone(a.code)
        self.assertIsNone(b.code)

    def test_subject_code_normalised_and_unique(self):
        Subject.objects.create(name="Physics", code="phy")
        self.assertTrue(Subject.objects.filter(code="PHY").exists())
        with self.assertRaises(IntegrityError), transaction.atomic():
            Subject.objects.create(name="Physical Science", code="PHY")


class TeacherSubjectTests(TestCase):
    def test_teacher_can_teach_several_subjects(self):
        maths, physics = make_subject("Mathematics"), make_subject("Physics")
        teacher = make_teacher(subjects=[maths, physics])
        self.assertTrue(teacher.is_qualified_for(maths))
        self.assertTrue(teacher.is_qualified_for(physics))
        self.assertFalse(teacher.is_qualified_for(make_subject("Biology")))

    def test_pair_is_unique(self):
        maths = make_subject()
        teacher = make_teacher(subjects=[maths])
        with self.assertRaises(IntegrityError), transaction.atomic():
            TeacherSubject.objects.create(teacher=teacher, subject=maths)


class TeacherDailyAvailabilityTests(TestCase):
    def setUp(self):
        self.teacher = make_teacher()

    def test_missing_record_means_unavailable(self):
        self.assertEqual(self.teacher.capacity_for_day(Day.MONDAY), 0)

    def test_capacity_is_a_daily_maximum(self):
        TeacherDailyAvailability.objects.create(teacher=self.teacher, day=Day.MONDAY, max_periods=5)
        self.assertEqual(self.teacher.capacity_for_day(Day.MONDAY), 5)

    def test_one_record_per_day(self):
        TeacherDailyAvailability.objects.create(teacher=self.teacher, day=Day.MONDAY, max_periods=5)
        with self.assertRaises(IntegrityError), transaction.atomic():
            TeacherDailyAvailability.objects.create(
                teacher=self.teacher, day=Day.MONDAY, max_periods=3
            )

    def test_cannot_exceed_periods_in_day(self):
        record = TeacherDailyAvailability(teacher=self.teacher, day=Day.MONDAY, max_periods=10)
        with self.assertRaises(ValidationError):
            record.full_clean()

    def test_respects_day_override(self):
        SchoolDayConfiguration.objects.filter(day=Day.SATURDAY).update(periods=6)
        record = TeacherDailyAvailability(teacher=self.teacher, day=Day.SATURDAY, max_periods=7)
        with self.assertRaisesMessage(ValidationError, "only 6 teaching periods"):
            record.full_clean()
        record.max_periods = 6
        record.full_clean()

    def test_rejects_non_operating_day(self):
        record = TeacherDailyAvailability(teacher=self.teacher, day=Day.SUNDAY, max_periods=3)
        with self.assertRaises(ValidationError):
            record.full_clean()

    def test_rejects_invalid_day_at_database_level(self):
        with self.assertRaises(IntegrityError), transaction.atomic():
            TeacherDailyAvailability.objects.create(teacher=self.teacher, day=9, max_periods=3)


class SubjectRequirementTests(TestCase):
    def setUp(self):
        self.class_group = make_class("10", "A")
        self.maths = make_subject("Mathematics")

    def test_one_requirement_per_class_subject(self):
        make_requirement(self.class_group, self.maths, periods=6)
        with self.assertRaises(IntegrityError), transaction.atomic():
            make_requirement(self.class_group, self.maths, periods=4)

    def test_periods_must_be_positive(self):
        with self.assertRaises(IntegrityError), transaction.atomic():
            make_requirement(self.class_group, self.maths, periods=0)

    def test_periods_cannot_exceed_weekly_slots(self):
        requirement = SubjectRequirement(
            class_group=self.class_group, subject=self.maths, periods_per_week=55
        )
        with self.assertRaisesMessage(ValidationError, "only 54 teaching periods"):
            requirement.full_clean()

    def test_designated_teacher_is_optional(self):
        SubjectRequirement(
            class_group=self.class_group, subject=self.maths, periods_per_week=6
        ).full_clean()

    def test_designated_teacher_must_be_qualified(self):
        biology_teacher = make_teacher("Teacher B", subjects=[make_subject("Biology")])
        requirement = SubjectRequirement(
            class_group=self.class_group,
            subject=self.maths,
            periods_per_week=6,
            teacher=biology_teacher,
        )
        with self.assertRaisesMessage(ValidationError, "not qualified"):
            requirement.full_clean()

    def test_qualified_designated_teacher_is_accepted(self):
        teacher = make_teacher(subjects=[self.maths])
        SubjectRequirement(
            class_group=self.class_group, subject=self.maths, periods_per_week=6, teacher=teacher
        ).full_clean()

    def test_designated_teacher_must_be_active(self):
        teacher = make_teacher(subjects=[self.maths], is_active=False)
        requirement = SubjectRequirement(
            class_group=self.class_group, subject=self.maths, periods_per_week=6, teacher=teacher
        )
        with self.assertRaisesMessage(ValidationError, "inactive"):
            requirement.full_clean()

    def test_total_required_periods(self):
        make_requirement(self.class_group, self.maths, periods=6)
        make_requirement(self.class_group, make_subject("Physics"), periods=5)
        self.assertEqual(self.class_group.total_required_periods(), 11)
