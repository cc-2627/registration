// Fine-grained tokens: the links that make them, and the checks before one is used.
import { gh, GitHubError } from "./github.js";

// GitHub fills a new fine-grained token in from these. Repository access is the
// one field it won't take from a link, which is why the pages ask for it in words.
// Each one is explained, permission by permission, where the token is asked for.
export const SETUP_PERMISSIONS = {
  organization_administration: "write", administration: "write", contents: "write", issues: "write",
  secrets: "write", actions: "write", workflows: "write", pages: "write", members: "read",
};
// The bot keeps its token all term, so it gets only what the workflows use.
export const BOT_PERMISSIONS = {
  administration: "write", contents: "write", issues: "write", statuses: "write", workflows: "write",
  members: "write",
};
// For running a course from its page: assignments, the course, the roster.
export const COURSE_PERMISSIONS = {
  administration: "write", contents: "write", secrets: "write", actions: "write", workflows: "write",
  members: "read",
};

export function tokenUrl(org, name, description, days, permissions) {
  const q = [`name=${encodeURIComponent(name.slice(0, 40))}`, `description=${encodeURIComponent(description)}`,
             `expires_in=${days}`];
  if (org) q.push(`target_name=${encodeURIComponent(org)}`);
  for (const [k, v] of Object.entries(permissions)) q.push(`${k}=${v}`);
  return `https://github.com/settings/personal-access-tokens/new?${q.join("&")}`;
}

export const TOKENS_PAGE = "https://github.com/settings/personal-access-tokens";

// Before a token is used for anything: it must be fine-grained, belong to `org`,
// and belong to an owner of it. Returns its owner's login. Each check passed is
// reported through `passed`, so the page can show them.
//
// A fine-grained token has exactly one resource owner, fixed when it's made. So a
// token that may create repositories in `org` can't reach anything outside it:
// not its owner's own repositories, not any other organization.
export async function checkToken(token, org, passed = () => {}) {
  if (!token) throw new Error("Paste the token first.");
  if (!token.startsWith("github_pat_")) {
    throw new Error(token.startsWith("ghp_")
      ? "That's a classic token, which reaches every repository you can, in every organization. " +
        "This page only takes fine-grained tokens, tied to one organization: make one with the link above."
      : "That isn't a fine-grained token (those start with github_pat_). Make one with the link above.");
  }
  passed("Fine-grained: it reaches one owner's repositories, and only with the permissions listed");

  let me;
  try { me = await gh(token, "GET", "/user"); }
  catch (e) {
    throw new Error(e.status === 401 ? "GitHub doesn't accept that token: copy it again, or make a new one." : e.message);
  }

  // A permitted write with an empty body is refused as malformed (422) and
  // creates nothing; a token without the permission is refused outright (403).
  try {
    await gh(token, "POST", `/orgs/${org}/repos`, {});
  } catch (e) {
    if (e instanceof GitHubError && e.status === 404) {
      throw new Error(`GitHub knows no organization called ${org} that this token can reach. Check the name, ` +
        `that you've created the organization, and that the token's Resource owner is ${org}.`);
    }
    if (!(e instanceof GitHubError) || e.status !== 422) {
      throw new Error(`This token can't work in ${org} (${e.message}). On its page on GitHub, check that ` +
        `Resource owner is ${org}, Repository access is All repositories, and Administration is Read and write. ` +
        "If your organization approves tokens, approve it under Settings → Personal access tokens.");
    }
  }
  passed(`Belongs to ${org}: nothing outside ${org} is reachable with it`);

  let role = null;
  try { role = (await gh(token, "GET", `/user/memberships/orgs/${org}`)).role; }
  catch (e) { throw new Error(`Couldn't read ${me.login}'s membership of ${org}: ${e.message}`); }
  if (role !== "admin") throw new Error(`${me.login} is a member of ${org} but not an owner, and this needs an owner.`);
  passed(`Made by ${me.login}, an owner of ${org}`);
  return me.login;
}
