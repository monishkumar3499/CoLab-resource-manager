const BASE = "/api";

async function req(path, opts = {}) {
  const res = await fetch(`${BASE}${path}`, {
    headers: { "Content-Type": "application/json" },
    ...opts,
  });
  if (!res.ok) {
    let detail = res.statusText;
    try {
      const j = await res.json();
      detail = j.detail || detail;
    } catch {}
    throw new Error(detail);
  }
  return res.json();
}

export const api = {
  overview: () => req("/overview"),
  pipelineProjects: () => req("/pipeline-projects"),
  people: () => req("/people"),
  projects: () => req("/projects"),
  config: () => req("/config"),
  preview: (pipelineId, weights) =>
    req(`/recommend/${encodeURIComponent(pipelineId)}`, {
      method: "POST",
      body: JSON.stringify(weights ? { weights } : {}),
    }),
  allocate: (pipelineId, weights) =>
    req(`/allocate/${encodeURIComponent(pipelineId)}`, {
      method: "POST",
      body: JSON.stringify(weights ? { weights } : {}),
    }),
  undoAllocate: (pipelineId) =>
    req(`/undo-allocate/${encodeURIComponent(pipelineId)}`, { method: "POST" }),
  runAll: () => req("/run-all", { method: "POST" }),
  generateExcel: () => req("/generate-excel", { method: "POST" }),
  downloadExcelUrl: () => `${BASE}/download-excel`,
  // optional/extended endpoints — backend may need to add these (see backend notes)
  employeeDetail: (employeeId) =>
    req(`/employee/${encodeURIComponent(employeeId)}`),
  projectHealthHistory: (projectId) =>
    req(`/project/${encodeURIComponent(projectId)}/health-history`),
  allocationLogs: (pipelineId) =>
    req(`/allocation-logs/${encodeURIComponent(pipelineId)}`),
};
