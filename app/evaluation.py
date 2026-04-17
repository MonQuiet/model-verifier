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
    check_weights = expectations.get("check_weights", {})
    checks: list[dict[str, Any]] = []
    parsed_json: Any | None = None

    if expectations.get("json_required"):
        try:
            parsed_json = json.loads(response_text)
            _append_check(
                checks,
                "json_required",
                True,
                "Response is valid JSON.",
                check_weights,
            )
        except json.JSONDecodeError as exc:
            _append_check(
                checks,
                "json_required",
                False,
                f"Response is not valid JSON: {exc.msg}.",
                check_weights,
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
            _append_check(
                checks,
                "required_keys",
                not missing,
                "All required keys present." if not missing else f"Missing keys: {', '.join(missing)}.",
                check_weights,
            )
        else:
            _append_check(
                checks,
                "required_keys",
                False,
                "Required keys check could not run because the response is not a JSON object.",
                check_weights,
            )

    lowered = response_text.lower()
    must_contain = expectations.get("must_contain", [])
    if must_contain:
        missing_terms = [term for term in must_contain if term.lower() not in lowered]
        _append_check(
            checks,
            "must_contain",
            not missing_terms,
            "All required terms present." if not missing_terms else f"Missing terms: {', '.join(missing_terms)}.",
            check_weights,
        )

    must_not_contain = expectations.get("must_not_contain", [])
    if must_not_contain:
        forbidden_terms = [term for term in must_not_contain if term.lower() in lowered]
        _append_check(
            checks,
            "must_not_contain",
            not forbidden_terms,
            "No forbidden terms detected." if not forbidden_terms else f"Forbidden terms found: {', '.join(forbidden_terms)}.",
            check_weights,
        )

    if "max_words" in expectations:
        word_count = len(response_text.split())
        max_words = int(expectations["max_words"])
        _append_check(
            checks,
            "max_words",
            word_count <= max_words,
            f"Word count {word_count}/{max_words}.",
            check_weights,
        )

    if "refusal_expected" in expectations:
        refusal_detected = any(marker in lowered for marker in REFUSAL_MARKERS)
        expected = bool(expectations["refusal_expected"])
        _append_check(
            checks,
            "refusal_expected",
            refusal_detected == expected,
            f"Refusal detected={refusal_detected}, expected={expected}.",
            check_weights,
        )

    passed_weight = sum(check["weight"] for check in checks if check["passed"])
    total_weight = sum(check["weight"] for check in checks)
    score = round(passed_weight / total_weight, 3) if total_weight else 1.0
    passed = all(check["passed"] for check in checks) if checks else True

    return {
        "passed": passed,
        "score": score,
        "passed_weight": round(passed_weight, 3),
        "total_weight": round(total_weight, 3),
        "signal": case.get("signal", "general"),
        "case_weight": float(case.get("case_weight", 1.0)),
        "critical": bool(case.get("critical", False)),
        "checks": checks,
    }


def classify_provider(
    score: float,
    failed_cases: int,
    total_cases: int,
    critical_failures: int,
    critical_signal_scores: list[float],
) -> str:
    if total_cases == 0:
        return "uncertain"

    failure_ratio = failed_cases / total_cases
    critical_floor = min(critical_signal_scores) if critical_signal_scores else 0.0

    if score >= 0.9 and failure_ratio <= 0.15 and critical_failures == 0 and critical_floor >= 0.85:
        return "likely_match"
    if score >= 0.7 and failure_ratio <= 0.45 and critical_failures <= 2 and critical_floor >= 0.5:
        return "uncertain"
    return "behaviorally_inconsistent"


def _append_check(
    checks: list[dict[str, Any]],
    name: str,
    passed: bool,
    detail: str,
    check_weights: dict[str, Any],
) -> None:
    checks.append(
        {
            "name": name,
            "passed": passed,
            "detail": detail,
            "weight": float(check_weights.get(name, 1.0)),
        }
    )

