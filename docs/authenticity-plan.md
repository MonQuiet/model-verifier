# Model Authenticity Plan

## Goal

Turn `model-verifier` from a fixed-rule behavior checker into an evidence-based authenticity review tool for upstream AI gateways.

The target is not absolute proof. The target is a reproducible confidence judgment backed by:

- weighted behavioral evidence
- critical-signal coverage
- official baseline comparisons
- repeated sampling and stability analysis
- API-level evidence such as `usage`, `finish_reason`, and tool-call shape

## Current Gap

The current implementation now covers weighted scoring, baseline pairing, repeat sampling, and protocol evidence. The remaining structural limit is:

1. The web review surface still under-explains the evidence trail compared with the richer backend summary.

## Roadmap

### Step 1: Weighted Evidence Model

Status: `completed`

Deliverables:

- add signal metadata to cases
- add per-check weights
- aggregate by signal group
- classify with critical-signal gates instead of flat average only
- record progress in this document

### Step 2: Baseline Pairing

Status: `completed`

Deliverables:

- support linking an upstream provider to a baseline provider
- run the same case set against both
- store per-case deltas and mismatch reasons

### Step 3: Repeat Sampling

Status: `completed`

Deliverables:

- run selected cases multiple times
- measure variance in JSON adherence, refusal style, and context retention
- factor instability into classification

### Step 4: API-Level Evidence

Status: `completed`

Deliverables:

- capture `usage`
- capture `finish_reason`
- inspect tool-call and content-block structure
- surface protocol-level mismatches in reports

### Step 5: Review UX

Status: `pending`

Deliverables:

- show signal summaries in the report and UI
- show critical failures separately
- show an explicit evidence trail for the final classification

## Progress Log

### 2026-04-17: Step 1 Completed

Implemented:

- case-level signal groups and case weights
- per-check weights inside expectations
- weighted per-case scoring
- provider-level signal summaries
- classification rules that require critical signals to stay above threshold

Verified with:

- `python3 -m unittest discover -s tests`
- `python3 -m app.cli --providers mock-reference-gpt41,mock-suspect-gateway --cases json_contract,context_memory,refusal_boundary,tool_plan_json`

Impact:

- the system now distinguishes “minor formatting drift” from “high-value authenticity drift”
- reports can explain *where* the drift happened, not just *that* drift happened
- the next step can build on stable signal groups instead of a flat score

### 2026-04-17: Step 2 Completed

Implemented:

- provider-level `baseline_provider` linkage in configuration
- automatic inclusion of referenced baseline providers during a run
- per-signal deltas against the configured baseline
- per-case mismatch reasons against the configured baseline
- baseline alignment states: `aligned`, `partial_drift`, `strong_drift`

Verified with:

- `python3 -m unittest discover -s tests`
- `python3 -m app.cli --providers mock-clean-gateway,mock-suspect-gateway --cases json_contract,context_memory,refusal_boundary,tool_plan_json`

Impact:

- the system can now distinguish “this provider is internally weak” from “this provider diverges from its claimed reference”
- reports now expose a concrete mismatch trail that is closer to real authenticity review work

### 2026-04-17: Step 3 Completed

Implemented:

- `sample_count` support in CLI, service layer, and HTTP API
- repeated execution of each selected case per provider
- case-level stability summaries with `stable`, `style_variance`, `moderate_variance`, and `high_variance`
- stability-adjusted provider scoring and classification
- baseline comparison that now includes repeated-sample pass-rate and stability drift
- deterministic `mock-flaky-gateway` coverage for regression tests

Verified with:

- `python3 -m unittest discover -s tests`
- `python3 -m app.cli --providers mock-clean-gateway,mock-flaky-gateway --cases json_contract,context_memory,refusal_boundary,tool_plan_json --samples 3`

Impact:

- the system can now separate “single-run looked fine” from “behavior stays consistent across repeated probes”
- flaky upstream behavior now leaves a concrete trail: pass-rate drift, check flips, and stability penalties
- the next step can add protocol evidence on top of a stronger behavioral baseline instead of a single-shot sample

### 2026-04-17: Step 4 Completed

Implemented:

- protocol evidence capture in the provider layer for `usage`, `finish_reason`, content mode, content block types, and `tool_calls`
- case-level protocol summaries with `compatible`, `minor_drift`, and `major_drift`
- provider-level protocol summaries and classification adjustment
- baseline mismatch reasons that now include protocol drift, usage coverage drift, and malformed tool-call evidence
- deterministic `mock-protocol-gateway` coverage for regression tests

Verified with:

- `python3 -m unittest discover -s tests`
- `python3 -m app.cli --providers mock-reference-gpt41,mock-protocol-gateway --cases json_contract,context_memory,refusal_boundary,tool_plan_json`

Impact:

- the system can now catch gateways that copy the right answer text while exposing the wrong response protocol
- protocol evidence is now part of the final confidence judgment instead of being hidden in raw payloads
- the remaining work can focus on review UX rather than backend evidence collection
