// Tutor chat and the "Tutor's view" debug tab (LEARNING_SIDE_PLAN L2.2, L2.4).
import { html, useState, useEffect, useRef, useCallback } from "/static/vendor/standalone.module.js";
import { project } from "./api.js";
import { toast, clip, ago, guard, Badge } from "./ui.js";
import { stashMath } from "./math.js";

/* tiny, safe markdown: escape first, then **bold**, `code`, lists, paragraphs */
export function md(text) {
  const math = stashMath(text);
  const esc = (s) => s.replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;");
  const inline = (s) => esc(s)
    .replace(/\*\*([^*]+)\*\*/g, "<b>$1</b>")
    .replace(/`([^`]+)`/g, "<code>$1</code>");
  const out = [];
  let list = false;
  for (const line of math.text.split("\n")) {
    const m = line.match(/^\s*[-*]\s+(.*)/) || line.match(/^\s*\d+[.)]\s+(.*)/);
    if (m) { if (!list) { out.push("<ul class='ideas'>"); list = true; } out.push(`<li>${inline(m[1])}</li>`); continue; }
    if (list) { out.push("</ul>"); list = false; }
    if (line.trim()) out.push(`<p>${inline(line)}</p>`);
  }
  if (list) out.push("</ul>");
  return math.restore(out.join(""));
}

const QUICK = [
  ["explain", "Explain this", true], ["why_needed", "Why do I need this?", true],
  ["quiz", "Quiz me", true], ["exercise", "Give me an exercise", true],
  ["next", "What next?", false], ["overview", "Overview", false],
  ["got_it", "⚑ I get it", true], ["lost", "⚐ I'm lost", true],
];
const MOVE_ICON = { review_prerequisite: "↩", use_analogy: "≈", advance: "→", practice: "✎", check: "?" };
const MOVE_VERB = { review_prerequisite: "Review", use_analogy: "Compare with", advance: "Next:", practice: "Try exercise on", check: "Check" };

function Suggestions({ pid, learner, reply, onFocus, onOpenTask }) {
  if (!reply.suggestions?.length) return null;
  const accept = (m) => guard(async () => {
    await project(pid).post("/learn/interactions", { learner_id: learner, kind: "chip_accept", concept_id: m.concept_id,
      meta: { decision_id: reply.decision_id, index: m.rank, move: m.kind } });
    if (m.kind === "practice" && m.task_id) onOpenTask(m.task_id, m.concept_id);
    else if (m.concept_id) onFocus(m.concept_id);
  });
  return html`<div class="chips">${reply.suggestions.map((m) => html`<button class="chip-btn move" title=${m.reason} onClick=${accept(m)}>
    ${MOVE_ICON[m.kind] || "•"} ${MOVE_VERB[m.kind] || m.kind} ${m.concept_name}</button>`)}</div>`;
}

function Message({ m, pid, learner, onFocus, onOpenTask, onHighlight }) {
  if (m.divider) return html`<div class="divider">${m.text}</div>`;
  const r = m.reply;
  return html`<div class="msg ${m.role === "learner" ? "me" : ""} ${r?.check ? "check" : ""}">
    <div dangerouslySetInnerHTML=${{ __html: md(m.text) }}></div>
    ${r && html`<div>
      ${r.check && html`<div class="evid"><b>Check question</b> — answer it in your next message.</div>`}
      <${Suggestions} pid=${pid} learner=${learner} reply=${r} onFocus=${onFocus} onOpenTask=${onOpenTask} />
      ${r.highlight?.length > 1 && onHighlight && html`<button class="sm" title="Light these concepts up on the graph"
        onClick=${() => onHighlight(r.highlight, "Shown by the tutor")}>✨ Show on the map (${r.highlight.length})</button>`}
      ${r.observations?.length > 0 && html`<div class="evid muted">recorded: ${r.observations.map((o) =>
        `${o.target.id}/${o.target.facet} ${o.outcome}`).join(", ")}</div>`}
      <div class="meta">
        ${r.cost.calls ? html`<span>${r.cost.model} · ${r.cost.input_tokens}→${r.cost.output_tokens} tok${r.cost.usd ? ` · $${r.cost.usd.toFixed(4)}` : ""}</span>`
          : r.cost.cached ? html`<span>cached</span>` : html`<span>no model call</span>`}
      </div></div>`}
  </div>`;
}

export function Chat({ pid, learner, session, focus, graph, onFocus, onChanged, onCost, onOpenTask, onHighlight }) {
  const L = project(pid);
  const [items, setItems] = useState([]);
  const [text, setText] = useState("");
  const [busy, setBusy] = useState(false);
  const end = useRef();
  const lastFocus = useRef(null);
  const nameOf = (id) => graph?.nodes.find((n) => n.id === id)?.name || id;

  useEffect(() => {
    if (!session) return;
    setItems(session.turns.map((t) => ({ id: t.id, role: t.role, text: t.text })));
    L.get(`/learn/sessions/${session.session.id}/cost`).then((c) => onCost({ calls: c.calls, usd: c.usd })).catch(() => {});
  }, [session?.session.id]);
  useEffect(() => {
    if (focus && focus !== lastFocus.current && graph) {
      setItems((it) => [...it, { divider: true, id: `d${Date.now()}`, text: `— now looking at ${nameOf(focus)} —` }]);
    }
    lastFocus.current = focus;
  }, [focus, !!graph]);
  useEffect(() => { end.current?.scrollIntoView({ block: "end" }); }, [items.length, busy]);

  const send = useCallback(async (msg, intent = "message") => {
    if (!session || busy) return;
    const label = QUICK.find((q) => q[0] === intent)?.[1] || "";
    setItems((it) => [...it, { id: `l${Date.now()}`, role: "learner", text: msg || label }]);
    setBusy(true);
    try {
      const r = await L.post("/learn/turn", { learner_id: learner, session_id: session.session.id,
        text: msg, intent, focus_concept_id: focus });
      setItems((it) => [...it, { id: r.turn.id, role: "tutor", text: r.turn.text, reply: r }]);
      onCost({ calls: r.session_cost.calls, usd: r.session_cost.usd });
      if (r.highlight?.length > 1) onHighlight?.(r.highlight, "Shown by the tutor");
      if (r.observations.length || r.changed.length) onChanged();
    } catch (e) {
      toast(e.message);
    } finally { setBusy(false); }
  }, [session, busy, focus, learner]);

  const submit = () => { const t = text.trim(); if (!t) return; setText(""); send(t); };
  return html`<div class="chat">
    <div class="thread">
      ${items.length === 0 && html`<div class="empty muted small">Ask anything about ${focus ? nameOf(focus) : "the topic"}, or use a quick action below.
        With nothing selected I talk about the whole topic.</div>`}
      ${items.map((m) => html`<${Message} key=${m.id} m=${m} pid=${pid} learner=${learner} onFocus=${onFocus} onOpenTask=${onOpenTask} onHighlight=${onHighlight} />`)}
      ${busy && html`<div class="msg"><span class="muted">thinking…</span></div>`}
      <div ref=${end}></div>
    </div>
    <div class="quick">${QUICK.map(([k, l, needs]) => html`<button class="sm" key=${k} disabled=${busy || (needs && !focus)}
      title=${needs && !focus ? "Select a concept first" : ""} onClick=${() => send("", k)}>${l}</button>`)}</div>
    <div class="composer">
      <textarea placeholder=${focus ? `Ask about ${nameOf(focus)}…` : "Ask about the topic…"} value=${text}
        onInput=${(e) => setText(e.target.value)}
        onKeyDown=${(e) => { if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); submit(); } }}></textarea>
      <button class="primary" disabled=${busy || !text.trim()} onClick=${submit}>Send</button>
    </div>
  </div>`;
}

/* ------------------------------------------------------------ tutor's view */
export function TutorView({ pid, learner, session }) {
  const L = project(pid);
  const [turns, setTurns] = useState([]);
  const [sel, setSel] = useState(null);
  const [dbg, setDbg] = useState(null);
  useEffect(() => {
    if (!session) return;
    L.get(`/learn/turns?session_id=${session.session.id}`).then((ts) => {
      const tutor = ts.filter((t) => t.role === "tutor");
      setTurns(tutor);
      setSel((s) => s || (tutor.length ? tutor[tutor.length - 1].id : null));
    });
  }, [session?.session.id, session?.turns.length]);
  useEffect(() => { if (sel) L.get(`/learn/turns/${sel}/debug`).then(setDbg); else setDbg(null); }, [sel]);
  if (!session) return html`<div class="empty">No session.</div>`;
  if (turns.length === 0) return html`<div class="empty">Talk to the tutor first; then you can inspect exactly what it was shown and why it answered as it did.</div>`;
  const m = dbg?.meta || {};
  const cost = m.cost || {};
  return html`<div class="dbg">
    <label>Tutor message</label>
    <select value=${sel} onChange=${(e) => setSel(e.target.value)}>
      ${turns.map((t, i) => html`<option value=${t.id}>#${i + 1} ${clip(t.text, 60)}</option>`)}</select>
    ${dbg && html`<div>
      <div class="row wrap" style="margin:8px 0">
        <${Badge}>${cost.calls ? `${cost.model}` : m.closed ? "no model call (closed action)" : "cached"}<//>
        ${cost.input_tokens ? html`<${Badge}>${cost.input_tokens} in / ${cost.output_tokens} out<//>` : null}
        ${cost.usd ? html`<${Badge}>$${cost.usd.toFixed(4)}<//>` : null}
        ${cost.ms ? html`<${Badge}>${cost.ms} ms<//>` : null}
        ${dbg.decision && html`<${Badge} kind="brand">chosen by ${dbg.decision.chosen_by}<//>`}
      </div>
      <div class="small muted">Session budget: ${dbg.budget.session_calls}/${dbg.budget.session_limit} calls ·
        today ${dbg.budget.day_calls}/${dbg.budget.day_limit}</div>
      ${m.sizes && html`<div><h3>Context sections (characters)</h3>
        <div class="row wrap">${Object.entries(m.sizes).map(([k, v]) => html`<${Badge}>${k} ${v}<//>`)}</div></div>`}
      ${dbg.decision && html`<div><h3>Moves the policy offered</h3>
        ${dbg.decision.candidates.map((c, i) => html`<div class="small" style=${i === dbg.decision.chosen_index ? "font-weight:600" : ""}>
          ${i === dbg.decision.chosen_index ? "▶ " : ""}${c.kind} ${c.concept_name || ""} — score ${c.score.toFixed(2)}
          <span class="muted">${Object.entries(c.components || {}).map(([k, v]) => `${k} ${v.toFixed(2)}`).join(", ")}</span></div>`)}</div>`}
      ${dbg.observations.length > 0 && html`<div><h3>Evidence it recorded</h3>
        ${dbg.observations.map((o) => html`<div class="small"><b>${o.target.id}/${o.target.facet}</b> ${o.outcome}
          (weight ${o.weight}, ${o.judge.kind}) “${clip(o.quote || "", 100)}”</div>`)}</div>`}
      ${m.raw && html`<div><h3>Raw structured output</h3><pre>${JSON.stringify(m.raw, null, 2)}</pre></div>`}
      ${m.context && html`<div><h3>Everything the tutor was shown</h3><pre>${m.context}</pre></div>`}
    </div>`}
  </div>`;
}
