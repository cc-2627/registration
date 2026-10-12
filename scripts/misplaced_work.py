#!/usr/bin/env python3
"""Find groups still pushing an assignment's follow-up into the earlier one's
repository, and (with --yes) tell them how to move it.

    GH_TOKEN=$(gh auth token) python scripts/misplaced_work.py project1_part1 project1_part2
    GH_TOKEN=$(gh auth token) python scripts/misplaced_work.py project1_part1 project1_part2 --yes

A group is listed when its OLD repository holds work GitHub received after OLD's
soft deadline: on the default branch (which would make OLD late) or on any other
branch. Groups that already moved their work and reset OLD are not listed, so
re-running only finds who is left.

For each one it says which commit is the last that arrived on time - the one to
reset OLD to - and what NEW holds. With --yes it opens one issue in the OLD
repository, @-mentioning the group (GitHub emails a mention), with those exact
commands. It posts once per group; re-running never posts twice.

Late fixes to OLD are legitimate and look the same from here, so the issue asks
rather than tells, and nothing is moved for anyone.
"""
import argparse
import os
import sys

import rules
from arrival import arrivals, head_of
from ghlib import (GitHub, GitHubError, default_org, load_assignments, load_claims,
                   load_classes, load_groups, repo_name, resolve_soft, to_dt)
from deadlines import GRACE

MARKER = "<!-- registration-bot:misplaced:{old}:{new} -->"


def when(dt):
    """'8 Oct 2026, 23:59 (UTC+01:00)': the deadline as students were given it."""
    z = dt.strftime("%z")
    return dt.strftime("%-d %b %Y, %H:%M") + (f" (UTC{z[:3]}:{z[3:]})" if z and z != "+0000" else " UTC")


def received(first, sha):
    row = first.get(sha)
    return to_dt(row["pushed_at"]) if row else None


def inspect(gh, org, group, old, new, deadline):
    """What OLD holds past the deadline, or None if nothing is out of place."""
    repo = repo_name(group["name"], old)
    try:
        info = gh.get(f"/repos/{org}/{repo}")
    except GitHubError as e:
        if e.status == 404:
            return None
        raise
    branch = info.get("default_branch")
    head = head_of(gh, org, repo, branch) if branch else None
    if not head:
        return None
    first = arrivals(gh, org, repo, old, group["name"], branch)
    late_head = (received(first, head) or deadline) > deadline   # no record: not ours to say
    others = []
    for b in gh.paginate(f"/repos/{org}/{repo}/branches"):
        if b["name"] != branch and (received(first, b["commit"]["sha"]) or deadline) > deadline:
            others.append(b["name"])
    if not late_head and not others:
        return None
    on_time = None
    if late_head:
        for c in gh.paginate(f"/repos/{org}/{repo}/commits?sha={branch}"):   # newest first
            when = received(first, c["sha"])
            if when and when <= deadline:
                on_time = c["sha"]
                break
    target = repo_name(group["name"], new)
    try:
        new_head = head_of(gh, org, target, gh.get(f"/repos/{org}/{target}")["default_branch"])
    except GitHubError as e:
        if e.status != 404:
            raise
        new_head = None
    return {"repo": repo, "branch": branch, "late_head": late_head, "on_time": on_time,
            "others": others, "target": target, "new_empty": new_head is None}


def body(org, group, old, new, deadline_text, found):
    url = f"https://github.com/{org}/{found['target']}.git"
    mentions = " ".join(f"@{u}" for u in sorted(group["users"]))
    branch = found["branch"]
    steps = ["git fetch origin"]
    if found["late_head"]:
        # origin/<branch>, not the local branch: exactly what GitHub holds, even if
        # this clone is behind a partner's pushes.
        steps.append(f"git push {url} origin/{branch}:refs/heads/main"
                     + ("" if found["new_empty"] else "    # see below if this is refused"))
        if found["on_time"]:
            steps += [f"git branch {new}-backup origin/{branch}    # a copy, just in case",
                      f"git checkout {branch}",
                      f"git reset --hard {found['on_time'][:12]}",
                      f"git push --force-with-lease origin {branch}"]
    for b in found["others"]:
        # Into an empty repository, a lone branch becomes its main.
        dest = "main" if found["new_empty"] and not found["late_head"] and len(found["others"]) == 1 else b
        steps += [f"git push {url} origin/{b}:refs/heads/{dest}", f"git push origin --delete {b}"]
    where = ([f"`{branch}`"] if found["late_head"] else []) + [f"`{b}`" for b in found["others"]]
    text = [MARKER.format(old=old, new=new), mentions, "",
            f"This repository is for **{old}**, whose deadline was {deadline_text}. "
            f"GitHub received new work here after it, on {' and '.join(where)}.", "",
            f"**If that is work for {new}, it belongs in "
            f"[{found['target']}](https://github.com/{org}/{found['target']}).** "
            + (f"Moved there, it no longer counts against {old}." if found["late_head"] else
               f"Only `{branch}` is graded, so it doesn't count against {old}, but it "
               f"isn't in {new} either.")
            + (f" `{found['on_time'][:12]}` is the last commit here that arrived on time."
               if found["on_time"] else ""), "",
            "In a clone of this repository:", "", "```", *steps, "```", ""]
    if found["late_head"] and not found["new_empty"]:
        text += [f"If the push to {found['target']} is refused because it already has commits, "
                 f"combine the two first, then push the result:", "", "```",
                 f"git checkout {branch}", f"git pull origin {branch}",
                 f"git pull {url} main --allow-unrelated-histories",
                 f"git push {url} {branch}:main", "```", "",
                 f"Before resetting this repository, check that {found['target']} has "
                 f"everything you need.", ""]
    if found["late_head"] and not found["on_time"]:
        text += ["Nothing in this repository arrived before the deadline, so there is nothing "
                 "to reset to: just move the work, and tell your teacher.", ""]
    if found["late_head"]:
        text += [f"If these commits really are late fixes to {old}, ignore this: they count "
                 f"as late for {old}, as usual."]
    return "\n".join(text)


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("old", help="the earlier assignment, whose repositories got the work")
    ap.add_argument("new", help="the assignment the work belongs to")
    ap.add_argument("--yes", action="store_true",
                    help="open the issue in each group's repository; without this, only list")
    ap.add_argument("--repo", default="registration",
                    help="the registration repo holding assignments/ (default: %(default)s)")
    args = ap.parse_args()

    org = default_org()
    if not org:
        sys.exit("Set ORG (the organization name).")
    gh = GitHub(os.environ.get("ADMIN_TOKEN") or os.environ.get("GH_TOKEN"))
    source = f"{org}/{args.repo}"
    defs = {a["name"]: a for a in load_assignments(gh, source)}
    for n in (args.old, args.new):
        if n not in defs:
            sys.exit(f"No assignment called {n} in {source}.")
    groups = load_groups(gh, org, ignore_users=[gh.get("/user")["login"]])
    classes, claims = load_classes(), load_claims(gh, source)

    found_any = 0
    for g in groups:
        soft, _ = resolve_soft(rules, defs[args.old], g, classes, claims)
        if not soft:
            continue
        found = inspect(gh, org, g, args.old, args.new, soft + GRACE)
        if not found:
            continue
        found_any += 1
        what = (["main past the deadline" if found["late_head"] else None]
                + [f"branch {b}" for b in found["others"]])
        print(f"{g['name']:<24} {', '.join(w for w in what if w)}; "
              f"on time: {found['on_time'][:7] if found['on_time'] else '-'}; "
              f"{args.new}: {'empty' if found['new_empty'] else 'has work'}")
        if args.yes:
            marker = MARKER.format(old=args.old, new=args.new)
            if any(marker in (i.get("body") or "")
                   for i in gh.paginate(f"/repos/{org}/{found['repo']}/issues?state=all")):
                print("    already told")
                continue
            gh.post(f"/repos/{org}/{found['repo']}/issues", {
                "title": f"Work pushed here after the deadline: is it for {args.new}?",
                "body": body(org, g, args.old, args.new, when(soft), found)})
            print("    issue opened")
    print(f"\n{found_any} group(s) with work in {args.old} past its deadline."
          + ("" if args.yes or not found_any else " Run with --yes to open an issue for each."))


if __name__ == "__main__":
    main()
