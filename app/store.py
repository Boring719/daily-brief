"""本地存储：SQLite 保存每天生成的简报，供历史回看。"""
from __future__ import annotations

import json
import sqlite3
from datetime import datetime
from pathlib import Path

from .config import DATA_DIR, ensure_dirs


def _conn() -> sqlite3.Connection:
    ensure_dirs()
    conn = sqlite3.connect(str(DATA_DIR / "brief.db"), timeout=15)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    return conn


def init_db() -> None:
    with _conn() as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS reports (
                date        TEXT PRIMARY KEY,
                generated_at TEXT NOT NULL,
                title       TEXT,
                one_line    TEXT,
                digest_json TEXT,
                html        TEXT,
                stats_json  TEXT
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS runs (
                id         INTEGER PRIMARY KEY AUTOINCREMENT,
                started_at TEXT,
                date       TEXT,
                ok         INTEGER,
                message    TEXT
            )
            """
        )


def save_report(date: str, digest: dict, html: str, stats: dict) -> None:
    init_db()
    with _conn() as conn:
        conn.execute(
            "INSERT OR REPLACE INTO reports (date, generated_at, title, one_line, digest_json, html, stats_json)"
            " VALUES (?, ?, ?, ?, ?, ?, ?)",
            (
                date,
                datetime.now().isoformat(timespec="seconds"),
                digest.get("title") or f"{date} 国际简报",
                digest.get("one_line") or "",
                json.dumps(digest, ensure_ascii=False),
                html,
                json.dumps(stats, ensure_ascii=False),
            ),
        )


def get_report(date: str) -> dict | None:
    init_db()
    with _conn() as conn:
        row = conn.execute("SELECT * FROM reports WHERE date = ?", (date,)).fetchone()
    return dict(row) if row else None


def latest_report() -> dict | None:
    init_db()
    with _conn() as conn:
        row = conn.execute("SELECT * FROM reports ORDER BY date DESC LIMIT 1").fetchone()
    return dict(row) if row else None


def list_dates() -> list[str]:
    init_db()
    with _conn() as conn:
        rows = conn.execute("SELECT date FROM reports ORDER BY date DESC").fetchall()
    return [r["date"] for r in rows]


def log_run(date: str, ok: bool, message: str, started_at: datetime | None = None) -> None:
    init_db()
    with _conn() as conn:
        conn.execute(
            "INSERT INTO runs (started_at, date, ok, message) VALUES (?, ?, ?, ?)",
            ((started_at or datetime.now()).isoformat(timespec="seconds"), date, 1 if ok else 0, message[:2000]),
        )
