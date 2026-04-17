from __future__ import annotations

import argparse
import json

from .config import Settings
from .service import VerificationService


def main() -> None:
    parser = argparse.ArgumentParser(description="Run a model verification batch.")
    parser.add_argument(
        "--providers",
        help="Comma-separated provider names. Defaults to all configured providers.",
    )
    parser.add_argument(
        "--cases",
        help="Comma-separated case ids. Defaults to all configured cases.",
    )
    parser.add_argument(
        "--list",
        action="store_true",
        help="Print configured providers and cases, then exit.",
    )
    parser.add_argument(
        "--samples",
        type=int,
        default=1,
        help="Repeat each selected case this many times per provider.",
    )
    args = parser.parse_args()

    settings = Settings.from_env()
    service = VerificationService(settings)

    if args.list:
        print(json.dumps(service.get_catalog(), ensure_ascii=False, indent=2))
        return

    provider_names = _split_csv(args.providers)
    case_ids = _split_csv(args.cases)
    run_payload = service.run_sync(
        provider_names=provider_names,
        case_ids=case_ids,
        sample_count=max(args.samples, 1),
    )
    print(json.dumps(run_payload["summary"], ensure_ascii=False, indent=2))
    print(f"Markdown report: {run_payload['report_path']}")
    print(f"JSON report: {run_payload['report_json_path']}")


def _split_csv(raw_value: str | None) -> list[str] | None:
    if not raw_value:
        return None
    return [item.strip() for item in raw_value.split(",") if item.strip()]


if __name__ == "__main__":
    main()
