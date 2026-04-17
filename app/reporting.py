from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .config import Settings


def write_reports(
    settings: Settings,
    run_payload: dict[str, Any],
    selected_cases: list[dict[str, Any]],
) -> dict[str, str]:
    markdown_path = settings.reports_dir / f"{run_payload['id']}.md"
    json_path = settings.reports_dir / f"{run_payload['id']}.json"

    markdown_path.write_text(_build_markdown(run_payload, selected_cases), encoding="utf-8")
    json_path.write_text(json.dumps(run_payload, ensure_ascii=False, indent=2), encoding="utf-8")

    return {
        "markdown_path": str(markdown_path),
        "json_path": str(json_path),
    }


def _build_markdown(run_payload: dict[str, Any], selected_cases: list[dict[str, Any]]) -> str:
    summary = run_payload.get("summary") or {}
    results = run_payload.get("results") or []
    case_lookup = {case["id"]: case for case in selected_cases}

    lines: list[str] = [
        f"# Model Verifier Report `{run_payload['id']}`",
        "",
        f"- Status: `{run_payload['status']}`",
        f"- Created At: `{run_payload['created_at']}`",
        f"- Updated At: `{run_payload['updated_at']}`",
        "",
        "## Provider Summary",
        "",
        "| Provider | Model | Weighted Score | Classification | Passed | Failed | Critical Failures |",
        "| --- | --- | ---: | --- | ---: | ---: | ---: |",
    ]

    for provider_summary in summary.get("provider_summaries", []):
        lines.append(
            "| {provider_name} | {provider_model} | {average_score:.2f} | {classification} | {passed_cases} | {failed_cases} | {critical_failures} |".format(
                **provider_summary
            )
        )

    lines.extend(["", "## Case Details", ""])
    for provider_summary in summary.get("provider_summaries", []):
        provider_name = provider_summary["provider_name"]
        lines.extend(
            [
                f"### {provider_name}",
                "",
                f"- Classification: `{provider_summary['classification']}`",
                f"- Weighted Score: `{provider_summary['average_score']:.2f}`",
                f"- Critical Failures: `{provider_summary['critical_failures']}`",
                f"- Diagnosis: {provider_summary['diagnosis']}",
                "",
            ]
        )
        comparison_summary = provider_summary.get("comparison_summary")
        if comparison_summary:
            lines.extend(
                [
                    f"- Baseline Provider: `{comparison_summary['baseline_provider_name']}`",
                    f"- Baseline Model: `{comparison_summary['baseline_provider_model']}`",
                    f"- Baseline Alignment: `{comparison_summary['alignment']}`",
                    f"- Weighted Delta vs Baseline: `{comparison_summary['weighted_score_delta']:+.2f}`",
                    f"- Baseline Diagnosis: {comparison_summary['diagnosis']}",
                    "",
                ]
            )
            signal_deltas = comparison_summary.get("signal_deltas", [])
            if signal_deltas:
                lines.extend(
                    [
                        "| Signal | Critical | Provider | Baseline | Delta |",
                        "| --- | --- | ---: | ---: | ---: |",
                    ]
                )
                for signal_delta in signal_deltas:
                    lines.append(
                        "| {signal} | {critical} | {provider_score:.2f} | {baseline_score:.2f} | {score_delta:+.2f} |".format(
                            **signal_delta
                        )
                    )
                lines.extend(["", ""])

            mismatched_case_deltas = [
                item for item in comparison_summary.get("case_deltas", []) if not item.get("matched", True)
            ]
            if mismatched_case_deltas:
                lines.extend(
                    [
                        "| Case | Signal | Provider | Baseline | Delta | Reasons |",
                        "| --- | --- | --- | --- | ---: | --- |",
                    ]
                )
                for case_delta in mismatched_case_deltas:
                    lines.append(
                        "| {case_id} | {signal} | {provider_status} | {baseline_status} | {score_delta:+.2f} | {reasons} |".format(
                            case_id=case_delta["case_id"],
                            signal=case_delta["signal"],
                            provider_status=case_delta["provider_status"],
                            baseline_status=case_delta["baseline_status"],
                            score_delta=case_delta["score_delta"],
                            reasons="; ".join(case_delta["mismatch_reasons"]),
                        )
                    )
                lines.extend(["", ""])

        signal_summaries = provider_summary.get("signal_summaries", [])
        if signal_summaries:
            lines.extend(
                [
                    "| Signal | Critical | Weighted Score | Failed Cases |",
                    "| --- | --- | ---: | ---: |",
                ]
            )
            for signal_summary in signal_summaries:
                lines.append(
                    "| {signal} | {critical} | {weighted_score:.2f} | {failed_cases}/{total_cases} |".format(
                        **signal_summary
                    )
                )
            lines.extend(["", ""])

        provider_results = [result for result in results if result["provider_name"] == provider_name]

        for result in provider_results:
            case = case_lookup.get(result["case_id"], {})
            lines.extend(
                [
                    f"#### {result['case_title']} (`{result['case_id']}`)",
                    "",
                    f"- Status: `{result['status']}`",
                    f"- Score: `{result['score']:.2f}`",
                    f"- Signal: `{result['evaluation'].get('signal', 'general')}`",
                    f"- Latency: `{result['latency_ms']} ms`",
                    f"- Goal: {case.get('description', 'No description provided.')}",
                ]
            )

            failed_checks = [check for check in result["evaluation"]["checks"] if not check["passed"]]
            if failed_checks:
                details = "; ".join(f"{item['name']}: {item['detail']}" for item in failed_checks)
                lines.append(f"- Failed Checks: {details}")
            else:
                lines.append("- Failed Checks: none")

            lines.extend(["", "```text", _truncate(result["response_text"]), "```", ""])

    return "\n".join(lines).strip() + "\n"


def _truncate(value: str, limit: int = 700) -> str:
    if len(value) <= limit:
        return value
    return value[: limit - 3] + "..."
