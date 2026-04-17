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
    grouped_results = _group_results_by_provider_and_case(results)

    lines: list[str] = [
        f"# Model Verifier Report `{run_payload['id']}`",
        "",
        f"- Status: `{run_payload['status']}`",
        f"- Created At: `{run_payload['created_at']}`",
        f"- Updated At: `{run_payload['updated_at']}`",
        f"- Sample Count: `{summary.get('sample_count', 1)}`",
        "",
        "## Provider Summary",
        "",
        "| Provider | Model | Weighted Score | Adjusted Score | Protocol | Protocol Score | Classification | Failed | Unstable |",
        "| --- | --- | ---: | ---: | --- | ---: | --- | ---: | ---: |",
    ]

    for provider_summary in summary.get("provider_summaries", []):
        protocol_summary = provider_summary.get("protocol_summary") or {}
        lines.append(
            "| {provider_name} | {provider_model} | {average_score:.2f} | {adjusted_score:.2f} | {protocol_alignment} | {protocol_score:.2f} | {classification} | {failed_cases} | {unstable_cases} |".format(
                provider_name=provider_summary["provider_name"],
                provider_model=provider_summary["provider_model"],
                average_score=provider_summary["average_score"],
                adjusted_score=provider_summary["adjusted_score"],
                protocol_alignment=protocol_summary.get("alignment", "unknown"),
                protocol_score=protocol_summary.get("protocol_score", 0.0),
                classification=provider_summary["classification"],
                failed_cases=provider_summary["failed_cases"],
                unstable_cases=provider_summary["unstable_cases"],
            )
        )

    lines.extend(["", "## Case Details", ""])
    for provider_summary in summary.get("provider_summaries", []):
        provider_name = provider_summary["provider_name"]
        protocol_summary = provider_summary.get("protocol_summary") or {}
        lines.extend(
            [
                f"### {provider_name}",
                "",
                f"- Classification: `{provider_summary['classification']}`",
                f"- Weighted Score: `{provider_summary['average_score']:.2f}`",
                f"- Stability-Adjusted Score: `{provider_summary['adjusted_score']:.2f}`",
                f"- Stability Penalty: `{provider_summary['stability_penalty']:.2f}`",
                f"- Protocol Alignment: `{protocol_summary.get('alignment', 'unknown')}`",
                f"- Protocol Score: `{protocol_summary.get('protocol_score', 0.0):.2f}`",
                f"- Protocol Diagnosis: {protocol_summary.get('diagnosis', 'No protocol diagnosis available.')}",
                f"- Critical Failures: `{provider_summary['critical_failures']}`",
                f"- Unstable Cases: `{provider_summary['unstable_cases']}`",
                f"- Critical Unstable Cases: `{provider_summary['critical_unstable_cases']}`",
                f"- Style Variance Cases: `{provider_summary['style_variance_cases']}`",
                f"- Missing Usage Cases: `{protocol_summary.get('missing_usage_cases', 0)}`",
                f"- Missing Finish Reason Cases: `{protocol_summary.get('missing_finish_reason_cases', 0)}`",
                f"- Invalid Tool Call Cases: `{protocol_summary.get('invalid_tool_call_cases', 0)}`",
                f"- Unsupported Content Block Cases: `{protocol_summary.get('unsupported_content_block_cases', 0)}`",
                f"- Diagnosis: {provider_summary['diagnosis']}",
                "",
            ]
        )

        evidence_trail = provider_summary.get("evidence_trail", [])
        if evidence_trail:
            lines.extend(["#### Evidence Trail", ""])
            for entry in evidence_trail:
                lines.append(
                    "- `{level}` {title}: {detail}".format(
                        level=entry.get("level", "neutral"),
                        title=entry.get("title", "Evidence"),
                        detail=entry.get("detail", ""),
                    )
                )
            lines.extend(["", ""])

        critical_findings = provider_summary.get("critical_findings", [])
        if critical_findings:
            lines.extend(
                [
                    "#### Critical Findings",
                    "",
                    "| Kind | Severity | Case | Signal | Detail |",
                    "| --- | --- | --- | --- | --- |",
                ]
            )
            for finding in critical_findings:
                lines.append(
                    "| {kind} | {severity} | {case_id} | {signal} | {detail} |".format(
                        kind=finding["kind"],
                        severity=finding["severity"],
                        case_id=finding["case_id"],
                        signal=finding["signal"],
                        detail=finding["detail"].replace("|", "/"),
                    )
                )
            lines.extend(["", ""])

        comparison_summary = provider_summary.get("comparison_summary")
        if comparison_summary:
            lines.extend(
                [
                    f"- Baseline Provider: `{comparison_summary['baseline_provider_name']}`",
                    f"- Baseline Model: `{comparison_summary['baseline_provider_model']}`",
                    f"- Baseline Alignment: `{comparison_summary['alignment']}`",
                    f"- Adjusted Delta vs Baseline: `{comparison_summary['weighted_score_delta']:+.2f}`",
                    f"- Protocol Delta vs Baseline: `{comparison_summary['protocol_score_delta']:+.2f}`",
                    f"- Stability Penalty Delta: `{comparison_summary['stability_penalty_delta']:+.2f}`",
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
                        "| Case | Signal | Provider | Baseline | Protocol | Delta | Reasons |",
                        "| --- | --- | --- | --- | --- | ---: | --- |",
                    ]
                )
                for case_delta in mismatched_case_deltas:
                    lines.append(
                        "| {case_id} | {signal} | {provider_status}/{provider_stability} | {baseline_status}/{baseline_stability} | {provider_protocol_alignment}/{baseline_protocol_alignment} | {score_delta:+.2f} | {reasons} |".format(
                            case_id=case_delta["case_id"],
                            signal=case_delta["signal"],
                            provider_status=case_delta["provider_status"],
                            provider_stability=case_delta["provider_stability"],
                            baseline_status=case_delta["baseline_status"],
                            baseline_stability=case_delta["baseline_stability"],
                            provider_protocol_alignment=case_delta["provider_protocol_alignment"],
                            baseline_protocol_alignment=case_delta["baseline_protocol_alignment"],
                            score_delta=case_delta["score_delta"],
                            reasons="; ".join(case_delta["mismatch_reasons"]),
                        )
                    )
                lines.extend(["", ""])

        signal_summaries = provider_summary.get("signal_summaries", [])
        if signal_summaries:
            lines.extend(
                [
                    "| Signal | Critical | Weighted Score | Adjusted Score | Penalty | Failed Cases | Unstable |",
                    "| --- | --- | ---: | ---: | ---: | ---: | ---: |",
                ]
            )
            for signal_summary in signal_summaries:
                lines.append(
                    "| {signal} | {critical} | {weighted_score:.2f} | {adjusted_weighted_score:.2f} | {stability_penalty:.2f} | {failed_cases}/{total_cases} | {unstable_cases} |".format(
                        **signal_summary
                    )
                )
            lines.extend(["", ""])

        case_rollups = provider_summary.get("case_rollups", [])
        if case_rollups:
            lines.extend(
                [
                    "| Case | Status | Stability | Protocol | Pass Rate | Avg Score | Adjusted | Protocol Score |",
                    "| --- | --- | --- | --- | ---: | ---: | ---: | ---: |",
                ]
            )
            for case_rollup in case_rollups:
                lines.append(
                    "| {case_id} | {status} | {stability} | {protocol_alignment} | {pass_rate:.2f} | {average_score:.2f} | {adjusted_score:.2f} | {protocol_score:.2f} |".format(
                        case_id=case_rollup["case_id"],
                        status=case_rollup["status"],
                        stability=case_rollup["stability"],
                        protocol_alignment=case_rollup["protocol_summary"]["alignment"],
                        pass_rate=case_rollup["pass_rate"],
                        average_score=case_rollup["average_score"],
                        adjusted_score=case_rollup["adjusted_score"],
                        protocol_score=case_rollup["protocol_summary"]["protocol_score"],
                    )
                )
            lines.extend(["", ""])

        for case_rollup in case_rollups:
            case = case_lookup.get(case_rollup["case_id"], {})
            case_protocol = case_rollup["protocol_summary"]
            lines.extend(
                [
                    f"#### {case_rollup['case_title']} (`{case_rollup['case_id']}`)",
                    "",
                    f"- Status: `{case_rollup['status']}`",
                    f"- Stability: `{case_rollup['stability']}`",
                    f"- Protocol Alignment: `{case_protocol['alignment']}`",
                    f"- Protocol Score: `{case_protocol['protocol_score']:.2f}`",
                    f"- Protocol Diagnosis: {case_protocol['diagnosis']}",
                    f"- Pass Rate: `{case_rollup['pass_rate']:.2f}`",
                    f"- Average Score: `{case_rollup['average_score']:.2f}`",
                    f"- Adjusted Score: `{case_rollup['adjusted_score']:.2f}`",
                    f"- Stability Penalty: `{case_rollup['stability_penalty']:.2f}`",
                    f"- Usage Coverage: `{case_protocol['usage_coverage']:.2f}`",
                    f"- Finish Reason Coverage: `{case_protocol['finish_reason_coverage']:.2f}`",
                    f"- Dominant Content Mode: `{case_protocol['dominant_content_mode']}`",
                    f"- Dominant Tool Call Shape: `{case_protocol['dominant_tool_call_shape']}`",
                    f"- Score Range: `{case_rollup['min_score']:.2f}` -> `{case_rollup['max_score']:.2f}`",
                    f"- Response Variants: `{case_rollup['response_variants']}`",
                    f"- Goal: {case.get('description', 'No description provided.')}",
                ]
            )

            if case_rollup["check_flips"]:
                lines.append(f"- Check Flips: {', '.join(case_rollup['check_flips'])}")
            else:
                lines.append("- Check Flips: none")

            if case_protocol["issue_types"]:
                lines.append(f"- Protocol Issues: {', '.join(case_protocol['issue_types'])}")
            else:
                lines.append("- Protocol Issues: none")

            if case_rollup["dominant_failures"]:
                lines.append(f"- Failure Signals: {'; '.join(case_rollup['dominant_failures'])}")
            else:
                lines.append("- Failure Signals: none")

            lines.extend(
                [
                    "",
                    "| Sample | Status | Score | Protocol | Finish | Content | Tool Calls | Usage | Failed Checks | Issues |",
                    "| ---: | --- | ---: | ---: | --- | --- | --- | --- | --- | --- |",
                ]
            )
            for attempt in case_rollup.get("attempts", []):
                failed_checks = ", ".join(attempt.get("failed_checks", [])) or "none"
                issues = ", ".join(attempt.get("issues", [])) or "none"
                lines.append(
                    "| {sample} | {status} | {score:.2f} | {protocol_score:.2f} | {finish_reason} | {content_mode} | {tool_call_shape} | {usage_present} | {failed_checks} | {issues} |".format(
                        sample=attempt["sample_index"] + 1,
                        status=attempt["status"],
                        score=attempt["score"],
                        protocol_score=attempt["protocol_score"],
                        finish_reason=attempt["finish_reason"],
                        content_mode=attempt["content_mode"],
                        tool_call_shape=attempt["tool_call_shape"],
                        usage_present="yes" if attempt["usage_present"] else "no",
                        failed_checks=failed_checks,
                        issues=issues,
                    )
                )
            lines.extend(["", ""])

            for result in grouped_results.get(provider_name, {}).get(case_rollup["case_id"], []):
                lines.extend(
                    [
                        f"Sample {result.get('sample_index', 0) + 1}",
                        "",
                        "```text",
                        _truncate(result["response_text"]),
                        "```",
                        "",
                    ]
                )

    return "\n".join(lines).strip() + "\n"


def _group_results_by_provider_and_case(results: list[dict[str, Any]]) -> dict[str, dict[str, list[dict[str, Any]]]]:
    grouped: dict[str, dict[str, list[dict[str, Any]]]] = {}
    for result in results:
        provider_group = grouped.setdefault(result["provider_name"], {})
        case_group = provider_group.setdefault(result["case_id"], [])
        case_group.append(result)
    for provider_group in grouped.values():
        for case_id, case_group in provider_group.items():
            provider_group[case_id] = sorted(case_group, key=lambda item: item.get("sample_index", 0))
    return grouped


def _truncate(value: str, limit: int = 700) -> str:
    if len(value) <= limit:
        return value
    return value[: limit - 3] + "..."
