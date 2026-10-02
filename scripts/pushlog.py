#!/usr/bin/env python3
"""Archive every push to the group repositories into a repository of its own.

The Events API is the only per-push record GitHub stamps itself: the timestamp
comes from the server when the push arrives, so unlike a commit date it cannot be
backdated, and unlike `pushed_at` it is one row per push rather than one per repo.

It is also short-lived - roughly the last 300 events, nothing older than 90 days -
so it has to be copied somewhere durable while it is still there. This does that
copy. Run it often enough (the deadlines workflow runs it every 30 minutes) and
the archive is complete even though the source expires.

Laid out as pushes/<assignment>/<group>.csv in <org>/push-log, private, created
on first run. One row per commit, carrying the push that delivered it, so a row
says both when the server received the work and what the work said it was. Rows
are keyed by (event id, sha), so re-running is free and never duplicates.
"""
import argparse
import csv
import io
import os
import sys

from ghlib import (GitHub, GitHubError, commit_files, default_org, ensure_repo,
                   get_file, load_assignments, load_groups, repo_name)

ORG = default_org()
REPO = os.environ.get("REPO")   # "owner/name"; set by the workflow
FIELDS = ["pushed_at", "assignment", "group", "repo", "actor", "ref", "sha",
          "message", "author", "committed_at", "source", "event_id"]
ZERO = "0" * 40


def push_commits(gh, org, repo, before, head):
    """The commits one push delivered, oldest first.

    The event payload for a private repository carries only before/head, so the
    messages have to be asked for separately. A force-push or a later rewrite can
    leave either end unreachable, and then the push is recorded without them
    rather than not at all.
    """
    try:
        if before and before != ZERO:
            return gh.get(f"/repos/{org}/{repo}/compare/{before}...{head}").get("commits") or []
        return [gh.get(f"/repos/{org}/{repo}/commits/{head}")]
    except GitHubError as e:
        if e.status in (404, 422):
            return []
        raise


def push_events(gh, org, repo, assignment, group):
    """Every PushEvent the API will still admit to, oldest first."""
    try:
        events = gh.paginate(f"/repos/{org}/{repo}/events")
    except GitHubError as e:
        if e.status in (404, 410):   # repo deleted, or never created
            return None
        raise
    rows = []
    for e in events:
        if e.get("type") != "PushEvent":
            continue
        payload = e.get("payload") or {}
        base = {
            "pushed_at": e["created_at"],
            "assignment": assignment,
            "group": group,
            "repo": repo,
            "actor": (e.get("actor") or {}).get("login", ""),
            "ref": payload.get("ref") or "",
            "source": "push",
            "event_id": e["id"],
        }
        commits = push_commits(gh, org, repo, payload.get("before"), payload.get("head"))
        if not commits:
            # Still record the push: that it happened, and when, is the point.
            rows.append(dict(base, sha="", message="", author="", committed_at=""))
            continue
        for c in commits:
            info = c.get("commit") or {}
            rows.append(dict(
                base,
                sha=c.get("sha", ""),
                message=info.get("message", ""),
                author=(info.get("author") or {}).get("name", ""),
                committed_at=(info.get("author") or {}).get("date", ""),
            ))
    return rows


def commit_scan(gh, org, repo, assignment, group, limit=100):
    """The newest commits on all branches, whether or not an event ever mentioned
    them.

    The Events API drops pushes - observed directly: four of five web-editor
    commits in one repository produced no event at all. Those commits would
    otherwise be missing from the archive entirely. They are recorded with no
    pushed_at, because the only time available for them is the commit's own,
    which the client sets and can therefore lie about.
    """
    try:
        commits = gh.get(f"/repos/{org}/{repo}/commits?per_page={limit}")
    except GitHubError as e:
        if e.status in (404, 409, 410):   # gone, or no commits yet
            return []
        raise
    rows = []
    for c in commits or []:
        info = c.get("commit") or {}
        rows.append({
            "pushed_at": "",
            "assignment": assignment,
            "group": group,
            "repo": repo,
            "actor": (c.get("author") or {}).get("login", ""),
            "ref": "",
            "sha": c.get("sha", ""),
            "message": info.get("message", ""),
            "author": (info.get("author") or {}).get("name", ""),
            "committed_at": (info.get("author") or {}).get("date", ""),
            "source": "commit-scan",
            "event_id": "",
        })
    return rows


def render(rows):
    buf = io.StringIO()
    w = csv.DictWriter(buf, fieldnames=FIELDS, lineterminator="\n")
    w.writeheader()
    # Pushed rows in push order; commit-only rows fall back to their own date.
    w.writerows(sorted(rows, key=lambda r: (r["pushed_at"] or r["committed_at"],
                                            r["event_id"], r["sha"])))
    return buf.getvalue().encode()


def existing(gh, org, log_repo, path):
    """Archived rows, squared up with the current columns: the archive outlives
    any one version of this script, so a file written before a column existed
    still has to read back cleanly."""
    raw = get_file(gh, org, log_repo, path)
    if not raw:
        return []
    rows = []
    for r in csv.DictReader(io.StringIO(raw.decode())):
        row = {f: (r.get(f) or "") for f in FIELDS}
        row["source"] = row["source"] or ("push" if row["event_id"] else "commit-scan")
        rows.append(row)
    return rows


def key(row):
    """A commit is the same commit however it was noticed; a push that delivered
    nothing readable is identified by the event instead."""
    return ("sha", row["sha"]) if row["sha"] else ("event", row["event_id"])


def merge(old, new):
    """Old rows win, with one exception: a push event supersedes a row the commit
    sweep found first, because the push carries a server timestamp and the sweep
    has only the client's. Nothing already written is otherwise revised."""
    rows = {key(r): r for r in old}
    pushes, commits = set(), 0
    for r in new:
        k = key(r)
        cur = rows.get(k)
        if cur is None:
            rows[k] = r
            commits += 1
            if r["event_id"]:
                pushes.add(r["event_id"])
        elif r["event_id"] and not cur["event_id"]:
            rows[k] = r
            pushes.add(r["event_id"])
    return list(rows.values()), len(pushes), commits


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--log-repo", default="push-log",
                    help="repository the archive lives in (default: %(default)s)")
    ap.add_argument("--repo", default="registration",
                    help="the registration repo holding assignments/ (default: %(default)s)")
    ap.add_argument("--local", action="store_true",
                    help="read assignments/ from this checkout instead of the repo")
    ap.add_argument("--show", nargs="?", const="*", metavar="ASSIGNMENT",
                    help="print the archive as CSV instead of updating it")
    ap.add_argument("--dry-run", action="store_true",
                    help="say what would be archived, write nothing")
    args = ap.parse_args()

    if not ORG:
        sys.exit("ORG is not set.")
    gh = GitHub(os.environ.get("ADMIN_TOKEN") or os.environ.get("GH_TOKEN"))

    if args.show:
        show(gh, args)
        return

    source = None if args.local else (REPO or f"{ORG}/{args.repo}")
    assignments = load_assignments(gh, source)
    if not assignments:
        print("No assignments yet; nothing to archive.")
        return
    groups = load_groups(gh, ORG, ignore_users=[gh.get("/user")["login"]])
    if not groups:
        print("No groups registered yet; nothing to archive.")
        return

    if not args.dry_run and ensure_repo(gh, ORG, args.log_repo):
        print(f"created {ORG}/{args.log_repo} (private)")

    changed, added, added_commits, missing = {}, 0, 0, 0
    for a in assignments:
        for g in groups:
            repo = repo_name(g["name"], a["name"])
            rows = push_events(gh, ORG, repo, a["name"], g["name"])
            if rows is None:
                missing += 1
                continue
            rows += commit_scan(gh, ORG, repo, a["name"], g["name"])
            path = f"pushes/{a['name']}/{g['name']}.csv"
            old = [] if args.dry_run else existing(gh, ORG, args.log_repo, path)
            merged, pushes, commits = merge(old, rows)
            if pushes or commits:
                changed[path] = render(merged)
                added += pushes
                added_commits += commits
                print(f"{a['name']}/{g['name']}: +{pushes} push(es), "
                      f"+{commits} commit(s), {len(merged)} row(s) total")

    if missing:
        print(f"({missing} repo(s) not reachable - deleted or not created yet)")
    if not changed:
        print("Nothing new to archive.")
        return
    what = f"{added} push(es), {added_commits} commit(s)"
    if args.dry_run:
        print(f"(dry run) would archive {what} across {len(changed)} file(s)")
        return

    sha = commit_files(gh, ORG, args.log_repo, changed,
                       f"Archive {what} across {len(changed)} repo(s)")
    print(f"committed {sha[:8]} to {ORG}/{args.log_repo}: {what}")


def archive_paths(gh, org, log_repo):
    """Every CSV in the archive. One recursive tree read beats walking directories."""
    try:
        info = gh.get(f"/repos/{org}/{log_repo}")
        tree = gh.get(f"/repos/{org}/{log_repo}/git/trees/"
                      f"{info.get('default_branch') or 'main'}?recursive=1")
    except GitHubError as e:
        if e.status in (404, 409):   # no repo, or no commits yet
            return []
        raise
    return sorted(t["path"] for t in tree.get("tree") or []
                  if t["type"] == "blob" and t["path"].startswith("pushes/")
                  and t["path"].endswith(".csv"))


def show(gh, args):
    """Print the whole archive, so grading never has to touch the live API."""
    paths = archive_paths(gh, ORG, args.log_repo)
    if not paths:
        sys.exit(f"No archive in {ORG}/{args.log_repo} yet.")
    if args.show != "*":
        paths = [p for p in paths if p.startswith(f"pushes/{args.show}/")]
    rows = []
    for path in paths:
        rows += existing(gh, ORG, args.log_repo, path)
    sys.stdout.write(render(rows).decode())


if __name__ == "__main__":
    main()
