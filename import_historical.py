"""Import historical CSV snapshots into data/trending.db.

Reads every CSV in data/Historical Data/, computes trending scores and
ranks using the same formula as ranker.py (but relative to each
snapshot's date), and bulk-inserts into daily_snapshots.

Existing rows are never overwritten — INSERT OR IGNORE means rows
whose (run_date, github_id) already exist are silently skipped.

Usage
-----
    python import_historical.py              # full import
    python import_historical.py --dry-run   # preview only, no writes
"""
from __future__ import annotations

import argparse
import csv
import json
import logging
import sqlite3
import sys
import time
from datetime import date, timedelta
from pathlib import Path

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)-8s %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger(__name__)

HISTORICAL_DIR = Path("data/Historical Data")
DB_PATH = Path("data/trending.db")
BATCH_SIZE = 2000


# ---------------------------------------------------------------------------
# Trending score — same formula as ranker.py, but uses snapshot_date
# as "today" so historical scores are accurate for that point in time.
# ---------------------------------------------------------------------------

def _compute_score(row: dict, snapshot_date: date) -> float:
    try:
        stars       = int(row.get("stars") or 0)
        forks       = int(row.get("forks") or 0)
        watchers    = int(row.get("watchers") or 0)
        open_issues = int(row.get("open_issues") or 0)

        try:
            created = date.fromisoformat((row.get("created_at") or "")[:10])
        except (ValueError, IndexError):
            created = snapshot_date

        try:
            pushed = date.fromisoformat((row.get("pushed_at") or "")[:10])
        except (ValueError, IndexError):
            pushed = snapshot_date

        repo_age_days  = max(1, min(365, (snapshot_date - created).days))
        star_velocity  = stars / repo_age_days
        fork_ratio     = forks  / max(1, stars)
        watcher_ratio  = watchers / max(1, stars)
        days_since_push = max(0, (snapshot_date - pushed).days)
        recency_bonus  = max(0.0, 1.0 - days_since_push / 30)
        issue_health   = 1.0 / (1.0 + (open_issues / max(1, stars)) * 10)

        return round(
            star_velocity  * 0.50
            + fork_ratio   * 0.15
            + watcher_ratio * 0.10
            + recency_bonus * 0.20
            + issue_health  * 0.05,
            6,
        )
    except Exception:
        return 0.0


# ---------------------------------------------------------------------------
# CSV -> DB row conversion
# ---------------------------------------------------------------------------

def _to_db_row(row: dict, run_date: str, score: float, rank: int) -> tuple:
    topics_raw = row.get("topics") or ""
    topics_json = json.dumps(
        [t.strip() for t in topics_raw.split("|") if t.strip()],
        ensure_ascii=False,
    )
    return (
        run_date,
        int(row["github_id"]),
        row.get("full_name") or "",
        row.get("name") or "",
        row.get("description") or None,
        int(row.get("stars") or 0),
        int(row.get("forks") or 0),
        int(row.get("watchers") or 0),
        int(row.get("open_issues") or 0),
        row.get("language") or None,
        topics_json,
        row.get("license") or None,
        row.get("homepage") or None,
        row.get("html_url") or "",
        row.get("created_at") or "",
        row.get("updated_at") or "",
        row.get("pushed_at") or "",
        score,
        None,   # stars_7d — computed separately after all imports
        rank,
    )


INSERT_SQL = """
    INSERT OR IGNORE INTO daily_snapshots (
        run_date, github_id, full_name, name, description,
        stars, forks, watchers, open_issues, language,
        topics, license, homepage, html_url, created_at,
        updated_at, pushed_at, trending_score, stars_7d, rank
    ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
"""


# ---------------------------------------------------------------------------
# stars_7d back-fill — run once after all CSVs are loaded
# ---------------------------------------------------------------------------

def _backfill_stars_7d(conn: sqlite3.Connection) -> int:
    """Set stars_7d = delta from the immediately preceding run per repo.

    Uses a single SQL pass: for every row whose stars_7d is NULL, looks
    up the same github_id on the nearest prior run_date and computes the
    difference.  Returns the number of rows updated.
    """
    logger.info("Back-filling stars_7d from consecutive run deltas...")
    conn.execute("""
        UPDATE daily_snapshots
        SET stars_7d = stars - (
            SELECT prev.stars
            FROM daily_snapshots prev
            WHERE prev.github_id = daily_snapshots.github_id
              AND prev.run_date < daily_snapshots.run_date
            ORDER BY prev.run_date DESC
            LIMIT 1
        )
        WHERE stars_7d IS NULL
          AND (
            SELECT COUNT(*)
            FROM daily_snapshots prev
            WHERE prev.github_id = daily_snapshots.github_id
              AND prev.run_date < daily_snapshots.run_date
          ) > 0
    """)
    updated = conn.execute("SELECT changes()").fetchone()[0]
    conn.commit()
    logger.info("Back-filled stars_7d for %d rows", updated)
    return updated


# ---------------------------------------------------------------------------
# Per-file import
# ---------------------------------------------------------------------------

def _import_file(
    csv_path: Path,
    conn: sqlite3.Connection,
    existing_dates: set[str],
    dry_run: bool,
) -> int:
    """Read one CSV, score and rank its rows, and insert into the DB.

    Returns the number of rows inserted (0 on dry-run or skip).
    """
    t0 = time.monotonic()

    # Read entire file into memory so we can score + rank before inserting.
    # Files are 20-40 MB; Python can handle this comfortably.
    logger.info("Reading %s ...", csv_path.name)
    try:
        with open(csv_path, newline="", encoding="utf-8", errors="replace") as f:
            rows = list(csv.DictReader(f))
    except Exception as exc:
        logger.error("Could not read %s: %s", csv_path.name, exc)
        return 0

    if not rows:
        logger.warning("%s is empty — skipping", csv_path.name)
        return 0

    # Determine the run_date from the snapshot_date column (more reliable
    # than parsing the filename).
    run_date = (rows[0].get("snapshot_date") or "").strip()
    if not run_date:
        logger.warning("%s has no snapshot_date — skipping", csv_path.name)
        return 0

    if run_date in existing_dates:
        logger.info(
            "%s -> date %s already in DB — skipping (%d rows)",
            csv_path.name, run_date, len(rows),
        )
        return 0

    logger.info(
        "%s -> %s | %d rows — scoring...",
        csv_path.name, run_date, len(rows),
    )

    # Score every row relative to this snapshot's date
    try:
        snap_date = date.fromisoformat(run_date)
    except ValueError:
        logger.error("Invalid run_date %r in %s — skipping", run_date, csv_path.name)
        return 0

    scored: list[tuple[float, dict]] = []
    for row in rows:
        try:
            s = _compute_score(row, snap_date)
        except Exception:
            s = 0.0
        scored.append((s, row))

    # Sort descending by score and assign 1-based ranks
    scored.sort(key=lambda t: t[0], reverse=True)

    db_rows = [
        _to_db_row(row, run_date, score, rank_idx)
        for rank_idx, (score, row) in enumerate(scored, start=1)
    ]

    if dry_run:
        logger.info(
            "[DRY RUN] Would insert %d rows for %s (top score=%.4f)",
            len(db_rows), run_date, scored[0][0] if scored else 0,
        )
        return 0

    # Batch insert
    inserted_before = conn.execute(
        "SELECT COUNT(*) FROM daily_snapshots WHERE run_date = ?", (run_date,)
    ).fetchone()[0]

    for i in range(0, len(db_rows), BATCH_SIZE):
        conn.executemany(INSERT_SQL, db_rows[i : i + BATCH_SIZE])
    conn.commit()

    inserted_after = conn.execute(
        "SELECT COUNT(*) FROM daily_snapshots WHERE run_date = ?", (run_date,)
    ).fetchone()[0]
    new_rows = inserted_after - inserted_before

    elapsed = time.monotonic() - t0
    logger.info(
        "  -> inserted %d rows for %s in %.1fs (skipped %d duplicates)",
        new_rows, run_date, elapsed, len(db_rows) - new_rows,
    )

    # Mark this date as seen so subsequent files for the same date
    # (e.g. outputsub1k5.16.26.csv alongside output5.16.26.csv) are
    # processed rather than skipped entirely.
    # We do NOT add run_date to existing_dates here — the sub-file for
    # the same date should still be processed; INSERT OR IGNORE handles
    # duplicate github_ids within the same date automatically.
    return new_rows


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--dry-run", action="store_true",
        help="Preview what would be imported without writing to the DB",
    )
    args = parser.parse_args()

    if not HISTORICAL_DIR.exists():
        logger.error("Historical Data directory not found: %s", HISTORICAL_DIR)
        sys.exit(1)

    csv_files = sorted(HISTORICAL_DIR.glob("*.csv"))
    if not csv_files:
        logger.error("No CSV files found in %s", HISTORICAL_DIR)
        sys.exit(1)

    logger.info("Found %d CSV files in %s", len(csv_files), HISTORICAL_DIR)

    # Open DB connection (get_db also initialises the schema if needed)
    import storage
    conn = storage.get_db("data")

    # Dates already fully loaded — we'll skip these files entirely unless
    # the file is a supplementary file for the same date (the sub1k file).
    existing_dates: set[str] = set(storage.get_run_dates("data"))
    if existing_dates:
        logger.info("Dates already in DB: %s", sorted(existing_dates))

    total_inserted = 0
    t_start = time.monotonic()

    # Sort files so supplementary files (e.g. sub1k) come AFTER the
    # primary file for the same date — this way the primary file sets
    # the date, and the sub file adds extra repos via INSERT OR IGNORE.
    # Sorting alphabetically achieves this since "output5.16" < "outputsub1k5.16".
    for csv_path in sorted(csv_files):
        n = _import_file(csv_path, conn, existing_dates, args.dry_run)
        total_inserted += n

    if not args.dry_run and total_inserted > 0:
        _backfill_stars_7d(conn)

    conn.close()

    elapsed = time.monotonic() - t_start
    logger.info(
        "Import complete: %d rows inserted across %d files in %.1fs (%.1f min)",
        total_inserted, len(csv_files), elapsed, elapsed / 60,
    )

    # Print DB summary
    conn2 = storage.get_db("data")
    dates = storage.get_run_dates("data")
    total_rows = conn2.execute("SELECT COUNT(*) FROM daily_snapshots").fetchone()[0]
    conn2.close()

    logger.info("DB now contains %d total rows across %d dates:", total_rows, len(dates))
    for d in dates:
        logger.info("  %s", d)


if __name__ == "__main__":
    main()
