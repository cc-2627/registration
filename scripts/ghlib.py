"""Minimal GitHub REST client and shared helpers (standard library only)."""
import base64
import csv
import datetime
import io
import json
import os
import re
import subprocess
import tempfile
import time
import urllib.error
import urllib.request
import zoneinfo

API = "https://api.github.com"

# Team names look like g07_61234_61789
TEAM_RE = re.compile(r"^g(\d+)_(\d+(?:_\d+)*)$")


class GitHubError(Exception):
    def __init__(self, status, body):
        super().__init__(f"HTTP {status}: {body[:500]}")
        self.status = status
        self.body = body


def _next_link(link_header):
    if not link_header:
        return None
    for part in link_header.split(","):
        if 'rel="next"' in part:
            return part[part.index("<") + 1 : part.index(">")]
    return None


class GitHub:
    def __init__(self, token):
        if not token:
            raise SystemExit("Missing GitHub token")
        self.token = token

    def request(self, method, path, data=None):
        url = path if path.startswith("http") else API + path
        body = None if data is None else json.dumps(data).encode()
        req = urllib.request.Request(url, data=body, method=method)
        req.add_header("Authorization", f"Bearer {self.token}")
        req.add_header("Accept", "application/vnd.github+json")
        req.add_header("X-GitHub-Api-Version", "2022-11-28")
        req.add_header("User-Agent", "group-registration-bot")
        if body is not None:
            req.add_header("Content-Type", "application/json")
        try:
            with urllib.request.urlopen(req, timeout=30) as resp:
                raw = resp.read()
                return (json.loads(raw) if raw else None), resp.headers
        except urllib.error.HTTPError as e:
            raise GitHubError(e.code, e.read().decode(errors="replace")) from None

    def get(self, path):
        return self.request("GET", path)[0]

    def post(self, path, data=None):
        return self.request("POST", path, data or {})[0]

    def put(self, path, data=None):
        return self.request("PUT", path, data or {})[0]

    def patch(self, path, data):
        return self.request("PATCH", path, data)[0]

    def delete(self, path, data=None):
        return self.request("DELETE", path, data)[0]

    def graphql(self, query, variables=None):
        out = self.request("POST", "https://api.github.com/graphql",
                           {"query": query, "variables": variables or {}})[0]
        if out and out.get("errors"):
            raise GitHubError(200, json.dumps(out["errors"]))
        return (out or {}).get("data")

    def exists(self, path):
        try:
            self.get(path)
            return True
        except GitHubError as e:
            if e.status == 404:
                return False
            raise

    def paginate(self, path):
        url = path + ("&" if "?" in path else "?") + "per_page=100"
        items = []
        while url:
            data, headers = self.request("GET", url)
            items.extend(data)
            url = _next_link(headers.get("Link"))
        return items


REMOTE_OWNER = re.compile(r"(?:[:/])([^/:]+)/[^/]+?(?:\.git)?/?$")


def org_from_remote(cwd=None):
    """The owner in this checkout's origin URL, or None.

    Covers git@host:owner/repo.git, https://host/owner/repo and the
    https://x-access-token:...@host/owner/repo form Actions checkouts use.
    """
    try:
        url = subprocess.run(["git", "remote", "get-url", "origin"],
                             cwd=cwd, capture_output=True, text=True,
                             timeout=10).stdout.strip()
    except (OSError, subprocess.SubprocessError):
        return None
    m = REMOTE_OWNER.search(url)
    return m.group(1) if m else None


def default_org(explicit=None):
    """Which organization to act on, in order of how explicit it is: an argument,
    then $ORG, then $REPO as Actions sets it, then the checkout you are standing
    in - so running any of these from the repo needs no org at all.
    """
    if explicit:
        return explicit
    if os.environ.get("ORG"):
        return os.environ["ORG"]
    repo = os.environ.get("REPO") or ""
    if "/" in repo:
        return repo.split("/", 1)[0]
    return org_from_remote() or org_from_remote(os.path.dirname(os.path.abspath(__file__)))


def sort_students(numbers):
    """Numeric sort of digit strings without int() (keeps leading zeros)."""
    return tuple(sorted(numbers, key=lambda s: (len(s), s)))


def repo_name(team_name, assignment):
    return f"{team_name}-{assignment}"


def load_groups(gh, org, ignore_users=()):
    """All registered groups, read back from the org's gXX_... teams."""
    ignore = {u.lower() for u in ignore_users}
    groups = []
    for t in gh.paginate(f"/orgs/{org}/teams"):
        m = TEAM_RE.match(t["name"])
        if not m:
            continue
        slug = t["slug"]
        users = {u["login"].lower() for u in gh.paginate(f"/orgs/{org}/teams/{slug}/members")}
        users |= {
            i["login"].lower()
            for i in gh.paginate(f"/orgs/{org}/teams/{slug}/invitations")
            if i.get("login")
        }
        groups.append({
            "name": t["name"],
            "slug": slug,
            "number": int(m[1]),
            "students": tuple(m[2].split("_")),
            "users": users - ignore,
        })
    return sorted(groups, key=lambda g: g["number"])


def ensure_repo(gh, org, name, template=None, empty=False):
    """Create a private repo: from the template if it exists, else with a README,
    or with nothing at all when `empty`, for a group pushing in work of its own
    (anything already there would make their push a conflict). Returns True if created."""
    if gh.exists(f"/repos/{org}/{name}"):
        return False
    if empty:
        gh.post(f"/orgs/{org}/repos", {"name": name, "private": True, "auto_init": False})
    elif template and gh.exists(f"/repos/{org}/{template}"):
        gh.post(f"/repos/{org}/{template}/generate",
                {"owner": org, "name": name, "private": True})
    else:
        gh.post(f"/orgs/{org}/repos", {"name": name, "private": True, "auto_init": True})
    return True


def push_own_work(org, repo):
    """The commands, as Markdown, that push work a group already has elsewhere
    into its empty assignment repository: every branch and tag, history and all."""
    return ("```\n"
            f"git remote add course https://github.com/{org}/{repo}.git\n"
            "git push course --all\n"
            "git push course --tags\n"
            "```\n")


def grant_team(gh, org, slug, repo, permission="push", attempts=6):
    """Give a team access to a repo, retrying while a fresh repo becomes visible."""
    for i in range(attempts):
        try:
            gh.put(f"/orgs/{org}/teams/{slug}/repos/{org}/{repo}", {"permission": permission})
            return
        except GitHubError as e:
            if e.status not in (404, 422) or i == attempts - 1:
                raise
            time.sleep(2 * (i + 1))


# ---------- assignments ----------

ASSIGNMENT_RE = re.compile(r"^[a-z0-9][a-z0-9_-]*$")


def assignments_dir(root=None):
    """The folder holding one JSON file per assignment, next to the scripts."""
    here = os.path.dirname(os.path.abspath(__file__))
    return os.path.join(root or os.path.dirname(here), "assignments")


def load_assignments(gh=None, repo=None, root=None):
    """Every assignment definition, oldest first.

    Given a client and a repo ("owner/name"), they are read from the repository,
    which is the only copy guaranteed to be current - a local checkout is behind
    the moment someone creates an assignment from another machine, and a stale one
    fails silently by simply not listing it. Without them, falls back to the
    assignments/ folder next to the scripts.
    """
    if gh is not None and repo:
        return _remote_assignments(gh, repo)
    d = assignments_dir(root)
    if not os.path.isdir(d):
        return []
    out = []
    for fn in sorted(os.listdir(d)):
        if not fn.endswith(".json"):
            continue
        with open(os.path.join(d, fn), encoding="utf-8") as f:
            a = json.load(f)
        a.setdefault("name", fn[:-5])
        out.append(a)
    return sorted(out, key=lambda a: (a.get("created", ""), a["name"]))


def _remote_assignments(gh, repo):
    try:
        items = gh.get(f"/repos/{repo}/contents/assignments")
    except GitHubError as e:
        if e.status == 404:
            return []
        raise
    out = []
    for it in items or []:
        if it.get("type") != "file" or not it["name"].endswith(".json"):
            continue
        blob = gh.get(f"/repos/{repo}/contents/{it['path']}")
        a = json.loads(base64.b64decode(blob["content"]))
        a.setdefault("name", it["name"][:-5])
        out.append(a)
    return sorted(out, key=lambda a: (a.get("created", ""), a["name"]))


# ---------- the course: course.json ----------

# Everything that changes from one course to the next lives in this one file at
# the top of the repo, written by scripts/setup/06_course.sh. The code never
# names a course, a timetable or a group size of its own.
COURSE_PATH = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                           "course.json")
WEEKDAYS = ["monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday"]


def load_course(path=COURSE_PATH):
    """The course's settings, with every optional field filled in.

        name        shown to students in the README and the form
        group_size  {"min": 2, "max": 2}
        timezone    deadlines are given and shown in this zone
        classes     {"P1": {"day": "tuesday", "ends": "16:00", "note": "..."}},
                    the weekly sessions soft deadlines are counted from; may be
                    empty, and then there is no class field anywhere
    """
    try:
        with open(path, encoding="utf-8") as f:
            c = json.load(f)
    except FileNotFoundError:
        raise SystemExit(f"No {os.path.basename(path)} - this checkout isn't set up for a "
                         "course yet. Run scripts/setup/06_course.sh.") from None
    size = c.get("group_size") or {}
    lo = int(size.get("min", 1))
    c["group_size"] = {"min": lo, "max": int(size.get("max", lo))}
    c.setdefault("name", "")
    c.setdefault("timezone", "UTC")
    c["classes"] = {k.upper(): v for k, v in (c.get("classes") or {}).items()}
    return c


def class_schedule(course):
    """{class: (weekday, "HH:MM")}, Monday being 0 - the shape rules.py uses."""
    return {k: (WEEKDAYS.index(v["day"].lower()), v["ends"])
            for k, v in course["classes"].items()}


# ---------- classes and the deadline rules ----------

def load_classes(value=None):
    """{student number: class} from the CLASSES secret, "61234,P1" per line.

    Only numbers and classes are ever in there - the listings the secret is built
    from have names in them, and make_classes.py leaves those behind.
    """
    text = os.environ.get("CLASSES", "") if value is None else value
    out = {}
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        parts = re.split(r"[,;\t]", line)
        if len(parts) >= 2 and parts[0].strip().isdigit():
            out[parts[0].strip()] = parts[1].strip().upper()
    return out


CLAIMS_PATH = "claims/classes.csv"


def load_claims(gh, repo):
    """{student number: the class they said they were in} from the repo.

    A claim beats the listing, so this is what the rules see. It is kept as a
    file rather than in a secret because its whole job is to be looked at.
    """
    try:
        raw = gh.get(f"/repos/{repo}/contents/{CLAIMS_PATH}")
    except GitHubError as e:
        if e.status == 404:
            return {}
        raise
    text = base64.b64decode(raw.get("content") or "").decode()
    out = {}
    for row in csv.DictReader(io.StringIO(text)):
        num = (row.get("student") or "").strip()
        claimed = (row.get("claimed") or "").strip().upper()
        if num.isdigit() and claimed:
            out[num] = claimed
    return out


# "claimed" is what the student typed, "official" what the listing says, "used"
# the one we went with. Saying nothing is the normal case: the class is filled in
# from the listing, which is the nearest thing to the form doing it itself.
CLAIM_FIELDS = ["student", "claimed", "official", "used", "status", "group",
                "issue", "recorded"]
SETTLED = ("ok", "from list")   # statuses that need nobody to look at them


def record_claims(gh, org, repo, rows):
    """Merge class claims into claims/classes.csv, newest wins per student.

    Returns the rows that disagree with the listing, so the caller can say so
    where somebody will see it.
    """
    existing = {}
    raw = get_file(gh, org, repo, CLAIMS_PATH)
    if raw:
        for r in csv.DictReader(io.StringIO(raw.decode())):
            if (r.get("student") or "").strip():
                existing[r["student"].strip()] = {k: r.get(k, "") for k in CLAIM_FIELDS}
    for r in rows:
        existing[r["student"]] = r

    buf = io.StringIO()
    w = csv.DictWriter(buf, fieldnames=CLAIM_FIELDS, lineterminator="\n")
    w.writeheader()
    w.writerows([existing[k] for k in sorted(existing, key=lambda x: (len(x), x))])
    put_file(gh, org, repo, CLAIMS_PATH, buf.getvalue().encode(),
             f"Record class claims for {rows[0]['group']}" if rows else "Record class claims")
    return [r for r in rows if r["status"] not in SETTLED]


class DeadlineContext:
    """What a deadline rule in rules.py is handed.

    Deliberately small: a rule should be readable by someone who has never seen
    the rest of this code.
    """

    def __init__(self, assignment, group, classes, schedule, tz, override=None):
        self.assignment = assignment
        self.group = group
        self.classes = classes          # {number: class}, this group's members only
        self.schedule = schedule
        self.tz = tz
        self.override = override
        self.notes = []

    def note(self, text):
        """Leave a note against this group in the report."""
        self.notes.append(text)

    def week_start(self):
        """Monday of the assignment's soft week, or None if it has none."""
        week = self.assignment.get("soft_week")
        if not week:
            return None
        d = datetime.date.fromisoformat(str(week)[:10])
        return d - datetime.timedelta(days=d.weekday())

    def session(self, class_):
        """When that class's session in the soft week ends, or None."""
        slot = self.schedule.get((class_ or "").upper())
        start = self.week_start()
        if not slot or start is None:
            return None
        weekday, hhmm = slot
        hour, minute = (int(x) for x in str(hhmm).split(":"))
        day = start + datetime.timedelta(days=int(weekday))
        return datetime.datetime(day.year, day.month, day.day, hour, minute,
                                 tzinfo=zoneinfo.ZoneInfo(self.tz))


def resolve_soft(rules, assignment, group, classes, claims=None):
    """The group's soft deadline per rules.py, plus whatever notes it left.

    A student's claimed class wins over the listing: the claim is what they told
    us at registration, and the listings go stale when someone cannot get onto
    their class on the faculty's platform. The disagreement is recorded instead
    of being resolved silently.
    """
    claims = claims or {}
    mine = {}
    for n in group.get("students", ()):
        t = claims.get(n) or classes.get(n)
        if t:
            mine[n] = t
    override = to_dt(assignment.get("soft_deadline")) if assignment.get("soft_manual") else None
    ctx = DeadlineContext(assignment, group, mine, rules.SCHEDULE, rules.TZ, override)
    try:
        soft = rules.soft_deadline(ctx)
    except Exception as e:                      # a broken rule must not lock nothing
        return to_dt(assignment.get("soft_deadline")), [f"rules.py failed: {e}"]
    return soft or to_dt(assignment.get("soft_deadline")), ctx.notes


def parse_deadline(value, tz="UTC"):
    """'2026-11-15 23:59' (in tz) or a full ISO string -> aware datetime."""
    if not value:
        return None
    value = value.strip().replace("/", "-")
    try:
        dt = datetime.datetime.fromisoformat(value)
    except ValueError:
        for fmt in ("%Y-%m-%d %H:%M", "%Y-%m-%d %H:%M:%S", "%Y-%m-%d"):
            try:
                dt = datetime.datetime.strptime(value, fmt)
                break
            except ValueError:
                continue
        else:
            raise ValueError(f"Can't read '{value}' as a date. Use 'YYYY-MM-DD HH:MM'.")
    if dt.tzinfo is None:
        try:
            dt = dt.replace(tzinfo=zoneinfo.ZoneInfo(tz))
        except Exception:
            raise ValueError(f"Unknown time zone '{tz}'.") from None
    return dt


def to_dt(value):
    """Parse a stored ISO timestamp (ours) or a GitHub '...Z' one."""
    if not value:
        return None
    return datetime.datetime.fromisoformat(value.replace("Z", "+00:00"))


def now():
    return datetime.datetime.now(datetime.timezone.utc)


def iso_z(dt):
    """UTC, ending in Z, for use in a URL query.

    An offset like +01:00 must never go into a query string raw: '+' there means a
    space, so the parameter arrives mangled and GitHub silently ignores the filter.
    """
    return dt.astimezone(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


# ---------- repo permissions ----------

def team_permission(gh, org, repo, slug):
    """The team's permission on the repo ('push', 'pull', ...) or None."""
    try:
        for t in gh.paginate(f"/repos/{org}/{repo}/teams"):
            if t["slug"] == slug:
                return t.get("permission")
    except GitHubError as e:
        if e.status == 404:
            return None
        raise
    return None


# ---------- pushing a folder ----------

SKIP_NAMES = {".git", ".DS_Store", "__pycache__", ".pytest_cache", "node_modules"}
MAX_BLOB = 10 * 1024 * 1024


def collect_files(folder):
    """(repo-relative path, absolute path) for everything worth uploading."""
    files = []
    for dirpath, dirnames, filenames in os.walk(folder):
        dirnames[:] = sorted(d for d in dirnames if d not in SKIP_NAMES)
        for fn in sorted(filenames):
            if fn in SKIP_NAMES:
                continue
            full = os.path.join(dirpath, fn)
            if os.path.islink(full) or not os.path.isfile(full):
                continue
            rel = os.path.relpath(full, folder).replace(os.sep, "/")
            files.append((rel, full))
    return files


def push_folder(gh, org, repo, folder, message):
    """Replace the default branch's tree with `folder`, in a single commit.

    Returns the number of files pushed. Uses the Git Data API, so no local
    git and no credentials beyond the token are needed.
    """
    files = collect_files(folder)
    if not files:
        raise ValueError(f"{folder} has no files to push.")

    info = gh.get(f"/repos/{org}/{repo}")
    branch = info.get("default_branch") or "main"
    try:
        ref = gh.get(f"/repos/{org}/{repo}/git/ref/heads/{branch}")
        parents = [ref["object"]["sha"]]
    except GitHubError as e:
        if e.status != 404:
            raise
        ref, parents = None, []

    tree = []
    for rel, full in files:
        size = os.path.getsize(full)
        if size > MAX_BLOB:
            print(f"  skipping {rel} ({size // 1024 // 1024} MB, over the API limit)")
            continue
        with open(full, "rb") as f:
            raw = f.read()
        blob = gh.post(f"/repos/{org}/{repo}/git/blobs",
                       {"content": base64.b64encode(raw).decode(), "encoding": "base64"})
        tree.append({
            "path": rel,
            "mode": "100755" if os.access(full, os.X_OK) else "100644",
            "type": "blob",
            "sha": blob["sha"],
        })
    if not tree:
        raise ValueError(f"Nothing in {folder} could be uploaded.")

    new_tree = gh.post(f"/repos/{org}/{repo}/git/trees", {"tree": tree})
    commit = gh.post(f"/repos/{org}/{repo}/git/commits",
                     {"message": message, "tree": new_tree["sha"], "parents": parents})
    if ref is None:
        gh.post(f"/repos/{org}/{repo}/git/refs",
                {"ref": f"refs/heads/{branch}", "sha": commit["sha"]})
    else:
        gh.patch(f"/repos/{org}/{repo}/git/refs/heads/{branch}",
                 {"sha": commit["sha"], "force": True})
    return len(tree)


def commit_files(gh, org, repo, files, message, modes=None):
    """Create or update several files in one commit, leaving the rest of the tree
    alone. `files` is {repo path: bytes}, `modes` an optional {repo path: git mode}
    for anything that is not a plain file. Returns the commit sha, or None if there
    was nothing to write.

    Unlike push_folder this builds on the current tree instead of replacing it, so
    it is safe on a repository that holds history worth keeping.
    """
    files = {k: v for k, v in files.items() if v is not None}
    if not files:
        return None

    info = gh.get(f"/repos/{org}/{repo}")
    branch = info.get("default_branch") or "main"
    try:
        ref = gh.get(f"/repos/{org}/{repo}/git/ref/heads/{branch}")
        parents = [ref["object"]["sha"]]
        base_tree = gh.get(f"/repos/{org}/{repo}/git/commits/{parents[0]}")["tree"]["sha"]
    except GitHubError as e:
        if e.status != 404:
            raise
        ref, parents, base_tree = None, [], None

    tree = []
    for path, raw in files.items():
        blob = gh.post(f"/repos/{org}/{repo}/git/blobs",
                       {"content": base64.b64encode(raw).decode(), "encoding": "base64"})
        tree.append({"path": path, "mode": (modes or {}).get(path, "100644"),
                     "type": "blob", "sha": blob["sha"]})

    body = {"tree": tree}
    if base_tree:
        body["base_tree"] = base_tree
    new_tree = gh.post(f"/repos/{org}/{repo}/git/trees", body)
    commit = gh.post(f"/repos/{org}/{repo}/git/commits",
                     {"message": message, "tree": new_tree["sha"], "parents": parents})
    if ref is None:
        gh.post(f"/repos/{org}/{repo}/git/refs",
                {"ref": f"refs/heads/{branch}", "sha": commit["sha"]})
    else:
        gh.patch(f"/repos/{org}/{repo}/git/refs/heads/{branch}", {"sha": commit["sha"]})
    return commit["sha"]


# ---------- carrying work from one assignment into the next ----------

# a1's boundary tag would otherwise ride along into a2, where it would both
# mislead and stop a2 from getting a boundary tag of its own.
CARRY_SKIP_TAGS = ("soft-deadline",)


def have_git():
    try:
        subprocess.run(["git", "--version"], capture_output=True, check=True)
        return True
    except (OSError, subprocess.CalledProcessError):
        return False


def _git(args, token, cwd=None):
    """Run git. The token travels in a URL, so scrub it out of any error."""
    out = subprocess.run(["git", *args], capture_output=True, text=True, cwd=cwd)
    if out.returncode:
        msg = (out.stderr or out.stdout).strip()
        raise RuntimeError((msg.replace(token, "***") if token else msg)[:500])
    return out.stdout


def repo_empty(gh, org, repo):
    """True for a repository that exists but has no commits yet."""
    try:
        gh.get(f"/repos/{org}/{repo}/commits?per_page=1")
        return False
    except GitHubError as e:
        if e.status == 409:   # "Git Repository is empty"
            return True
        raise


def clone_history(token, org, src, dst):
    """Copy every commit, branch and tag of one repository into another.

    Over git rather than the API, because the API cannot move objects between
    repositories, and a fork would tie the two together for good. Returns the
    source's default branch, or None if there was nothing committed to copy.

    Neither URL is ever printed: both carry the token.
    """
    auth = f"https://x-access-token:{token}@github.com"
    with tempfile.TemporaryDirectory() as tmp:
        work = os.path.join(tmp, "src.git")
        _git(["clone", "--bare", "--quiet", f"{auth}/{org}/{src}.git", work], token)
        if not _git(["rev-list", "-n", "1", "--all"], token, cwd=work).strip():
            return None
        for tag in CARRY_SKIP_TAGS:
            subprocess.run(["git", "tag", "-d", tag], cwd=work, capture_output=True)
        # Explicit refspecs, not --mirror: a bare clone also picks up GitHub's
        # hidden refs/pull/* and pushing those is refused.
        _git(["push", "--quiet", f"{auth}/{org}/{dst}.git",
              "refs/heads/*:refs/heads/*", "refs/tags/*:refs/tags/*"], token, cwd=work)
        return _git(["symbolic-ref", "--short", "HEAD"], token, cwd=work).strip() or None


def repo_paths(gh, org, repo, ref=None):
    """Every file path on a branch, as a set. One request."""
    branch = ref or (gh.get(f"/repos/{org}/{repo}").get("default_branch") or "main")
    try:
        tree = gh.get(f"/repos/{org}/{repo}/git/trees/{branch}?recursive=1")
    except GitHubError as e:
        if e.status in (404, 409):
            return set()
        raise
    return {t["path"] for t in (tree.get("tree") or []) if t.get("type") == "blob"}


def repo_files(gh, org, repo, ref=None):
    """{path: (bytes, git mode)} for every file on a branch.

    One request per file, so this is for skeletons and other small trees - a
    group's own repository is copied with clone_history instead.
    """
    branch = ref or (gh.get(f"/repos/{org}/{repo}").get("default_branch") or "main")
    try:
        tree = gh.get(f"/repos/{org}/{repo}/git/trees/{branch}?recursive=1")
    except GitHubError as e:
        if e.status in (404, 409):
            return {}
        raise
    out = {}
    for t in tree.get("tree") or []:
        if t.get("type") != "blob":
            continue
        blob = gh.get(f"/repos/{org}/{repo}/git/blobs/{t['sha']}")
        if blob.get("encoding") != "base64":
            print(f"  skipping {t['path']} ({blob.get('size', '?')} bytes, too big "
                  f"to read back through the API)")
            continue
        out[t["path"]] = (base64.b64decode(blob["content"]),
                          t.get("mode") or "100644")
    return out


# ---------- single files, committed straight to a branch ----------

def get_file(gh, org, repo, path, branch=None):
    """The file's bytes, or None if it isn't there."""
    url = f"/repos/{org}/{repo}/contents/{path}" + (f"?ref={branch}" if branch else "")
    try:
        cur = gh.get(url)
    except GitHubError as e:
        if e.status == 404:
            return None
        raise
    if not isinstance(cur, dict) or "content" not in cur:
        return None
    return base64.b64decode(cur["content"])


def put_file(gh, org, repo, path, data, message, branch=None):
    """Create or update one file in a single commit. Returns the commit, or None
    if the file was already byte-identical."""
    body = {"message": message, "content": base64.b64encode(data).decode()}
    if branch:
        body["branch"] = branch
    url = f"/repos/{org}/{repo}/contents/{path}" + (f"?ref={branch}" if branch else "")
    try:
        cur = gh.get(url)
        if isinstance(cur, dict) and cur.get("sha"):
            if base64.b64decode(cur.get("content") or "") == data:
                return None
            body["sha"] = cur["sha"]
    except GitHubError as e:
        if e.status != 404:
            raise
    return gh.put(f"/repos/{org}/{repo}/contents/{path}", body)
