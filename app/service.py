from __future__ import annotations

from collections import defaultdict
from datetime import datetime, timezone
import traceback
import threading
import uuid
from typing import Any

from . import db
from .cases import load_cases, select_cases
from .config import Settings, ensure_runtime_paths
from .evaluation import classify_provider, evaluate_response
from .providers.openai_compatible import ProviderConfig, generate_completion, load_provider_configs, select_providers
from .reporting import write_reports


class VerificationService:
    def __init__(self, settings: Settings) -> None:
        ensure_runtime_paths(settings)
        db.init_db(settings)
        self.settings = settings

    def get_catalog(self) -> dict[str, Any]:
        providers = load_provider_configs(self.settings.providers_path)
        cases = load_cases(self.settings)
        return {
            "providers": [
                {
                    "name": provider.name,
                    "model": provider.model,
                    "type": provider.provider_type,
                    "base_url": provider.base_url,
                    "baseline_provider": provider.baseline_provider,
                }
                for provider in providers
            ],
            "cases": [
                {
                    "id": case["id"],
                    "title": case["title"],
                    "description": case["description"],
                }
                for case in cases
            ],
        }

    def list_runs(self) -> list[dict[str, Any]]:
        return db.list_runs(self.settings)

    def get_run(self, run_id: str) -> dict[str, Any] | None:
        return db.get_run(self.settings, run_id)

    def start_run(
        self,
        provider_names: list[str] | None = None,
        case_ids: list[str] | None = None,
        sample_count: int = 1,
    ) -> dict[str, Any]:
        selected_providers, selected_cases = self._prepare_selection(provider_names, case_ids)
        run_id = self._new_run_id()
        created_at = _utc_now()
        request_payload = {
            "requested_provider_names": provider_names or [],
            "resolved_provider_names": [provider.name for provider in selected_providers],
            "case_ids": [case["id"] for case in selected_cases],
            "sample_count": max(sample_count, 1),
        }
        db.create_run(self.settings, run_id, "queued", created_at, request_payload)

        worker = threading.Thread(
            target=self._execute_run,
            args=(run_id, selected_providers, selected_cases, max(sample_count, 1)),
            daemon=True,
            name=f"run-{run_id}",
        )
        worker.start()
        run_payload = self.get_run(run_id)
        if run_payload is None:
            raise RuntimeError("Failed to load queued run after creation.")
        return run_payload

    def run_sync(
        self,
        provider_names: list[str] | None = None,
        case_ids: list[str] | None = None,
        sample_count: int = 1,
    ) -> dict[str, Any]:
        selected_providers, selected_cases = self._prepare_selection(provider_names, case_ids)
        run_id = self._new_run_id()
        created_at = _utc_now()
        request_payload = {
            "requested_provider_names": provider_names or [],
            "resolved_provider_names": [provider.name for provider in selected_providers],
            "case_ids": [case["id"] for case in selected_cases],
            "sample_count": max(sample_count, 1),
        }
        db.create_run(self.settings, run_id, "queued", created_at, request_payload)
        self._execute_run(run_id, selected_providers, selected_cases, max(sample_count, 1))
        run_payload = self.get_run(run_id)
        if run_payload is None:
            raise RuntimeError("Failed to load completed run.")
        return run_payload

    def _prepare_selection(
        self,
        provider_names: list[str] | None,
        case_ids: list[str] | None,
    ) -> tuple[list[ProviderConfig], list[dict[str, Any]]]:
        providers = load_provider_configs(self.settings.providers_path)
        cases = load_cases(self.settings)
        selected_providers = _resolve_selected_providers_with_baselines(
            providers,
            select_providers(providers, provider_names),
        )
        selected_cases = select_cases(cases, case_ids)
        return selected_providers, selected_cases

    def _execute_run(
        self,
        run_id: str,
        selected_providers: list[ProviderConfig],
        selected_cases: list[dict[str, Any]],
        sample_count: int,
    ) -> None:
        db.update_run_status(self.settings, run_id, "running", _utc_now())
        records: list[dict[str, Any]] = []

        try:
            for provider in selected_providers:
                for case in selected_cases:
                    for sample_index in range(sample_count):
                        created_at = _utc_now()
                        try:
                            completion = generate_completion(provider, case, sample_index=sample_index)
                            evaluation = evaluate_response(case, completion["text"])
                            status = "passed" if evaluation["passed"] else "failed"
                            latency_ms = completion["latency_ms"]
                            response_text = completion["text"]
                            raw = {
                                **completion,
                                "sample_index": sample_index,
                            }
                        except Exception as exc:
                            evaluation = {
                                "passed": False,
                                "score": 0.0,
                                "passed_weight": 0.0,
                                "total_weight": 0.0,
                                "signal": case.get("signal", "general"),
                                "case_weight": float(case.get("case_weight", 1.0)),
                                "critical": bool(case.get("critical", False)),
                                "checks": [
                                    {
                                        "name": "request_error",
                                        "passed": False,
                                        "detail": str(exc),
                                        "weight": 1.0,
                                    }
                                ],
                            }
                            status = "error"
                            latency_ms = 0
                            response_text = ""
                            raw = {
                                "error": str(exc),
                                "sample_index": sample_index,
                                "traceback": traceback.format_exc(limit=3),
                            }

                        record = {
                            "run_id": run_id,
                            "provider_name": provider.name,
                            "provider_model": provider.model,
                            "case_id": case["id"],
                            "case_title": case["title"],
                            "status": status,
                            "score": evaluation["score"],
                            "latency_ms": latency_ms,
                            "response_text": response_text,
                            "evaluation": evaluation,
                            "raw": raw,
                            "sample_index": sample_index,
                            "created_at": created_at,
                        }
                        db.insert_case_result(self.settings, record)
                        records.append(record)

            summary = _build_summary(run_id, selected_providers, selected_cases, records, sample_count)
            run_payload = self.get_run(run_id)
            if run_payload is None:
                raise RuntimeError("Run disappeared before report generation.")
            run_payload["summary"] = summary
            run_payload["results"] = records

            report_paths = write_reports(self.settings, run_payload, selected_cases)
            db.finalize_run(
                self.settings,
                run_id,
                "completed",
                _utc_now(),
                summary,
                report_paths["markdown_path"],
                report_paths["json_path"],
            )
        except Exception as exc:
            db.update_run_status(self.settings, run_id, "failed", _utc_now(), error_text=str(exc))

    def _new_run_id(self) -> str:
        return uuid.uuid4().hex[:12]


def _build_summary(
    run_id: str,
    selected_providers: list[ProviderConfig],
    selected_cases: list[dict[str, Any]],
    records: list[dict[str, Any]],
    sample_count: int,
) -> dict[str, Any]:
    provider_lookup = {provider.name: provider for provider in selected_providers}
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for record in records:
        grouped[record["provider_name"]].append(record)

    provider_summaries: list[dict[str, Any]] = []
    provider_summary_lookup: dict[str, dict[str, Any]] = {}
    for provider in selected_providers:
        provider_records = grouped.get(provider.name, [])
        case_rollups = _build_case_rollups(provider_records)
        total_cases = len(case_rollups)
        passed_cases = sum(1 for rollup in case_rollups if rollup["status"] == "passed")
        failed_cases = total_cases - passed_cases
        total_case_weight = sum(rollup["case_weight"] for rollup in case_rollups)
        average_score = round(
            (
                sum(rollup["average_score"] * rollup["case_weight"] for rollup in case_rollups) / total_case_weight
            )
            if total_case_weight
            else 0.0,
            3,
        )
        stability_penalty = round(
            (
                sum(rollup["stability_penalty"] * rollup["case_weight"] for rollup in case_rollups) / total_case_weight
            )
            if total_case_weight
            else 0.0,
            3,
        )
        adjusted_score = round(max(average_score - stability_penalty, 0.0), 3)
        average_latency_ms = round(
            sum(record["latency_ms"] for record in provider_records) / len(provider_records) if provider_records else 0.0,
            1,
        )
        signal_summaries = _build_signal_summaries(case_rollups)
        unstable_cases = sum(1 for rollup in case_rollups if _is_instability_case(rollup["stability"]))
        critical_failures = sum(
            1 for rollup in case_rollups if rollup["critical"] and rollup["status"] != "passed"
        )
        critical_unstable_cases = sum(
            1
            for rollup in case_rollups
            if rollup["critical"] and _is_instability_case(rollup["stability"])
        )
        style_variance_cases = sum(1 for rollup in case_rollups if rollup["stability"] == "style_variance")
        critical_signal_scores = [
            signal_summary["adjusted_weighted_score"]
            for signal_summary in signal_summaries
            if signal_summary["critical"]
        ]
        classification = classify_provider(
            adjusted_score,
            failed_cases,
            max(total_cases, 1),
            critical_failures,
            critical_signal_scores,
        )
        classification = _apply_stability_adjustment(
            classification,
            unstable_cases,
            critical_unstable_cases,
            total_cases,
        )
        failures = _build_failure_notes(case_rollups)

        provider_summary = {
            "provider_name": provider.name,
            "provider_model": provider.model,
            "sample_count": sample_count,
            "average_score": average_score,
            "stability_penalty": stability_penalty,
            "adjusted_score": adjusted_score,
            "average_latency_ms": average_latency_ms,
            "passed_cases": passed_cases,
            "failed_cases": failed_cases,
            "critical_failures": critical_failures,
            "unstable_cases": unstable_cases,
            "critical_unstable_cases": critical_unstable_cases,
            "style_variance_cases": style_variance_cases,
            "signal_summaries": signal_summaries,
            "case_rollups": case_rollups,
            "classification": classification,
            "diagnosis": _diagnosis_for(
                classification,
                failures,
                unstable_cases,
                critical_unstable_cases,
                sample_count,
            ),
            "comparison_summary": None,
        }
        provider_summaries.append(provider_summary)
        provider_summary_lookup[provider.name] = provider_summary

    for provider_summary in provider_summaries:
        provider = provider_lookup[provider_summary["provider_name"]]
        if not provider.baseline_provider:
            continue

        baseline_summary = provider_summary_lookup.get(provider.baseline_provider)
        comparison_summary = _build_baseline_comparison_summary(
            provider_case_rollups=provider_summary["case_rollups"],
            provider_summary=provider_summary,
            baseline_provider_name=provider.baseline_provider,
            baseline_summary=baseline_summary,
            baseline_case_rollups=baseline_summary.get("case_rollups", []) if baseline_summary else [],
        )
        provider_summary["comparison_summary"] = comparison_summary
        provider_summary["classification"] = _apply_baseline_alignment(
            provider_summary["classification"],
            comparison_summary["alignment"],
        )
        provider_summary["diagnosis"] = (
            f"{provider_summary['diagnosis']} Baseline comparison vs {provider.baseline_provider}: "
            f"{comparison_summary['diagnosis']}"
        )

    return {
        "run_id": run_id,
        "sample_count": sample_count,
        "total_providers": len(selected_providers),
        "total_cases": len(selected_cases),
        "total_results": len(records),
        "provider_summaries": provider_summaries,
    }


def _build_case_rollups(provider_records: list[dict[str, Any]]) -> list[dict[str, Any]]:
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for record in provider_records:
        grouped[record["case_id"]].append(record)

    rollups: list[dict[str, Any]] = []
    for case_id in sorted(grouped):
        attempts = sorted(grouped[case_id], key=lambda record: record.get("sample_index", 0))
        exemplar = attempts[0]
        sample_count = len(attempts)
        pass_count = sum(1 for attempt in attempts if attempt["status"] == "passed")
        failed_attempts = sum(1 for attempt in attempts if attempt["status"] == "failed")
        error_attempts = sum(1 for attempt in attempts if attempt["status"] == "error")
        scores = [float(attempt["score"]) for attempt in attempts]
        average_score = round(sum(scores) / sample_count if sample_count else 0.0, 3)
        min_score = round(min(scores) if scores else 0.0, 3)
        max_score = round(max(scores) if scores else 0.0, 3)
        score_spread = round(max_score - min_score, 3)
        response_variants = len(
            {
                _response_fingerprint(attempt["response_text"])
                for attempt in attempts
                if attempt["response_text"].strip()
            }
        )
        check_flips = _build_check_flips(attempts)
        stability = _classify_case_stability(
            pass_count,
            sample_count,
            score_spread,
            response_variants,
            check_flips,
            exemplar["evaluation"].get("critical", False),
        )
        stability_penalty = _stability_penalty(stability, exemplar["evaluation"].get("critical", False))
        adjusted_score = round(max(average_score - stability_penalty, 0.0), 3)

        rollups.append(
            {
                "case_id": case_id,
                "case_title": exemplar["case_title"],
                "signal": exemplar["evaluation"].get("signal", "general"),
                "critical": exemplar["evaluation"].get("critical", False),
                "case_weight": float(exemplar["evaluation"].get("case_weight", 1.0)),
                "sample_count": sample_count,
                "status": _rollup_status(pass_count, failed_attempts, error_attempts, sample_count),
                "passed_attempts": pass_count,
                "failed_attempts": failed_attempts,
                "error_attempts": error_attempts,
                "pass_rate": round(pass_count / sample_count if sample_count else 0.0, 3),
                "average_score": average_score,
                "adjusted_score": adjusted_score,
                "min_score": min_score,
                "max_score": max_score,
                "score_spread": score_spread,
                "stability": stability,
                "stability_penalty": stability_penalty,
                "response_variants": response_variants,
                "check_flips": check_flips,
                "dominant_failures": _collect_failure_reasons(attempts),
                "attempts": [
                    {
                        "sample_index": attempt.get("sample_index", 0),
                        "status": attempt["status"],
                        "score": attempt["score"],
                        "latency_ms": attempt["latency_ms"],
                        "failed_checks": [
                            check["name"]
                            for check in attempt["evaluation"].get("checks", [])
                            if not check.get("passed", False)
                        ],
                    }
                    for attempt in attempts
                ],
            }
        )

    return rollups


def _diagnosis_for(
    classification: str,
    failures: list[str],
    unstable_cases: int,
    critical_unstable_cases: int,
    sample_count: int,
) -> str:
    if classification == "likely_match":
        if sample_count > 1:
            return "Weighted evidence stayed strong across repeated samples and all critical signals."
        return "Weighted evidence stayed strong across all critical signals."
    if classification == "uncertain":
        reasons: list[str] = []
        if critical_unstable_cases:
            reasons.append(f"{critical_unstable_cases} critical cases drifted across repeated samples.")
        elif unstable_cases:
            reasons.append(f"{unstable_cases} cases showed instability across repeated samples.")
        if failures:
            reasons.append("Review the first issues: " + "; ".join(failures[:2]))
        return "Mixed evidence detected. " + " ".join(reasons) if reasons else "Mixed signals detected without a decisive mismatch."
    if failures:
        return "Critical evidence drift detected. Review failing cases: " + "; ".join(failures[:3])
    return "Behavior diverged from the expected baseline."


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _build_signal_summaries(case_rollups: list[dict[str, Any]]) -> list[dict[str, Any]]:
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for rollup in case_rollups:
        grouped[rollup["signal"]].append(rollup)

    summaries: list[dict[str, Any]] = []
    for signal_name, signal_rollups in sorted(grouped.items()):
        total_case_weight = sum(rollup["case_weight"] for rollup in signal_rollups)
        weighted_score = round(
            (
                sum(rollup["average_score"] * rollup["case_weight"] for rollup in signal_rollups) / total_case_weight
            )
            if total_case_weight
            else 0.0,
            3,
        )
        stability_penalty = round(
            (
                sum(rollup["stability_penalty"] * rollup["case_weight"] for rollup in signal_rollups) / total_case_weight
            )
            if total_case_weight
            else 0.0,
            3,
        )
        summaries.append(
            {
                "signal": signal_name,
                "critical": any(rollup["critical"] for rollup in signal_rollups),
                "weighted_score": weighted_score,
                "adjusted_weighted_score": round(max(weighted_score - stability_penalty, 0.0), 3),
                "stability_penalty": stability_penalty,
                "failed_cases": sum(1 for rollup in signal_rollups if rollup["status"] != "passed"),
                "unstable_cases": sum(1 for rollup in signal_rollups if _is_instability_case(rollup["stability"])),
                "style_variance_cases": sum(1 for rollup in signal_rollups if rollup["stability"] == "style_variance"),
                "total_cases": len(signal_rollups),
            }
        )
    return summaries


def _resolve_selected_providers_with_baselines(
    all_providers: list[ProviderConfig],
    selected_providers: list[ProviderConfig],
) -> list[ProviderConfig]:
    lookup = {provider.name: provider for provider in all_providers}
    resolved: list[ProviderConfig] = []
    seen: set[str] = set()

    def add_provider(provider: ProviderConfig) -> None:
        if provider.name in seen:
            return
        resolved.append(provider)
        seen.add(provider.name)
        if provider.baseline_provider:
            baseline_provider = lookup.get(provider.baseline_provider)
            if baseline_provider is None:
                raise ValueError(
                    f"Provider {provider.name} references missing baseline provider: {provider.baseline_provider}"
                )
            add_provider(baseline_provider)

    for provider in selected_providers:
        add_provider(provider)

    return resolved


def _build_baseline_comparison_summary(
    provider_case_rollups: list[dict[str, Any]],
    provider_summary: dict[str, Any],
    baseline_provider_name: str,
    baseline_summary: dict[str, Any] | None,
    baseline_case_rollups: list[dict[str, Any]],
) -> dict[str, Any]:
    baseline_by_case = {rollup["case_id"]: rollup for rollup in baseline_case_rollups}
    case_deltas: list[dict[str, Any]] = []
    mismatch_cases = 0
    critical_mismatch_cases = 0

    for provider_rollup in provider_case_rollups:
        baseline_rollup = baseline_by_case.get(provider_rollup["case_id"])
        mismatch_reasons = _build_case_mismatch_reasons(provider_rollup, baseline_rollup)
        matched = not mismatch_reasons
        if not matched:
            mismatch_cases += 1
            if provider_rollup["critical"]:
                critical_mismatch_cases += 1

        baseline_score = baseline_rollup["adjusted_score"] if baseline_rollup else 0.0
        case_deltas.append(
            {
                "case_id": provider_rollup["case_id"],
                "case_title": provider_rollup["case_title"],
                "signal": provider_rollup["signal"],
                "critical": provider_rollup["critical"],
                "provider_status": provider_rollup["status"],
                "baseline_status": baseline_rollup["status"] if baseline_rollup else "missing",
                "provider_score": provider_rollup["adjusted_score"],
                "baseline_score": baseline_score,
                "score_delta": round(provider_rollup["adjusted_score"] - baseline_score, 3),
                "provider_stability": provider_rollup["stability"],
                "baseline_stability": baseline_rollup["stability"] if baseline_rollup else "missing",
                "matched": matched,
                "mismatch_reasons": mismatch_reasons,
            }
        )

    signal_deltas = _build_signal_deltas(
        provider_summary.get("signal_summaries", []),
        baseline_summary.get("signal_summaries", []) if baseline_summary else [],
    )
    weighted_score_delta = round(
        provider_summary["adjusted_score"] - (baseline_summary["adjusted_score"] if baseline_summary else 0.0),
        3,
    )
    stability_penalty_delta = round(
        provider_summary["stability_penalty"] - (baseline_summary["stability_penalty"] if baseline_summary else 0.0),
        3,
    )
    alignment = _classify_baseline_alignment(weighted_score_delta, mismatch_cases, critical_mismatch_cases, signal_deltas)

    return {
        "baseline_provider_name": baseline_provider_name,
        "baseline_provider_model": baseline_summary["provider_model"] if baseline_summary else "unknown",
        "alignment": alignment,
        "weighted_score_delta": weighted_score_delta,
        "stability_penalty_delta": stability_penalty_delta,
        "mismatch_cases": mismatch_cases,
        "critical_mismatch_cases": critical_mismatch_cases,
        "signal_deltas": signal_deltas,
        "case_deltas": case_deltas,
        "diagnosis": _comparison_diagnosis(
            alignment,
            weighted_score_delta,
            mismatch_cases,
            critical_mismatch_cases,
            stability_penalty_delta,
        ),
    }


def _build_case_mismatch_reasons(
    provider_rollup: dict[str, Any],
    baseline_rollup: dict[str, Any] | None,
) -> list[str]:
    if baseline_rollup is None:
        return ["baseline result missing"]

    reasons: list[str] = []
    if provider_rollup["status"] != baseline_rollup["status"]:
        reasons.append(f"status drift: {baseline_rollup['status']} -> {provider_rollup['status']}")

    score_delta = provider_rollup["adjusted_score"] - baseline_rollup["adjusted_score"]
    if abs(score_delta) >= 0.15:
        reasons.append(f"score delta {score_delta:+.2f}")

    pass_rate_delta = provider_rollup["pass_rate"] - baseline_rollup["pass_rate"]
    if abs(pass_rate_delta) >= 0.34:
        reasons.append(f"pass-rate delta {pass_rate_delta:+.2f}")

    if provider_rollup["stability"] != baseline_rollup["stability"]:
        reasons.append(f"stability drift: {baseline_rollup['stability']} -> {provider_rollup['stability']}")

    changed_flips = sorted(set(provider_rollup["check_flips"]) ^ set(baseline_rollup["check_flips"]))
    if changed_flips:
        reasons.append("check instability: " + ", ".join(changed_flips))

    return reasons


def _build_signal_deltas(
    provider_signal_summaries: list[dict[str, Any]],
    baseline_signal_summaries: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    baseline_lookup = {item["signal"]: item for item in baseline_signal_summaries}
    deltas: list[dict[str, Any]] = []

    for provider_signal in provider_signal_summaries:
        baseline_signal = baseline_lookup.get(provider_signal["signal"])
        baseline_score = baseline_signal["adjusted_weighted_score"] if baseline_signal else 0.0
        deltas.append(
            {
                "signal": provider_signal["signal"],
                "critical": provider_signal["critical"],
                "provider_score": provider_signal["adjusted_weighted_score"],
                "baseline_score": baseline_score,
                "score_delta": round(provider_signal["adjusted_weighted_score"] - baseline_score, 3),
            }
        )

    return deltas


def _classify_baseline_alignment(
    weighted_score_delta: float,
    mismatch_cases: int,
    critical_mismatch_cases: int,
    signal_deltas: list[dict[str, Any]],
) -> str:
    worst_critical_delta = min(
        (signal_delta["score_delta"] for signal_delta in signal_deltas if signal_delta["critical"]),
        default=0.0,
    )
    if mismatch_cases == 0 and weighted_score_delta >= -0.1 and worst_critical_delta >= -0.1:
        return "aligned"
    if critical_mismatch_cases <= 1 and weighted_score_delta >= -0.35 and worst_critical_delta >= -0.35:
        return "partial_drift"
    return "strong_drift"


def _comparison_diagnosis(
    alignment: str,
    weighted_score_delta: float,
    mismatch_cases: int,
    critical_mismatch_cases: int,
    stability_penalty_delta: float,
) -> str:
    if alignment == "aligned":
        return (
            f"Aligned with baseline. Adjusted delta {weighted_score_delta:+.2f} and "
            f"stability penalty delta {stability_penalty_delta:+.2f}."
        )
    if alignment == "partial_drift":
        return (
            f"Partial drift detected. Adjusted delta {weighted_score_delta:+.2f}, "
            f"{mismatch_cases} mismatched cases, {critical_mismatch_cases} critical, "
            f"stability penalty delta {stability_penalty_delta:+.2f}."
        )
    return (
        f"Strong drift detected. Adjusted delta {weighted_score_delta:+.2f}, "
        f"{mismatch_cases} mismatched cases, {critical_mismatch_cases} critical, "
        f"stability penalty delta {stability_penalty_delta:+.2f}."
    )


def _apply_baseline_alignment(classification: str, alignment: str) -> str:
    if alignment == "strong_drift":
        return "behaviorally_inconsistent"
    if alignment == "partial_drift" and classification == "likely_match":
        return "uncertain"
    return classification


def _apply_stability_adjustment(
    classification: str,
    unstable_cases: int,
    critical_unstable_cases: int,
    total_cases: int,
) -> str:
    if classification == "behaviorally_inconsistent":
        return classification
    if critical_unstable_cases >= 2:
        return "behaviorally_inconsistent"
    if classification == "likely_match" and unstable_cases >= 1:
        return "uncertain"
    if classification == "uncertain" and total_cases and unstable_cases >= max(2, total_cases // 2 + total_cases % 2):
        return "behaviorally_inconsistent"
    return classification


def _build_check_flips(attempts: list[dict[str, Any]]) -> list[str]:
    outcomes: dict[str, set[bool]] = defaultdict(set)
    for attempt in attempts:
        for check in attempt["evaluation"].get("checks", []):
            outcomes[check["name"]].add(bool(check.get("passed", False)))
    return sorted(name for name, values in outcomes.items() if len(values) > 1)


def _classify_case_stability(
    pass_count: int,
    sample_count: int,
    score_spread: float,
    response_variants: int,
    check_flips: list[str],
    critical: bool,
) -> str:
    if sample_count <= 1:
        return "stable"
    if 0 < pass_count < sample_count:
        return "high_variance" if critical else "moderate_variance"
    if check_flips or score_spread >= 0.35:
        return "high_variance" if critical or len(check_flips) > 1 else "moderate_variance"
    if response_variants > 1 or score_spread >= 0.1:
        return "style_variance"
    return "stable"


def _stability_penalty(stability: str, critical: bool) -> float:
    if stability == "moderate_variance":
        return 0.12 if critical else 0.08
    if stability == "high_variance":
        return 0.25 if critical else 0.15
    return 0.0


def _rollup_status(pass_count: int, failed_attempts: int, error_attempts: int, sample_count: int) -> str:
    if sample_count and pass_count == sample_count:
        return "passed"
    if sample_count and error_attempts == sample_count:
        return "error"
    if sample_count and failed_attempts == sample_count:
        return "failed"
    if pass_count == 0 and failed_attempts >= error_attempts:
        return "failed"
    return "flaky"


def _collect_failure_reasons(attempts: list[dict[str, Any]]) -> list[str]:
    reasons: list[str] = []
    seen: set[str] = set()
    for attempt in attempts:
        if attempt["status"] == "error":
            detail = attempt.get("raw", {}).get("error", "request error")
            if detail not in seen:
                reasons.append(detail)
                seen.add(detail)
            continue
        for check in attempt["evaluation"].get("checks", []):
            if check.get("passed", False):
                continue
            detail = f"{check['name']}: {check['detail']}"
            if detail in seen:
                continue
            reasons.append(detail)
            seen.add(detail)
    return reasons[:3]


def _build_failure_notes(case_rollups: list[dict[str, Any]]) -> list[str]:
    notes: list[str] = []
    for rollup in case_rollups:
        if rollup["status"] == "passed" and not _is_instability_case(rollup["stability"]):
            continue
        if rollup["status"] == "flaky":
            detail = (
                f"{rollup['case_id']}: unstable across {rollup['sample_count']} samples "
                f"(pass rate {rollup['pass_rate']:.2f})"
            )
            if rollup["check_flips"]:
                detail += f", check flips: {', '.join(rollup['check_flips'])}"
            notes.append(detail)
            continue
        if rollup["dominant_failures"]:
            notes.append(f"{rollup['case_id']}: {rollup['dominant_failures'][0]}")
            continue
        if _is_instability_case(rollup["stability"]):
            notes.append(f"{rollup['case_id']}: {rollup['stability']}")
    return notes


def _response_fingerprint(value: str) -> str:
    return " ".join(value.lower().split())


def _is_instability_case(stability: str) -> bool:
    return stability in {"moderate_variance", "high_variance"}
