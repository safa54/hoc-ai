import {
  html, useState, useEffect, useRef, useMemo, useCallback,
} from "/static/vendor/standalone.module.js";
import { api, project } from "./api.js";
import { GraphController, PALETTE, EDGE_TYPES, groupView } from "./graph.js";
import { Tex, Formal } from "./math.js";
import { useLens, LensPanel, LensBanner, LensToggle } from "./views.js";
import {
  toast, clip, ago, who, guard, ROLE_ICON, MODES, lines,
  Badge, Field, Modal, ChangeChips, SideSwitch, AreaBar,
} from "./ui.js";

/* ---------------------------------------------------------------- activity */
function eventView(e, onPick) {
  const d = e.data;
  switch (e.type) {
    case "agent.started":
      return [ROLE_ICON[d.role] || "🤖", html`<b>${d.agent}</b> ${d.label}`];
    case "agent.finished":
      return ["✓", html`<span>${d.summary}</span>`];
    case "agent.error":
      return ["⚠️", html`<span style="color:var(--bad)"><b>${d.agent}</b> failed: ${d.error}</span>`];
    case "changeset.proposed":
      return ["📝", html`<div><b>${who(d.author)}</b> proposed
        <${Badge} kind=${d.route === "apply" ? "ok" : "warn"}>${d.route === "apply" ? "auto-applying" : "needs review"}<//>
        <div class="small">${clip(d.rationale, 200)}</div>
        <${ChangeChips} diff=${d.diff} onPick=${onPick} />
        ${d.problems?.length ? html`<div class="small muted">dropped: ${d.problems.length} unusable op(s)</div>` : null}</div>`];
    case "changeset.applied":
      return ["✅", html`<div>Applied #${d.seq}${d.decided_by?.kind === "human" ? " by you" : ""}
        <div class="small">${clip(d.rationale, 160)}</div>
        <${ChangeChips} diff=${d.diff} onPick=${onPick} /></div>`];
    case "changeset.rejected":
      return ["🚫", html`<span>Proposal rejected${d.reason ? `: ${d.reason}` : ""}</span>`];
    case "changeset.reverted":
      return ["↩️", html`<span>Reverted: ${clip(d.rationale, 100)}</span>`];
    case "proposal.dropped":
      return ["🗑️", html`<span class="muted">Dropped an unusable proposal from ${d.author}</span>`];
    case "feedback.created":
      return ["💬", html`<span><b>${who(d.feedback.author)}</b> ${d.feedback.kind}: ${clip(d.feedback.text, 140)}</span>`];
    case "question.asked":
      return ["❓", html`<span><b>${d.feedback.author.name}</b> asks: ${clip(d.feedback.text, 160)}</span>`];
    case "feedback.updated":
      return ["•", html`<span class="muted">Note ${d.feedback.status}: ${clip(d.feedback.text, 70)}</span>`];
    case "blueprint.updated":
      return ["🗺️", html`<span>Plan updated: ${d.areas.length} areas${d.new?.length ? ` (${d.new.length} new)` : ""}</span>`];
    case "test.created":
      return ["🧪", html`<span>Test: ${clip(d.test.question, 120)}</span>`];
    case "test.traced":
      return ["🧭", html`<span>Traced a test — <b>${d.trace.verdict.replace("_", " ")}</b>${d.trace.missing_edges.length ? `, ${d.trace.missing_edges.length} missing edge(s)` : ""}</span>`];
    case "run.status":
      return ["⏯", html`<span class="muted">${d.status}${d.detail ? ` — ${d.detail}` : ""}</span>`];
    case "round.closed":
      return ["🏁", html`<span>Review round ${d.round} finished${d.completed ? " — stable for now" : ""}</span>`];
    case "source.added":
      return ["📄", html`<span>${d.material ? "Exercise sheet" : "Source"} added: ${d.title} (${d.chunks} parts)</span>`];
    case "source.removed":
      return ["📄", html`<span class="muted">Source removed</span>`];
    case "task.saved":
      return ["📝", html`<span>Exercise <b>${d.status}</b>: ${clip(d.title, 70)}</span>`];
    case "brief.updated":
      return ["📋", html`<span>Brief updated</span>`];
    case "settings.updated":
      return ["⚙️", html`<span>Model settings updated</span>`];
    default:
      return ["•", html`<span class="muted">${e.type}</span>`];
  }
}

function Activity({ events, onPick, onlyKey }) {
  const [filter, setFilter] = useState("all");
  const list = events.filter((e) => {
    if (e.type === "run.status" && filter !== "all") return false;
    if (filter === "graph") return e.type.startsWith("changeset") || e.type === "proposal.dropped";
    if (filter === "agents") return e.type.startsWith("agent") || e.type === "run.status" || e.type === "round.closed";
    return true;
  });
  return html`<div>
    <div class="seg" style="margin-bottom:6px">
      ${[["all", "All"], ["graph", "Graph changes"], ["agents", "Agents"]].map(([k, l]) =>
        html`<button class=${filter === k ? "on" : ""} onClick=${() => setFilter(k)}>${l}</button>`)}
    </div>
    ${list.length === 0 ? html`<div class="empty">Nothing yet. Press <b>Start</b> to let the agents work.</div>` : null}
    ${[...list].reverse().map((e) => {
      const [ic, body] = eventView(e, onPick);
      return html`<div class="ev" key=${e.seq}><div class="ic">${ic}</div>
        <div>${body}<div class="t">${ago(e.created_at)}</div></div></div>`;
    })}
  </div>`;
}

/* -------------------------------------------------------------------- plan */
function Plan({ snap, graphData, colorIndex }) {
  const bp = snap.blueprint, prog = snap.progress;
  if (!bp) return html`<div class="empty">No plan yet. The planner writes one when the run starts.</div>`;
  const counts = {};
  (graphData?.nodes || []).forEach((n) => { counts[n.area || ""] = (counts[n.area || ""] || 0) + 1; });
  return html`<div>
    <p>${bp.summary}</p>
    <h3>Areas</h3>
    <div class="areas">
      ${bp.areas.map((a, i) => html`<div class="area" key=${a.id}>
        <span class="swatch" style=${`background:${PALETTE[i % PALETTE.length]}`}></span>
        <div class="grow"><b>${a.title}</b><div class="small muted">${clip(a.description, 130)}</div></div>
        <span class="badge">${counts[a.id] || 0} nodes</span>
        ${prog.built_areas.includes(a.id) ? html`<${Badge} kind="ok">built<//>` : html`<${Badge}>queued<//>`}
      </div>`)}
    </div>
    <h3>Review rounds</h3>
    <div class="small">Round ${prog.round}${prog.completed ? " — complete" : " in progress"} ·
      ${prog.critic_done ? "critic done" : "critic pending"} · ${prog.traced.length} test(s) traced</div>
    <h3>Grain guide</h3>
    <div class="small" style="white-space:pre-wrap">${bp.grain_guide}</div>
  </div>`;
}

/* ----------------------------------------------------------- feedback form */
function FeedbackForm({ pid, target, onDone, compact }) {
  const [text, setText] = useState("");
  const [kind, setKind] = useState("comment");
  const [severity, setSeverity] = useState("medium");
  const submit = guard(async () => {
    await project(pid).post("/feedback", {
      text, kind, severity,
      target_kind: target?.kind || "graph", target_id: target?.id || null,
    });
    setText(""); onDone && onDone(); toast("Feedback sent to the agents");
  });
  return html`<div>
    <textarea placeholder=${target ? "Tell the agents what is wrong or what to change…" : "Give the agents a standing instruction (scope, level, what to add or avoid)…"}
      value=${text} onInput=${(e) => setText(e.target.value)} style=${compact ? "min-height:56px" : ""}></textarea>
    <div class="row wrap" style="margin-top:6px">
      <select style="width:auto" value=${kind} onChange=${(e) => setKind(e.target.value)}>
        <option value="comment">comment</option><option value="issue">issue</option>
        <option value="suggestion">suggestion</option><option value="directive">directive (instruction)</option>
      </select>
      <select style="width:auto" value=${severity} onChange=${(e) => setSeverity(e.target.value)}>
        <option value="low">low</option><option value="medium">medium</option><option value="high">high</option>
      </select>
      <div class="grow"></div>
      <button class="primary" disabled=${!text.trim()} onClick=${submit}>Send</button>
    </div></div>`;
}

/* ----------------------------------------------------------------- inspect */
function EditConceptModal({ pid, data, onClose }) {
  const c = data.concept;
  const [name, setName] = useState(c.name);
  const [kind, setKind] = useState(c.kind);
  const [summary, setSummary] = useState(c.card.one_line);
  const [definition, setDefinition] = useState(c.card.definition);
  const [ideas, setIdeas] = useState(c.card.key_ideas.map((k) => k.text).join("\n"));
  const [examples, setExamples] = useState(c.card.examples.join("\n"));
  const [lock, setLock] = useState(true);
  const save = guard(async () => {
    await project(pid).post("/edit", {
      ops: [{ op: "update_concept", concept_id: c.id, rationale: "Edited by the expert",
        concepts: [{ id: c.id, name, kind, summary, definition, key_ideas: lines(ideas), examples: lines(examples) }] }],
      rationale: `Edited “${name}” by hand`, lock,
    });
    toast("Saved"); onClose();
  });
  return html`<${Modal} title=${`Edit ${c.name}`} onClose=${onClose}>
    <${Field} label="Name"><input value=${name} onInput=${(e) => setName(e.target.value)} /><//>
    <${Field} label="Kind"><select value=${kind} onChange=${(e) => setKind(e.target.value)}>
      <option value="concept">concept — what something is</option>
      <option value="principle">principle — a rule or relationship that holds</option>
      <option value="procedure">procedure — a method or skill</option></select><//>
    <${Field} label="One-line summary"><input value=${summary} onInput=${(e) => setSummary(e.target.value)} /><//>
    <${Field} label="Definition"><textarea value=${definition} onInput=${(e) => setDefinition(e.target.value)}></textarea><//>
    <${Field} label="Key ideas (one per line)"><textarea value=${ideas} onInput=${(e) => setIdeas(e.target.value)}></textarea><//>
    <${Field} label="Examples (one per line)"><textarea value=${examples} onInput=${(e) => setExamples(e.target.value)}></textarea><//>
    <label class="row" style="margin-top:10px"><input type="checkbox" style="width:auto" checked=${lock}
      onChange=${(e) => setLock(e.target.checked)} /> Lock after saving (agents then only propose changes to it)</label>
    <div class="row" style="margin-top:12px"><div class="grow"></div>
      <button onClick=${onClose}>Cancel</button><button class="primary" onClick=${save}>Save</button></div>
  <//>`;
}

function AddRelationModal({ pid, nodes, from, onClose }) {
  const [type, setType] = useState("prerequisite_of");
  const [other, setOther] = useState("");
  const [dir, setDir] = useState("in");
  const [necessity, setNecessity] = useState("essential");
  const [why, setWhy] = useState("");
  const options = nodes.filter((n) => n.id !== from.id).sort((a, b) => a.name.localeCompare(b.name));
  const save = guard(async () => {
    const [source, target] = dir === "in" ? [other, from.id] : [from.id, other];
    await project(pid).post("/edit", {
      ops: [{ op: "add_relation", relation_type: type, source, target,
        necessity: type === "prerequisite_of" ? necessity : "", rationale: why }],
      rationale: `Added relation ${source} → ${target} by hand`, lock: true,
    });
    toast("Relation added"); onClose();
  });
  const dirLabels = type === "prerequisite_of"
    ? ["is a prerequisite of", "needs (has as prerequisite)"] : ["→", "←"];
  return html`<${Modal} title=${`Add relation for ${from.name}`} onClose=${onClose}>
    <${Field} label="Type"><select value=${type} onChange=${(e) => setType(e.target.value)}>
      ${Object.entries(EDGE_TYPES).map(([k, v]) => html`<option value=${k}>${v.label}</option>`)}</select><//>
    <${Field} label="Direction"><select value=${dir} onChange=${(e) => setDir(e.target.value)}>
      <option value="in">other ${dirLabels[0]} ${from.name}</option>
      <option value="out">${from.name} ${dirLabels[0]} other</option></select><//>
    <${Field} label="Other concept"><select value=${other} onChange=${(e) => setOther(e.target.value)}>
      <option value="">— choose —</option>${options.map((n) => html`<option value=${n.id}>${n.name}</option>`)}</select><//>
    ${type === "prerequisite_of" && html`<${Field} label="Necessity"><select value=${necessity}
      onChange=${(e) => setNecessity(e.target.value)}>
      <option value="essential">essential — cannot be understood without it</option>
      <option value="supportive">supportive — helps, other routes exist</option></select><//>`}
    <${Field} label="Why (optional)"><input value=${why} onInput=${(e) => setWhy(e.target.value)} /><//>
    <div class="row" style="margin-top:12px"><div class="grow"></div>
      <button onClick=${onClose}>Cancel</button>
      <button class="primary" disabled=${!other} onClick=${save}>Add</button></div>
  <//>`;
}

function AddConceptModal({ pid, onClose, onCreated }) {
  const [name, setName] = useState("");
  const [kind, setKind] = useState("concept");
  const [summary, setSummary] = useState("");
  const [definition, setDefinition] = useState("");
  const save = guard(async () => {
    const r = await project(pid).post("/edit", {
      ops: [{ op: "add_concept", rationale: "Added by the expert",
        concepts: [{ name, kind, summary, definition, key_ideas: [], examples: [] }] }],
      rationale: `Added “${name}” by hand`, lock: true,
    });
    toast("Concept added — ask the agents to flesh it out if needed"); onClose(); onCreated && onCreated(r);
  });
  return html`<${Modal} title="Add a concept" onClose=${onClose}>
    <${Field} label="Name"><input value=${name} onInput=${(e) => setName(e.target.value)} /><//>
    <${Field} label="Kind"><select value=${kind} onChange=${(e) => setKind(e.target.value)}>
      <option value="concept">concept</option><option value="principle">principle</option>
      <option value="procedure">procedure</option></select><//>
    <${Field} label="One-line summary"><input value=${summary} onInput=${(e) => setSummary(e.target.value)} /><//>
    <${Field} label="Definition (optional)"><textarea value=${definition} onInput=${(e) => setDefinition(e.target.value)}></textarea><//>
    <div class="row" style="margin-top:12px"><div class="grow"></div>
      <button onClick=${onClose}>Cancel</button>
      <button class="primary" disabled=${!name.trim()} onClick=${save}>Add</button></div>
  <//>`;
}

function Inspector({ pid, selection, nodes, areas, version, onPick, tick }) {
  const [data, setData] = useState(null);
  const [modal, setModal] = useState(null);
  const [err, setErr] = useState(null);
  const sel = selection;
  useEffect(() => {
    if (!sel) { setData(null); return; }
    let live = true;
    const path = sel.kind === "concept" ? `/concepts/${sel.id}` : `/relations/${encodeURIComponent(sel.id)}`;
    project(pid).get(path).then((d) => { if (live) { setData(d); setErr(null); } })
      .catch((e) => { if (live) { setData(null); setErr(e.message); } });
    return () => { live = false; };
  }, [pid, sel?.kind, sel?.id, version, tick]);

  if (!sel) return html`<div class="empty">Click any node or edge in the graph to inspect it.
    <div style="margin-top:10px"><button onClick=${() => setModal("add")}>＋ Add a concept</button></div>
    ${modal === "add" && html`<${AddConceptModal} pid=${pid} onClose=${() => setModal(null)} onCreated=${(r) => r} />`}</div>`;
  if (err) return html`<div class="empty">${err}<br/>This element no longer exists in the current graph.</div>`;
  if (!data) return html`<div class="empty">Loading…</div>`;

  if (sel.kind === "relation") {
    const r = data.relation;
    const remove = guard(async () => {
      await project(pid).post("/edit", {
        ops: [{ op: "remove_relation", relation_type: r.type, source: r.source, target: r.target }],
        rationale: `Removed relation ${r.id} by hand`, lock: true });
      toast("Relation removed"); onPick(null);
    });
    const setNec = guard(async (n) => {
      await project(pid).post("/edit", {
        ops: [{ op: "update_relation", relation_type: r.type, source: r.source, target: r.target, necessity: n }],
        rationale: `Set necessity of ${r.id} to ${n}`, lock: true });
    });
    return html`<div>
      <h2>${EDGE_TYPES[r.type]?.label || r.type}</h2>
      <p><a href="#" onClick=${(e) => { e.preventDefault(); onPick({ kind: "concept", id: r.source }); }}>${r.source}</a> →
        <a href="#" onClick=${(e) => { e.preventDefault(); onPick({ kind: "concept", id: r.target }); }}>${r.target}</a></p>
      <div class="row wrap">
        <${Badge} kind=${r.status === "accepted" ? "ok" : "warn"}>${r.status}<//>
        ${r.necessity && html`<${Badge}>${r.necessity}<//>`}${r.locked && html`<${Badge}>🔒 locked<//>`}
      </div>
      ${r.rationale && html`<h3>Why</h3><p>${r.rationale}</p>`}
      <h3>Edit</h3>
      <div class="row wrap">
        ${r.type === "prerequisite_of" && html`<button onClick=${() => setNec(r.necessity === "essential" ? "supportive" : "essential")}>
          Make ${r.necessity === "essential" ? "supportive" : "essential"}</button>`}
        <button class="bad" onClick=${remove}>Remove relation</button>
      </div>
      <h3>History</h3>${data.history.map((h) => html`<div class="small">#${h.seq} ${who(h.author)}: ${clip(h.rationale, 120)}</div>`)}
      <h3>Feedback on this relation</h3>
      ${data.feedback.map((f) => html`<div class="fb ${f.severity} small">${f.text}</div>`)}
      <${FeedbackForm} pid=${pid} target=${{ kind: "relation", id: r.id }} compact />
    </div>`;
  }

  const c = data.concept;
  const remove = guard(async () => {
    if (!confirm(`Remove “${c.name}” and its relations from the graph?`)) return;
    await project(pid).post("/edit", {
      ops: [{ op: "remove_concept", concept_id: c.id }], rationale: `Removed “${c.name}” by hand`, lock: true });
    toast("Concept removed"); onPick(null);
  });
  const moveArea = guard(async (area) => {
    if (!area || area === c.topic_ids[0]) return;
    await project(pid).post("/edit", { ops: [{ op: "update_concept", concept_id: c.id, area }],
      rationale: `Moved “${c.name}” to area ${area}`, lock: false });
    toast("Moved");
  });
  const rels = data.relations;
  const group = (pred) => rels.filter(pred);
  const RelList = ({ title, items, other }) => items.length === 0 ? null : html`
    <h3>${title}</h3>
    ${items.map((r) => html`<div class="row small" style="padding:2px 0">
      <a href="#" onClick=${(e) => { e.preventDefault(); onPick({ kind: "concept", id: other(r) }); }}>
        ${other(r) === r.source ? r.source_name : r.target_name}</a>
      ${r.necessity === "supportive" && html`<${Badge}>supportive<//>`}
      <span class="grow"></span>
      <button class="ghost sm" title="inspect this relation" onClick=${() => onPick({ kind: "relation", id: r.id })}>ⓘ</button>
    </div>`)}`;
  return html`<div>
    <div class="row"><h2 class="grow">${c.name}</h2>
      <button onClick=${() => setModal("edit")}>Edit</button></div>
    <div class="row wrap" style="margin:6px 0">
      <${Badge} kind="brand">${c.kind}<//>
      <${Badge} kind=${c.status === "accepted" ? "ok" : "warn"}>${c.status}<//>
      ${c.locked && html`<${Badge}>🔒 locked by you<//>`}<${Badge}>v${c.version}<//>
      ${c.topic_ids[0] && html`<${Badge}>${c.topic_ids[0]}<//>`}
    </div>
    <p style="font-weight:500"><${Tex} text=${c.card.one_line} /></p>
    ${areas && areas.length > 0 && html`<div class="row small" style="margin-bottom:6px"><span class="muted">area</span>
      <select style="width:auto" value=${c.topic_ids[0] || ""} onChange=${(e) => moveArea(e.target.value)}
        title="Move this concept to another area (a grouping only; no effect on prerequisites)">
        ${!c.topic_ids[0] && html`<option value="">—</option>`}${areas.map((a) => html`<option value=${a.id}>${a.title}</option>`)}</select></div>`}
    ${c.card.definition && html`<h3>Definition</h3><p><${Tex} text=${c.card.definition} /></p>`}
    ${c.card.formal && html`<h3>Formal</h3><${Formal} text=${c.card.formal} />`}
    ${c.card.key_ideas.length > 0 && html`<h3>Key ideas</h3><ul class="ideas">${c.card.key_ideas.map((k) => html`<li><b>${k.id}</b> <${Tex} text=${k.text} /></li>`)}</ul>`}
    ${c.card.examples.length > 0 && html`<h3>Examples</h3><ul class="ideas">${c.card.examples.map((x) => html`<li><${Tex} text=${x} /></li>`)}</ul>`}
    ${c.card.non_examples.length > 0 && html`<h3>Non-examples</h3><ul class="ideas">${c.card.non_examples.map((x) => html`<li><${Tex} text=${x} /></li>`)}</ul>`}
    ${c.card.scope_conditions && html`<h3>Scope</h3><p><${Tex} text=${c.card.scope_conditions} /></p>`}
    <${RelList} title="Prerequisites (learn first)" items=${group((r) => r.type === "prerequisite_of" && r.target === c.id)} other=${(r) => r.source} />
    <${RelList} title="Needed for" items=${group((r) => r.type === "prerequisite_of" && r.source === c.id)} other=${(r) => r.target} />
    <${RelList} title="Other relations" items=${group((r) => r.type !== "prerequisite_of")} other=${(r) => (r.source === c.id ? r.target : r.source)} />
    <div class="row wrap" style="margin-top:8px">
      <button class="sm" onClick=${() => setModal("rel")}>＋ Add relation</button>
      <button class="sm bad" onClick=${remove}>Remove concept</button></div>
    ${data.used_by_tests.length > 0 && html`<h3>Used by tests</h3>
      ${data.used_by_tests.map((t) => html`<div class="small">• ${clip(t.question, 90)} <${Badge} kind=${t.verdict === "covered" ? "ok" : "warn"}>${t.verdict}<//></div>`)}`}
    <h3>Feedback</h3>
    ${data.feedback.map((f) => html`<div class="fb ${f.severity} small" style="margin-bottom:6px">
      <b>${who(f.author)}</b> · ${f.status}<br/>${f.text}${f.resolution ? html`<div class="muted">${f.resolution}</div>` : null}</div>`)}
    <${FeedbackForm} pid=${pid} target=${{ kind: "concept", id: c.id }} compact />
    <h3>History</h3>
    ${data.history.length === 0 && html`<div class="muted small">No applied changes yet.</div>`}
    ${data.history.map((h) => html`<div class="small" style="margin-bottom:4px">
      <b>#${h.seq}</b> ${who(h.author)}${h.decided_by?.kind === "human" ? " · approved by you" : ""}<br/>
      <span class="muted">${clip(h.rationale, 160)}</span></div>`)}
    ${modal === "edit" && html`<${EditConceptModal} pid=${pid} data=${data} onClose=${() => setModal(null)} />`}
    ${modal === "rel" && html`<${AddRelationModal} pid=${pid} nodes=${nodes} from=${c} onClose=${() => setModal(null)} />`}
  </div>`;
}

/* ------------------------------------------------------------------ review */
function Review({ pid, items, onPick, onPreview, mode }) {
  const proposed = items.filter((c) => c.status === "proposed");
  const applied = items.filter((c) => c.status === "auto_applied" || c.status === "approved");
  const [reason, setReason] = useState({});
  const decide = (id, decision) => guard(async () => {
    await project(pid).post(`/changesets/${id}/decision`, { decision, reason: reason[id] || "" });
    onPreview(null);
  });
  const revert = (id) => guard(async () => { await project(pid).post(`/changesets/${id}/revert`); toast("Reverted"); });
  const approveAll = guard(async () => {
    for (const c of proposed) await project(pid).post(`/changesets/${c.id}/decision`, { decision: "approve" });
  });
  const lastApplied = applied.slice().sort((a, b) => b.seq - a.seq).slice(0, 12);
  return html`<div>
    <div class="row"><h3 class="grow" style="margin:0">Waiting for you (${proposed.length})</h3>
      ${proposed.length > 1 && html`<button class="sm" onClick=${approveAll}>Approve all</button>`}</div>
    ${proposed.length === 0 && html`<div class="empty">
      ${mode === "auto" ? "Automatic mode: proposals apply by themselves." : "No proposals waiting."}</div>`}
    <div class="list">${proposed.map((c) => html`<div class="card" key=${c.id}
      onMouseEnter=${() => onPreview(c)} onMouseLeave=${() => onPreview(null)}>
      <div class="row wrap"><b>${who(c.author)}</b><${Badge}>${Math.round(c.confidence * 100)}% sure<//>
        ${c.touches_locked && html`<${Badge} kind="bad">touches your locked items<//>`}
        <span class="grow"></span><span class="muted small">${ago(c.created_at)}</span></div>
      <div class="row wrap" style="margin:8px 0">
        <button class="good" onClick=${decide(c.id, "approve")} title="Apply and lock: agents will only propose changes to these items">Approve</button>
        <button onClick=${decide(c.id, "apply")} title="Apply without locking">Apply</button>
        <button class="bad" onClick=${decide(c.id, "reject")}>Reject</button>
        <input class="grow" placeholder="Reason if rejecting (optional)" style="min-width:120px"
          value=${reason[c.id] || ""} onInput=${(e) => setReason({ ...reason, [c.id]: e.target.value })} />
      </div>
      <div class="clamp" title="Rationale">${c.rationale}</div>
      <div class="diff scroll">${(c.diff || []).map((d) => html`<div class="ln ${d.change}" onClick=${() => d.change !== "removed" && onPick({ kind: d.kind, id: d.id })} style="cursor:pointer">
        <b>${d.change}</b> ${d.kind}: ${d.label}${d.detail ? html` <span class="muted">(${d.detail})</span>` : ""}</div>`)}</div>
      ${c.errors?.length > 0 && html`<div class="small" style="color:var(--bad)">Cannot apply: ${c.errors.join("; ")}</div>`}
    </div>`)}</div>
    <h3>Recently applied</h3>
    <div class="list">${lastApplied.map((c) => html`<div class="card" key=${c.id} style="padding:8px">
      <div class="row"><span class="small grow"><b>#${c.seq}</b> ${who(c.author)} — ${clip(c.rationale, 90)}</span>
        <button class="sm" onClick=${revert(c.id)}>Revert</button></div>
      <${ChangeChips} diff=${c.diff} onPick=${onPick} /></div>`)}</div>
  </div>`;
}

/* ---------------------------------------------------------------- feedback */
function FeedbackPane({ pid, items, onPick }) {
  const [filter, setFilter] = useState("open");
  const [answers, setAnswers] = useState({});
  const questions = items.filter((f) => f.kind === "question" && f.status === "open");
  const rest = items.filter((f) => f.kind !== "question" || f.status !== "open")
    .filter((f) => filter === "all" || (filter === "open" ? f.status === "open" || f.status === "in_review" : f.status === filter));
  const answer = (q, text) => guard(async () => {
    await project(pid).post(`/feedback/${q.id}/answer`, { text: text ?? answers[q.id] });
  });
  const resolve = (f, status) => guard(async () => project(pid).post(`/feedback/${f.id}/resolve`, { status }));
  return html`<div>
    ${questions.length > 0 && html`<h3 style="color:var(--purple)">The agents are asking (${questions.length})</h3>`}
    <div class="list">${questions.map((q) => html`<div class="card fb question" key=${q.id}>
      <div class="small muted">${q.author.name}${q.target_id ? ` · about ${q.target_id}` : ""}</div>
      <p style="margin:4px 0">${q.text}</p>
      <div class="row wrap">${q.options.map((o) => html`<button class="sm" onClick=${answer(q, o)}>${o}</button>`)}</div>
      <div class="row" style="margin-top:6px"><input placeholder="Answer in your own words…" value=${answers[q.id] || ""}
        onInput=${(e) => setAnswers({ ...answers, [q.id]: e.target.value })} />
        <button class="primary sm" disabled=${!(answers[q.id] || "").trim()} onClick=${answer(q)}>Send</button></div>
    </div>`)}</div>
    <h3>New feedback for the agents</h3>
    <${FeedbackForm} pid=${pid} />
    <h3>Notes</h3>
    <div class="seg" style="margin-bottom:8px">${[["open", "Open"], ["addressed", "Addressed"], ["dismissed", "Dismissed"], ["all", "All"]].map(([k, l]) =>
      html`<button class=${filter === k ? "on" : ""} onClick=${() => setFilter(k)}>${l}</button>`)}</div>
    ${rest.length === 0 && html`<div class="empty">Nothing here.</div>`}
    <div class="list">${rest.slice().reverse().map((f) => html`<div class="fb ${f.severity}" key=${f.id}>
      <div class="row wrap small"><b>${who(f.author)}</b><${Badge}>${f.kind}<//><${Badge} kind=${f.status === "open" ? "warn" : f.status === "addressed" ? "ok" : ""}>${f.status.replace("_", " ")}<//>
        ${f.target_id && html`<a href="#" onClick=${(e) => { e.preventDefault(); (f.target_kind === "concept" || f.target_kind === "relation") && onPick({ kind: f.target_kind, id: f.target_id }); }}>${clip(f.target_id, 28)}</a>`}</div>
      <div style="margin:3px 0">${f.text}</div>
      ${f.resolution && html`<div class="small muted">${f.resolution}</div>`}
      <div class="row">
        ${f.status === "open" && html`<button class="sm" onClick=${resolve(f, "dismissed")}>Dismiss</button><button class="sm" onClick=${resolve(f, "addressed")}>Mark addressed</button>`}
        ${(f.status === "dismissed" || f.status === "addressed") && html`<button class="sm" onClick=${resolve(f, "open")}>Reopen</button>`}
      </div></div>`)}</div>
  </div>`;
}

/* ------------------------------------------------------------------- tests */
function Tests({ pid, tests, onTrace, activeTrace }) {
  const [q, setQ] = useState("");
  const [exp, setExp] = useState("");
  const add = guard(async () => {
    await project(pid).post("/tests", { question: q, expected: exp, trace_now: true });
    setQ(""); setExp(""); toast("Test added; the tester will trace it");
  });
  const retrace = (t) => guard(async () => { await project(pid).post(`/tests/${t.id}/trace`); toast("Queued"); });
  const toggle = (t) => guard(async () => { await project(pid).post(`/tests/${t.id}/active?active=${!t.active}`); });
  const verdict = { covered: "ok", gaps: "warn", not_covered: "bad" };
  return html`<div>
    <h3 style="margin-top:0">Add a test question</h3>
    <textarea placeholder="e.g. Find gcd(84, 126) and explain why the Euclidean algorithm works" value=${q} onInput=${(e) => setQ(e.target.value)} style="min-height:56px"></textarea>
    <input placeholder="Expected answer / solution outline (optional)" value=${exp} onInput=${(e) => setExp(e.target.value)} style="margin-top:6px" />
    <div class="row" style="margin-top:6px"><div class="grow"></div><button class="primary" disabled=${!q.trim()} onClick=${add}>Add and trace</button></div>
    <h3>Tests (${tests.length})</h3>
    ${tests.length === 0 && html`<div class="empty">The tester writes questions during review rounds, or add your own.</div>`}
    <div class="list">${tests.map((t) => {
      const tr = t.trace;
      const open = activeTrace === t.id;
      return html`<div class="card" key=${t.id} style=${t.active ? "" : "opacity:.55"}>
        <div class="row wrap"><${Badge}>${t.origin}<//>
          ${tr ? html`<${Badge} kind=${verdict[tr.verdict]}>${tr.verdict.replace("_", " ")}<//>` : html`<${Badge}>not traced<//>`}
          <span class="grow"></span>
          <button class="sm" onClick=${() => onTrace(open ? null : t)} disabled=${!tr}>${open ? "Hide on graph" : "Show on graph"}</button></div>
        <p style="margin:6px 0">${t.question}</p>
        ${t.expected && html`<div class="small muted">Expected: ${clip(t.expected, 200)}</div>`}
        ${tr && html`<div class="small" style="margin-top:6px">${tr.summary}</div>
          ${open && html`<ol class="ideas small">${tr.steps.map((s) => html`<li>${s.text}
            ${s.missing.length > 0 && html`<div style="color:var(--bad)">missing: ${s.missing.join("; ")}</div>`}</li>`)}</ol>
            ${tr.missing_edges.map((m) => html`<div class="small" style="color:var(--bad)">missing relation: ${m.source} → ${m.target}${m.reason ? ` (${m.reason})` : ""}</div>`)}`}`}
        <div class="row" style="margin-top:6px"><button class="sm" onClick=${retrace(t)}>Trace again</button>
          <button class="sm" onClick=${toggle(t)}>${t.active ? "Deactivate" : "Activate"}</button></div>
      </div>`;
    })}</div></div>`;
}

/* ---------------------------------------------------------------- materials */
// Exercise bank (LEARNING_SIDE_PLAN L7.1): upload sheets, agents extract and match exercises to
// concepts, the expert confirms or fixes uncertain matches. Learners only see confirmed ones.
function EditLinksModal({ pid, task, nodes, onClose }) {
  const [links, setLinks] = useState(task.links.map((l) => ({ concept_id: l.concept_id, name: l.name, facet: l.facet, weight: l.weight })));
  const [pick, setPick] = useState("");
  const sorted = useMemo(() => [...nodes].sort((a, b) => a.name.localeCompare(b.name)), [nodes]);
  const add = () => {
    const n = nodes.find((x) => x.id === pick);
    if (n && !links.some((l) => l.concept_id === n.id)) setLinks([...links, { concept_id: n.id, name: n.name, facet: "apply", weight: "primary" }]);
    setPick("");
  };
  const update = (i, k, v) => setLinks(links.map((l, j) => (j === i ? { ...l, [k]: v } : l)));
  const save = guard(async () => {
    await project(pid).post(`/tasks/${task.id}/decision`, { action: "edit",
      approaches: [{ label: "expert", requirements: links.map(({ concept_id, facet, weight }) => ({ concept_id, facet, weight })) }] });
    toast("Links saved and confirmed"); onClose();
  });
  return html`<${Modal} title="Concepts this exercise needs" onClose=${onClose}>
    <p class="small">${clip(task.prompt, 240)}</p>
    ${links.length === 0 && html`<div class="muted small">No links yet.</div>`}
    ${links.map((l, i) => html`<div class="row" style="margin-bottom:6px" key=${l.concept_id}>
      <span class="grow small">${l.name}</span>
      <select style="width:auto" value=${l.facet} onChange=${(e) => update(i, "facet", e.target.value)}>
        ${["recall", "apply", "explain", "transfer"].map((f) => html`<option value=${f}>${f}</option>`)}</select>
      <select style="width:auto" value=${l.weight} onChange=${(e) => update(i, "weight", e.target.value)}>
        <option value="primary">main</option><option value="secondary">on the way</option></select>
      <button class="sm bad" onClick=${() => setLinks(links.filter((_, j) => j !== i))}>✕</button></div>`)}
    <div class="row" style="margin-top:10px">
      <select value=${pick} onChange=${(e) => setPick(e.target.value)}>
        <option value="">add a concept…</option>${sorted.map((n) => html`<option value=${n.id}>${n.name}</option>`)}</select>
      <button class="sm" disabled=${!pick} onClick=${add}>Add</button></div>
    <div class="row" style="margin-top:12px"><div class="grow"></div>
      <button onClick=${onClose}>Cancel</button>
      <button class="primary" disabled=${links.length === 0} onClick=${save}>Save and confirm</button></div>
  <//>`;
}

function Materials({ pid, tasks, nodes, onPreview, reload }) {
  const [filter, setFilter] = useState("proposed");
  const [title, setTitle] = useState("");
  const [text, setText] = useState("");
  const [busy, setBusy] = useState(false);
  const [edit, setEdit] = useState(null);
  const [form, setForm] = useState({ prompt: "", type: "numeric", answers: "", solution: "", options: "" });
  const fileRef = useRef();
  const upload = guard(async () => {
    const f = fileRef.current.files[0];
    if (!f) return;
    setBusy(true);
    try {
      const fd = new FormData(); fd.append("file", f); fd.append("title", title || f.name);
      await project(pid).post("/materials/file", fd);
      toast("Exercise sheet added: the agents will extract and match its exercises"); fileRef.current.value = ""; setTitle(""); reload();
    } finally { setBusy(false); }
  });
  const addText = guard(async () => {
    await project(pid).post("/materials/text", { title: title || "Exercises", text });
    setText(""); setTitle(""); toast("Added: extraction will run"); reload();
  });
  const addOne = guard(async () => {
    const split = (v) => v.split(",").map((x) => x.trim()).filter(Boolean);
    await project(pid).post("/tasks", { prompt: form.prompt, task_type: form.type, solution: form.solution,
      correct_answers: split(form.answers), options: form.options.split("\n").map((x) => x.trim()).filter(Boolean) });
    setForm({ ...form, prompt: "", answers: "", solution: "", options: "" }); reload();
  });
  const decide = (t, action) => guard(async () => { await project(pid).post(`/tasks/${t.id}/decision`, { action }); reload(); });
  const groups = { proposed: "Needs your review", confirmed: "Live for learners", unmatched: "Waiting to be matched", rejected: "Rejected", all: "All" };
  const count = (k) => (k === "all" ? tasks.length : tasks.filter((t) => t.match_status === k).length);
  const shown = tasks.filter((t) => filter === "all" || t.match_status === filter);
  return html`<div>
    <p class="muted small">Exercise sheets are not teaching text: agents split them into exercises and link each to the concepts it needs. Learners get the confirmed ones, per concept, and answers feed what the system believes.</p>
    <h3 style="margin-top:0">Add exercises</h3>
    <div class="row"><input type="file" ref=${fileRef} accept=".pdf,.txt,.md,.markdown,.tex" />
      <button class="primary" disabled=${busy} onClick=${upload}>${busy ? "Reading…" : "Upload"}</button></div>
    <input placeholder="Title (optional)" value=${title} onInput=${(e) => setTitle(e.target.value)} style="margin-top:6px" />
    <textarea placeholder="…or paste an exercise sheet" value=${text} onInput=${(e) => setText(e.target.value)} style="margin-top:6px;min-height:80px"></textarea>
    <div class="row" style="margin-top:6px"><div class="grow"></div><button class="primary" disabled=${!text.trim()} onClick=${addText}>Add sheet</button></div>
    <details style="margin-top:8px"><summary class="small">Add a single exercise by hand</summary>
      <textarea placeholder="Exercise text" value=${form.prompt} onInput=${(e) => setForm({ ...form, prompt: e.target.value })}></textarea>
      <div class="row" style="margin-top:6px">
        <select style="width:auto" value=${form.type} onChange=${(e) => setForm({ ...form, type: e.target.value })}>
          ${["numeric", "mcq", "short_answer", "explain_why", "compare", "transfer_problem", "worked_problem"].map((t) => html`<option value=${t}>${t}</option>`)}</select>
        <input placeholder="Correct answer(s), comma separated" value=${form.answers} onInput=${(e) => setForm({ ...form, answers: e.target.value })} /></div>
      ${form.type === "mcq" && html`<textarea placeholder="Options, one per line" value=${form.options} onInput=${(e) => setForm({ ...form, options: e.target.value })} style="margin-top:6px;min-height:60px"></textarea>`}
      <textarea placeholder="Worked solution (optional; used for grading and 'show solution')" value=${form.solution} onInput=${(e) => setForm({ ...form, solution: e.target.value })} style="margin-top:6px;min-height:50px"></textarea>
      <div class="row" style="margin-top:6px"><div class="grow"></div><button class="primary" disabled=${!form.prompt.trim()} onClick=${addOne}>Add exercise</button></div></details>
    <h3>Exercises (${tasks.length})</h3>
    <div class="seg" style="margin-bottom:8px;flex-wrap:wrap">${Object.entries(groups).map(([k, l]) =>
      html`<button class=${filter === k ? "on" : ""} onClick=${() => setFilter(k)}>${l.split(" ")[0] === "Needs" ? "Review" : l.split(" ")[0]} ${count(k)}</button>`)}</div>
    ${shown.length === 0 && html`<div class="empty">Nothing here.</div>`}
    <div class="list">${shown.map((t) => html`<div class="card" key=${t.id}
      onMouseEnter=${() => onPreview(t.links.map((l) => l.concept_id))} onMouseLeave=${() => onPreview(null)}>
      <div class="row wrap"><b class="grow">${t.title || clip(t.prompt, 50)}</b>
        <${Badge}>${t.task_type}<//>
        <${Badge} kind=${t.match_status === "confirmed" ? "ok" : t.match_status === "proposed" ? "warn" : ""}>${t.match_status}<//>
        ${t.match_status === "proposed" && html`<${Badge}>${Math.round(t.match_confidence * 100)}% sure<//>`}</div>
      <div class="small" style="margin:5px 0">${clip(t.prompt, 220)}</div>
      ${t.links.length > 0 ? html`<div class="row wrap">${t.links.map((l) => html`<span class="chip" title=${`${l.facet}, ${l.weight}`}>${l.name} · ${l.facet}${l.weight === "secondary" ? " (on the way)" : ""}</span>`)}</div>`
        : t.match_status !== "unmatched" && html`<div class="small muted">No concepts linked.</div>`}
      ${t.unmapped.length > 0 && html`<div class="small" style="color:var(--warn)">The graph lacks: ${t.unmapped.join("; ")} (sent to the agents as feedback)</div>`}
      <div class="row wrap" style="margin-top:6px">
        ${t.match_status === "proposed" && t.links.length > 0 && html`<button class="good sm" onClick=${decide(t, "confirm")}>Confirm</button>`}
        ${t.match_status !== "rejected" && html`<button class="sm" onClick=${() => setEdit(t)}>Edit links</button>`}
        ${t.match_status !== "rejected" && html`<button class="sm bad" onClick=${decide(t, "reject")}>Reject</button>`}
        ${t.match_status !== "unmatched" && html`<button class="sm" onClick=${decide(t, "rematch")} title="Let the matcher try again">Re-match</button>`}
      </div></div>`)}</div>
    ${edit && html`<${EditLinksModal} pid=${pid} task=${edit} nodes=${nodes} onClose=${() => { setEdit(null); reload(); }} />`}
  </div>`;
}

/* ------------------------------------------------------------------ sources */
function Sources({ pid, sources, reload }) {
  const [title, setTitle] = useState("");
  const [text, setText] = useState("");
  const [busy, setBusy] = useState(false);
  const fileRef = useRef();
  const addText = guard(async () => {
    await project(pid).post("/sources/text", { title: title || "Expert notes", text });
    setText(""); setTitle(""); toast("Source added — the planner will take it into account"); reload();
  });
  const upload = guard(async () => {
    const f = fileRef.current.files[0];
    if (!f) return;
    setBusy(true);
    try {
      const fd = new FormData(); fd.append("file", f); fd.append("title", title || f.name);
      await project(pid).post("/sources/file", fd);
      toast("File added"); setTitle(""); fileRef.current.value = ""; reload();
    } finally { setBusy(false); }
  });
  const remove = (s) => guard(async () => { await project(pid).del(`/sources/${s.id}`); reload(); });
  return html`<div>
    <p class="muted small">Give the agents your own material at any time: a syllabus, lecture notes, a textbook chapter (PDF, text or Markdown), or just a description of what you want covered.</p>
    <h3>Upload a file</h3>
    <div class="row"><input type="file" ref=${fileRef} accept=".pdf,.txt,.md,.markdown,.tex" />
      <button class="primary" disabled=${busy} onClick=${upload}>${busy ? "Reading…" : "Add"}</button></div>
    <h3>Or paste text</h3>
    <input placeholder="Title (optional)" value=${title} onInput=${(e) => setTitle(e.target.value)} />
    <textarea placeholder="Paste notes, an exercise sheet, a description of scope…" value=${text} onInput=${(e) => setText(e.target.value)} style="margin-top:6px;min-height:120px"></textarea>
    <div class="row" style="margin-top:6px"><div class="grow"></div><button class="primary" disabled=${!text.trim()} onClick=${addText}>Add text</button></div>
    <h3>Sources (${sources.length})</h3>
    ${sources.length === 0 && html`<div class="empty">No sources yet. Agents then rely on standard curriculum knowledge.</div>`}
    <div class="list">${sources.map((s) => html`<div class="card">
      <div class="row"><b class="grow">${s.title}</b><button class="sm bad" onClick=${remove(s)}>Remove</button></div>
      <div class="small muted">${s.chunks} parts · ${s.chars.toLocaleString()} characters · ${ago(s.created_at)}</div>
      <div class="small" style="margin-top:4px">${clip(s.preview, 220)}</div></div>`)}</div>
  </div>`;
}

/* -------------------------------------------------------------------- setup */
function Setup({ pid, snap, reload }) {
  const [brief, setBrief] = useState(snap.brief);
  const [llm, setLlm] = useState(null);
  const [roleModels, setRoleModels] = useState({});
  useEffect(() => { project(pid).get("/llm").then((d) => {
    setLlm(d);
    setRoleModels(Object.fromEntries(Object.entries(d.roles).map(([r, v]) => [r, v.override || ""])));
  }); }, [pid]);
  const saveBrief = guard(async () => { await project(pid).put("/brief", brief); toast("Brief saved — the planner will re-plan"); reload(); });
  const saveModels = guard(async () => { setLlm(await project(pid).put("/llm", { roles: roleModels })); toast("Models updated"); });
  return html`<div>
    <h3 style="margin-top:0">Topic brief</h3>
    <${Field} label="Title"><input value=${brief.title} onInput=${(e) => setBrief({ ...brief, title: e.target.value })} /><//>
    <${Field} label="Audience / level"><input value=${brief.audience} onInput=${(e) => setBrief({ ...brief, audience: e.target.value })} /><//>
    <${Field} label="Scope and aspects to cover"><textarea style="min-height:130px" value=${brief.description}
      onInput=${(e) => setBrief({ ...brief, description: e.target.value })}></textarea><//>
    <div class="row" style="margin-top:6px"><div class="grow"></div><button class="primary" onClick=${saveBrief}>Save brief</button></div>
    <h3>Models per agent</h3>
    ${!llm ? html`<div class="muted">Loading…</div>` : html`
      <p class="small muted">Any model from any configured provider, written <code>provider:model</code>
        (e.g. <code>openai:gpt-5.1</code>, <code>ollama:qwen3:32b</code>). Empty = default for that role.</p>
      <datalist id="models">${Object.keys(llm.providers).map((p) => html`<option value=${`${p}:`}></option>`)}
        ${Object.values(llm.tiers).map((m) => html`<option value=${m}></option>`)}</datalist>
      ${Object.entries(llm.roles).map(([role, v]) => html`<div class="row" style="margin-bottom:6px">
        <span style="width:80px">${ROLE_ICON[role]} ${role}</span>
        <input list="models" placeholder=${v.model} value=${roleModels[role] || ""}
          onInput=${(e) => setRoleModels({ ...roleModels, [role]: e.target.value })} /></div>`)}
      <div class="row"><div class="grow"></div><button class="primary" onClick=${saveModels}>Save models</button></div>
      <h3>Providers</h3>
      ${Object.entries(llm.providers).map(([name, p]) => html`<div class="row small" style="padding:2px 0">
        <b style="width:90px">${name}</b><${Badge}>${p.kind}<//>
        ${p.api_key_env ? html`<${Badge} kind=${p.key_present ? "ok" : "bad"}>${p.api_key_env} ${p.key_present ? "set" : "missing"}<//>` : html`<${Badge}>no key<//>`}
        ${llm.default_provider === name && html`<${Badge} kind="brand">default<//>`}</div>`)}
      <h3>Usage</h3>
      ${llm.usage.length === 0 ? html`<div class="muted small">No model calls yet.</div>` : html`<table class="small" style="width:100%;border-collapse:collapse">
        <tr class="muted"><td>step</td><td>calls</td><td>cached</td><td>tokens in/out</td></tr>
        ${llm.usage.map((u) => html`<tr><td>${u.step}</td><td>${u.calls}</td><td>${u.cache_hits}</td><td>${u.in.toLocaleString()} / ${u.out.toLocaleString()}</td></tr>`)}</table>`}`}
  </div>`;
}

/* -------------------------------------------------------------- graph panel */
function GraphPanel({ pid, graphData, diffRef, selection, onSelect, preview, trace, nodesCount, onAdd, readOnly }) {
  const box = useRef();
  const ctl = useRef();
  const [hidden, setHidden] = useState(new Set(["related"]));
  const [colorBy, setColorBy] = useState("area");
  const [dir, setDir] = useState("LR");
  const [frames, setFrames] = useState(true);  // draw expanded areas as boxes
  // super-nodes: null = every area expanded (the Studio default, so building stays visible)
  const [expanded, setExpanded] = useState(null);
  const lens = useLens({ pid, version: graphData?.latest_seq, author: "expert" });
  const areas = graphData?.areas || [];
  const allAreas = useMemo(() => new Set(areas.map((a) => a.id)), [graphData?.areas]);
  const eff = useMemo(() => expanded ?? allAreas, [expanded, allAreas]);
  const shown = useMemo(() => lens.display(graphData), [graphData, lens.active, lens.editing, lens.context, lens.glow]);
  // inside a view every area is open: the view itself is the simplification
  const view = useMemo(() => (shown ? groupView(shown, lens.focused ? allAreas : eff, { frames }) : null),
    [shown, eff, frames, lens.focused, allAreas]);
  const toggleArea = (id) => {
    const n = new Set(eff);
    if (n.has(id)) n.delete(id); else { n.add(id); ctl.current?.openArea(id); }
    setExpanded(n);
  };
  const select = useRef(onSelect);
  select.current = (s) => {
    if (lens.onSelect(s)) return;
    s && s.kind === "group" ? toggleArea(s.id) : onSelect(s);
  };
  const lensKey = lens.editing ? "edit" : lens.active?.id || "";
  const lastLens = useRef(lensKey);
  useEffect(() => {
    ctl.current = new GraphController(box.current, { onSelect: (s) => select.current(s) });
    return () => ctl.current.destroy();
  }, []);
  useEffect(() => {  // selecting something inside a collapsed area opens that area
    const n = selection?.kind === "concept" && graphData?.nodes.find((x) => x.id === selection.id);
    if (n && n.area && !eff.has(n.area)) toggleArea(n.area);
  }, [selection?.id]);
  useEffect(() => {
    if (!view || !ctl.current) return;
    const diff = diffRef.current.splice(0);
    const fit = lastLens.current !== lensKey;  // entering or leaving a view: frame it
    lastLens.current = lensKey;
    ctl.current.setData(view, { diff, animate: !readOnly && diff.length > 0 && !fit, fit });
    ctl.current.setHiddenTypes(hidden);
  }, [view]);
  useEffect(() => { ctl.current?.setHiddenTypes(hidden); }, [hidden]);
  useEffect(() => { ctl.current?.setColorBy(colorBy); }, [colorBy]);
  useEffect(() => { ctl.current?.focus(selection); }, [selection, view]);
  useEffect(() => {
    const c = ctl.current; if (!c) return;
    if (preview) c.highlight(preview, "preview"); else c.clear("preview");
  }, [preview]);
  useEffect(() => { const c = ctl.current; if (!c) return; trace ? c.showTrace(trace) : c.clearTrace(); }, [trace]);
  const toggleType = (t) => { const n = new Set(hidden); n.has(t) ? n.delete(t) : n.add(t); setHidden(n); };
  return html`<div class="canvas-wrap">
    <div class="canvas" ref=${box}></div>
    <div class="toolbar">
      <div class="card">
        <span class="muted">color</span>
        <select style="width:auto;padding:2px 6px" value=${colorBy} onChange=${(e) => setColorBy(e.target.value)}>
          <option value="area">by area</option><option value="status">by review status</option><option value="kind">by kind</option></select>
        <select style="width:auto;padding:2px 6px" value=${dir} onChange=${(e) => { setDir(e.target.value); ctl.current.setDirection(e.target.value); }}>
          <option value="LR">left → right</option><option value="TB">top → bottom</option></select>
        <button class="sm" onClick=${() => ctl.current.layout(true, true)}>Re-layout</button>
        <button class="sm" onClick=${() => ctl.current.fit()}>Fit</button>
        <button class="sm" onClick=${onAdd} disabled=${readOnly}>＋ Concept</button>
        <label class="inline-check" title="Draw each open area as a box; click its title to collapse it">
          <input type="checkbox" checked=${frames} onChange=${(e) => setFrames(e.target.checked)} /> area boxes</label>
        <${LensToggle} lens=${lens} />
        <span class="muted">${nodesCount.nodes} nodes · ${nodesCount.edges} edges</span>
      </div>
      <${LensBanner} lens=${lens} />
      ${!lens.focused && html`<${AreaBar} areas=${areas} expanded=${eff} onToggle=${toggleArea}
        onAll=${() => setExpanded(new Set(areas.map((a) => a.id)))} onNone=${() => setExpanded(new Set())} />`}
    </div>
    <${LensPanel} lens=${lens} selected=${selection?.kind === "concept" ? selection.id : null}
      selectedName=${selection?.kind === "concept" ? graphData?.nodes.find((n) => n.id === selection.id)?.name : ""} />
    <div class="card legend">
      ${Object.entries(EDGE_TYPES).map(([t, v]) => html`<label style="margin:0;display:flex;gap:4px;align-items:center;cursor:pointer;color:var(--ink)">
        <input type="checkbox" style="width:auto" checked=${!hidden.has(t)} onChange=${() => toggleType(t)} />
        <i style=${`border-color:${v.color}`}></i>${v.label}</label>`)}
      <span class="muted">dashed node = proposed · solid = accepted · thick = locked</span>
    </div>
  </div>`;
}

/* ----------------------------------------------------------------- studio */
export function Studio({ pid, onHome }) {
  const P = useMemo(() => project(pid), [pid]);
  const [snap, setSnap] = useState(null);
  const [graphData, setGraphData] = useState(null);
  const [events, setEvents] = useState([]);
  const [changesets, setChangesets] = useState([]);
  const [feedback, setFeedback] = useState([]);
  const [tests, setTests] = useState([]);
  const [sources, setSources] = useState([]);
  const [tasks, setTasks] = useState([]);
  const [tab, setTab] = useState("activity");
  const [selection, setSelection] = useState(null);
  const [previewIds, setPreviewIds] = useState(null);
  const [traceId, setTraceId] = useState(null);
  const [at, setAt] = useState(null);
  const [tick, setTick] = useState(0);
  const [addConcept, setAddConcept] = useState(false);
  const diffRef = useRef([]);
  const lastSeq = useRef(0);
  const atRef = useRef(null);
  atRef.current = at;
  const timers = useRef({});

  const debounce = useCallback((key, fn, ms = 120) => {
    clearTimeout(timers.current[key]);
    timers.current[key] = setTimeout(fn, ms);
  }, []);
  const loadSnap = useCallback(() => P.get().then(setSnap).catch(() => {}), [P]);
  const loadGraph = useCallback(() => P.get(atRef.current === null ? "/graph" : `/graph?at=${atRef.current}`)
    .then(setGraphData).catch(() => {}), [P]);
  const loadChangesets = useCallback(() => P.get("/changesets?limit=300").then(setChangesets).catch(() => {}), [P]);
  const loadFeedback = useCallback(() => P.get("/feedback").then(setFeedback).catch(() => {}), [P]);
  const loadTests = useCallback(() => P.get("/tests").then(setTests).catch(() => {}), [P]);
  const loadSources = useCallback(() => P.get("/sources").then(setSources).catch(() => {}), [P]);
  const loadTasks = useCallback(() => P.get("/tasks").then(setTasks).catch(() => {}), [P]);
  const loadAll = useCallback(() => {
    loadSnap(); loadGraph(); loadChangesets(); loadFeedback(); loadTests(); loadSources(); loadTasks();
  }, [loadSnap, loadGraph, loadChangesets, loadFeedback, loadTests, loadSources, loadTasks]);

  const handle = useCallback((e) => {
    lastSeq.current = Math.max(lastSeq.current, e.seq);
    setEvents((old) => (old.some((x) => x.seq === e.seq) ? old : [...old, e].slice(-400)));
    const t = e.type;
    debounce("snap", loadSnap, 250);
    if (t === "changeset.applied" || t === "changeset.reverted") {
      if (t === "changeset.applied") diffRef.current.push(...(e.data.diff || []));
      debounce("graph", loadGraph, 150);
      debounce("cs", loadChangesets);
      setTick((n) => n + 1);
    } else if (t === "changeset.proposed" || t === "changeset.rejected") {
      debounce("cs", loadChangesets);
    } else if (t.startsWith("feedback") || t === "question.asked") {
      debounce("fb", loadFeedback);
    } else if (t.startsWith("test.")) {
      debounce("tests", loadTests);
    } else if (t.startsWith("source.")) {
      debounce("src", loadSources);
    } else if (t.startsWith("task.")) {
      debounce("tasks", loadTasks);
    } else if (t === "blueprint.updated") {
      debounce("graph", loadGraph, 150);
    }
  }, [debounce, loadSnap, loadGraph, loadChangesets, loadFeedback, loadTests, loadSources, loadTasks]);

  useEffect(() => {
    let es, closed = false, first = true;
    loadAll();
    P.get("/events?limit=300").then((evs) => {
      setEvents(evs); lastSeq.current = evs.length ? evs[evs.length - 1].seq : 0;
    });
    const connect = () => {
      es = new EventSource(`${P.base}/events/stream`);
      es.onopen = () => {
        if (first) { first = false; return; }
        // reconnected: catch up on what we missed
        P.get(`/events?since=${lastSeq.current}`).then((evs) => evs.forEach(handle));
        loadAll();
      };
      es.onmessage = (m) => handle(JSON.parse(m.data));
      es.onerror = () => {};
    };
    connect();
    return () => { closed = true; es && es.close(); Object.values(timers.current).forEach(clearTimeout); };
  }, [pid]);

  // history slider
  useEffect(() => { debounce("graph", loadGraph, 80); }, [at]);

  if (!snap) return html`<div class="empty">Loading…</div>`;
  const run = snap.run;
  const mode = run.mode;
  const live = run.status === "running";
  const counts = snap.counts;
  const needsYou = counts.pending + counts.questions;
  const act = guard(async (action, m) => { setSnap({ ...snap, run: await P.post("/run", { action, mode: m }) }); loadSnap(); });
  const setMode = (m) => act("mode", m);

  const pick = (s) => {
    if (s && s.kind === "concept") setTab("inspect");
    if (s && s.kind === "relation") setTab("inspect");
    setSelection(s);
  };
  const preview = (cs) => setPreviewIds(cs ? cs.diff.filter((d) => d.change !== "added").map((d) => d.id) : null);
  const trace = traceId ? tests.find((t) => t.id === traceId)?.trace : null;
  const latest = graphData?.latest_seq ?? snap.seq;
  const viewing = at !== null && at < latest;
  const tabs = [
    ["activity", "Activity"], ["inspect", "Inspect"],
    ["review", "Review", counts.pending, "warn"], ["feedback", "Feedback", counts.questions, "purple"],
    ["tests", "Tests"], ["materials", "Materials", counts.tasks_proposed, "warn"], ["plan", "Plan"],
    ["sources", "Sources", 0], ["setup", "Setup"],
  ];
  const statusText = run.status === "running" ? (run.detail || "Working") : run.detail || run.status;
  const startLabel = run.status === "idle" ? "Start" : "Resume";
  const canPause = live || run.status === "waiting";
  return html`<div class="shell">
    <div class="topbar">
      <button class="ghost" onClick=${onHome} title="All topics">←</button>
      <div class="title" title=${snap.brief.title}>${snap.brief.title}</div>
      <${SideSwitch} side="build" pid=${pid} />
      <div class="status"><span class="dot ${run.status}"></span><span class="txt" title=${run.error || statusText}>
        ${run.current_agent ? ROLE_ICON[run.current_agent] + " " : ""}${run.status === "error" ? "Error: " + clip(run.error, 70) : statusText}</span></div>
      <div class="row">
        ${canPause
          ? html`<button onClick=${() => act("pause")}>⏸ Pause</button>`
          : html`<button class="primary" onClick=${() => act(run.status === "idle" ? "start" : "resume")}>▶ ${startLabel}</button>`}
        <button onClick=${() => act("step")} disabled=${live} title="Run exactly one agent step">⏭ Step</button>
        <button onClick=${() => act("review")} title="Run another critique → test → refine round">🔎 Review again</button>
      </div>
      <div class="grow"></div>
      <div class="seg" title="How much you want to be involved">${MODES.map(([k, l, d]) =>
        html`<button class=${mode === k ? "on" : ""} title=${d} onClick=${() => setMode(k)}>${l}</button>`)}</div>
      ${needsYou > 0 && html`<button class="primary" onClick=${() => setTab(counts.pending ? "review" : "feedback")}>
        ${needsYou} waiting for you</button>`}
    </div>
    <div class="main">
      <div class="stage">
        <${GraphPanel} pid=${pid} graphData=${graphData} diffRef=${diffRef} selection=${selection} onSelect=${pick}
          preview=${previewIds} trace=${trace} readOnly=${viewing} onAdd=${() => setAddConcept(true)}
          nodesCount=${{ nodes: graphData?.nodes.length || 0, edges: graphData?.edges.length || 0 }} />
        <div class="timeline">
          <span class="small muted">history</span>
          <input type="range" min="0" max=${latest} value=${at === null ? latest : at}
            onInput=${(e) => { const v = +e.target.value; setAt(v >= latest ? null : v); }} disabled=${latest === 0} />
          <span class="small" style="min-width:150px">${viewing ? `viewing step ${at} of ${latest}` : `live · step ${latest}`}</span>
          ${viewing && html`<button class="sm" onClick=${() => setAt(null)}>Back to live</button>`}
        </div>
      </div>
      <div class="side">
        <div class="tabs">${tabs.map(([k, l, n, c]) => html`<button class=${tab === k ? "on" : ""} onClick=${() => setTab(k)}>
          ${l}${n > 0 ? html`<span class="n ${c || ""}">${n}</span>` : ""}</button>`)}</div>
        <div class="pane">
          ${tab === "activity" && html`<${Activity} events=${events} onPick=${pick} />`}
          ${tab === "inspect" && html`<${Inspector} pid=${pid} selection=${selection} nodes=${graphData?.nodes || []} areas=${graphData?.areas || []}
            version=${latest} tick=${tick} onPick=${pick} />`}
          ${tab === "review" && html`<${Review} pid=${pid} items=${changesets} onPick=${pick} onPreview=${preview} mode=${mode} />`}
          ${tab === "feedback" && html`<${FeedbackPane} pid=${pid} items=${feedback} onPick=${pick} />`}
          ${tab === "tests" && html`<${Tests} pid=${pid} tests=${tests} activeTrace=${traceId}
            onTrace=${(t) => setTraceId(t ? t.id : null)} />`}
          ${tab === "plan" && html`<${Plan} snap=${snap} graphData=${graphData} />`}
          ${tab === "materials" && html`<${Materials} pid=${pid} tasks=${tasks} nodes=${graphData?.nodes || []}
            onPreview=${(ids) => setPreviewIds(ids)} reload=${() => { loadTasks(); loadSnap(); loadSources(); }} />`}
          ${tab === "sources" && html`<${Sources} pid=${pid} sources=${sources} reload=${() => { loadSources(); loadSnap(); }} />`}
          ${tab === "setup" && html`<${Setup} pid=${pid} snap=${snap} reload=${loadSnap} />`}
        </div>
      </div>
    </div>
    ${addConcept && html`<${AddConceptModal} pid=${pid} onClose=${() => setAddConcept(false)} />`}
  </div>`;
}
