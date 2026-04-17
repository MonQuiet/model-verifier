const state = {
  catalog: null,
  selectedProviders: new Set(),
  selectedCases: new Set(),
  activeRunId: null,
  currentRun: null,
  pollTimer: null,
  detailView: createDetailViewState(),
};

const providersEl = document.getElementById("providers");
const casesEl = document.getElementById("cases");
const runsEl = document.getElementById("runs");
const detailEl = document.getElementById("detail");
const detailSubtitleEl = document.getElementById("detail-subtitle");
const runStatusEl = document.getElementById("run-status");
const heroRunCountEl = document.getElementById("hero-run-count");
const runButtonEl = document.getElementById("run-button");
const sampleCountEl = document.getElementById("sample-count");

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

function createDetailViewState(runId = null) {
  return {
    runId,
    providerName: "all",
    classification: "all",
    protocolAlignment: "all",
    searchText: "",
    criticalOnly: false,
    providerOpen: {},
    caseOpen: {},
    defaultProviderOpen: true,
    defaultCaseOpen: true,
  };
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
      sample_count: Math.max(Number(sampleCountEl.value) || 1, 1),
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
      const activeClass = run.id === state.activeRunId ? " active" : "";
      return `
        <button class="run-row${activeClass}" data-run-id="${escapeHtml(run.id)}">
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
      await loadRuns();
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

  if (state.detailView.runId !== payload.id) {
    state.detailView = createDetailViewState(payload.id);
  }

  state.currentRun = payload;
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
  const groupedResults = groupResultsByCase(run.results || []);
  const view = buildRunView(run, summary, groupedResults, state.detailView);
  const providerPanels = view.visibleProviders.map((entry) => renderProviderPanel(entry)).join("");
  const resultRows = renderResultRows(view.visibleResults, view);

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
        <span class="stat-label">Samples</span>
        <strong>${summary.sample_count || run.request?.sample_count || 1}</strong>
      </div>
      <div class="summary-card">
        <span class="stat-label">Report</span>
        ${
          run.report_path
            ? `<a href="/api/reports/${escapeHtml(run.id)}" target="_blank" rel="noreferrer">open markdown</a>`
            : '<span class="muted">pending</span>'
        }
      </div>
    </div>
    ${renderDetailToolbar(view)}
    <div class="provider-detail-list">${providerPanels || '<div class="detail-empty">No provider panels match the current filters.</div>'}</div>
    <div class="section-block">
      <div class="section-mini-head">
        <div>
          <h3>Attempt Log</h3>
          <p class="muted">Showing ${view.visibleResults.length} of ${view.totalAttempts} attempts after filters.</p>
        </div>
        <div class="provider-chip-row">
          <span class="mini-pill">${view.visibleResults.length} visible</span>
          <span class="mini-pill">${view.totalAttempts} total</span>
        </div>
      </div>
      <div class="table-wrap">
        <table>
          <thead>
            <tr>
              <th>Provider</th>
              <th>Case</th>
              <th>Sample</th>
              <th>Status</th>
              <th>Score</th>
              <th>Protocol</th>
              <th>Notes</th>
            </tr>
          </thead>
          <tbody>
            ${resultRows || '<tr><td colspan="7">No results match the current filters.</td></tr>'}
          </tbody>
        </table>
      </div>
    </div>
  `;

  bindDetailInteractions();
}

function renderDetailToolbar(view) {
  if (!view.providers.length) {
    return "";
  }

  return `
    <div class="detail-toolbar">
      <div class="toolbar-grid">
        <label class="toolbar-field">
          <span>Provider</span>
          <select data-detail-filter="providerName">
            ${renderOptionTags(["all", ...view.filterOptions.providerNames], state.detailView.providerName)}
          </select>
        </label>
        <label class="toolbar-field">
          <span>Classification</span>
          <select data-detail-filter="classification">
            ${renderOptionTags(["all", ...view.filterOptions.classifications], state.detailView.classification)}
          </select>
        </label>
        <label class="toolbar-field">
          <span>Protocol</span>
          <select data-detail-filter="protocolAlignment">
            ${renderOptionTags(["all", ...view.filterOptions.protocolAlignments], state.detailView.protocolAlignment)}
          </select>
        </label>
        <label class="toolbar-field toolbar-search">
          <span>Search</span>
          <input type="search" data-detail-filter="searchText" value="${escapeHtml(state.detailView.searchText)}" placeholder="provider, case, issue, response text">
        </label>
      </div>
      <div class="toolbar-actions">
        <label class="toggle-pill">
          <input type="checkbox" data-detail-filter="criticalOnly" ${state.detailView.criticalOnly ? "checked" : ""}>
          <span>Critical only</span>
        </label>
        <button type="button" class="ghost compact-button" data-detail-action="expand-all">Expand all</button>
        <button type="button" class="ghost compact-button" data-detail-action="collapse-all">Collapse all</button>
        <button type="button" class="ghost compact-button" data-detail-action="export-visible">Export visible CSV</button>
      </div>
      <div class="toolbar-summary muted">
        Showing ${view.visibleProviders.length}/${view.providers.length} providers, ${view.visibleCaseCount}/${view.totalCaseCount} cases, ${view.visibleResults.length}/${view.totalAttempts} attempts.
      </div>
    </div>
  `;
}

function renderProviderPanel(entry) {
  const { provider, visibleCaseEntries, visibleCriticalFindings } = entry;
  const protocol = provider.protocol_summary || {};
  const evidenceTrail = renderEvidenceTrail(provider.evidence_trail || []);
  const criticalFindings = renderCriticalFindings(
    visibleCriticalFindings,
    "No critical findings match the current filters."
  );
  const comparison = renderComparisonSummary(provider.comparison_summary);
  const signalSummary = renderSignalSummary(provider.signal_summaries || []);
  const caseCards = visibleCaseEntries.map((caseEntry) => renderCaseCard(provider, caseEntry)).join("");
  const visibleCaseCount = visibleCaseEntries.length;
  const totalCaseCount = (provider.case_rollups || []).length;

  return `
    <details class="provider-fold" data-provider-name="${escapeHtml(provider.provider_name)}" ${isProviderOpen(provider.provider_name) ? "open" : ""}>
      <summary class="fold-summary provider-fold-summary">
        <div class="fold-title-block">
          <div class="provider-headline">
            <strong>${escapeHtml(provider.provider_name)}</strong>
            <span class="badge ${escapeHtml(provider.classification)}">${escapeHtml(provider.classification)}</span>
          </div>
          <p class="provider-meta">${escapeHtml(provider.provider_model)} · avg score ${formatNumber(provider.average_score)} · adjusted ${formatNumber(provider.adjusted_score)} · protocol ${formatNumber(protocol.protocol_score || 0)}</p>
        </div>
        <div class="fold-summary-side">
          <span class="mini-pill">${visibleCaseCount}/${totalCaseCount} cases</span>
          <span class="mini-pill">${provider.failed_cases} failed</span>
          <span class="mini-pill">${protocol.alignment || "unknown"} protocol</span>
        </div>
      </summary>
      <div class="provider-fold-body">
        <div class="fold-action-row">
          <p class="inline-note">Provider-level evidence trail, baseline deltas, and filtered case evidence.</p>
          <button type="button" class="ghost compact-button" data-provider-export="${escapeHtml(provider.provider_name)}">Export Provider JSON</button>
        </div>

        <div class="metric-grid">
          <div class="metric-card">
            <span class="stat-label">Diagnosis</span>
            <strong>${escapeHtml(provider.diagnosis)}</strong>
          </div>
          <div class="metric-card">
            <span class="stat-label">Critical Findings</span>
            <strong>${provider.critical_findings.length}</strong>
          </div>
          <div class="metric-card">
            <span class="stat-label">Protocol Drift Cases</span>
            <strong>${protocol.flagged_cases || 0}</strong>
          </div>
          <div class="metric-card">
            <span class="stat-label">Critical Unstable</span>
            <strong>${provider.critical_unstable_cases}</strong>
          </div>
        </div>

        <div class="detail-columns">
          <section class="section-block">
            <div class="section-mini-head">
              <h3>Evidence Trail</h3>
              <p class="muted">The explicit path from raw evidence to the final classification.</p>
            </div>
            ${evidenceTrail}
          </section>
          <section class="section-block">
            <div class="section-mini-head">
              <h3>Critical Findings</h3>
              <p class="muted">Critical behavior, stability, and protocol issues are broken out separately.</p>
            </div>
            ${criticalFindings}
          </section>
        </div>

        <div class="detail-columns">
          <section class="section-block">
            <div class="section-mini-head">
              <h3>Signal Summary</h3>
              <p class="muted">Behavior quality aggregated by signal group.</p>
            </div>
            ${signalSummary}
          </section>
          <section class="section-block">
            <div class="section-mini-head">
              <h3>Baseline Comparison</h3>
              <p class="muted">Behavior, protocol, and stability deltas versus the configured baseline.</p>
            </div>
            ${comparison}
          </section>
        </div>

        <div class="case-detail-grid">${caseCards || '<div class="detail-empty">No case cards match the current filters.</div>'}</div>
      </div>
    </details>
  `;
}

function renderEvidenceTrail(entries) {
  if (!entries.length) {
    return '<div class="detail-empty">No evidence trail available.</div>';
  }

  return `
    <ol class="evidence-list">
      ${entries
        .map(
          (entry) => `
            <li class="evidence-item evidence-${escapeHtml(entry.level || "neutral")}">
              <span class="badge badge-${escapeHtml(entry.level || "neutral")}">${escapeHtml(entry.level || "neutral")}</span>
              <div>
                <strong>${escapeHtml(entry.title || "Evidence")}</strong>
                <p class="muted">${escapeHtml(entry.detail || "")}</p>
              </div>
            </li>
          `
        )
        .join("")}
    </ol>
  `;
}

function renderCriticalFindings(findings, emptyText = "No critical findings.") {
  if (!findings.length) {
    return `<div class="detail-empty">${escapeHtml(emptyText)}</div>`;
  }

  return `
    <ul class="finding-list">
      ${findings
        .map(
          (finding) => `
            <li class="finding-item">
              <div class="finding-meta">
                <span class="badge badge-${escapeHtml(finding.severity || "neutral")}">${escapeHtml(finding.severity || "neutral")}</span>
                <strong>${escapeHtml(finding.case_id)}</strong>
                <span class="muted">${escapeHtml(finding.kind)} · ${escapeHtml(finding.signal)}</span>
              </div>
              <p>${escapeHtml(finding.detail)}</p>
            </li>
          `
        )
        .join("")}
    </ul>
  `;
}

function renderSignalSummary(signalSummaries) {
  if (!signalSummaries.length) {
    return '<div class="detail-empty">No signal summary available.</div>';
  }

  return `
    <div class="table-wrap compact-table">
      <table>
        <thead>
          <tr>
            <th>Signal</th>
            <th>Critical</th>
            <th>Adjusted</th>
            <th>Penalty</th>
            <th>Failed</th>
            <th>Unstable</th>
          </tr>
        </thead>
        <tbody>
          ${signalSummaries
            .map(
              (signal) => `
                <tr>
                  <td>${escapeHtml(signal.signal)}</td>
                  <td>${signal.critical ? "yes" : "no"}</td>
                  <td>${formatNumber(signal.adjusted_weighted_score)}</td>
                  <td>${formatNumber(signal.stability_penalty)}</td>
                  <td>${signal.failed_cases}/${signal.total_cases}</td>
                  <td>${signal.unstable_cases}</td>
                </tr>
              `
            )
            .join("")}
        </tbody>
      </table>
    </div>
  `;
}

function renderComparisonSummary(comparison) {
  if (!comparison) {
    return '<div class="detail-empty">No baseline configured.</div>';
  }

  const mismatches = (comparison.case_deltas || []).filter((item) => !item.matched).slice(0, 4);
  return `
    <div class="comparison-card">
      <div class="comparison-overview">
        <span class="badge ${escapeHtml(comparison.alignment)}">${escapeHtml(comparison.alignment)}</span>
        <strong>${escapeHtml(comparison.baseline_provider_name)}</strong>
      </div>
      <p class="muted">${escapeHtml(comparison.diagnosis)}</p>
      <div class="metric-grid metric-grid-compact">
        <div class="metric-card">
          <span class="stat-label">Adjusted Delta</span>
          <strong>${formatSignedNumber(comparison.weighted_score_delta)}</strong>
        </div>
        <div class="metric-card">
          <span class="stat-label">Protocol Delta</span>
          <strong>${formatSignedNumber(comparison.protocol_score_delta)}</strong>
        </div>
        <div class="metric-card">
          <span class="stat-label">Mismatched Cases</span>
          <strong>${comparison.mismatch_cases}</strong>
        </div>
      </div>
      ${
        mismatches.length
          ? `
            <ul class="finding-list compact-findings">
              ${mismatches
                .map(
                  (item) => `
                    <li class="finding-item">
                      <div class="finding-meta">
                        <strong>${escapeHtml(item.case_id)}</strong>
                        <span class="muted">${escapeHtml(item.provider_protocol_alignment)} vs ${escapeHtml(item.baseline_protocol_alignment)}</span>
                      </div>
                      <p>${escapeHtml((item.mismatch_reasons || []).join("; "))}</p>
                    </li>
                  `
                )
                .join("")}
            </ul>
          `
          : '<div class="detail-empty">No baseline mismatches.</div>'
      }
    </div>
  `;
}

function renderCaseCard(provider, caseEntry) {
  const { rollup, caseResults } = caseEntry;
  const protocol = rollup.protocol_summary || {};
  const attemptRows = (rollup.attempts || [])
    .map((attempt) => {
      const issues = attempt.issues?.length ? attempt.issues.join(", ") : "none";
      const failedChecks = attempt.failed_checks?.length ? attempt.failed_checks.join(", ") : "none";
      return `
        <tr>
          <td>${attempt.sample_index + 1}</td>
          <td><span class="badge ${escapeHtml(attempt.status)}">${escapeHtml(attempt.status)}</span></td>
          <td>${formatNumber(attempt.score)}</td>
          <td>${formatNumber(attempt.protocol_score)}</td>
          <td>${escapeHtml(attempt.finish_reason)}</td>
          <td>${escapeHtml(failedChecks)}</td>
          <td>${escapeHtml(issues)}</td>
        </tr>
      `;
    })
    .join("");

  const responseBlocks = caseResults
    .map(
      (result) => `
        <div class="response-block">
          <div class="response-head">
            <strong>Sample ${result.sample_index + 1}</strong>
            <span class="muted">${escapeHtml(result.raw?.protocol_evidence?.content_mode || "unknown")} · ${escapeHtml(result.raw?.protocol_evidence?.tool_call_shape || "none")}</span>
          </div>
          <pre>${escapeHtml(result.response_text || "")}</pre>
        </div>
      `
    )
    .join("");

  return `
    <details class="case-fold" data-provider-name="${escapeHtml(provider.provider_name)}" data-case-id="${escapeHtml(rollup.case_id)}" ${isCaseOpen(provider.provider_name, rollup.case_id) ? "open" : ""}>
      <summary class="fold-summary case-fold-summary">
        <div class="fold-title-block">
          <strong>${escapeHtml(rollup.case_title)}</strong>
          <p class="muted">${escapeHtml(rollup.case_id)} · ${escapeHtml(rollup.signal)}</p>
        </div>
        <div class="provider-chip-row">
          <span class="badge ${escapeHtml(rollup.status)}">${escapeHtml(rollup.status)}</span>
          <span class="mini-pill">${escapeHtml(rollup.stability)}</span>
          <span class="mini-pill">${escapeHtml(protocol.alignment || "compatible")}</span>
        </div>
      </summary>
      <div class="case-fold-body">
        <div class="fold-action-row">
          <p class="inline-note">Sample-by-sample protocol and response evidence for this case.</p>
          <button type="button" class="ghost compact-button" data-case-export="${escapeHtml(caseKey(provider.provider_name, rollup.case_id))}">Export Case JSON</button>
        </div>
        <div class="metric-grid metric-grid-compact">
          <div class="metric-card">
            <span class="stat-label">Pass Rate</span>
            <strong>${formatNumber(rollup.pass_rate)}</strong>
          </div>
          <div class="metric-card">
            <span class="stat-label">Adjusted</span>
            <strong>${formatNumber(rollup.adjusted_score)}</strong>
          </div>
          <div class="metric-card">
            <span class="stat-label">Protocol</span>
            <strong>${formatNumber(protocol.protocol_score || 0)}</strong>
          </div>
          <div class="metric-card">
            <span class="stat-label">Issues</span>
            <strong>${(protocol.issue_types || []).length}</strong>
          </div>
        </div>
        <p class="muted">${escapeHtml(protocol.diagnosis || "No protocol diagnosis available.")}</p>
        <div class="table-wrap compact-table">
          <table>
            <thead>
              <tr>
                <th>Sample</th>
                <th>Status</th>
                <th>Score</th>
                <th>Protocol</th>
                <th>Finish</th>
                <th>Failed Checks</th>
                <th>Issues</th>
              </tr>
            </thead>
            <tbody>
              ${attemptRows || '<tr><td colspan="7">No attempts recorded.</td></tr>'}
            </tbody>
          </table>
        </div>
        <div class="response-stack">
          ${responseBlocks || '<div class="detail-empty">No response preview available.</div>'}
        </div>
      </div>
    </details>
  `;
}

function renderResultRows(results, view) {
  return results
    .map((result) => {
      const failedChecks = (result.evaluation?.checks || [])
        .filter((item) => !item.passed)
        .map((item) => `${item.name}: ${item.detail}`)
        .join(" | ");
      const protocolIssues = (result.raw?.protocol_evidence?.issues || []).join(", ");
      const notes = [failedChecks || "none", protocolIssues || "none"].join(" / protocol: ");
      const caseRollup = view.caseLookup[caseKey(result.provider_name, result.case_id)];
      const alignment = caseRollup?.protocol_summary?.alignment || "unknown";
      return `
        <tr>
          <td>${escapeHtml(result.provider_name)}</td>
          <td>${escapeHtml(result.case_id)}<br><span class="muted">${escapeHtml(alignment)}</span></td>
          <td>${(result.sample_index || 0) + 1}</td>
          <td><span class="badge ${escapeHtml(result.status)}">${escapeHtml(result.status)}</span></td>
          <td>${formatNumber(result.score)}</td>
          <td>${formatNumber(result.raw?.protocol_evidence?.protocol_score || 0)}</td>
          <td>${escapeHtml(notes)}</td>
        </tr>
      `;
    })
    .join("");
}

function buildRunView(run, summary, groupedResults, detailView) {
  const providers = summary.provider_summaries || [];
  const providerLookup = Object.fromEntries(providers.map((provider) => [provider.provider_name, provider]));
  const caseLookup = {};
  providers.forEach((provider) => {
    (provider.case_rollups || []).forEach((rollup) => {
      caseLookup[caseKey(provider.provider_name, rollup.case_id)] = rollup;
    });
  });

  const visibleProviders = providers
    .map((provider) => {
      if (!matchesProviderSelectFilters(provider, detailView)) {
        return null;
      }
      if (detailView.criticalOnly && !providerHasCriticalContent(provider)) {
        return null;
      }

      const providerCaseResults = groupedResults[provider.provider_name] || {};
      const visibleCaseEntries = (provider.case_rollups || [])
        .map((rollup) => {
          const caseResults = providerCaseResults[rollup.case_id] || [];
          if (!matchesCaseFilters(provider, rollup, caseResults, detailView)) {
            return null;
          }
          return { rollup, caseResults };
        })
        .filter(Boolean);

      const providerSearchMatch = !detailView.searchText || matchesProviderSearch(provider, detailView.searchText);
      const requiresCaseMatch = detailView.protocolAlignment !== "all" || (!providerSearchMatch && Boolean(detailView.searchText));
      if (requiresCaseMatch && !visibleCaseEntries.length) {
        return null;
      }
      if (!providerSearchMatch && !visibleCaseEntries.length) {
        return null;
      }

      const visibleCaseIds = new Set(visibleCaseEntries.map((entry) => entry.rollup.case_id));
      const visibleCriticalFindings = filterCriticalFindings(
        provider.critical_findings || [],
        visibleCaseIds,
        detailView.searchText
      );

      return {
        provider,
        visibleCaseEntries,
        visibleCriticalFindings,
      };
    })
    .filter(Boolean);

  const visibleResults = (run.results || []).filter((result) =>
    matchesResultFilters(result, providerLookup, caseLookup, detailView)
  );

  return {
    run,
    providers,
    providerLookup,
    caseLookup,
    filterOptions: {
      providerNames: sortText(providers.map((provider) => provider.provider_name)),
      classifications: sortText(unique(providers.map((provider) => provider.classification).filter(Boolean))),
      protocolAlignments: sortProtocolAlignments(
        unique(
          providers.flatMap((provider) =>
            (provider.case_rollups || []).map((rollup) => rollup.protocol_summary?.alignment || "unknown")
          )
        )
      ),
    },
    visibleProviders,
    visibleCaseCount: visibleProviders.reduce((count, entry) => count + entry.visibleCaseEntries.length, 0),
    totalCaseCount: providers.reduce((count, provider) => count + (provider.case_rollups || []).length, 0),
    visibleResults,
    totalAttempts: (run.results || []).length,
  };
}

function matchesProviderSelectFilters(provider, detailView) {
  if (detailView.providerName !== "all" && provider.provider_name !== detailView.providerName) {
    return false;
  }
  if (detailView.classification !== "all" && provider.classification !== detailView.classification) {
    return false;
  }
  return true;
}

function matchesCaseFilters(provider, rollup, caseResults, detailView) {
  const alignment = rollup.protocol_summary?.alignment || "unknown";
  if (detailView.protocolAlignment !== "all" && alignment !== detailView.protocolAlignment) {
    return false;
  }
  if (detailView.criticalOnly && !isCriticalCaseRollup(rollup)) {
    return false;
  }
  if (!detailView.searchText) {
    return true;
  }
  if (matchesProviderSearch(provider, detailView.searchText)) {
    return true;
  }

  const attemptDetails = (rollup.attempts || []).flatMap((attempt) => [
    attempt.status,
    attempt.finish_reason,
    attempt.content_mode,
    attempt.tool_call_shape,
    (attempt.failed_checks || []).join(" "),
    (attempt.issues || []).join(" "),
  ]);

  return matchesSearch(detailView.searchText, [
    rollup.case_id,
    rollup.case_title,
    rollup.signal,
    rollup.status,
    rollup.stability,
    rollup.protocol_summary?.alignment,
    rollup.protocol_summary?.diagnosis,
    (rollup.dominant_failures || []).join(" "),
    ...attemptDetails,
    ...caseResults.map((result) => result.response_text),
  ]);
}

function matchesResultFilters(result, providerLookup, caseLookup, detailView) {
  const provider = providerLookup[result.provider_name];
  if (!provider || !matchesProviderSelectFilters(provider, detailView)) {
    return false;
  }
  if (detailView.criticalOnly && !providerHasCriticalContent(provider)) {
    return false;
  }

  const rollup = caseLookup[caseKey(result.provider_name, result.case_id)];
  if (!rollup) {
    return false;
  }
  if (detailView.protocolAlignment !== "all" && (rollup.protocol_summary?.alignment || "unknown") !== detailView.protocolAlignment) {
    return false;
  }
  if (detailView.criticalOnly && !isCriticalResult(result, rollup)) {
    return false;
  }
  if (!detailView.searchText) {
    return true;
  }
  if (matchesProviderSearch(provider, detailView.searchText)) {
    return true;
  }

  const failedChecks = (result.evaluation?.checks || [])
    .filter((item) => !item.passed)
    .map((item) => `${item.name} ${item.detail}`);

  return matchesSearch(detailView.searchText, [
    result.provider_name,
    provider.provider_model,
    provider.classification,
    result.case_id,
    rollup.case_title,
    result.status,
    result.raw?.protocol_evidence?.finish_reason,
    result.raw?.protocol_evidence?.content_mode,
    result.raw?.protocol_evidence?.tool_call_shape,
    (result.raw?.protocol_evidence?.issues || []).join(" "),
    failedChecks.join(" "),
    result.response_text,
  ]);
}

function matchesProviderSearch(provider, searchText) {
  if (!searchText) {
    return true;
  }
  return matchesSearch(searchText, [
    provider.provider_name,
    provider.provider_model,
    provider.classification,
    provider.diagnosis,
    provider.protocol_summary?.alignment,
    provider.protocol_summary?.diagnosis,
    ...((provider.failures || []).slice(0, 5)),
    ...(provider.evidence_trail || []).flatMap((entry) => [entry.title, entry.detail]),
    ...(provider.critical_findings || []).flatMap((finding) => [
      finding.case_id,
      finding.kind,
      finding.signal,
      finding.detail,
    ]),
  ]);
}

function providerHasCriticalContent(provider) {
  if (provider.critical_findings?.length) {
    return true;
  }
  if ((provider.case_rollups || []).some((rollup) => isCriticalCaseRollup(rollup))) {
    return true;
  }
  return false;
}

function isCriticalCaseRollup(rollup) {
  return Boolean(
    rollup.critical ||
      rollup.status === "failed" ||
      rollup.status === "error" ||
      rollup.protocol_summary?.alignment === "major_drift"
  );
}

function isCriticalResult(result, rollup) {
  return Boolean(
    result.evaluation?.critical ||
      result.status === "error" ||
      (result.raw?.protocol_evidence?.issues || []).some((issue) => isCriticalProtocolIssue(issue)) ||
      isCriticalCaseRollup(rollup)
  );
}

function isCriticalProtocolIssue(issue) {
  return [
    "missing_choices",
    "missing_message",
    "missing_content",
    "missing_finish_reason",
    "unsupported_content_block",
    "invalid_tool_calls",
  ].includes(issue);
}

function filterCriticalFindings(findings, visibleCaseIds, searchText) {
  return findings.filter((finding) => {
    if (visibleCaseIds.size && !visibleCaseIds.has(finding.case_id) && !searchText) {
      return false;
    }
    if (!searchText) {
      return true;
    }
    return matchesSearch(searchText, [
      finding.case_id,
      finding.kind,
      finding.signal,
      finding.detail,
      finding.severity,
    ]);
  });
}

function bindDetailInteractions() {
  detailEl.querySelectorAll("[data-detail-filter]").forEach((control) => {
    const eventName = control.type === "search" ? "input" : "change";
    control.addEventListener(eventName, onDetailFilterChange);
  });

  detailEl.querySelectorAll("[data-detail-action]").forEach((button) => {
    button.addEventListener("click", onDetailAction);
  });

  detailEl.querySelectorAll("[data-provider-export]").forEach((button) => {
    button.addEventListener("click", onProviderExport);
  });

  detailEl.querySelectorAll("[data-case-export]").forEach((button) => {
    button.addEventListener("click", onCaseExport);
  });

  detailEl.querySelectorAll("details.provider-fold").forEach((element) => {
    element.addEventListener("toggle", () => {
      state.detailView.providerOpen[element.dataset.providerName] = element.open;
    });
  });

  detailEl.querySelectorAll("details.case-fold").forEach((element) => {
    element.addEventListener("toggle", () => {
      state.detailView.caseOpen[caseKey(element.dataset.providerName, element.dataset.caseId)] = element.open;
    });
  });
}

function onDetailFilterChange(event) {
  const { detailView } = state;
  const field = event.target.dataset.detailFilter;
  if (!field) {
    return;
  }

  if (field === "criticalOnly") {
    detailView.criticalOnly = event.target.checked;
  } else {
    detailView[field] = event.target.value;
  }

  if (state.currentRun) {
    renderRunDetail(state.currentRun);
  }
}

function onDetailAction(event) {
  const action = event.currentTarget.dataset.detailAction;
  if (!action || !state.currentRun) {
    return;
  }

  if (action === "expand-all") {
    state.detailView.defaultProviderOpen = true;
    state.detailView.defaultCaseOpen = true;
    state.detailView.providerOpen = {};
    state.detailView.caseOpen = {};
    renderRunDetail(state.currentRun);
    return;
  }

  if (action === "collapse-all") {
    state.detailView.defaultProviderOpen = false;
    state.detailView.defaultCaseOpen = false;
    state.detailView.providerOpen = {};
    state.detailView.caseOpen = {};
    renderRunDetail(state.currentRun);
    return;
  }

  if (action === "export-visible") {
    exportVisibleResults();
  }
}

function onProviderExport(event) {
  const providerName = event.currentTarget.dataset.providerExport;
  const view = buildRunView(
    state.currentRun,
    state.currentRun.summary || { provider_summaries: [] },
    groupResultsByCase(state.currentRun.results || []),
    state.detailView
  );
  const entry = view.visibleProviders.find((item) => item.provider.provider_name === providerName);
  if (!entry) {
    return;
  }

  const payload = {
    exported_at: new Date().toISOString(),
    run_id: state.currentRun.id,
    filters: serializeDetailFilters(state.detailView),
    provider_summary: entry.provider,
    visible_case_rollups: entry.visibleCaseEntries.map((item) => item.rollup),
    visible_results: entry.visibleCaseEntries.flatMap((item) => item.caseResults),
  };
  downloadJson(`${slugify(providerName)}-${state.currentRun.id}.json`, payload);
}

function onCaseExport(event) {
  const identifier = event.currentTarget.dataset.caseExport;
  const [providerName, caseId] = identifier.split("::");
  const view = buildRunView(
    state.currentRun,
    state.currentRun.summary || { provider_summaries: [] },
    groupResultsByCase(state.currentRun.results || []),
    state.detailView
  );
  const entry = view.visibleProviders.find((item) => item.provider.provider_name === providerName);
  const caseEntry = entry?.visibleCaseEntries.find((item) => item.rollup.case_id === caseId);
  if (!caseEntry) {
    return;
  }

  const payload = {
    exported_at: new Date().toISOString(),
    run_id: state.currentRun.id,
    filters: serializeDetailFilters(state.detailView),
    provider_name: providerName,
    provider_model: entry.provider.provider_model,
    case_rollup: caseEntry.rollup,
    results: caseEntry.caseResults,
  };
  downloadJson(`${slugify(providerName)}-${slugify(caseId)}-${state.currentRun.id}.json`, payload);
}

function exportVisibleResults() {
  const view = buildRunView(
    state.currentRun,
    state.currentRun.summary || { provider_summaries: [] },
    groupResultsByCase(state.currentRun.results || []),
    state.detailView
  );
  const rows = view.visibleResults.map((result) => buildCsvRow(result, view.providerLookup, view.caseLookup));
  downloadCsv(`visible-attempts-${state.currentRun.id}.csv`, rows);
}

function buildCsvRow(result, providerLookup, caseLookup) {
  const provider = providerLookup[result.provider_name] || {};
  const rollup = caseLookup[caseKey(result.provider_name, result.case_id)] || {};
  const protocol = result.raw?.protocol_evidence || {};
  const failedChecks = (result.evaluation?.checks || [])
    .filter((item) => !item.passed)
    .map((item) => `${item.name}: ${item.detail}`)
    .join(" | ");

  return {
    run_id: result.run_id || state.currentRun?.id || "",
    provider_name: result.provider_name,
    provider_model: provider.provider_model || "",
    provider_classification: provider.classification || "",
    case_id: result.case_id,
    case_title: rollup.case_title || "",
    signal: rollup.signal || "",
    critical: String(Boolean(result.evaluation?.critical || rollup.critical)),
    sample_index: (result.sample_index || 0) + 1,
    status: result.status,
    score: formatNumber(result.score),
    protocol_score: formatNumber(protocol.protocol_score || 0),
    case_protocol_alignment: rollup.protocol_summary?.alignment || "",
    finish_reason: protocol.finish_reason || "",
    content_mode: protocol.content_mode || "",
    tool_call_shape: protocol.tool_call_shape || "",
    protocol_issues: (protocol.issues || []).join("; "),
    failed_checks: failedChecks,
    latency_ms: String(result.latency_ms || 0),
    response_excerpt: String(result.response_text || "").slice(0, 180),
  };
}

function renderOptionTags(options, selectedValue) {
  return options
    .map((value) => {
      const label = value === "all" ? "All" : humanizeToken(value);
      return `<option value="${escapeHtml(value)}" ${value === selectedValue ? "selected" : ""}>${escapeHtml(label)}</option>`;
    })
    .join("");
}

function serializeDetailFilters(detailView) {
  return {
    provider_name: detailView.providerName,
    classification: detailView.classification,
    protocol_alignment: detailView.protocolAlignment,
    search_text: detailView.searchText,
    critical_only: detailView.criticalOnly,
  };
}

function groupResultsByCase(results) {
  const grouped = {};
  results.forEach((result) => {
    const providerGroup = grouped[result.provider_name] || (grouped[result.provider_name] = {});
    const caseGroup = providerGroup[result.case_id] || (providerGroup[result.case_id] = []);
    caseGroup.push(result);
  });
  Object.values(grouped).forEach((providerGroup) => {
    Object.keys(providerGroup).forEach((caseId) => {
      providerGroup[caseId] = providerGroup[caseId].sort((left, right) => (left.sample_index || 0) - (right.sample_index || 0));
    });
  });
  return grouped;
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

function isProviderOpen(providerName) {
  return state.detailView.providerOpen[providerName] ?? state.detailView.defaultProviderOpen;
}

function isCaseOpen(providerName, caseId) {
  return state.detailView.caseOpen[caseKey(providerName, caseId)] ?? state.detailView.defaultCaseOpen;
}

function caseKey(providerName, caseId) {
  return `${providerName}::${caseId}`;
}

function matchesSearch(searchText, values) {
  const needle = normalizeText(searchText);
  if (!needle) {
    return true;
  }
  return values.some((value) => normalizeText(value).includes(needle));
}

function normalizeText(value) {
  return String(value || "").toLowerCase();
}

function unique(values) {
  return Array.from(new Set(values));
}

function sortText(values) {
  return [...values].sort((left, right) => left.localeCompare(right));
}

function sortProtocolAlignments(values) {
  const order = ["compatible", "minor_drift", "major_drift", "unknown"];
  return [...values].sort((left, right) => order.indexOf(left) - order.indexOf(right));
}

function humanizeToken(value) {
  return String(value || "")
    .replaceAll("_", " ")
    .replaceAll("-", " ")
    .replace(/\b\w/g, (letter) => letter.toUpperCase());
}

function downloadJson(filename, payload) {
  downloadText(filename, JSON.stringify(payload, null, 2), "application/json;charset=utf-8");
}

function downloadCsv(filename, rows) {
  const columns = [
    "run_id",
    "provider_name",
    "provider_model",
    "provider_classification",
    "case_id",
    "case_title",
    "signal",
    "critical",
    "sample_index",
    "status",
    "score",
    "protocol_score",
    "case_protocol_alignment",
    "finish_reason",
    "content_mode",
    "tool_call_shape",
    "protocol_issues",
    "failed_checks",
    "latency_ms",
    "response_excerpt",
  ];

  const lines = [columns.join(",")];
  rows.forEach((row) => {
    lines.push(columns.map((column) => csvCell(row[column])).join(","));
  });
  downloadText(filename, lines.join("\n"), "text/csv;charset=utf-8");
}

function downloadText(filename, content, mimeType) {
  const blob = new Blob([content], { type: mimeType });
  const url = URL.createObjectURL(blob);
  const anchor = document.createElement("a");
  anchor.href = url;
  anchor.download = filename;
  document.body.appendChild(anchor);
  anchor.click();
  anchor.remove();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
}

function csvCell(value) {
  const text = String(value ?? "");
  return `"${text.replaceAll('"', '""')}"`;
}

function slugify(value) {
  return String(value || "")
    .trim()
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, "-")
    .replace(/^-+|-+$/g, "");
}

function formatNumber(value) {
  return Number(value || 0).toFixed(2);
}

function formatSignedNumber(value) {
  const number = Number(value || 0);
  return `${number >= 0 ? "+" : ""}${number.toFixed(2)}`;
}

function escapeHtml(value) {
  return String(value)
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#39;");
}
