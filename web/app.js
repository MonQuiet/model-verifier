const state = {
  catalog: null,
  selectedProviders: new Set(),
  selectedCases: new Set(),
  activeRunId: null,
  pollTimer: null,
};

const providersEl = document.getElementById("providers");
const casesEl = document.getElementById("cases");
const runsEl = document.getElementById("runs");
const detailEl = document.getElementById("detail");
const detailSubtitleEl = document.getElementById("detail-subtitle");
const runStatusEl = document.getElementById("run-status");
const heroRunCountEl = document.getElementById("hero-run-count");
const runButtonEl = document.getElementById("run-button");

document.getElementById("run-form").addEventListener("submit", onRunSubmit);
document.querySelectorAll("[data-toggle]").forEach((button) => {
  button.addEventListener("click", () => toggleSelection(button.dataset.toggle));
});

boot().catch((error) => {
  runStatusEl.textContent = error.message;
});

async function boot() {
  await loadCatalog();
  await loadRuns();
}

async function loadCatalog() {
  const response = await fetch("/api/config");
  const payload = await response.json();
  state.catalog = payload;

  payload.providers.forEach((provider) => state.selectedProviders.add(provider.name));
  payload.cases.forEach((item) => state.selectedCases.add(item.id));

  renderProviders(payload.providers);
  renderCases(payload.cases);
}

function renderProviders(providers) {
  providersEl.innerHTML = providers
    .map(
      (provider) => `
        <label class="chip">
          <input type="checkbox" data-kind="provider" value="${escapeHtml(provider.name)}" checked>
          <span>${escapeHtml(provider.name)}</span>
          <small>${escapeHtml(provider.model)}</small>
        </label>
      `
    )
    .join("");

  providersEl.querySelectorAll("input").forEach((input) => {
    input.addEventListener("change", () => syncSelections("provider"));
  });
}

function renderCases(cases) {
  casesEl.innerHTML = cases
    .map(
      (item) => `
        <label class="case-card">
          <input type="checkbox" data-kind="case" value="${escapeHtml(item.id)}" checked>
          <span class="case-title">${escapeHtml(item.title)}</span>
          <small>${escapeHtml(item.description)}</small>
        </label>
      `
    )
    .join("");

  casesEl.querySelectorAll("input").forEach((input) => {
    input.addEventListener("change", () => syncSelections("case"));
  });
}

function syncSelections(kind) {
  const selector = kind === "provider" ? 'input[data-kind="provider"]' : 'input[data-kind="case"]';
  const target = kind === "provider" ? state.selectedProviders : state.selectedCases;
  target.clear();
  document.querySelectorAll(selector).forEach((input) => {
    if (input.checked) {
      target.add(input.value);
    }
  });
}

function toggleSelection(kind) {
  const selector = kind === "providers" ? 'input[data-kind="provider"]' : 'input[data-kind="case"]';
  const inputs = Array.from(document.querySelectorAll(selector));
  const shouldCheck = inputs.some((input) => !input.checked);
  inputs.forEach((input) => {
    input.checked = shouldCheck;
  });
  syncSelections(kind === "providers" ? "provider" : "case");
}

async function onRunSubmit(event) {
  event.preventDefault();
  syncSelections("provider");
  syncSelections("case");

  if (!state.selectedProviders.size || !state.selectedCases.size) {
    runStatusEl.textContent = "Select at least one provider and one case.";
    return;
  }

  runButtonEl.disabled = true;
  runStatusEl.textContent = "Submitting verification run...";

  const response = await fetch("/api/runs", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      provider_names: Array.from(state.selectedProviders),
      case_ids: Array.from(state.selectedCases),
    }),
  });
  const payload = await response.json();
  if (!response.ok) {
    runStatusEl.textContent = payload.error || "Failed to start run.";
    runButtonEl.disabled = false;
    return;
  }

  state.activeRunId = payload.id;
  runStatusEl.textContent = `Run ${payload.id} queued. Polling...`;
  await loadRuns();
  await loadRunDetail(payload.id);
  ensurePolling();
}

async function loadRuns() {
  const response = await fetch("/api/runs");
  const payload = await response.json();
  const runs = payload.runs || [];

  heroRunCountEl.textContent = `${runs.length} runs`;
  if (!runs.length) {
    runsEl.innerHTML = '<div class="detail-empty">No runs yet.</div>';
    return;
  }

  runsEl.innerHTML = runs
    .map((run) => {
      const summary = run.summary || {};
      const label = classifyRun(summary);
      return `
        <button class="run-row" data-run-id="${escapeHtml(run.id)}">
          <div>
            <strong>${escapeHtml(run.id)}</strong>
            <span class="muted">${escapeHtml(run.created_at)}</span>
          </div>
          <div class="run-meta">
            <span class="badge ${escapeHtml(run.status)}">${escapeHtml(run.status)}</span>
            <span class="muted">${escapeHtml(label)}</span>
          </div>
        </button>
      `;
    })
    .join("");

  runsEl.querySelectorAll(".run-row").forEach((button) => {
    button.addEventListener("click", async () => {
      state.activeRunId = button.dataset.runId;
      await loadRunDetail(button.dataset.runId);
      ensurePolling();
    });
  });
}

async function loadRunDetail(runId) {
  if (!runId) {
    return;
  }

  const response = await fetch(`/api/runs/${runId}`);
  const payload = await response.json();
  if (!response.ok) {
    detailEl.innerHTML = `<div class="detail-empty">${escapeHtml(payload.error || "Failed to load run.")}</div>`;
    return;
  }

  renderRunDetail(payload);
  if (payload.status !== "queued" && payload.status !== "running") {
    stopPolling();
    runStatusEl.textContent = `Run ${payload.id} ${payload.status}.`;
    runButtonEl.disabled = false;
  }
}

function renderRunDetail(run) {
  detailSubtitleEl.textContent = `Run ${run.id} created ${run.created_at}`;

  const summary = run.summary || { provider_summaries: [] };
  const providerCards = (summary.provider_summaries || [])
    .map(
      (provider) => `
        <article class="provider-card">
          <div class="provider-head">
            <strong>${escapeHtml(provider.provider_name)}</strong>
            <span class="badge ${escapeHtml(provider.classification)}">${escapeHtml(provider.classification)}</span>
          </div>
          <p class="provider-meta">${escapeHtml(provider.provider_model)} · avg score ${provider.average_score.toFixed(2)} · ${provider.average_latency_ms} ms</p>
          <p class="provider-meta">${escapeHtml(provider.diagnosis)}</p>
        </article>
      `
    )
    .join("");

  const resultRows = (run.results || [])
    .map((result) => {
      const failedChecks = (result.evaluation.checks || [])
        .filter((item) => !item.passed)
        .map((item) => `${item.name}: ${item.detail}`)
        .join(" | ");
      return `
        <tr>
          <td>${escapeHtml(result.provider_name)}</td>
          <td>${escapeHtml(result.case_id)}</td>
          <td><span class="badge ${escapeHtml(result.status)}">${escapeHtml(result.status)}</span></td>
          <td>${Number(result.score).toFixed(2)}</td>
          <td>${result.latency_ms} ms</td>
          <td>${escapeHtml(failedChecks || "none")}</td>
        </tr>
      `;
    })
    .join("");

  detailEl.innerHTML = `
    <div class="summary-row">
      <div class="summary-card">
        <span class="stat-label">Status</span>
        <strong>${escapeHtml(run.status)}</strong>
      </div>
      <div class="summary-card">
        <span class="stat-label">Providers</span>
        <strong>${summary.total_providers || 0}</strong>
      </div>
      <div class="summary-card">
        <span class="stat-label">Cases</span>
        <strong>${summary.total_cases || 0}</strong>
      </div>
      <div class="summary-card">
        <span class="stat-label">Report</span>
        ${
          run.report_path
            ? `<a href="/api/reports/${escapeHtml(run.id)}" target="_blank" rel="noreferrer">open markdown</a>`
            : "<span class=\"muted\">pending</span>"
        }
      </div>
    </div>
    <div class="provider-grid">${providerCards || '<div class="detail-empty">No provider summary yet.</div>'}</div>
    <div class="table-wrap">
      <table>
        <thead>
          <tr>
            <th>Provider</th>
            <th>Case</th>
            <th>Status</th>
            <th>Score</th>
            <th>Latency</th>
            <th>Notes</th>
          </tr>
        </thead>
        <tbody>
          ${resultRows || '<tr><td colspan="6">No results yet.</td></tr>'}
        </tbody>
      </table>
    </div>
  `;
}

function classifyRun(summary) {
  const classifications = (summary.provider_summaries || []).map((item) => item.classification);
  if (!classifications.length) {
    return "pending";
  }
  if (classifications.includes("behaviorally_inconsistent")) {
    return "contains drift";
  }
  if (classifications.includes("uncertain")) {
    return "mixed";
  }
  return "clean";
}

function ensurePolling() {
  stopPolling();
  if (!state.activeRunId) {
    return;
  }
  state.pollTimer = setInterval(async () => {
    await loadRuns();
    await loadRunDetail(state.activeRunId);
  }, 2000);
}

function stopPolling() {
  if (state.pollTimer) {
    clearInterval(state.pollTimer);
    state.pollTimer = null;
  }
}

function escapeHtml(value) {
  return String(value)
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#39;");
}

