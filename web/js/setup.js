// The setup page: six steps, each checked before the next one opens.
import { $, setStatus, logger, runButton } from "./dom.js";
import { initPage } from "./page.js";
import { TEMPLATE, gh, exists, probe, loadSodium, seal, b64FromText, textFromB64 } from "./github.js";
import { SETUP_PERMISSIONS, BOT_PERMISSIONS, TOKENS_PAGE, tokenUrl, checkToken } from "./tokens.js";
import { initCourseForm, readCourse, fillCourse } from "./course.js";
import { initStudentInputs, parseRoster, parseListing, mergeListings } from "./students.js";
import { ctx, session, students } from "./state.js";
import * as ops from "./ops.js";

const LAST = 6;
let current = 1;
const done = new Set();                  // steps whose checks have passed
const stored = { bot: false, roster: false };   // what an existing course already has
let botChecked = false;

// ---------- moving between steps ----------
const reachable = (k) => [...Array(k - 1).keys()].every((i) => done.has(i + 1));

function show(n) {
  current = n;
  for (const s of document.querySelectorAll(".wstep")) s.hidden = Number(s.dataset.step) !== n;
  for (const b of document.querySelectorAll("#progress button")) {
    const k = Number(b.dataset.go);
    b.disabled = k !== n && !reachable(k);
    b.parentElement.className = k === n ? "is-current" : done.has(k) ? "is-done" : "";
    if (k === n) b.setAttribute("aria-current", "step"); else b.removeAttribute("aria-current");
  }
  $("back").hidden = n === 1;
  $("next").hidden = n === LAST;
  if (n === 4 && stored.roster && !students.roster) {
    setStatus("studentsStatus", "A roster is already stored. Choose a file only to replace it.", "ok");
  }
  if (n === 5 && stored.bot && !$("botToken").value.trim()) {
    setStatus("botStatus", "The bot already has a token stored. Leave this empty to keep it, or paste a new one to replace it.", "ok");
  }
  if (n === LAST) renderPlan();
  $("progress").scrollIntoView({ block: "nearest" });
}

// Whatever comes after `step` has to be checked again.
function invalidate(step) {
  for (const k of [...done]) if (k >= step) done.delete(k);
  show(current);
}

const CHECKS = {
  1: useOrg,
  2: () => ctx.login ? true : checkSetupToken(),
  3: () => {
    const { errors } = readCourse();
    setStatus("courseStatus", errors.join(" "), errors.length ? "bad" : "");
    return !errors.length;
  },
  4: () => {
    if (students.roster || stored.roster) return true;
    setStatus("studentsStatus", "Choose the roster file: it's who may register.", "bad");
    return false;
  },
  5: () => botChecked ? true : checkBotToken(),
};

async function next() {
  const btn = $("next");
  btn.disabled = true;
  try {
    if (await CHECKS[current]()) { done.add(current); show(current + 1); }
  } finally { btn.disabled = false; }
}

// ---------- 1. the organization ----------
function fillOrgNames() {
  for (const el of document.querySelectorAll(".orgname")) el.textContent = ctx.org || "your organization";
  refreshLinks();
}

// Only the name's shape is checked here, without asking GitHub: GitHub allows a page
// few requests without a token, and the setup token's check proves the
// organization exists, and that you own it, anyway.
function useOrg() {
  const name = $("org").value.trim();
  if (!name) { setStatus("orgStatus", "Enter the organization's name.", "bad"); return false; }
  if (!/^[A-Za-z0-9](?:[A-Za-z0-9]|-(?=[A-Za-z0-9]))*$/.test(name) || name.length > 39) {
    setStatus("orgStatus", "That isn't an organization name: letters, digits and single hyphens, at most 39.", "bad");
    return false;
  }
  ctx.org = name;
  ctx.repo = $("repo").value.trim() || "registration";
  setStatus("orgStatus", "");
  fillOrgNames();
  return true;
}

// ---------- 2. the setup token ----------
function checklist(id) {
  const list = $(id);
  list.innerHTML = "";
  return (text) => { const li = document.createElement("li"); li.textContent = text; list.append(li); };
}

async function checkSetupToken() {
  const passed = checklist("setupChecks");
  setStatus("setupStatus", "Checking…");
  const token = $("setupToken").value.trim();
  try {
    ctx.login = await checkToken(token, ctx.org, passed);
    ctx.token = token;
    ctx.org = (await gh(token, "GET", `/orgs/${ctx.org}`)).login;   // as GitHub spells it
    fillOrgNames();
    const { org, repo } = ctx;
    let message = `${org}/${repo} will be created at the end.`;
    if (await ops.findRepo(token, org, repo)) {
      const course = await ops.readCourseFile(token, org, repo);
      if (course) fillCourse(course);
      stored.bot = await exists(token, `/repos/${org}/${repo}/actions/secrets/ORG_ADMIN_TOKEN`);
      stored.roster = await exists(token, `/repos/${org}/${repo}/actions/secrets/ROSTER`);
      message = `${org}/${repo} already exists` + (course ? ", and its settings are loaded into the next steps" : "") +
        ". Setting up again changes only what differs.";
    }
    setStatus("setupStatus", message, "ok");
    return true;
  } catch (e) {
    ctx.login = null;
    setStatus("setupStatus", e.message, "bad");
    return false;
  }
}

// ---------- 5. the bot's token ----------
async function checkBotToken() {
  const passed = checklist("botChecks");
  const token = $("botToken").value.trim();
  if (!token && stored.bot) { botChecked = true; return true; }
  setStatus("botStatus", "Checking…");
  try {
    if (token && token === ctx.token) {
      throw new Error("That's the setup token. The bot needs its own, made with the link above, so the setup token can be deleted.");
    }
    const login = await checkToken(token, ctx.org, passed);
    if ((await probe(token, `/orgs/${ctx.org}/teams`)) === "denied") {
      throw new Error("This token can't create teams: Members must be Read and write. Make a new one with the link above.");
    }
    passed(`Can create teams and repositories in ${ctx.org}. The rest is checked once the course repository exists.`);
    setStatus("botStatus", `Ready to be sealed and stored. The bot will act as ${login}.`, "ok");
    botChecked = true;
    return true;
  } catch (e) {
    setStatus("botStatus", e.message, "bad");
    return false;
  }
}

// ---------- 6. setting it up ----------
const testCount = () => Math.max(0, Math.min(20, parseInt($("testN").value, 10) || 0));

function renderPlan() {
  const { org, repo } = ctx;
  const { course } = readCourse();
  const n = testCount();
  const items = [
    `Lock down what members of ${org} can do: no access beyond their own team's repositories, and they can't create, delete or publish repositories, or create teams.`,
    session.repoExists ? `Keep ${org}/${repo}, which already exists.`
      : `Create ${org}/${repo} from ${TEMPLATE}. It's public, so students who aren't members yet can open the registration form.`,
    "Add the labels the bot puts on registrations.",
    $("botToken").value.trim() ? "Check the bot's token can do everything it needs to, then seal it and store it as the secret ORG_ADMIN_TOKEN."
      : "Keep the bot's token already stored.",
    students.roster ? `Seal and store the roster: ${students.roster.length} student numbers` + (n ? `, and ${n} test students.` : ".")
      : "Keep the roster already stored.",
    ...(students.classes ? [`Seal and store the class listings: ${students.classes.length} students.`] : []),
    `Write course.json (${course.name}) and generate the students' page and registration form from it.`,
    `Publish the course's own page at ${ops.coursePageUrl(org, repo)}, where you run the course from then on.`,
  ];
  const ul = $("plan");
  ul.innerHTML = "";
  for (const t of items) { const li = document.createElement("li"); li.textContent = t; ul.append(li); }
}

async function runSetup() {
  const log = logger("setupLog");
  $("setupDone").className = "done";
  for (let k = 1; k < LAST; k++) if (!done.has(k)) throw new Error(`Step ${k} isn't finished yet.`);
  const { org, repo, token } = ctx;
  const { course, errors } = readCourse();
  if (errors.length) throw new Error(errors.join(" "));

  await ops.lockDownOrg(log, token, org);
  await ops.createRepo(log, token, org, repo, course.name);
  await ops.addLabels(log, token, org, repo);
  const bot = $("botToken").value.trim();
  if (bot) await ops.storeBotToken(log, token, org, repo, bot);
  else log("Keeping the bot's token already stored", "ok");
  const tests = await ops.storeStudents(log, token, org, repo, testCount());
  // course.json last: the workflows stay idle until it's there, so they never
  // start before the secrets they need.
  const headSha = await ops.writeCourse(log, token, org, repo, course);
  await ops.generateStudentFiles(log, token, org, repo, headSha);
  const page = await ops.publishCoursePage(log, token, org, repo, (url) => showDone(org, repo, tests, url));
  showDone(org, repo, tests, page);
}

function showDone(org, repo, tests, page) {
  const box = $("setupDone");
  box.innerHTML = `<strong>Your course is ready.</strong>
    <p>Give your students this link: <a data-k="form"></a></p>
    <p>Run the course from its own page from now on: <a data-k="page"></a></p>
    <p>Try it first with two accounts of your own${tests.length ? ` and the test numbers ${tests.slice(0, 2).join(" and ")}` : ""}.</p>
    <p><strong>Now delete the setup token</strong>, on <a data-k="tokens">GitHub's token page</a>, and close this tab.
      The course's page asks for a token of its own when you change something.</p>`;
  const link = (k, href, text) => { const a = box.querySelector(`[data-k=${k}]`); a.href = href; if (text) a.textContent = text; };
  const form = `https://github.com/${org}/${repo}/issues/new?template=register.yml`;
  link("form", form, form);
  const own = page || `manage.html?repo=${org}/${repo}`;
  link("page", own, page || "this course on bedel's page");
  link("tokens", TOKENS_PAGE);
  box.className = "done show";
}

// ---------- wiring ----------
function refreshLinks() {
  const org = ctx.org;
  const days = Math.min(366, Math.max(1, parseInt($("botDays").value, 10) || 180));
  $("setupTokenLink").href = tokenUrl(org, `bedel setup (${org})`,
    "Sets up the course with the bedel setup page. Delete it once setup is done.", 7, SETUP_PERMISSIONS);
  $("botTokenLink").href = tokenUrl(org, `bedel bot (${org})`,
    "The course bot's token: creates teams and repos, locks them at deadlines", days, BOT_PERMISSIONS);
}

initPage();
initCourseForm();
initStudentInputs();
fillOrgNames();
show(1);

$("org").addEventListener("keydown", (e) => { if (e.key === "Enter") next(); });
$("org").addEventListener("input", () => {
  ctx.org = ""; ctx.login = null; ctx.token = ""; botChecked = false;
  stored.bot = stored.roster = false;
  setStatus("orgStatus", "");
  for (const id of ["setupChecks", "botChecks"]) $(id).innerHTML = "";
  for (const id of ["setupStatus", "botStatus"]) setStatus(id, "");
  fillOrgNames();
  invalidate(1);
});
$("repo").addEventListener("input", () => {
  ctx.repo = $("repo").value.trim() || "registration";
  ctx.login = null;
  invalidate(2);
});
$("checkSetup").onclick = async () => { if (await checkSetupToken()) { done.add(2); show(2); } };
$("setupToken").addEventListener("input", () => { ctx.login = null; ctx.token = ""; $("setupChecks").innerHTML = ""; invalidate(2); });
$("checkBot").onclick = async () => { if (await checkBotToken()) { done.add(5); show(5); } };
$("botToken").addEventListener("input", () => { botChecked = false; $("botChecks").innerHTML = ""; invalidate(5); });
$("botDays").addEventListener("input", refreshLinks);
document.addEventListener("students-changed", () => setStatus("studentsStatus", ""));
for (const b of document.querySelectorAll("#progress button")) b.onclick = () => show(Number(b.dataset.go));
$("back").onclick = () => show(current - 1);
$("next").onclick = next;
runButton("runSetup", "setupLog", runSetup);

// For the page's own tests: the pure parts, without a network.
window.bedel = { parseRoster, parseListing, mergeListings, tokenUrl, readCourse, fillCourse, b64FromText,
                 textFromB64, loadSodium, seal, checkToken, SETUP_PERMISSIONS, BOT_PERMISSIONS };
