#!/usr/bin/env python3
"""Enforce hard deadlines and report who met which deadline.

In GitHub Actions (see .github/workflows/deadlines.yml) it runs with no
arguments: every assignment whose hard deadline has passed has its group
repositories switched to read-only. The registration repo is public, and so are
its Actions logs and job summaries, so the job summary gets counts only; the
full table, with every group's name and verdict, goes to the private push-log
repo as reports/<assignment>.md.

Locally:
    GH_TOKEN=$(gh auth token) ORG=myorg python scripts/deadlines.py --report
    GH_TOKEN=$(gh auth token) ORG=myorg python scripts/deadlines.py --report a3 --csv
    GH_TOKEN=$(gh auth token) ORG=myorg python scripts/deadlines.py --unlock a3

Lateness is measured on the graded commit - the head of the default branch -
by when GitHub first received it (arrival.py): a server time, so unlike a commit
date a student cannot backdate it. The repository's `pushed_at` is not enough on
its own, because GitHub moves it for *any* ref update: a group that creates a
`main` branch the day after the deadline, pointing at work pushed on time, would
look late by it. Where no server record of the head survives, the group is
reported as `unverified` for a person to check, never as late.

Enforcement runs on a schedule, so a repo may stay writable for a while after
the hard deadline - but anything pushed in that window still shows up as late,
because the measurement uses the real push time, not the time we locked it.
"""
import argparse
import csv
import datetime
import os
import re
import sys
import time

import rules
from arrival import arrivals, describe, head_of, verdict
from ghlib import (GitHub, GitHubError, commit_files, default_org, get_file,
                   grant_team, iso_z, load_assignments, load_claims, load_groups, load_classes, now,
                   repo_name, resolve_soft, team_permission, to_dt)

ORG = default_org()
REPO = os.environ.get("REPO")   # "owner/name"; set by the workflow
LOG_REPO = os.environ.get("LOG_REPO", "push-log")
# In Actions everything printed is public (see the docstring): no group names.
IN_ACTIONS = os.environ.get("GITHUB_ACTIONS") == "true"
# Minutes of slack after every deadline before work counts as late. It lives in a
# secret, not in assignments/, because the registration repo is public: students
# see their deadlines as set and never learn there is any slack. It changes the
# verdicts only; repositories still lock on the dot.
#
# Write it with its unit - "5 minutes", not "5": Actions masks a secret's value
# wherever it appears in a log, and a bare digit would be starred out of every
# date and group number the run prints.
_grace = re.match(r"\s*(\d+(?:\.\d+)?)", os.environ.get("GRACE_MINUTES") or "")
GRACE = datetime.timedelta(minutes=float(_grace.group(1)) if _grace else 0)
OPEN_PERMISSION = "push"
LOCKED_PERMISSION = "pull"
LATE_CONTEXT = "deadline/soft"
BOUNDARY_TAG = "refs/tags/soft-deadline"


def mark_late_commits(gh, org, repo, soft, branch, first):
    """Flag every commit that reached GitHub after the soft deadline, and tag the
    last one that made it in on time.

    A commit message cannot be changed without rewriting history and force-pushing
    into the student's repo, which would break their clones. A commit status is the
    non-destructive equivalent: GitHub shows it beside the commit in the web UI.

    The tag makes the boundary usable from the command line:
        git fetch --tags && git log soft-deadline..HEAD

    "After" means the commit's first arrival (`first`, from arrival.arrivals), not
    its commit date, which the student's machine sets: a commit made on time and
    pushed late is late, and one dated late but pushed on time is not. A commit
    marked late by an earlier version that measured commit dates, and which in
    fact arrived on time, gets a newer status that says so. Commits with no
    server record are left alone and counted. This marks up the history, it does
    not decide who was late.

    Returns (marked, unknown).
    """
    if not soft or not branch:
        return 0, 0
    try:
        history = gh.paginate(f"/repos/{org}/{repo}/commits?sha={branch}")
    except GitHubError as e:
        if e.status == 409:   # still empty: a group that never pushed its work in
            return 0, 0
        raise
    marked = unknown = 0
    boundary = None
    for c in history:   # newest first
        row = first.get(c["sha"])
        when = to_dt(row["pushed_at"]) if row else None
        if when is None:
            unknown += 1
            continue
        if when <= soft:
            boundary = boundary or c["sha"]
        try:
            current = gh.get(f"/repos/{org}/{repo}/commits/{c['sha']}/status")
            # Statuses come newest first; the first one in our context is in force.
            ours = next((st for st in (current or {}).get("statuses", [])
                         if st.get("context") == LATE_CONTEXT), None)
            if when > soft and not (ours and ours.get("state") == "failure"):
                state, text = "failure", "Reached GitHub after the soft deadline - counts as late"
            elif when <= soft and ours and ours.get("state") == "failure":
                state, text = "success", "Reached GitHub before the soft deadline"
            else:
                continue
            gh.post(f"/repos/{org}/{repo}/statuses/{c['sha']}", {
                "state": state, "context": LATE_CONTEXT,
                "description": f"{text} ({row['pushed_at']})"[:140],
            })
            marked += state == "failure"
        except GitHubError as e:
            print(f"  could not mark {c['sha'][:7]} in {repo}: {e}", file=sys.stderr)

    try:
        if boundary and not gh.exists(f"/repos/{org}/{repo}/git/{BOUNDARY_TAG}"):
            gh.post(f"/repos/{org}/{repo}/git/refs", {"ref": BOUNDARY_TAG, "sha": boundary})
    except GitHubError:
        pass
    return marked, unknown


def add_note(existing, text):
    return f"{existing}; {text}" if existing else text


def marked_note(existing, marked, unknown):
    if marked:
        existing = add_note(existing, f"{marked} late commit(s) marked")
    if unknown:
        existing = add_note(existing, f"{unknown} commit(s) with no arrival time, not marked")
    return existing


def status(gh, org, group, assignment, soft=None, notes=(), class_="",
           lock=False, unlock=False, mark=False):
    """Everything we know about one group's repo for one assignment.

    `soft` is resolved per group by rules.py, so two groups on the same
    assignment can legitimately have different ones. The hard deadline is the
    assignment's and is the same for everybody.
    """
    name = assignment["name"]
    repo = repo_name(group["name"], name)
    hard = to_dt(assignment.get("hard_deadline"))
    # Lateness is judged against the deadlines plus the grace; locking uses `hard`.
    soft_by = soft + GRACE if soft else None
    hard_by = hard + GRACE if hard else None
    row = {
        "group": group["name"], "repo": repo, "pushed_at": None, "class": class_,
        "head": "", "received": "", "via": "",
        "soft": "-", "hard": "-", "state": "", "note": "; ".join(notes),
    }

    try:
        info = gh.get(f"/repos/{org}/{repo}")
    except GitHubError as e:
        if e.status != 404:
            raise
        row["state"] = "missing"
        row["note"] = "repo does not exist - run new_assignment.py again"
        return row

    pushed = to_dt(info.get("pushed_at"))
    row["pushed_at"] = pushed.isoformat() if pushed else ""
    branch = info.get("default_branch")
    head = head_of(gh, org, repo, branch) if branch else None
    first = arrivals(gh, org, repo, name, group["name"], branch, LOG_REPO) if head else {}
    got = first.get(head) if head else None
    received = to_dt(got["pushed_at"]) if got else None
    if head:
        row["head"] = head[:7]
        row["received"] = received.isoformat() if received else ""
        row["via"] = describe(got) if got else ""
        row["soft"] = verdict(soft_by, received, pushed)
        row["hard"] = verdict(hard_by, received, pushed)
        if received and pushed and pushed - received > datetime.timedelta(minutes=1) \
                and (row["soft"] == "on time" and soft_by and pushed > soft_by
                     or row["hard"] == "on time" and hard_by and pushed > hard_by):
            # A new branch, a re-push, or late work force-pushed away again: the
            # archive says which. What is graded is still the on-time commit.
            row["note"] = add_note(row["note"], f"pushed again at {row['pushed_at']}, but {branch} "
                                   "still holds the commit received on time - see the push archive")
        if "unverified" in (row["soft"], row["hard"]):
            row["note"] = add_note(row["note"], f"no server record of when {head[:7]} "
                                   "arrived - check the push archive by hand")

    if branch and not head:
        row["note"] = add_note(row["note"], "empty: nothing pushed to grade")

    permission = team_permission(gh, org, repo, group["slug"])
    row["state"] = {"push": "writable", "pull": "read-only"}.get(permission, permission or "no access")

    if unlock and permission == LOCKED_PERMISSION:
        grant_team(gh, org, group["slug"], repo, permission=OPEN_PERMISSION)
        row["state"] = "writable"
        row["note"] = add_note(row["note"], "unlocked")
    elif lock and permission == LOCKED_PERMISSION and not (hard and now() >= hard):
        # Locked, but the hard deadline has since moved later (or gone): the lock
        # no longer has a deadline behind it, so the repository opens again.
        grant_team(gh, org, group["slug"], repo, permission=OPEN_PERMISSION)
        row["state"] = "writable"
        row["note"] = add_note(row["note"], "reopened: the hard deadline is later now")
    elif lock and hard and now() >= hard and permission == OPEN_PERMISSION:
        grant_team(gh, org, group["slug"], repo, permission=LOCKED_PERMISSION)
        row["state"] = "read-only"
        row["note"] = add_note(row["note"], "locked now")
        # The history is final the moment it is locked, so mark it once, here.
        row["note"] = marked_note(row["note"], *mark_late_commits(gh, org, repo, soft_by, branch, first))
    elif mark and soft and head:
        # Every group, not just the late ones: an on-time group may carry marks an
        # earlier version set from commit dates, and this is what corrects them.
        row["note"] = marked_note(row["note"], *mark_late_commits(gh, org, repo, soft_by, branch, first))
    return row


def wait_for_deadline(assignments, minutes):
    """Sleep until the soonest hard deadline inside the window, so the lock lands on
    the dot rather than whenever the next scheduled run happens to fire.

    GitHub's shortest cron interval is 5 minutes and scheduled runs are often delayed,
    so waiting inside a run that already started is the only way to be punctual. If two
    hard deadlines fall in the same window, the second is picked up by the next run.
    """
    limit = now() + datetime.timedelta(minutes=minutes)
    due = [d for d in (to_dt(a.get("hard_deadline")) for a in assignments)
           if d and now() < d <= limit]
    if not due:
        return
    target = min(due)
    delay = (target - now()).total_seconds() + 5   # a little past, never a little short
    if delay <= 0:
        return
    print(f"waiting {delay / 60:.1f} min, until {target.isoformat()}, to lock on time")
    sys.stdout.flush()
    time.sleep(delay)


def counts(rows):
    late_soft = sum(1 for r in rows if r["soft"] == "late")
    late_hard = sum(1 for r in rows if r["hard"] == "late")
    unverified = sum(1 for r in rows if "unverified" in (r["soft"], r["hard"]))
    return (f"{len(rows)} group(s) · {late_soft} past the soft deadline · "
            f"{late_hard} past the hard deadline"
            + (f" · {unverified} to check by hand" if unverified else ""))


def publish_reports(gh, tables):
    """Write each assignment's full table to the private push-log repo, in one
    commit, and only what changed: the workflow runs every half hour."""
    changed = {}
    for name, text in tables.items():
        path, data = f"reports/{name}.md", (text + "\n").encode()
        if get_file(gh, ORG, LOG_REPO, path) != data:
            changed[path] = data
    if changed:
        commit_files(gh, ORG, LOG_REPO, changed,
                     f"Update deadline report for {', '.join(sorted(tables))}")
    return f"https://github.com/{ORG}/{LOG_REPO}/tree/HEAD/reports"


def table(assignment, rows):
    by_class = assignment.get("soft_by_class") or {}
    soft = (", ".join(f"{t} {w}" for t, w in sorted(by_class.items()))
            if by_class else (assignment.get("soft_deadline") or "-"))
    hard = assignment.get("hard_deadline") or "-"
    out = [f"### {assignment['name']}", "",
           f"soft deadline `{soft}` · hard deadline `{hard}`", "",
           "| group | class | graded commit | received | soft | hard | repo | |",
           "|---|---|---|---|---|---|---|---|"]
    def cell(value):
        return {"on time": "✅ on time", "late": "⚠️ late", "unverified": "❓ check",
                "-": "–"}.get(value, value)

    for r in rows:
        got = (f"{r['received']} ({r['via']})" if r["received"]
               else "–" if not r["head"] else f"? (last push {r['pushed_at'] or '–'})")
        out.append(
            f"| {r['group']} | {r.get('class') or '–'} | {'`' + r['head'] + '`' if r['head'] else '–'} | "
            f"{got} | "
            f"{cell(r['soft'])} | "
            f"{cell(r['hard'])} | `{r['repo']}` | "
            f"{r['state']}{' · ' + r['note'] if r['note'] else ''} |")
    out += ["", counts(rows), "", "*received* is when GitHub first got the graded commit, the head of the "
                "default branch: a new branch or a re-push of the same work doesn't move it."]
    return "\n".join(out)


def main():
    ap = argparse.ArgumentParser(description="Enforce and report assignment deadlines.")
    ap.add_argument("--report", nargs="?", const="*", metavar="ASSIGNMENT",
                    help="report only, change nothing (optionally one assignment)")
    ap.add_argument("--unlock", metavar="ASSIGNMENT",
                    help="give write access back (e.g. to grant an extension)")
    ap.add_argument("--csv", action="store_true", help="write CSV to stdout instead of a table")
    ap.add_argument("--repo", default="registration",
                    help="the registration repo holding assignments/ (default: %(default)s)")
    ap.add_argument("--local", action="store_true",
                    help="read assignments/ from this checkout instead of the repo")
    ap.add_argument("--mark-late", action="store_true",
                    help="flag commits that reached GitHub after the soft deadline, without locking")
    ap.add_argument("--wait", type=int, default=0, metavar="MINUTES",
                    help="if a hard deadline falls within this many minutes, wait for it "
                         "and lock on the dot instead of leaving it to the next run")
    args = ap.parse_args()

    if not ORG:
        sys.exit("Set ORG (the organization name).")
    gh = GitHub(os.environ.get("ADMIN_TOKEN") or os.environ.get("GH_TOKEN"))

    wanted = args.unlock or (None if args.report in (None, "*") else args.report)
    # From the repo, not the checkout: a stale `git pull` otherwise makes an
    # assignment silently invisible here.
    source = None if args.local else (REPO or f"{ORG}/{args.repo}")
    assignments = [a for a in load_assignments(gh, source) if wanted in (None, a["name"])]
    if not assignments:
        where = "this checkout" if args.local else (source or "the repo")
        print(f"No assignments defined yet (nothing in assignments/ of {where})."
              if not wanted else f"No assignment called '{wanted}' in {where}.")
        return

    groups = load_groups(gh, ORG, ignore_users=[gh.get("/user")["login"]])
    if not groups:
        print("No groups registered yet.")
        return

    enforcing = not args.report and not args.unlock and not args.mark_late
    if enforcing and args.wait:
        wait_for_deadline(assignments, args.wait)

    classes = load_classes()
    claims = load_claims(gh, source) if source else {}

    sections, all_rows, public = [], [], []
    for a in assignments:
        rows = []
        for g in groups:
            soft, notes = resolve_soft(rules, a, g, classes, claims)
            mine = sorted({(claims.get(n) or classes.get(n) or "?")
                           for n in g["students"]})
            rows.append(status(gh, ORG, g, a, soft=soft, notes=notes,
                               class_=",".join(mine), lock=enforcing,
                               unlock=bool(args.unlock), mark=args.mark_late))
        for r in rows:
            r["assignment"] = a["name"]
        all_rows += rows
        sections.append(table(a, rows))
        public.append(f"### {a['name']}\n\n{counts(rows)}")
        if IN_ACTIONS:
            continue
        for r in rows:
            # With --csv, stdout belongs to the CSV alone, or the file it is
            # redirected into opens as neither one thing nor the other.
            print(f"{a['name']} {r['group']:<24} soft={r['soft']:<8} hard={r['hard']:<8} "
                  f"{r['state']}{' (' + r['note'] + ')' if r['note'] else ''}",
                  file=sys.stderr if args.csv else sys.stdout)

    if args.csv:
        w = csv.DictWriter(sys.stdout, fieldnames=[
            "assignment", "group", "class", "repo", "head", "received", "via",
            "pushed_at", "soft", "hard", "state", "note"])
        w.writeheader()
        w.writerows(all_rows)

    summary = os.environ.get("GITHUB_STEP_SUMMARY")
    if not IN_ACTIONS:
        if summary:
            with open(summary, "a", encoding="utf-8") as f:
                f.write("\n\n".join(sections) + "\n")
        return
    where = publish_reports(gh, {a["name"]: t for a, t in zip(assignments, sections)})
    print("\n\n".join(public))
    if summary:
        with open(summary, "a", encoding="utf-8") as f:
            f.write("\n\n".join(public)
                    + f"\n\nThe full table, with each group, is in the private [push-log reports]({where}).\n")


if __name__ == "__main__":
    main()
