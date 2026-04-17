# Model Verifier

`Model Verifier` is a lightweight AI model behavior verification toolkit for upstream APIs that claim to expose specific foundation models. It runs a fixed evaluation suite, stores results in `SQLite`, produces `Markdown` and `JSON` reports, and ships with a thin web UI for task submission and result review.

This repository is intentionally built without third-party Python packages so it can run in restricted environments and still demonstrate:

- model behavior validation against a fixed test set
- consistent result collection and reporting
- small full-stack delivery from backend to frontend
- an evidence model that can evolve into authenticity review

## What It Verifies

The current suite focuses on high-signal black-box checks:

- strict JSON contract adherence
- multi-turn context recall
- refusal behavior on unsafe requests
- bilingual instruction following
- code generation shape
- tool-plan style structured output

Black-box validation cannot prove model identity with absolute certainty. The project intentionally reports outcomes as:

- `likely_match`
- `uncertain`
- `behaviorally_inconsistent`

The scoring model now uses weighted evidence and critical-signal gates. That means safety, context, structured output, and tool-planning drift can carry more weight than low-value formatting differences.

## Authenticity Roadmap

The implementation roadmap lives in [authenticity-plan.md](/home/debian/git/model-verifier/docs/authenticity-plan.md). Steps 1 through 3 are complete and establish:

- case-level signal groups
- per-check weights
- provider-level signal summaries
- critical-signal-aware classification
- baseline pairing between upstream and reference providers
- per-case and per-signal deltas against the configured baseline
- repeat sampling with case-level stability summaries
- stability-adjusted scoring for flaky upstream providers

## Project Layout

```text
app/
  cli.py
  config.py
  db.py
  evaluation.py
  reporting.py
  server.py
  service.py
  providers/
    openai_compatible.py
prompts/
  cases.json
web/
  index.html
  app.js
  styles.css
tests/
  test_verifier.py
providers.sample.json
sample.env
```

## Quick Start

### 1. Run a local verification from the CLI

```bash
cd /home/debian/git/model-verifier
python3 -m app.cli
```

The sample provider file includes deterministic and intentionally unstable mock providers:

- `mock-reference-gpt41`
- `mock-clean-gateway`
- `mock-suspect-gateway`
- `mock-flaky-gateway`

This lets you generate reports without external network access.

To exercise repeat sampling explicitly:

```bash
cd /home/debian/git/model-verifier
python3 -m app.cli --providers mock-clean-gateway,mock-flaky-gateway --cases json_contract,context_memory,refusal_boundary,tool_plan_json --samples 3
```

### 2. Start the web UI

```bash
cd /home/debian/git/model-verifier
python3 -m app.server
```

Then open `http://127.0.0.1:8000`.

### 3. Run tests

```bash
cd /home/debian/git/model-verifier
python3 -m unittest discover -s tests
```

## Environment

The service reads configuration from exported environment variables. Example values are in [sample.env](/home/debian/git/model-verifier/sample.env).

Important variables:

- `MODEL_VERIFIER_HOST`
- `MODEL_VERIFIER_PORT`
- `MODEL_VERIFIER_PROVIDERS`
- `MODEL_VERIFIER_ALLOWED_ORIGINS`

## Provider Configuration

Provider definitions live in `providers.sample.json`. Mock providers are included for demo purposes. To point the verifier at a real upstream endpoint, replace a mock entry with an OpenAI-compatible provider definition:

```json
{
  "name": "upstream-a",
  "type": "openai_compatible",
  "base_url": "https://example.com/v1",
  "model": "gpt-4.1-mini",
  "baseline_provider": "openai-official-gpt41",
  "api_key_env": "UPSTREAM_A_API_KEY",
  "temperature": 0
}
```

The backend calls `POST {base_url}/chat/completions` and expects an OpenAI-compatible response shape.

If `baseline_provider` is set, the run automatically includes that baseline provider, computes signal-level deltas, and records per-case mismatch reasons.

If you pass `--samples N` in the CLI or `sample_count` in `POST /api/runs`, the verifier repeats each selected case `N` times and records:

- pass-rate drift across repeated samples
- check-level flips such as `json_required` or `required_keys`
- case stability states: `stable`, `style_variance`, `moderate_variance`, `high_variance`
- stability-adjusted provider scores for final classification

## API Surface

- `GET /health`
- `GET /api/config`
- `GET /api/runs`
- `POST /api/runs`
- `GET /api/runs/{run_id}`
- `GET /api/reports/{run_id}`

## Notes

- The current web UI uses polling instead of WebSocket/SSE to keep the runtime small.
- Authentication is intentionally omitted in this MVP; the goal is to demonstrate the validation pipeline first.
- Reports are written to `reports/` and run metadata is stored in `data/results.db`.
