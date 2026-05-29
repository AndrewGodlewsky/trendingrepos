"""Fetch-and-rank pipeline step.

Orchestrates the full data-collection step:
  1. Rotate yesterday's repos.json -> previous_repos.json
  2. Fetch raw repo dicts from the GitHub Search API
  3. Convert to RawRepo objects
  4. Rank via ranker.rank_repos()
  5. Apply star deltas from the previous snapshot
  6. Persist the ranked list to data/repos.json

Public API
----------
fetch_and_rank(cfg: dict) -> list[dict]
    Run all steps and return the ranked repo dicts (same structure as
    what was written to data/repos.json).
"""
from __future__ import annotations

import logging
from pathlib import Path
from typing import List

import github_client
import ranker
import storage
from models import RawRepo

logger = logging.getLogger(__name__)

# Default data directory (relative to the project root)
DATA_DIR = Path("data")


def fetch_and_rank(cfg: dict) -> List[dict]:
    """Run the fetch-and-rank pipeline step.

    Args:
        cfg: Config dict as returned by ``config.load_config()``.  Expected
             keys used here: ``GITHUB_PAT``, ``FETCH_MIN_STARS``,
             ``FETCH_DAYS_BACK``, ``TOP_N_REPOS``.

    Returns:
        List of ranked repo dicts (each is a ``RankedRepo.to_dict()`` output
        with ``stars_7d`` populated from the previous snapshot where possible).

    Side effects:
        - Copies ``data/repos.json`` -> ``data/previous_repos.json`` (rotation)
        - Writes fresh ``data/repos.json``
    """
    data_dir = DATA_DIR
    data_dir.mkdir(parents=True, exist_ok=True)

    # ------------------------------------------------------------------
    # Step 1: Rotate snapshots so we have yesterday's data for deltas
    # ------------------------------------------------------------------
    rotated = storage.rotate_snapshots(data_dir)
    if rotated:
        logger.info("Snapshot rotation complete")
    else:
        logger.info("No previous repos.json to rotate (first run or missing file)")

    # ------------------------------------------------------------------
    # Step 2: Fetch raw items from GitHub Search API
    # ------------------------------------------------------------------
    pat = cfg["GITHUB_PAT"]
    min_stars = cfg["FETCH_MIN_STARS"]
    days_back = cfg["FETCH_DAYS_BACK"]
    top_n = cfg["TOP_N_REPOS"]

    logger.info(
        "Fetching repos: min_stars=%d, days_back=%d, top_n=%d",
        min_stars, days_back, top_n,
    )

    raw_items: List[dict] = []
    try:
        for batch in github_client.fetch_trending(pat, min_stars, days_back):
            raw_items.extend(batch)
            logger.debug("Accumulated %d raw items so far", len(raw_items))
    except github_client.AuthenticationError:
        logger.critical("GitHub authentication failed — check GITHUB_PAT in .env")
        raise
    except Exception as exc:
        logger.error("Error during GitHub fetch: %s", exc, exc_info=True)
        raise

    logger.info("Fetched %d total raw repo items from GitHub", len(raw_items))

    if not raw_items:
        logger.warning("No repos fetched — writing empty repos.json")
        storage.save_repos(data_dir, [])
        return []

    # ------------------------------------------------------------------
    # Step 3: Convert to RawRepo dataclass instances
    # ------------------------------------------------------------------
    raw_repos: List[RawRepo] = []
    for item in raw_items:
        try:
            raw_repos.append(RawRepo.from_api_dict(item))
        except (KeyError, TypeError) as exc:
            logger.warning("Skipping malformed API item (%s): %s", exc, str(item)[:120])

    logger.info("Converted %d valid RawRepo objects", len(raw_repos))

    # ------------------------------------------------------------------
    # Step 4: Rank repos
    # ------------------------------------------------------------------
    ranked = ranker.rank_repos(raw_repos, top_n)
    logger.info("Ranked %d repos (top_n=%d)", len(ranked), top_n)

    # ------------------------------------------------------------------
    # Step 5: Load previous snapshot and compute star deltas
    # ------------------------------------------------------------------
    previous: List[dict] = storage.load_previous_repos(data_dir)
    logger.info("Loaded %d repos from previous snapshot", len(previous))

    current_dicts = [r.to_dict() for r in ranked]
    deltas = storage.compute_star_deltas(current_dicts, previous)
    logger.debug("Computed star deltas for %d repos", len(deltas))

    # Apply deltas: set stars_7d on each RankedRepo
    for repo in ranked:
        repo.stars_7d = deltas.get(repo.github_id)  # None if not in previous snapshot

    # ------------------------------------------------------------------
    # Step 6: Persist to data/repos.json
    # ------------------------------------------------------------------
    final_dicts = [r.to_dict() for r in ranked]
    storage.save_repos(data_dir, final_dicts)
    logger.info("Saved %d ranked repos to data/repos.json", len(final_dicts))

    return final_dicts
