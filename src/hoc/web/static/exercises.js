// Exercises tab: practise a concept; answers become evidence (LEARNING_SIDE_PLAN L7.2).
import { html, useState, useEffect } from "/static/vendor/standalone.module.js";
import { project } from "./api.js";
import { toast, guard, Badge } from "./ui.js";
import { md } from "./chat.js";

const TYPE_LABEL = { mcq: "multiple choice", numeric: "number", short_answer: "short answer",
  explain_why: "explain", compare: "compare", transfer_problem: "new situation", worked_problem: "worked problem" };
const STATUS_BADGE = { correct: "ok", attempted: "warn", new: "" };

export function Exercises({ pid, learner, focus, graph, session, openTask, onChanged, onFocus, onCost }) {
  const L = project(pid);
  const [list, setList] = useState(null);
  const [cur, setCur] = useState(null); // {task, attempt, concepts}
  const [answer, setAnswer] = useState("");
  const [result, setResult] = useState(null);
  const [hints, setHints] = useState([]);
  const [solution, setSolution] = useState(null);
  const [busy, setBusy] = useState(false);
  const nameOf = (id) => graph?.nodes.find((n) => n.id === id)?.name || id;

  const loadList = () => (focus
    ? L.get(`/learn/tasks?concept_id=${encodeURIComponent(focus)}&learner_id=${learner}`).then(setList)
    : Promise.resolve(setList(null)));
  const open = guard(async (taskId) => {
    const r = await L.post(`/learn/tasks/${taskId}/open`, { learner_id: learner });
    setCur(r); setAnswer(""); setResult(null); setHints([]); setSolution(null);
  });
  useEffect(() => { setCur(null); loadList(); }, [focus, learner]);
  useEffect(() => { if (openTask) open(openTask); }, [openTask]);

  const check = guard(async () => {
    setBusy(true);
    try {
      const r = await L.post(`/learn/attempts/${cur.attempt.id}/answer`,
        { learner_id: learner, answer, session_id: session?.session.id });
      setResult(r); onChanged(); loadList();
      if (session) L.get(`/learn/sessions/${session.session.id}/cost`).then((c) => onCost({ calls: c.calls, usd: c.usd }));
    } finally { setBusy(false); }
  });
  const hint = guard(async () => {
    setBusy(true);
    try {
      const r = await L.post(`/learn/attempts/${cur.attempt.id}/hint`,
        { learner_id: learner, session_id: session.session.id, focus_concept_id: focus });
      setHints((h) => [...h, r.turn.text]);
      onCost({ calls: r.session_cost.calls, usd: r.session_cost.usd });
    } finally { setBusy(false); }
  });
  const reveal = guard(async () => {
    if (!confirm("Show the solution? This counts as a weak signal that you could not solve it yourself, and no further evidence is recorded for this attempt.")) return;
    const r = await L.post(`/learn/attempts/${cur.attempt.id}/reveal`, { learner_id: learner });
    setSolution(r.solution); onChanged();
  });

  if (!cur) {
    if (!focus) return html`<div class="empty">Select a concept in the graph to see its exercises.</div>`;
    if (!list) return html`<div class="empty">Loading…</div>`;
    return html`<div style="padding:12px"><h3 style="margin-top:0">Exercises for ${nameOf(focus)}</h3>
      ${list.length === 0 ? html`<div class="empty">No exercises are linked to this concept yet.<br/>
        Upload an exercise sheet in <b>Build → Materials</b> and the agents will match them.</div>` : null}
      <div class="list">${list.map((t) => html`<div class="card" key=${t.id}>
        <div class="row wrap"><b class="grow">${t.title}</b><${Badge}>${TYPE_LABEL[t.task_type] || t.task_type}<//>
          <${Badge} kind=${STATUS_BADGE[t.status]}>${t.status === "correct" ? "solved" : t.status === "attempted" ? "tried" : "new"}<//></div>
        <div class="small" style="margin:5px 0">${t.prompt.slice(0, 160)}${t.prompt.length > 160 ? "…" : ""}</div>
        <button class="primary sm" onClick=${() => open(t.id)}>${t.status === "new" ? "Start" : "Try again"}</button></div>`)}</div></div>`;
  }

  const t = cur.task;
  const solved = result?.solved;
  const input = t.task_type === "mcq"
    ? html`<div class="list">${t.options.map((o) => html`<label class="row" style="margin:0;color:var(--ink);cursor:pointer">
        <input type="radio" name="opt" style="width:auto" checked=${answer === o} onChange=${() => setAnswer(o)} /> ${o}</label>`)}</div>`
    : t.task_type === "numeric"
      ? html`<input placeholder="Your answer (a number)" value=${answer} onInput=${(e) => setAnswer(e.target.value)}
          onKeyDown=${(e) => { if (e.key === "Enter" && answer.trim()) check(); }} />`
      : html`<textarea placeholder="Write your answer and reasoning…" style="min-height:110px" value=${answer}
          onInput=${(e) => setAnswer(e.target.value)}></textarea>`;
  return html`<div style="padding:12px">
    <div class="row"><button class="ghost sm" onClick=${() => { setCur(null); loadList(); }}>← Exercises</button>
      <div class="grow"></div><${Badge}>${TYPE_LABEL[t.task_type] || t.task_type}<//></div>
    <h2 style="margin:6px 0">${t.title}</h2>
    <div class="row wrap">${cur.concepts.map((c) => html`<button class="chip-btn" onClick=${() => onFocus(c.id)} title="Open this concept">${c.name}</button>`)}</div>
    <div class="task-body" style="margin:10px 0" dangerouslySetInnerHTML=${{ __html: md(t.prompt) }}></div>
    ${!solved && !solution && html`<div>${input}
      <div class="row wrap" style="margin-top:8px">
        <button class="primary" disabled=${busy || !answer.trim()} onClick=${check}>${busy ? "Checking…" : "Check"}</button>
        <button disabled=${busy || !session} onClick=${hint} title="Asks the tutor for a nudge (counts one model call and discounts the evidence)">Hint${hints.length ? ` (${hints.length})` : ""}</button>
        <button class="ghost" onClick=${reveal}>Show solution</button></div></div>`}
    ${hints.map((h, i) => html`<div class="msg" style="margin-top:8px"><b>Hint ${i + 1}</b><div dangerouslySetInnerHTML=${{ __html: md(h) }}></div></div>`)}
    ${result && html`<div class="msg ${solved ? "" : "check"}" style="margin-top:10px">
      <b>${result.correct === null ? "Couldn't read that" : solved ? "✓ Correct" : "Not yet"}</b>
      <div dangerouslySetInnerHTML=${{ __html: md(result.feedback) }}></div>
      ${result.recorded.length > 0 && html`<div class="evid muted">recorded (${result.graded_by === "exact" ? "exact match, no model call" : "graded by the model"}):
        ${result.recorded.map((o) => `${nameOf(o.target.id)}/${o.target.facet} ${o.outcome}`).join(", ")}${hints.length ? ` · weighted down for ${hints.length} hint(s)` : ""}</div>`}
      ${result.evidence_skipped && html`<div class="evid muted">No evidence recorded: you had already seen the solution.</div>`}
      ${result.changed.length > 0 && html`<div class="evid"><b>Changed:</b> ${result.changed.map(nameOf).join(", ")}</div>`}
    </div>`}
    ${solution && html`<div class="msg" style="margin-top:10px"><b>Solution</b><div class="task-body">${solution}</div></div>`}
    ${(solved || solution) && html`<div class="row" style="margin-top:10px">
      <button class="primary" onClick=${() => { setCur(null); loadList(); }}>More exercises</button></div>`}
  </div>`;
}
