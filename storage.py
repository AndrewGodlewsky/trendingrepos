"""SQLite-backed storage for the GitHub trending pipeline.

Database: data/trending.db

Tables
------
daily_snapshots
    One row per (run_date, github_id). Accumulates every daily run so history
    is never lost — query any past date or compute star-growth trends over time.

summaries
    One row per repo URL. Upserted after every summarization run; never purged.

Public API
----------
get_db(data_dir)                          -> sqlite3.Connection (caller must close)
save_repos(data_dir, repos)               -> None
load_repos(data_dir, run_date=None)       -> list[dict]   (latest run by default)
load_previous_repos(data_dir)             -> list[dict]   (second-most-recent run)
get_run_dates(data_dir)                   -> list[str]    (all dates, newest first)
get_history(data_dir, github_id, days=30) -> list[dict]   (daily stars/rank rows)

save_summaries(data_dir, summaries)       -> None
load_summaries(data_dir)                  -> dict[str, dict]   (keyed by html_url)

compute_star_deltas(current, previous)    -> dict[int, int|None]  (compat helper)
write_json(path, obj)                     -> None   (kept for docs/data.json writes)
read_json(path, default)                  -> Any    (kept as general utility)

# Backward-compat names referenced by other modules
rotate_snapshots(data_dir)                -> bool   (no-op; DB retains history natively)
REPOS_FILE, PREVIOUS_REPOS_FILE, SUMMARIES_FILE  (constants; not actively written)
"""
from __future__ import annotations

import json
import logging
import sqlite3
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants kept for backward-compat (no longer written to disk)
# ---------------------------------------------------------------------------
DB_FILE = "trending.db"
REPOS_FILE = "repos.json"
PREVIOUS_REPOS_FILE = "previous_repos.json"
SUMMARIES_FILE = "summaries.json"


# ---------------------------------------------------------------------------
# Schema
# ---------------------------------------------------------------------------

_DDL = """
PRAGMA journal_mode = WAL;
PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS daily_snapshots (
    run_date        TEXT    NOT NULL,
    github_id       INTEGER NOT NULL,
    full_name       TEXT    NOT NULL,
    name            TEXT    NOT NULL,
    description     TEXT,
    stars           INTEGER DEFAULT 0,
    forks           INTEGER DEFAULT 0,
    watchers        INTEGER DEFAULT 0,
    open_issues     INTEGER DEFAULT 0,
    language        TEXT,
    topics          TEXT    DEFAULT '[]',
    license         TEXT,
    homepage        TEXT,
    html_url        TEXT    NOT NULL,
    created_at      TEXT    DEFAULT '',
    updated_at      TEXT    DEFAULT '',
    pushed_at       TEXT    DEFAULT '',
    trending_score  REAL    DEFAULT 0.0,
    stars_7d        INTEGER,
    rank            INTEGER DEFAULT 0,
    PRIMARY KEY (run_date, github_id)
);

CREATE INDEX IF NOT EXISTS idx_snap_date     ON daily_snapshots (run_date);
CREATE INDEX IF NOT EXISTS idx_snap_id       ON daily_snapshots (github_id);
CREATE INDEX IF NOT EXISTS idx_snap_url      ON daily_snapshots (html_url);

CREATE TABLE IF NOT EXISTS summaries (
    html_url        TEXT PRIMARY KEY,
    summary         TEXT DEFAULT '',
    tags            TEXT DEFAULT '[]',
    summarized_at   TEXT NOT NULL
);
"""


# ---------------------------------------------------------------------------
# Connection helper
# ---------------------------------------------------------------------------

def get_db(data_dir: "str | Path") -> sqlite3.Connection:
    """Open (and if needed, initialise) the SQLite database.

    The caller is responsible for closing the returned connection.
    Uses row_factory = sqlite3.Row so columns are accessible by name.
    """
    db_path = Path(data_dir) / DB_FILE
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    conn.executescript(_DDL)
    return conn


# ---------------------------------------------------------------------------
# Repo snapshots
# ---------------------------------------------------------------------------

def save_repos(data_dir: "str | Path", repos: List[dict], run_date: str = None) -> None:
    """Insert today's ranked repo list into daily_snapshots.

    Deletes any existing rows for the same run_date first, so re-running the
    pipeline on the same day is idempotent.

    Args:
        data_dir: Directory that contains (or will contain) trending.db.
        repos:    List of repo dicts (RankedRepo.to_dict() output).
        run_date: ISO date string (YYYY-MM-DD). Defaults to today.
    """
    if run_date is None:
        run_date = date.today().isoformat()

    conn = get_db(data_dir)
    try:
        conn.execute("DELETE FROM daily_snapshots WHERE run_date = ?", (run_date,))

        rows = [
            (
                run_date,
                int(r.get("github_id", 0)),
                r.get("full_name", ""),
                r.get("name", ""),
                r.get("description"),
                int(r.get("stars", 0)),
                int(r.get("forks", 0)),
                int(r.get("watchers", 0)),
                int(r.get("open_issues", 0)),
                r.get("language"),
                json.dumps(list(r.get("topics") or []), ensure_ascii=False),
                r.get("license"),
                r.get("homepage"),
                r.get("html_url", ""),
                r.get("created_at", ""),
                r.get("updated_at", ""),
                r.get("pushed_at", ""),
                float(r.get("trending_score", 0.0)),
                r.get("stars_7d"),
                int(r.get("rank", 0)),
            )
            for r in repos
        ]

        conn.executemany(
            """INSERT INTO daily_snapshots (
                   run_date, github_id, full_name, name, description,
                   stars, forks, watchers, open_issues, language,
                   topics, license, homepage, html_url, created_at,
                   updated_at, pushed_at, trending_score, stars_7d, rank
               ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            rows,
        )
        conn.commit()
        logger.info("Saved %d repos for run_date=%s to trending.db", len(repos), run_date)
    finally:
        conn.close()


def load_repos(data_dir: "str | Path", run_date: str = None) -> List[dict]:
    """Return the ranked repo list for a specific date (or the most recent run).

    Args:
        data_dir: Directory containing trending.db.
        run_date: ISO date string, or None to load the most recent run.

    Returns:
        List of repo dicts ordered by rank ascending. Empty list if no data.
    """
    conn = get_db(data_dir)
    try:
        if run_date is None:
            row = conn.execute(
                "SELECT run_date FROM daily_snapshots ORDER BY run_date DESC LIMIT 1"
            ).fetchone()
            if not row:
                return []
            run_date = row[0]

        rows = conn.execute(
            "SELECT * FROM daily_snapshots WHERE run_date = ? ORDER BY rank ASC",
            (run_date,),
        ).fetchall()

        return [_row_to_dict(r) for r in rows]
    finally:
        conn.close()


def load_previous_repos(data_dir: "str | Path") -> List[dict]:
    """Return repos from the second-most-recent run (used for star delta computation).

    Returns an empty list on first run (only one date in the DB).
    """
    conn = get_db(data_dir)
    try:
        dates = conn.execute(
            "SELECT DISTINCT run_date FROM daily_snapshots ORDER BY run_date DESC LIMIT 2"
        ).fetchall()

        if len(dates) < 2:
            logger.debug("load_previous_repos: fewer than 2 run dates — returning []")
            return []

        prev_date = dates[1][0]
        rows = conn.execute(
            "SELECT * FROM daily_snapshots WHERE run_date = ? ORDER BY rank ASC",
            (prev_date,),
        ).fetchall()

        logger.debug("load_previous_repos: loaded %d repos from %s", len(rows), prev_date)
        return [_row_to_dict(r) for r in rows]
    finally:
        conn.close()


def get_run_dates(data_dir: "str | Path") -> List[str]:
    """Return all distinct run dates, newest first."""
    conn = get_db(data_dir)
    try:
        rows = conn.execute(
            "SELECT DISTINCT run_date FROM daily_snapshots ORDER BY run_date DESC"
        ).fetchall()
        return [r[0] for r in rows]
    finally:
        conn.close()


def get_history(data_dir: "str | Path", github_id: int, days: int = 30) -> List[dict]:
    """Return per-day star counts and rank for a repo over the last N run dates.

    Useful for generating sparkline charts on the website.

    Args:
        data_dir:  Directory containing trending.db.
        github_id: GitHub repository ID.
        days:      Maximum number of historical entries to return.

    Returns:
        List of dicts with keys: run_date, stars, rank, trending_score. Ordered
        oldest first so chart libraries can plot them directly.
    """
    conn = get_db(data_dir)
    try:
        rows = conn.execute(
            """SELECT run_date, stars, rank, trending_score
               FROM daily_snapshots
               WHERE github_id = ?
               ORDER BY run_date ASC
               LIMIT ?""",
            (github_id, days),
        ).fetchall()
        return [
            {
                "run_date": r["run_date"],
                "stars": r["stars"],
                "rank": r["rank"],
                "trending_score": r["trending_score"],
            }
            for r in rows
        ]
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Summaries
# ---------------------------------------------------------------------------

def save_summaries(data_dir: "str | Path", summaries: Dict[str, dict]) -> None:
    """Upsert all entries in *summaries* into the summaries table.

    Args:
        data_dir:  Directory containing trending.db.
        summaries: Dict keyed by html_url; values have "summary" and "tags" keys.
    """
    conn = get_db(data_dir)
    now = datetime.now(timezone.utc).isoformat()
    try:
        rows = [
            (
                url,
                entry.get("summary", ""),
                json.dumps(list(entry.get("tags") or []), ensure_ascii=False),
                now,
            )
            for url, entry in summaries.items()
        ]

        conn.executemany(
            """INSERT INTO summaries (html_url, summary, tags, summarized_at)
               VALUES (?, ?, ?, ?)
               ON CONFLICT(html_url) DO UPDATE SET
                   summary       = excluded.summary,
                   tags          = excluded.tags,
                   summarized_at = excluded.summarized_at""",
            rows,
        )
        conn.commit()
        logger.info("Upserted %d summaries into trending.db", len(rows))
    finally:
        conn.close()


def load_summaries(data_dir: "str | Path") -> Dict[str, dict]:
    """Return all summaries as a dict keyed by html_url.

    Returns:
        Dict mapping html_url -> {"summary": str, "tags": list[str]}.
        Empty dict if no summaries have been saved yet.
    """
    conn = get_db(data_dir)
    try:
        rows = conn.execute(
            "SELECT html_url, summary, tags FROM summaries"
        ).fetchall()

        result: Dict[str, dict] = {}
        for r in rows:
            try:
                tags = json.loads(r["tags"] or "[]")
            except json.JSONDecodeError:
                tags = []
            result[r["html_url"]] = {"summary": r["summary"] or "", "tags": tags}

        return result
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Backward-compat helpers
# ---------------------------------------------------------------------------

def rotate_snapshots(data_dir: "str | Path") -> bool:
    """No-op. SQLite retains all historical snapshots natively.

    Kept so existing callers in fetcher.py don't need to change.
    Returns False (matching the old "nothing to rotate on first run" return).
    """
    logger.debug("rotate_snapshots: no-op with SQLite backend")
    return False


def compute_star_deltas(
    current_list: List[dict],
    previous_list: List[dict],
) -> Dict[int, Optional[int]]:
    """Compute per-repo star deltas between two ranked-repo lists.

    Kept for backward compatibility with fetcher.py. Both arguments are plain
    dicts (as returned by load_repos / load_previous_repos).

    Args:
        current_list:  Today's ranked repo dicts.
        previous_list: Yesterday's ranked repo dicts.

    Returns:
        Mapping github_id -> (current_stars - previous_stars), or None if the
        repo was absent from the previous snapshot.
    """
    previous_stars: Dict[int, int] = {
        int(r["github_id"]): int(r.get("stars", 0))
        for r in previous_list
        if "github_id" in r
    }

    deltas: Dict[int, Optional[int]] = {}
    for repo in current_list:
        gid = int(repo.get("github_id", 0))
        if gid in previous_stars:
            deltas[gid] = int(repo.get("stars", 0)) - previous_stars[gid]
        else:
            deltas[gid] = None

    return deltas


# ---------------------------------------------------------------------------
# General file I/O (kept for writing docs/data.json and other non-DB outputs)
# ---------------------------------------------------------------------------

def write_json(path: "str | Path", obj: Any) -> None:
    """Atomically write *obj* as indented JSON to *path*.

    Writes to a .tmp file first, then renames over the target. Safe on Windows
    (pathlib.Path.replace overwrites the destination).
    """
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = path.with_suffix(path.suffix + ".tmp")
    try:
        text = json.dumps(obj, indent=2, ensure_ascii=False)
        tmp_path.write_text(text, encoding="utf-8")
        tmp_path.replace(path)
        logger.debug("Wrote %s (%d bytes)", path, len(text))
    except Exception:
        try:
            tmp_path.unlink(missing_ok=True)
        except OSError:
            pass
        raise


def read_json(path: "str | Path", default: Any = None) -> Any:
    """Read and parse JSON from *path*; return *default* if missing or corrupt."""
    path = Path(path)
    if not path.exists():
        return default
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as exc:
        logger.warning("read_json: could not parse %s (%s)", path, exc)
        return default


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _row_to_dict(row: sqlite3.Row) -> dict:
    """Convert a daily_snapshots Row to a plain dict, deserialising topics JSON."""
    d = dict(row)
    try:
        d["topics"] = json.loads(d.get("topics") or "[]")
    except json.JSONDecodeError:
        d["topics"] = []
    return d
