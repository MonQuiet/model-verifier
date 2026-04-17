from __future__ import annotations

from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
import time
from typing import Any
from urllib.parse import urlparse
import uuid

from .config import Settings
from .service import VerificationService


class ApiError(Exception):
    def __init__(self, status_code: int, message: str) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.message = message


class ModelVerifierHTTPServer(ThreadingHTTPServer):
    def __init__(self, server_address: tuple[str, int], settings: Settings, service: VerificationService) -> None:
        super().__init__(server_address, ModelVerifierHandler)
        self.settings = settings
        self.service = service


class ModelVerifierHandler(BaseHTTPRequestHandler):
    server_version = "ModelVerifier/0.1"

    def do_GET(self) -> None:
        self._dispatch("GET")

    def do_POST(self) -> None:
        self._dispatch("POST")

    def do_OPTIONS(self) -> None:
        request_id = uuid.uuid4().hex[:8]
        self.send_response(HTTPStatus.NO_CONTENT)
        self._write_common_headers("text/plain; charset=utf-8", request_id)
        self.end_headers()

    def log_message(self, format: str, *args: object) -> None:
        return

    def _dispatch(self, method: str) -> None:
        request_id = uuid.uuid4().hex[:8]
        started = time.perf_counter()
        status_code = 500

        try:
            parsed = urlparse(self.path)
            path = parsed.path

            if method == "GET" and path == "/health":
                status_code = self._json_response(
                    200,
                    request_id,
                    {
                        "status": "ok",
                        "service": "model-verifier",
                        "providers": len(self.server.service.get_catalog()["providers"]),
                    },
                )
            elif method == "GET" and path == "/api/config":
                status_code = self._json_response(200, request_id, self.server.service.get_catalog())
            elif method == "GET" and path == "/api/runs":
                status_code = self._json_response(200, request_id, {"runs": self.server.service.list_runs()})
            elif method == "GET" and path.startswith("/api/runs/"):
                run_id = path.rsplit("/", 1)[-1]
                run_payload = self.server.service.get_run(run_id)
                if run_payload is None:
                    raise ApiError(404, f"Run not found: {run_id}")
                status_code = self._json_response(200, request_id, run_payload)
            elif method == "GET" and path.startswith("/api/reports/"):
                run_id = path.rsplit("/", 1)[-1]
                run_payload = self.server.service.get_run(run_id)
                if run_payload is None or not run_payload.get("report_path"):
                    raise ApiError(404, f"Report not found for run: {run_id}")
                status_code = self._file_response(200, request_id, Path(run_payload["report_path"]), "text/markdown; charset=utf-8")
            elif method == "POST" and path == "/api/runs":
                payload = self._read_json()
                if not isinstance(payload, dict):
                    raise ApiError(400, "Request body must be a JSON object.")
                run_payload = self.server.service.start_run(
                    provider_names=_read_string_list(payload.get("provider_names")),
                    case_ids=_read_string_list(payload.get("case_ids")),
                )
                status_code = self._json_response(202, request_id, run_payload)
            else:
                status_code = self._serve_static(path, request_id)
        except ApiError as exc:
            status_code = self._json_response(exc.status_code, request_id, {"error": exc.message})
        except Exception as exc:
            status_code = self._json_response(500, request_id, {"error": str(exc)})
        finally:
            duration_ms = round((time.perf_counter() - started) * 1000, 2)
            print(
                json.dumps(
                    {
                        "request_id": request_id,
                        "method": method,
                        "path": self.path,
                        "status_code": status_code,
                        "duration_ms": duration_ms,
                    },
                    ensure_ascii=False,
                ),
                flush=True,
            )

    def _serve_static(self, request_path: str, request_id: str) -> int:
        relative_path = "index.html" if request_path in ("", "/") else request_path.lstrip("/")
        candidate = (self.server.settings.web_dir / relative_path).resolve()
        web_root = self.server.settings.web_dir.resolve()
        if web_root not in candidate.parents and candidate != web_root:
            raise ApiError(403, "Static path is outside the web root.")
        if not candidate.exists() or not candidate.is_file():
            raise ApiError(404, f"Static asset not found: {relative_path}")

        content_type = _guess_content_type(candidate)
        return self._file_response(200, request_id, candidate, content_type)

    def _json_response(self, status_code: int, request_id: str, payload: Any) -> int:
        raw_body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status_code)
        self._write_common_headers("application/json; charset=utf-8", request_id, len(raw_body))
        self.end_headers()
        self.wfile.write(raw_body)
        return status_code

    def _file_response(self, status_code: int, request_id: str, file_path: Path, content_type: str) -> int:
        body = file_path.read_bytes()
        self.send_response(status_code)
        self._write_common_headers(content_type, request_id, len(body))
        self.end_headers()
        self.wfile.write(body)
        return status_code

    def _write_common_headers(self, content_type: str, request_id: str, content_length: int | None = None) -> None:
        self.send_header("Content-Type", content_type)
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Request-Id", request_id)
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("X-Frame-Options", "DENY")
        self.send_header("Referrer-Policy", "same-origin")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")

        origin = self.headers.get("Origin")
        if origin and origin in self.server.settings.allowed_origins:
            self.send_header("Access-Control-Allow-Origin", origin)
            self.send_header("Vary", "Origin")

        if content_length is not None:
            self.send_header("Content-Length", str(content_length))

    def _read_json(self) -> Any:
        content_length = self.headers.get("Content-Length")
        if not content_length:
            return {}
        length = int(content_length)
        raw_body = self.rfile.read(length).decode("utf-8")
        if not raw_body.strip():
            return {}
        try:
            return json.loads(raw_body)
        except json.JSONDecodeError as exc:
            raise ApiError(400, f"Invalid JSON body: {exc.msg}") from exc


def _read_string_list(value: Any) -> list[str] | None:
    if value is None:
        return None
    if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
        raise ApiError(400, "Expected a list of strings.")
    return value


def _guess_content_type(file_path: Path) -> str:
    suffix = file_path.suffix.lower()
    if suffix == ".html":
        return "text/html; charset=utf-8"
    if suffix == ".js":
        return "application/javascript; charset=utf-8"
    if suffix == ".css":
        return "text/css; charset=utf-8"
    if suffix == ".json":
        return "application/json; charset=utf-8"
    if suffix == ".md":
        return "text/markdown; charset=utf-8"
    return "application/octet-stream"


def main() -> None:
    settings = Settings.from_env()
    service = VerificationService(settings)
    server = ModelVerifierHTTPServer((settings.host, settings.port), settings, service)
    print(f"Model Verifier running at http://{settings.host}:{settings.port}", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == "__main__":
    main()

