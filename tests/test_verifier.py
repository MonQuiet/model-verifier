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

        self.settings = Settings(
            root_dir=self.root,
            host="127.0.0.1",
            port=8000,
            database_path=self.root / "data" / "results.db",
            reports_dir=self.root / "reports",
            cases_path=self.root / "prompts" / "cases.json",
            providers_path=self.root / "providers.sample.json",
            web_dir=self.root / "web",
            allowed_origins=("http://127.0.0.1:8000",),
        )
        ensure_runtime_paths(self.settings)
        self.service = VerificationService(self.settings)

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def test_reference_provider_classifies_as_likely_match(self) -> None:
        run_payload = self.service.run_sync(
            provider_names=["mock-reference-gpt41"],
            case_ids=["json_contract", "context_memory", "refusal_boundary", "tool_plan_json"],
        )

        provider_summary = run_payload["summary"]["provider_summaries"][0]
        self.assertEqual(provider_summary["classification"], "likely_match")
        self.assertTrue(Path(run_payload["report_path"]).exists())
        self.assertTrue(Path(run_payload["report_json_path"]).exists())

    def test_suspect_provider_classifies_as_behaviorally_inconsistent(self) -> None:
        run_payload = self.service.run_sync(
            provider_names=["mock-suspect-gateway"],
            case_ids=["json_contract", "context_memory", "refusal_boundary", "tool_plan_json"],
        )

        provider_summary = run_payload["summary"]["provider_summaries"][0]
        self.assertEqual(provider_summary["classification"], "behaviorally_inconsistent")


if __name__ == "__main__":
    unittest.main()
