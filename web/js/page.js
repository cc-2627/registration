// What every page has: the theme switch, and links to the bedel repository
// the page was made from.
import { $ } from "./dom.js";
import { TEMPLATE } from "./github.js";

export function initPage() {
  for (const a of document.querySelectorAll("a[data-template]")) {
    a.href = `https://github.com/${TEMPLATE}${a.dataset.template}`;
    if (a.dataset.label !== undefined) a.textContent = `github.com/${TEMPLATE}`;
  }
  // Light by default; dark is the reader's choice, remembered in this browser.
  const root = document.documentElement;
  try { if (localStorage.getItem("theme") === "dark") root.dataset.theme = "dark"; } catch {}
  $("themeBtn").onclick = () => {
    const dark = root.dataset.theme !== "dark";
    if (dark) root.dataset.theme = "dark"; else delete root.dataset.theme;
    try { localStorage.setItem("theme", dark ? "dark" : "light"); } catch {}
  };
}
