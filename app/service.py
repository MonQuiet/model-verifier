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

    def start_run(self, provider_names: list[str] | None = None, case_ids: list[str] | None = None) -> dict[str, Any]:
        selected_providers, selected_cases = self._prepare_selection(provider_names, case_ids)
        run_id = self._new_run_id()
        created_at = _utc_now()
        request_payload = {
            "requested_provider_names": provider_names or [],
            "resolved_provider_names": [provider.name for provider in selected_providers],
            "case_ids": [case["id"] for case in selected_cases],
        }
        db.create_run(self.settings, run_id, "queued", created_at, request_payload)

        worker = threading.Thread(
            target=self._execute_run,
            args=(run_id, selected_providers, selected_cases),
            daemon=True,
            name=f"run-{run_id}",
        )
        worker.start()
        run_payload = self.get_run(run_id)
        if run_payload is None:
            raise RuntimeError("Failed to load queued run after creation.")
        return run_payload

    def run_sync(self, provider_names: list[str] | None = None, case_ids: list[str] | None = None) -> dict[str, Any]:
        selected_providers, selected_cases = self._prepare_selection(provider_names, case_ids)
        run_id = self._new_run_id()
        created_at = _utc_now()
        request_payload = {
            "requested_provider_names": provider_names or [],
            "resolved_provider_names": [provider.name for provider in selected_providers],
            "case_ids": [case["id"] for case in selected_cases],
        }
        db.create_run(self.settings, run_id, "queued", created_at, request_payload)
        self._execute_run(run_id, selected_providers, selected_cases)
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
    ) -> None:
        db.update_run_status(self.settings, run_id, "running", _utc_now())
        records: list[dict[str, Any]] = []

        try:
            for provider in selected_providers:
                for case in selected_cases:
                    created_at = _utc_now()
                    try:
                        completion = generate_completion(provider, case)
                        evaluation = evaluate_response(case, completion["text"])
                        status = "passed" if evaluation["passed"] else "failed"
                        latency_ms = completion["latency_ms"]
                        response_text = completion["text"]
                        raw = completion
                    except Exception as exc:
                        evaluation = {
                            "passed": False,
                            "score": 0.0,
                            "checks": [
                                {
                                    "name": "request_error",
                                    "passed": False,
                                    "detail": str(exc),
                                }
                            ],
                        }
                        status = "error"
                        latency_ms = 0
                        response_text = ""
                        raw = {
                            "error": str(exc),
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
                        "created_at": created_at,
                    }
                    db.insert_case_result(self.settings, record)
                    records.append(record)

            summary = _build_summary(run_id, selected_providers, selected_cases, records)
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
) -> dict[str, Any]:
    provider_lookup = {provider.name: provider for provider in selected_providers}
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for record in records:
        grouped[record["provider_name"]].append(record)

    provider_summaries: list[dict[str, Any]] = []
    provider_summary_lookup: dict[str, dict[str, Any]] = {}
    for provider in selected_providers:
        provider_records = grouped.get(provider.name, [])
        total_cases = len(provider_records)
        passed_cases = sum(1 for record in provider_records if record["status"] == "passed")
        failed_cases = total_cases - passed_cases
        total_case_weight = sum(record["evaluation"].get("case_weight", 1.0) for record in provider_records)
        average_score = round(
            (
                sum(record["score"] * record["evaluation"].get("case_weight", 1.0) for record in provider_records)
                / total_case_weight
            )
            if total_case_weight
            else 0.0,
            3,
        )
        average_latency_ms = round(
            sum(record["latency_ms"] for record in provider_records) / total_cases if total_cases else 0.0,
            1,
        )
        signal_summaries = _build_signal_summaries(provider_records)
        critical_failures = sum(
            1 for record in provider_records if record["evaluation"].get("critical") and record["status"] != "passed"
        )
        critical_signal_scores = [
            signal_summary["weighted_score"]
            for signal_summary in signal_summaries
            if signal_summary["critical"]
        ]
        classification = classify_provider(
            average_score,
            failed_cases,
            max(total_cases, 1),
            critical_failures,
            critical_signal_scores,
        )
        failures = []
        for record in provider_records:
            failed_checks = [check for check in record["evaluation"]["checks"] if not check["passed"]]
            if failed_checks:
                failures.append(f"{record['case_id']}: {failed_checks[0]['detail']}")

        provider_summary = {
            "provider_name": provider.name,
            "provider_model": provider.model,
            "average_score": average_score,
            "average_latency_ms": average_latency_ms,
            "passed_cases": passed_cases,
            "failed_cases": failed_cases,
            "critical_failures": critical_failures,
            "signal_summaries": signal_summaries,
            "classification": classification,
            "diagnosis": _diagnosis_for(classification, failures),
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
            provider_records=grouped.get(provider.name, []),
            provider_summary=provider_summary,
            baseline_provider_name=provider.baseline_provider,
            baseline_summary=baseline_summary,
            baseline_records=grouped.get(provider.baseline_provider, []),
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
        "total_providers": len(selected_providers),
        "total_cases": len(selected_cases),
        "total_results": len(records),
        "provider_summaries": provider_summaries,
    }


def _diagnosis_for(classification: str, failures: list[str]) -> str:
    if classification == "likely_match":
        return "Weighted evidence stayed strong across all critical signals."
    if classification == "uncertain":
        if failures:
            return "Mixed evidence detected. Review the first failing signals: " + "; ".join(failures[:2])
        return "Mixed signals detected without a decisive mismatch."
    if failures:
        return "Critical evidence drift detected. Review failing cases: " + "; ".join(failures[:3])
    return "Behavior diverged from the expected baseline."


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _build_signal_summaries(provider_records: list[dict[str, Any]]) -> list[dict[str, Any]]:
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for record in provider_records:
        grouped[record["evaluation"].get("signal", "general")].append(record)

    summaries: list[dict[str, Any]] = []
    for signal_name, signal_records in sorted(grouped.items()):
        total_case_weight = sum(record["evaluation"].get("case_weight", 1.0) for record in signal_records)
        weighted_score = round(
            (
                sum(record["score"] * record["evaluation"].get("case_weight", 1.0) for record in signal_records)
                / total_case_weight
            )
            if total_case_weight
            else 0.0,
            3,
        )
        failed_cases = sum(1 for record in signal_records if record["status"] != "passed")
        summaries.append(
            {
                "signal": signal_name,
                "critical": any(record["evaluation"].get("critical", False) for record in signal_records),
                "weighted_score": weighted_score,
                "failed_cases": failed_cases,
                "total_cases": len(signal_records),
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
    provider_records: list[dict[str, Any]],
    provider_summary: dict[str, Any],
    baseline_provider_name: str,
    baseline_summary: dict[str, Any] | None,
    baseline_records: list[dict[str, Any]],
) -> dict[str, Any]:
    baseline_by_case = {record["case_id"]: record for record in baseline_records}
    case_deltas: list[dict[str, Any]] = []
    mismatch_cases = 0
    critical_mismatch_cases = 0

    for provider_record in provider_records:
        baseline_record = baseline_by_case.get(provider_record["case_id"])
        mismatch_reasons = _build_case_mismatch_reasons(provider_record, baseline_record)
        matched = not mismatch_reasons
        if not matched:
            mismatch_cases += 1
            if provider_record["evaluation"].get("critical"):
                critical_mismatch_cases += 1

        baseline_score = baseline_record["score"] if baseline_record else 0.0
        case_deltas.append(
            {
                "case_id": provider_record["case_id"],
                "case_title": provider_record["case_title"],
                "signal": provider_record["evaluation"].get("signal", "general"),
                "critical": provider_record["evaluation"].get("critical", False),
                "provider_status": provider_record["status"],
                "baseline_status": baseline_record["status"] if baseline_record else "missing",
                "provider_score": provider_record["score"],
                "baseline_score": baseline_score,
                "score_delta": round(provider_record["score"] - baseline_score, 3),
                "matched": matched,
                "mismatch_reasons": mismatch_reasons,
            }
        )

    signal_deltas = _build_signal_deltas(
        provider_summary.get("signal_summaries", []),
        baseline_summary.get("signal_summaries", []) if baseline_summary else [],
    )
    weighted_score_delta = round(
        provider_summary["average_score"] - (baseline_summary["average_score"] if baseline_summary else 0.0),
        3,
    )
    alignment = _classify_baseline_alignment(weighted_score_delta, mismatch_cases, critical_mismatch_cases, signal_deltas)

    return {
        "baseline_provider_name": baseline_provider_name,
        "baseline_provider_model": baseline_summary["provider_model"] if baseline_summary else "unknown",
        "alignment": alignment,
        "weighted_score_delta": weighted_score_delta,
        "mismatch_cases": mismatch_cases,
        "critical_mismatch_cases": critical_mismatch_cases,
        "signal_deltas": signal_deltas,
        "case_deltas": case_deltas,
        "diagnosis": _comparison_diagnosis(alignment, weighted_score_delta, mismatch_cases, critical_mismatch_cases),
    }


def _build_case_mismatch_reasons(
    provider_record: dict[str, Any],
    baseline_record: dict[str, Any] | None,
) -> list[str]:
    if baseline_record is None:
        return ["baseline result missing"]

    reasons: list[str] = []
    if provider_record["status"] != baseline_record["status"]:
        reasons.append(f"status drift: {baseline_record['status']} -> {provider_record['status']}")

    score_delta = provider_record["score"] - baseline_record["score"]
    if abs(score_delta) >= 0.2:
        reasons.append(f"score delta {score_delta:+.2f}")

    provider_checks = {check["name"]: check for check in provider_record["evaluation"]["checks"]}
    baseline_checks = {check["name"]: check for check in baseline_record["evaluation"]["checks"]}
    changed_checks = [
        check_name
        for check_name in sorted(set(provider_checks) | set(baseline_checks))
        if provider_checks.get(check_name, {}).get("passed") != baseline_checks.get(check_name, {}).get("passed")
    ]
    if changed_checks:
        reasons.append("check mismatch: " + ", ".join(changed_checks))

    return reasons


def _build_signal_deltas(
    provider_signal_summaries: list[dict[str, Any]],
    baseline_signal_summaries: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    baseline_lookup = {item["signal"]: item for item in baseline_signal_summaries}
    deltas: list[dict[str, Any]] = []

    for provider_signal in provider_signal_summaries:
        baseline_signal = baseline_lookup.get(provider_signal["signal"])
        baseline_score = baseline_signal["weighted_score"] if baseline_signal else 0.0
        deltas.append(
            {
                "signal": provider_signal["signal"],
                "critical": provider_signal["critical"],
                "provider_score": provider_signal["weighted_score"],
                "baseline_score": baseline_score,
                "score_delta": round(provider_signal["weighted_score"] - baseline_score, 3),
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
) -> str:
    if alignment == "aligned":
        return f"Aligned with baseline. Weighted delta {weighted_score_delta:+.2f} and no case mismatches."
    if alignment == "partial_drift":
        return (
            f"Partial drift detected. Weighted delta {weighted_score_delta:+.2f}, "
            f"{mismatch_cases} mismatched cases, {critical_mismatch_cases} critical."
        )
    return (
        f"Strong drift detected. Weighted delta {weighted_score_delta:+.2f}, "
        f"{mismatch_cases} mismatched cases, {critical_mismatch_cases} critical."
    )


def _apply_baseline_alignment(classification: str, alignment: str) -> str:
    if alignment == "strong_drift":
        return "behaviorally_inconsistent"
    if alignment == "partial_drift" and classification == "likely_match":
        return "uncertain"
    return classification
