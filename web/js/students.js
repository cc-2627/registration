// Reading the student lists. Everything here runs on the files in the browser;
// only the numbers (and classes) it returns are ever sent anywhere.
import { $, setStatus } from "./dom.js";
import { students } from "./state.js";

export async function readText(file) {
  const buf = new Uint8Array(await file.arrayBuffer());
  if (buf[0] === 0xff && buf[1] === 0xfe) return new TextDecoder("utf-16le").decode(buf.subarray(2));
  if (buf[0] === 0xfe && buf[1] === 0xff) return new TextDecoder("utf-16be").decode(buf.subarray(2));
  try { return new TextDecoder("utf-8", { fatal: true }).decode(buf).replace(/^﻿/, ""); }
  catch { return new TextDecoder("latin1").decode(buf); }
}

export const byNumber = (a, b) => a.length - b.length || (a < b ? -1 : a > b ? 1 : 0);

// The same headers scripts/make_roster.py looks for.
const CANDIDATES = ["nº", "n.º", "n°", "no", "número", "numero",
                    "number", "student number", "student no", "student id", "id"];

function splitRow(line, delim) {
  const out = [];
  let cur = "", quoted = false;
  for (let i = 0; i < line.length; i++) {
    const ch = line[i];
    if (quoted) {
      if (ch === '"' && line[i + 1] === '"') { cur += '"'; i++; }
      else if (ch === '"') quoted = false;
      else cur += ch;
    } else if (ch === '"') quoted = true;
    else if (ch === delim) { out.push(cur); cur = ""; }
    else cur += ch;
  }
  out.push(cur);
  return out;
}

// {numbers} from a CSV export or a plain list of numbers, or {headers} when the
// student-number column has to be picked by hand.
export function parseRoster(text, column) {
  const lines = text.split(/\r?\n/).map((l) => l.trim()).filter(Boolean);
  if (!lines.length) throw new Error("The file is empty.");
  if (!column && lines.every((l) => /^\d+$/.test(l))) return { numbers: [...new Set(lines)].sort(byNumber) };
  const delim = [";", ",", "\t"].map((d) => [d, splitRow(lines[0], d).length]).sort((a, b) => b[1] - a[1])[0][0];
  const rows = lines.map((l) => splitRow(l, delim));
  const header = rows[0].map((h) => h.trim());
  const wanted = column ? [column.toLowerCase()] : CANDIDATES;
  const col = header.findIndex((h) => wanted.includes(h.toLowerCase()));
  if (col < 0) return { headers: header.filter(Boolean) };
  const numbers = new Set();
  for (const r of rows.slice(1)) {
    const v = (r[col] || "").trim();
    if (/^\d+$/.test(v)) numbers.add(v);
  }
  return { numbers: [...numbers].sort(byNumber) };
}

// [number, class] pairs. Mirrors scripts/make_classes.py: the class a file
// declares on its header line, never its filename. Also takes a file of
// "number,class" lines.
const CLASS_LINE = /\b(?:Turno|Class):\s*([A-Za-z]+\s*\d+)/i;
export function parseListing(name, text) {
  const lines = text.split(/\r?\n/);
  const header = lines.slice(0, 10).map((l) => l.match(CLASS_LINE)).find(Boolean);
  if (header) {
    const cls = header[1].toUpperCase().replace(/\s+/g, "");
    const numbers = [];
    for (const line of lines) {
      const first = line.split("\t", 1)[0].trim();
      if (/^\d{4,6}$/.test(first)) numbers.push(first);
    }
    return numbers.map((n) => [n, cls]);
  }
  const pairs = [];
  for (const line of lines) {
    const m = line.trim().match(/^(\d{4,6})\s*[,;\t]\s*([A-Za-z]+\s*\d+)\s*$/);
    if (m) pairs.push([m[1], m[2].toUpperCase().replace(/\s+/g, "")]);
  }
  if (!pairs.length) throw new Error(`${name}: no 'Class: …' (or 'Turno: …') line and no 'number,class' lines.`);
  return pairs;
}

// Several listings into one sorted list, the first class kept for anyone listed twice.
export function mergeListings(listings) {
  const seen = new Map(), counts = {};
  let twice = 0;
  for (const pairs of listings) {
    for (const [n, cls] of pairs) {
      if (seen.has(n)) { if (seen.get(n) !== cls) twice++; continue; }
      seen.set(n, cls);
      counts[cls] = (counts[cls] || 0) + 1;
    }
  }
  return { pairs: [...seen.entries()].sort((a, b) => byNumber(a[0], b[0])), counts, twice };
}

// Wires the roster and class-listing file inputs (#roster, #rosterCol, #listings)
// into `students`. The files are read here, in the browser; only the numbers
// and classes found in them are ever sent anywhere.
export function initStudentInputs() {
  async function onRoster(column) {
    const file = $("roster").files[0];
    students.roster = null;
    if (!file) { setStatus("rosterStatus", ""); $("colWrap").hidden = true; return; }
    try {
      if (!column) students.rosterText = await readText(file);
      const res = parseRoster(students.rosterText, column);
      if (res.headers) {
        const sel = $("rosterCol");
        sel.length = 0;
        sel.add(new Option("Choose…", ""));
        for (const h of res.headers) sel.add(new Option(h, h));
        $("colWrap").hidden = false;
        setStatus("rosterStatus", "Which column holds the student numbers?", "warn");
        return;
      }
      if (!res.numbers.length) throw new Error("No student numbers found in that column.");
      students.roster = res.numbers;
      setStatus("rosterStatus", `${res.numbers.length} student numbers found. Only these numbers will be sent.`, "ok");
    } catch (e) { setStatus("rosterStatus", e.message, "bad"); }
    document.dispatchEvent(new Event("students-changed"));
  }
  $("roster").addEventListener("change", () => { $("colWrap").hidden = true; onRoster(); });
  $("rosterCol").addEventListener("change", (e) => e.target.value && onRoster(e.target.value));

  $("listings").addEventListener("change", async () => {
    students.classes = null;
    const files = [...$("listings").files];
    if (!files.length) { setStatus("listingStatus", ""); return; }
    try {
      const listings = [];
      for (const f of files) listings.push(parseListing(f.name, await readText(f)));
      const { pairs, counts, twice } = mergeListings(listings);
      students.classes = pairs;
      const summary = Object.entries(counts).sort().map(([c, k]) => `${c}: ${k}`).join(", ");
      setStatus("listingStatus", `${pairs.length} students across ${Object.keys(counts).length} classes (${summary}).` +
        (twice ? ` ${twice} listed twice, first class kept.` : ""), twice ? "warn" : "ok");
    } catch (e) { setStatus("listingStatus", e.message, "bad"); }
    document.dispatchEvent(new Event("students-changed"));
  });
}
