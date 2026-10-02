#!/usr/bin/env python3
"""End-to-end test against the live organization, driven by two student accounts.

It does what a group does - opens a registration issue, confirms it, pushes work,
runs into the deadlines - and checks the state that comes out at each step, so a
change to any part of the pipeline shows up as a failed assertion rather than as
a surprise in week three.

    export GH_TOKEN=$(gh auth token)      # an owner of the org
    export TEST_TOKEN_A=ghp_...           # a student account, NOT an owner
    export TEST_TOKEN_B=ghp_...           # a second one
    python scripts/e2e.py --classes ../classes

Each account needs a classic token with the `repo` scope, and must not be an
owner of the organization: the point is to prove the student's side works with a
student's permissions.

Everything it creates uses the 999xx student numbers, so `reset.py` with no
--all cleans it up; --cleanup does that for you. Nothing touches a real group.
"""
import argparse
import datetime
import os
import re
import subprocess
import sys
import tempfile
import time

from ghlib import (GitHub, GitHubError, default_org, get_file, load_groups,
                   repo_name, team_permission)

HERE = os.path.dirname(os.path.abspath(__file__))
NUM_A, NUM_B = "99901", "99902"
CONFIRM_MARK = "<!-- registration-bot:confirm-request -->"
TEAM = re.compile(rf"^g\d+_{NUM_A}_{NUM_B}$")

passed, failed = [], []


# ---------- harness ----------

def check(label, condition, detail=""):
    (passed if condition else failed).append(label)
    mark = "\033[32mPASS\033[0m" if condition else "\033[31mFAIL\033[0m"
    print(f"  [{mark}] {label}" + (f"  — {detail}" if detail else ""))
    return bool(condition)


def step(text):
    print(f"\n\033[1m{text}\033[0m")


def wait_for(what, fn, timeout=240, every=10):
    """Poll until fn() is truthy. Workflows take tens of seconds; a scheduled one
    can take minutes, so the timeouts here are generous on purpose."""
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            got = fn()
        except GitHubError:
            got = None
        if got:
            return got
        left = int(deadline - time.time())
        print(f"    waiting for {what}… {left}s left", end="\r", flush=True)
        time.sleep(every)
    print(" " * 60, end="\r")
    return None


def run(args, **kw):
    """A script, with the owner token, failing loudly."""
    env = dict(os.environ, **kw.pop("env", {}))
    out = subprocess.run([sys.executable, *args], capture_output=True, text=True, env=env)
    if out.returncode:
        print(out.stdout)
        print(out.stderr, file=sys.stderr)
    return out


def git(*args, cwd=None, token=None, url=None):
    cmd = ["git", *args]
    out = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True)
    if out.returncode:
        # Never echo the URL back: it carries the token.
        raise RuntimeError(f"git {args[0]} failed: {out.stderr.strip()[:300]}")
    return out.stdout


# ---------- the form a student would submit ----------

def issue_body(class_a, class_b, user_a, user_b):
    def field(label, value):
        return f"### {label}\n\n{value}\n\n"
    return (field("Member 1 - student number", NUM_A)
            + field("Member 1 - GitHub username", user_a)
            + field("Member 1 - class", class_a)
            + field("Member 2 - student number", NUM_B)
            + field("Member 2 - GitHub username", user_b)
            + field("Member 2 - class", class_b))


# ---------- phases ----------

def phase_accounts(a, b, org):
    step("1. The two test accounts")
    who_a = a.get("/user")["login"]
    who_b = b.get("/user")["login"]
    print(f"    A = {who_a}    B = {who_b}")
    for gh, who in ((a, who_a), (b, who_b)):
        role = None
        try:
            role = gh.get(f"/user/memberships/orgs/{org}").get("role")
        except GitHubError:
            pass
        check(f"{who} is not an owner of {org}", role != "admin",
              "a test that runs as an owner proves nothing about students")
    check("the two accounts are different", who_a != who_b)
    return who_a, who_b


def phase_secrets(owner, org, repo, classes_dir):
    step("2. Test numbers in the secrets")
    if not classes_dir:
        print("    no --classes, so the group will have no class (that path is tested too)")
        return False
    files = sorted(os.path.join(classes_dir, f) for f in os.listdir(classes_dir))
    out = run([os.path.join(HERE, "make_classes.py"), *files])
    if out.returncode:
        check("class listings readable", False, out.stderr.strip()[:120])
        return False
    # The real pairs plus the two test students: A matches its listing, B is
    # deliberately given a different one so the mismatch path is exercised.
    text = out.stdout.rstrip("\n") + f"\n{NUM_A},P1\n{NUM_B},P2\n"
    p = subprocess.run(["gh", "secret", "set", "CLASSES", "-R", f"{org}/{repo}"],
                       input=text, text=True, capture_output=True)
    return check("CLASSES includes the test students", p.returncode == 0,
                 f"{NUM_A}=P1, {NUM_B}=P2 (+{len(out.stdout.splitlines())} real)")


def phase_register(owner, a, b, org, repo, who_a, who_b, classes):
    step("3. Registration, as the students")
    # A leaves the class blank, which is what students are told to do: the bot
    # fills it in from the listing. B states one the listing disagrees with.
    body = issue_body("_No response_", "P3" if classes else "_No response_",
                      who_a, who_b)
    issue = a.post(f"/repos/{org}/{repo}/issues",
                   {"title": "Group registration", "body": body,
                    "labels": ["registration"]})
    n = issue["number"]
    check(f"{who_a} opened issue #{n}", True, issue["html_url"])

    def bot_asked():
        cs = owner.paginate(f"/repos/{org}/{repo}/issues/{n}/comments")
        return any(CONFIRM_MARK in c["body"] for c in cs) or None
    if not check("the workflow asked for confirmation", bool(wait_for("the bot", bot_asked)),
                 "issues:opened triggered register.yml"):
        return n, None

    b.post(f"/repos/{org}/{repo}/issues/{n}/comments", {"body": "/confirm"})
    check(f"{who_b} replied /confirm", True)

    def team_made():
        return next((g for g in load_groups(owner, org) if TEAM.match(g["name"])), None)
    group = wait_for("the team", team_made)
    if not check("the team was created", bool(group), group["name"] if group else ""):
        return n, None

    issue_now = owner.get(f"/repos/{org}/{repo}/issues/{n}")
    labels = {l["name"] for l in issue_now.get("labels", [])}
    check("the issue was closed", issue_now["state"] == "closed")
    check("it is labelled 'registered'", "registered" in labels)
    if classes:
        check("the class mismatch was labelled", "class-mismatch" in labels,
              f"{NUM_B} said P3, the listing says P2")
    return n, group


def phase_claims(owner, org, repo, classes):
    if not classes:
        return
    step("4. The class claims file")
    try:
        raw = owner.get(f"/repos/{org}/{repo}/contents/claims/classes.csv")
    except GitHubError:
        check("claims/classes.csv exists", False)
        return
    import base64
    text = base64.b64decode(raw["content"]).decode()
    check(f"{NUM_A}'s class was filled in from the listing",
          f"{NUM_A},,P1,P1,from list" in text, "they left the field blank")
    check(f"{NUM_B}'s claim is recorded as MISMATCH", f"{NUM_B},P3,P2,P3,MISMATCH" in text,
          "the claim wins; the disagreement is written down")


def phase_assignment(owner, org, repo, name, classes):
    step("5. An assignment with deadlines about to pass")
    soft = (datetime.datetime.now().astimezone()
            - datetime.timedelta(minutes=5)).strftime("%Y-%m-%d %H:%M")
    hard = (datetime.datetime.now().astimezone()
            + datetime.timedelta(minutes=2)).strftime("%Y-%m-%d %H:%M")
    out = run([os.path.join(HERE, "new_assignment.py"), org, name,
               "--soft", soft, "--hard", hard, "--repo", repo, "--no-announce"])
    if not check("the assignment was created", out.returncode == 0,
                 f"soft {soft} (past), hard {hard}"):
        return None, None
    return soft, hard


def phase_push(org, group, name, token_a, token_b, who_a, who_b):
    step("6. Pushing, as the students")
    repo = repo_name(group["name"], name)
    url = f"https://x-access-token:{token_a}@github.com/{org}/{repo}.git"
    with tempfile.TemporaryDirectory() as tmp:
        work = os.path.join(tmp, "repo")
        try:
            git("clone", "--quiet", url, work)
        except RuntimeError as e:
            check(f"{who_a} can clone {repo}", False, str(e)[:120])
            return None
        check(f"{who_a} can clone {repo}", True)

        for i, (token, who) in enumerate(((token_a, who_a), (token_b, who_b)), 1):
            path = os.path.join(work, f"work{i}.txt")
            with open(path, "w") as f:
                f.write(f"work from {who}\n")
            git("add", ".", cwd=work)
            git("-c", f"user.email={who}@users.noreply.github.com",
                "-c", f"user.name={who}", "commit", "--quiet",
                "-m", f"work from {who}", cwd=work)
            remote = f"https://x-access-token:{token}@github.com/{org}/{repo}.git"
            try:
                git("push", "--quiet", remote, "HEAD:main", cwd=work)
                check(f"{who} can push while the repo is open", True)
            except RuntimeError as e:
                check(f"{who} can push while the repo is open", False, str(e)[:120])
    return repo


def phase_carry_over(owner, org, repo, group, name, token_a):
    """The next assignment must start as a copy of this one, history included."""
    step("8. Carrying the group's work into the next assignment")
    nxt = f"{name}-next"
    out = run([os.path.join(HERE, "new_assignment.py"), org, nxt,
               "--soft-week", datetime.date.today().isoformat(), "--hard", "none",
               "--repo", repo, "--no-announce", "--carry-over", name])
    if not check("the follow-up assignment was created", out.returncode == 0,
                 (out.stderr or out.stdout).strip()[:160]):
        return
    check(f"it reports copying {name}", f"copied {name}" in out.stdout,
          out.stdout.strip().splitlines()[-3:][0] if out.stdout.strip() else "")

    src, dst = repo_name(group["name"], name), repo_name(group["name"], nxt)
    old = {c["sha"] for c in owner.paginate(f"/repos/{org}/{src}/commits")}
    new = {c["sha"] for c in owner.paginate(f"/repos/{org}/{dst}/commits")}
    check("every commit of the previous repo came across", bool(old) and old <= new,
          f"{len(old)} before, {len(new)} after")
    check("the students\' own work is in the new repo",
          get_file(owner, org, dst, "work1.txt") is not None)
    check("the new assignment\'s starting files are on top",
          get_file(owner, org, dst, "README.md") is not None)


def phase_lock(owner, org, repo, group, name, hard, token_a, who_a):
    step("9. The hard deadline")
    when = datetime.datetime.strptime(hard, "%Y-%m-%d %H:%M").astimezone()
    pause = (when - datetime.datetime.now().astimezone()).total_seconds() + 10
    if pause > 0:
        print(f"    waiting {pause:.0f}s for the hard deadline to pass")
        time.sleep(pause)

    out = run([os.path.join(HERE, "deadlines.py"), "--repo", repo.split("/")[-1]],
              env={"ORG": org})
    check("deadlines.py ran", out.returncode == 0, out.stderr.strip()[:120])

    group_repo = repo_name(group["name"], name)
    perm = wait_for("the lock",
                    lambda: team_permission(owner, org, group_repo, group["slug"]) == "pull")
    check("the team is read-only after the hard deadline", bool(perm),
          "permission is " + (team_permission(owner, org, group_repo, group["slug"]) or "none"))

    with tempfile.TemporaryDirectory() as tmp:
        work = os.path.join(tmp, "repo")
        url = f"https://x-access-token:{token_a}@github.com/{org}/{group_repo}.git"
        try:
            git("clone", "--quiet", url, work)
            with open(os.path.join(work, "late.txt"), "w") as f:
                f.write("too late\n")
            git("add", ".", cwd=work)
            git("-c", f"user.email={who_a}@users.noreply.github.com",
                "-c", f"user.name={who_a}", "commit", "--quiet", "-m", "too late", cwd=work)
            git("push", "--quiet", url, "HEAD:main", cwd=work)
            check(f"{who_a} is refused a push after the lock", False, "the push succeeded")
        except RuntimeError:
            check(f"{who_a} is refused a push after the lock", True)


def phase_archive(owner, org, repo, group, name, who_a, who_b):
    step("7. The push archive")
    out = run([os.path.join(HERE, "pushlog.py"), "--repo", repo.split("/")[-1]],
              env={"ORG": org})
    check("pushlog.py ran", out.returncode == 0, out.stderr.strip()[:120])
    path = f"pushes/{name}/{group['name']}.csv"
    try:
        raw = owner.get(f"/repos/{org}/push-log/contents/{path}")
    except GitHubError as e:
        check(f"{path} exists in push-log", False, str(e)[:80])
        return
    import base64
    text = base64.b64decode(raw["content"]).decode()
    check(f"{path} exists in push-log", True, f"{len(text.splitlines()) - 1} row(s)")
    check(f"{who_a}'s push is archived", who_a in text)
    check(f"{who_b}'s push is archived", who_b in text)
    check("a server-stamped push time is recorded", ",push," in text)


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("org", nargs="?", help="defaults to this checkout's organization")
    ap.add_argument("--repo", default="registration")
    ap.add_argument("--assignment", default="e2e-test")
    ap.add_argument("--classes", metavar="DIR",
                    help="class listings, so the class and mismatch paths are tested")
    ap.add_argument("--keep", action="store_true", help="leave everything in place")
    ap.add_argument("--cleanup", action="store_true", help="only tear the test down")
    args = ap.parse_args()

    org = default_org(args.org)
    token_a = os.environ.get("TEST_TOKEN_A")
    token_b = os.environ.get("TEST_TOKEN_B")
    owner = GitHub(os.environ.get("GH_TOKEN") or os.environ.get("ADMIN_TOKEN"))

    if args.cleanup:
        return cleanup(org, args)
    if not token_a or not token_b:
        sys.exit("TEST_TOKEN_A and TEST_TOKEN_B must be set to two student accounts' tokens.")

    a, b = GitHub(token_a), GitHub(token_b)
    print(f"\033[1mEnd-to-end test against {org}/{args.repo}\033[0m")

    who_a, who_b = phase_accounts(a, b, org)
    classes = phase_secrets(owner, org, args.repo, args.classes)
    n, group = phase_register(owner, a, b, org, args.repo, who_a, who_b, classes)
    if group:
        phase_claims(owner, org, args.repo, classes)
        soft, hard = phase_assignment(owner, org, args.repo, args.assignment, classes)
        if hard:
            phase_push(org, group, args.assignment, token_a, token_b, who_a, who_b)
            phase_archive(owner, org, args.repo, group, args.assignment, who_a, who_b)
            phase_carry_over(owner, org, args.repo, group, args.assignment, token_a)
            phase_lock(owner, org, args.repo, group, args.assignment, hard, token_a, who_a)

    print(f"\n\033[1m{len(passed)} passed, {len(failed)} failed\033[0m")
    for f in failed:
        print(f"  \033[31m✗\033[0m {f}")
    if not args.keep:
        cleanup(org, args)
    else:
        print(f"\nLeft in place. Tear down with:  python scripts/e2e.py --cleanup")
    sys.exit(1 if failed else 0)


def cleanup(org, args):
    step("Cleanup")
    out = run([os.path.join(HERE, "reset.py"), org, "--repo", args.repo,
               "--assignments", f"{args.assignment},{args.assignment}-next", "--yes"])
    print(out.stdout.strip()[-800:] or out.stderr.strip()[-800:])
    if args.classes:
        files = sorted(os.path.join(args.classes, f) for f in os.listdir(args.classes))
        real = run([os.path.join(HERE, "make_classes.py"), *files])
        if real.returncode == 0:
            subprocess.run(["gh", "secret", "set", "CLASSES", "-R", f"{org}/{args.repo}"],
                           input=real.stdout, text=True, capture_output=True)
            print("CLASSES restored to the real listings only")
    return 0


if __name__ == "__main__":
    main()
