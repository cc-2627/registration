// The assignment form: which assignments exist, the deadline fields, the folder.
import { $, setStatus } from "./dom.js";
import { gh, textFromB64 } from "./github.js";
import { hasClasses } from "./course.js";
import { ctx, session } from "./state.js";

const SKIP = new Set([".git", ".DS_Store", "__pycache__", ".pytest_cache", "node_modules"]);
export const MAX_BLOB = 10 * 1024 * 1024;

// Every assignment's definition (assignments/*.json), oldest first. The course
// repository is public, so this works without a token too.
export async function loadAssignments() {
  const { org, repo, token } = ctx;
  let items = [];
  try { items = await gh(token, "GET", `/repos/${org}/${repo}/contents/assignments`); }
  catch (e) { if (e.status !== 404) throw e; }
  const defs = await Promise.all(items.filter((i) => i.name.endsWith(".json")).map(async (i) => {
    try { return JSON.parse(textFromB64((await gh(token, "GET", `/repos/${org}/${repo}/contents/${i.path}`)).content)); }
    catch { return { name: i.name.slice(0, -5) }; }
  }));
  defs.sort((x, y) => (x.created || "").localeCompare(y.created || "") || x.name.localeCompare(y.name));
  session.assignments = defs.map((d) => d.name);
  const sel = $("aCarry");
  if (sel) {
    sel.length = 1;
    for (const a of session.assignments) sel.add(new Option(a, a));
  }
  return defs;
}

let softModeChosen = false;   // once someone picks, the page stops choosing for them

// A soft deadline in class weeks needs classes; without any, it's a date or none.
function refreshSoftModes() {
  const classes = hasClasses();
  const sel = $("aSoftMode");
  sel.options[0].disabled = !classes;
  if (!classes && sel.value === "week") sel.value = "date";
  else if (classes && !softModeChosen) sel.value = "week";
  $("aSoftWeekWrap").hidden = sel.value !== "week";
  $("aSoftTimeWrap").hidden = sel.value !== "week";
  $("aSoftDateWrap").hidden = sel.value !== "date";
}

// The folder's files, with paths inside it, minus what never belongs in a repo.
export function folderFiles() {
  return [...$("aFolder").files]
    .map((f) => ({ file: f, path: f.webkitRelativePath.split("/").slice(1).join("/") }))
    .filter(({ path }) => path && !path.split("/").some((p) => SKIP.has(p)));
}

export function initAssignmentForm() {
  document.addEventListener("classes-changed", refreshSoftModes);
  $("aSoftMode").addEventListener("change", () => { softModeChosen = true; refreshSoftModes(); });
  refreshSoftModes();
  $("aNoHard").addEventListener("change", () => { $("aHard").disabled = $("aNoHard").checked; });
  // Bringing their own work means an empty repository: no starting files, nothing carried over.
  $("aOwn").addEventListener("change", () => {
    const own = $("aOwn").checked;
    for (const id of ["aFolder", "aTemplate", "aCarry"]) $(id).disabled = own;
    for (const id of ["aStart", "folderStatus", "aCarryLabel"]) $(id).hidden = own;
    $("aOwnHint").hidden = !own;
  });
  $("aFolder").addEventListener("change", () => {
    const files = folderFiles();
    const big = files.filter((f) => f.file.size > MAX_BLOB).length;
    setStatus("folderStatus", files.length
      ? `${files.length} files${big ? `, ${big} over 10 MB will be skipped` : ""}. ` +
        "Browsers don't pass on file permissions, so scripts arrive without their executable bit."
      : "", big ? "warn" : "");
  });
}
