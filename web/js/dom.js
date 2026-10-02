// Small helpers for the page itself.

export const $ = (id) => document.getElementById(id);

export function setStatus(id, text, kind = "") {
  const el = $(id);
  el.textContent = text;
  el.className = "status " + kind;
}

// A list of steps that fill in as they run: log("doing x") adds a line and
// returns a function that rewrites it when the step finishes. Either can take
// actions to offer under the line: {label, href} opens a page, {label, run}
// is a button that runs `run` (and is disabled while it does).
export function logger(listId) {
  const list = $(listId);
  list.innerHTML = "";
  const write = (li, text, kind, actions) => {
    li.className = kind;
    li.textContent = "";
    const body = document.createElement("div");
    body.textContent = text;
    li.append(body);
    if (!actions?.length) return;
    const row = document.createElement("div");
    row.className = "actions";
    for (const a of actions) {
      if (a.href) {
        const link = document.createElement("a");
        link.className = "btn small";
        link.href = a.href;
        link.target = "_blank";
        link.rel = "noopener";
        link.textContent = a.label;
        row.append(link);
      } else {
        const b = document.createElement("button");
        b.type = "button";
        b.className = "btn small primary";
        b.textContent = a.label;
        b.onclick = async () => {
          b.disabled = true;
          try { await a.run(); } finally { b.disabled = false; }
        };
        row.append(b);
      }
    }
    li.append(row);
  };
  return (text, kind = "run", actions) => {
    const li = document.createElement("li");
    list.append(li);
    write(li, text, kind, actions);
    return (t, k = "ok", acts) => write(li, t, k, acts);
  };
}

// Runs a button's action, shows any error at the end of its log, and leaves no
// step looking as if it were still running.
export function runButton(buttonId, listId, action) {
  $(buttonId).onclick = async () => {
    const btn = $(buttonId);
    btn.disabled = true;
    try {
      await action();
    } catch (e) {
      const li = document.createElement("li");
      li.className = "bad";
      li.textContent = e.message;
      $(listId).append(li);
    } finally {
      for (const li of $(listId).querySelectorAll("li.run")) li.className = "warn";
      btn.disabled = false;
    }
  };
}
