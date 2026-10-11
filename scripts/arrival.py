"""When each commit first reached GitHub, from the records GitHub stamps itself.

A group's lateness is a question about one commit - the head of the default
branch, the work that gets graded - and the answer is the first time the server
received it. Two records carry that time, and neither can be set by a student:

- the push archive (pushlog.py), a durable copy of the Events API and of this
  module's Activity rows;
- the Activity API (/repos/{o}/{r}/activity), which lists every ref update with
  its kind: push, force_push, branch_creation, branch_deletion, pr_merge, ...

Taking the *earliest* time a sha was seen is what makes the measure robust:
creating a branch, re-pushing the same commit, or pointing another ref at it
moves the repository's `pushed_at` but cannot make work that was already there
arrive later. New work pushed after a deadline is a new sha, first seen then.
"""
import csv
import io

from ghlib import GitHubError, get_file, to_dt

FIELDS = ["pushed_at", "assignment", "group", "repo", "actor", "ref", "sha",
          "message", "author", "committed_at", "source", "event_id", "kind"]
ZERO = "0" * 40
# Activity ids are prefixed so they never collide with Events API ids.
ACTIVITY = "activity:"


def activities(gh, org, repo):
    """Every ref update the Activity API lists, oldest first; None if no repo."""
    try:
        items = gh.paginate(f"/repos/{org}/{repo}/activity")
    except GitHubError as e:
        if e.status in (404, 410):
            return None
        if e.status == 409:   # empty repository
            return []
        raise
    return sorted(items, key=lambda a: a.get("timestamp") or "")


def delivered(gh, org, repo, before, after, ref, default_branch):
    """The commits one ref update brought, as GitHub's commit objects.

    An update from an existing tip brings what lies between the two. A new ref
    brings what its default branch didn't already have; a new default branch
    (the first push) brings its whole history. Ends a rewrite made unreachable
    are recorded without commits rather than not at all.
    """
    try:
        if before and before != ZERO:
            return gh.get(f"/repos/{org}/{repo}/compare/{before}...{after}").get("commits") or []
        if default_branch and ref != f"refs/heads/{default_branch}":
            got = gh.get(f"/repos/{org}/{repo}/compare/{default_branch}...{after}").get("commits")
            if got:
                return got
            return [gh.get(f"/repos/{org}/{repo}/commits/{after}")]
        return gh.get(f"/repos/{org}/{repo}/commits?sha={after}&per_page=100") or []
    except GitHubError as e:
        if e.status in (404, 409, 422):
            return []
        raise


def commit_row(base, c):
    info = c.get("commit") or {}
    return dict(base,
                sha=c.get("sha", ""),
                message=info.get("message", ""),
                author=(info.get("author") or {}).get("name", ""),
                committed_at=(info.get("author") or {}).get("date", ""))


def activity_rows(gh, org, repo, assignment, group, default_branch, known=()):
    """Archive rows for every Activity entry whose id isn't in `known`.

    Deletions bring nothing and are left out. Entries already archived are
    skipped, so a run costs one compare per *new* ref update, not per update.
    Returns None if the repository is gone.
    """
    acts = activities(gh, org, repo)
    if acts is None:
        return None
    rows = []
    for a in acts:
        after = a.get("after") or ""
        eid = f"{ACTIVITY}{a.get('id')}"
        if a.get("activity_type") == "branch_deletion" or not after or after == ZERO or eid in known:
            continue
        base = {
            "pushed_at": a.get("timestamp") or "",
            "assignment": assignment,
            "group": group,
            "repo": repo,
            "actor": (a.get("actor") or {}).get("login", ""),
            "ref": a.get("ref") or "",
            "source": "activity",
            "event_id": eid,
            "kind": a.get("activity_type") or "",
        }
        commits = delivered(gh, org, repo, a.get("before"), after, base["ref"], default_branch)
        # The tip itself always arrived with this update, even if the compare didn't
        # list it (it had arrived before, on another ref): earliest-wins sorts that out.
        if not any(c.get("sha") == after for c in commits):
            commits = list(commits) + [{"sha": after}]
        rows += [commit_row(base, c) for c in commits]
    return rows


def earlier(new, cur):
    """Whether `new` says the commit arrived before `cur` does.

    A server time beats none (a commit-scan row has only the client's), and of
    two server times the earlier is when the commit actually first arrived.
    """
    if not new.get("pushed_at"):
        return False
    if not cur.get("pushed_at"):
        return True
    return to_dt(new["pushed_at"]) < to_dt(cur["pushed_at"])


def first_arrivals(rows):
    """{sha: the row of its first server-stamped arrival}; untimed rows left out."""
    out = {}
    for r in rows:
        if r.get("sha") and r.get("pushed_at") and (r["sha"] not in out or earlier(r, out[r["sha"]])):
            out[r["sha"]] = r
    return out


def read_rows(raw):
    """Archive CSV rows, squared up with the current columns: the archive outlives
    any one version of the scripts, so a file written before a column existed
    still has to read back cleanly."""
    rows = []
    for r in csv.DictReader(io.StringIO(raw.decode())):
        row = {f: (r.get(f) or "") for f in FIELDS}
        row["source"] = row["source"] or ("push" if row["event_id"] else "commit-scan")
        rows.append(row)
    return rows


def archived(gh, org, log_repo, assignment, group):
    """The group's rows in the push archive; [] if there is none (yet)."""
    try:
        raw = get_file(gh, org, log_repo, f"pushes/{assignment}/{group}.csv")
    except GitHubError as e:
        if e.status in (403, 404):
            return []
        raise
    return read_rows(raw) if raw else []


def head_of(gh, org, repo, branch):
    """The sha at the tip of `branch`, or None for an empty repository."""
    try:
        return gh.get(f"/repos/{org}/{repo}/commits/{branch}")["sha"]
    except GitHubError as e:
        if e.status in (404, 409, 422):
            return None
        raise


def arrivals(gh, org, repo, assignment, group, default_branch, log_repo="push-log"):
    """First arrival of every commit we have a server time for: the archive,
    topped up live from the Activity API with whatever it hasn't copied yet."""
    rows = archived(gh, org, log_repo, assignment, group)
    known = {r["event_id"] for r in rows if r["event_id"].startswith(ACTIVITY)}
    rows += activity_rows(gh, org, repo, assignment, group, default_branch, known) or []
    return first_arrivals(rows)


def verdict(deadline, received, pushed_at):
    """'on time', 'late' or 'unverified' for the graded commit against one deadline.

    `pushed_at`, the repository's last push, is an upper bound on every arrival:
    nothing pushed by the deadline means the head can't have arrived after it.
    Past that, only the head's own first arrival decides. With neither, the
    record has a gap (the Events API drops pushes) and a person has to look -
    a gap is never counted against the group.
    """
    if not deadline:
        return "-"
    if pushed_at and pushed_at <= deadline:
        return "on time"
    if received:
        return "on time" if received <= deadline else "late"
    return "unverified"


def describe(row):
    """How the graded commit arrived, in words: 'push to master by alice'."""
    kind = {"push": "push", "force_push": "force-push", "branch_creation": "new branch",
            "pr_merge": "pull request merge", "merge_queue_merge": "merge queue"}.get(
        row.get("kind") or "push", row.get("kind") or "push")
    ref = (row.get("ref") or "").removeprefix("refs/heads/")
    who = row.get("actor")
    return f"{kind}{' to ' + ref if ref else ''}{' by ' + who if who else ''}"
