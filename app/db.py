from __future__ import annotations

import json
import sqlite3
from typing import Any

from .config import Settings


def init_db(settings: Settings) -> None:
    with _connect(settings) as connection:
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS runs (
                id TEXT PRIMARY KEY,
                status TEXT NOT NULL,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                request_json TEXT NOT NULL,
                summary_json TEXT,
                error_text TEXT,
                report_path TEXT,
                report_json_path TEXT
            )
            """
        )
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS case_results (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                run_id TEXT NOT NULL,
                provider_name TEXT NOT NULL,
                provider_model TEXT NOT NULL,
                case_id TEXT NOT NULL,
                case_title TEXT NOT NULL,
                status TEXT NOT NULL,
                score REAL NOT NULL,
                latency_ms INTEGER NOT NULL,
                response_text TEXT NOT NULL,
                evaluation_json TEXT NOT NULL,
                raw_json TEXT NOT NULL,
                created_at TEXT NOT NULL,
                FOREIGN KEY(run_id) REFERENCES runs(id)
            )
            """
        )
        connection.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_case_results_run_id
            ON case_results(run_id)
            """
        )
        _ensure_column(connection, "case_results", "sample_index", "INTEGER NOT NULL DEFAULT 0")


def create_run(settings: Settings, run_id: str, status: str, created_at: str, request_payload: dict[str, Any]) -> None:
    with _connect(settings) as connection:
        connection.execute(
            """
            INSERT INTO runs (id, status, created_at, updated_at, request_json)
            VALUES (?, ?, ?, ?, ?)
            """,
            (run_id, status, created_at, created_at, _dump_json(request_payload)),
        )


def update_run_status(settings: Settings, run_id: str, status: str, updated_at: str, error_text: str | None = None) -> None:
    with _connect(settings) as connection:
        connection.execute(
            """
            UPDATE runs
            SET status = ?, updated_at = ?, error_text = ?
            WHERE id = ?
            """,
            (status, updated_at, error_text, run_id),
        )


def finalize_run(
    settings: Settings,
    run_id: str,
    status: str,
    updated_at: str,
    summary: dict[str, Any],
    report_path: str,
    report_json_path: str,
) -> None:
    with _connect(settings) as connection:
        connection.execute(
            """
            UPDATE runs
            SET status = ?, updated_at = ?, summary_json = ?, report_path = ?, report_json_path = ?, error_text = NULL
            WHERE id = ?
            """,
            (status, updated_at, _dump_json(summary), report_path, report_json_path, run_id),
        )


def insert_case_result(settings: Settings, payload: dict[str, Any]) -> None:
    with _connect(settings) as connection:
        connection.execute(
            """
            INSERT INTO case_results (
                run_id,
                provider_name,
                provider_model,
                case_id,
                case_title,
                status,
                score,
                latency_ms,
                response_text,
                evaluation_json,
                raw_json,
                sample_index,
                created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                payload["run_id"],
                payload["provider_name"],
                payload["provider_model"],
                payload["case_id"],
                payload["case_title"],
                payload["status"],
                payload["score"],
                payload["latency_ms"],
                payload["response_text"],
                _dump_json(payload["evaluation"]),
                _dump_json(payload["raw"]),
                payload.get("sample_index", 0),
                payload["created_at"],
            ),
        )


def list_runs(settings: Settings, limit: int = 20) -> list[dict[str, Any]]:
    with _connect(settings) as connection:
        rows = connection.execute(
            """
            SELECT id, status, created_at, updated_at, request_json, summary_json, error_text, report_path, report_json_path
            FROM runs
            ORDER BY created_at DESC
            LIMIT ?
            """,
            (limit,),
        ).fetchall()
    return [_decode_run(row) for row in rows]


def get_run(settings: Settings, run_id: str) -> dict[str, Any] | None:
    with _connect(settings) as connection:
        run_row = connection.execute(
            """
            SELECT id, status, created_at, updated_at, request_json, summary_json, error_text, report_path, report_json_path
            FROM runs
            WHERE id = ?
            """,
            (run_id,),
        ).fetchone()
        if run_row is None:
            return None

        result_rows = connection.execute(
            """
            SELECT provider_name, provider_model, case_id, case_title, status, score, latency_ms, response_text, evaluation_json, raw_json, sample_index, created_at
            FROM case_results
            WHERE run_id = ?
            ORDER BY provider_name ASC, case_id ASC, sample_index ASC
            """,
            (run_id,),
        ).fetchall()

    run_payload = _decode_run(run_row)
    run_payload["results"] = [_decode_case_result(row) for row in result_rows]
    return run_payload


def _connect(settings: Settings) -> sqlite3.Connection:
    connection = sqlite3.connect(settings.database_path, timeout=30)
    connection.row_factory = sqlite3.Row
    return connection


def _decode_run(row: sqlite3.Row) -> dict[str, Any]:
    return {
        "id": row["id"],
        "status": row["status"],
        "created_at": row["created_at"],
        "updated_at": row["updated_at"],
        "request": _load_json(row["request_json"]),
        "summary": _load_json(row["summary_json"]),
        "error_text": row["error_text"],
        "report_path": row["report_path"],
        "report_json_path": row["report_json_path"],
    }


def _decode_case_result(row: sqlite3.Row) -> dict[str, Any]:
    return {
        "provider_name": row["provider_name"],
        "provider_model": row["provider_model"],
        "case_id": row["case_id"],
        "case_title": row["case_title"],
        "status": row["status"],
        "score": row["score"],
        "latency_ms": row["latency_ms"],
        "response_text": row["response_text"],
        "evaluation": _load_json(row["evaluation_json"]),
        "raw": _load_json(row["raw_json"]),
        "sample_index": row["sample_index"],
        "created_at": row["created_at"],
    }


def _dump_json(payload: Any) -> str:
    return json.dumps(payload, ensure_ascii=False, sort_keys=True)


def _load_json(raw_value: str | None) -> Any:
    if raw_value is None:
        return None
    return json.loads(raw_value)


def _ensure_column(connection: sqlite3.Connection, table_name: str, column_name: str, definition: str) -> None:
    columns = {
        row["name"]
        for row in connection.execute(f"PRAGMA table_info({table_name})").fetchall()
    }
    if column_name in columns:
        return
    connection.execute(f"ALTER TABLE {table_name} ADD COLUMN {column_name} {definition}")
