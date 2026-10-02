# bedel

**Group registration, per-assignment repositories and deadlines for a course on
GitHub, run from one repository in your own organization.** A flexible,
self-hosted alternative to GitHub Classroom and Classroom 50 (classroom50), built
on nothing but issues, teams and GitHub Actions — so every workflow, rule and form
is a file you can change.

**[Set up a course in your browser →](https://ajccosta.github.io/bedel/)**

*Bedel* is Portuguese for the university porter who keeps the rooms and the
timetable: he lets you in, and he locks the door when time is up.

## What it does

- **Groups register themselves.** Students fill in an issue form with their
  student numbers and GitHub usernames. The bot checks each number against your
  roster, waits for every member to reply `/confirm`, then creates their team
  and invites them to the organization.
- **One private repository per group per assignment.** Release an assignment
  with one command: it becomes a template, and every group gets a repo seeded
  from it and an issue that emails them it is out. Groups registering later get
  theirs automatically.
- **Work carries over.** An assignment can start as a copy of each group's
  previous one, with the full history, and the new starting files committed on
  top.
- **Soft and hard deadlines.** Pushing after the soft one is allowed and
  recorded. At the hard one, the repositories become read-only, on the minute.
  Soft deadlines can follow each class's own weekly session, and the rule is a
  short Python function you can rewrite.
- **Evidence students can't rewrite.** Commits after the soft deadline are
  marked late in GitHub itself, and every push is archived to a private repo,
  so a force-push doesn't erase when something happened.
- **Reports.** Who pushed what before which deadline, as a table or CSV.
- **Clean teardown.** One command removes test groups, repos and invitations
  after a dry run.

Only student numbers ever reach GitHub: names, emails and the rest of your
lists stay on your machine.

## After GitHub Classroom

GitHub [retired GitHub Classroom](https://github.blog/changelog/2026-08-27-github-classroom-deprecated/)
on 28 August 2026, pointing teachers to partner tools such as
[Classroom 50](https://github.com/foundation50/classroom50). Like both, bedel gives
every group its own repository seeded from a template. The difference is where it
runs: bedel is plain files in a repository you own, inside your organization. There
is no app to authorize and no server, and every rule (group size, who may register,
how deadlines are worked out) is code you can read and change.

|                     | bedel | Classroom 50 |
|---------------------|-------|--------------|
| GitHub plan         | a free organization | Team or Enterprise (free for verified teachers) |
| Groups              | students register against your roster, every member confirms | the first student creates the group, or you assign them |
| Hard deadline       | repositories become read-only on the minute, by themselves | you close the assignment by hand |
| Autograding         | not built in: add a workflow | built in |
| Changing how it works | edit any file: the whole tool is your copy | your own grading scripts; the app itself is open source and could be forked |

## Starting a course

**In your browser:** [ajccosta.github.io/bedel](https://ajccosta.github.io/bedel/setup.html)
walks you through it in six steps. You create the course's organization first, so
that both tokens it asks for can be made for that organization alone; it refuses
classic tokens and proves each one belongs to the organization before using it.
The page has no server, and its Content-Security-Policy lets it talk to
`api.github.com` only. It creates the course's repository from bedel, stores your
roster and the bot's token as encrypted secrets, and writes `course.json`.

**Then, the course's own page.** Setup turns on GitHub Pages for the course
repository, which publishes `web/manage.html` from it at
`https://ORG.github.io/registration/`. It shows registrations, assignments,
deadlines and whether the bot's runs succeed, without a token. With one, it
releases assignments, changes the course, replaces the roster and renews the bot's
token. It's your course's copy of the code, which changes only when you pull
bedel's updates.

**From a terminal:** you need `gh` (the GitHub CLI) logged in, `git`, and
Python 3.9 or later.

```sh
git clone https://github.com/ajccosta/bedel registration
cd registration
./scripts/setup.sh YOUR-ORG
```

Setup walks through seven steps and asks before each one: organization
permissions, creating `YOUR-ORG/registration` and pushing this checkout to it,
labels, the admin token (a pre-filled page; GitHub has no API for making one),
the roster, the course itself (name, group size, time zone, classes) and a
final check. It ends with the link to give your students.

The clone keeps bedel as the `upstream` remote, so later fixes are one command
away: `git pull upstream main`. A course set up from the browser, or with **Use
this template**, starts a history of its own instead, so bedel's later changes
have to be merged in by hand.

Everything about the course lives in `course.json`; everything else is the same
for every course. The full guide, including assignments, deadlines, testing and
resetting, is in [scripts/README.md](scripts/README.md).
