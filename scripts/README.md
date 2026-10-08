# bedel — the staff guide

The tooling behind the registration repo: setup, assignments, deadlines and
teardown. Students never need to read this — their page is `.github/README.md`, which
GitHub shows in place of bedel's own README once a course is set up.

Nothing here names a course. Everything that differs between courses — the name, group
size, time zone and weekly classes — is in `course.json` at the top of the repo, and the
students' README and issue form are generated from it. The one file you might also edit
is `rules.py`, if your deadline policy differs (see *Writing your own deadline rules*).

Students open an issue from a form and list their student numbers and GitHub usernames.
A GitHub Action validates the issue against the course roster and waits for every member
to reply `/confirm`. It then creates the team `gXX_<n1>_<n2>` (sending org invites) and
gives it write access to one repository per assignment.

Assignments are created when you release them, not up front: you hand a folder to
`new_assignment.py` and every registered group gets `gXX_<n1>_<n2>-<assignment>` seeded
from it. Each assignment carries a soft and a hard deadline — late after the soft one is
allowed and recorded, the hard one makes the repositories read-only.

Every script takes the organization from this checkout's `origin` remote, so the
examples below never spell it out. Pass one explicitly (`reset.py other-org`, `ORG=…`)
only to act on a different organization.

## Starting a new course

Make an organization for the course (one per course edition keeps them apart), then:

```
git clone https://github.com/ajccosta/bedel registration
cd registration
./scripts/setup.sh ORG
```

Step 2 creates `ORG/registration`, makes it this checkout's `origin` and pushes. bedel
itself becomes the `upstream` remote, which is where fixes come from later (see *Getting
bedel's updates*). Step 6 asks for the course details and writes `course.json`. Until that
file is on the default branch the workflows start, see there is no course, and stop, so a
half-set-up repo never fails or creates anything.

**Or in a browser:** [the setup page](https://ajccosta.github.io/bedel/setup.html) does the same
steps through GitHub's API: organization permissions, the repository (created from bedel's
template), labels, the roster and class secrets, the bot's token (checked with the same
probes as step 4) and `course.json`, and then publishes the course's own page (see
*The course's page*). It runs entirely in the browser; student lists are parsed there and
only the numbers are sent, encrypted. It needs two fine-grained tokens, one for itself
(7 days) and one for the bot, both from pre-filled links, and asks for them only once
the organization exists, so that both are made for it alone. It refuses classic tokens,
and proves a token belongs to the organization by asking it to create a repository there
with an empty request: GitHub answers 422 (malformed, nothing created) to a token that
may, 403 to one that may not, and a fine-grained token has exactly one owner.

GitHub's **Use this template** button, which is what the page uses, starts the copy's
history afresh, so pulling bedel's updates later means merging two unrelated histories
by hand. A clone keeps them related.

## Setup (once per course edition)

```
./scripts/setup.sh ORG
```

That walks through all seven steps below, asking before each one, so you can skip
what's already done and re-run what you want:

```
── Step 1: Organization member privileges
   Base permissions to none; members can't create repos or teams.
   Run step 1? [Y/n/q]
```

`y` runs it, `n` skips it, `q` stops. Every step reads the current state first and
only changes what differs, so re-running is harmless. Useful flags:

| | |
|---|---|
| `--only 1,3` | run just those steps (still asks before each) |
| `--from 4` | start at step 4 |
| `--roster students.csv` | student CSV for step 5, instead of being asked for it |
| `--yes` | run everything unattended |

Each step is also a standalone script under `scripts/setup/`, so you can run one on
its own. All of them take the org as the first argument and have a `--help`.

1. **Organization member privileges** — `scripts/setup/01_org_settings.sh ORG`
   Base permissions to **none**, and members may not create repos, create teams,
   delete repos or change repo visibility. Only the bot does those. Shows
   `current → wanted` and asks before touching anything; `--dry-run` just shows.
   A setting your org's plan doesn't support is reported and the rest still apply.

2. **Create the public repo and push** — `scripts/setup/02_create_repo.sh ORG`
   It has to be public so students who aren't org members yet can open issues.
   Nothing is ever force-pushed; if the repo already exists you are offered a plain
   push of the current branch instead.

3. **Create the two labels** `registration` and `registered` —
   `scripts/setup/03_labels.sh ORG`
   The issue form applies `registration` itself, so it has to exist before the first
   student submits. Labels that already exist are left alone, colours included.

4. **Admin token** — `scripts/setup/04_admin_token.sh ORG [--days N]`
   GitHub has no API for creating tokens, so this is the one step with a browser in
   it. The script offers to open a **fine-grained** token page with the name, `ORG` as
   owner, the expiry (180 days by default; `--days` changes it, up to 366) and the four
   permissions already filled in: Administration, Contents, Issues, Commit statuses and
   Workflows (repository) and Members (organization), all read and write. Issues is for
   the issue that announces an assignment in each group's repo; Workflows for starting
   files or carried-over work that contain workflow files.

   One field you set by hand: **Repository access → All repositories**. GitHub's
   pre-fill link has no parameter for it, and it must be All, since only All covers
   the repos the bot creates later in the semester.

   It then reads the token without echoing it and checks it before storing it as
   `ORG_ADMIN_TOKEN`: it belongs to an org owner, each permission is there (an
   empty-bodied write, which GitHub refuses with 403 when the permission is missing
   and 422 when it is present, so nothing is ever created), and it reaches every repo
   in `ORG`. `ORG` has to allow fine-grained tokens. `--classic` gives you the old
   `admin:org` + `repo` token instead, which works on every org you own.

5. **Roster** — `scripts/setup/05_roster.sh --csv students.csv`
   Extracts the student numbers with `make_roster.py` and stores them as the `ROSTER`
   secret. Student numbers only, so no names or status leave your machine, and
   nothing is written to a temporary file. Re-run whenever enrolment changes.

   Add `--test-students 2` to also allow the fake numbers `99901`, `99902`, … so you can
   register a test group with your own GitHub accounts without borrowing a real student's
   number. Re-run without the flag to drop them again.

   The same step reads the **class listings** — the per-class exports, looked for in
   `classes/` next to the CSV, or wherever `CLASSES_DIR` points — and stores number/class
   pairs as the `CLASSES` secret. Again, numbers only. Without it everything still works;
   every group simply falls back to the assignment's own soft deadline.

6. **The course** — `scripts/setup/06_course.sh`
   Asks for the course name, the group size, the time zone deadlines are in, and the
   weekly classes (Enter keeps what is there), and writes them to `course.json`:

   ```json
   {
     "name": "Cloud Computing",
     "group_size": {"min": 2, "max": 2},
     "timezone": "Europe/Lisbon",
     "classes": {
       "P1": {"day": "tuesday", "ends": "16:00", "note": "14:00-16:00, Lab 2"},
       "P2": {"day": "thursday", "ends": "13:00"}
     },
     "number_hint": "your *Nº*"
   }
   ```

   `classes` are the sessions soft deadlines are counted from: `ends` is when the session
   finishes. A course without classes leaves it empty; then the form has no class field,
   and every group gets the assignment's own soft deadline. `number_hint` is optional:
   what students call their number, shown on the form.

   From that it generates `.github/README.md` (the students' page, with a link to the
   course's page once that is published),
   `.github/ISSUE_TEMPLATE/register.yml` (the form) and `config.yml` (no blank issues),
   then offers to commit and push all four, because the bot only runs what is on the
   default branch. Every question has a flag for unattended runs, e.g.
   `--name "…" --min 2 --max 3 --timezone Europe/Lisbon --class "P1 tuesday 16:00"`; see
   `--help`. Assignments are not configured here — see below.

7. **Verify and test** — `scripts/setup/07_verify.sh ORG`
   Read-only. Checks the org privileges, the repo and its visibility, both labels, both
   secrets, and that `register.yml`, `course.json`, the issue form and the students'
   README are on the **default branch** —
   Actions only ever runs what is committed there, so an uncommitted fix changes nothing.
   Then prints the link to post for students:
   `https://github.com/ORG/registration/issues/new?template=register.yml`

   `deadlines.yml` is treated more leniently: until you have created an assignment it has
   no work to do, so it is reported as a note (`·`) and doesn't fail the check. Once any
   `assignments/*.json` is on the branch, a missing `deadlines.yml` becomes a real problem
   — nothing would ever be locked.

   Test with two accounts of your own first: run step 5 with `--test-students 2`,
   register a group using `99901`/`99902`, reply `/confirm` from the second account, then
   delete the team and repos it created.

## Assignments

**From GitHub:** the *Release an assignment* workflow in the course repository's **Actions**
tab (or the course's page, which runs it for you) does everything below with the bot's token.
Its form asks for the name, the repository holding the starting files (default
`<name>-template`), the deadlines and an optional assignment to carry over from, or
whether groups bring their own work instead; tick *dry run* to see what would happen. The course's page can upload a folder of starting files
for you.

**From a terminal:** create one when you release it. Nothing about assignments is set up in advance, so you
don't need to know how many there will be.

```
GH_TOKEN=$(gh auth token) python scripts/new_assignment.py a3 \
    --from ./skeletons/a3 \
    --soft "2026-11-15 23:59" --hard "2026-11-22 23:59"
```

That one command does five things:

1. creates `ORG/a3-template` and pushes the contents of `./skeletons/a3` into it, as a
   single commit, and marks it a template repository;
2. creates `gXX_<numbers>-a3` for every registered group, seeded from that template;
3. gives each group write access to its own repo;
4. opens an issue in each group's repo @-mentioning its members, which is what emails
   them (see below);
5. commits `assignments/a3.json`, holding both deadlines, to the registration repo.

Step 4 goes straight to the default branch over the API, so the deadlines are live when
the command returns — there is nothing to remember to push. Run `git pull` afterwards to
bring your own checkout up to date. (`--local-only` writes the file next to you instead,
for when you'd rather review it before it counts.)

**A new assignment must be given both deadlines.** Leave one out and the command refuses;
pass the word `none` for one you genuinely don't want, so it's a decision rather than an
oversight:

```
--soft "2026-11-15 23:59" --hard none      # never locked
```

Deadlines are read in the course's time zone (`course.json`) unless you pass `--tz`. `--dry-run` shows what
would happen. Re-running is safe and is how you change things later: existing repos are
left alone, the template is only overwritten if you pass `--from` again, and a deadline
you leave out keeps whatever it has — so `--hard "2026-11-29 23:59"` on its own just
moves that one date.

### Carrying work into the next assignment

If a3 continues from a2, `--carry-over` starts each group's new repository as a copy of
their old one instead of a bare template:

```
GH_TOKEN=$(gh auth token) python scripts/new_assignment.py a3 \
    --from ./skeletons/a3 --carry-over a2 \
    --soft-week 2026-11-10 --hard "2026-11-22 23:59"
```

Each `gXX_<numbers>-a3` then starts with every commit, branch and tag of that group's
`gXX_<numbers>-a2`, with the contents of `./skeletons/a3` committed on top. Students clone
one repository and their work is there, history and all; nobody copies files between
repos by hand, and the announcement issue tells them it happened.

Worth knowing:

- **The starting files win.** A template file that collides with one of theirs replaces
  it — it is the statement for the new assignment — but their version is still there, one
  commit back. Collisions are printed per group and named in the announcement issue.
- It only applies to repositories it creates. Re-running changes nothing, and a group
  that registers later gets a plain repo from the template, which is right: they have
  nothing to carry.
- The `soft-deadline` tag is left behind on purpose, so it can't be mistaken for the new
  assignment's boundary. Their own tags and branches do come across.
- It goes through `git clone`, so git must be on your PATH and `GH_TOKEN` must be set —
  it authenticates the push. The token only travels inside the URL, and is scrubbed out
  of any error.
- Do it after the previous hard deadline if you can. The copy happens once, when the repo
  is created; anything pushed to the old repo afterwards does not follow it.
- A repository left half-copied by a run that died is finished by the next run, rather
  than mistaken for one that is already done.
- The push archive for the new assignment also lists the commits that came across,
  recorded as pushed by you at copy time. When the students actually pushed them is still
  in the archive under the previous assignment, which is the record that counts.

### Work students already started elsewhere

When students have already been working in repositories of their own, before bedel or
outside it, `--own-work` lets them bring that work in instead of starting from files you
provide (on the course's page: tick *Groups bring their own work*):

```
GH_TOKEN=$(gh auth token) python scripts/new_assignment.py a1 --own-work \
    --soft-week 2026-10-06 --hard "2026-10-25 23:59"
```

Each group's `gXX_<numbers>-a1` is created **empty**, and the announcement issue gives them
the commands to run in a clone of the repository they've been working in:

```
git remote add course https://github.com/ORG/gXX_<numbers>-a1.git
git push course --all
git push course --tags
```

Every branch, tag and commit comes across, with the original dates. From then on they push
there, and deadlines, locking and the push archive work as for any other assignment. A
group that hasn't started just clones the empty repository and begins.

Worth knowing:

- bedel can't copy their repositories for them: those belong to the students' own
  accounts, usually private, and the bot's token reaches only the course's organization.
- The repository has to be empty for that push to go through, so there are no starting
  files and nothing to carry over; the option rules both out.
- A group that registers later gets an empty repository too, and the same commands in the
  comment that confirms its registration.
- Their earlier pushes, to their own repository, aren't in the push archive. It records
  the commits they bring in as pushed when they bring them in. The commit dates say when
  the work was done, but those come from the students' machines and can be set by hand.
- Re-running to move a deadline keeps the option; you don't need to pass it again.

### Telling students it's out

The bot has no email addresses — the roster is student numbers and nothing else, which is
the point. So notification goes through GitHub: creating an assignment opens an issue in
each group's own repository that @-mentions both members and states the two deadlines.
GitHub emails everyone mentioned, whatever they have the repo watched as, so it reaches
them without any mailing list to maintain.

It is idempotent — a hidden marker in the issue body means re-running `new_assignment.py`
never posts a second time. `--no-announce` skips it.

Two things to know:

- A student who has not yet **accepted the org invitation** can't see the repo, so the
  mention doesn't reach them. They get it once they accept. For the first assignment of
  the semester, check that everyone is in the org first.
- Changing a deadline later does **not** send anything. Re-run with a new `--hard` and
  then tell them yourself, or close the issue so the next run posts a fresh one.

### Soft and hard deadlines

| | |
|---|---|
| **soft** | nothing is enforced. The report shows who pushed after it, so you can take off the points. |
| **hard** | `.github/workflows/deadlines.yml` switches the team's access from write to read. Students keep seeing the repo, but can no longer push. |

Lateness is measured with the repository's `pushed_at`, which GitHub sets server-side on
every push — unlike a commit date, a student cannot backdate it. The measurement is
therefore exact no matter when the lock lands.

**Locking to the minute.** GitHub's shortest cron interval is 5 minutes and scheduled runs
are regularly delayed under load, so no schedule alone can lock punctually. Instead the
workflow runs every 30 minutes and, when a hard deadline falls within the next 35, the run
*waits* for the exact moment and locks then (`deadlines.py --wait 35`). So a deadline at
23:59:00 is enforced at 23:59:05, not at half past midnight.

A run that is delayed past the deadline locks immediately on arrival, and the report still
shows the true lateness either way. The waiting job holds a runner for up to 35 minutes —
free on a public repo, and `timeout-minutes: 90` stops it ever hanging.

### Classes, and who gets which deadline

Hard deadlines are the same for everybody. Soft ones follow each class's timetable: a
class gets a week from *its own* session, so nobody is asked to hand in work a week after
a class they had not had yet.

```
new_assignment.py a3 --soft-week 2026-11-03 --hard "2026-11-29 23:59"

  P1  Tue 11-03 16:00  ->  soft Tue 11-10 16:00
  P2  Thu 11-05 13:00  ->  soft Thu 11-12 13:00
  P3  Tue 11-03 13:00  ->  soft Tue 11-10 13:00
```

`--soft-week` takes **any** date in the week whose sessions start the clock, and is
required for a new assignment. `--soft "2026-11-20 23:59"` instead sets one deadline for
everyone and turns the per-class rule off for that assignment.

Each class's soft deadline ends when its session ends, a week on. Add `--soft-time 23:59`
to end it at that time instead, on the same day (*Ending at* on the course page):

```
new_assignment.py a3 --soft-week 2026-11-03 --soft-time 23:59 --hard "2026-11-29 23:59"

  P1  Tue 11-03 16:00  ->  soft Tue 11-10 23:59
  P2  Thu 11-05 13:00  ->  soft Thu 11-12 23:59
```

The time is kept when the assignment is re-run, until `--soft-time session` goes back to
the session's end.

**A group with members in different classes** is held to the *earliest* of their sessions,
and the report says `mixed classes P1,P2` next to it. **A group whose classes are unknown**
falls back to the assignment's stored soft deadline, which is the latest class's — missing
data never costs a student time — and the report says `no class known`.

**Students normally say nothing.** The class field on the form is optional and the form
tells them to leave it alone: they write their number, and the bot fills the class in from
the listing when it processes the issue. An issue form is static YAML — there is no
scripting in it, so a field genuinely cannot react to what someone types — and this is the
nearest thing to it reacting. The comment the group gets back names each member's class
and where it came from, which is the moment to object:

```
Your class, which your soft deadlines follow:
- `61234` → **P1** — from the class list
- `61789` → **P3** — as you said, though the list has you elsewhere
```

**A student who picks one anyway wins.** Someone who could not get onto their real class
on the faculty platform would otherwise be held to a class they do not attend. Every
member is written to `claims/classes.csv` in this repo — what they said, what the listing
says, and the one actually used:

```
student,claimed,official,used,status,group,issue,recorded
61234,,P1,P1,from list,g01_61234_61789,7,2026-11-02T10:04:11+00:00
61789,P3,P2,P3,MISMATCH,g01_61234_61789,7,2026-11-02T10:04:11+00:00
```

`ok` and `from list` need nobody to look at them. `MISMATCH` (they disagree),
`not in any listing` (they picked one but are on no list) and `no class known` (we have
nothing at all) each put the `class-mismatch` label on the registration issue and are
spelled out in the group's comment. Deadlines follow the claim either way; the label is
your cue to check it.

One warning from the listings this was built against: **the class a file declares inside
disagreed with its filename** (the file named `p1` said `Turno: P2`). `make_classes.py` therefore
reads the header line and ignores the filename entirely. Getting that backwards would hand
two whole classes each other's deadlines.

### Writing your own deadline rules

`scripts/rules.py` is the only file that decides soft deadlines, and it is meant to be
edited. The timetable itself is in `course.json` (step 6); `rules.py` reads it as
`SCHEDULE`, `{class: (weekday, session end)}` with Monday as 0, and holds one function:

```python
AFTER_SESSION = datetime.timedelta(days=7)

def soft_deadline(ctx):
    if ctx.override:                       # --soft was used; respect it
        return ctx.override
    if not SCHEDULE:                       # a course without classes
        return None
    sessions = [s for s in (ctx.session(t) for t in set(ctx.classes.values())) if s]
    if not sessions:
        ctx.note("no class known")
        return None                        # fall back to the assignment's own
    return min(sessions) + AFTER_SESSION   # earliest member's session wins
```

`ctx` gives you the assignment, the group, `ctx.classes` as `{number: class}`,
`ctx.session(class)` for that class's session in the assignment's soft week, `ctx.override`
and `ctx.note(text)` to leave something in the report. Return a datetime, or `None` to use
the assignment's own soft deadline.

Want the latest member's session instead of the earliest? `max` instead of `min`. Two
weeks for one class that lost a class to a holiday? Special-case it. A rule that raises is
caught, the assignment's own deadline is used, and the report says `rules.py failed: …`
rather than quietly locking nothing.

### Marking late commits

A commit message can't be prefixed after the fact — changing one rewrites history and
needs a force-push into the student's repo, which changes every SHA and breaks their
clones. GitHub's non-destructive equivalent is a **commit status**, and that is what the
bot sets:

```
✗ deadline/soft — Pushed after the soft deadline - counts as late
```

It shows next to the commit in the repo's commit list and on the commit page, so late
work is obvious while browsing, exactly where a prefix would have been.

It also creates a `soft-deadline` tag on the last commit that made it in on time, which
makes the boundary usable from a terminal while marking:

```
git fetch --tags
git log soft-deadline..HEAD        # everything that arrived late
git diff soft-deadline..HEAD       # what changed after the deadline
```

**When it runs.** Automatically at the moment a repo is locked by its hard deadline —
the history is final then, so it is marked once and stays correct. Before that, or for an
assignment with no hard deadline, run it yourself:

```
GH_TOKEN=$(gh auth token) python scripts/deadlines.py --mark-late
GH_TOKEN=$(gh auth token) python scripts/deadlines.py --mark-late a3
```

That only marks, it never locks. Already-marked commits are skipped, so re-running is
cheap and safe.

**What is evidence and what is decoration.** Anything written *into* a group repo is
decoration: a student with push access can write commit statuses themselves (the Statuses
API needs write access, which is exactly what they have), and they own the history, so
markers and tags can be changed by them. Commit dates are no better — `git commit --date`,
or `GIT_COMMITTER_DATE`, sets whatever you like, and a rebase rewrites dates wholesale.

The evidence is the repository's `pushed_at`: GitHub stamps it on every push, it is not
part of the repo's contents, and nobody can write it. That is the only thing the report
uses to decide `on time` / `late`, so grade from the report — the job summary or
`--report --csv`, both of which live where students cannot write.

This is also why marking runs **at lock time** by default. Once the hard deadline drops
the team to read-only, they can no longer write statuses, so what is stamped then stays
stamped. `--mark-late` run earlier is the one case where a student could later paint
over it.

### Who met which deadline

```
git pull        # the reports read assignments/ from your checkout
GH_TOKEN=$(gh auth token) python scripts/deadlines.py --report
GH_TOKEN=$(gh auth token) python scripts/deadlines.py --report a3 --csv > a3.csv
```

The same table is written to the job summary every time the deadline workflow runs — open
*Actions → Assignment deadlines → (latest run)* to read it without leaving the browser.
*Run workflow* there gives you a report on demand, with a *Report without locking* tick box
(which also skips the waiting, so it returns straight away).

| group | last push | soft | hard | state |
|---|---|---|---|---|
| g01_61234_61789 | 2026-11-15T22:04:11+00:00 | ✅ on time | ✅ on time | read-only |
| g02_61111_61222 | 2026-11-19T01:30:52+00:00 | ⚠️ late | ✅ on time | read-only |

### The push archive

`pushed_at` says when a repository was *last* pushed to — one fact per repo, overwritten
by the next push. For the per-push record, `scripts/pushlog.py` copies GitHub's Events
API into **`<org>/push-log`**, a private repository created on first use, laid out one
file per group per assignment:

```
pushes/a3/g01_61234_61789.csv
pushes/a3/g02_61002_61044.csv
pushes/a4/g01_61234_61789.csv
```

One row per commit, carrying the push that delivered it and the commit message:

| column | |
|---|---|
| `pushed_at` | when GitHub received the push — **server-set, cannot be faked** |
| `actor` | which member pushed |
| `sha`, `message`, `author`, `committed_at` | the commit, as the commit says it is |
| `source` | `push` if an event recorded it, `commit-scan` if only the sweep saw it |

```bash
GH_TOKEN=$(gh auth token) python scripts/pushlog.py --show a3 > a3-pushes.csv
```

The deadlines workflow runs it every 30 minutes, before the step that may sit waiting for
a deadline, and with `continue-on-error` so a failed archive can never stop a lock. It
costs nothing: the archive repo runs no Actions of its own, and the job writing to it runs
in this public repository.

**Why there are two sources.** The Events API is the only place GitHub stamps a per-push
time itself, but it is lossy — measured on this org, four of five web-editor commits in
one repository produced no event at all, and another repository produced none whatsoever.
So each run also sweeps the commits API, and anything the events missed is archived with
`source=commit-scan` and an empty `pushed_at`, because the only time available for it is
the commit's own, which the client sets and can therefore lie about.

The result is a complete record of commits, of which the `source=push` rows additionally
carry a trustworthy arrival time. It survives what the group repo does not — rewritten
history, deleted branches, a deleted repo — because it is a different repository students
cannot reach. It does not replace `pushed_at`, which is still what the report grades on.

### Extensions

```
GH_TOKEN=$(gh auth token) python scripts/deadlines.py --unlock a3
```

That gives write access back to every group for that assignment straight away. Move the
deadline too, or the next run locks them straight back:

```
GH_TOKEN=$(gh auth token) python scripts/new_assignment.py a3 --hard "2026-11-29 23:59"
```

Moving the hard deadline later is enough on its own: the next *Assignment deadlines* run
(every 30 minutes, or run it from the Actions tab) reopens every repository that is locked
but no longer past its deadline. The course's page does both for you: *Assignments → Edit
deadlines* saves the new dates and starts that run.

For a single group, change that one team's permission on the repo in the org UI.

## Testing it

### Triggering a workflow by hand

| | |
|---|---|
| `gh workflow run deadlines.yml -f report_only=true` | the deadline job, reporting only — changes nothing |
| `gh workflow run deadlines.yml` | the real thing: locks anything past its hard deadline |
| `gh workflow run register.yml` | re-processes every open registration issue |
| opening an issue, or commenting `/confirm` | what actually triggers `register.yml` day to day |

A scheduled workflow that has never run can take a while before GitHub honours its
`schedule:`. Dispatching it once by hand gets it going.

### The end-to-end test

`scripts/e2e.py` plays a group from both sides: it opens a registration issue as one
student account, confirms as the other, pushes work from each, walks into the deadlines,
and checks the state after every step.

```bash
export GH_TOKEN=$(gh auth token)   # an owner
export TEST_TOKEN_A=ghp_...        # a student account, NOT an owner
export TEST_TOKEN_B=ghp_...        # a second one
python scripts/e2e.py --classes ../classes
```

Each test account needs a classic token with the `repo` scope. The run refuses to call
itself a pass if either account turns out to be an org owner — a test that runs with
owner rights proves nothing about what a student can do.

What it asserts, in order:

1. both accounts exist and neither is an owner;
2. `issues: opened` triggers `register.yml` and the bot asks for confirmation;
3. `/confirm` from the second account creates the team, closes the issue and labels it;
4. the class claim that disagrees with the listing is recorded as `MISMATCH` in
   `claims/classes.csv` and labelled `class-mismatch`;
5. both students can clone and push while the repo is open;
6. their pushes reach `push-log` with a server-stamped time;
7. after the hard deadline the team is read-only and a push is **refused**.

It creates an assignment whose soft deadline is already past and whose hard deadline is
two minutes out, so the whole deadline path runs in about three minutes rather than a
week. Everything it touches uses the `999xx` student numbers, so the teardown is the
ordinary `reset.py`; it runs automatically unless you pass `--keep`, and
`python scripts/e2e.py --cleanup` does it later.

With `--classes` it temporarily adds the two test students to the `CLASSES` secret
(`99901`→P1, `99902`→P2, while the form claims P3 for the second, so the mismatch path is
exercised) and restores the secret to the real listings on the way out.

## Resetting after a test run

Once you have registered a test group and created a test assignment, this puts the org
back the way it was:

```
GH_TOKEN=$(gh auth token) python scripts/reset.py --dry-run
GH_TOKEN=$(gh auth token) python scripts/reset.py --assignments test-assignment
```

By default it only deletes **test** artefacts — groups whose student numbers all start
with `999`, their repositories, the registration issues that mention such a number, their
rows in the push archive, and any pending org invitations for those accounts. Real groups
are left alone. Assignments
are only removed when you name them with `--assignments`, since there is no way to tell a
test assignment from a real one.

`--all` instead deletes every group, every group repo, every registration issue and every
assignment — the org as it was just after setup. Use it before a real edition starts, and
never during one.

It asks twice: once to approve the list, then to type the organization name. `--dry-run`
prints the list and stops, which is how you should always start. The listing shows each
repository's last push, so work you didn't expect is visible before you confirm.

Not touched, because they are setup rather than testing: the registration repo, its
labels and secrets, and the org member privileges. The test student numbers live in the
`ROSTER` secret, which can't be read back — re-run step 5 without `--test-students` to
clear them.

### The token that deletes repositories

Deleting a repository needs permission `gh auth login` does not ask for, so the first run
stops and prints how to get it — `reset.py --token-help` prints the same thing any time.
It leads with a **fine-grained token**, because that one can name `ORG` as its resource
owner and reach nothing else:

```
Resource owner       ORG                              <- what limits the token
Repository access    All repositories
Repository perms     Administration  Read and write   deletes the repos
                     Contents        Read and write   assignments, archive
                     Issues          Read and write   registration issues
Organization perms   Members         Read and write   teams, invitations
```

GitHub has no API for creating tokens, so that step is a browser either way, but the
link the script prints (and offers to open) fills in everything GitHub allows a URL to:
name, owner, a 7-day expiry and all four permissions. **Repository access is the one field
it cannot fill** — there is no parameter for it — so tick *All repositories* yourself; the
page defaults to *Only select*, which would miss group repos made after the token. The org
has to allow fine-grained tokens, and as an owner you may have to approve your own
request. The other two routes it prints — a classic token with both scopes pre-ticked, or
`gh auth refresh -s delete_repo` followed by `--remove-scopes` — are one click each, but a
classic token's scopes are account-wide: `delete_repo` lets it delete any repository you
administer, in any organization.

The check runs *before* anything is deleted, so a missing permission costs you nothing
rather than leaving teams deleted and their repos orphaned. A classic token is judged on
the scopes it advertises; a fine-grained one advertises none, so it is judged on whether
it actually administers each repository in the list, which is what decides it anyway.

## Day-to-day

- Rejected issues are closed with an explanation; students open a new one.
- Failed creations (e.g. the daily invitation limit) are retried every 30 min.
  You can also trigger a retry from *Actions → Group registration → Run workflow*.
- **New assignment?** See *Assignments* above — one command, nothing to commit.
- **Something looks wrong?** `scripts/setup/07_verify.sh ORG` re-checks the whole setup
  without changing anything. Run it after any push to the bot itself — it is what catches
  "I edited the workflow but never committed it".
- **Token expired?** The course's page shows the bot's runs failing; replace the token there
  (*The bot's token*), or with `scripts/setup/04_admin_token.sh ORG`.
- **Cleaning up a test run?** See *Resetting after a test run* above.
- **Finding an old registration:** on success the bot retitles the issue to
  `Group registration — gXX_<numbers>`, so searching the closed issues for a group number
  finds the thread. Students can edit the title themselves — GitHub issue forms only
  pre-fill it — so this is the only thing that makes the archive reliable.
- **Fixing a group by hand:** the bot reads state from team names and memberships, so
  editing a team in the org UI is enough. Keep the `gXX_<numbers>` name format.
- **Changed your mind about a skeleton?** Re-run `new_assignment.py a3 --from ...`.
  It updates the template, but repos already created keep what they have — only groups
  registering afterwards get the new contents.

## Changing the course

```
scripts/setup/06_course.sh --min 2 --max 3      # or no flags, to be asked
```

Group size, classes, name and time zone all change the same way: the step rewrites
`course.json`, regenerates the students' README and form from it, and commits and pushes.
Editing `course.json` anywhere else — GitHub's web editor, the course's page — works too: the
*Course files* workflow (`.github/workflows/course.yml`) regenerates the students' files
whenever `course.json` changes on the default branch.
Don't edit the generated files by hand. The bot finds a member by looking the form's label
up verbatim, so a slot labelled `Member 3 – student number` (en dash) or
`Member 3 - Student Number` is silently ignored and the group registers one member short.
Slots up to the minimum are required and the rest optional; at most 6 members.

## The course's page

`web/` is a small static site, published by `.github/workflows/pages.yml`. In bedel itself
it's the landing page and the setup page; in a course, `web/manage.html` becomes the front
page, at `https://ORG.github.io/registration/`. Without a token it reads what's public —
`course.json`, `assignments/`, the registration issues and the bot's last runs. With a
fine-grained token for the organization (a pre-filled link, 7 days by default) it releases
assignments, edits the course, replaces the roster and class secrets, and renews the bot's
token. Tokens stay in the tab's memory; the page's Content-Security-Policy allows no
connection but `api.github.com`.

The setup page turns it on. A course set up from a terminal turns it on with the commands
below. Step 1 stops members creating Pages, and to GitHub an owner is a member too, so the
setting is lifted for the moment it takes and put back, as the setup page does:

```
gh api -X PATCH orgs/ORG -F members_can_create_pages=true
gh api -X POST repos/ORG/registration/pages -f build_type=workflow
gh api -X PATCH orgs/ORG -F members_can_create_pages=false
gh workflow run pages.yml -R ORG/registration
```

It opens by asking whether you're a student or a teacher, and remembers the answer in that
browser. **Students** see the overview, the assignments and their deadlines, and *My group*:
with a read-only key of their own, each of their group's repositories with its commits,
the days they worked on it, a chart of commits per day against their deadlines, who made
them, the last push and how many commits came after their soft deadline. GitHub
gives that key the student's own access, so it can't see any other group's repositories.
For students' keys to work without you approving each one, choose *Do not require
administrator approval* under the organization's **Settings → Personal access tokens**,
once. **Teachers** see everything, and change things with a token as above. The choice
protects nothing on its own: every change still needs an owner's token.

Under each assignment, *Each group's progress* shows, per group: its classes, a chart of
commits per day with the soft and hard deadlines marked, commits, days with work, the last
work and how long it's been quiet, commits after the group's soft deadline, the share made
in the final two days, and each member's share. Above it: how many groups have started,
how many have been quiet for a week, how many have one member doing nearly all of it, and
the same per class. The table sorts by any column and downloads as CSV. It reads
`push-log`, so its times are GitHub's and it lags by up to 30 minutes; a group not in
`push-log` yet falls back to its commits' own dates, and the page says so. It's activity,
not progress: use it to find a group to talk to, never to grade.

So that students' visits don't each spend GitHub's 60-an-hour allowance for pages without
a token, which a class on one network would use up in minutes, the page is published with
`data.json`: the course, its assignments and how many groups have registered, all public
already (`scripts/site_data.py`). It's refreshed when `course.json` or an assignment
changes, and after registrations and releases, republishing only when it has changed.

Until Pages is on with *GitHub Actions* as its source, the workflow checks, finds nothing to
do and stops. Once it has published, the *Course files* workflow runs again and links the
page from the students' page, `.github/README.md`, so students and staff can find it. Any course can also be opened from bedel's own copy:
`https://ajccosta.github.io/bedel/manage.html?repo=ORG/registration`.

## Getting bedel's updates

A course started from a clone has bedel as its `upstream` remote:

```
git pull upstream main && git push
```

bedel never ships the files that belong to your course — `course.json`, `.github/README.md`,
the issue form, `assignments/*.json`, `claims/` — so those never conflict. The only file
both sides may change is `rules.py`, and only if you changed your deadline rule; resolve
it as any other merge. After pulling, run `scripts/setup/06_course.sh --yes` once in case
the generated files changed shape: it rewrites them and commits only if anything differs.

Fixing something for everyone? Make the change in a clone of bedel, not in the course repo,
or `git cherry-pick` it across, so no course's details end up in bedel.

## Security notes

- The issue body is parsed in Python and never interpolated into a shell command. Keep it
  that way: never put `${{ github.event.issue.body }}` inside a `run:` step.
- Workflows always run from the default branch, so students can't change the bot.
- Issues in the public repo show usernames and student numbers. If that matters, invite
  everyone by email first and make the repo private with a read-only students team.
- A hard deadline is enforced by dropping the team to read access, so it holds only as
  long as students have no other route to the repo. That is what step 1 is for: with
  members allowed to create repos or teams, or with base permissions above *none*, a
  student can route around it.
