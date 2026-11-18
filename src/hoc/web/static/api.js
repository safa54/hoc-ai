// Thin JSON client for the hoc-ai API.
async function request(path, { method = "GET", body } = {}) {
  const isForm = body instanceof FormData;
  const res = await fetch(path, {
    method,
    headers: body && !isForm ? { "Content-Type": "application/json" } : {},
    body: body === undefined ? undefined : isForm ? body : JSON.stringify(body),
  });
  if (!res.ok) {
    let detail = res.statusText;
    try {
      const j = await res.json();
      detail = typeof j.detail === "string" ? j.detail : JSON.stringify(j.detail);
    } catch (_) {}
    throw new Error(detail);
  }
  return res.json();
}

export const api = {
  get: (p) => request(p),
  post: (p, body = {}) => request(p, { method: "POST", body }),
  put: (p, body = {}) => request(p, { method: "PUT", body }),
  del: (p) => request(p, { method: "DELETE" }),
};

export function project(pid) {
  const base = `/api/projects/${encodeURIComponent(pid)}`;
  return {
    base,
    get: (p = "") => api.get(base + p),
    post: (p, b) => api.post(base + p, b),
    put: (p, b) => api.put(base + p, b),
    del: (p) => api.del(base + p),
  };
}
