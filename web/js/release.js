// Releasing an assignment: its starting files, then the Release assignment
// workflow (.github/workflows/assignment.yml), which runs new_assignment.py.
import { $, logger } from "./dom.js";
import { gh, exists, sleep, b64FromBytes, waitForRun } from "./github.js";
import { ctx, session } from "./state.js";
import { loadAssignments, folderFiles, MAX_BLOB } from "./assignments.js";

const ASSIGNMENT_RE = /^[a-z0-9][a-z0-9_-]*$/;
const asDeadline = (v) => v.replace("T", " ");

// The assignment.yml inputs from the form; throws on what new_assignment.py would refuse.
function readInputs(name) {
  const isNew = !session.assignments.includes(name);
  const mode = $("aSoftMode").value;
  const inputs = { name };
  if (mode === "week" && $("aSoftWeek").value) {
    inputs.soft_week = $("aSoftWeek").value;
    if ($("aSoftTime").value) inputs.soft_time = $("aSoftTime").value;
  }
  else if (mode === "date" && $("aSoft").value) inputs.soft = asDeadline($("aSoft").value);
  else if (mode === "none") inputs.soft = "none";
  if ($("aNoHard").checked) inputs.hard = "none";
  else if ($("aHard").value) inputs.hard = asDeadline($("aHard").value);
  if (isNew && !inputs.soft_week && !inputs.soft) throw new Error(`${name} is new, so it needs a soft deadline (or None).`);
  if (isNew && !inputs.hard) throw new Error(`${name} is new, so it needs a hard deadline (or Never lock).`);
  if ($("aOwn").checked) inputs.own_work = "true";
  else if ($("aCarry").value) {
    if ($("aCarry").value === name) throw new Error("Continue from an earlier assignment, not this one.");
    inputs.carry_over = $("aCarry").value;
  }
  inputs.dry_run = $("aDry").checked ? "true" : "false";
  return inputs;
}

// Makes the repository's default branch exactly these files, in one commit.
async function pushFolder(token, org, repo, files, message) {
  const info = await gh(token, "GET", `/repos/${org}/${repo}`);
  const branch = info.default_branch || "main";
  let parents = [];
  try { parents = [(await gh(token, "GET", `/repos/${org}/${repo}/git/ref/heads/${branch}`)).object.sha]; }
  catch (e) { if (![404, 409].includes(e.status)) throw e; }
  const tree = [];
  for (const { file, path } of files) {
    if (file.size > MAX_BLOB) continue;
    const blob = await gh(token, "POST", `/repos/${org}/${repo}/git/blobs`,
      { content: b64FromBytes(new Uint8Array(await file.arrayBuffer())), encoding: "base64" });
    tree.push({ path, mode: "100644", type: "blob", sha: blob.sha });
  }
  // A whole new tree, not on top of the old one: the repository becomes exactly the folder.
  const t = await gh(token, "POST", `/repos/${org}/${repo}/git/trees`, { tree });
  const c = await gh(token, "POST", `/repos/${org}/${repo}/git/commits`, { message, tree: t.sha, parents });
  if (parents.length) await gh(token, "PATCH", `/repos/${org}/${repo}/git/refs/heads/${branch}`, { sha: c.sha });
  else await gh(token, "POST", `/repos/${org}/${repo}/git/refs`, { ref: `refs/heads/${branch}`, sha: c.sha });
  return tree.length;
}

async function putStartingFiles(log, token, org, name, template, dry) {
  const files = folderFiles();
  if (!files.length) {
    if (!dry && !(await exists(token, `/repos/${org}/${template}`))) {
      throw new Error(`${org}/${template} doesn't exist. Choose a folder with the starting files, name a repository that has them, or tick "Groups bring their own work".`);
    }
    return;
  }
  const s = log(`Putting your ${files.length} starting files in ${org}/${template}`);
  if (dry) return s(`Would put ${files.length} starting files in ${org}/${template} (dry run)`, "ok");
  if (!(await exists(token, `/repos/${org}/${template}`))) {
    await gh(token, "POST", `/orgs/${org}/repos`, { name: template, private: true, auto_init: true,
      description: `Starting files for ${name}` });
    for (let i = 0; i < 20; i++) {
      try { await gh(token, "GET", `/repos/${org}/${template}/git/ref/heads/main`); break; }
      catch { await sleep(1500); }
    }
  }
  const n = await pushFolder(token, org, template, files, `Starting files for ${name}`);
  s(`${n} starting files in ${org}/${template}`);
}

export async function release() {
  const log = logger("releaseLog");
  const { org, repo, token } = ctx;
  if (!ctx.login) throw new Error("Check a token for this course first.");
  const name = $("aName").value.trim();
  if (!ASSIGNMENT_RE.test(name)) throw new Error("The name must be lowercase letters, digits, - and _, like a1.");
  const inputs = readInputs(name);
  const dry = inputs.dry_run === "true";
  if (!inputs.own_work) {
    inputs.template = $("aTemplate").value.trim() || `${name}-template`;
    await putStartingFiles(log, token, org, name, inputs.template, dry);
  }

  const s = log(dry ? "Asking GitHub Actions for a dry run" : `Releasing ${name}`);
  const since = Date.now() - 10000;
  await gh(token, "POST", `/repos/${org}/${repo}/actions/workflows/assignment.yml/dispatches`,
    { ref: session.branch, inputs });
  const run = await waitForRun(token, org, repo, "assignment.yml",
    (r) => r.event === "workflow_dispatch" && Date.parse(r.created_at) >= since,
    (u) => s(`Release: ${u}`, "run"), 15);
  if (run.conclusion !== "success") throw new Error(`The release ${run.conclusion}. What went wrong is in its log: ${run.html_url}`);
  s(dry ? `Dry run finished. What would happen is in its log: ${run.html_url}`
        : `${name} released: every registered group has its repository and an issue about it`
          + (inputs.own_work ? ", with the commands to push their work in" : "") + `. Log: ${run.html_url}`);
  await loadAssignments();
}
