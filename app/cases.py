from __future__ import annotations

import json
from typing import Any

from .config import Settings


def load_cases(settings: Settings) -> list[dict[str, Any]]:
    payload = json.loads(settings.cases_path.read_text(encoding="utf-8"))
    cases = payload.get("cases", [])
    if not isinstance(cases, list) or not cases:
        raise ValueError("cases.json must contain a non-empty 'cases' array")
    return cases


def select_cases(all_cases: list[dict[str, Any]], case_ids: list[str] | None) -> list[dict[str, Any]]:
    if not case_ids:
        return all_cases

    lookup = {case["id"]: case for case in all_cases}
    selected: list[dict[str, Any]] = []
    missing: list[str] = []

    for case_id in case_ids:
        case = lookup.get(case_id)
        if case is None:
            missing.append(case_id)
            continue
        selected.append(case)

    if missing:
        raise ValueError(f"Unknown case ids: {', '.join(missing)}")
    return selected

