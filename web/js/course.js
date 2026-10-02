// The course form, and course.json in and out of it.
import { $ } from "./dom.js";

export const WEEKDAYS = ["monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday"];

// Anything that depends on whether the course has classes listens for this.
const classesChanged = () => document.dispatchEvent(new Event("classes-changed"));

export function initCourseForm() {
  const tz = $("cTz");
  const zones = Intl.supportedValuesOf ? Intl.supportedValuesOf("timeZone") : ["UTC"];
  if (!zones.includes("UTC")) zones.unshift("UTC");
  for (const z of zones) tz.add(new Option(z, z));
  tz.value = Intl.DateTimeFormat().resolvedOptions().timeZone || "UTC";
  $("addClass").onclick = () => { addClassRow(); classesChanged(); };
  addClassRow();
}

function addClassRow(name = "", day = "monday", ends = "", note = "") {
  const tr = document.createElement("tr");
  tr.innerHTML = `<td><input placeholder="P1" aria-label="Class"></td>
    <td><select aria-label="Day">${WEEKDAYS.map((d) =>
      `<option value="${d}">${d[0].toUpperCase() + d.slice(1)}</option>`).join("")}</select></td>
    <td><input type="time" aria-label="Session ends"></td>
    <td><input placeholder="Lab 2" aria-label="Note"></td>
    <td><button class="iconbtn" type="button" aria-label="Remove class">×</button></td>`;
  const [n, d, e, o] = tr.querySelectorAll("input, select");
  n.value = name; d.value = day; e.value = ends; o.value = note;
  tr.querySelector("button").onclick = () => { tr.remove(); classesChanged(); };
  n.addEventListener("input", classesChanged);
  $("classRows").append(tr);
}

export function hasClasses() {
  return [...$("classRows").children].some((tr) => tr.querySelector("input").value.trim());
}

// {course, errors}: the form as course.json, with the same fields in the same
// order as scripts/setup/course.py writes them.
export function readCourse() {
  const errors = [];
  const name = $("cName").value.trim();
  if (!name) errors.push("The course needs a name.");
  const min = parseInt($("cMin").value, 10), max = parseInt($("cMax").value, 10);
  if (!(min >= 1 && min <= max && max <= 6)) errors.push("Group size must be 1 to 6, smallest first.");
  const classes = {};
  for (const tr of $("classRows").children) {
    const [n, d, e, o] = tr.querySelectorAll("input, select");
    const cls = n.value.trim().toUpperCase().replace(/\s+/g, "");
    if (!cls && !e.value) continue;
    if (!/^[A-Z0-9]+$/.test(cls)) { errors.push(`'${n.value}' isn't a usable class name (letters and digits, like P1).`); continue; }
    if (!/^([01]\d|2[0-3]):[0-5]\d$/.test(e.value)) { errors.push(`Class ${cls} needs the time its session ends.`); continue; }
    if (classes[cls]) { errors.push(`Class ${cls} is listed twice.`); continue; }
    classes[cls] = { day: d.value, ends: e.value };
    if (o.value.trim()) classes[cls].note = o.value.trim();
  }
  const course = { name, group_size: { min, max }, timezone: $("cTz").value, classes };
  const hint = $("cHint").value.trim();
  if (hint) course.number_hint = hint;
  return { course, errors };
}

export function fillCourse(c) {
  const tz = $("cTz");
  $("cName").value = c.name || "";
  $("cMin").value = c.group_size?.min ?? 2;
  $("cMax").value = c.group_size?.max ?? c.group_size?.min ?? 2;
  if (c.timezone) {
    if (![...tz.options].some((o) => o.value === c.timezone)) tz.add(new Option(c.timezone, c.timezone));
    tz.value = c.timezone;
  }
  $("cHint").value = c.number_hint || "";
  $("classRows").innerHTML = "";
  for (const [k, v] of Object.entries(c.classes || {})) {
    addClassRow(k, (v.day || "monday").toLowerCase(), v.ends || "", v.note || "");
  }
  if (!$("classRows").children.length) addClassRow();
  classesChanged();
}
