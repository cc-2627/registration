#!/usr/bin/env python3
"""Create an assignment: a template repo from a folder, plus one repo per group.

Run locally as an org owner:

    GH_TOKEN=$(gh auth token) python scripts/new_assignment.py ORG a3 \
        --from ./skeletons/a3 \
        --soft "2026-11-15 23:59" --hard "2026-11-22 23:59"

That:
  1. creates (or updates) ORG/a3-template from ./skeletons/a3 and marks it a template,
  2. creates gXX_..._-a3 for every registered group, from that template, with write access,
  3. commits assignments/a3.json, with the deadlines, to the registration repo.

Step 3 goes straight to the default branch over the API, so the deadlines are live
as soon as the command finishes - there is nothing to remember to push. The bot
reads that folder to know which repos a newly registered group needs, and the
deadline workflow reads it to know when to lock. Run `git pull` to bring your own
checkout up to date.

Students who already started on repositories of their own can bring that work in:
with --own-work each group's repository starts empty, and the announcement shows
them the commands that push their work into it, history and all.

A new assignment must be given both deadlines; pass `none` for one you don't want
(`--hard none` means the repos are never locked). Re-running to change the skeleton
or a deadline is safe: existing repos are left alone, --from re-pushes the folder
only when you pass it, and a deadline you leave out keeps its current value.
"""
import argparse
import datetime
import glob
import json
import os
import subprocess
import sys

import rules
from ghlib import (ASSIGNMENT_RE, GitHub, GitHubError, assignments_dir, clone_history,
                   commit_files, default_org, ensure_repo, get_file, grant_team,
                   have_git, load_groups, load_classes, parse_deadline, push_folder,
                   push_own_work, put_file, repo_empty, repo_files, repo_name,
                   repo_paths, resolve_soft, to_dt)


def announce(gh, org, repo, name, group, definition, carried=None, replaced=(),
             own_work=False):
    """Open an issue in the group's repo @-mentioning its members.

    A mention is what actually reaches a student: GitHub emails everyone mentioned,
    whatever they have the repo watched as. We have no email addresses of our own -
    the roster is student numbers only - so this is the whole delivery mechanism.

    The marker makes it idempotent: re-running never posts a second time.
    """
    marker = f"<!-- registration-bot:assignment:{name} -->"
    for i in gh.paginate(f"/repos/{org}/{repo}/issues?state=all"):
        if marker in (i.get("body") or ""):
            return False

    when = []
    if definition.get("soft_deadline"):
        when.append(f"- **Soft deadline:** {definition['soft_deadline']} — "
                    f"you can still push after this, but it counts as late.")
    if definition.get("hard_deadline"):
        when.append(f"- **Hard deadline:** {definition['hard_deadline']} — "
                    f"this repository becomes read-only. Push before then.")
    mentions = " ".join(f"@{u}" for u in sorted(group["users"]))

    carry = ""
    if carried:
        carry = (f"\n\nYour work from **{carried}** is already here, history and all — "
                 f"clone this repository and carry on.")
        if replaced:
            carry += ("\n\n> The starting files for this assignment replaced "
                      + ", ".join(f"`{p}`" for p in sorted(replaced))
                      + ". Your version of "
                      + ("those files is" if len(replaced) > 1 else "that file is")
                      + " still in the commit before.")

    if own_work:
        start = (f"**{name}** is available. This repository is yours, and it starts empty "
                 f"so you can bring in the work you've already done, history and all. "
                 f"In a clone of the repository you've been working in, run:\n\n"
                 + push_own_work(org, repo)
                 + "\nFrom then on, push here. Haven't started yet? Just clone this "
                   "repository and begin.")
    else:
        start = (f"**{name}** is available. This repository is yours — it already has the "
                 f"starting files." + carry)
    body = (f"{marker}\n"
            f"{mentions}\n\n"
            + start + "\n\n"
            + ("\n".join(when) + "\n\n" if when else "")
            + "Everything you push to the default branch before the hard deadline counts. "
              "After it, pushes are refused, so don't leave it to the last minute.")
    gh.post(f"/repos/{org}/{repo}/issues", {"title": f"{name} is available", "body": body})
    return True


def carry_over(gh, token, org, group, previous, repo, template, name):
    """Create `repo` as a copy of the group's `previous` repository, with this
    assignment's starting files committed on top.

    Returns the paths the starting files replaced, or None if there was nothing
    to carry - no previous repository, or an empty one - in which case the caller
    makes the repository from the template in the usual way.

    Resumable: a repository left empty by a run that died mid-copy is picked up
    and finished, rather than being mistaken for one that is already done.
    """
    src = repo_name(group["name"], previous)
    if not gh.exists(f"/repos/{org}/{src}") or repo_empty(gh, org, src):
        return None
    if not gh.exists(f"/repos/{org}/{repo}"):
        gh.post(f"/orgs/{org}/repos",
                {"name": repo, "private": True, "auto_init": False})

    branch = clone_history(token, org, src, repo)
    if branch and gh.get(f"/repos/{org}/{repo}").get("default_branch") != branch:
        gh.patch(f"/repos/{org}/{repo}", {"default_branch": branch})

    theirs = repo_paths(gh, org, repo)
    files = repo_files(gh, org, template)
    # The starting files win: they are the statement for the new assignment, and
    # what they overwrite stays one commit back in the history we just copied.
    commit_files(gh, org, repo, {p: b for p, (b, _) in files.items()},
                 f"Starting files for {name}",
                 modes={p: m for p, (_, m) in files.items()})
    return sorted(theirs & set(files))


def listings_dir():
    """Where the class listings are, by default: `classes/` next to the checkout,
    or `turnos/` - the name the faculty's own export folder tends to have."""
    for name in ("../classes", "../turnos"):
        if os.path.isdir(name):
            return name
    return "../classes"


def classes_from(folder):
    """The class listings as CLASSES-secret text, or "" if the folder isn't there.

    Read locally and only for the announcement; nothing from these files is
    written anywhere, names included.
    """
    if not folder or not os.path.isdir(folder):
        return ""
    here = os.path.dirname(os.path.abspath(__file__))
    script = os.path.join(here, "make_classes.py")
    files = sorted(glob.glob(os.path.join(folder, "*")))
    if not files or not os.path.exists(script):
        return ""
    out = subprocess.run([sys.executable, script, *files],
                         capture_output=True, text=True)
    return out.stdout if out.returncode == 0 else ""


def read_deadline(value, tz):
    """(was it given on the command line?, the datetime or None for 'no deadline')"""
    if value is None:
        return False, None
    if value.strip().lower() in ("none", "never", "-"):
        return True, None
    return True, parse_deadline(value, tz)


def deadline_value(given, parsed, current):
    """What to store: a new value, an explicit none, or whatever is there already."""
    if not given:
        return current
    return parsed.isoformat() if parsed else None


def main():
    ap = argparse.ArgumentParser(
        description="Create an assignment and its per-group repositories.")
    ap.add_argument("org", nargs="?",
                    help="defaults to the organization this checkout belongs to")
    ap.add_argument("assignment", help="short name, e.g. a3")
    ap.add_argument("--from", dest="folder", metavar="DIR",
                    help="folder that becomes the template's contents")
    ap.add_argument("--soft-week", dest="soft_week", metavar="DATE",
                    help="any date in the week whose sessions start the clock; each "
                         "class then gets a week from its own session (see rules.py). "
                         "Required for a new assignment unless --soft is given.")
    ap.add_argument("--soft", metavar="WHEN",
                    help="one soft deadline for everyone, overriding the per-class "
                         "rule, e.g. '2026-11-15 23:59'; 'none' to have none")
    ap.add_argument("--classes", metavar="DIR", default=listings_dir(),
                    help="class listings, read locally so the announcement can tell "
                         "each group its own date (default: %(default)s)")
    ap.add_argument("--hard", metavar="WHEN",
                    help="hard deadline, e.g. '2026-11-22 23:59' (repos become read-only); "
                         "required for a new assignment, 'none' to have none")
    ap.add_argument("--repo", default="registration",
                    help="the registration repo the definition is committed to "
                         "(default: %(default)s)")
    ap.add_argument("--tz", default=rules.TZ,
                    help="time zone the deadlines are given in (default: %(default)s)")
    ap.add_argument("--template", help="template repo name (default: <assignment>-template)")
    ap.add_argument("--carry-over", metavar="ASSIGNMENT",
                    help="start each group\'s repo as a copy of their repo for this "
                         "earlier assignment - every commit, branch and tag - with the "
                         "new starting files committed on top")
    ap.add_argument("--own-work", action="store_true",
                    help="each group's repo starts empty, and the announcement shows them "
                         "how to push in work they already have elsewhere, history and "
                         "all; no starting files")
    ap.add_argument("--no-repos", action="store_true",
                    help="only create the template and the definition, no group repos")
    ap.add_argument("--no-announce", action="store_true",
                    help="don't open the issue that notifies each group by email")
    ap.add_argument("--local-only", action="store_true",
                    help="write assignments/<name>.json on disk instead of committing it")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()
    args.org = default_org(args.org)
    if not args.org:
        sys.exit("No organization given, and this folder is not a checkout of one.")

    name = args.assignment
    if not ASSIGNMENT_RE.match(name):
        sys.exit(f"'{name}' is not a usable assignment name (lowercase letters, digits, - and _).")

    try:
        soft_given, soft = read_deadline(args.soft, args.tz)
        hard_given, hard = read_deadline(args.hard, args.tz)
    except ValueError as e:
        sys.exit(str(e))
    soft_week = None
    if args.soft_week:
        try:
            soft_week = datetime.date.fromisoformat(args.soft_week[:10]).isoformat()
        except ValueError:
            sys.exit(f"--soft-week wants a date like 2026-11-03, not {args.soft_week!r}.")
        if args.soft:
            sys.exit("--soft and --soft-week set the same thing two different ways; "
                     "pass one.")
    if args.folder and not os.path.isdir(args.folder):
        sys.exit(f"Not a folder: {args.folder}")
    if args.own_work and (args.folder or args.template or args.carry_over):
        sys.exit("--own-work starts the repositories empty, so it takes no starting "
                 "files (--from, --template) and nothing to carry over.")
    if args.carry_over:
        # Checked before anything is created: finding out halfway would leave
        # half the groups with a copy of their work and half without.
        if args.carry_over == name:
            sys.exit("--carry-over takes the *previous* assignment, not this one.")
        if not ASSIGNMENT_RE.match(args.carry_over):
            sys.exit(f"'{args.carry_over}' is not a usable assignment name.")
        if not have_git():
            sys.exit("--carry-over copies repositories with git, and git is not on PATH.")
        if not os.environ.get("GH_TOKEN"):
            sys.exit("--carry-over needs GH_TOKEN set: it authenticates the git push.")

    template = args.template or f"{name}-template"
    token = os.environ.get("GH_TOKEN")
    gh = GitHub(token)

    # An assignment that doesn't exist yet has to be given both deadlines, so none
    # can quietly end up without one.
    path = f"assignments/{name}.json"
    existing = {}
    if args.local_only:
        local = os.path.join(assignments_dir(), f"{name}.json")
        if os.path.exists(local):
            with open(local, encoding="utf-8") as f:
                existing = json.load(f)
    else:
        raw = get_file(gh, args.org, args.repo, path)
        if raw:
            existing = json.loads(raw)
    if not existing:
        missing = []
        if not soft_week and not soft_given:
            missing.append("--soft-week (or --soft)")
        if not hard_given:
            missing.append("--hard")
        if missing:
            sys.exit(f"{name} is new, so it needs {' and '.join(missing)}.\n"
                     f"--soft-week takes any date in the week whose sessions start the "
                     f"clock; --hard takes a date or the word 'none'.")

    # A re-run keeps what the assignment started from, so moving a deadline
    # needs neither the template nor the flag again.
    if not args.template and existing.get("template"):
        template = existing["template"]
    own_work = args.own_work or existing.get("own_work", False)
    if own_work:
        template = None
    soft_week = soft_week or existing.get("soft_week")
    soft_manual = bool(soft_given) if (soft_given or args.soft_week) \
        else existing.get("soft_manual", False)

    # Per class, for the announcement and for the printout. The group-level rule
    # runs later, in deadlines.py, because groups keep arriving.
    classes = load_classes() or load_classes(classes_from(args.classes))
    probe = {"name": name, "soft_week": soft_week,
             "soft_deadline": soft.isoformat() if soft else None,
             "soft_manual": soft_manual}
    by_class = {}
    if soft_week and not soft_manual:
        for t in sorted(rules.SCHEDULE):
            when, _ = resolve_soft(rules, probe, {"name": "-", "students": ("0",)}, {},
                                   claims={"0": t})
            if when:
                by_class[t] = when.isoformat()
    # The stored soft_deadline is what a group with no known class falls back to:
    # the latest session, so missing data never costs anyone time.
    if by_class and not soft_manual:
        soft = to_dt(max(by_class.values()))

    if soft and hard and hard < soft:
        sys.exit(f"The hard deadline ({hard}) is before the soft one ({soft}).")

    groups = load_groups(gh, args.org, ignore_users=[gh.get("/user")["login"]])

    print(f"Assignment {name} in {args.org}")
    if own_work:
        print("  starting     empty: each group pushes in its own work")
    else:
        print(f"  template     {template}" + ("" if args.folder else "  (contents unchanged)"))
    if by_class:
        print(f"  soft week     {soft_week} (each class gets "
              f"{rules.AFTER_SESSION.days} days from its own session)")
        for t, when in sorted(by_class.items()):
            print(f"    {t}          {when}")
        print(f"  soft fallback {soft or '-'}  (groups with no known class)")
    else:
        print(f"  soft deadline {soft or '-'}" + ("  (set by hand)" if soft_manual else ""))
    print(f"  hard deadline {hard or '-'}")
    if args.carry_over:
        print(f"  carry over    each group's {args.carry_over} repo, with its history")
    print(f"  groups        {len(groups)}")
    if args.dry_run:
        for g in groups:
            prev = repo_name(g["name"], args.carry_over) if args.carry_over else None
            copied = prev and gh.exists(f"/repos/{args.org}/{prev}")
            print(f"  would create {repo_name(g['name'], name)}"
                  + (f"  (copying {prev})" if copied else
                     f"  (nothing to copy: no {prev})" if prev else "")
                  + ("" if args.no_announce
                     else "  + an issue mentioning " + ", ".join(f"@{u}" for u in sorted(g["users"]))))
        print(f"  would commit {path} to {args.org}/{args.repo}"
              if not args.local_only else f"  would write {path} locally")
        print("(dry run, nothing changed)")
        return

    # 1. template repo
    if template and ensure_repo(gh, args.org, template):
        print(f"created {template}")
    if template:
        gh.patch(f"/repos/{args.org}/{template}", {"is_template": True})
    if args.folder:
        n = push_folder(gh, args.org, template, args.folder,
                        f"Contents for {name}")
        print(f"pushed {n} file(s) from {args.folder} to {template}")

    # 2. definition, so the bot and the deadline workflow know about it
    definition = {
        "name": name,
        "template": template,
        "soft_week": soft_week,
        "soft_manual": soft_manual,
        # One date (or none) for everyone replaces any per-class dates.
        "soft_by_class": {} if soft_manual else (by_class or existing.get("soft_by_class", {})),
        "soft_deadline": soft.isoformat() if (by_class and soft) else
                         deadline_value(soft_given, soft, existing.get("soft_deadline")),
        "hard_deadline": deadline_value(hard_given, hard, existing.get("hard_deadline")),
        "created": existing.get("created") or datetime.datetime.now(
            datetime.timezone.utc).isoformat(timespec="seconds"),
    }
    if own_work:
        definition["own_work"] = True
    data = (json.dumps(definition, indent=2) + "\n").encode()
    if args.local_only:
        os.makedirs(assignments_dir(), exist_ok=True)
        local = os.path.join(assignments_dir(), f"{name}.json")
        with open(local, "wb") as f:
            f.write(data)
        print(f"wrote {os.path.relpath(local)} - commit and push it yourself")
    else:
        commit = put_file(gh, args.org, args.repo, path, data,
                          f"{'Update' if existing else 'Add'} assignment {name}")
        if commit:
            print(f"committed {path} to {args.org}/{args.repo} "
                  f"({commit['commit']['sha'][:7]})")
        else:
            print(f"{path} already up to date in {args.org}/{args.repo}")

    # 3. one repo per group
    failed = 0
    if not args.no_repos:
        for g in groups:
            repo = repo_name(g["name"], name)
            try:
                carried, replaced = None, ()
                fresh = not gh.exists(f"/repos/{args.org}/{repo}")
                if args.carry_over and (fresh or repo_empty(gh, args.org, repo)):
                    got = carry_over(gh, token, args.org, g, args.carry_over,
                                     repo, template, name)
                    if got is not None:
                        carried, replaced = args.carry_over, got
                created = fresh if carried else ensure_repo(gh, args.org, repo, template,
                                                            empty=own_work)
                grant_team(gh, args.org, g["slug"], repo)
                told = (not args.no_announce
                        and announce(gh, args.org, repo, name, g, definition,
                                     carried=carried, replaced=replaced,
                                     own_work=own_work))
                print(f"{'created' if created else 'exists '} {repo}"
                      + (f"  (copied {carried}"
                         + (f", {len(replaced)} file(s) replaced" if replaced else "")
                         + ")" if carried else "")
                      + ("  (announced)" if told else ""))
            except (GitHubError, RuntimeError) as e:
                failed += 1
                print(f"FAILED  {repo}: {e}", file=sys.stderr)
        if failed:
            print(f"\n{failed} repo(s) failed; re-run to retry.", file=sys.stderr)

    if args.local_only:
        print(f"\nCommit it so the bot picks it up:\n"
              f"  git add {path} && git commit -m 'Add {name}' && git push")
    else:
        print(f"\nDeadlines are live. Run `git pull` to update your checkout.")
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    main()
