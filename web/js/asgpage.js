// The Assignments page: every assignment with its deadlines, which anyone can
// read, and with a token, a form to change them and each group's repository.
import { logger } from "./dom.js";
import { gh, paginate, textFromB64, waitForRun } from "./github.js";
import { classesFromReply, dayOf, measure, parseCsv, perDay, softFor, sparkline, split } from "./progress.js";
import { ctx, session } from "./state.js";

const TEAM_RE = /^g\d+_\d+(?:_\d+)*$/;   // ghlib.TEAM_RE: a registered group's team
let teams = null;                         // the groups, read once per sign-in
let kept = null;                          // a save's log, carried into the re-drawn card

function el(tag, text, cls) {
  const e = document.createElement(tag);
  if (text !== undefined) e.textContent = text;
  if (cls) e.className = cls;
  return e;
}
function linkTo(href, text) { const a = el("a", text); a.href = href; return a; }
function field(label, input) { const l = el("label", label + " "); l.append(input); return l; }
function input(type, value = "") { const i = el("input"); i.type = type; i.value = value; return i; }

// An ISO time as the "YYYY-MM-DDTHH:MM" a datetime-local input wants, in the course's time zone.
function local(iso, tz) {
  if (!iso) return "";
  const p = Object.fromEntries(new Intl.DateTimeFormat("en-CA", {
    timeZone: tz, year: "numeric", month: "2-digit", day: "2-digit", hour: "2-digit", minute: "2-digit", hourCycle: "h23",
  }).formatToParts(new Date(iso)).map((x) => [x.type, x.value]));
  return `${p.year}-${p.month}-${p.day}T${p.hour}:${p.minute}`;
}

export function stateOf(d, now = Date.now()) {
  const byClass = Object.values(d.soft_by_class || {});
  const softs = byClass.length ? byClass.map(Date.parse) : d.soft_deadline ? [Date.parse(d.soft_deadline)] : [];
  if (d.hard_deadline && Date.parse(d.hard_deadline) <= now) return ["Locked", "bad"];
  if (softs.length && softs.every((x) => x <= now)) return ["Late from here", "warn"];
  if (softs.some((x) => x <= now)) return ["Late for some classes", "warn"];
  return ["Open", "ok"];
}

// ---------- the list ----------
export function renderAssignments(defs, course, when) {
  const list = document.getElementById("asgList");
  list.innerHTML = "";
  document.getElementById("asgNone").hidden = defs.length > 0;
  for (const d of [...defs].reverse()) list.append(card(d, course, when));   // newest first
  kept = null;
}

function card(d, course, when) {
  const box = el("article", undefined, "asg");
  box.dataset.name = d.name;
  const head = el("header");
  const [state, kind] = stateOf(d);
  head.append(el("h3", d.name), el("span", state, `pill ${kind}`));
  box.append(head);

  const meta = el("p", undefined, "hint");
  if (d.own_work) meta.append("Groups bring their own work");
  else meta.append("Starting files from ", linkTo(`https://github.com/${ctx.org}/${d.template || `${d.name}-template`}`, d.template || `${d.name}-template`));
  if (d.created) meta.append(` · released ${when(d.created)}`);
  box.append(meta);

  const dl = el("dl", undefined, "deadlines");
  const soft = el("dd");
  const byClass = Object.entries(d.soft_by_class || {}).sort();
  if (byClass.length) for (const [c, iso] of byClass) soft.append(el("div", `${c}: ${when(iso)}`));
  else soft.textContent = d.soft_deadline ? when(d.soft_deadline) : "None";
  dl.append(el("dt", byClass.length ? "Soft, by class" : "Soft"), soft,
            el("dt", "Hard"), el("dd", d.hard_deadline ? `${when(d.hard_deadline)} — repositories lock` : "Never locks"));
  box.append(dl);

  if (!ctx.login) return box;

  const actions = el("div", undefined, "row");
  const edit = el("button", "Edit deadlines", "btn small");
  edit.type = "button";
  actions.append(edit);
  box.append(actions);
  const form = editForm(d, course);
  form.hidden = true;
  box.append(form);
  edit.onclick = () => { form.hidden = !form.hidden; edit.textContent = form.hidden ? "Edit deadlines" : "Close"; };
  if (kept?.name === d.name) {   // just saved: stay open, with the save's log
    form.querySelector("ol.log").replaceWith(kept.log);
    edit.click();
  }

  const groups = el("details", undefined, "groups");
  groups.append(el("summary", "Each group's progress"));
  const table = el("div", "Loading…", "hint");
  groups.append(table);
  groups.addEventListener("toggle", () => { if (groups.open) loadGroups(d, course, table, when); });
  box.append(groups);
  return box;
}

// ---------- changing the deadlines ----------
function editForm(d, course) {
  const tz = course?.timezone || "UTC";
  const classes = Object.keys(course?.classes || {}).length > 0;
  const id = `edit-${d.name}`;
  const form = el("div", undefined, "edit");

  const mode = el("select");
  mode.add(new Option("By class: a week after each class's session in…", "week"));
  mode.add(new Option("One date for everyone", "date"));
  mode.add(new Option("None", "none"));
  mode.options[0].disabled = !classes;
  mode.value = !d.soft_manual && d.soft_week && classes ? "week" : d.soft_deadline ? "date" : d.soft_manual ? "none" : "date";
  const week = input("date", d.soft_week || "");
  const date = input("datetime-local", local(d.soft_deadline, tz));
  const time = input("time", d.soft_time || "");
  const weekWrap = field("Week of", week);
  const timeWrap = field("Ending at", time);
  timeWrap.append(el("span", "Empty: when each class's session ends.", "hint"));
  const dateWrap = field("On", date);
  const showMode = () => {
    weekWrap.hidden = timeWrap.hidden = mode.value !== "week";
    dateWrap.hidden = mode.value !== "date";
  };
  mode.onchange = showMode;
  showMode();

  const hard = input("datetime-local", local(d.hard_deadline, tz));
  const never = input("checkbox");
  never.checked = !d.hard_deadline;
  hard.disabled = never.checked;
  never.onchange = () => { hard.disabled = never.checked; };
  const neverLabel = el("label", undefined, "check");
  neverLabel.append(never, " Never lock");

  const r1 = el("div", undefined, "row"); r1.append(field("Soft deadline", mode), weekWrap, timeWrap, dateWrap);
  const r2 = el("div", undefined, "row"); r2.append(field("Hard deadline (repositories lock)", hard), neverLabel);
  const note = el("p", `Times are in ${tz}. Saving changes only the dates: repositories and their work stay as they are. `
    + "A repository already locked opens again if the hard deadline moves later.", "hint");
  const save = el("button", "Save the deadlines", "btn primary");
  save.type = "button";
  const log = el("ol", undefined, "log");
  log.id = `${id}-log`;
  form.append(r1, r2, note, save, log);

  save.onclick = async () => {
    save.disabled = true;
    try {
      const inputs = { name: d.name, dry_run: "false" };
      if (mode.value === "week") {
        if (!week.value) throw new Error("Pick the week the class sessions start the clock.");
        inputs.soft_week = week.value;
        inputs.soft_time = time.value || "session";
      } else if (mode.value === "date") {
        if (!date.value) throw new Error("Pick the soft deadline.");
        inputs.soft = date.value.replace("T", " ");
      } else inputs.soft = "none";
      if (never.checked) inputs.hard = "none";
      else if (!hard.value) throw new Error("Pick the hard deadline, or tick Never lock.");
      else inputs.hard = hard.value.replace("T", " ");
      await saveDeadlines(logger(log.id), inputs);
      // The page reads the new dates back; the log stays, in a card re-drawn around it.
      document.dispatchEvent(new CustomEvent("assignments-changed", { detail: { name: d.name, log } }));
    } catch (e) {
      const li = el("li", e.message, "bad");
      log.append(li);
    } finally {
      for (const li of log.querySelectorAll("li.run")) li.className = "warn";
      save.disabled = false;
    }
  };
  return form;
}

// The same workflow that released it, given only the dates: new_assignment.py
// keeps the repositories and rewrites assignments/<name>.json. Then the
// deadlines run, so locks follow the new dates now rather than within 30 minutes.
async function saveDeadlines(log, inputs) {
  const { org, repo, token } = ctx;
  const s = log(`Saving ${inputs.name}'s deadlines`);
  const since = Date.now() - 10000;
  await gh(token, "POST", `/repos/${org}/${repo}/actions/workflows/assignment.yml/dispatches`,
    { ref: session.branch, inputs });
  const run = await waitForRun(token, org, repo, "assignment.yml",
    (r) => r.event === "workflow_dispatch" && Date.parse(r.created_at) >= since,
    (u) => s(`Saving the deadlines: ${u}`, "run"), 10);
  if (run.conclusion !== "success") throw new Error(`Saving ${run.conclusion}. What went wrong is in its log: ${run.html_url}`);
  s(`${inputs.name}'s deadlines saved`, "ok", [{ label: "Log ↗", href: run.html_url }]);

  const l = log("Locking or opening repositories to match");
  const after = Date.now() - 10000;
  await gh(token, "POST", `/repos/${org}/${repo}/actions/workflows/deadlines.yml/dispatches`,
    { ref: session.branch, inputs: { report_only: "false" } });
  // It can sit waiting for a deadline due in the next half hour, so this only
  // waits for it to start, and leaves the link.
  for (let i = 0; i < 30; i++) {
    const runs = await gh(token, "GET", `/repos/${org}/${repo}/actions/workflows/deadlines.yml/runs?per_page=5`);
    const r = (runs.workflow_runs || []).find((x) => x.event === "workflow_dispatch" && Date.parse(x.created_at) >= after);
    if (r) return l("Locking or opening repositories to match: started", "ok", [{ label: "Its log ↗", href: r.html_url }]);
    await new Promise((res) => setTimeout(res, 3000));
  }
  l("The deadlines run didn't start; the next scheduled one, within 30 minutes, applies the new dates", "warn");
}

// ---------- each group's progress ----------
// From push-log, which the deadlines workflow fills every 30 minutes: it keeps
// when GitHub received each push, which a student's clock can't change, and it
// keeps work a force-push later erased. A course without it yet falls back to the
// repository's commits, whose dates are the students' own.
const LOG_REPO = "push-log";
const HEADS = [
  ["Group", (r) => r.t.name],
  ["Class", (r) => r.classes.join(" ")],
  ["Activity", (r) => r.m?.commits ?? -1],
  ["Commits", (r) => r.m?.commits ?? -1],
  ["Days active", (r) => r.m?.days ?? -1],
  ["Last work", (r) => r.m?.last ?? 0],
  ["After soft", (r) => r.m?.late ?? -1],
  ["Final 2 days", (r) => r.m?.rush ?? -1],
  ["Members", (r) => (r.split?.lopsided ? 1 : 0)],
  ["Access", (r) => r.state || ""],
];

// At most `n` requests at a time: GitHub turns away bursts from one token.
async function eachLimited(items, n, fn) {
  const out = new Array(items.length);
  let next = 0;
  await Promise.all(Array.from({ length: Math.min(n, items.length) }, async () => {
    while (next < items.length) { const i = next++; out[i] = await fn(items[i]); }
  }));
  return out;
}

async function pushLog(org, token, d, group) {
  let f;
  try { f = await gh(token, "GET", `/repos/${org}/${LOG_REPO}/contents/pushes/${d.name}/${group}.csv`); }
  catch (e) { if (e.status === 404) return null; throw e; }
  // Over 1 MB the contents API leaves the content out; the blob has it.
  const b64 = f.content || (await gh(token, "GET", `/repos/${org}/${LOG_REPO}/git/blobs/${f.sha}`)).content;
  return parseCsv(textFromB64(b64)).map((x) => ({
    at: Date.parse(x.pushed_at || x.committed_at), pushed: !!x.pushed_at, push: x.event_id, who: x.actor || x.author,
  })).filter((c) => !Number.isNaN(c.at));
}

async function fromCommits(org, token, name) {
  try {
    return (await paginate(token, `/repos/${org}/${name}/commits`)).map((c) => ({
      at: Date.parse(c.commit.committer?.date || c.commit.author?.date), pushed: false,
      who: c.author?.login || c.commit.author?.name,
    }));
  } catch (e) { if (e.status === 409) return []; throw e; }   // 409: still empty
}

// The group's classes, from the bot's reply on the issue its team names.
async function teamClasses(org, token, t) {
  if (t.classes) return t.classes;
  const n = (t.description || "").match(/#(\d+)$/)?.[1];
  t.classes = [];
  if (n) {
    try { t.classes = classesFromReply(await paginate(token, `/repos/${org}/${ctx.repo}/issues/${n}/comments`)); }
    catch { /* no classes is fine: the assignment's own soft deadline applies */ }
  }
  return t.classes;
}

async function loadGroups(d, course, box, when) {
  const { org, token } = ctx;
  const tz = course?.timezone || "UTC";
  try {
    teams ||= (await paginate(token, `/orgs/${org}/teams`)).filter((t) => TEAM_RE.test(t.name))
      .sort((a, b) => a.name.localeCompare(b.name, undefined, { numeric: true }));
    if (!teams.length) { box.textContent = "No groups registered yet."; return; }
    let fallback = false;
    const rows = await eachLimited(teams, 6, async (t) => {
      const name = `${t.name}-${d.name}`;
      const classes = await teamClasses(org, token, t);
      try {
        const [info, access, logged] = await Promise.all([
          gh(token, "GET", `/repos/${org}/${name}`),
          gh(token, "GET", `/repos/${org}/${name}/teams`),
          pushLog(org, token, d, t.name),
        ]);
        const perm = access.find((x) => x.slug === t.slug)?.permission;
        let commits = logged;
        if (!commits) { commits = await fromCommits(org, token, name); fallback = true; }
        const soft = softFor(d, classes);
        const m = measure(commits, {
          since: Date.parse(info.created_at), soft: soft && Date.parse(soft), own: d.own_work, tz,
          hard: d.hard_deadline && Date.parse(d.hard_deadline),
        });
        const members = t.name.split("_").length - 1;
        return { t, name, classes, soft, info, commits, m, split: split(m.whos, members),
                 state: { push: "Writable", pull: "Read-only" }[perm] || (perm ? perm : "No access") };
      } catch (e) {
        if (e.status === 404) return { t, name, classes, missing: true };
        throw e;
      }
    });
    box.className = "";
    box.textContent = "";
    box.append(summary(rows, course), table(rows, d, tz, when));
    const foot = el("p", undefined, "hint");
    foot.append(fallback
      ? `Some groups aren't in ${LOG_REPO} yet, so their times are their commits' own, which a student's computer sets. `
      : `From ${LOG_REPO}, updated every 30 minutes: times are when GitHub received each push. `,
      "Activity isn't progress: one big commit and ten small ones can be the same work, so these are for spotting a group to talk to, not for grading.");
    const csv = el("button", "Download as CSV", "btn small");
    csv.type = "button";
    csv.onclick = () => download(`${d.name}-progress.csv`, toCsv(rows, d, tz));
    box.append(foot, csv);
  } catch (e) {
    box.className = "status bad";
    box.textContent = e.message;
  }
}

function summary(rows, course) {
  const live = rows.filter((r) => !r.missing);
  const idle = live.filter((r) => !r.m.started).length;
  const quiet = live.filter((r) => r.m.started && r.m.quiet >= 7).length;
  const lop = live.filter((r) => r.split.lopsided).length;
  const box = el("div", undefined, "stats");
  const stat = (n, label, cls) => { const s = el("div", undefined, `stat${cls ? " " + cls : ""}`); s.append(el("strong", String(n)), el("span", label)); box.append(s); };
  stat(`${live.length - idle}/${live.length}`, "groups have started");
  if (idle) stat(idle, idle === 1 ? "hasn't pushed anything yet" : "haven't pushed anything yet", "warn");
  stat(quiet, "quiet for a week or more", quiet ? "warn" : "");
  if (lop) stat(lop, "with one member doing nearly all of it", "warn");
  const wrap = el("div");
  wrap.append(box);
  // Per class, to spot a class that's behind: groups count in each of their classes.
  const classes = Object.keys(course?.classes || {}).sort();
  if (classes.length) {
    const list = el("ul", undefined, "byclass");
    for (const c of classes) {
      const of = live.filter((r) => r.classes.includes(c));
      if (!of.length) continue;
      const days = of.map((r) => r.m.days).sort((a, b) => a - b);
      list.append(el("li", `${c}: ${of.filter((r) => r.m.started).length}/${of.length} started, typically ${days[Math.floor(days.length / 2)]} day${days[Math.floor(days.length / 2)] === 1 ? "" : "s"} active`));
    }
    if (list.children.length) wrap.append(list);
  }
  return wrap;
}

function table(rows, d, tz, when) {
  const wrap = el("div", undefined, "table-scroll");
  const t = el("table", undefined, "compare plain progress");
  const thead = el("thead");
  const head = el("tr");
  const body = el("tbody");
  let by = 0, dir = 1;
  const draw = () => {
    body.textContent = "";
    const key = HEADS[by][1];
    const sorted = [...rows].sort((a, b) => {
      const x = key(a), y = key(b);
      return (typeof x === "string" ? x.localeCompare(y, undefined, { numeric: true }) : x - y) * dir;
    });
    for (const r of sorted) body.append(rowFor(r, d, tz, when));
  };
  HEADS.forEach(([h], i) => {
    const th = el("th");
    const b = el("button", h, "linkish");
    b.type = "button";
    b.onclick = () => { dir = by === i ? -dir : 1; by = i; draw(); };
    th.append(b);
    head.append(th);
  });
  thead.append(head);
  t.append(thead, body);
  draw();
  wrap.append(t);
  return wrap;
}

function rowFor(r, d, tz, when) {
  const { org } = ctx;
  const tr = el("tr");
  const g = el("td");
  if (r.missing) g.append(r.t.name);
  else g.append(linkTo(`https://github.com/${org}/${r.name}`, r.t.name));
  tr.append(g, el("td", r.classes.join(", ") || "—"));
  if (r.missing) {
    const td = el("td", "Repository not created yet");
    td.colSpan = HEADS.length - 2;
    tr.append(td);
    return tr;
  }
  const { m } = r;
  const now = Date.now();
  const from = Date.parse(d.created || r.info.created_at);
  const to = d.hard_deadline ? Math.min(now, Date.parse(d.hard_deadline)) : now;
  const marks = [];
  if (r.soft) marks.push({ day: dayOf(Date.parse(r.soft), tz), kind: "soft", label: `Soft deadline: ${when(r.soft)}` });
  if (d.hard_deadline) marks.push({ day: dayOf(Date.parse(d.hard_deadline), tz), kind: "hard", label: `Hard deadline: ${when(d.hard_deadline)}` });
  const work = r.commits.filter((c) => d.own_work || c.at > Date.parse(r.info.created_at) + 5 * 60000);
  const spark = el("td");
  spark.append(sparkline(perDay(work.filter((c) => c.at >= from), from, to, tz), marks));
  tr.append(spark, el("td", String(m.commits)), el("td", String(m.days)));
  tr.append(el("td", m.last ? `${when(new Date(m.last).toISOString())}${m.quiet >= 2 ? ` · quiet ${m.quiet} days` : ""}` : "Not started",
               !m.started || m.quiet >= 7 ? "warn" : ""));
  tr.append(el("td", m.late === null ? "—" : String(m.late), m.late ? "warn" : ""));
  tr.append(el("td", m.rush === null ? "—" : `${Math.round(m.rush * 100)}%`, m.rush >= 0.5 ? "warn" : ""));
  tr.append(el("td", r.split.text || "—", r.split.lopsided ? "warn" : ""));
  tr.append(el("td", r.state));
  return tr;
}

function toCsv(rows, d, tz) {
  const q = (v) => (/[",\n]/.test(String(v)) ? `"${String(v).replace(/"/g, '""')}"` : String(v));
  const lines = [["group", "classes", "repository", "started", "commits", "pushes", "days_active", "last_work",
                  "quiet_days", "soft_deadline", "after_soft", "final_2_days_share", "members", "access"]];
  for (const r of rows) {
    if (r.missing) { lines.push([r.t.name, r.classes.join(" "), r.name, "", "", "", "", "", "", "", "", "", "", "missing"]); continue; }
    const { m } = r;
    lines.push([r.t.name, r.classes.join(" "), r.name, m.started ? "yes" : "no", m.commits, m.pushes, m.days,
                m.last ? new Date(m.last).toISOString() : "", m.quiet ?? "", r.soft || "", m.late ?? "",
                m.rush === null ? "" : m.rush.toFixed(2), r.split.text, r.state]);
  }
  return lines.map((l) => l.map(q).join(",")).join("\n") + "\n";
}

function download(name, text) {
  const a = el("a");
  a.href = URL.createObjectURL(new Blob([text], { type: "text/csv" }));
  a.download = name;
  document.body.append(a);
  a.click();
  a.remove();
  setTimeout(() => URL.revokeObjectURL(a.href), 1000);
}

export function forgetGroups() { teams = null; }
export function keepLog(name, log) { kept = { name, log }; }
