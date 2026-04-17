from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import os


@dataclass(frozen=True)
class Settings:
    root_dir: Path
    host: str
    port: int
    database_path: Path
    reports_dir: Path
    cases_path: Path
    providers_path: Path
    web_dir: Path
    allowed_origins: tuple[str, ...]
    review_policy: str = "standard"

    @classmethod
    def from_env(cls, root_override: Path | None = None) -> "Settings":
        root_dir = (root_override or Path(__file__).resolve().parent.parent).resolve()
        host = os.environ.get("MODEL_VERIFIER_HOST", "127.0.0.1")
        port = int(os.environ.get("MODEL_VERIFIER_PORT", "8000"))
        database_path = _resolve_path(root_dir, os.environ.get("MODEL_VERIFIER_DATABASE", "data/results.db"))
        reports_dir = _resolve_path(root_dir, os.environ.get("MODEL_VERIFIER_REPORTS_DIR", "reports"))
        cases_path = _resolve_path(root_dir, os.environ.get("MODEL_VERIFIER_CASES", "prompts/cases.json"))
        providers_path = _resolve_path(root_dir, os.environ.get("MODEL_VERIFIER_PROVIDERS", "providers.sample.json"))
        web_dir = _resolve_path(root_dir, os.environ.get("MODEL_VERIFIER_WEB_DIR", "web"))
        allowed_origins = tuple(
            origin.strip()
            for origin in os.environ.get(
                "MODEL_VERIFIER_ALLOWED_ORIGINS",
                "http://127.0.0.1:8000,http://localhost:8000",
            ).split(",")
            if origin.strip()
        )
        review_policy = _read_review_policy(os.environ.get("MODEL_VERIFIER_REVIEW_POLICY", "standard"))
        return cls(
            root_dir=root_dir,
            host=host,
            port=port,
            database_path=database_path,
            reports_dir=reports_dir,
            cases_path=cases_path,
            providers_path=providers_path,
            web_dir=web_dir,
            allowed_origins=allowed_origins,
            review_policy=review_policy,
        )


def ensure_runtime_paths(settings: Settings) -> None:
    settings.database_path.parent.mkdir(parents=True, exist_ok=True)
    settings.reports_dir.mkdir(parents=True, exist_ok=True)
    settings.web_dir.mkdir(parents=True, exist_ok=True)
    settings.cases_path.parent.mkdir(parents=True, exist_ok=True)
    settings.providers_path.parent.mkdir(parents=True, exist_ok=True)

    if not settings.cases_path.exists():
        raise FileNotFoundError(f"Case file not found: {settings.cases_path}")
    if not settings.providers_path.exists():
        raise FileNotFoundError(f"Provider file not found: {settings.providers_path}")


def _resolve_path(root_dir: Path, raw_value: str) -> Path:
    path = Path(raw_value)
    if not path.is_absolute():
        path = root_dir / path
    return path.resolve()


def _read_review_policy(raw_value: str) -> str:
    value = raw_value.strip().lower() or "standard"
    if value not in {"standard", "strict"}:
        raise ValueError(f"Unsupported MODEL_VERIFIER_REVIEW_POLICY: {raw_value}")
    return value
