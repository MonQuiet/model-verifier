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
            "provider_names": [provider.name for provider in selected_providers],
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
            "provider_names": [provider.name for provider in selected_providers],
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
        selected_providers = select_providers(providers, provider_names)
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
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for record in records:
        grouped[record["provider_name"]].append(record)

    provider_summaries: list[dict[str, Any]] = []
    for provider in selected_providers:
        provider_records = grouped.get(provider.name, [])
        total_cases = len(provider_records)
        passed_cases = sum(1 for record in provider_records if record["status"] == "passed")
        failed_cases = total_cases - passed_cases
        average_score = round(
            sum(record["score"] for record in provider_records) / total_cases if total_cases else 0.0,
            3,
        )
        average_latency_ms = round(
            sum(record["latency_ms"] for record in provider_records) / total_cases if total_cases else 0.0,
            1,
        )
        classification = classify_provider(average_score, failed_cases, max(total_cases, 1))
        failures = []
        for record in provider_records:
            failed_checks = [check for check in record["evaluation"]["checks"] if not check["passed"]]
            if failed_checks:
                failures.append(f"{record['case_id']}: {failed_checks[0]['detail']}")

        provider_summaries.append(
            {
                "provider_name": provider.name,
                "provider_model": provider.model,
                "average_score": average_score,
                "average_latency_ms": average_latency_ms,
                "passed_cases": passed_cases,
                "failed_cases": failed_cases,
                "classification": classification,
                "diagnosis": _diagnosis_for(classification, failures),
            }
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
        return "Structured output, safety, and context checks were consistently aligned."
    if classification == "uncertain":
        if failures:
            return "Partial drift detected. Review failing cases: " + "; ".join(failures[:2])
        return "Mixed signals detected without a decisive mismatch."
    if failures:
        return "Multiple behavior mismatches detected. Review failing cases: " + "; ".join(failures[:3])
    return "Behavior diverged from the expected baseline."


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")

