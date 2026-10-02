// Talking to GitHub's API, straight from the browser. Nothing else is contacted:
// the pages' Content-Security-Policy lets them connect to api.github.com only.

// The repository courses are created from. A fork of bedel can point its own
// page at itself with ?template=owner/repo.
export const TEMPLATE = new URLSearchParams(location.search).get("template") || "ajccosta/bedel";
const API = "https://api.github.com";

export const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

export class GitHubError extends Error {
  constructor(status, message) { super(message); this.status = status; }
}

export async function gh(token, method, path, body) {
  const headers = { Accept: "application/vnd.github+json", "X-GitHub-Api-Version": "2022-11-28" };
  if (token) headers.Authorization = `Bearer ${token}`;
  if (body !== undefined) headers["Content-Type"] = "application/json";
  let r;
  try {
    r = await fetch(API + path, { method, headers, body: body === undefined ? undefined : JSON.stringify(body) });
  } catch (e) {
    throw new GitHubError(0, `Couldn't reach GitHub (${e.message}).`);
  }
  const text = await r.text();
  let data = null;
  try { data = text ? JSON.parse(text) : null; } catch { data = text; }
  if (!r.ok) {
    let msg = (data && data.message) || `HTTP ${r.status}`;
    if (data && Array.isArray(data.errors) && data.errors.length) {
      msg += ": " + data.errors.map((e) => e.message || e.code || JSON.stringify(e)).join("; ");
    }
    throw new GitHubError(r.status, msg);
  }
  return data;
}

export async function exists(token, path) {
  try { await gh(token, "GET", path); return true; }
  catch (e) { if (e.status === 404) return false; throw e; }
}

export async function paginate(token, path) {
  const out = [];
  for (let page = 1; page < 50; page++) {
    const sep = path.includes("?") ? "&" : "?";
    const items = await gh(token, "GET", `${path}${sep}per_page=100&page=${page}`);
    out.push(...items);
    if (items.length < 100) break;
  }
  return out;
}

export function b64FromBytes(bytes) {
  let s = "";
  for (let i = 0; i < bytes.length; i += 0x8000) s += String.fromCharCode(...bytes.subarray(i, i + 0x8000));
  return btoa(s);
}
export const b64FromText = (text) => b64FromBytes(new TextEncoder().encode(text));
export const textFromB64 = (b64) =>
  new TextDecoder().decode(Uint8Array.from(atob(b64.replace(/\s/g, "")), (c) => c.charCodeAt(0)));

// libsodium is big (2 MB) and only needed to store a secret, so it loads then.
// It's a copy kept in web/vendor, not fetched from anywhere else.
let sodiumReady = null;
export function loadSodium() {
  sodiumReady ||= import("../vendor/libsodium-wrappers.js").then(async (m) => {
    await m.default.ready;
    return m.default;
  });
  return sodiumReady;
}

// Seals a value with a repository's public key, as GitHub requires of secrets:
// only Actions, holding the private key, can open it.
export async function seal(publicKey, value) {
  const sodium = await loadSodium();
  const b64 = sodium.base64_variants.ORIGINAL;
  return sodium.to_base64(sodium.crypto_box_seal(sodium.from_string(value), sodium.from_base64(publicKey, b64)), b64);
}

export async function setSecret(token, org, repo, name, value) {
  const key = await gh(token, "GET", `/repos/${org}/${repo}/actions/secrets/public-key`);
  await gh(token, "PUT", `/repos/${org}/${repo}/actions/secrets/${name}`, {
    encrypted_value: await seal(key.key, value), key_id: key.key_id,
  });
}

// Can this token do a write? An empty body: GitHub checks permission before it
// reads the body, so a token that may gets 422 (bad request, nothing created)
// and one that may not gets 403.
export async function probe(token, path) {
  try { await gh(token, "POST", path, {}); return "unexpected"; }
  catch (e) { return e.status === 422 ? "ok" : e.status === 403 ? "denied" : `HTTP ${e.status}: ${e.message}`; }
}

// Waits for a workflow run picked out by `match` to finish, reporting progress.
export async function waitForRun(token, org, repo, workflow, match, update, minutes = 5) {
  const until = Date.now() + minutes * 60000;
  const started = Date.now();
  let run = null;
  while (Date.now() < until) {
    const runs = await gh(token, "GET", `/repos/${org}/${repo}/actions/workflows/${workflow}/runs?per_page=10`);
    run = (runs.workflow_runs || []).find(match) || null;
    if (run) {
      update(`${run.status.replace("_", " ")} — ${run.html_url}`);
      if (run.status === "completed") return run;
    } else if (Date.now() - started > 60000) {
      update(`waiting for GitHub Actions to start it… (check that Actions is enabled for ${org})`);
    }
    await sleep(4000);
  }
  throw new Error(run ? `Still running after ${minutes} minutes: ${run.html_url}` :
    `GitHub Actions never started ${workflow}. Check that Actions is enabled in the organization's settings.`);
}
