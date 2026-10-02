// What a page knows about the course it's working on. Tokens live here, in
// memory only: nothing is ever saved, so closing the tab forgets them.

// org and repo name the course; token is the one the person pasted, once checked.
export const ctx = { org: "", repo: "registration", token: "", login: null };

export const session = { branch: "main", repoExists: false, assignments: [] };

// The student lists, once read: numbers only.
export const students = { roster: null, rosterText: null, classes: null };
