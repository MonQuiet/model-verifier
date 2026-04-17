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

The current implementation can detect obvious drift, but it still has three structural limits:

1. All checks are effectively equal, which makes severe safety drift and minor formatting drift too close in score impact.
2. There is no signal grouping, so the system cannot say whether a provider failed on `safety`, `context`, or `structured output`.
3. There is no baseline comparison yet, so the result is still “rule conformance” rather than “authenticity confidence”.

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

Status: `pending`

Deliverables:

- support linking an upstream provider to a baseline provider
- run the same case set against both
- store per-case deltas and mismatch reasons

### Step 3: Repeat Sampling

Status: `pending`

Deliverables:

- run selected cases multiple times
- measure variance in JSON adherence, refusal style, and context retention
- factor instability into classification

### Step 4: API-Level Evidence

Status: `pending`

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
