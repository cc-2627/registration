#!/usr/bin/env python3
"""Rename an assignment, and every group's repository for it, without the
students having to do anything.

    GH_TOKEN=$(gh auth token) python scripts/rename_assignment.py project_1 project1_part1
    GH_TOKEN=$(gh auth token) python scripts/rename_assignment.py project_1 project1_part1 --yes

Without --yes it only says what it would do.

GitHub redirects a renamed repository's old name - web pages, the API, and git
itself - so a student's clone keeps pushing and pulling through its old remote
URL as if nothing happened. The only thing they see is the new name on GitHub.
That redirect lasts until a repository is created under the old name, so the old
name is recorded in the definition (`renamed_from`) and new_assignment.py refuses
to reuse it.

In order, so that a run that stops halfway leaves nothing a re-run can't finish:
  1. each group's <group>-OLD repository becomes <group>-NEW (and OLD-template,
     if the assignment uses the default template name);
  2. the push archive's pushes/OLD/ moves to pushes/NEW/, in one commit;
  3. assignments/OLD.json becomes assignments/NEW.json, in one commit.

Commit history, issues, commit statuses, tags, team access and locks all belong
to the repository and come along unchanged. Nothing is posted to the groups and
nobody is emailed. Renaming back is the same command the other way round.
"""
import argparse
import json
import os
import sys

from ghlib import (ASSIGNMENT_RE, GitHub, GitHubError, commit_files, default_org,
                   get_file, load_assignments, load_groups, repo_name)


def exists(gh, org, repo):
    """The repository's own name if `repo` resolves to one, else None.

    A renamed repository's old name still resolves, through the redirect, so the
    name GitHub answers with is what says whether this one was renamed already.
    """
    try:
        return gh.get(f"/repos/{org}/{repo}")["name"]
    except GitHubError as e:
        if e.status == 404:
            return None
        raise


def rename_repo(gh, org, old, new, apply):
    """'renamed', 'done' (renamed already), 'missing', or 'would rename'."""
    if exists(gh, org, new) == new:
        return "done"
    actual = exists(gh, org, old)
    if actual is None or actual != old:
        return "missing"
    if not apply:
        return "would rename"
    gh.patch(f"/repos/{org}/{old}", {"name": new})
    return "renamed"


def move_archive(gh, org, log_repo, old, new, apply):
    """Move pushes/OLD/*.csv to pushes/NEW/ in one commit. Returns how many files."""
    try:
        info = gh.get(f"/repos/{org}/{log_repo}")
        tree = gh.get(f"/repos/{org}/{log_repo}/git/trees/"
                      f"{info.get('default_branch') or 'main'}?recursive=1")
    except GitHubError as e:
        if e.status in (404, 409):
            return 0
        raise
    paths = [t["path"] for t in tree.get("tree") or []
             if t["type"] == "blob" and t["path"].startswith(f"pushes/{old}/")]
    if paths and apply:
        files = {f"pushes/{new}/{p[len(f'pushes/{old}/'):]}": get_file(gh, org, log_repo, p)
                 for p in paths}
        commit_files(gh, org, log_repo, files,
                     f"Rename {old} to {new}: move its push archive", delete=paths)
    return len(paths)


def move_definition(gh, org, repo, definition, old, new, apply):
    renamed = dict(definition, name=new)
    renamed["renamed_from"] = sorted(set(definition.get("renamed_from") or ()) | {old})
    if apply:
        commit_files(gh, org, repo,
                     {f"assignments/{new}.json":
                          (json.dumps(renamed, indent=2, ensure_ascii=False) + "\n").encode()},
                     f"Rename assignment {old} to {new}",
                     delete=[f"assignments/{old}.json"])
    return renamed


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("old", help="the assignment's current name")
    ap.add_argument("new", help="its new name (lowercase letters, digits, - and _)")
    ap.add_argument("--yes", action="store_true", help="do it; without this, only say what would happen")
    ap.add_argument("--repo", default="registration",
                    help="the registration repo holding assignments/ (default: %(default)s)")
    ap.add_argument("--log-repo", default="push-log",
                    help="the push archive (default: %(default)s)")
    args = ap.parse_args()

    org = default_org()
    if not org:
        sys.exit("Set ORG (the organization name).")
    if not ASSIGNMENT_RE.match(args.new):
        sys.exit(f"'{args.new}' is not a usable name: lowercase letters, digits, - and _.")
    if args.new == args.old:
        sys.exit("That is already its name.")
    gh = GitHub(os.environ.get("ADMIN_TOKEN") or os.environ.get("GH_TOKEN"))
    source = f"{org}/{args.repo}"

    assignments = {a["name"]: a for a in load_assignments(gh, source)}
    definition = assignments.get(args.old)
    if definition is None:
        if args.new in assignments and args.old in (assignments[args.new].get("renamed_from") or ()):
            # Steps 1 and 2 are idempotent; finish them in case a run stopped early.
            definition = assignments[args.new]
            print(f"assignments/{args.new}.json is already in place; checking the rest.")
        else:
            sys.exit(f"No assignment called {args.old} in {source}.")
    elif args.new in assignments:
        sys.exit(f"There is already an assignment called {args.new}.")
    for a in assignments.values():
        if args.new in (a.get("renamed_from") or ()):
            sys.exit(f"{args.new} is a former name of {a['name']}: its repositories' "
                     f"old names redirect there. Pick another.")

    groups = load_groups(gh, org, ignore_users=[gh.get("/user")["login"]])
    print(f"{'Renaming' if args.yes else 'Would rename'} {args.old} to {args.new} "
          f"for {len(groups)} group(s) in {org}.")

    counts = {}
    pairs = [(repo_name(g["name"], args.old), repo_name(g["name"], args.new)) for g in groups]
    if not definition.get("own_work") and not definition.get("template"):
        # The default template name follows the assignment; a named one is left alone.
        pairs.append((f"{args.old}-template", f"{args.new}-template"))
    for old, new in pairs:
        state = rename_repo(gh, org, old, new, args.yes)
        counts[state] = counts.get(state, 0) + 1
        if state != "done":
            print(f"  {state:<13} {old} -> {new}")
    print("  repositories: " + ", ".join(f"{n} {s}" for s, n in sorted(counts.items())))

    n = move_archive(gh, org, args.log_repo, args.old, args.new, args.yes)
    print(f"  push archive: {n} file(s) {'moved' if args.yes else 'to move'} "
          f"from pushes/{args.old}/ to pushes/{args.new}/")

    if definition["name"] == args.old:
        move_definition(gh, org, args.repo, definition, args.old, args.new, args.yes)
        print(f"  definition: assignments/{args.old}.json "
              f"{'is now' if args.yes else 'would become'} assignments/{args.new}.json")

    if not args.yes:
        print("\nNothing changed. Run again with --yes to do it.")
    else:
        print(f"\nDone. Students' clones keep working through the redirect; nobody was emailed.\n"
              f"Run `git pull` in your checkout to pick up assignments/{args.new}.json.")


if __name__ == "__main__":
    main()
