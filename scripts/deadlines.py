#!/usr/bin/env python3
"""Enforce hard deadlines and report who met which deadline.

In GitHub Actions (see .github/workflows/deadlines.yml) it runs with no
arguments: every assignment whose hard deadline has passed has its group
repositories switched to read-only, and a table goes into the job summary.

Locally:
    GH_TOKEN=$(gh auth token) ORG=myorg python scripts/deadlines.py --report
    GH_TOKEN=$(gh auth token) ORG=myorg python scripts/deadlines.py --report a3 --csv
    GH_TOKEN=$(gh auth token) ORG=myorg python scripts/deadlines.py --unlock a3

Lateness is measured with the repository's `pushed_at`, which GitHub sets
server-side on every push. Unlike a commit date, a student cannot backdate it.
Enforcement runs on a schedule, so a repo may stay writable for a while after
the hard deadline - but anything pushed in that window still shows up as late,
because the measurement uses the real push time, not the time we locked it.
"""
import argparse
import csv
import datetime
import os
import sys
import time

import rules
from ghlib import (GitHub, GitHubError, default_org, grant_team, iso_z,
                   load_assignments, load_claims, load_groups, load_classes, now,
                   repo_name, resolve_soft, team_permission, to_dt)

ORG = default_org()
REPO = os.environ.get("REPO")   # "owner/name"; set by the workflow
OPEN_PERMISSION = "push"
LOCKED_PERMISSION = "pull"
LATE_CONTEXT = "deadline/soft"
BOUNDARY_TAG = "refs/tags/soft-deadline"


def mark_late_commits(gh, org, repo, soft):
    """Flag every commit after the soft deadline, and tag the last on-time one.

    A commit message cannot be changed without rewriting history and force-pushing
    into the student's repo, which would break their clones. A commit status is the
    non-destructive equivalent: GitHub shows it beside the commit in the web UI.

    The tag makes the boundary usable from the command line:
        git fetch --tags && git log soft-deadline..HEAD

    Commit dates come from the student's machine and can be backdated; the repo's
    `pushed_at`, which the report uses, cannot. This marks up the history, it does
    not decide who was late.
    """
    if not soft:
        return 0
    marked = 0
    try:
        late = gh.paginate(f"/repos/{org}/{repo}/commits?since={iso_z(soft)}")
    except GitHubError as e:
        if e.status == 409:   # still empty: a group that never pushed its work in
            return 0
        raise
    for c in late:
        try:
            current = gh.get(f"/repos/{org}/{repo}/commits/{c['sha']}/status")
            if any(st.get("context") == LATE_CONTEXT
                   for st in (current or {}).get("statuses", [])):
                continue
            gh.post(f"/repos/{org}/{repo}/statuses/{c['sha']}", {
                "state": "failure",
                "context": LATE_CONTEXT,
                "description": "Pushed after the soft deadline - counts as late",
            })
            marked += 1
        except GitHubError as e:
            print(f"  could not mark {c['sha'][:7]} in {repo}: {e}", file=sys.stderr)

    try:
        before = gh.paginate(f"/repos/{org}/{repo}/commits?until={iso_z(soft)}&per_page=1")
        if before and not gh.exists(f"/repos/{org}/{repo}/git/{BOUNDARY_TAG}"):
            gh.post(f"/repos/{org}/{repo}/git/refs",
                    {"ref": BOUNDARY_TAG, "sha": before[0]["sha"]})
    except GitHubError:
        pass
    return marked


def add_note(existing, text):
    return f"{existing}; {text}" if existing else text


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
    row = {
        "group": group["name"], "repo": repo, "pushed_at": None, "class": class_,
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
    if soft and pushed:
        row["soft"] = "on time" if pushed <= soft else "late"
    if hard and pushed:
        row["hard"] = "on time" if pushed <= hard else "late"

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
        n = mark_late_commits(gh, org, repo, soft)
        if n:
            row["note"] = add_note(row["note"], f"{n} late commit(s) marked")
    elif mark and row["soft"] == "late":
        n = mark_late_commits(gh, org, repo, soft)
        if n:
            row["note"] = add_note(row["note"], f"{n} late commit(s) marked")
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


def table(assignment, rows):
    by_class = assignment.get("soft_by_class") or {}
    soft = (", ".join(f"{t} {w}" for t, w in sorted(by_class.items()))
            if by_class else (assignment.get("soft_deadline") or "-"))
    hard = assignment.get("hard_deadline") or "-"
    out = [f"### {assignment['name']}", "",
           f"soft deadline `{soft}` · hard deadline `{hard}`", "",
           "| group | class | last push | soft | hard | repo | |",
           "|---|---|---|---|---|---|---|"]
    def cell(value):
        return {"on time": "✅ on time", "late": "⚠️ late", "-": "–"}.get(value, value)

    for r in rows:
        out.append(
            f"| {r['group']} | {r.get('class') or '–'} | {r['pushed_at'] or '–'} | "
            f"{cell(r['soft'])} | "
            f"{cell(r['hard'])} | `{r['repo']}` | "
            f"{r['state']}{' · ' + r['note'] if r['note'] else ''} |")
    late_soft = sum(1 for r in rows if r["soft"] == "late")
    late_hard = sum(1 for r in rows if r["hard"] == "late")
    out += ["", f"{len(rows)} group(s) · {late_soft} past the soft deadline · "
                f"{late_hard} past the hard deadline"]
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
                    help="flag commits pushed after the soft deadline, without locking")
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

    sections, all_rows = [], []
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
        for r in rows:
            # With --csv, stdout belongs to the CSV alone, or the file it is
            # redirected into opens as neither one thing nor the other.
            print(f"{a['name']} {r['group']:<24} soft={r['soft']:<8} hard={r['hard']:<8} "
                  f"{r['state']}{' (' + r['note'] + ')' if r['note'] else ''}",
                  file=sys.stderr if args.csv else sys.stdout)

    if args.csv:
        w = csv.DictWriter(sys.stdout, fieldnames=[
            "assignment", "group", "class", "repo", "pushed_at", "soft", "hard",
            "state", "note"])
        w.writeheader()
        w.writerows(all_rows)

    summary = os.environ.get("GITHUB_STEP_SUMMARY")
    if summary:
        with open(summary, "a", encoding="utf-8") as f:
            f.write("\n\n".join(sections) + "\n")


if __name__ == "__main__":
    main()
