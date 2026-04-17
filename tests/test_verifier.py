from __future__ import annotations

from pathlib import Path
import shutil
import sys
import tempfile
import unittest

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from app.config import Settings, ensure_runtime_paths
from app.service import VerificationService


class VerificationServiceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.root = Path(self.temp_dir.name)
        for directory in ("data", "reports", "prompts", "web"):
            (self.root / directory).mkdir(parents=True, exist_ok=True)

        shutil.copy(REPO_ROOT / "prompts" / "cases.json", self.root / "prompts" / "cases.json")
        shutil.copy(REPO_ROOT / "providers.sample.json", self.root / "providers.sample.json")
        (self.root / "web" / "index.html").write_text("<h1>test</h1>", encoding="utf-8")

        self.settings = self._make_settings()
        ensure_runtime_paths(self.settings)
        self.service = VerificationService(self.settings)

    def _make_settings(self, review_policy: str = "standard") -> Settings:
        return Settings(
            root_dir=self.root,
            host="127.0.0.1",
            port=8000,
            database_path=self.root / "data" / "results.db",
            reports_dir=self.root / "reports",
            cases_path=self.root / "prompts" / "cases.json",
            providers_path=self.root / "providers.sample.json",
            web_dir=self.root / "web",
            allowed_origins=("http://127.0.0.1:8000",),
            review_policy=review_policy,
        )

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def test_reference_provider_stays_stable_under_repeat_sampling(self) -> None:
        run_payload = self.service.run_sync(
            provider_names=["mock-reference-gpt41"],
            case_ids=["json_contract", "context_memory", "refusal_boundary", "tool_plan_json"],
            sample_count=3,
        )

        provider_summary = _summary_for(run_payload, "mock-reference-gpt41")
        self.assertEqual(run_payload["summary"]["sample_count"], 3)
        self.assertEqual(provider_summary["classification"], "likely_match")
        self.assertEqual(provider_summary["critical_failures"], 0)
        self.assertEqual(provider_summary["unstable_cases"], 0)
        self.assertEqual(provider_summary["protocol_summary"]["alignment"], "compatible")
        self.assertEqual(provider_summary["protocol_summary"]["flagged_cases"], 0)
        self.assertTrue(provider_summary["evidence_trail"])
        self.assertEqual(provider_summary["critical_findings"], [])
        self.assertTrue(provider_summary["signal_summaries"])
        self.assertTrue(provider_summary["case_rollups"])
        self.assertEqual(provider_summary["review_summary"]["risk_level"], "low")
        self.assertEqual(provider_summary["review_summary"]["action"], "accept_with_monitoring")
        self.assertTrue(all(item["sample_count"] == 3 for item in provider_summary["case_rollups"]))
        self.assertTrue(Path(run_payload["report_path"]).exists())
        self.assertTrue(Path(run_payload["report_json_path"]).exists())

    def test_clean_gateway_aligns_with_baseline_under_repeat_sampling(self) -> None:
        run_payload = self.service.run_sync(
            provider_names=["mock-clean-gateway"],
            case_ids=["json_contract", "context_memory", "refusal_boundary", "tool_plan_json"],
            sample_count=3,
        )

        provider_summary = _summary_for(run_payload, "mock-clean-gateway")
        self.assertEqual(provider_summary["classification"], "likely_match")
        self.assertEqual(provider_summary["comparison_summary"]["alignment"], "aligned")
        self.assertEqual(provider_summary["comparison_summary"]["mismatch_cases"], 0)
        self.assertEqual(provider_summary["unstable_cases"], 0)
        self.assertEqual(provider_summary["protocol_summary"]["alignment"], "compatible")
        self.assertEqual(provider_summary["review_summary"]["risk_level"], "low")
        self.assertEqual(provider_summary["review_summary"]["action"], "accept_with_monitoring")

    def test_flaky_provider_is_flagged_for_repeat_sampling_instability(self) -> None:
        run_payload = self.service.run_sync(
            provider_names=["mock-flaky-gateway"],
            case_ids=["json_contract", "context_memory", "refusal_boundary", "tool_plan_json"],
            sample_count=3,
        )

        provider_summary = _summary_for(run_payload, "mock-flaky-gateway")
        self.assertEqual(provider_summary["classification"], "behaviorally_inconsistent")
        self.assertGreaterEqual(provider_summary["unstable_cases"], 1)
        self.assertGreaterEqual(provider_summary["critical_unstable_cases"], 1)
        self.assertEqual(provider_summary["comparison_summary"]["alignment"], "strong_drift")
        self.assertGreaterEqual(provider_summary["comparison_summary"]["mismatch_cases"], 1)
        self.assertTrue(provider_summary["critical_findings"])
        self.assertTrue(any(item["kind"] == "stability" for item in provider_summary["critical_findings"]))
        self.assertEqual(provider_summary["review_summary"]["risk_level"], "critical")
        self.assertEqual(provider_summary["review_summary"]["action"], "block_and_investigate")

    def test_protocol_drift_provider_is_flagged_even_when_behavior_matches(self) -> None:
        run_payload = self.service.run_sync(
            provider_names=["mock-protocol-gateway"],
            case_ids=["json_contract", "context_memory", "refusal_boundary", "tool_plan_json"],
        )

        provider_summary = _summary_for(run_payload, "mock-protocol-gateway")
        self.assertEqual(provider_summary["classification"], "behaviorally_inconsistent")
        self.assertEqual(provider_summary["protocol_summary"]["alignment"], "major_drift")
        self.assertGreaterEqual(provider_summary["protocol_summary"]["major_drift_cases"], 1)
        self.assertGreaterEqual(provider_summary["protocol_summary"]["missing_usage_cases"], 1)
        self.assertGreaterEqual(provider_summary["comparison_summary"]["mismatch_cases"], 1)
        self.assertEqual(provider_summary["comparison_summary"]["alignment"], "strong_drift")
        self.assertTrue(any(item["kind"] == "protocol" for item in provider_summary["critical_findings"]))
        self.assertTrue(any(item["title"] == "Protocol evidence drifted" for item in provider_summary["evidence_trail"]))
        self.assertEqual(provider_summary["review_summary"]["risk_level"], "critical")
        self.assertEqual(provider_summary["review_summary"]["action"], "block_and_investigate")

    def test_suspect_provider_classifies_as_behaviorally_inconsistent(self) -> None:
        run_payload = self.service.run_sync(
            provider_names=["mock-suspect-gateway"],
            case_ids=["json_contract", "context_memory", "refusal_boundary", "tool_plan_json"],
        )

        provider_summary = _summary_for(run_payload, "mock-suspect-gateway")
        self.assertEqual(provider_summary["classification"], "behaviorally_inconsistent")
        self.assertGreaterEqual(provider_summary["critical_failures"], 1)
        self.assertEqual(provider_summary["comparison_summary"]["alignment"], "strong_drift")
        self.assertGreaterEqual(provider_summary["comparison_summary"]["mismatch_cases"], 1)
        self.assertEqual(provider_summary["review_summary"]["risk_level"], "critical")
        self.assertEqual(provider_summary["review_summary"]["action"], "block_and_investigate")

    def test_strict_policy_requires_more_sampling_before_accepting_clean_gateway(self) -> None:
        strict_settings = self._make_settings(review_policy="strict")
        strict_service = VerificationService(strict_settings)

        run_payload = strict_service.run_sync(
            provider_names=["mock-clean-gateway"],
            case_ids=["json_contract", "context_memory", "refusal_boundary", "tool_plan_json"],
            sample_count=1,
        )

        provider_summary = _summary_for(run_payload, "mock-clean-gateway")
        self.assertEqual(run_payload["summary"]["review_policy"], "strict")
        self.assertEqual(provider_summary["classification"], "likely_match")
        self.assertEqual(provider_summary["review_summary"]["risk_level"], "medium")
        self.assertEqual(provider_summary["review_summary"]["action"], "expand_sampling")


def _summary_for(run_payload: dict, provider_name: str) -> dict:
    for provider_summary in run_payload["summary"]["provider_summaries"]:
        if provider_summary["provider_name"] == provider_name:
            return provider_summary
    raise AssertionError(f"Missing provider summary for {provider_name}")


if __name__ == "__main__":
    unittest.main()
