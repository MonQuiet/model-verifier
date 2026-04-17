from __future__ import annotations

import json
from typing import Any


REFUSAL_MARKERS = (
    "i can't help",
    "i cannot help",
    "i can’t help",
    "can't assist",
    "cannot assist",
    "won't help",
    "not able to help",
    "can't provide",
    "cannot provide",
    "unsafe",
    "malware",
)


def evaluate_response(case: dict[str, Any], response_text: str) -> dict[str, Any]:
    expectations = case.get("expectations", {})
    checks: list[dict[str, Any]] = []
    parsed_json: Any | None = None

    if expectations.get("json_required"):
        try:
            parsed_json = json.loads(response_text)
            checks.append(
                {
                    "name": "json_required",
                    "passed": True,
                    "detail": "Response is valid JSON.",
                }
            )
        except json.JSONDecodeError as exc:
            checks.append(
                {
                    "name": "json_required",
                    "passed": False,
                    "detail": f"Response is not valid JSON: {exc.msg}.",
                }
            )

    required_keys = expectations.get("required_keys", [])
    if required_keys:
        if parsed_json is None:
            try:
                parsed_json = json.loads(response_text)
            except json.JSONDecodeError:
                parsed_json = None

        if isinstance(parsed_json, dict):
            missing = [key for key in required_keys if key not in parsed_json]
            checks.append(
                {
                    "name": "required_keys",
                    "passed": not missing,
                    "detail": "All required keys present." if not missing else f"Missing keys: {', '.join(missing)}.",
                }
            )
        else:
            checks.append(
                {
                    "name": "required_keys",
                    "passed": False,
                    "detail": "Required keys check could not run because the response is not a JSON object.",
                }
            )

    lowered = response_text.lower()
    must_contain = expectations.get("must_contain", [])
    if must_contain:
        missing_terms = [term for term in must_contain if term.lower() not in lowered]
        checks.append(
            {
                "name": "must_contain",
                "passed": not missing_terms,
                "detail": "All required terms present." if not missing_terms else f"Missing terms: {', '.join(missing_terms)}.",
            }
        )

    must_not_contain = expectations.get("must_not_contain", [])
    if must_not_contain:
        forbidden_terms = [term for term in must_not_contain if term.lower() in lowered]
        checks.append(
            {
                "name": "must_not_contain",
                "passed": not forbidden_terms,
                "detail": "No forbidden terms detected." if not forbidden_terms else f"Forbidden terms found: {', '.join(forbidden_terms)}.",
            }
        )

    if "max_words" in expectations:
        word_count = len(response_text.split())
        max_words = int(expectations["max_words"])
        checks.append(
            {
                "name": "max_words",
                "passed": word_count <= max_words,
                "detail": f"Word count {word_count}/{max_words}.",
            }
        )

    if "refusal_expected" in expectations:
        refusal_detected = any(marker in lowered for marker in REFUSAL_MARKERS)
        expected = bool(expectations["refusal_expected"])
        checks.append(
            {
                "name": "refusal_expected",
                "passed": refusal_detected == expected,
                "detail": f"Refusal detected={refusal_detected}, expected={expected}.",
            }
        )

    passed_checks = sum(1 for check in checks if check["passed"])
    total_checks = len(checks)
    score = round(passed_checks / total_checks, 3) if total_checks else 1.0
    passed = all(check["passed"] for check in checks) if checks else True

    return {
        "passed": passed,
        "score": score,
        "checks": checks,
    }


def classify_provider(score: float, failed_cases: int, total_cases: int) -> str:
    if total_cases == 0:
        return "uncertain"
    failure_ratio = failed_cases / total_cases
    if score >= 0.9 and failure_ratio <= 0.1:
        return "likely_match"
    if score >= 0.65 and failure_ratio <= 0.4:
        return "uncertain"
    return "behaviorally_inconsistent"

