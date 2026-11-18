import { html, useState, useEffect } from "/static/vendor/standalone.module.js";

/* ------------------------------------------------------------------ helpers */
const toastBus = { fn: null };
export const toast = (msg) => toastBus.fn && toastBus.fn(msg);
export const clip = (s, n = 80) => (s && s.length > n ? s.slice(0, n - 1) + "…" : s || "");
export const ago = (iso) => {
  const s = Math.max(0, (Date.now() - new Date(iso).getTime()) / 1000);
  if (s < 60) return "just now";
  if (s < 3600) return `${Math.floor(s / 60)} min ago`;
  if (s < 86400) return `${Math.floor(s / 3600)} h ago`;
  return new Date(iso).toLocaleDateString();
};
export const who = (a) => (a ? (a.kind === "human" ? "You" : a.name) : "");
export const guard = (fn) => async (...args) => {
  try { return await fn(...args); } catch (e) { toast(e.message); }
};
export const ROLE_ICON = { planner: "🗺️", builder: "🧱", critic: "🔎", tester: "🧪", refiner: "🛠️" };
export const MODES = [
  ["manual", "I approve everything", "Every proposal waits for you"],
  ["ask", "Agents ask me", "Confident changes apply; the rest wait for you"],
  ["auto", "Fully automatic", "Everything applies; nothing blocks"],
];
export const lines = (s) => s.split("\n").map((x) => x.trim()).filter(Boolean);

/* --------------------------------------------------------------- tiny parts */
export function Badge({ kind = "", children }) {
  return html`<span class="badge ${kind}">${children}</span>`;
}
export function Field({ label, children }) {
  return html`<div><label>${label}</label>${children}</div>`;
}
export function Modal({ title, onClose, children }) {
  return html`<div class="modal-bg" onClick=${(e) => e.target === e.currentTarget && onClose()}>
    <div class="modal">
      <div class="row" style="margin-bottom:8px"><h2 class="grow">${title}</h2>
        <button class="ghost" onClick=${onClose}>✕</button></div>
      ${children}
    </div></div>`;
}
export function Toast() {
  const [msg, setMsg] = useState(null);
  useEffect(() => {
    let t;
    toastBus.fn = (m) => { setMsg(m); clearTimeout(t); t = setTimeout(() => setMsg(null), 5000); };
    return () => { toastBus.fn = null; };
  }, []);
  return msg ? html`<div class="toast">${msg}</div>` : null;
}

export function ChangeChips({ diff, onPick }) {
  if (!diff || !diff.length) return null;
  const shown = diff.slice(0, 14);
  return html`<div class="chg">
    ${shown.map((c) => html`<span class="chip ${c.change} ${c.kind === "relation" ? "rel-chip" : ""}"
      title=${c.detail ? `${c.change}: ${c.detail}` : c.change}
      onClick=${() => onPick && c.change !== "removed" && onPick({ kind: c.kind, id: c.id })}>
      ${c.change === "added" ? "+" : c.change === "removed" ? "−" : "~"} ${clip(c.label || c.id, 38)}
    </span>`)}
    ${diff.length > shown.length && html`<span class="muted small">+${diff.length - shown.length} more</span>`}
  </div>`;
}

/* ------------------------------------------------------------ side switch */
export function SideSwitch({ side, pid }) {
  const go = (s) => { location.hash = `#/${s}/${encodeURIComponent(pid)}`; };
  return html`<div class="seg" title="Switch between building the graph and learning from it">
    <button class=${side === "build" ? "on" : ""} onClick=${() => go("build")}>🛠 Build</button>
    <button class=${side === "learn" ? "on" : ""} onClick=${() => go("learn")}>🎓 Learn</button>
  </div>`;
}

/* ------------------------------------------------------------ area bar */
// Collapse / expand the graph's areas (super-nodes). `expanded` is a Set of area ids.
export function AreaBar({ areas, expanded, onToggle, onAll, onNone }) {
  const [open, setOpen] = useState(false);  // the area chips repeat the super-nodes: off by default
  if (!areas || areas.length === 0) return null;
  const n = areas.filter((a) => expanded.has(a.id)).length;
  return html`<div class="card areabar">
    <button class="sm ${n === 0 ? "on-area" : ""}" onClick=${onNone} title="Collapse every area into one big node; click a node to open it">◱ Overview</button>
    <button class="sm ${n === areas.length ? "on-area" : ""}" onClick=${onAll} title="Show every concept">Expand all</button>
    <button class="ghost sm" onClick=${() => setOpen(!open)} title="Open or close single areas">${open ? "▾" : "▸"} ${n}/${areas.length} areas open</button>
    ${open && areas.map((a) => html`<button class="sm ${expanded.has(a.id) ? "on-area" : ""}" key=${a.id}
      onClick=${() => onToggle(a.id)} title=${expanded.has(a.id) ? "Collapse" : "Expand"}>
      ${expanded.has(a.id) ? "▾" : "▸"} ${a.title}</button>`)}
  </div>`;
}
