# Model Verifier

`Model Verifier` is a lightweight AI model behavior verification toolkit for upstream APIs that claim to expose specific foundation models. It runs a fixed evaluation suite, stores results in `SQLite`, produces `Markdown` and `JSON` reports, and ships with a thin web UI for task submission and result review.

This repository is intentionally built without third-party Python packages so it can run in restricted environments and still demonstrate:

- model behavior validation against a fixed test set
- consistent result collection and reporting
- small full-stack delivery from backend to frontend

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

The sample provider file includes two deterministic mock providers:

- `mock-reference-gpt41`
- `mock-suspect-gateway`

This lets you generate reports without external network access.

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
  "api_key_env": "UPSTREAM_A_API_KEY",
  "temperature": 0
}
```

The backend calls `POST {base_url}/chat/completions` and expects an OpenAI-compatible response shape.

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

