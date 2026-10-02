#!/usr/bin/env python3
"""Undo testing: delete the teams, repos, issues and assignments you made trying it out.

By default it only touches *test* artefacts - groups whose student numbers all start
with 999, which is what `05_roster.sh --test-students` hands out:

    GH_TOKEN=$(gh auth token) python scripts/reset.py ORG --dry-run
    GH_TOKEN=$(gh auth token) python scripts/reset.py ORG

`--all` instead wipes every group, every group repo, every registration issue and every
assignment, putting the org back to just-after-setup. That is the one to use before a
real course edition starts, and never during one.

It always shows the full list first and asks twice: once to approve, once to type the
organization name. `--dry-run` shows the list and stops.

Not touched, because they are setup rather than testing: the registration repo itself,
its labels and secrets, and the org member privileges. Test student numbers live in the
ROSTER secret, which cannot be read back - re-run 05_roster.sh without --test-students
to clear them.
"""
import argparse
import csv
import io
import os
import re
import sys
import urllib.parse
import webbrowser

from ghlib import (CLAIMS_PATH, CLAIM_FIELDS, GitHub, GitHubError, default_org,
                   get_file, load_assignments, load_groups, put_file)

# Any number starting with 999: the 99901.. that --test-students hands out, plus
# whatever you improvised by hand. Real student numbers don't live in that range.
TEST_NUMBER = re.compile(r"^999\d{1,3}$")
TEST_NUMBER_IN_BODY = re.compile(r"\b999\d{1,3}\b")
DELETE_ISSUE = """
mutation($id: ID!) { deleteIssue(input: {issueId: $id}) { clientMutationId } }
"""


def is_test_group(group):
    return all(TEST_NUMBER.match(n) for n in group["students"])


def plan(gh, org, repo, args):
    """Everything that would be deleted, without deleting any of it."""
    me = gh.get("/user")["login"]
    groups = [g for g in load_groups(gh, org, ignore_users=[me])
              if args.all or is_test_group(g)]
    names = {g["name"] for g in groups}
    users = sorted({u for g in groups for u in g["users"]})

    all_repos = gh.paginate(f"/orgs/{org}/repos?type=all")
    repos = [r for r in all_repos
             if any(r["name"].startswith(f"{n}-") for n in names)]

    issues = []
    for i in gh.paginate(f"/repos/{org}/{repo}/issues?state=all&labels=registration"):
        if "pull_request" in i:
            continue
        if args.all or (i.get("body") and TEST_NUMBER_IN_BODY.search(i["body"])):
            issues.append(i)

    wanted = set(args.assignments.split(",")) if args.assignments else set()
    assignments = [a for a in load_assignments(gh, f"{org}/{repo}")
                   if args.all or a["name"] in wanted]
    templates = [a.get("template") or f"{a['name']}-template" for a in assignments]
    templates = [t for t in templates if any(r["name"] == t for r in all_repos)]

    invitations = [v for v in gh.paginate(f"/orgs/{org}/invitations")
                   if (v.get("login") or "").lower() in users]

    # The push archive outlives the repositories on purpose, so it is only
    # cleared for the groups actually being deleted.
    # Laid out pushes/<assignment>/<group>.csv, so one recursive read, matched
    # on the group the file is named after.
    log_files = []
    try:
        info = gh.get(f"/repos/{org}/{args.log_repo}")
        tree = gh.get(f"/repos/{org}/{args.log_repo}/git/trees/"
                      f"{info.get('default_branch') or 'main'}?recursive=1")
        for t in tree.get("tree") or []:
            path = t["path"]
            if (t["type"] == "blob" and path.startswith("pushes/")
                    and path.endswith(".csv")
                    and os.path.basename(path)[:-4] in names):
                log_files.append({"path": path, "sha": t["sha"],
                                  "name": os.path.basename(path)})
    except GitHubError as e:
        if e.status not in (404, 409):
            raise

    # The class claims are one row per student, so only the rows belonging to
    # the groups being deleted go - the rest of the sheet is still wanted.
    claims, claims_keep = [], []
    numbers = {n for g in groups for n in g["students"]}
    raw = get_file(gh, org, repo, CLAIMS_PATH)
    if raw:
        for r in csv.DictReader(io.StringIO(raw.decode())):
            if (r.get("student") or "").strip() in numbers:
                claims.append(r)
            elif (r.get("student") or "").strip():
                claims_keep.append(r)

    return {"groups": groups, "repos": repos, "issues": issues,
            "assignments": assignments, "templates": templates,
            "invitations": invitations, "users": users, "log_files": log_files,
            "claims": claims, "claims_keep": claims_keep,
            # every repository that will be deleted, as the API described it -
            # `permissions` is what the preflight below reads
            "delete_targets": repos + [r for r in all_repos
                                       if r["name"] in set(templates)]}


def show(p, org, repo, args):
    total = 0
    def section(title, items, fmt):
        nonlocal total
        if not items:
            return
        total += len(items)
        print(f"\n{title} ({len(items)}):")
        for i in items:
            print(f"    {fmt(i)}")

    section("Teams to delete", p["groups"], lambda g: g["name"])
    section("Repositories to delete", p["repos"],
            lambda r: f"{r['name']:<34} last push {r.get('pushed_at') or '-'}")
    section(f"Issues to delete from {org}/{repo}", p["issues"],
            lambda i: f"#{i['number']} {i['title'][:60]}")
    section("Assignment definitions to delete", p["assignments"], lambda a: f"assignments/{a['name']}.json")
    section("Template repositories to delete", p["templates"], lambda t: t)
    section(f"Push archive files to delete from {org}/{args.log_repo}", p["log_files"],
            lambda f: f["path"])
    section(f"Class claims to remove from {CLAIMS_PATH}", p["claims"],
            lambda r: f"{r['student']} {r.get('used') or '-'} ({r.get('status') or '-'})")
    section("Pending org invitations to cancel", p["invitations"], lambda v: v.get("login") or v.get("email"))
    if args.remove_members and p["users"]:
        section("Members to remove from the org", p["users"], lambda u: u)
    return total


# What reset.py needs, in the names GitHub's pre-fill parameters use.
TOKEN_PERMISSIONS = {
    "administration": "write",   # deletes the repos
    "contents": "write",         # assignment definitions, push archive, claims
    "issues": "write",           # registration issues
    "members": "write",          # teams and invitations (an org permission)
}


def token_url(org, days=7):
    """The fine-grained token page with everything GitHub lets a URL fill in:
    name, description, owner, expiry and permissions.

    Repository access is the one thing it does not take, so "All repositories"
    stays a click - see token_help.
    """
    params = {
        "name": f"Reset ({org})",
        "description": f"reset.py for {org}: deletes test groups, repos and issues",
        "target_name": org,
        "expires_in": str(days),
        **TOKEN_PERMISSIONS,
    }
    # quote, not quote_plus, and parentheses encoded: terminals stop
    # auto-linking a URL at an unbalanced ")".
    return ("https://github.com/settings/personal-access-tokens/new?"
            + urllib.parse.urlencode(params, quote_via=urllib.parse.quote))


def classic_url(org):
    """The classic-token page with both scopes already ticked.

    Parentheses are percent-encoded: terminals stop auto-linking a URL at an
    unbalanced ")", which would hand you a truncated link.
    """
    return ("https://github.com/settings/tokens/new?scopes=repo,delete_repo"
            f"&description=Reset%20%28{org}%29")


def token_help(org, problems=(), offer_to_open=True):
    """What to do about a token that cannot delete repositories.

    GitHub has no API for creating tokens, so this is the one step that happens
    in a browser - the same bargain as 04_admin_token.sh in setup.
    """
    url, classic = token_url(org), classic_url(org)
    if problems:
        print("\nNothing has been deleted:")
        for why in problems:
            print(f"  - {why}")
    else:
        print(f"\nA token that can delete {org}'s repositories:")
    print(f"""
  1. A fine-grained token, which reaches {org} and nothing else. This link
     fills in the name, owner, a 7-day expiry and all four permissions:

       {url}

     The one thing GitHub will not take from a link is repository access, so
     click it yourself:

       Repository access    (o) All repositories

     "All", not "Only select": the group repos come and go during a semester,
     and the token has to reach whichever ones exist when you reset.

     Then Generate token, and run this again with it:

       GH_TOKEN=github_pat_... python scripts/reset.py {org} ...

     {org} has to allow fine-grained tokens (its Settings -> Personal access
     tokens), and as an owner you may have to approve your own request first.

  2. Or a classic token, one click and both scopes already ticked - but its
     scopes are account-wide, so delete_repo lets it delete any repository you
     administer, in any organization:

       {classic}

  3. Or lend the scope to the gh CLI token and take it back afterwards:

       gh auth refresh -h github.com -s delete_repo
       GH_TOKEN=$(gh auth token) python scripts/reset.py {org} ...
       gh auth refresh -h github.com --remove-scopes delete_repo
""")
    if not (offer_to_open and sys.stdin.isatty()):
        return
    try:
        if input("Open the fine-grained token page in your browser? [y/N] ") \
                .strip().lower() in ("y", "yes"):
            if not webbrowser.open(url):
                print("Couldn't open a browser; use the link above.")
    except (EOFError, KeyboardInterrupt):
        print()


def access_problems(gh, targets):
    """Why this token could not delete those repositories, before any of them is.

    A classic token advertises its scopes, so it is judged on those. A
    fine-grained one advertises nothing, and used to skip this check entirely -
    but the repository listing already says whether the token administers each
    one, which is the thing that actually decides it.
    """
    try:
        _, headers = gh.request("GET", "/user")
    except GitHubError:
        return []
    scopes = headers.get("X-OAuth-Scopes")
    if scopes is not None:
        if "delete_repo" in [x.strip() for x in scopes.split(",")]:
            return []
        return ["this is a classic token without the 'delete_repo' scope"]

    denied = sorted(r["name"] for r in targets
                    if not (r.get("permissions") or {}).get("admin"))
    if not denied:
        return []
    shown = ", ".join(denied[:4]) + (f" (+{len(denied) - 4} more)"
                                     if len(denied) > 4 else "")
    return [f"this token does not administer {shown}",
            "a fine-grained token needs Administration: Read and write on them"]


def confirm(org, total, args):
    """Two prompts, because everything below this point is irreversible."""
    if args.yes:
        return True
    if not sys.stdin.isatty():
        print("\nNot a terminal; re-run with --yes if you really mean it.", file=sys.stderr)
        return False
    print(f"\n{total} item(s). This cannot be undone.")
    try:
        if input("Delete them? [y/N] ").strip().lower() not in ("y", "yes"):
            return False
        typed = input(f"Type the organization name ({org}) to confirm: ").strip()
    except (EOFError, KeyboardInterrupt):
        print()
        return False
    if typed != org:
        print("Didn't match; nothing deleted.")
        return False
    return True


def main():
    ap = argparse.ArgumentParser(
        description="Delete test teams, repos, issues and assignments.")
    ap.add_argument("org", nargs="?",
                    help="defaults to the organization this checkout belongs to")
    ap.add_argument("--repo", default="registration", help="the registration repo")
    ap.add_argument("--all", action="store_true",
                    help="every group and assignment, not just the 999... test ones")
    ap.add_argument("--assignments", metavar="A,B",
                    help="also delete these assignments and their templates")
    ap.add_argument("--log-repo", default="push-log",
                    help="the push archive to clear matching rows from")
    ap.add_argument("--remove-members", action="store_true",
                    help="also remove those users from the organization")
    ap.add_argument("--token-help", action="store_true",
                    help="show how to get a token that can delete repositories, and stop")
    ap.add_argument("--dry-run", action="store_true", help="show the list and stop")
    ap.add_argument("--yes", action="store_true", help="skip both prompts (for scripts)")
    args = ap.parse_args()

    args.org = default_org(args.org)
    if not args.org:
        sys.exit("No organization given, and this folder is not a checkout of one.")
    if args.token_help:
        token_help(args.org)
        return
    gh = GitHub(os.environ.get("GH_TOKEN") or os.environ.get("ADMIN_TOKEN"))
    print(f"Reset {args.org}" + ("  [--all: EVERY group and assignment]" if args.all
                                 else "  [test artefacts only: student numbers 999...]"))
    p = plan(gh, args.org, args.repo, args)
    total = show(p, args.org, args.repo, args)

    if total == 0:
        print("\nNothing to delete.")
        return
    if args.dry_run:
        print("\n(dry run, nothing deleted)")
        return
    problems = access_problems(gh, p["delete_targets"]) if p["delete_targets"] else []
    if problems:
        token_help(args.org, problems)
        sys.exit(1)
    if not confirm(args.org, total, args):
        print("Cancelled.")
        return

    print()
    failed = 0

    for i in p["issues"]:
        try:
            gh.graphql(DELETE_ISSUE, {"id": i["node_id"]})
            print(f"deleted issue #{i['number']}")
        except GitHubError as e:
            failed += 1
            print(f"FAILED issue #{i['number']}: {e}", file=sys.stderr)

    for r in p["repos"] + [{"name": t} for t in p["templates"]]:
        try:
            gh.delete(f"/repos/{args.org}/{r['name']}")
            print(f"deleted repo {r['name']}")
        except GitHubError as e:
            failed += 1
            hint = ("  (a classic token needs the 'delete_repo' scope for this)"
                    if e.status == 403 else "")
            print(f"FAILED repo {r['name']}: {e}{hint}", file=sys.stderr)

    for g in p["groups"]:
        try:
            gh.delete(f"/orgs/{args.org}/teams/{g['slug']}")
            print(f"deleted team {g['name']}")
        except GitHubError as e:
            failed += 1
            print(f"FAILED team {g['name']}: {e}", file=sys.stderr)

    for a in p["assignments"]:
        path = f"assignments/{a['name']}.json"
        try:
            cur = gh.get(f"/repos/{args.org}/{args.repo}/contents/{path}")
            gh.delete(f"/repos/{args.org}/{args.repo}/contents/{path}",
                      {"message": f"Remove assignment {a['name']}", "sha": cur["sha"]})
            print(f"deleted {path}")
        except GitHubError as e:
            failed += 1
            print(f"FAILED {path}: {e}", file=sys.stderr)

    for f in p["log_files"]:
        try:
            gh.delete(f"/repos/{args.org}/{args.log_repo}/contents/{f['path']}",
                      {"message": f"Remove archive for {f['name'][:-4]}", "sha": f["sha"]})
            print(f"deleted {args.log_repo}/{f['path']}")
        except GitHubError as e:
            failed += 1
            print(f"FAILED {f['path']}: {e}", file=sys.stderr)

    if p["claims"]:
        try:
            buf = io.StringIO()
            w = csv.DictWriter(buf, fieldnames=CLAIM_FIELDS, lineterminator="\n")
            w.writeheader()
            w.writerows([{k: r.get(k, "") for k in CLAIM_FIELDS} for r in p["claims_keep"]])
            put_file(gh, args.org, args.repo, CLAIMS_PATH, buf.getvalue().encode(),
                     f"Remove class claims for {len(p['claims'])} student(s)")
            print(f"removed {len(p['claims'])} row(s) from {CLAIMS_PATH}")
        except GitHubError as e:
            failed += 1
            print(f"FAILED {CLAIMS_PATH}: {e}", file=sys.stderr)

    for v in p["invitations"]:
        try:
            gh.delete(f"/orgs/{args.org}/invitations/{v['id']}")
            print(f"cancelled invitation for {v.get('login') or v.get('email')}")
        except GitHubError as e:
            failed += 1
            print(f"FAILED invitation {v['id']}: {e}", file=sys.stderr)

    if args.remove_members:
        for u in p["users"]:
            try:
                gh.delete(f"/orgs/{args.org}/members/{u}")
                print(f"removed {u} from the org")
            except GitHubError as e:
                failed += 1
                print(f"FAILED removing {u}: {e}", file=sys.stderr)

    print("\nDone." if not failed else f"\n{failed} item(s) failed.")
    if p["assignments"]:
        print("Run `git pull` - assignment definitions were deleted on the remote.")
    print("Test student numbers live in the ROSTER secret and cannot be read back; "
          "re-run 05_roster.sh without --test-students to clear them.")
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    main()
