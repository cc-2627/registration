// The things done to a course, each one checking what's there first so that
// doing it again changes only what differs. The setup page runs them in order;
// a course's own page runs the ones it needs. Each takes `log` (see dom.js).
import { TEMPLATE, gh, exists, paginate, sleep, b64FromText, textFromB64, setSecret, probe,
         waitForRun } from "./github.js";
import { session, students } from "./state.js";

// What scripts/setup/01_org_settings.sh sets.
const ORG_SETTINGS = {
  default_repository_permission: "none",        // members see only what their team is given
  members_can_create_repositories: false,       // only the bot creates repos
  members_can_create_public_repositories: false,
  members_can_create_private_repositories: false,
  members_can_create_pages: false,
  members_can_delete_repositories: false,       // students can't delete their work
  members_can_change_repo_visibility: false,    // no accidentally public solutions
  members_can_create_teams: false,              // only the bot creates teams
};
// What scripts/setup/03_labels.sh creates.
const LABELS = [
  { name: "registration", color: "1D76DB", description: "Group registration request" },
  { name: "registered", color: "0E8A16", description: "Team and repos created" },
  { name: "class-mismatch", color: "D93F0B", description: "A member's class differs from the faculty listing - check it" },
];

// The course's own page: GitHub Pages serves an organization's repository here.
export const coursePageUrl = (org, repo) => `https://${org.toLowerCase()}.github.io/${repo}/`;

// The course repository as it stands, or null; sets session.branch and session.repoExists.
export async function findRepo(token, org, repo) {
  try {
    const info = await gh(token, "GET", `/repos/${org}/${repo}`);
    session.repoExists = true;
    session.branch = info.default_branch || "main";
    return info;
  } catch (e) {
    if (e.status !== 404) throw e;
    session.repoExists = false;
    return null;
  }
}

export async function readCourseFile(token, org, repo) {
  try {
    const f = await gh(token, "GET", `/repos/${org}/${repo}/contents/course.json`);
    return JSON.parse(textFromB64(f.content));
  } catch (e) { if (e.status === 404) return null; throw e; }
}

export async function lockDownOrg(log, token, org) {
  const s = log("Locking down what members can do in the organization");
  const current = await gh(token, "GET", `/orgs/${org}`);
  const change = Object.fromEntries(Object.entries(ORG_SETTINGS).filter(([k, v]) => current[k] !== v));
  if (!Object.keys(change).length) return s("Organization permissions already locked down");
  const failed = [];
  try { await gh(token, "PATCH", `/orgs/${org}`, change); }
  catch {
    for (const [k, v] of Object.entries(change)) {
      try { await gh(token, "PATCH", `/orgs/${org}`, { [k]: v }); } catch { failed.push(k); }
    }
  }
  if (failed.length) s(`Organization permissions set, except ${failed.join(", ")} (not available on this plan)`, "warn");
  else s("Organization permissions locked down: base permission none, members can't create or delete repos or teams");
}

export async function createRepo(log, token, org, repo, courseName) {
  const s = log(`Creating ${org}/${repo} from ${TEMPLATE}`);
  if (await findRepo(token, org, repo)) s(`${org}/${repo} already exists`);
  else {
    await gh(token, "POST", `/repos/${TEMPLATE}/generate`, {
      owner: org, name: repo, private: false, include_all_branches: false,
      description: `Group registration, repos and deadlines for ${courseName} (bedel)`,
    });
    // GitHub fills a generated repository in a moment after creating it.
    for (let i = 0; i < 45; i++) {
      try { if ((await gh(token, "GET", `/repos/${org}/${repo}/commits?per_page=1`)).length) break; }
      catch (e) { if (![404, 409].includes(e.status)) throw e; }
      await sleep(2000);
    }
    s(`Created ${org}/${repo}, public so students outside the organization can open the form`);
  }
  const info = await findRepo(token, org, repo);
  if (info.private) {
    log(`${org}/${repo} is private: students who aren't members yet can't see the form. Make it public in its settings.`, "warn");
  }
}

export async function addLabels(log, token, org, repo) {
  const s = log("Adding the labels the bot uses");
  const have = new Set((await paginate(token, `/repos/${org}/${repo}/labels`)).map((l) => l.name));
  const added = [];
  for (const l of LABELS) {
    if (!have.has(l.name)) { await gh(token, "POST", `/repos/${org}/${repo}/labels`, l); added.push(l.name); }
  }
  s(added.length ? `Labels added: ${added.join(", ")}` : "Labels already there");
}

// The same checks as scripts/setup/04_admin_token.sh, on top of checkToken's,
// before the bot's token is stored. `token` stores it; `botToken` is checked.
export async function storeBotToken(log, token, org, repo, botToken) {
  const s = log("Checking everything the bot will do with its token");
  const checks = [
    ["create teams (Members)", `/orgs/${org}/teams`],
    ["create repositories (Administration)", `/orgs/${org}/repos`],
    ["write files (Contents)", `/repos/${org}/${repo}/git/blobs`],
    ["mark commits (Commit statuses)", `/repos/${org}/${repo}/statuses/0000000000000000000000000000000000000000`],
    ["open issues (Issues)", `/repos/${org}/${repo}/issues`],
  ];
  const bad = [];
  for (const [what, path] of checks) {
    const r = await probe(botToken, path);
    if (r === "denied") bad.push(what);
    else if (r !== "ok") log(`Couldn't tell whether the bot's token can ${what}: ${r}`, "warn");
  }
  if (bad.length) throw new Error(`The bot's token can't ${bad.join(", ")}. Make a new one with the link given for it.`);
  // "All repositories" can't be read off a token; what can be seen is whether
  // it reaches every repository the owner can see right now.
  const mine = new Set((await paginate(token, `/orgs/${org}/repos?type=all`)).map((r) => r.name));
  const its = new Set((await paginate(botToken, `/orgs/${org}/repos?type=all`)).map((r) => r.name));
  const missing = [...mine].filter((n) => !its.has(n));
  if (missing.length) {
    throw new Error(`The bot's token reaches only ${mine.size - missing.length} of ${mine.size} repositories. Make a new one with Repository access: All repositories.`);
  }
  await setSecret(token, org, repo, "ORG_ADMIN_TOKEN", botToken);
  s(`The bot's token checked, sealed and stored as the secret ORG_ADMIN_TOKEN in ${org}/${repo}`);
}

export const testNumbers = (n) => Array.from({ length: n }, (_, i) => `999${String(i + 1).padStart(2, "0")}`);

// Stores what's been read into `students`; without a roster, one must already be stored.
export async function storeStudents(log, token, org, repo, testN) {
  const tests = testNumbers(testN);
  const s = log("Storing the roster");
  if (students.roster) {
    await setSecret(token, org, repo, "ROSTER", [...students.roster, ...tests].join("\n"));
    s(`Roster sealed and stored as the secret ROSTER: ${students.roster.length} students` +
      (testN ? ` and ${testN} test students` : ""));
  } else if (await exists(token, `/repos/${org}/${repo}/actions/secrets/ROSTER`)) {
    s("Keeping the roster already stored" + (testN ? " (choose the roster file again to add test students)" : ""),
      testN ? "warn" : "ok");
  } else {
    throw new Error("No roster yet: choose the roster file, so the bot knows who may register.");
  }
  if (students.classes) {
    const c = log("Storing the class listings");
    await setSecret(token, org, repo, "CLASSES", students.classes.map(([n, k]) => `${n},${k}`).join("\n"));
    c(`Classes sealed and stored as the secret CLASSES: ${students.classes.length} students`);
  }
  return tests;
}

// Returns the commit that changed course.json, or null if it was already current.
export async function writeCourse(log, token, org, repo, course) {
  const s = log("Writing course.json");
  const text = JSON.stringify(course, null, 2) + "\n";
  let sha = null;
  try {
    const f = await gh(token, "GET", `/repos/${org}/${repo}/contents/course.json`);
    sha = f.sha;
    if (textFromB64(f.content) === text) { s("course.json already up to date"); return null; }
  } catch (e) { if (e.status !== 404) throw e; }
  const res = await gh(token, "PUT", `/repos/${org}/${repo}/contents/course.json`, {
    message: sha ? `Update the course: ${course.name}` : `Set up the course: ${course.name}`,
    content: b64FromText(text), ...(sha ? { sha } : {}), branch: session.branch,
  });
  s(sha ? "course.json updated" : "course.json written");
  return res.commit.sha;
}

// The students' README and form are generated by the Course files workflow
// (.github/workflows/course.yml), so the Python stays the one place they're made.
export async function generateStudentFiles(log, token, org, repo, headSha) {
  const s = log("Generating the students' page and form (a GitHub Actions run, about a minute)");
  if (!headSha && await exists(token, `/repos/${org}/${repo}/contents/.github/ISSUE_TEMPLATE/register.yml`)) {
    return s("Students' page and form already generated");
  }
  const since = Date.now() - 60000;
  if (!headSha) {
    await gh(token, "POST", `/repos/${org}/${repo}/actions/workflows/course.yml/dispatches`, { ref: session.branch });
  }
  const run = await waitForRun(token, org, repo, "course.yml",
    (r) => headSha ? r.head_sha === headSha : Date.parse(r.created_at) >= since,
    (u) => s(`Generating the students' page and form: ${u}`, "run"));
  if (run.conclusion !== "success") throw new Error(`The Course files workflow ${run.conclusion}: ${run.html_url}`);
  s("Students' page and form generated");
}

// GitHub Pages for the course repository, published by the workflow. Org owners
// count as members here, so the lock-down's "members can't create Pages" stops
// this too: when it does, it's lifted for the moment it takes and put back.
const PAGES_SETTINGS = ["members_can_create_pages", "members_can_create_public_pages"];

async function turnOnPages(token, org, repo) {
  const enable = async () => {
    try { await gh(token, "POST", `/repos/${org}/${repo}/pages`, { build_type: "workflow" }); }
    catch (e) {
      if (e.status !== 409) throw e;   // already on: make sure it's published by the workflow
      await gh(token, "PUT", `/repos/${org}/${repo}/pages`, { build_type: "workflow" });
    }
  };
  try { return await enable(); }
  catch (e) { if (!/disabled Pages/i.test(e.message)) throw e; }

  const current = await gh(token, "GET", `/orgs/${org}`);
  const blocked = PAGES_SETTINGS.filter((k) => current[k] === false);
  if (!blocked.length) throw new Error("the organization doesn't allow Pages, and this page can't change that");
  try { await gh(token, "PATCH", `/orgs/${org}`, Object.fromEntries(blocked.map((k) => [k, true]))); }
  catch (e) {
    throw new Error(`the organization doesn't let members create Pages, and lifting that for a moment failed (${e.message}). ` +
      "Allow Pages under Member privileges, try again, then turn it back off");
  }
  try {
    await enable();
  } finally {
    try { await gh(token, "PATCH", `/orgs/${org}`, Object.fromEntries(blocked.map((k) => [k, false]))); }
    catch (e) {
      throw new Error(`Pages was allowed for a moment and couldn't be disallowed again (${e.message}). ` +
        "Turn \"Pages creation\" off under the organization's Settings → Member privileges.");
    }
  }
}

// Turns on the course's own page and publishes it. Optional: the course works
// without it, so a failure is a warning, with a button to try again and links
// to the settings on GitHub. Returns the page's address, or null; a later
// success from the button is passed to `published`.
export async function publishCoursePage(log, token, org, repo, published = () => {}) {
  const s = log("Publishing the course's own page, for running it from now on");
  const attempt = async () => {
    s("Publishing the course's own page, for running it from now on", "run");
    try {
      await turnOnPages(token, org, repo);
      const since = Date.now() - 10000;
      await gh(token, "POST", `/repos/${org}/${repo}/actions/workflows/pages.yml/dispatches`, { ref: session.branch });
      const run = await waitForRun(token, org, repo, "pages.yml",
        (r) => r.event === "workflow_dispatch" && Date.parse(r.created_at) >= since,
        (u) => s(`Publishing the course's own page: ${u}`, "run"));
      if (run.conclusion !== "success") throw new Error(`its workflow ${run.conclusion}: ${run.html_url}`);
    } catch (e) {
      s(`The course's own page isn't published: ${e.message}. The course works without it.`, "warn", [
        { label: "Try again", run: async () => { const url = await attempt(); if (url) published(url); } },
        { label: "Pages settings ↗", href: `https://github.com/${org}/${repo}/settings/pages` },
        { label: "Member privileges ↗", href: `https://github.com/organizations/${org}/settings/member_privileges` },
      ]);
      return null;
    }
    const url = coursePageUrl(org, repo);
    s(`The course's own page is at ${url}`, "ok", [{ label: "Open it ↗", href: url }]);
    return url;
  };
  return attempt();
}
