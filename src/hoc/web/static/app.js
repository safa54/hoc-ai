import { html, render, useState, useEffect } from "/static/vendor/standalone.module.js";
import { api } from "./api.js";
import { guard, Field, Badge, Toast } from "./ui.js";
import { Studio } from "./studio.js";
import { Learn } from "./learn.js";

/* -------------------------------------------------------------------- home */
function Home({ onOpen }) {
  const [projects, setProjects] = useState(null);
  const [form, setForm] = useState({ title: "", audience: "", description: "", language: "English" });
  const [busy, setBusy] = useState(false);
  const load = () => api.get("/api/projects").then(setProjects);
  useEffect(() => { load(); }, []);
  const create = guard(async () => {
    setBusy(true);
    try { const p = await api.post("/api/projects", form); onOpen("build", p.id); } finally { setBusy(false); }
  });
  const remove = (p) => guard(async (e) => {
    e.stopPropagation();
    if (confirm(`Delete “${p.title}” and everything in it?`)) { await api.del(`/api/projects/${p.id}`); load(); }
  });
  return html`<div class="home">
    <h1 style="font-size:26px">Graph Studio</h1>
    <p class="muted">Describe a topic. A team of agents builds, tests and maintains its knowledge graph while you watch and steer.</p>
    <div class="card" style="margin:18px 0">
      <h2 style="font-size:17px">New topic</h2>
      <${Field} label="Topic"><input placeholder="e.g. GCD and LCM" value=${form.title} onInput=${(e) => setForm({ ...form, title: e.target.value })} /><//>
      <${Field} label="Audience / level"><input placeholder="e.g. middle school and high school" value=${form.audience} onInput=${(e) => setForm({ ...form, audience: e.target.value })} /><//>
      <${Field} label="Scope and aspects to cover (you can add PDFs and notes next)"><textarea
        placeholder="e.g. include listing and prime-factorization methods, the Euclidean algorithm, gcd·lcm = a·b, and word problems"
        value=${form.description} onInput=${(e) => setForm({ ...form, description: e.target.value })}></textarea><//>
      <div class="row" style="margin-top:10px"><div class="grow"></div>
        <button class="primary" disabled=${!form.title.trim() || busy} onClick=${create}>Create topic</button></div>
    </div>
    <h3>Your topics</h3>
    ${projects === null && html`<div class="muted">Loading…</div>`}
    ${projects && projects.length === 0 && html`<div class="empty">No topics yet.</div>`}
    <div class="list">${(projects || []).map((p) => html`<div class="card proj" key=${p.id} onClick=${() => onOpen("build", p.id)}>
      <div class="row"><b class="grow" style="font-size:15px">${p.title}</b>
        <${Badge}>${p.counts.concepts} concepts<//><${Badge}>${p.counts.relations} relations<//>
        ${p.counts.pending > 0 && html`<${Badge} kind="warn">${p.counts.pending} to review<//>`}
        <${Badge} kind=${p.run.status === "running" ? "ok" : ""}>${p.run.status}<//>
        <button class="sm" onClick=${(e) => { e.stopPropagation(); onOpen("learn", p.id); }}>🎓 Learn</button>
        <button class="sm" onClick=${(e) => { e.stopPropagation(); onOpen("build", p.id); }}>🛠 Build</button>
        <button class="sm bad" onClick=${remove(p)}>Delete</button></div>
      <div class="small muted">${p.audience}</div></div>`)}</div>
  </div>`;
}

/* --------------------------------------------------------------------- app */
function parseRoute() {
  const parts = location.hash.replace(/^#\/?/, "").split("/").map(decodeURIComponent).filter(Boolean);
  if (parts.length === 0) return { side: null, pid: null };
  if (parts[0] === "build" || parts[0] === "learn") return { side: parts[0], pid: parts[1] || null };
  return { side: "build", pid: parts[0] }; // old links: #/<topic>
}

function App() {
  const [route, setRoute] = useState(parseRoute());
  useEffect(() => {
    const f = () => setRoute(parseRoute());
    addEventListener("hashchange", f);
    return () => removeEventListener("hashchange", f);
  }, []);
  const open = (side, pid) => { location.hash = side && pid ? `#/${side}/${encodeURIComponent(pid)}` : ""; };
  const home = () => open(null);
  let view;
  if (!route.pid) view = html`<${Home} onOpen=${open} />`;
  else if (route.side === "learn") view = html`<${Learn} key=${"l" + route.pid} pid=${route.pid} onHome=${home} />`;
  else view = html`<${Studio} key=${"b" + route.pid} pid=${route.pid} onHome=${home} />`;
  return html`<${Toast} />${view}`;
}

render(html`<${App} />`, document.getElementById("app"));
