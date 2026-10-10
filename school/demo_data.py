"""
The demo school used by `manage.py seed_demo`.

Everything here is a plain manifest. The command creates exactly these
records (matched by their natural keys), so running it twice changes nothing
and `--reset` can find and remove precisely what it created.

Feasibility is by construction: designated teachers are chosen greedily
against each teacher's weekly capacity, keeping some headroom, and the data
health checks are run at the end of every seed.
"""

from .constants import Day

GRADES = [("9", 9), ("10", 10), ("11", 11), ("12", 12)]

CLASSES = [
    ("9", "A"), ("9", "B"), ("9", "C"),
    ("10", "A"), ("10", "B"), ("10", "C"),
    ("11", "A"), ("11", "B"),
    ("12", "A"), ("12", "B"),
]  # fmt: skip

SUBJECTS = [
    ("Mathematics", "MATH"),
    ("Physics", "PHY"),
    ("Chemistry", "CHEM"),
    ("Biology", "BIO"),
    ("English", "ENG"),
    ("Urdu", "URD"),
    ("History", "HIST"),
    ("Geography", "GEO"),
    ("Civics", "CIV"),
    ("Computer Science", "CS"),
    ("Physical Education", "PE"),
    ("Music", "MUS"),
]

SATURDAY_PERIODS = 6

# Weekly periods per subject code. Grades 9–10 total 49, grades 11–12 total 48,
# both inside the 51 weekly slots (5 × 9 + 6 on Saturday).
CURRICULUM = {
    "9": {
        "MATH": 7, "PHY": 5, "CHEM": 5, "BIO": 5, "ENG": 6, "URD": 5,
        "HIST": 3, "GEO": 3, "CIV": 2, "CS": 3, "PE": 3, "MUS": 2,
    },
    "11": {
        "MATH": 8, "PHY": 6, "CHEM": 6, "BIO": 5, "ENG": 6, "URD": 5,
        "HIST": 2, "GEO": 2, "CIV": 2, "CS": 4, "PE": 2,
    },
}  # fmt: skip
CURRICULUM["10"] = CURRICULUM["9"]
CURRICULUM["12"] = CURRICULUM["11"]

# Daily capacity profiles: {day: max lessons}. A missing day means unavailable.
WEEKDAYS = [Day.MONDAY, Day.TUESDAY, Day.WEDNESDAY, Day.THURSDAY, Day.FRIDAY]
PROFILES = {
    "full": {**{d: 6 for d in WEEKDAYS}, Day.SATURDAY: 4},  # 34 a week
    "standard": {**{d: 5 for d in WEEKDAYS}, Day.SATURDAY: 3},  # 28
    "light": {**{d: 4 for d in WEEKDAYS}, Day.SATURDAY: 2},  # 22
    # Works Monday to Thursday only.
    "part_time": {Day.MONDAY: 5, Day.TUESDAY: 5, Day.WEDNESDAY: 5, Day.THURSDAY: 4},  # 19
    "cover": {**{d: 3 for d in WEEKDAYS}, Day.SATURDAY: 2},  # 17
}

# (name, subject codes — the first is the main subject, capacity profile, cover teacher)
TEACHERS = [
    ("Ayesha Khan", ["MATH", "PHY"], "full", False),
    ("Bilal Ahmed", ["MATH", "CS"], "standard", False),
    ("Sana Malik", ["MATH"], "full", False),
    ("Usman Raza", ["MATH", "PHY"], "standard", False),
    ("Fatima Siddiqui", ["PHY", "CHEM"], "full", False),
    ("Hamza Qureshi", ["PHY", "MATH"], "standard", False),
    ("Zainab Hussain", ["CHEM", "BIO"], "full", False),
    ("Imran Sheikh", ["CHEM"], "standard", False),
    ("Nadia Iqbal", ["CHEM", "PHY"], "light", False),
    ("Omar Farooq", ["BIO", "CHEM"], "full", False),
    ("Hira Javed", ["BIO"], "standard", False),
    ("Sarah Thompson", ["ENG"], "full", False),
    ("James Wilson", ["ENG", "HIST"], "standard", False),
    ("Mariam Aslam", ["ENG", "URD"], "standard", False),
    ("Kamran Butt", ["URD", "HIST"], "full", False),
    ("Rabia Noor", ["URD", "CIV"], "standard", False),
    ("Tariq Mehmood", ["HIST", "GEO", "CIV"], "standard", False),
    ("Saima Akhtar", ["GEO", "CIV"], "light", False),
    ("Asad Chaudhry", ["HIST", "GEO"], "part_time", False),
    ("Ali Hassan", ["CS", "MATH"], "standard", False),
    ("Mehwish Tariq", ["CS"], "part_time", False),
    ("Daniyal Shah", ["PE"], "light", False),
    ("Kiran Abbas", ["PE", "CIV"], "light", False),
    ("Laila Rehman", ["MUS", "ENG"], "light", False),
    ("Faisal Karim", ["MATH", "PHY", "CHEM"], "cover", True),
    ("Amna Yousaf", ["ENG", "URD", "HIST"], "cover", True),
    ("Waqar Ali", ["BIO", "GEO", "PE"], "cover", True),
]

# Designations use at most this share of a teacher's weekly capacity, leaving
# room for the generator to place undesignated requirements.
DESIGNATION_HEADROOM = 0.85

# Requirements left without a designated teacher so the generator (or the
# person building by hand) chooses one: (class label, subject code).
UNDESIGNATED = {
    ("9C", "PE"), ("10C", "MUS"), ("10B", "GEO"), ("11B", "CIV"),
    ("12A", "CS"), ("12B", "HIST"), ("9B", "CIV"), ("11A", "PE"),
}  # fmt: skip


def class_label(grade, section):
    return f"{grade}{section}"
