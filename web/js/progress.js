// How a group's work is going, from a list of dated commits: shared by the
// teachers' table (push-log's rows) and a student's own view (the commits API).
// These are signals to start a conversation with a group, never a grade: one
// big commit and ten small ones are the same work.

const DAY = 86400000;
const SVG = "http://www.w3.org/2000/svg";

// push-log's CSV: quoted fields, with commas and newlines inside commit messages.
export function parseCsv(text) {
  const rows = [];
  let row = [], f = "", q = false;
  for (let i = 0; i < text.length; i++) {
    const c = text[i];
    if (q) {
      if (c === '"' && text[i + 1] === '"') { f += '"'; i++; }
      else if (c === '"') q = false;
      else f += c;
    } else if (c === '"') q = true;
    else if (c === ",") { row.push(f); f = ""; }
    else if (c === "\n") { row.push(f.replace(/\r$/, "")); rows.push(row); row = []; f = ""; }
    else f += c;
  }
  if (f || row.length) { row.push(f); rows.push(row); }
  const [head, ...body] = rows;
  return head ? body.filter((r) => r.length > 1).map((r) => Object.fromEntries(head.map((h, i) => [h, r[i] || ""]))) : [];
}

// The calendar day of a time, in the course's time zone, as "YYYY-MM-DD".
const formats = {};
export function dayOf(ms, tz) {
  formats[tz] ||= new Intl.DateTimeFormat("en-CA", { timeZone: tz, year: "numeric", month: "2-digit", day: "2-digit" });
  return formats[tz].format(new Date(ms));
}

// commits: [{ at: ms, who, pushed: bool, push: id }], where `pushed` says the time
// is the server's (a push) rather than the commit's own, which a client can set.
// since: when the group's repository was made; work from its first minutes is the
// starting files, unless the group brought its own.
export function measure(commits, { since, soft, hard, tz, own, now = Date.now() }) {
  const work = commits.filter((c) => own || !since || c.at > since + 5 * 60000);
  const days = new Set(work.map((c) => dayOf(c.at, tz)));
  const last = work.length ? Math.max(...work.map((c) => c.at)) : null;
  const end = hard ? Math.min(now, hard) : now;
  const whos = {};
  for (const c of work) whos[c.who || "unknown"] = (whos[c.who || "unknown"] || 0) + 1;
  // The final two days before the hard deadline, once those two days have begun.
  const rush = hard && now >= hard - 2 * DAY && work.length
    ? work.filter((c) => c.at > hard - 2 * DAY && c.at <= hard).length / work.length : null;
  return {
    started: work.length > 0,
    commits: work.length,
    pushes: new Set(work.filter((c) => c.push).map((c) => c.push)).size,
    days: days.size,
    last,
    quiet: last ? Math.floor((end - last) / DAY) : null,
    late: soft ? work.filter((c) => c.at > soft).length : null,
    rush,
    whos,
    unverified: work.filter((c) => !c.pushed).length,
  };
}

// Commits per day from `from` to `to`, oldest first.
export function perDay(commits, from, to, tz) {
  const counts = {};
  for (const c of commits) { const d = dayOf(c.at, tz); counts[d] = (counts[d] || 0) + 1; }
  const out = [];
  const last = dayOf(to, tz);
  // Hour steps, so a day that daylight saving makes 23 or 25 hours long isn't skipped.
  for (let t = from; out.length < 400; t += 3600000) {
    const d = dayOf(t, tz);
    if (d > last) break;
    if (!out.length || out[out.length - 1].day !== d) out.push({ day: d, n: counts[d] || 0 });
  }
  return out;
}

// A bar per day, with a line at each deadline. Drawn in SVG, so no library and
// nothing the page's Content-Security-Policy would stop.
export function sparkline(days, marks = [], { width = 160, height = 28 } = {}) {
  const svg = document.createElementNS(SVG, "svg");
  svg.setAttribute("viewBox", `0 0 ${width} ${height}`);
  svg.setAttribute("width", width);
  svg.setAttribute("height", height);
  svg.setAttribute("class", "spark");
  svg.setAttribute("preserveAspectRatio", "none");
  svg.setAttribute("role", "img");
  const total = days.reduce((s, d) => s + d.n, 0);
  svg.setAttribute("aria-label", `${total} commit${total === 1 ? "" : "s"} over ${days.length} day${days.length === 1 ? "" : "s"}`);
  if (!days.length) return svg;
  const max = Math.max(1, ...days.map((d) => d.n));
  const w = width / days.length;
  days.forEach((d, i) => {
    const r = document.createElementNS(SVG, "rect");
    const h = d.n ? Math.max(2, (d.n / max) * (height - 2)) : 1;
    r.setAttribute("x", (i * w).toFixed(2));
    r.setAttribute("y", (height - h).toFixed(2));
    r.setAttribute("width", Math.max(1, w - 1).toFixed(2));
    r.setAttribute("height", h.toFixed(2));
    r.setAttribute("class", d.n ? "on" : "off");
    const t = document.createElementNS(SVG, "title");
    t.textContent = `${d.day}: ${d.n}`;
    r.append(t);
    svg.append(r);
  });
  for (const m of marks) {
    const i = days.findIndex((d) => d.day === m.day);
    if (i < 0) continue;
    const l = document.createElementNS(SVG, "line");
    const x = (i * w + w / 2).toFixed(2);
    l.setAttribute("x1", x); l.setAttribute("x2", x);
    l.setAttribute("y1", 0); l.setAttribute("y2", height);
    l.setAttribute("class", m.kind);
    l.setAttribute("vector-effect", "non-scaling-stroke");
    const t = document.createElementNS(SVG, "title");
    t.textContent = m.label;
    l.append(t);
    svg.append(l);
  }
  return svg;
}

// A group's classes, from the bot's reply on its registration issue.
export function classesFromReply(comments) {
  const reply = [...comments].reverse().find((c) => (c.body || "").includes("registered as"));
  return [...new Set([...(reply?.body || "").matchAll(/→ \*\*([A-Za-z0-9]+)\*\*/g)].map((x) => x[1]))];
}

// The default rule (rules.py): a week from the earliest member's session, so the
// earliest of the group's classes. A date set by hand applies to everyone.
export function softFor(d, classes) {
  const byClass = d.soft_by_class || {};
  if (!d.soft_manual && classes.length && classes.some((c) => byClass[c])) {
    return classes.map((c) => byClass[c]).filter(Boolean).sort((a, b) => Date.parse(a) - Date.parse(b))[0];
  }
  return d.soft_deadline || null;
}

// "alice 70% · bob 30%", and whether one member has nearly all of it.
// Only a prompt to talk to the group: pair programming looks just like this.
export function split(whos, members = 2) {
  const total = Object.values(whos).reduce((s, n) => s + n, 0);
  const parts = Object.entries(whos).sort((a, b) => b[1] - a[1]);
  return {
    text: parts.map(([w, n]) => `${w} ${Math.round((n / total) * 100)}%`).join(" · "),
    lopsided: members > 1 && total >= 5 && parts[0][1] / total >= 0.85,
  };
}
