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
  const groupedResults = groupResultsByCase(run.results || []);
  const providerPanels = (summary.provider_summaries || [])
    .map((provider) => renderProviderPanel(provider, groupedResults[provider.provider_name] || {}))
    .join("");
  const resultRows = renderResultRows(run.results || []);

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
            : "<span class=\"muted\">pending</span>"
        }
      </div>
    </div>
    <div class="provider-detail-list">${providerPanels || '<div class="detail-empty">No provider summary yet.</div>'}</div>
    <div class="section-block">
      <div class="section-mini-head">
        <h3>Attempt Log</h3>
        <p class="muted">Every provider-case-sample result with failed checks and protocol issues.</p>
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
            ${resultRows || '<tr><td colspan="7">No results yet.</td></tr>'}
          </tbody>
        </table>
      </div>
    </div>
  `;
}

function renderProviderPanel(provider, groupedCaseResults) {
  const protocol = provider.protocol_summary || {};
  const evidenceTrail = renderEvidenceTrail(provider.evidence_trail || []);
  const criticalFindings = renderCriticalFindings(provider.critical_findings || []);
  const comparison = renderComparisonSummary(provider.comparison_summary);
  const signalSummary = renderSignalSummary(provider.signal_summaries || []);
  const caseCards = (provider.case_rollups || [])
    .map((caseRollup) => renderCaseCard(caseRollup, groupedCaseResults[caseRollup.case_id] || []))
    .join("");

  return `
    <article class="provider-detail-card">
      <div class="provider-detail-head">
        <div>
          <div class="provider-headline">
            <strong>${escapeHtml(provider.provider_name)}</strong>
            <span class="badge ${escapeHtml(provider.classification)}">${escapeHtml(provider.classification)}</span>
          </div>
          <p class="provider-meta">${escapeHtml(provider.provider_model)} · avg score ${formatNumber(provider.average_score)} · adjusted ${formatNumber(provider.adjusted_score)} · protocol ${formatNumber(protocol.protocol_score || 0)}</p>
        </div>
        <div class="provider-chip-row">
          <span class="mini-pill">${provider.failed_cases} failed</span>
          <span class="mini-pill">${provider.unstable_cases} unstable</span>
          <span class="mini-pill">${protocol.alignment || "unknown"} protocol</span>
          <span class="mini-pill">${provider.sample_count} sample${provider.sample_count > 1 ? "s" : ""}</span>
        </div>
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

      <div class="case-detail-grid">
        ${caseCards || '<div class="detail-empty">No case detail available.</div>'}
      </div>
    </article>
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

function renderCriticalFindings(findings) {
  if (!findings.length) {
    return '<div class="detail-empty">No critical findings.</div>';
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

function renderCaseCard(caseRollup, caseResults) {
  const protocol = caseRollup.protocol_summary || {};
  const attemptRows = (caseRollup.attempts || [])
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
    <article class="case-detail-card">
      <div class="case-detail-head">
        <div>
          <strong>${escapeHtml(caseRollup.case_title)}</strong>
          <p class="muted">${escapeHtml(caseRollup.case_id)} · ${escapeHtml(caseRollup.signal)}</p>
        </div>
        <div class="provider-chip-row">
          <span class="badge ${escapeHtml(caseRollup.status)}">${escapeHtml(caseRollup.status)}</span>
          <span class="mini-pill">${escapeHtml(caseRollup.stability)}</span>
          <span class="mini-pill">${escapeHtml(protocol.alignment || "compatible")}</span>
        </div>
      </div>
      <div class="metric-grid metric-grid-compact">
        <div class="metric-card">
          <span class="stat-label">Pass Rate</span>
          <strong>${formatNumber(caseRollup.pass_rate)}</strong>
        </div>
        <div class="metric-card">
          <span class="stat-label">Adjusted</span>
          <strong>${formatNumber(caseRollup.adjusted_score)}</strong>
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
    </article>
  `;
}

function renderResultRows(results) {
  return results
    .map((result) => {
      const failedChecks = (result.evaluation.checks || [])
        .filter((item) => !item.passed)
        .map((item) => `${item.name}: ${item.detail}`)
        .join(" | ");
      const protocolIssues = (result.raw?.protocol_evidence?.issues || []).join(", ");
      const notes = [failedChecks || "none", protocolIssues || "none"].join(" / protocol: ");
      return `
        <tr>
          <td>${escapeHtml(result.provider_name)}</td>
          <td>${escapeHtml(result.case_id)}</td>
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
