// Graph views ("lenses", ADR 0012): connected, possibly overlapping subgraphs picked from a
// panel. Reusable by any graph screen:
//   const lens = useLens({ pid, version, author });
//   const shown = lens.display(data);            // the graph data to draw
//   if (lens.onSelect(sel)) return;              // let the lens consume clicks while editing
//   <${LensToggle} lens=${lens} />  <${LensBanner} lens=${lens} />
//   <${LensPanel} lens=${lens} selected=${conceptId} />
import { html, useState, useEffect, useCallback } from "/static/vendor/standalone.module.js";
import { project } from "./api.js";
import { toast, clip, guard } from "./ui.js";
import { glowView } from "./graph.js";

export const VIEW_KINDS = {
  overview: { icon: "🗺", label: "Overview" },
  area: { icon: "▦", label: "Areas" },
  subtopic: { icon: "◈", label: "Subtopics" },
  question: { icon: "❓", label: "Questions" },
  exercise: { icon: "✎", label: "Exercises" },
  path: { icon: "↳", label: "Paths" },
  custom: { icon: "★", label: "My views" },
};
const SAVE_KINDS = ["subtopic", "question", "overview", "custom"];

/** Keep only a view's concepts (+ connecting ones, + optional one-step context) in graph data. */
export function applyView(data, view, { context = false } = {}) {
  if (!data || !view) return data;
  const members = new Set(view.concept_ids);
  const bridges = new Set(view.bridge_ids || []);
  const focus = new Set(view.focus_ids || []);
  const keep = new Set([...members, ...bridges]);
  const ctx = new Set();
  if (context) {
    for (const e of data.edges) {
      if (keep.has(e.source) && !keep.has(e.target)) ctx.add(e.target);
      if (keep.has(e.target) && !keep.has(e.source)) ctx.add(e.source);
    }
  }
  const shown = new Set([...keep, ...ctx]);
  const nodes = data.nodes.filter((n) => shown.has(n.id)).map((n) => ({
    ...n, bridge: bridges.has(n.id) && !members.has(n.id), vfocus: focus.has(n.id), ctx: ctx.has(n.id),
  }));
  const edges = data.edges.filter((e) => shown.has(e.source) && shown.has(e.target))
    .map((e) => (ctx.has(e.source) || ctx.has(e.target) ? { ...e, ctx: true } : e));
  return { ...data, nodes, edges, total: nodes.length - ctx.size };
}

/** State and actions for views on one graph screen. */
export function useLens({ pid, version, author = "expert" }) {
  const [views, setViews] = useState([]);
  const [active, setActive] = useState(null);
  const [context, setContext] = useState(false);
  const [glow, setGlow] = useState(false); // light the view up inside the whole graph
  const [editing, setEditing] = useState(null); // { ids: Set, base: view | null }
  const [open, setOpen] = useState(false);
  const P = project(pid);
  const reload = useCallback(guard(async () => setViews(await P.get("/views"))), [pid]);
  useEffect(() => { reload(); }, [pid, version]);
  useEffect(() => {  // a refreshed list carries the newest members of the active view
    if (!active) return;
    const v = views.find((x) => x.id === active.id);
    if (v && JSON.stringify(v) !== JSON.stringify(active)) setActive(v);
  }, [views]);

  const pick = (v) => { setEditing(null); setActive((cur) => (v && cur && cur.id === v.id ? null : v)); };
  const fetchView = (path) => guard(async () => { setEditing(null); setActive(await P.get(path)); })();
  const lens = {
    views, active, context, editing, open, author, glow,
    focused: !!((active && !glow) || editing),  // the graph is cut down to the view
    setOpen, setContext, setGlow, pick, reload,
    clear: () => setActive(null),
    neededFor: (cid) => fetchView(`/views/needed-for/${encodeURIComponent(cid)}`),
    around: (cid) => fetchView(`/views/around/${encodeURIComponent(cid)}`),
    startEdit: (base = null) => {
      setOpen(true);
      setEditing({ ids: new Set(base ? [...base.concept_ids, ...(base.bridge_ids || [])] : []), base });
    },
    cancelEdit: () => setEditing(null),
    toggleMember: (id) => setEditing((e) => {
      const ids = new Set(e.ids);
      ids.has(id) ? ids.delete(id) : ids.add(id);
      return { ...e, ids };
    }),
    save: guard(async ({ title, kind, description }) => {
      const base = editing.base;
      const r = await P.post("/views", {
        title, kind, description, author, concept_ids: [...editing.ids],
        focus_ids: base ? base.focus_ids : [], id: base && base.saved ? base.id : null,
      });
      toast("View saved");
      setEditing(null);
      const list = await P.get("/views");
      setViews(list);
      setActive(list.find((v) => v.id === r.id) || null);
    }),
    remove: (v) => guard(async () => {
      if (!confirm(`Delete the view "${v.title}"? The graph is not affected.`)) return;
      await P.del(`/views/${encodeURIComponent(v.id)}`);
      if (active && active.id === v.id) setActive(null);
      reload();
    })(),
    /** Graph data to draw: the whole graph while editing (members marked), else the view. */
    display: (data) => {
      if (!data) return data;
      if (editing) return { ...data, nodes: data.nodes.map((n) => ({ ...n, vfocus: editing.ids.has(n.id) })) };
      if (active && glow) return glowView(data, [...active.concept_ids, ...(active.bridge_ids || [])]);
      return active ? applyView(data, active, { context }) : data;
    },
    /** True if the click was used to edit a view (the screen should then ignore it). */
    onSelect: (sel) => {
      if (editing && sel && sel.kind === "concept") { lens.toggleMember(sel.id); return true; }
      return false;
    },
  };
  return lens;
}

/* ----------------------------------------------------------------- parts */

export function LensToggle({ lens }) {
  return html`<button class="sm ${lens.open ? "on-area" : ""}" onClick=${() => lens.setOpen(!lens.open)}
    title="Focus on part of the graph: an area, a question, an exercise, an overview…">🔭 Views</button>`;
}

export function LensBanner({ lens }) {
  const v = lens.active;
  if (lens.editing) {
    return html`<div class="card lens-banner editing">✎ Click concepts to add or remove them ·
      <b>${lens.editing.ids.size}</b> chosen <button class="sm" onClick=${lens.cancelEdit}>Cancel</button></div>`;
  }
  if (!v) return null;
  const kind = VIEW_KINDS[v.kind] || VIEW_KINDS.custom;
  return html`<div class="card lens-banner">
    <span title=${v.description || ""}>${kind.icon} <b>${clip(v.title, 70)}</b></span>
    <span class="muted">${v.concept_ids.length} concepts${v.bridge_ids.length ? ` + ${v.bridge_ids.length} connecting` : ""}${v.connected ? "" : " · not fully connected"}</span>
    <label class="inline" title="Keep the whole graph and make this view glow inside it">
      <input type="checkbox" checked=${lens.glow} onChange=${(e) => lens.setGlow(e.target.checked)} /> ✨ glow</label>
    ${!lens.glow && html`<label class="inline" title="Also show concepts one step outside the view, faded">
      <input type="checkbox" checked=${lens.context} onChange=${(e) => lens.setContext(e.target.checked)} /> context</label>`}
    <button class="sm" onClick=${() => lens.startEdit(v)} title=${v.saved ? "Edit this view" : "Save an editable copy"}>✎</button>
    <button class="sm" onClick=${lens.clear} title="Back to the whole graph">✕</button>
  </div>`;
}

function EditForm({ lens }) {
  const base = lens.editing.base;
  const [title, setTitle] = useState(base ? (base.saved ? base.title : `${base.title} (copy)`) : "");
  const [kind, setKind] = useState(base && SAVE_KINDS.includes(base.kind) ? base.kind : "subtopic");
  const [description, setDescription] = useState(base ? base.description : "");
  const n = lens.editing.ids.size;
  return html`<div class="lens-edit">
    <div class="small muted">Click concepts on the graph to add or remove them (${n} chosen).</div>
    <input placeholder="Title, e.g. “Proof of the JL lemma”" value=${title} onInput=${(e) => setTitle(e.target.value)} />
    <select value=${kind} onChange=${(e) => setKind(e.target.value)}>
      ${SAVE_KINDS.map((k) => html`<option value=${k}>${VIEW_KINDS[k].icon} ${VIEW_KINDS[k].label}</option>`)}</select>
    <textarea placeholder="What this view is for (optional)" value=${description}
      onInput=${(e) => setDescription(e.target.value)} style="min-height:44px"></textarea>
    <div class="row"><button class="primary sm" disabled=${!title.trim() || n === 0}
      onClick=${() => lens.save({ title: title.trim(), kind, description })}>Save view</button>
      <button class="sm" onClick=${lens.cancelEdit}>Cancel</button></div>
  </div>`;
}

export function LensPanel({ lens, selected, selectedName }) {
  const [q, setQ] = useState("");
  const [closed, setClosed] = useState(new Set(["exercise"]));
  if (!lens.open) return null;
  const needle = q.trim().toLowerCase();
  const match = (v) => !needle || v.title.toLowerCase().includes(needle) || (v.description || "").toLowerCase().includes(needle);
  const groups = {};
  for (const v of lens.views.filter(match)) {
    const k = v.saved && !["subtopic", "question", "overview"].includes(v.kind) ? "custom" : v.kind;
    (groups[k] ||= []).push(v);
  }
  const flip = (k) => { const s = new Set(closed); s.has(k) ? s.delete(k) : s.add(k); setClosed(s); };
  return html`<div class="card lens-panel">
    <div class="row"><b class="grow">🔭 Views</b><button class="ghost sm" onClick=${() => lens.setOpen(false)}>✕</button></div>
    <div class="small muted">Look at one part of the graph at a time. Views may overlap.</div>
    ${selected && html`<div class="lens-sel">
      <div class="small muted">For “${clip(selectedName || selected, 40)}”:</div>
      <div class="row wrap"><button class="sm" onClick=${() => lens.neededFor(selected)}>↳ Everything needed</button>
        <button class="sm" onClick=${() => lens.around(selected)}>◎ Around it</button></div></div>`}
    ${lens.editing ? html`<${EditForm} lens=${lens} />`
      : html`<button class="sm" style="align-self:flex-start" onClick=${() => lens.startEdit(null)}>＋ New view</button>`}
    <input placeholder="Search views…" value=${q} onInput=${(e) => setQ(e.target.value)} />
    <div class="lens-list">
      ${Object.entries(VIEW_KINDS).filter(([k]) => groups[k]).map(([k, meta]) => html`<div key=${k}>
        <div class="lens-group" onClick=${() => flip(k)}>${closed.has(k) && !needle ? "▸" : "▾"} ${meta.icon} ${meta.label}
          <span class="muted">${groups[k].length}</span></div>
        ${(!closed.has(k) || needle) && groups[k].map((v) => html`<div key=${v.id}
          class="lens-item ${lens.active && lens.active.id === v.id ? "on" : ""}" title=${v.description || v.title}
          onClick=${() => lens.pick(v)}>
          <span class="grow">${clip(v.title, 64)}</span>
          <span class="muted small">${v.concept_ids.length}${v.bridge_ids.length ? `+${v.bridge_ids.length}` : ""}</span>
          ${v.saved && html`<button class="ghost sm" title="Delete this view"
            onClick=${(e) => { e.stopPropagation(); lens.remove(v); }}>🗑</button>`}
        </div>`)}
      </div>`)}
      ${lens.views.length === 0 && html`<div class="muted small">No views yet. They appear once the graph has areas, tests or exercises.</div>`}
    </div>
  </div>`;
}
