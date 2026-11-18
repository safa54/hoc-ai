// LaTeX in card text and chat: $...$ and \(...\) inline, $$...$$ and \[...\] display (KaTeX, vendored).
import { html } from "/static/vendor/standalone.module.js";

const esc = (s) => s.replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;");
// "$5 and $6" must stay text: a single $ needs a non-space after it and no digit after the closer.
const MATH = /\$\$([\s\S]+?)\$\$|\\\[([\s\S]+?)\\\]|\\\(([\s\S]+?)\\\)|\$(?!\s)([^$\n]+?)(?<!\s)\$(?!\d)/g;

function render(src, display) {
  const k = window.katex;
  if (!k) return `<code>${esc(src)}</code>`;
  try {
    return k.renderToString(src.trim(), { displayMode: display, throwOnError: false, strict: "ignore", trust: false });
  } catch {
    return `<code>${esc(src)}</code>`;
  }
}

/** Swap every math span for a placeholder; `restore` puts rendered math back into escaped/markdown HTML. */
export function stashMath(text) {
  const parts = [];
  const stripped = String(text ?? "").replace(MATH, (_m, a, b, c, d) => {
    const display = a !== undefined || b !== undefined;
    parts.push(render(a ?? b ?? c ?? d, display));
    return `\u0000${parts.length - 1}\u0000`;
  });
  return { text: stripped, restore: (s) => s.replace(/\u0000(\d+)\u0000/g, (_m, i) => parts[+i]) };
}

export function texHtml(text) {
  const s = stashMath(text);
  return s.restore(esc(s.text));
}

/** Plain text with LaTeX rendered. Use for card fields (definition, key ideas, examples...). */
export function Tex({ text, tag = "span" }) {
  return html`<${tag} class="tex" dangerouslySetInnerHTML=${{ __html: texHtml(text) }}></${tag}>`;
}

/** A formal statement: the whole string is math unless it already carries delimiters. */
export function Formal({ text }) {
  const t = String(text ?? "");
  const hasDelims = /\$|\\\(|\\\[/.test(t);
  return html`<div class="tex formal" dangerouslySetInnerHTML=${{ __html: hasDelims ? texHtml(t) : render(t, true) }}></div>`;
}
