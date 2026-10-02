#!/usr/bin/env python3
"""Edit course.json, and write the files students see from it.

    course.py edit [--keep] [--name NAME] [--min N] [--max N] [--timezone TZ]
                   [--class "P1 tuesday 16:00 Lab 114"]... [--no-classes]
                   [--number-hint TEXT]
    course.py render --repo ORG/REPO [--page URL]

`edit` asks for anything not given on the command line (Enter keeps what is
there) and writes course.json; --keep asks nothing. `render` writes, from course.json:

    .github/README.md                     the page students land on
    .github/ISSUE_TEMPLATE/register.yml   the registration form
    .github/ISSUE_TEMPLATE/config.yml     no blank issues, only the form

All three are generated rather than edited by hand: the form's labels have to
match what register.py looks for exactly, and README and form have to agree
with course.json. GitHub shows .github/README.md in place of the README at the
top of the repo, which is bedel's own and stays as it is.
"""
import argparse
import json
import os
import re
import sys
import zoneinfo

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
from ghlib import COURSE_PATH, WEEKDAYS, load_course  # noqa: E402

ROOT = os.path.dirname(COURSE_PATH)
README = os.path.join(ROOT, ".github", "README.md")
FORM = os.path.join(ROOT, ".github", "ISSUE_TEMPLATE", "register.yml")
FORM_CONFIG = os.path.join(ROOT, ".github", "ISSUE_TEMPLATE", "config.yml")

MAX_SLOTS = 6  # register.py parses this many
NUMBERS = ["61234", "61789", "61011", "61213", "61415", "61617"]
USERS = ["octocat", "hubot", "mona", "tonsky", "defunkt", "mojombo"]


# ---------- editing ----------

def parse_class(spec):
    """'P1 tuesday 16:00 Lab 114' -> ("P1", {"day", "ends", "note"})."""
    parts = spec.split(None, 3)
    if len(parts) < 3:
        raise ValueError(f"'{spec}': want NAME DAY END [note], e.g. 'P1 tuesday 16:00'")
    name, day, ends = parts[0].upper(), parts[1].lower(), parts[2]
    days = [d for d in WEEKDAYS if d.startswith(day)] if len(day) >= 2 else []
    if len(days) != 1:
        raise ValueError(f"'{parts[1]}' is not a day of the week")
    if not re.fullmatch(r"([01]?\d|2[0-3]):[0-5]\d", ends):
        raise ValueError(f"'{ends}' is not a time like 16:00")
    entry = {"day": days[0], "ends": ends}
    if len(parts) == 4:
        entry["note"] = parts[3]
    return name, entry


def show_class(name, c):
    return " ".join([name, c["day"], c["ends"]] + ([c["note"]] if c.get("note") else []))


def ask(prompt, current):
    try:
        ans = input(f"{prompt} [{current}] ").strip()
    except EOFError:
        ans = ""
    return ans or current


def ask_classes(current):
    print("\nClasses - the weekly sessions soft deadlines are counted from. One per line:")
    print("  NAME DAY END [note]      e.g.  P1 tuesday 16:00 Lab 114")
    if current:
        print("Now:")
        for k, v in current.items():
            print("  " + show_class(k, v))
        print("Enter keeps them, '-' means no classes, or type the new list (empty line ends it).")
    else:
        print("None yet. Type them, or just press Enter for a course without classes.")
    lines = []
    while True:
        try:
            line = input("  > ").strip()
        except EOFError:
            break
        if not line:
            break
        if line == "-" and not lines:
            return {}
        try:
            parse_class(line)
        except ValueError as e:
            print(f"    {e} - try that line again")
            continue
        lines.append(line)
    return dict(parse_class(l) for l in lines) if lines else current


def edit(args):
    try:
        c = load_course()
    except SystemExit:
        c = {"name": "", "group_size": {"min": 2, "max": 2}, "timezone": "UTC", "classes": {}}
    interactive = sys.stdin.isatty() and not any(
        v not in (None, [], False) for k, v in vars(args).items() if k not in ("cmd", "keep"))
    interactive = interactive and not args.keep

    if args.name is not None:
        c["name"] = args.name
    elif interactive:
        c["name"] = ask("Course name, as students know it", c["name"])
    if not c["name"]:
        sys.exit("The course needs a name (--name).")

    size = c["group_size"]
    if args.min is not None:
        size["min"] = args.min
    if args.max is not None:
        size["max"] = args.max
    if interactive:
        size["min"] = int(ask("Smallest group", size["min"]))
        size["max"] = int(ask("Largest group", max(size["max"], size["min"])))
    if not 1 <= size["min"] <= size["max"] <= MAX_SLOTS:
        sys.exit(f"Group size must be 1 to {MAX_SLOTS}, smallest first "
                 f"(got {size['min']} to {size['max']}).")

    if args.timezone is not None:
        c["timezone"] = args.timezone
    elif interactive:
        c["timezone"] = ask("Time zone deadlines are in", c["timezone"])
    try:
        zoneinfo.ZoneInfo(c["timezone"])
    except (zoneinfo.ZoneInfoNotFoundError, ValueError):
        sys.exit(f"Unknown time zone '{c['timezone']}' - use a name like Europe/Lisbon.")

    if args.no_classes:
        c["classes"] = {}
    elif args.classes:
        try:
            c["classes"] = dict(parse_class(s) for s in args.classes)
        except ValueError as e:
            sys.exit(str(e))
    elif interactive:
        c["classes"] = ask_classes(c["classes"])

    if args.number_hint is not None:
        c["number_hint"] = args.number_hint
    if not c.get("number_hint"):
        c.pop("number_hint", None)

    out = {"name": c["name"], "group_size": size, "timezone": c["timezone"],
           "classes": c["classes"]}
    if c.get("number_hint"):
        out["number_hint"] = c["number_hint"]
    with open(COURSE_PATH, "w", encoding="utf-8") as f:
        json.dump(out, f, indent=2, ensure_ascii=False)
        f.write("\n")
    lo, hi = size["min"], size["max"]
    print(f"course.json: {c['name']}, groups of {lo if lo == hi else f'{lo} to {hi}'}, "
          f"{c['timezone']}, " + (", ".join(c["classes"]) or "no classes"))


# ---------- rendering ----------

def readme(c, repo, page=None):
    hint = c.get("number_hint")
    number = f"{hint}, digits only" if hint else "digits only"
    classes = bool(c["classes"])
    lines = [
        f"# {c['name']} — group registration",
        "",
        "Register your group here. You only do this once, for the whole semester.",
        "",
        f"### [→ Open a registration issue](https://github.com/{repo}/issues/new?template=register.yml)",
        "",
    ]
    if page:
        lines += [
            f"**[The course's page]({page})** shows the assignments, their deadlines and",
            "how many groups have registered. Staff run the course from there too.",
            "",
        ]
    lines += [
        "Fill in **every** member of the group, including yourself:",
        "",
        f"- **student number** — {number}",
        "- **GitHub username** — the account you will use for the course",
    ]
    if classes:
        lines += [
            "- **class** — leave this blank. We fill it in from the class list once you",
            "  submit, and tell you which one you got. Only pick one if that list has you in",
            "  the wrong class: say the one you really go to and we will sort it out.",
        ]
    deadline = ("your soft deadline depends\non your class and is in the issue that "
                "announces the assignment." if classes else
                "both are in the issue\nthat announces the assignment.")
    lines += [
        "",
        "Then every other member replies `/confirm` on that issue. Nothing is created",
        "until all of you have confirmed.",
        "",
        "Once that happens the bot creates your team and one repository per assignment,",
        "and comments with the links. If you were not already in the organization you",
        "will get an invitation by email — accept it or you will not see the repos.",
        "",
        "When an assignment continues from an earlier one, your previous work is already",
        "in the new repository, history and all — just clone it and carry on.",
        "",
        "**Deadlines.** Each assignment has a soft and a hard deadline. Work pushed after",
        f"the soft one still counts, but is recorded as late; {deadline} After the hard",
        "deadline the repository becomes read-only and pushes are refused, so do not",
        "leave it to the last minute.",
        "",
        "Something wrong? Comment on your registration issue and we will look.",
        "",
        "<!-- Generated from course.json by scripts/setup/06_course.sh - edit those, not",
        "     this file. The tooling behind it is documented in scripts/README.md -->",
    ]
    return "\n".join(lines) + "\n"


def form(c):
    hint = c.get("number_hint")
    number = f"{hint} (digits only)" if hint else "digits only"
    classes = c["classes"]
    head = f"""name: Register your group
description: Register your group to get your team and repositories.
title: "Group registration"
labels: ["registration"]
body:
  - type: markdown
    attributes:
      value: |
        Fill in **every** member of your group, **including yourself**.
        - Student number = {number}.
        - GitHub username = the account you will use for the course.
"""
    if classes:
        head += """        - Class = leave it blank. We fill it in from the class list, and only
          need you to pick one if that list has you in the wrong class.
"""
    head += """
        After you submit, the bot checks the details. Every other member must then reply `/confirm` on this issue.
        Your team and repositories are created automatically once everyone has confirmed.
"""
    lo, hi = c["group_size"]["min"], c["group_size"]["max"]
    return head + "".join(slot(i, i <= lo, classes) for i in range(1, hi + 1))


def slot(i, required, classes):
    req = str(required).lower()
    out = f"""  - type: input
    id: m{i}_number
    attributes:
      label: "Member {i} - student number"
      placeholder: "{NUMBERS[(i - 1) % len(NUMBERS)]}"
    validations:
      required: {req}
  - type: input
    id: m{i}_user
    attributes:
      label: "Member {i} - GitHub username"
      placeholder: "{USERS[(i - 1) % len(USERS)]}"
    validations:
      required: {req}
"""
    if classes:
        # A dropdown, so a student cannot invent a class that has no session.
        options = "\n".join(f"        - {t}" for t in sorted(classes))
        out += f"""  - type: dropdown
    id: m{i}_class
    attributes:
      label: "Member {i} - class"
      description: "Leave this blank and we fill it in from the class list. Only pick one if the list has you in the wrong class - say the one you actually go to."
      options:
{options}
    validations:
      required: false
"""
    return out


def write(path, text):
    old = None
    if os.path.exists(path):
        with open(path, encoding="utf-8") as f:
            old = f.read()
    if old == text:
        print(f"{os.path.relpath(path, ROOT)} unchanged")
        return
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        f.write(text)
    print(f"{os.path.relpath(path, ROOT)} {'updated' if old is not None else 'written'}")


def render(args):
    if not re.fullmatch(r"[\w.-]+/[\w.-]+", args.repo or ""):
        sys.exit("render needs --repo ORG/REPO, for the links students click.")
    c = load_course()
    if args.page and not re.fullmatch(r"https://[^\s()]+", args.page):
        sys.exit(f"--page wants the page's https:// address, got '{args.page}'.")
    write(README, readme(c, args.repo, args.page))
    write(FORM, form(c))
    write(FORM_CONFIG, "blank_issues_enabled: false\n")


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    e = sub.add_parser("edit")
    e.add_argument("--keep", action="store_true", help="ask nothing; change only what is given")
    e.add_argument("--name")
    e.add_argument("--min", type=int)
    e.add_argument("--max", type=int)
    e.add_argument("--timezone")
    e.add_argument("--class", dest="classes", action="append", default=[],
                   metavar="'NAME DAY END [note]'")
    e.add_argument("--no-classes", action="store_true")
    e.add_argument("--number-hint", help="what students call their number, e.g. 'your *Nº*'")
    r = sub.add_parser("render")
    r.add_argument("--repo")
    r.add_argument("--page", help="the course's page, when it is published, to link to")
    args = ap.parse_args()
    (edit if args.cmd == "edit" else render)(args)


if __name__ == "__main__":
    main()
