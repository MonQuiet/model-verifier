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


CRITICAL_PROTOCOL_ISSUES = {
    "missing_choices",
    "missing_message",
    "missing_content",
    "missing_finish_reason",
    "unsupported_content_block",
    "invalid_tool_calls",
}


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
                                "protocol_evidence": {
                                    "protocol_score": 0.0,
                                    "issues": ["request_error"],
                                    "finish_reason": None,
                                    "has_finish_reason": False,
                                    "usage_present": False,
                                    "usage_keys": [],
                                    "content_mode": "missing",
                                    "content_block_types": [],
                                    "tool_call_shape": "none",
                                    "tool_call_count": 0,
                                },
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
        protocol_summary = _build_provider_protocol_summary(case_rollups)
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
        classification = _apply_protocol_adjustment(classification, protocol_summary)
        failures = _build_failure_notes(case_rollups)
        critical_findings = _build_critical_findings(case_rollups)

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
            "protocol_summary": protocol_summary,
            "critical_findings": critical_findings,
            "case_rollups": case_rollups,
            "classification": classification,
            "diagnosis": _diagnosis_for(
                classification,
                failures,
                unstable_cases,
                critical_unstable_cases,
                sample_count,
                protocol_summary,
            ),
            "evidence_trail": [],
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

    for provider_summary in provider_summaries:
        provider_summary["evidence_trail"] = _build_evidence_trail(provider_summary)

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
        protocol_summary = _build_case_protocol_summary(attempts, exemplar["evaluation"].get("critical", False))
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
                "protocol_summary": protocol_summary,
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
                        "protocol_score": _attempt_protocol_evidence(attempt)["protocol_score"],
                        "finish_reason": _attempt_protocol_evidence(attempt)["finish_reason"] or "missing",
                        "content_mode": _attempt_protocol_evidence(attempt)["content_mode"],
                        "tool_call_shape": _attempt_protocol_evidence(attempt)["tool_call_shape"],
                        "usage_present": _attempt_protocol_evidence(attempt)["usage_present"],
                        "issues": _attempt_protocol_evidence(attempt)["issues"],
                    }
                    for attempt in attempts
                ],
            }
        )

    return rollups


def _build_case_protocol_summary(attempts: list[dict[str, Any]], critical: bool) -> dict[str, Any]:
    evidences = [_attempt_protocol_evidence(attempt) for attempt in attempts]
    sample_count = len(evidences)
    protocol_score = round(
        sum(evidence["protocol_score"] for evidence in evidences) / sample_count if sample_count else 0.0,
        3,
    )
    issue_types = sorted({issue for evidence in evidences for issue in evidence.get("issues", [])})
    critical_issue_types = sorted(issue for issue in issue_types if issue in CRITICAL_PROTOCOL_ISSUES)
    issue_attempts = sum(1 for evidence in evidences if evidence.get("issues"))
    usage_coverage = round(
        sum(1 for evidence in evidences if evidence.get("usage_present")) / sample_count if sample_count else 0.0,
        3,
    )
    finish_reason_coverage = round(
        sum(1 for evidence in evidences if evidence.get("has_finish_reason")) / sample_count if sample_count else 0.0,
        3,
    )
    finish_reason_markers = [evidence.get("finish_reason") or "missing" for evidence in evidences]
    finish_reason_variants = len(set(finish_reason_markers))
    content_modes = [evidence.get("content_mode", "missing") for evidence in evidences]
    tool_call_shapes = [evidence.get("tool_call_shape", "none") for evidence in evidences]
    shape_variance = len(set(content_modes)) > 1 or len(set(tool_call_shapes)) > 1 or finish_reason_variants > 1

    if not issue_types:
        alignment = "compatible"
    elif critical_issue_types or protocol_score < (0.72 if critical else 0.65):
        alignment = "major_drift"
    else:
        alignment = "minor_drift"

    return {
        "protocol_score": protocol_score,
        "alignment": alignment,
        "issue_attempts": issue_attempts,
        "usage_coverage": usage_coverage,
        "finish_reason_coverage": finish_reason_coverage,
        "finish_reasons": sorted(set(finish_reason_markers)),
        "dominant_finish_reason": _dominant_value(finish_reason_markers, "missing"),
        "dominant_content_mode": _dominant_value(content_modes, "missing"),
        "content_mode_variants": len(set(content_modes)),
        "content_block_types": sorted(
            {
                block_type
                for evidence in evidences
                for block_type in evidence.get("content_block_types", [])
            }
        ),
        "dominant_tool_call_shape": _dominant_value(tool_call_shapes, "none"),
        "tool_call_shape_variants": len(set(tool_call_shapes)),
        "tool_call_attempts": sum(1 for evidence in evidences if evidence.get("tool_call_count", 0) > 0),
        "invalid_tool_call_attempts": sum(
            1
            for evidence in evidences
            if evidence.get("tool_call_shape") in {"invalid", "mixed"}
        ),
        "issue_types": issue_types,
        "critical_issue_types": critical_issue_types,
        "shape_variance": shape_variance,
        "diagnosis": _protocol_case_diagnosis(alignment, issue_types, issue_attempts, sample_count),
    }


def _build_provider_protocol_summary(case_rollups: list[dict[str, Any]]) -> dict[str, Any]:
    total_case_weight = sum(rollup["case_weight"] for rollup in case_rollups)
    protocol_score = round(
        (
            sum(rollup["protocol_summary"]["protocol_score"] * rollup["case_weight"] for rollup in case_rollups)
            / total_case_weight
        )
        if total_case_weight
        else 0.0,
        3,
    )
    flagged_cases = sum(
        1
        for rollup in case_rollups
        if rollup["protocol_summary"]["alignment"] != "compatible"
    )
    major_drift_cases = sum(
        1
        for rollup in case_rollups
        if rollup["protocol_summary"]["alignment"] == "major_drift"
    )
    critical_major_drift_cases = sum(
        1
        for rollup in case_rollups
        if rollup["critical"] and rollup["protocol_summary"]["alignment"] == "major_drift"
    )
    critical_cases_with_drift = sum(
        1
        for rollup in case_rollups
        if rollup["critical"] and rollup["protocol_summary"]["alignment"] != "compatible"
    )
    missing_usage_cases = sum(
        1 for rollup in case_rollups if rollup["protocol_summary"]["usage_coverage"] < 1.0
    )
    missing_finish_reason_cases = sum(
        1 for rollup in case_rollups if rollup["protocol_summary"]["finish_reason_coverage"] < 1.0
    )
    invalid_tool_call_cases = sum(
        1 for rollup in case_rollups if "invalid_tool_calls" in rollup["protocol_summary"]["issue_types"]
    )
    unsupported_content_block_cases = sum(
        1
        for rollup in case_rollups
        if "unsupported_content_block" in rollup["protocol_summary"]["issue_types"]
    )
    issue_types = sorted(
        {
            issue
            for rollup in case_rollups
            for issue in rollup["protocol_summary"]["issue_types"]
        }
    )

    if major_drift_cases >= 1 or protocol_score < 0.75:
        alignment = "major_drift"
    elif flagged_cases >= 1 or protocol_score < 0.95:
        alignment = "minor_drift"
    else:
        alignment = "compatible"

    return {
        "protocol_score": protocol_score,
        "alignment": alignment,
        "flagged_cases": flagged_cases,
        "major_drift_cases": major_drift_cases,
        "critical_major_drift_cases": critical_major_drift_cases,
        "critical_cases_with_drift": critical_cases_with_drift,
        "missing_usage_cases": missing_usage_cases,
        "missing_finish_reason_cases": missing_finish_reason_cases,
        "invalid_tool_call_cases": invalid_tool_call_cases,
        "unsupported_content_block_cases": unsupported_content_block_cases,
        "issue_types": issue_types,
        "diagnosis": _protocol_provider_diagnosis(
            alignment,
            protocol_score,
            flagged_cases,
            major_drift_cases,
            issue_types,
        ),
    }


def _diagnosis_for(
    classification: str,
    failures: list[str],
    unstable_cases: int,
    critical_unstable_cases: int,
    sample_count: int,
    protocol_summary: dict[str, Any],
) -> str:
    if classification == "likely_match":
        reasons = []
        if sample_count > 1:
            reasons.append("Weighted evidence stayed strong across repeated samples.")
        else:
            reasons.append("Weighted evidence stayed strong.")
        reasons.append("Protocol evidence matched expected OpenAI-compatible fields.")
        return " ".join(reasons)

    if classification == "uncertain":
        reasons: list[str] = []
        if critical_unstable_cases:
            reasons.append(f"{critical_unstable_cases} critical cases drifted across repeated samples.")
        elif unstable_cases:
            reasons.append(f"{unstable_cases} cases showed instability across repeated samples.")
        if protocol_summary["alignment"] != "compatible":
            reasons.append(protocol_summary["diagnosis"])
        if failures:
            reasons.append("Review the first issues: " + "; ".join(failures[:2]))
        return "Mixed evidence detected. " + " ".join(reasons) if reasons else "Mixed signals detected without a decisive mismatch."

    reasons = []
    if protocol_summary["alignment"] == "major_drift":
        reasons.append(protocol_summary["diagnosis"])
    if failures:
        reasons.append("Review failing cases: " + "; ".join(failures[:3]))
    elif unstable_cases:
        reasons.append(f"{unstable_cases} cases were unstable across repeated samples.")
    return "Critical evidence drift detected. " + " ".join(reasons) if reasons else "Behavior diverged from the expected baseline."


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
        baseline_protocol = baseline_rollup["protocol_summary"] if baseline_rollup else {}
        provider_protocol = provider_rollup["protocol_summary"]
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
                "provider_protocol_alignment": provider_protocol.get("alignment", "unknown"),
                "baseline_protocol_alignment": baseline_protocol.get("alignment", "missing"),
                "provider_protocol_score": provider_protocol.get("protocol_score", 0.0),
                "baseline_protocol_score": baseline_protocol.get("protocol_score", 0.0),
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
    protocol_score_delta = round(
        provider_summary["protocol_summary"]["protocol_score"]
        - (baseline_summary["protocol_summary"]["protocol_score"] if baseline_summary else 0.0),
        3,
    )
    alignment = _classify_baseline_alignment(weighted_score_delta, mismatch_cases, critical_mismatch_cases, signal_deltas)

    return {
        "baseline_provider_name": baseline_provider_name,
        "baseline_provider_model": baseline_summary["provider_model"] if baseline_summary else "unknown",
        "alignment": alignment,
        "weighted_score_delta": weighted_score_delta,
        "stability_penalty_delta": stability_penalty_delta,
        "protocol_score_delta": protocol_score_delta,
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
            protocol_score_delta,
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

    provider_protocol = provider_rollup["protocol_summary"]
    baseline_protocol = baseline_rollup["protocol_summary"]
    if provider_protocol["alignment"] != baseline_protocol["alignment"]:
        reasons.append(
            f"protocol drift: {baseline_protocol['alignment']} -> {provider_protocol['alignment']}"
        )

    protocol_delta = provider_protocol["protocol_score"] - baseline_protocol["protocol_score"]
    if abs(protocol_delta) >= 0.15:
        reasons.append(f"protocol delta {protocol_delta:+.2f}")

    usage_delta = provider_protocol["usage_coverage"] - baseline_protocol["usage_coverage"]
    if abs(usage_delta) >= 0.34:
        reasons.append(f"usage coverage delta {usage_delta:+.2f}")

    finish_reason_delta = provider_protocol["finish_reason_coverage"] - baseline_protocol["finish_reason_coverage"]
    if abs(finish_reason_delta) >= 0.34:
        reasons.append(f"finish_reason coverage delta {finish_reason_delta:+.2f}")

    new_protocol_issues = sorted(
        set(provider_protocol["issue_types"]) - set(baseline_protocol["issue_types"])
    )
    if new_protocol_issues:
        reasons.append("protocol issues: " + ", ".join(new_protocol_issues[:3]))

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
    protocol_score_delta: float,
) -> str:
    if alignment == "aligned":
        return (
            f"Aligned with baseline. Adjusted delta {weighted_score_delta:+.2f}, "
            f"protocol delta {protocol_score_delta:+.2f}, "
            f"stability penalty delta {stability_penalty_delta:+.2f}."
        )
    if alignment == "partial_drift":
        return (
            f"Partial drift detected. Adjusted delta {weighted_score_delta:+.2f}, "
            f"protocol delta {protocol_score_delta:+.2f}, {mismatch_cases} mismatched cases, "
            f"{critical_mismatch_cases} critical, stability penalty delta {stability_penalty_delta:+.2f}."
        )
    return (
        f"Strong drift detected. Adjusted delta {weighted_score_delta:+.2f}, "
        f"protocol delta {protocol_score_delta:+.2f}, {mismatch_cases} mismatched cases, "
        f"{critical_mismatch_cases} critical, stability penalty delta {stability_penalty_delta:+.2f}."
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


def _apply_protocol_adjustment(classification: str, protocol_summary: dict[str, Any]) -> str:
    if classification == "behaviorally_inconsistent":
        return classification
    if protocol_summary["alignment"] == "major_drift":
        if protocol_summary["critical_major_drift_cases"] >= 1 or protocol_summary["major_drift_cases"] >= 2:
            return "behaviorally_inconsistent"
        if classification == "likely_match":
            return "uncertain"
        return "behaviorally_inconsistent"
    if protocol_summary["alignment"] == "minor_drift" and classification == "likely_match":
        return "uncertain"
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
        protocol_summary = rollup["protocol_summary"]
        if (
            rollup["status"] == "passed"
            and not _is_instability_case(rollup["stability"])
            and protocol_summary["alignment"] == "compatible"
        ):
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

        if protocol_summary["alignment"] != "compatible":
            notes.append(
                f"{rollup['case_id']}: protocol {protocol_summary['alignment']} "
                f"({', '.join(protocol_summary['issue_types'][:2])})"
            )
            continue

        if _is_instability_case(rollup["stability"]):
            notes.append(f"{rollup['case_id']}: {rollup['stability']}")
    return notes


def _build_critical_findings(case_rollups: list[dict[str, Any]]) -> list[dict[str, Any]]:
    findings: list[dict[str, Any]] = []
    for rollup in case_rollups:
        if not rollup["critical"]:
            continue

        if rollup["status"] != "passed":
            findings.append(
                {
                    "kind": "behavior",
                    "severity": "high",
                    "case_id": rollup["case_id"],
                    "case_title": rollup["case_title"],
                    "signal": rollup["signal"],
                    "detail": rollup["dominant_failures"][0] if rollup["dominant_failures"] else rollup["status"],
                }
            )

        if _is_instability_case(rollup["stability"]):
            findings.append(
                {
                    "kind": "stability",
                    "severity": "high" if rollup["stability"] == "high_variance" else "medium",
                    "case_id": rollup["case_id"],
                    "case_title": rollup["case_title"],
                    "signal": rollup["signal"],
                    "detail": (
                        f"{rollup['stability']} with pass rate {rollup['pass_rate']:.2f}"
                    ),
                }
            )

        protocol_summary = rollup["protocol_summary"]
        if protocol_summary["alignment"] != "compatible":
            findings.append(
                {
                    "kind": "protocol",
                    "severity": "high" if protocol_summary["alignment"] == "major_drift" else "medium",
                    "case_id": rollup["case_id"],
                    "case_title": rollup["case_title"],
                    "signal": rollup["signal"],
                    "detail": protocol_summary["diagnosis"],
                }
            )

    return findings


def _build_evidence_trail(provider_summary: dict[str, Any]) -> list[dict[str, str]]:
    trail: list[dict[str, str]] = []

    if provider_summary["adjusted_score"] >= 0.9:
        trail.append(
            {
                "level": "positive",
                "title": "Behavior score stayed strong",
                "detail": (
                    f"Adjusted score {provider_summary['adjusted_score']:.2f} across "
                    f"{provider_summary['passed_cases']}/{provider_summary['passed_cases'] + provider_summary['failed_cases']} cases."
                ),
            }
        )
    else:
        trail.append(
            {
                "level": "negative",
                "title": "Behavior score drifted",
                "detail": (
                    f"Adjusted score {provider_summary['adjusted_score']:.2f} with "
                    f"{provider_summary['failed_cases']} failed cases."
                ),
            }
        )

    if provider_summary["critical_failures"]:
        trail.append(
            {
                "level": "negative",
                "title": "Critical behavior failures were detected",
                "detail": f"{provider_summary['critical_failures']} critical cases did not pass cleanly.",
            }
        )

    if provider_summary["unstable_cases"]:
        trail.append(
            {
                "level": "warning",
                "title": "Repeated sampling found instability",
                "detail": (
                    f"{provider_summary['unstable_cases']} unstable cases, including "
                    f"{provider_summary['critical_unstable_cases']} critical."
                ),
            }
        )
    elif provider_summary["sample_count"] > 1:
        trail.append(
            {
                "level": "positive",
                "title": "Repeated sampling stayed stable",
                "detail": f"{provider_summary['sample_count']} samples per case showed no critical instability.",
            }
        )

    protocol_summary = provider_summary["protocol_summary"]
    if protocol_summary["alignment"] == "compatible":
        trail.append(
            {
                "level": "positive",
                "title": "Protocol evidence matched expected shape",
                "detail": f"Protocol score {protocol_summary['protocol_score']:.2f} with no flagged cases.",
            }
        )
    else:
        trail.append(
            {
                "level": "negative" if protocol_summary["alignment"] == "major_drift" else "warning",
                "title": "Protocol evidence drifted",
                "detail": protocol_summary["diagnosis"],
            }
        )

    comparison_summary = provider_summary.get("comparison_summary")
    if comparison_summary:
        if comparison_summary["alignment"] == "aligned":
            trail.append(
                {
                    "level": "positive",
                    "title": "Baseline comparison aligned",
                    "detail": comparison_summary["diagnosis"],
                }
            )
        else:
            trail.append(
                {
                    "level": "negative" if comparison_summary["alignment"] == "strong_drift" else "warning",
                    "title": "Baseline comparison diverged",
                    "detail": comparison_summary["diagnosis"],
                }
            )

    trail.append(
        {
            "level": "neutral",
            "title": "Final classification",
            "detail": provider_summary["classification"],
        }
    )
    return trail


def _attempt_protocol_evidence(attempt: dict[str, Any]) -> dict[str, Any]:
    raw = attempt.get("raw") or {}
    evidence = raw.get("protocol_evidence")
    if isinstance(evidence, dict):
        return evidence
    return {
        "protocol_score": 0.0,
        "issues": ["missing_protocol_evidence"],
        "finish_reason": None,
        "has_finish_reason": False,
        "usage_present": False,
        "usage_keys": [],
        "content_mode": "missing",
        "content_block_types": [],
        "tool_call_shape": "none",
        "tool_call_count": 0,
    }


def _protocol_case_diagnosis(
    alignment: str,
    issue_types: list[str],
    issue_attempts: int,
    sample_count: int,
) -> str:
    if alignment == "compatible":
        return "Protocol shape matched expected OpenAI-compatible response fields."
    if alignment == "minor_drift":
        return (
            f"Minor protocol drift in {issue_attempts}/{sample_count} attempts: "
            + ", ".join(issue_types[:3])
        )
    return (
        f"Major protocol drift in {issue_attempts}/{sample_count} attempts: "
        + ", ".join(issue_types[:4])
    )


def _protocol_provider_diagnosis(
    alignment: str,
    protocol_score: float,
    flagged_cases: int,
    major_drift_cases: int,
    issue_types: list[str],
) -> str:
    if alignment == "compatible":
        return "Protocol evidence matched expected OpenAI-compatible fields across all cases."
    if alignment == "minor_drift":
        return (
            f"Minor protocol drift detected across {flagged_cases} cases. "
            f"Protocol score {protocol_score:.2f}. Issues: {', '.join(issue_types[:3])}."
        )
    return (
        f"Major protocol drift detected across {major_drift_cases} cases. "
        f"Protocol score {protocol_score:.2f}. Issues: {', '.join(issue_types[:4])}."
    )


def _dominant_value(values: list[str], default: str) -> str:
    if not values:
        return default
    counts: dict[str, int] = defaultdict(int)
    for value in values:
        counts[value] += 1
    return sorted(counts.items(), key=lambda item: (-item[1], item[0]))[0][0]


def _response_fingerprint(value: str) -> str:
    return " ".join(value.lower().split())


def _is_instability_case(stability: str) -> bool:
    return stability in {"moderate_variance", "high_variance"}
