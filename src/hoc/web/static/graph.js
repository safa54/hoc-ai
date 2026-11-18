// Cytoscape controller: incremental updates, change animations, highlights, traces.
const cytoscape = window.cytoscape;
if (window.cytoscapeDagre) cytoscape.use(window.cytoscapeDagre);

export const PALETTE = [
  "#cfe3ff", "#d9f2d0", "#ffe3c2", "#ead7ff", "#ffd6dc",
  "#c9f0ee", "#fff1b8", "#e2e5ea", "#d7e8c6", "#f6d5f0",
];
export const EDGE_TYPES = {
  prerequisite_of: { label: "prerequisite", color: "#334155" },
  specializes: { label: "specializes", color: "#2563eb" },
  part_of: { label: "part of", color: "#7c3aed" },
  contrasts_with: { label: "contrasts", color: "#dc2626" },
  analogous_to: { label: "analogous", color: "#059669" },
  related: { label: "related", color: "#94a3b8" },
};
const HIERARCHICAL = new Set(["prerequisite_of", "part_of", "specializes"]);
export const GLOW = { path: "#38bdf8", pick: "#c084fc" };
const MARKS = { onpath: false, pathnext: false, glow: false, faded: false };

function style() {
  const edgeColor = (t) => EDGE_TYPES[t]?.color || "#94a3b8";
  return [
    {
      selector: "node",
      style: {
        label: "data(label)",
        "font-size": 11,
        "font-family": "Inter, system-ui, sans-serif",
        "text-wrap": "wrap",
        "text-max-width": 110,
        "text-valign": "center",
        "text-halign": "center",
        width: "label",
        height: "label",
        padding: 10,
        shape: "round-rectangle",
        "background-color": "data(color)",
        "border-width": 1.5,
        "border-color": "#64748b",
        "border-style": "dashed",
        color: "#0f172a",
        "transition-property": "background-color, border-color, border-width, opacity",
        "transition-duration": "400ms",
      },
    },
    // learning side: ready-next glow, draft dashed outline, super-nodes
    { selector: "node[?ready]", style: { "underlay-color": "#22c55e", "underlay-opacity": 0.35, "underlay-padding": 7 } },
    { selector: "node[?draft]", style: { "border-style": "dashed" } },
    { selector: "node[?group]", style: { shape: "round-rectangle", "border-width": 5, "border-style": "double",
        "font-weight": 700, padding: 34, "text-max-width": 200, "font-size": 16, "text-wrap": "wrap",
        "min-width": 150, "min-height": 70 } },
    { selector: "node[?group][gborder]", style: { "border-color": "data(gborder)" } },
    { selector: "edge[?agg]", style: { width: "mapData(count, 1, 14, 2, 8)", "curve-style": "bezier",
        label: "data(count)", "font-size": 9, color: "#94a3b8", "text-background-color": "#0b1020",
        "text-background-opacity": 0.7, "text-background-padding": 2 } },
    // area frames: an expanded area drawn as a box around its concepts (compound parent)
    { selector: "node[?frame]", style: { shape: "round-rectangle", "background-color": "data(color)",
        "background-opacity": 0.13, "border-width": 2, "border-style": "solid", "border-color": "data(fborder)",
        label: "data(label)", "text-valign": "top", "text-halign": "center", "text-margin-y": -4,
        "font-size": 13, "font-weight": 600, color: "#cbd5e1", "text-max-width": 400, padding: 18,
        "text-background-color": "#0b1020", "text-background-opacity": 0.6, "text-background-padding": 3,
        "text-background-shape": "round-rectangle" } },
    { selector: "node[?frame]:active", style: { "overlay-opacity": 0.06 } },
    // views: concepts added only to connect a view, and faded context around it
    { selector: "node[?bridge]", style: { "border-style": "dotted", "border-width": 2.5, "border-color": "#94a3b8", opacity: 0.8 } },
    { selector: "node[?vfocus]", style: { "border-width": 4, "border-color": "#f8fafc" } },
    // glow: a path or a chosen subgraph lit up inside the whole graph (nothing is hidden)
    { selector: "node[?onpath]", style: { "underlay-color": GLOW.path, "underlay-opacity": 0.45, "underlay-padding": 9,
        "border-color": GLOW.path, "border-width": 3, "border-style": "solid" } },
    { selector: "node[?pathnext]", style: { "underlay-opacity": 0.7, "underlay-padding": 12, "border-width": 5 } },
    { selector: "node[?glow]", style: { "underlay-color": GLOW.pick, "underlay-opacity": 0.5, "underlay-padding": 9,
        "border-color": GLOW.pick, "border-width": 3, "border-style": "solid" } },
    { selector: "node.pulse", style: { "underlay-opacity": 0.95, "underlay-padding": 20 } },
    { selector: "node[?faded]", style: { opacity: 0.3 } },
    { selector: "edge[?faded]", style: { opacity: 0.15 } },
    { selector: "node[?ctx]", style: { opacity: 0.38 } },
    { selector: "edge[?ctx]", style: { opacity: 0.25 } },
    { selector: "node[kind = 'principle']", style: { shape: "round-hexagon", padding: 14 } },
    { selector: "node[kind = 'procedure']", style: { shape: "round-tag", padding: 12 } },
    { selector: "node[status = 'accepted']", style: { "border-style": "solid" } },
    { selector: "node[?locked]", style: { "border-width": 3, "border-color": "#0f172a" } },
    {
      selector: "edge",
      style: {
        width: 1.6,
        "curve-style": "bezier",
        "line-color": (e) => edgeColor(e.data("type")),
        "target-arrow-color": (e) => edgeColor(e.data("type")),
        "target-arrow-shape": "triangle",
        "arrow-scale": 0.9,
        opacity: 0.85,
        "transition-property": "line-color, width, opacity",
        "transition-duration": "400ms",
      },
    },
    { selector: "edge[necessity = 'supportive']", style: { "line-style": "dashed" } },
    {
      selector: "edge[type = 'contrasts_with'], edge[type = 'analogous_to'], edge[type = 'related']",
      style: { "target-arrow-shape": "none", "line-style": "dotted", width: 1.4 },
    },
    { selector: "edge[?locked]", style: { width: 2.6 } },
    { selector: "edge[?onpath]", style: { "line-color": GLOW.path, "target-arrow-color": GLOW.path, width: 4.5,
        opacity: 1, "underlay-color": GLOW.path, "underlay-opacity": 0.3, "underlay-padding": 4, "z-index": 5 } },
    { selector: "edge[?glow]", style: { "line-color": GLOW.pick, "target-arrow-color": GLOW.pick, width: 4.5,
        opacity: 1, "underlay-color": GLOW.pick, "underlay-opacity": 0.3, "underlay-padding": 4, "z-index": 5 } },
    { selector: ".hidden", style: { display: "none" } },
    { selector: ".dim", style: { opacity: 0.12 } },
    {
      selector: "node.added",
      style: { "border-color": "#16a34a", "border-width": 5, "border-style": "solid",
               "background-color": "#bbf7d0" },
    },
    {
      selector: "node.updated",
      style: { "border-color": "#d97706", "border-width": 5, "border-style": "solid" },
    },
    { selector: "edge.added", style: { "line-color": "#16a34a", "target-arrow-color": "#16a34a", width: 4 } },
    { selector: "edge.updated", style: { "line-color": "#d97706", "target-arrow-color": "#d97706", width: 4 } },
    {
      selector: ".removing",
      style: { "background-color": "#fecaca", "border-color": "#dc2626", "line-color": "#dc2626",
               "target-arrow-color": "#dc2626", opacity: 0.25, "border-style": "solid" },
    },
    {
      selector: "node.focus",
      style: { "border-color": "#f59e0b", "border-width": 5, "border-style": "solid",
               "underlay-color": "#f59e0b", "underlay-opacity": 0.25, "underlay-padding": 6 },
    },
    { selector: "edge.focus", style: { "line-color": "#f59e0b", "target-arrow-color": "#f59e0b", width: 4.5 } },
    {
      selector: "node.trace",
      style: { "border-color": "#2563eb", "border-width": 5, "border-style": "solid",
               "underlay-color": "#2563eb", "underlay-opacity": 0.18, "underlay-padding": 6 },
    },
    {
      selector: "edge.missing",
      style: { "line-color": "#dc2626", "target-arrow-color": "#dc2626", "line-style": "dashed",
               width: 3, label: "missing?", "font-size": 9, color: "#dc2626" },
    },
    { selector: "node.preview", style: { "border-color": "#a855f7", "border-width": 5, "border-style": "solid" } },
    { selector: "edge.preview", style: { "line-color": "#a855f7", "target-arrow-color": "#a855f7", width: 4 } },
  ];
}

export class GraphController {
  constructor(container, { onSelect } = {}) {
    this.areaIndex = new Map();
    this.colorFn = null; // learning side: colour by belief status instead of area/kind
    this.hiddenTypes = new Set();
    this.colorBy = "area";
    this.direction = "LR";
    this.container = container;
    this.cy = cytoscape({
      container,
      style: style(),
      wheelSensitivity: 0.25,
      minZoom: 0.08,
      maxZoom: 3,
      boxSelectionEnabled: false,
    });
    this.cy.on("tap", "node", (e) => {
      const d = e.target.data();
      if (d.group || d.frame) onSelect?.({ kind: "group", id: d.area, collapse: !!d.frame });
      else onSelect?.({ kind: "concept", id: e.target.id() });
    });
    this.cy.on("tap", "edge", (e) => {
      if (!e.target.hasClass("missing") && !e.target.data("agg")) {
        onSelect?.({ kind: "relation", id: e.target.id() });
      }
    });
    this.cy.on("tap", (e) => { if (e.target === this.cy) onSelect?.(null); });
    this._timers = new Set();
    // Follow the growing graph until the user pans or zooms by hand.
    this.userMoved = false;
    const moved = () => { this.userMoved = true; };
    for (const ev of ["wheel", "mousedown", "touchstart"]) {
      container.addEventListener(ev, moved, { passive: true });
    }
    // the next step of a path breathes, so the eye finds it at once
    this._pulse = setInterval(() => {
      if (this.cy.destroyed()) return;
      const next = this.cy.nodes("[?pathnext]");
      this._beat = !this._beat;
      this.cy.nodes(".pulse").not(next).removeClass("pulse");
      next.toggleClass("pulse", this._beat);
    }, 850);
  }

  destroy() {
    clearInterval(this._pulse);
    this._timers.forEach(clearTimeout);
    this.cy.destroy();
  }

  _later(fn, ms) {
    const t = setTimeout(() => { this._timers.delete(t); fn(); }, ms);
    this._timers.add(t);
  }

  _areaColor(area) {
    const i = this.areaIndex.get(area);
    return i === undefined ? "#eef2f7" : PALETTE[i % PALETTE.length];
  }

  _color(n) {
    if (n.frame) return this._areaColor(n.area);
    if (this.colorFn) return this.colorFn(n);
    if (this.colorBy === "status") {
      return n.locked ? "#e2e8f0" : n.status === "accepted" ? "#dbeafe" : "#fef9c3";
    }
    if (this.colorBy === "kind") {
      return { concept: "#dbeafe", principle: "#ede9fe", procedure: "#dcfce7" }[n.kind] || "#e5e7eb";
    }
    const i = this.areaIndex.get(n.area);
    return i === undefined ? "#eef2f7" : PALETTE[i % PALETTE.length];
  }

  _nodeData(n) {
    // view flags default to off so a node leaving a view loses its marks
    // (an area id is reused when a box collapses into a super-node and back)
    const d = { bridge: false, vfocus: false, ctx: false, group: false, frame: false, ...MARKS,
      ...n, label: n.label ?? n.name, color: this._color(n) };
    if (n.frame) d.fborder = this._areaColor(n.area);
    return d;
  }

  setColorBy(mode) {
    this.colorBy = mode;
    this.cy.nodes().forEach((el) => el.data("color", this._color(el.data())));
  }

  setHiddenTypes(types) {
    this.hiddenTypes = new Set(types);
    this.cy.edges().forEach((e) => e.toggleClass("hidden", this.hiddenTypes.has(e.data("type"))));
  }

  // Replace the graph with `data`, animating what changed.
  // diff: [{kind, id, change}] from the server (optional); animate: bool.
  setData(data, { diff = [], animate = false, fit = false } = {}) {
    const cy = this.cy;
    (data.areas || []).forEach((a, i) => this.areaIndex.set(a.id, i));
    const nodes = new Map(data.nodes.map((n) => [n.id, n]));
    const edges = new Map(data.edges.map((e) => [e.id, e]));
    const removed = [];
    const added = [];
    const first = cy.nodes().length === 0;
    // elements about to be removed or re-parented must not be mid-transition (Cytoscape
    // would restyle an element that is no longer in the graph)
    cy.nodes(".pulse").removeClass("pulse");
    cy.elements().stop(true, true);
    cy.batch(() => {
      cy.edges().forEach((e) => { if (!edges.has(e.id())) removed.push(e); });
      cy.nodes().forEach((n) => { if (!nodes.has(n.id())) removed.push(n); });
      const ext = cy.extent();
      // frames (compound parents) must exist before their children are added or moved in
      const ordered = [...data.nodes].sort((a, b) => (b.frame ? 1 : 0) - (a.frame ? 1 : 0));
      for (const n of ordered) {
        const el = cy.getElementById(n.id);
        if (el.nonempty()) {
          const { parent, ...rest } = this._nodeData(n);
          el.data(rest);
          const want = parent && nodes.has(parent) ? parent : null;
          const have = el.isChild() ? el.parent().id() : null;
          if (want !== have) el.move({ parent: want });
        } else {
          const d = this._nodeData(n);
          if (d.parent && !nodes.has(d.parent)) delete d.parent;
          added.push(cy.add({ group: "nodes", data: d,
            position: { x: (ext.x1 + ext.x2) / 2, y: (ext.y1 + ext.y2) / 2 } }));
        }
      }
      // a removed frame must not take its (still shown) children with it
      for (const el of removed) {
        if (el.isNode() && el.isParent()) el.children().filter((c) => nodes.has(c.id())).move({ parent: null });
      }
      for (const e of data.edges) {
        const el = cy.getElementById(e.id);
        if (el.nonempty()) el.data({ ctx: false, ...MARKS, ...e });
        else if (cy.getElementById(e.source).nonempty() && cy.getElementById(e.target).nonempty()) {
          const added_e = cy.add({ group: "edges", data: e });
          added_e.toggleClass("hidden", this.hiddenTypes.has(e.type));
          added.push(added_e);
        }
      }
      // start new nodes next to a neighbour so the layout animation reads as "growing"
      for (const el of added) {
        if (!el.isNode()) continue;
        const nb = el.neighborhood("node").filter((x) => !added.includes(x));
        if (nb.nonempty()) {
          const p = nb[0].position();
          el.position({ x: p.x + 40, y: p.y + 30 });
        }
      }
    });

    if (animate && !first) {
      removed.forEach((el) => el.addClass("removing"));
      this._later(() => cy.remove(cy.collection(removed).filter((x) => x.inside())), 1400);
      const updated = diff.filter((d) => d.change === "updated").map((d) => d.id);
      const mark = (els, cls) => {
        els.forEach((el) => el.addClass(cls));
        this._later(() => els.forEach((el) => el.inside() && el.removeClass(cls)), 7000);
      };
      mark(added, "added");
      mark(updated.map((id) => cy.getElementById(id)).filter((x) => x.nonempty()), "updated");
    } else {
      cy.remove(cy.collection(removed));
    }
    if (first || added.length || removed.length) this.layout(animate && !first, fit || first);
  }

  layout(animate = true, fit = false) {
    const cy = this.cy;
    // with some areas open and others collapsed, the bundled edges to the super-nodes would
    // stretch an open area across the whole canvas: lay it out by its own edges only
    const mixed = cy.nodes("[?group]").nonempty() && cy.nodes("[?frame]").nonempty();
    const eles = cy.elements().filter(
      (e) => e.isNode() || (HIERARCHICAL.has(e.data("type")) && !e.hasClass("removing") && !(mixed && e.data("agg"))),
    );
    const opts = window.cytoscapeDagre
      ? { name: "dagre", rankDir: this.direction, nodeSep: 18, rankSep: 70, edgeSep: 8,
          ...(cy.nodes("[?frame]").nonempty() ? { nodeSep: 26, rankSep: 80 } : {}) }
      : { name: "breadthfirst", directed: true };
    if (fit) this.userMoved = false;
    // overview (mostly super-nodes): a grid in learning order keeps them big and readable;
    // a layered layout of ten wide boxes would shrink them to fit one long row
    const groups = cy.nodes("[?group]");
    if (groups.length >= 2 && cy.nodes("[?frame]").empty() && groups.length >= cy.nodes().length * 0.6) {
      const n = cy.nodes().length;
      const order = (el) => (el.data("group") ? this.areaIndex.get(el.data("area")) ?? 998 : 999);
      const cols = Math.max(2, Math.round(Math.sqrt((n * cy.width()) / Math.max(cy.height() - 150, 200) / 1.5)));
      this._run(cy.layout({ name: "grid", cols, avoidOverlap: true, avoidOverlapPadding: 70, condense: true,
        sort: (a, b) => order(a) - order(b), animate, animationDuration: 600, fit: false }), animate);
      return;
    }
    this._run(eles.layout({ ...opts, animate, animationDuration: 600, fit: false }), animate);
  }

  _run(layout, animate) {
    const follow = !this.userMoved;
    layout.one("layoutstop", () => {
      const area = this._openArea;
      this._openArea = null;
      const inside = area ? this.cy.nodes().filter((n) => n.data("area") === area && !n.data("group")) : null;
      if (inside && inside.nonempty()) this._frame(inside, true, 50);  // show what was just opened
      else if (follow) this._frame(null, animate);
    });
    layout.run();
  }

  /** Call before the data with a newly opened area arrives: the camera then moves to it. */
  openArea(areaId) { this._openArea = areaId; }

  // Fit `eles` (default: everything) into the part of the canvas the toolbar and the legend
  // leave free, so nothing ends up underneath them.
  _frame(eles, animate = true, padding = 30) {
    const cy = this.cy;
    const bb = (eles && eles.nonempty() ? eles : cy.elements(":visible")).boundingBox();
    if (!bb.w || !bb.h) return;
    const wrap = this.container.parentElement;
    const inset = (sel) => { const el = wrap?.querySelector(sel); return el ? el.offsetHeight + 14 : 0; };
    const top = inset(".toolbar"), bottom = inset(".legend");
    const W = cy.width(), H = Math.max(cy.height() - top - bottom, 120);
    const zoom = Math.max(cy.minZoom(), Math.min((W - 2 * padding) / bb.w, (H - 2 * padding) / bb.h, 1.5));
    const pan = { x: (W - zoom * (bb.x1 + bb.x2)) / 2, y: top + (H - zoom * (bb.y1 + bb.y2)) / 2 };
    if (animate) cy.animate({ zoom, pan }, { duration: 400 });
    else cy.viewport({ zoom, pan });
  }

  fit() {
    this.userMoved = false;
    this._frame(null);
  }

  setDirection(dir) { this.direction = dir; this.layout(true, true); }

  clear(cls) { this.cy.elements(`.${cls}`).removeClass(cls); }

  focus(sel) {
    this.clear("focus");
    if (!sel) return;
    const el = this.cy.getElementById(sel.id);
    if (el.empty()) return;
    el.addClass("focus");
    if (!el.visible() || !this._inView(el)) this.cy.animate({ center: { eles: el } }, { duration: 300 });
  }

  _inView(el) {
    const bb = el.renderedBoundingBox();
    return bb.x1 > 0 && bb.y1 > 0 && bb.x2 < this.cy.width() && bb.y2 < this.cy.height();
  }

  // Highlight a set of element ids with a class; dims everything else if `dim`.
  highlight(ids, cls = "preview", { dim = false } = {}) {
    this.clear(cls);
    this.cy.elements().removeClass("dim");
    const set = this.cy.collection();
    ids.forEach((id) => { const el = this.cy.getElementById(id); if (el.nonempty()) set.merge(el); });
    set.addClass(cls);
    if (dim && set.nonempty()) {
      const keep = set.union(set.connectedEdges().filter((e) => set.contains(e.source()) && set.contains(e.target())));
      this.cy.elements().not(keep).addClass("dim");
    }
    return set;
  }

  showTrace(trace) {
    this.clearTrace();
    if (!trace) return;
    const ids = [...new Set(trace.steps.flatMap((s) => s.concept_ids))];
    const set = this.highlight(ids, "trace", { dim: true });
    for (const m of trace.missing_edges || []) {
      if (this.cy.getElementById(m.source).empty() || this.cy.getElementById(m.target).empty()) continue;
      this.cy.add({ group: "edges", classes: "missing",
        data: { id: `missing:${m.source}->${m.target}`, source: m.source, target: m.target, type: "missing" } });
    }
    if (set.nonempty()) this._frame(set, true, 60);
  }

  clearTrace() {
    this.cy.remove(".missing");
    this.clear("trace");
    this.cy.elements().removeClass("dim");
  }
}


// ------------------------------------------------------------------ super-nodes
// A *view* over the areas the planner already assigned (topic ids): not graph semantics
// (LEARNING_SIDE_PLAN L8). A collapsed area becomes one ordinary node, so dagre keeps working.
export function groupView(data, expanded, { groupNode, frames = false } = {}) {
  const areas = new Map((data.areas || []).map((a) => [a.id, a]));
  const collapsed = (n) => n.area && areas.has(n.area) && !expanded.has(n.area);
  const members = new Map();
  const nodes = [];
  for (const n of data.nodes) {
    if (collapsed(n)) {
      if (!members.has(n.area)) members.set(n.area, []);
      members.get(n.area).push(n);
    } else nodes.push(n);
  }
  const groupId = (a) => `area:${a}`;
  const make = groupNode || ((area, list) => ({
    id: groupId(area.id), name: `${area.title} (${list.length})`, group: true, area: area.id,
    kind: "concept", status: "unknown",
  }));
  const any = (list, k) => list.some((m) => m[k]);
  for (const [aid, list] of members) {
    nodes.push({ ...make(areas.get(aid), list), id: groupId(aid), group: true, area: aid,
      onpath: any(list, "onpath"), pathnext: any(list, "pathnext"), glow: any(list, "glow"),
      faded: list.every((m) => m.faded) });
  }
  if (frames) {  // an expanded area is drawn as a box (compound parent) around its concepts
    const open = new Map();
    for (const n of nodes) {
      if (!n.group && n.area && areas.has(n.area)) open.set(n.area, (open.get(n.area) || 0) + 1);
    }
    for (const [aid, count] of open) {
      nodes.push({ id: groupId(aid), frame: true, area: aid, name: `▾ ${areas.get(aid).title} (${count})`, kind: "frame" });
    }
    for (let i = 0; i < nodes.length; i++) {
      const n = nodes[i];
      if (!n.group && !n.frame && open.has(n.area)) nodes[i] = { ...n, parent: groupId(n.area) };
    }
  }
  const viewId = new Map(data.nodes.map((n) => [n.id, collapsed(n) ? groupId(n.area) : n.id]));
  const agg = new Map();
  const edges = [];
  for (const e of data.edges) {
    const s = viewId.get(e.source), t = viewId.get(e.target);
    if (!s || !t || s === t) continue;
    if (s === e.source && t === e.target) { edges.push(e); continue; }
    const key = `agg:${s}->${t}:${e.type}`;
    const cur = agg.get(key);
    if (cur) { cur.count += 1; cur.onpath ||= !!e.onpath; cur.glow ||= !!e.glow; cur.faded &&= !!e.faded; }
    else agg.set(key, { id: key, source: s, target: t, type: e.type, necessity: null, agg: true, count: 1,
      onpath: !!e.onpath, glow: !!e.glow, faded: !!e.faded });
  }
  return { ...data, nodes, edges: [...edges, ...agg.values()] };
}


// ------------------------------------------------------------------------- glow
// Light up a set of concepts inside the whole graph: nothing is hidden, the rest is faded.
// `flag` is "glow" (a chosen subgraph) or "onpath" (a learning path); `next` breathes.
// Edges light up when both ends do. `order` (ids) numbers the node labels: ① ② ③ …
const DIGITS = "①②③④⑤⑥⑦⑧⑨⑩⑪⑫⑬⑭⑮⑯⑰⑱⑲⑳";
export function glowView(data, ids, { flag = "glow", next = null, fade = true, order = [], edgeTypes = null } = {}) {
  const set = new Set(ids);
  if (!data || set.size === 0) return data;
  const no = new Map(order.map((id, i) => [id, i]));
  const nodes = data.nodes.map((n) => {
    if (!set.has(n.id)) return fade ? { ...n, faded: true } : n;
    const mark = no.has(n.id) && no.get(n.id) < DIGITS.length ? `${DIGITS[no.get(n.id)]} ` : "";
    return { ...n, [flag]: true, pathnext: n.id === next, stepMark: mark };
  });
  const edges = data.edges.map((e) => {
    const lit = set.has(e.source) && set.has(e.target) && (!edgeTypes || edgeTypes.includes(e.type));
    return lit ? { ...e, [flag]: true } : fade ? { ...e, faded: true } : e;
  });
  return { ...data, nodes, edges };
}
