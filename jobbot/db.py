"""SQLite storage for collected jobs, the answer bank, and application history."""

from __future__ import annotations

import json
import sqlite3
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator

SCHEMA = """
CREATE TABLE IF NOT EXISTS jobs (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    source          TEXT NOT NULL,              -- 'linkedin' | 'naukri'
    external_id     TEXT NOT NULL,              -- platform job id
    url             TEXT NOT NULL,
    title           TEXT,
    company         TEXT,
    location        TEXT,
    workplace_type  TEXT,                       -- remote / hybrid / onsite
    salary          TEXT,
    experience      TEXT,
    posted          TEXT,                       -- raw "2 days ago" style string
    easy_apply      INTEGER DEFAULT 0,          -- 1 if in-platform apply
    description     TEXT,
    search_query    TEXT,                       -- which saved search found it
    collected_at    INTEGER NOT NULL,

    -- filled in by later stages
    status          TEXT NOT NULL DEFAULT 'new',-- new|scored|queued|applied|skipped|failed
    fit_score       INTEGER,
    fit_reason      TEXT,
    raw             TEXT,                       -- JSON blob of anything extra

    UNIQUE (source, external_id)
);

CREATE INDEX IF NOT EXISTS idx_jobs_status ON jobs (status);
CREATE INDEX IF NOT EXISTS idx_jobs_score  ON jobs (fit_score DESC);

-- Cache of application-form questions you've already answered, so the
-- applier can auto-fill repeats (notice period, expected CTC, "years of X").
CREATE TABLE IF NOT EXISTS answers (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    question_key    TEXT NOT NULL UNIQUE,       -- normalised question text
    question_raw    TEXT NOT NULL,
    answer          TEXT NOT NULL,
    field_type      TEXT,                       -- text|number|select|radio|checkbox
    times_used      INTEGER DEFAULT 0,
    updated_at      INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS applications (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    job_id          INTEGER NOT NULL REFERENCES jobs(id),
    applied_at      INTEGER NOT NULL,
    mode            TEXT,                       -- auto|manual
    outcome         TEXT,                       -- submitted|abandoned|error
    notes           TEXT
);

-- Accounts created on external ATS platforms (Workday, iCIMS, Oracle Cloud
-- Recruiting, ...) so an application can log back in on a later visit.
-- One row per employer's own tenant -- Workday, for example, hosts a
-- separate account per company, not one account across all Workday jobs.
-- Deliberately its own table, kept out of every export/print path that
-- walks `jobs` or `answers`.
CREATE TABLE IF NOT EXISTS external_credentials (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    platform        TEXT NOT NULL,              -- 'workday' | 'icims' | 'oracle_cloud' | ...
    tenant          TEXT NOT NULL,              -- host, e.g. mrisoftware.wd501.myworkdayjobs.com
    email           TEXT NOT NULL,
    password        TEXT NOT NULL,
    created_at      INTEGER NOT NULL,
    notes           TEXT,
    UNIQUE (platform, tenant, email)
);

-- Cache of "does this new question mean the same as one we already
-- answered?" lookups (see question_matcher.py). Each distinct question
-- wording is resolved via Claude at most once, ever -- matched_key is
-- NULL for a confident "no match", which is itself a valid cached result
-- and must not be re-queried. Answer VALUES are never stored here or sent
-- to the API -- only question text and the answers table's existing keys.
CREATE TABLE IF NOT EXISTS question_aliases (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    question_raw    TEXT NOT NULL UNIQUE,       -- normalised (stripped/lowered) incoming question
    matched_key     TEXT,                       -- answers.question_key this means the same as, or NULL
    created_at      INTEGER NOT NULL
);
"""


class Store:
    def __init__(self, path: str | Path = "jobs.db"):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(self.path)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA journal_mode=WAL")
        self.conn.executescript(SCHEMA)
        self.conn.commit()
        self._migrate()

    def _migrate(self) -> None:
        cols = {row["name"] for row in self.conn.execute("PRAGMA table_info(jobs)")}
        if "resume_path" not in cols:
            self.conn.execute("ALTER TABLE jobs ADD COLUMN resume_path TEXT")
            self.conn.commit()

    # ---------- jobs ----------

    def upsert_job(self, job: dict[str, Any]) -> tuple[int, bool]:
        """Insert a job. Returns (row_id, was_new).

        Existing rows are never overwritten wholesale -- we only backfill
        fields that were empty, so a re-run doesn't wipe fit scores or status.
        """
        now = int(time.time())
        cur = self.conn.execute(
            "SELECT id, description FROM jobs WHERE source = ? AND external_id = ?",
            (job["source"], str(job["external_id"])),
        )
        row = cur.fetchone()

        if row:
            # backfill description if we now have a fuller one
            if job.get("description") and not row["description"]:
                self.conn.execute(
                    "UPDATE jobs SET description = ? WHERE id = ?",
                    (job["description"], row["id"]),
                )
                self.conn.commit()
            return row["id"], False

        cur = self.conn.execute(
            """
            INSERT INTO jobs (source, external_id, url, title, company, location,
                              workplace_type, salary, experience, posted, easy_apply,
                              description, search_query, collected_at, raw)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            """,
            (
                job["source"],
                str(job["external_id"]),
                job["url"],
                job.get("title"),
                job.get("company"),
                job.get("location"),
                job.get("workplace_type"),
                job.get("salary"),
                job.get("experience"),
                job.get("posted"),
                1 if job.get("easy_apply") else 0,
                job.get("description"),
                job.get("search_query"),
                now,
                json.dumps(job.get("raw", {}), ensure_ascii=False),
            ),
        )
        self.conn.commit()
        return cur.lastrowid, True

    def seen(self, source: str, external_id: str) -> bool:
        cur = self.conn.execute(
            "SELECT 1 FROM jobs WHERE source = ? AND external_id = ?",
            (source, str(external_id)),
        )
        return cur.fetchone() is not None

    def jobs(self, status: str | None = None, limit: int = 100) -> list[sqlite3.Row]:
        if status:
            sql = "SELECT * FROM jobs WHERE status = ? ORDER BY collected_at DESC LIMIT ?"
            return self.conn.execute(sql, (status, limit)).fetchall()
        sql = "SELECT * FROM jobs ORDER BY collected_at DESC LIMIT ?"
        return self.conn.execute(sql, (limit,)).fetchall()

    def set_status(self, job_id: int, status: str) -> None:
        self.conn.execute("UPDATE jobs SET status = ? WHERE id = ?", (status, job_id))
        self.conn.commit()

    def set_score(self, job_id: int, score: int, reason: str) -> None:
        self.conn.execute(
            "UPDATE jobs SET fit_score = ?, fit_reason = ?, status = 'scored' WHERE id = ?",
            (score, reason, job_id),
        )
        self.conn.commit()

    def set_resume_path(self, job_id: int, path: str) -> None:
        self.conn.execute("UPDATE jobs SET resume_path = ? WHERE id = ?", (path, job_id))
        self.conn.commit()

    def stats(self) -> dict[str, int]:
        rows = self.conn.execute(
            "SELECT status, COUNT(*) AS n FROM jobs GROUP BY status"
        ).fetchall()
        out = {r["status"]: r["n"] for r in rows}
        out["total"] = sum(out.values())
        return out

    # ---------- answer bank ----------

    @staticmethod
    def normalise(question: str) -> str:
        return " ".join(question.lower().split())[:300]

    def get_answer(self, question: str) -> sqlite3.Row | None:
        return self.conn.execute(
            "SELECT * FROM answers WHERE question_key = ?", (self.normalise(question),)
        ).fetchone()

    def put_answer(
        self, question: str, answer: str, field_type: str = "text", display_question: str | None = None
    ) -> None:
        self.conn.execute(
            """
            INSERT INTO answers (question_key, question_raw, answer, field_type, updated_at)
            VALUES (?,?,?,?,?)
            ON CONFLICT(question_key) DO UPDATE SET
                answer = excluded.answer,
                field_type = excluded.field_type,
                updated_at = excluded.updated_at
            """,
            (
                self.normalise(question),
                display_question or question,
                answer,
                field_type,
                int(time.time()),
            ),
        )
        self.conn.commit()

    def get_question_alias(self, question: str) -> sqlite3.Row | None:
        """A previously-resolved "does this mean the same as X" lookup, or
        None if this exact wording has never been checked before. NOTE: a
        *found* row can still have matched_key = NULL -- that's a cached
        confident "no match", not a cache miss; callers must distinguish
        "row is None" (never checked) from "row.matched_key is None"
        (checked, no match)."""
        return self.conn.execute(
            "SELECT * FROM question_aliases WHERE question_raw = ?", (self.normalise(question),)
        ).fetchone()

    def put_question_alias(self, question: str, matched_key: str | None) -> None:
        self.conn.execute(
            """
            INSERT INTO question_aliases (question_raw, matched_key, created_at)
            VALUES (?,?,?)
            ON CONFLICT(question_raw) DO UPDATE SET
                matched_key = excluded.matched_key,
                created_at = excluded.created_at
            """,
            (self.normalise(question), matched_key, int(time.time())),
        )
        self.conn.commit()

    def known_questions(self, limit: int = 60) -> list[sqlite3.Row]:
        """Distinct already-answered questions (key + display text only --
        never the answer value), most recently used first. Used as the
        candidate list for question_matcher's semantic matching."""
        return self.conn.execute(
            "SELECT question_key, question_raw FROM answers ORDER BY updated_at DESC LIMIT ?",
            (limit,),
        ).fetchall()

    def close(self) -> None:
        self.conn.close()

    # ---------- external ATS credentials ----------
    # Kept separate from every other table on purpose: nothing that exports
    # or prints `jobs`/`answers` should ever accidentally walk this one too.

    def get_external_credential(self, platform: str, tenant: str) -> sqlite3.Row | None:
        return self.conn.execute(
            "SELECT * FROM external_credentials WHERE platform = ? AND tenant = ? "
            "ORDER BY id DESC LIMIT 1",
            (platform, tenant),
        ).fetchone()

    def save_external_credential(
        self, platform: str, tenant: str, email: str, password: str, notes: str = ""
    ) -> None:
        self.conn.execute(
            """
            INSERT INTO external_credentials (platform, tenant, email, password, created_at, notes)
            VALUES (?,?,?,?,?,?)
            ON CONFLICT(platform, tenant, email) DO UPDATE SET
                password = excluded.password,
                notes = excluded.notes
            """,
            (platform, tenant, email, password, int(time.time()), notes),
        )
        self.conn.commit()

    # ---------- applications ----------

    def applications_today(self) -> int:
        # Only 'submitted' and 'awaiting_review' represent a real attempt at
        # the job (form was actually opened and worked with). 'skipped'
        # (wrong platform, expired posting) and 'error' (a bug, a selector
        # mismatch) never got that far and shouldn't burn a daily slot.
        cur = self.conn.execute(
            "SELECT COUNT(*) AS n FROM applications "
            "WHERE outcome IN ('submitted', 'awaiting_review') "
            "AND date(applied_at, 'unixepoch', 'localtime') = date('now', 'localtime')"
        )
        return cur.fetchone()["n"]

    def log_application(
        self, job_id: int, mode: str, outcome: str, notes: str = ""
    ) -> None:
        self.conn.execute(
            "INSERT INTO applications (job_id, applied_at, mode, outcome, notes) "
            "VALUES (?,?,?,?,?)",
            (job_id, int(time.time()), mode, outcome, notes),
        )
        status = (
            "applied" if outcome in ("submitted", "already_applied")
            else "failed" if outcome == "error"
            else "skipped"
        )
        self.conn.execute("UPDATE jobs SET status = ? WHERE id = ?", (status, job_id))
        self.conn.commit()


@contextmanager
def open_store(path: str | Path = "jobs.db") -> Iterator[Store]:
    store = Store(path)
    try:
        yield store
    finally:
        store.close()
