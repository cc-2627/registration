#!/usr/bin/env python3
"""Process open group-registration issues. Runs inside GitHub Actions (see README).

For each open issue labelled `registration`, oldest first:
  1. parse the form and validate it against the roster and existing groups;
     on error: comment what's wrong and close the issue;
  2. wait until every listed member has replied /confirm (the author counts as confirmed);
  3. create team gXX_<n1>_<n2>, add members (this also sends org invites),
     create a repo for every assignment defined in assignments/, give the team
     write access, comment, close.

Steps are idempotent: if creation fails halfway, the next run resumes it.
"""
import os
import re
import sys

import rules
from ghlib import (GitHub, GitHubError, default_org, ensure_repo, grant_team,
                   load_assignments, load_groups, load_classes, now, push_own_work,
                   record_claims, repo_name, sort_students, to_dt)

ORG = default_org()
REPO = os.environ["REPO"]
MIN_SIZE = rules.COURSE["group_size"]["min"]
MAX_SIZE = rules.COURSE["group_size"]["max"]
ROSTER = set(re.findall(r"\d+", os.environ.get("ROSTER", "")))
CLASSES = load_classes()          # {number: class} from the faculty listings
VALID_CLASSES = {t.upper() for t in rules.SCHEDULE}
MAX_SLOTS = 6  # form slots the parser looks for

# How each member's class was arrived at, said plainly enough that someone who
# ended up in the wrong one notices and says so.
WHERE_FROM = {
    "from list": " — from the class list",
    "ok": " — as you said",
    "MISMATCH": " — as you said, though the list has you elsewhere",
    "not in any listing": " — as you said; you are not on any class list",
    "no class known": " — we could not find you on any class list, so tell us",
}

CONFIRM_MARK = "<!-- registration-bot:confirm-request -->"
RETRY_MARK = "<!-- registration-bot:retry -->"

admin = GitHub(os.environ.get("ADMIN_TOKEN"))   # org-level actions
bot = GitHub(os.environ.get("ISSUES_TOKEN"))    # comments/closing on this repo
ME = admin.get("/user")["login"]                 # token owner, excluded from team checks
failed = False


# ---------- form parsing ----------

def parse_form(body):
    """Issue forms render as '### Label' followed by the value."""
    fields, current = {}, None
    for line in body.splitlines():
        if line.startswith("### "):
            current = line[4:].strip()
            fields[current] = []
        elif current is not None:
            fields[current].append(line)
    return {k: "\n".join(v).strip() for k, v in fields.items()}


def clean(value):
    value = (value or "").strip()
    if value == "_No response_":
        return ""
    return value.lstrip("@").strip()


def parse_members(body):
    fields = parse_form(body or "")
    members, classes, errors = [], {}, []
    for i in range(1, MAX_SLOTS + 1):
        num = clean(fields.get(f"Member {i} - student number"))
        user = clean(fields.get(f"Member {i} - GitHub username"))
        class_ = clean(fields.get(f"Member {i} - class")).upper().replace(" ", "")
        # An issue form writes "None" for a dropdown nobody picked from: that's blank.
        if class_ == "NONE":
            class_ = ""
        if not num and not user:
            continue
        if not num or not user:
            errors.append(f"Member {i}: fill in both the student number and the GitHub username.")
            continue
        if class_ and class_ not in VALID_CLASSES:
            errors.append(f"Member {i}: `{class_}` is not one of "
                          f"{', '.join(sorted(VALID_CLASSES))}.")
            continue
        members.append((num, user))
        if class_:
            classes[num] = class_
    return members, classes, errors


# ---------- validation ----------

def validate(members, author, groups):
    errors = []
    if not MIN_SIZE <= len(members) <= MAX_SIZE:
        size = str(MIN_SIZE) if MIN_SIZE == MAX_SIZE else f"{MIN_SIZE} to {MAX_SIZE}"
        errors.append(f"Groups must have {size} members; you listed {len(members)}.")

    nums = [n for n, _ in members]
    users = [u.lower() for _, u in members]
    if len(set(nums)) != len(nums):
        errors.append("The same student number appears more than once.")
    if len(set(users)) != len(users):
        errors.append("The same GitHub username appears more than once.")

    for n in nums:
        if not n.isdigit():
            errors.append(f"`{n}` is not a valid student number (digits only).")
        elif n not in ROSTER:
            errors.append(f"Student number `{n}` is not enrolled in this course. Check for typos.")

    canonical = []
    for num, u in members:
        try:
            canonical.append((num, admin.get(f"/users/{u}")["login"]))
        except GitHubError as e:
            if e.status != 404:
                raise
            errors.append(f"GitHub user `{u}` does not exist. Check the spelling.")

    if author.lower() not in users:
        errors.append("You (the author of this issue) must be one of the listed members.")

    mine = sort_students(nums)
    for g in groups:
        if g["students"] == mine:
            continue  # same group, registration being resumed
        for n in nums:
            if n in g["students"]:
                errors.append(f"Student `{n}` is already registered in group `{g['name']}`.")
        for u in users:
            if u in g["users"]:
                errors.append(f"GitHub user `{u}` is already in group `{g['name']}`.")
    return canonical, errors


# ---------- issue helpers ----------

def comments(n):
    return bot.paginate(f"/repos/{REPO}/issues/{n}/comments")


def comment(n, text):
    bot.post(f"/repos/{REPO}/issues/{n}/comments", {"body": text})


def close(n, reason, title=None):
    """Close the issue, optionally retitling it in the same call.

    Students can edit the title - GitHub issue forms only pre-fill it - so the
    closed list is whatever people typed. Stamping the group name on the way out
    makes the archive searchable by group.
    """
    data = {"state": "closed", "state_reason": reason}
    if title:
        data["title"] = title
    bot.patch(f"/repos/{REPO}/issues/{n}", data)


def add_label(n, name):
    bot.post(f"/repos/{REPO}/issues/{n}/labels", {"labels": [name]})


# ---------- creation ----------

def claim_rows(members, classes, group_name, issue):
    """One row per member: what they said, what the listing says, which one we
    went with, and whether anyone needs to look at it.

    Saying nothing is the normal path. A student writes their number and the
    class is filled in from the listing - an issue form is static YAML and
    cannot do that while they type, so the bot does it a minute later.

    Stating one that disagrees is the case this has to cope with: someone who
    could not get onto their real class on the faculty platform. The claim wins
    and the disagreement is recorded, never silently resolved."""
    stamp = now().isoformat(timespec="seconds")
    rows = []
    for num, _ in members:
        claimed = classes.get(num, "")
        official = CLASSES.get(num, "")
        if claimed and official and claimed == official:
            status = "ok"
        elif claimed and official:
            status = "MISMATCH"
        elif claimed:
            status = "not in any listing"
        elif official:
            status = "from list"
        else:
            status = "no class known"
        rows.append({"student": num, "claimed": claimed, "official": official,
                     "used": claimed or official, "status": status,
                     "group": group_name, "issue": str(issue),
                     "recorded": stamp})
    return rows


def create_group(n, members, groups, classes=None):
    students = sort_students(s for s, _ in members)
    group = next((g for g in groups if g["students"] == students), None)

    if group is None:
        number = max((g["number"] for g in groups), default=0) + 1
        name = f"g{number:02d}_" + "_".join(students)
        team = admin.post(f"/orgs/{ORG}/teams", {
            "name": name,
            "privacy": "secret",
            "description": f"Registered via {REPO}#{n}",
        })
        group = {"name": name, "slug": team["slug"], "number": number,
                 "students": students, "users": set()}
        groups.append(group)
        try:  # the token owner is auto-added as maintainer; owners see everything anyway
            admin.delete(f"/orgs/{ORG}/teams/{team['slug']}/memberships/{ME}")
        except GitHubError:
            pass

    for _, user in members:
        # Adding a non-member to a team sends them an org invitation.
        admin.put(f"/orgs/{ORG}/teams/{group['slug']}/memberships/{user}", {"role": "member"})
        group["users"].add(user.lower())

    links, own = [], []
    for a in load_assignments(admin, REPO):
        repo = repo_name(group["name"], a["name"])
        ensure_repo(admin, ORG, repo, a.get("template"), empty=a.get("own_work", False))
        if a.get("own_work"):
            own.append(repo)
        # A group registering after an assignment closed gets it read-only.
        hard = to_dt(a.get("hard_deadline"))
        closed = bool(hard and now() >= hard)
        grant_team(admin, ORG, group["slug"], repo, permission="pull" if closed else "push")
        links.append(f"- [{repo}](https://github.com/{ORG}/{repo})"
                     + (" — closed, read-only" if closed else ""))

    # Class claims: recorded before the comment goes out, so what the student
    # is told and what we will look at later are the same thing.
    rows, flagged = [], []
    try:
        rows = claim_rows(members, classes or {}, group["name"], n)
        flagged = record_claims(admin, ORG, REPO.split("/")[-1], rows)
    except GitHubError as e:
        print(f"#{n}: could not record class claims: {e}", file=sys.stderr)
    if flagged:
        add_label(n, "class-mismatch")
    # Only a class someone actually chose can disagree with the listing. A blank
    # one for a student on no list is already explained in the lines above.
    disputed = [r for r in flagged if r["claimed"]]

    body = (
        f"🎉 Your group is registered as **`{group['name']}`**.\n\n"
        + ("Your repositories:\n" + "\n".join(links) + "\n\n" if links else "")
        + "".join(f"**{r}** starts empty, so you can bring in the work you've already done, "
                  f"history and all. In a clone of the repository you've been working in, run:\n\n"
                  + push_own_work(ORG, r) + "\n" for r in own)
        + ("Your class, which your soft deadlines follow:\n"
           + "".join(f"- `{r['student']}` → **{r['used'] or '—'}**{WHERE_FROM.get(r['status'], '')}\n"
                     for r in rows) + "\n" if rows else "")
        + ("".join(f"- ⚠️ `{r['student']}` said **{r['claimed'] or '-'}**, "
                   f"the listing says **{r['official'] or 'no class'}** — "
                   f"we will check this; your deadline follows what you said.\n"
                   for r in disputed) + "\n" if disputed else "")
        + "If you were not yet in the organization, you have been sent an invitation by email. "
        + f"Accept it at https://github.com/orgs/{ORG}/invitation to see your repositories."
    )
    comment(n, body)
    add_label(n, "registered")
    close(n, "completed", title=f"Group registration — {group['name']}")
    print(f"#{n}: created {group['name']}")


def process(issue, groups):
    global failed
    n = issue["number"]
    author = issue["user"]["login"]

    members, classes, errors = parse_members(issue["body"])
    canonical = []
    if not errors:
        canonical, errors = validate(members, author, groups)
    if errors:
        comment(n, "❌ **This registration could not be accepted:**\n\n"
                + "\n".join(f"- {e}" for e in errors)
                + "\n\nPlease open a **new** registration issue with the corrected details.")
        close(n, "not_planned")
        print(f"#{n}: rejected ({len(errors)} errors)")
        return

    cs = comments(n)
    confirmed = {author.lower()} | {
        c["user"]["login"].lower() for c in cs
        if c["body"].strip().lower().startswith("/confirm")
    }
    missing = [u for _, u in canonical if u.lower() not in confirmed]
    if missing:
        if not any(CONFIRM_MARK in c["body"] for c in cs):
            comment(n, CONFIRM_MARK + "\n✅ The details look correct. To finish, each of these "
                    "members must reply to this issue with `/confirm`:\n\n"
                    + "\n".join(f"- @{u}" for u in missing)
                    + "\n\nNothing is created until everyone has confirmed.")
        print(f"#{n}: waiting for {', '.join(missing)}")
        return

    try:
        create_group(n, canonical, groups, classes)
    except GitHubError as e:
        failed = True
        print(f"#{n}: creation failed: {e}", file=sys.stderr)
        if not any(RETRY_MARK in c["body"] for c in cs):
            comment(n, RETRY_MARK + "\n⚠️ Something went wrong while creating your team or "
                    "repositories. It will be retried automatically; no action needed. "
                    "If this is still here in a few hours, contact the teaching team.")


def main():
    if not ROSTER:
        sys.exit("ROSTER secret is empty or missing")
    issues = [
        i for i in bot.paginate(
            f"/repos/{REPO}/issues?state=open&labels=registration&sort=created&direction=asc")
        if "pull_request" not in i
    ]
    if not issues:
        print("No open registrations.")
        return
    groups = load_groups(admin, ORG, ignore_users=[ME])
    for issue in issues:
        process(issue, groups)
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    main()
