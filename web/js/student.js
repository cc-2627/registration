// A student's own view of their group: with a read-only key of theirs, how each
// of the group's repositories is doing. GitHub gives the key the student's own
// access, so it can't see any other group's repositories, whatever the page asks.
import { gh, paginate, GitHubError } from "./github.js";
import { tokenUrl } from "./tokens.js";
import { ctx } from "./state.js";
import { stateOf } from "./asgpage.js";
import { classesFromReply, dayOf, measure, perDay, softFor, sparkline } from "./progress.js";

// Contents: read for the repositories, Commit statuses: read for the verdict the
// deadlines workflow posts; GitHub adds Metadata: read, which every token has.
export const STUDENT_PERMISSIONS = { contents: "read", statuses: "read" };
const VERDICT_CONTEXT = "deadline/verdict";   // scripts/deadlines.py, post_verdict
const GROUP_REPO = /^(g\d+_\d+(?:_\d+)*)-(.+)$/;   // gXX_<numbers>-<assignment>

export const studentTokenUrl = (org) =>
  tokenUrl(org, `bedel ${org} (read only)`, "Reads my group's repositories for the course page", 30, STUDENT_PERMISSIONS);

function el(tag, text, cls) {
  const e = document.createElement(tag);
  if (text !== undefined) e.textContent = text;
  if (cls) e.className = cls;
  return e;
}
function linkTo(href, text) { const a = el("a", text); a.href = href; a.target = "_blank"; a.rel = "noopener"; return a; }

// Throws, in words a student can act on, unless the key works and shows at least
// one of their group's repositories. Returns what the page shows.
export async function loadMine(token, defs) {
  const { org, repo } = ctx;
  if (!token) throw new Error("Paste the key first.");
  if (!token.startsWith("github_pat_")) {
    throw new Error(token.startsWith("ghp_")
      ? "That's a classic token, which can read and change everything you can on GitHub. Make a fine-grained one with the link above: it reads only this course."
      : "That doesn't look like a key from the link above: those start with github_pat_.");
  }
  let me;
  try { me = await gh(token, "GET", "/user"); }
  catch (e) {
    if (e.status === 401) throw new Error("GitHub doesn't accept that key. Copy it again, or make a new one: it may have expired.");
    throw e;
  }
  let repos;
  try { repos = await paginate(token, `/orgs/${org}/repos?type=all`); }
  catch (e) {
    if (e instanceof GitHubError && e.status === 404) throw new Error(`This key can't see ${org}. Its Resource owner must be ${org}.`);
    throw e;
  }
  const mine = repos.map((r) => ({ r, m: r.name.match(GROUP_REPO) })).filter((x) => x.m && x.r.private);
  if (!mine.length) {
    throw new Error(`This key, ${me.login}'s, sees none of your group's repositories. Either: its Resource owner isn't ${org}; `
      + `it's waiting for your teacher's approval (GitHub says so on your token's page); you haven't accepted the invitation to ${org}; `
      + "or your group isn't registered yet.");
  }
  const group = mine[0].m[1];
  const classes = await groupClasses(token, org, repo, group);
  const byName = Object.fromEntries(defs.map((d) => [d.name, d]));
  const rows = await Promise.all(mine.map(async ({ r, m }) => {
    const d = byName[m[2]] || { name: m[2] };
    let commits = [];
    try { commits = await paginate(token, `/repos/${org}/${r.name}/commits`); }
    catch (e) { if (e.status !== 409) throw e; }   // 409: still empty
    return { r, d, commits, soft: softFor(d, classes), graded: await gradedCommit(token, org, r, commits) };
  }));
  // In the order the assignments were released.
  const order = defs.map((d) => d.name);
  rows.sort((a, b) => order.indexOf(a.d.name) - order.indexOf(b.d.name));
  return { login: me.login, group, classes, rows };
}

// The commit that gets graded - the head of the default branch, which the commits
// list starts with - and when GitHub first received it, from the repository's
// activity: a server time, like the one the teacher's report uses. The earliest
// update that set any branch to it counts, so a branch made later pointing at the
// same work doesn't move it. null when there's nothing to grade or no record.
async function gradedCommit(token, org, r, commits) {
  const sha = commits[0]?.sha;
  if (!sha) return null;
  let acts;
  // One page, the newest 100: the activity list pages by cursor, which paginate()
  // doesn't follow, and the graded commit is nearly always recent.
  try { acts = await gh(token, "GET", `/repos/${org}/${r.name}/activity?per_page=100`); }
  catch { return { sha, at: null }; }
  const hits = acts.filter((a) => a.after === sha && a.activity_type !== "branch_deletion")
    .sort((a, b) => Date.parse(a.timestamp) - Date.parse(b.timestamp));
  const first = hits[0];
  return { sha, at: first ? first.timestamp : null, ref: first?.ref?.replace(/^refs\/heads\//, ""),
           verdict: await verdictOf(token, org, r, sha) };
}

// The deadline report's verdict on the graded commit, which is what's graded: it
// counts from the push archive, so it can differ from the activity time above
// (work first pushed elsewhere, say). null before the soft deadline, or with an
// older key that can't read commit statuses.
async function verdictOf(token, org, r, sha) {
  try {
    const st = await gh(token, "GET", `/repos/${org}/${r.name}/commits/${sha}/status`);
    const ours = (st.statuses || []).find((s) => s.context === VERDICT_CONTEXT);
    return ours ? { onTime: ours.state === "success" } : null;
  } catch { return null; }
}

// The group's classes, from the bot's reply on its registration issue, which is public.
async function groupClasses(token, org, repo, group) {
  try {
    const issues = await paginate(token, `/repos/${org}/${repo}/issues?labels=registered&state=all`);
    const issue = issues.find((i) => i.title.endsWith(group));
    if (!issue) return [];
    const comments = await paginate(token, `/repos/${org}/${repo}/issues/${issue.number}/comments`);
    return classesFromReply(comments);
  } catch { return []; }
}

export function renderMine(box, data, when, tz = "UTC") {
  box.innerHTML = "";
  const intro = el("p", undefined, "sub");
  intro.append(`Group ${data.group}`, data.classes.length ? `, class ${data.classes.join(" and ")}` : "", `. Seen with ${data.login}'s key.`);
  box.append(intro);
  for (const row of data.rows) {
    const { r, d, commits, soft, graded } = row;
    const card = el("article", undefined, "asg");
    const head = el("header");
    const [state, kind] = stateOf(d);
    head.append(el("h3", d.name), el("span", r.permissions?.push === false ? "Read-only" : state,
      `pill ${r.permissions?.push === false ? "bad" : kind}`));
    card.append(head);
    const meta = el("p", undefined, "hint");
    meta.append(linkTo(r.html_url, r.name));
    card.append(meta);

    // Work landing here after the deadline while a later assignment is open is,
    // more often than not, that assignment's work pushed to the wrong place.
    const next = soft && graded?.at && Date.parse(graded.at) > Date.parse(soft) && !graded.verdict?.onTime
      && data.rows.find((o) => o !== row && Date.parse(o.d.created || o.r.created_at) > Date.parse(d.created || r.created_at)
        && (!o.d.hard_deadline || Date.now() < Date.parse(o.d.hard_deadline)));
    if (next) {
      const warn = el("p", undefined, "callout");
      warn.append(`New work reached this repository after its deadline. If it's for ${next.d.name}, it belongs in `,
        linkTo(next.r.html_url, next.r.name),
        `, where it doesn't count against ${d.name}. Your teacher can tell you how to move it.`);
      card.append(warn);
    }

    const pushed = r.pushed_at && Date.parse(r.pushed_at) - Date.parse(r.created_at) > 60000 ? r.pushed_at : null;
    const dated = commits.map((c) => ({ at: Date.parse(c.commit.committer?.date || c.commit.author?.date),
                                        who: c.author?.login || c.commit.author?.name || "unknown" }));
    const m = measure(dated, { since: Date.parse(r.created_at), soft: soft && Date.parse(soft), own: d.own_work, tz,
                               hard: d.hard_deadline && Date.parse(d.hard_deadline) });
    const stats = el("div", undefined, "stats");
    const stat = (n, label) => { const s = el("div", undefined, "stat"); s.append(el("strong", String(n)), el("span", label)); stats.append(s); };
    stat(m.commits, m.commits === 1 ? "commit of your own" : "commits of your own");
    stat(m.days, m.days === 1 ? "day with work" : "days with work");
    stat(pushed ? when(pushed) : "—", pushed ? "last push" : "nothing pushed yet");
    const open = !d.hard_deadline || Date.now() < Date.parse(d.hard_deadline);
    if (pushed && open && m.quiet >= 2) stat(m.quiet, "days since the last commit");
    // Whether it's late is a question about when the graded work arrived; commit
    // dates are only a fallback, for when GitHub has no record of the push.
    if (soft && graded?.verdict) stat(graded.verdict.onTime ? "On time" : "Late", "for your soft deadline");
    else if (soft && graded?.at) stat(Date.parse(graded.at) <= Date.parse(soft) ? "On time" : "Late", "for your soft deadline");
    else if (soft) stat(m.late, m.late === 1 ? "commit dated after the soft deadline" : "commits dated after the soft deadline");
    card.append(stats);

    if (m.started) {
      const work = dated.filter((c) => d.own_work || c.at > Date.parse(r.created_at) + 5 * 60000);
      const from = Math.min(Date.parse(d.created || r.created_at), ...work.map((c) => c.at));
      const to = d.hard_deadline ? Math.min(Date.now(), Date.parse(d.hard_deadline)) : Date.now();
      const marks = [];
      if (soft) marks.push({ day: dayOf(Date.parse(soft), tz), kind: "soft", label: `Your soft deadline: ${when(soft)}` });
      if (d.hard_deadline) marks.push({ day: dayOf(Date.parse(d.hard_deadline), tz), kind: "hard", label: `Hard deadline: ${when(d.hard_deadline)}` });
      const fig = el("figure", undefined, "activity");
      fig.append(sparkline(perDay(work, from, to, tz), marks, { width: 480, height: 48 }),
                 el("figcaption", "Commits per day. The lines are your soft and hard deadlines."));
      card.append(el("h4", "Activity"), fig);
    }

    const dl = el("dl", undefined, "deadlines");
    dl.append(el("dt", "Your soft deadline"), el("dd", soft ? when(soft) : "None"),
              el("dt", "Hard deadline"), el("dd", d.hard_deadline ? `${when(d.hard_deadline)} — the repository locks` : "Never locks"));
    if (graded) {
      dl.append(el("dt", "Your work, as graded"), el("dd", graded.at
        ? `${graded.sha.slice(0, 7)}, the latest commit on ${r.default_branch}, reached GitHub ${when(graded.at)}${graded.ref ? ` (pushed to ${graded.ref})` : ""}`
        : `${graded.sha.slice(0, 7)}, the latest commit on ${r.default_branch}. GitHub has no record of when it was pushed; your teacher checks these by hand.`));
      if (graded.verdict) {
        dl.append(el("dt", "Your teachers' record"), el("dd", `Counts it as ${graded.verdict.onTime ? "on time" : "late"} for your soft deadline. `
          + "That's what is graded, and it can differ from the time above, for work first pushed somewhere else."));
      }
    }
    card.append(dl);

    if (m.started) {
      const counts = m.whos;
      const max = Math.max(...Object.values(counts));
      const list = el("ul", undefined, "bars");
      for (const [who, n] of Object.entries(counts).sort((a, b) => b[1] - a[1])) {
        const li = el("li");
        const bar = el("span", undefined, "bar");
        bar.style.setProperty("--w", `${Math.round((n / max) * 100)}%`);
        li.append(el("span", who, "name"), bar, el("span", String(n), "n"));
        list.append(li);
      }
      card.append(el("h4", "Commits by member"), list);
    }
    card.append(el("p", `Commit times come from your own computer. Whether work is late is decided by when GitHub first received the latest commit on ${r.default_branch}, which is what your teacher sees: making another branch, or pushing the same commit again, doesn't change it. New commits pushed after a deadline do.`, "hint"));
    box.append(card);
  }
}
