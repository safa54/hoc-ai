// Learn side: explore the graph, read nodes, talk to the tutor, see what the system believes.
import {
  html, useState, useEffect, useRef, useMemo, useCallback,
} from "/static/vendor/standalone.module.js";
import { api, project } from "./api.js";
import { GraphController, groupView, glowView, GLOW } from "./graph.js";
import { toast, clip, ago, guard, Badge, SideSwitch, AreaBar } from "./ui.js";
import { Chat, TutorView } from "./chat.js";
import { Exercises } from "./exercises.js";
import { Tex, Formal } from "./math.js";
import { useLens, LensPanel, LensBanner, LensToggle } from "./views.js";

/* ---------------------------------------------------------------- encoding */
// One encoding for the graph, the reader and the map (LEARNING_SIDE_PLAN L2.3).
export const STATUS = {
  unknown:    { label: "not yet known", fill: "#e5e7eb", icon: "",   css: "unknown" },
  likely_not: { label: "struggling",    fill: "#fca5a5", icon: "! ", css: "bad" },
  uncertain:  { label: "shaky",         fill: "#fcd34d", icon: "~ ", css: "warn" },
  likely:     { label: "understood",    fill: "#86efac", icon: "✓ ", css: "ok" },
  mastered:   { label: "solid",         fill: "#22c55e", icon: "✓✓ ", css: "ok" },
};
export const GROUP_STATUS_ORDER = ["likely_not", "uncertain", "unknown", "likely", "mastered"];
const FACETS = ["recall", "apply", "explain", "transfer"];
const understood = (s) => s === "likely" || s === "mastered";

function nodeLabel(n) {
  const flag = n.flag === "got_it" ? "⚑ " : n.flag === "lost" ? "⚐ " : "";
  const seen = n.visited ? "• " : "";
  return `${n.stepMark || ""}${flag}${seen}${STATUS[n.status]?.icon || ""}${n.name}`;
}

function saved(key, fallback) {
  try { return localStorage.getItem(key) || fallback; } catch (_) { return fallback; }
}
function save(key, value) {
  try { localStorage.setItem(key, value); } catch (_) {}
}

/* ------------------------------------------------------------- graph canvas */
// A collapsed area is one node drawn as a progress ring: share struggling / shaky / understood.
function groupNode(area, list) {
  const total = list.length;
  const n = { bad: 0, warn: 0, ok: 0 };
  for (const m of list) {
    if (m.status === "likely_not") n.bad++;
    else if (m.status === "uncertain") n.warn++;
    else if (understood(m.status)) n.ok++;
  }
  const filled = Math.round((10 * n.ok) / total);
  const bar = "▰".repeat(filled) + "▱".repeat(10 - filled);
  const border = n.bad ? "#dc2626" : n.warn ? "#d97706" : n.ok === total ? "#16a34a" : "#64748b";
  const flags = [n.bad ? `! ${n.bad}` : "", n.warn ? `~ ${n.warn}` : ""].filter(Boolean).join("  ");
  return {
    name: area.title, group: true, kind: "concept", gborder: border,
    label: `${area.title}\n${bar} ${n.ok}/${total}${flags ? `\n${flags}` : ""}${list.some((m) => m.pathnext) ? "\n▶ your next step is here" : ""}`,
    status: n.bad ? "likely_not" : n.warn ? "uncertain" : "unknown",
    ready: list.some((m) => m.ready), visited: list.some((m) => m.visited), draft: false,
  };
}

// Which concepts of the learner's path to light up: all that is left on the way to a goal,
// or just the next few steps when the path is the whole topic.
const PATH_PREVIEW = 5;
export function pathSteps(path) {
  if (!path) return [];
  const done = new Set(path.done);
  const left = path.ids.filter((id) => !done.has(id));
  return path.goal ? left : left.slice(0, PATH_PREVIEW);
}

function PathBar({ data, steps, on, setOn, onFocus, onGoal }) {
  const path = data?.path;
  if (!path || path.ids.length === 0) return null;
  const name = (id) => data.nodes.find((n) => n.id === id)?.name || id;
  const left = path.ids.length - path.done.length;
  return html`<div class="card pathbar">
    <label class="inline-check" title="Light up your path on the graph"><input type="checkbox" checked=${on}
      onChange=${(e) => setOn(e.target.checked)} /> <span style=${`color:${GLOW.path}`}>✨</span> my path</label>
    ${path.goal ? html`<span title="You are working towards this concept">🎯 <b>${clip(path.goal_name, 36)}</b>
        <span class="muted"> · ${left === 0 ? "reached" : `${left} step${left > 1 ? "s" : ""} left`}</span></span>
        <button class="ghost sm" title="Drop the goal: the path covers the whole topic again" onClick=${() => onGoal(null)}>✕</button>`
      : html`<span class="muted">${path.done.length}/${path.ids.length} · next:</span>`}
    ${steps.slice(0, path.goal ? 3 : PATH_PREVIEW).map((id, i) => html`<button key=${id} class="sm ${id === path.next ? "path-next" : ""}"
      title=${id === path.next ? "Your next step" : "Coming up"} onClick=${() => onFocus(id)}>${i + 1}. ${clip(name(id), 26)}</button>`)}
    ${left === 0 && !path.goal && html`<span>🎉 everything understood</span>`}
  </div>`;
}

function LearnGraph({ pid, learner, data, focus, onFocus, expanded, setExpanded, ctlRef, spot, setSpot, onGoal }) {
  const box = useRef();
  const ctl = useRef();
  const [frames, setFrames] = useState(true);
  const lens = useLens({ pid, version: data?.seq, author: learner ? `learner:${learner}` : "learner" });
  const areas = data?.areas || [];
  const allAreas = useMemo(() => new Set(areas.map((a) => a.id)), [data?.areas]);
  const eff = useMemo(() => expanded ?? allAreas, [expanded, allAreas]);
  const toggle = (id) => {
    const n = new Set(eff);
    if (n.has(id)) n.delete(id); else { n.add(id); ctl.current?.openArea(id); }
    setExpanded(n);
  };
  const handler = useRef(null);
  handler.current = (s) => {
    if (lens.onSelect(s)) return;
    if (s && s.kind === "group") toggle(s.id);
    else onFocus(s && s.kind === "concept" ? s.id : null);
  };
  const [pathOn, setPathOn] = useState(() => saved("hoc.pathGlow", "1") === "1");
  const steps = useMemo(() => pathSteps(data?.path), [data?.path]);
  const shown = useMemo(() => {
    let d = lens.display(data);
    if (!d || lens.editing) return d;
    // one thing glows at a time: what the tutor is showing, else a glowing view, else the path
    if (spot?.ids.length) return glowView(d, spot.ids, { order: spot.ids });
    if (lens.active && lens.glow) return d;
    if (pathOn && steps.length) {
      const lit = data.path.goal ? [...steps, ...data.path.done] : steps;
      d = glowView(d, lit, { flag: "onpath", next: data.path.next, fade: false, order: steps, edgeTypes: ["prerequisite_of"] });
    }
    return d;
  }, [data, lens.active, lens.editing, lens.context, lens.glow, spot, pathOn, steps]);
  const view = useMemo(() => (shown ? groupView(shown, lens.focused ? allAreas : eff, { groupNode, frames }) : null),
    [shown, eff, frames, lens.focused, allAreas]);
  const lensKey = lens.editing ? "edit" : lens.active?.id || "";
  const lastLens = useRef(lensKey);
  useEffect(() => {
    const c = new GraphController(box.current, { onSelect: (s) => handler.current(s) });
    c.colorFn = (n) => (n.group ? "#e2e8f0" : STATUS[n.status]?.fill || STATUS.unknown.fill);
    ctl.current = c;
    if (ctlRef) ctlRef.current = c;
    return () => c.destroy();
  }, []);
  useEffect(() => {
    if (!view || !ctl.current) return;
    const fit = lastLens.current !== lensKey;  // entering or leaving a view: frame it
    lastLens.current = lensKey;
    ctl.current.setData(
      { nodes: view.nodes.map((n) => (n.group || n.frame ? n : { ...n, label: nodeLabel(n) })), edges: view.edges, areas: view.areas },
      { animate: false, fit },
    );
    ctl.current.setHiddenTypes(["related"]);
  }, [view]);
  useEffect(() => {
    ctl.current?.focus(focus ? { id: focus } : null);
  }, [focus, view]);
  return html`<div class="canvas-wrap">
    <div class="canvas" ref=${box}></div>
    <div class="toolbar">
      <div class="card">
        <button class="sm" onClick=${() => ctl.current.layout(true, true)}>Re-layout</button>
        <button class="sm" onClick=${() => ctl.current.fit()}>Fit</button>
        <label class="inline-check" title="Draw each open area as a box; click its title to collapse it">
          <input type="checkbox" checked=${frames} onChange=${(e) => setFrames(e.target.checked)} /> area boxes</label>
        <${LensToggle} lens=${lens} />
        <span class="muted">${data ? `${data.total} concepts` : ""}</span>
      </div>
      <${LensBanner} lens=${lens} />
      ${spot?.ids.length > 0 && html`<div class="card lens-banner" style=${`border-color:${GLOW.pick}`}>
        <span><span style=${`color:${GLOW.pick}`}>✨</span> <b>${spot.label}</b></span>
        <span class="muted">${spot.ids.length} concepts lit up</span>
        <button class="sm" onClick=${() => setSpot(null)} title="Back to your path">✕</button></div>`}
      <${PathBar} data=${data} steps=${steps} on=${pathOn} onFocus=${onFocus} onGoal=${onGoal}
        setOn=${(v) => { setPathOn(v); save("hoc.pathGlow", v ? "1" : "0"); }} />
      ${!lens.focused && html`<${AreaBar} areas=${areas} expanded=${eff} onToggle=${toggle}
        onAll=${() => setExpanded(new Set(areas.map((a) => a.id)))} onNone=${() => setExpanded(new Set())} />`}
    </div>
    <${LensPanel} lens=${lens} selected=${focus} selectedName=${data?.nodes.find((n) => n.id === focus)?.name || ""} />
    <${Legend} counts=${data?.counts} />
  </div>`;
}

function Legend({ counts }) {
  return html`<div class="card legend">
    ${Object.entries(STATUS).map(([k, v]) => html`<span title=${v.label}>
      <span class="swatch" style=${`background:${v.fill}`}></span>${v.icon}${v.label}${counts ? ` (${counts[k] ?? 0})` : ""}</span>`)}
    <span class="muted">• visited · ⚑ “I get it” · ⚐ “lost” · green glow = ready ·
      <span style=${`color:${GLOW.path}`}>blue glow</span> = your path (① ② ③, next one pulses) ·
      <span style=${`color:${GLOW.pick}`}>purple glow</span> = shown by the tutor or a view · dashed = draft</span>
  </div>`;
}

/* ------------------------------------------------------------------- reader */
function BeliefStrip({ data }) {
  const [open, setOpen] = useState(null);
  const facets = data.state.facets;
  return html`<div>
    <h3>What the system believes <span class="muted" style="text-transform:none">(click a bar for the evidence)</span></h3>
    ${FACETS.map((f) => {
      const b = facets[f];
      const st = STATUS[b.status];
      const ev = data.evidence[f] || [];
      return html`<div key=${f}>
        <div class="bar-row" onClick=${() => setOpen(open === f ? null : f)} title="Click for the evidence">
          <span class="bar-label">${f}</span>
          <span class="bar"><i style=${`width:${b.status === "unknown" ? 4 : Math.round(b.mean * 100)}%;background:${st.fill}`}></i></span>
          <span class="small">${st.label}</span>
          <span class="muted small">mass ${b.evidence_mass.toFixed(1)}</span>
        </div>
        ${open === f && html`<div class="evidence">
          ${ev.length === 0 ? html`<div class="muted small">No evidence about this yet.</div>` : ev.map((c) => html`<div class="small ev-row">
            <b>${c.outcome}</b> <span class="muted">weight ${c.weight.toFixed(2)} · ${c.judge} · ${ago(c.timestamp)}${c.inferred ? " · inferred" : ""}</span>
            ${c.quote && html`<div>“${clip(c.quote, 160)}”</div>`}
            ${c.rationale && html`<div class="muted">${clip(c.rationale, 160)}</div>`}</div>`)}
        </div>`}
      </div>`;
    })}
    ${data.state.inferred_mean != null && html`<div class="small muted">Hint from prerequisites: about ${Math.round(data.state.inferred_mean * 100)}% (not counted as evidence).</div>`}
  </div>`;
}

function NeighbourList({ title, items, onFocus }) {
  if (!items.length) return null;
  return html`<div><h3>${title}</h3>
    <div class="row wrap">${items.map((n) => html`<button class="chip-btn ${STATUS[n.status].css}" key=${n.id + n.relation}
      title=${`${n.summary || ""} — ${STATUS[n.status].label}`} onClick=${() => onFocus(n.id)}>
      <span class="swatch" style=${`background:${STATUS[n.status].fill}`}></span>${n.name}
      ${n.necessity === "supportive" ? html`<span class="muted small"> (helps)</span>` : ""}
      ${n.relation !== "prerequisite_of" ? html`<span class="muted small"> · ${n.relation.replace(/_/g, " ")}</span>` : ""}</button>`)}</div></div>`;
}

function Reader({ pid, learner, focus, onFocus, version, onChanged, goal, onGoal }) {
  const L = project(pid);
  const [data, setData] = useState(null);
  const [err, setErr] = useState(null);
  const [collapsed, setCollapsed] = useState(false);
  useEffect(() => {
    if (!focus) { setData(null); setErr(null); return; }
    let live = true;
    L.get(`/learn/concepts/${encodeURIComponent(focus)}?learner_id=${learner}`)
      .then((d) => { if (live) { setData(d); setErr(null); } })
      .catch((e) => { if (live) setErr(e.message); });
    return () => { live = false; };
  }, [pid, learner, focus, version]);
  // handler factory: onClick=${flag("got_it")} must return a function, not run during render
  const flag = (value) => guard(async () => {
    await L.post("/learn/interactions", { learner_id: learner, kind: "flag", concept_id: focus,
      meta: { flag: data.state.self_flag === value ? null : value } });
    onChanged();
  });
  if (!focus) return html`<div class="empty">Click a node in the graph to read it.<br/>
    With nothing selected the tutor talks about the whole topic.</div>`;
  if (err) return html`<div class="empty">${err}</div>`;
  if (!data) return html`<div class="empty">Loading…</div>`;
  const c = data.concept, st = data.state;
  return html`<div class="reader">
    <div class="row" style="align-items:flex-start">
      <div class="grow"><h2>${c.name}</h2>
        <div class="row wrap" style="margin:4px 0">
          <${Badge} kind=${STATUS[st.status].css === "unknown" ? "" : STATUS[st.status].css}>${STATUS[st.status].label}<//>
          <${Badge}>${c.kind}<//>
          ${st.visited && html`<${Badge}>visited<//>`}
          ${c.status === "proposed" && html`<${Badge} kind="warn">draft<//>`}
          ${data.tasks.length > 0 && html`<${Badge}>${data.tasks.length} exercise${data.tasks.length > 1 ? "s" : ""}<//>`}
        </div></div>
      <button class="ghost sm" onClick=${() => setCollapsed(!collapsed)}>${collapsed ? "Show" : "Hide"}</button>
    </div>
    ${!collapsed && html`<div>
      <p style="font-weight:500"><${Tex} text=${c.card.one_line} /></p>
      ${c.card.definition && html`<p><${Tex} text=${c.card.definition} /></p>`}
      ${c.card.formal && html`<${Formal} text=${c.card.formal} />`}
      ${c.card.key_ideas.length > 0 && html`<h3>Key ideas</h3><ul class="ideas">${c.card.key_ideas.map((k) => html`<li><${Tex} text=${k.text} /></li>`)}</ul>`}
      ${c.card.examples.length > 0 && html`<h3>Examples</h3><ul class="ideas">${c.card.examples.map((x) => html`<li><${Tex} text=${x} /></li>`)}</ul>`}
      <div class="row wrap" style="margin:10px 0">
        <button class=${st.self_flag === "got_it" ? "good" : ""} onClick=${flag("got_it")}>⚑ I get it</button>
        <button class=${st.self_flag === "lost" ? "bad" : ""} onClick=${flag("lost")}>⚐ I'm lost</button>
        <button class=${goal === c.id ? "on-area" : ""} onClick=${() => onGoal(goal === c.id ? null : c.id)}
          title="Your path then leads here: only this concept and what it needs, step by step">
          🎯 ${goal === c.id ? "This is my goal" : "Make this my goal"}</button>
        <span class="muted small">Your own opinion counts as weak evidence and is shown separately from what the system believes.</span>
      </div>
      <${NeighbourList} title="Learn these first" items=${data.prerequisites} onFocus=${onFocus} />
      <${NeighbourList} title="This leads to" items=${data.leads_to} onFocus=${onFocus} />
      <${NeighbourList} title="Related" items=${data.bridges} onFocus=${onFocus} />
      <${BeliefStrip} data=${data} />
    </div>`}
  </div>`;
}

/* -------------------------------------------------------------- next moves */
function MoveChips({ pid, learner, offered, onFocus, onOpenTask, onDismiss }) {
  if (!offered || offered.moves.length === 0) return null;
  const L = project(pid);
  const icon = { review_prerequisite: "↩", use_analogy: "≈", advance: "→", practice: "✎", check: "?", overview: "◎" };
  const verb = { review_prerequisite: "Review", use_analogy: "Compare with", advance: "Next:", practice: "Try exercise on", check: "Check", overview: "Overview" };
  const accept = (m, i) => guard(async () => {
    await L.post("/learn/interactions", { learner_id: learner, kind: "chip_accept", concept_id: m.concept_id,
      meta: { decision_id: offered.decision_id, index: i, move: m.kind } });
    if (m.kind === "practice" && m.task_id) onOpenTask(m.task_id, m.concept_id);
    else if (m.concept_id) onFocus(m.concept_id);
  });
  return html`<div class="chips">
    ${offered.moves.map((m, i) => html`<button class="chip-btn move" key=${i} title=${`${m.reason} (score ${m.score.toFixed(2)})`}
      onClick=${accept(m, i)}>${icon[m.kind] || "•"} ${verb[m.kind] || m.kind} ${m.concept_name}
      <span class="muted small"> — ${m.reason}</span></button>`)}
    <button class="ghost sm" onClick=${() => onDismiss(offered)} title="Not now">✕</button>
  </div>`;
}

/* ------------------------------------------------------------------ my map */
function MyMap({ pid, learner, onFocus, version }) {
  const [data, setData] = useState(null);
  const [model, setModel] = useState(null);
  const [filter, setFilter] = useState("all");
  const [sort, setSort] = useState({ key: "area", dir: 1 });
  const [cmp, setCmp] = useState("");
  const [diff, setDiff] = useState(null);
  const [logs, setLogs] = useState(null);
  useEffect(() => {
    project(pid).get(`/learn/map?learner_id=${learner}${model ? `&model=${model}` : ""}`).then(setData);
  }, [pid, learner, model, version]);
  useEffect(() => {
    if (!cmp || !data) { setDiff(null); return; }
    project(pid).get(`/learn/compare?learner_id=${learner}&a=${data.model}&b=${cmp}`).then(setDiff);
  }, [cmp, data?.model, version]);
  const loadLogs = () => Promise.all([
    project(pid).get(`/learn/observations?learner_id=${learner}`),
    project(pid).get(`/learn/decisions?learner_id=${learner}`),
  ]).then(([observations, decisions]) => setLogs({ observations, decisions }));
  if (!data) return html`<div class="empty">Loading…</div>`;
  const order = { likely_not: 0, uncertain: 1, unknown: 2, likely: 3, mastered: 4 };
  let rows = data.rows.filter((r) => {
    if (filter === "struggling") return r.status === "likely_not" || r.status === "uncertain";
    if (filter === "ready") return r.ready;
    if (filter === "unvisited") return !r.visited;
    if (filter === "understood") return understood(r.status);
    return true;
  });
  const val = (r) => sort.key === "status" ? order[r.status] : sort.key === "obs" ? r.n_obs : (r[sort.key] || "");
  rows = [...rows].sort((a, b) => (val(a) > val(b) ? 1 : val(a) < val(b) ? -1 : 0) * sort.dir);
  const th = (key, label) => html`<th onClick=${() => setSort({ key, dir: sort.key === key ? -sort.dir : 1 })}
    style="cursor:pointer">${label}${sort.key === key ? (sort.dir > 0 ? " ▲" : " ▼") : ""}</th>`;
  return html`<div>
    <div class="row wrap" style="margin-bottom:8px">
      <div class="seg">${[["all", "All"], ["struggling", "Struggling"], ["ready", "Ready next"], ["unvisited", "Never visited"], ["understood", "Understood"]].map(([k, l]) =>
        html`<button class=${filter === k ? "on" : ""} onClick=${() => setFilter(k)}>${l}</button>`)}</div>
      <div class="grow"></div>
      <label style="margin:0">student model</label>
      <select style="width:auto" value=${data.model} onChange=${(e) => setModel(e.target.value)}>
        ${data.models.map((m) => html`<option value=${m}>${m}</option>`)}</select>
      <label style="margin:0">compare with</label>
      <select style="width:auto" value=${cmp} onChange=${(e) => setCmp(e.target.value)}>
        <option value="">—</option>${data.models.filter((m) => m !== data.model).map((m) => html`<option value=${m}>${m}</option>`)}</select>
    </div>
    ${diff && html`<p class="small"><b>${diff.differences}</b> of ${diff.rows.length} concepts get a different status under <code>${diff.b}</code> (shown in the last column).</p>`}
    <p class="muted small">Same evidence, different model: switching re-derives every belief from the log (nothing is stored per model).</p>
    <table class="map small">
      <tr>${th("name", "Concept")}${th("area", "Area")}${th("status", "Belief")}<th>Facets</th>${th("obs", "Evidence")}<th></th>${diff && html`<th>${diff.b}</th>`}</tr>
      ${rows.map((r) => html`<tr key=${r.id} onClick=${() => onFocus(r.id)}>
        <td>${r.name}</td><td class="muted">${r.area || ""}</td>
        <td><span class="swatch" style=${`background:${STATUS[r.status].fill}`}></span>${STATUS[r.status].label}</td>
        <td>${FACETS.map((f) => html`<span class="facet-dot" title=${`${f}: ${STATUS[r.facets[f].status].label}`}
          style=${`background:${STATUS[r.facets[f].status].fill}`}></span>`)}</td>
        <td>${r.n_obs}</td>
        <td>${r.visited ? "•" : ""}${r.flag === "got_it" ? "⚑" : r.flag === "lost" ? "⚐" : ""}${r.ready ? "★" : ""}</td>
        ${diff && (() => { const d = diff.rows.find((x) => x.id === r.id); return html`<td style=${d?.differs ? "font-weight:600" : "opacity:.5"}>
          <span class="swatch" style=${`background:${STATUS[d?.b || "unknown"].fill}`}></span>${STATUS[d?.b || "unknown"].label}${d?.differs ? " ≠" : ""}</td>`; })()}</tr>`)}
    </table>
    <h3>Research data</h3>
    <div class="row wrap"><a href=${`/api/projects/${encodeURIComponent(pid)}/learn/export?learner_id=${learner}`} download>⬇ Download everything (JSON lines)</a>
      <button class="sm" onClick=${loadLogs}>${logs ? "Refresh" : "Show"} evidence and decision logs</button></div>
    ${logs && html`<details open><summary class="small">Evidence log (${logs.observations.length})</summary>
      <table class="map small"><tr><th>when</th><th>target</th><th>outcome</th><th>w</th><th>judge</th></tr>
      ${logs.observations.slice(-60).reverse().map((o) => html`<tr key=${o.id}><td>${ago(o.timestamp)}</td>
        <td>${clip(o.target.id, 28)}/${o.target.facet}</td><td>${o.outcome}</td><td>${o.weight}</td><td>${o.judge.kind}</td></tr>`)}</table></details>
      <details><summary class="small">Policy decisions (${logs.decisions.length})</summary>
      <table class="map small"><tr><th>when</th><th>focus</th><th>top candidates</th><th>chosen</th></tr>
      ${logs.decisions.slice(-40).reverse().map((d) => html`<tr key=${d.id}><td>${ago(d.timestamp)}</td><td>${clip(d.focus_concept_id || "—", 22)}</td>
        <td>${d.candidates.slice(0, 2).map((c) => `${c.kind} ${clip(c.concept_name || "", 18)}`).join(", ")}</td>
        <td>${d.accepted === true ? "✓ accepted" : d.accepted === false ? "dismissed" : d.chosen_by}</td></tr>`)}</table></details>`}
    <h3>The tutor's notes about you</h3>
    ${data.notes.length === 0 ? html`<div class="muted small">No notes yet.</div>` : data.notes.slice().reverse().map((n) =>
      html`<div class="small" style="margin-bottom:4px">• ${n.text} <span class="muted">${ago(n.at)}</span></div>`)}
  </div>`;
}

/* ------------------------------------------------------------------- learn */
/* Drag handle for resizing panes; sizes are remembered per browser. */
const stored = (k, d) => { try { const v = Number(localStorage.getItem(k)); return v > 0 ? v : d; } catch { return d; } };
const remember = (k, v) => { try { localStorage.setItem(k, String(Math.round(v))); } catch { /* private mode */ } };

function Splitter({ axis, onDrag, onDone, onReset }) {
  const down = (e) => {
    e.preventDefault();
    const move = (ev) => onDrag(ev.clientX, ev.clientY);
    const up = () => { window.removeEventListener("pointermove", move); window.removeEventListener("pointerup", up); onDone?.(); };
    window.addEventListener("pointermove", move);
    window.addEventListener("pointerup", up);
  };
  return html`<div class="splitter ${axis}" onPointerDown=${down} onDblClick=${onReset}
    title=${axis === "v" ? "Drag to widen the panel (double-click to reset)" : "Drag to resize the lecture (double-click to enlarge)"}></div>`;
}

export function Learn({ pid, onHome }) {
  const L = useMemo(() => project(pid), [pid]);
  const [title, setTitle] = useState("");
  const [learners, setLearners] = useState([]);
  const [learner, setLearner] = useState(saved(`hoc.learner.${pid}`, "me"));
  const [graph, setGraph] = useState(null);
  const [focus, setFocus] = useState(null);
  const [tab, setTab] = useState("learn");
  const [sideW, setSideW] = useState(() => stored("hoc.sideW", 400));
  const [readerH, setReaderH] = useState(() => stored("hoc.readerH", 0)); // 0 = automatic (36%)
  const splitRef = useRef();
  const [version, setVersion] = useState(0);
  const [offered, setOffered] = useState(null);
  const [session, setSession] = useState(null);
  const [openTask, setOpenTask] = useState(null);
  const [cost, setCost] = useState({ calls: 0, usd: 0 });
  const [expanded, setExpanded] = useState(null); // Set of expanded area ids; null until first load
  const [spot, setSpot] = useState(null); // { ids, label }: concepts lit up for now (e.g. by the tutor)
  const graphRef = useRef(null);
  graphRef.current = graph;
  const ctl = useRef();
  const focusRef = useRef(focus);
  focusRef.current = focus;

  const loadGraph = useCallback(() => L.get(`/learn/graph?learner_id=${learner}`).then(setGraph)
    .catch((e) => toast(e.message)), [L, learner]);
  const changed = useCallback(() => { setVersion((v) => v + 1); loadGraph(); }, [loadGraph]);
  useEffect(() => {  // big graphs open as super-nodes, small ones fully expanded
    if (expanded === null && graph) {
      setExpanded(graph.total > graph.collapse_threshold ? new Set() : new Set(graph.areas.map((a) => a.id)));
    }
  }, [graph]);

  useEffect(() => {
    L.get().then((s) => setTitle(s.brief.title)).catch(() => {});
    L.get("/learn/learners").then(setLearners);
  }, [pid]);
  useEffect(() => {
    save(`hoc.learner.${pid}`, learner);
    setFocus(null); setOffered(null); setOpenTask(null); setSpot(null);
    loadGraph();
    L.post("/learn/sessions", { learner_id: learner }).then(setSession).catch((e) => toast(e.message));
  }, [learner]);

  const doFocus = useCallback((id) => {
    setFocus(id);
    setOpenTask(null);
    if (id) {
      const area = graphRef.current?.nodes.find((n) => n.id === id)?.area;  // open its area
      if (area) setExpanded((e) => (e && !e.has(area) ? new Set([...e, area]) : e));
      L.post("/learn/interactions", { learner_id: learner, kind: "visit", concept_id: id })
        .then(() => loadGraph());
      setTab((t) => (t === "map" || t === "tutor" ? "learn" : t));
    }
  }, [L, learner, loadGraph]);

  const setGoal = guard(async (id) => {
    await L.post("/learn/interactions", { learner_id: learner, kind: "goal", concept_id: id });
    await loadGraph();
    toast(id ? "Goal set: your path now leads there" : "Goal cleared");
  });
  const showSpot = useCallback((ids, label) => {  // open the areas involved so the glow is visible
    const areas = new Set(graphRef.current?.nodes.filter((n) => ids.includes(n.id) && n.area).map((n) => n.area));
    setExpanded((e) => (e ? new Set([...e, ...areas]) : e));
    setSpot({ ids, label });
  }, []);
  const whatNext = guard(async () => {
    setOffered(await L.post("/learn/moves", { learner_id: learner, focus, session_id: session?.session.id }));
    setTab("learn");
  });
  const dismiss = guard(async (o) => {
    await L.post("/learn/interactions", { learner_id: learner, kind: "chip_dismiss",
      meta: { decision_id: o.decision_id } });
    setOffered(null);
  });
  const newLearner = guard(async () => {
    const name = prompt("Name for the new learner profile (keeps its own beliefs and history):");
    if (!name) return;
    const l = await L.post("/learn/learners", { name });
    setLearners(await L.get("/learn/learners"));
    setLearner(l.id);
  });
  const understoodCount = graph ? graph.counts.likely + graph.counts.mastered : 0;
  const tabs = [["learn", "Learn"], ["exercises", "Exercises"], ["map", "My map"], ["tutor", "Tutor's view"]];

  return html`<div class="shell">
    <div class="topbar">
      <button class="ghost" onClick=${onHome} title="All topics">←</button>
      <div class="title" title=${title}>${title}</div>
      <${SideSwitch} side="learn" pid=${pid} />
      <div class="row small"><span class="muted">learner</span>
        <select style="width:auto" value=${learner} onChange=${(e) => setLearner(e.target.value)}>
          ${learners.map((l) => html`<option value=${l.id}>${l.display_name}</option>`)}</select>
        <button class="sm" onClick=${newLearner} title="New learner profile">＋</button></div>
      <div class="grow"></div>
      ${graph && html`<div class="status" title="Concepts the system believes you understand">
        <span class="txt">${understoodCount}/${graph.total} understood</span></div>`}
      <div class="status" title="Real (non-cached) LLM calls this session and estimated cost">
        <span class="txt">🧠 ${cost.calls} calls${cost.usd ? ` · $${cost.usd.toFixed(3)}` : ""}</span></div>
      <button class="primary" onClick=${whatNext}>What next?</button>
    </div>
    <div class="main" style=${`--side-w:${sideW}px`}>
      <div class="stage"><${LearnGraph} pid=${pid} learner=${learner} data=${graph} focus=${focus} onFocus=${doFocus} expanded=${expanded} setExpanded=${setExpanded} ctlRef=${ctl}
        spot=${spot} setSpot=${setSpot} onGoal=${setGoal} /></div>
      <div class="side">
        <${Splitter} axis="v" onDrag=${(x) => { const w = Math.max(320, Math.min(window.innerWidth * 0.75, window.innerWidth - x)); setSideW(w); remember("hoc.sideW", w); }}
          onReset=${() => { setSideW(400); remember("hoc.sideW", 400); }} />
        <div class="tabs">${tabs.map(([k, l]) => html`<button class=${tab === k ? "on" : ""} onClick=${() => setTab(k)}>${l}</button>`)}</div>
        <div class="pane learn-pane">
          ${tab === "learn" && html`<div class="learn-split" ref=${splitRef}>
            <div class="reader-wrap" style=${readerH ? `max-height:none;height:${readerH}px` : ""}><${Reader} pid=${pid} learner=${learner} focus=${focus} onFocus=${doFocus}
              version=${version} onChanged=${changed} goal=${graph?.path?.goal} onGoal=${setGoal} /></div>
            <${Splitter} axis="h" onDrag=${(_x, y) => { const r = splitRef.current.getBoundingClientRect();
                const h = Math.max(80, Math.min(r.height - 180, y - r.top)); setReaderH(h); remember("hoc.readerH", h); }}
              onReset=${() => { const r = splitRef.current.getBoundingClientRect(); const h = readerH > r.height * 0.6 ? 0 : r.height * 0.75;
                setReaderH(h); remember("hoc.readerH", h); }} />
            <${MoveChips} pid=${pid} learner=${learner} offered=${offered} onFocus=${doFocus}
              onOpenTask=${(tid, cid) => { setOpenTask(tid); setTab("exercises"); if (cid) setFocus(cid); }} onDismiss=${dismiss} />
            <${Chat} pid=${pid} learner=${learner} session=${session} focus=${focus} graph=${graph}
              onFocus=${doFocus} onChanged=${changed} onCost=${setCost} onOffer=${setOffered} onHighlight=${showSpot}
              onOpenTask=${(tid, cid) => { setOpenTask(tid); setTab("exercises"); if (cid) setFocus(cid); }} />
          </div>`}
          ${tab === "exercises" && html`<${Exercises} pid=${pid} learner=${learner} focus=${focus} graph=${graph} session=${session}
            openTask=${openTask} onChanged=${changed} onFocus=${doFocus} onCost=${setCost} />`}
          ${tab === "map" && html`<${MyMap} pid=${pid} learner=${learner} onFocus=${doFocus} version=${version} />`}
          ${tab === "tutor" && html`<${TutorView} pid=${pid} learner=${learner} session=${session} />`}
        </div>
      </div>
    </div>
  </div>`;
}
