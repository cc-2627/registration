#!/usr/bin/env python3
"""How soft deadlines are decided. This file is meant to be edited.

Everything else - locking, reporting, archiving - calls `soft_deadline()` below
and uses whatever it returns. Change the function and you change the policy; no
other file needs to know.

Hard deadlines are deliberately not here: they are the same for everyone and come
straight from the assignment.
"""
import datetime

from ghlib import class_schedule, load_course

# The weekly timetable and time zone come from course.json (set them with
# scripts/setup/06_course.sh): class -> (weekday, when the session ends), with
# Monday as 0, so Tuesday is 1 and Thursday is 3.
COURSE = load_course()
SCHEDULE = class_schedule(COURSE)
TZ = COURSE["timezone"]

# How long after their session a class gets.
AFTER_SESSION = datetime.timedelta(days=7)


def soft_deadline(ctx):
    """The group's soft deadline, or None to leave it to the assignment.

    `ctx` gives you:
        ctx.assignment      the definition dict, including soft_week
        ctx.group           name, students (numbers), users
        ctx.classes          {student number: class} for this group's members,
                            as claimed at registration, falling back to the
                            official listing
        ctx.session(class)  that class's session in the assignment's soft week
        ctx.override        the hand-set soft deadline, if there is one
        ctx.note(text)      leave a note on this group in the report

    The default: a group gets a week from its *earliest* member's session, so a
    mixed-class group is held to the earlier of the two.
    """
    if ctx.override:
        return ctx.override
    if not SCHEDULE:
        # A course without classes: every group gets the assignment's own date.
        return None

    sessions = [s for s in (ctx.session(t) for t in set(ctx.classes.values())) if s]
    if not sessions:
        # Nobody's class is known. Say so rather than inventing a date; the
        # assignment's own soft deadline applies.
        ctx.note("no class known")
        return None

    if len(set(ctx.classes.values())) > 1:
        ctx.note("mixed classes " + ",".join(sorted(set(ctx.classes.values()))))
    return min(sessions) + AFTER_SESSION
