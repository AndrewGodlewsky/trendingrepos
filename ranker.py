"""Trending-score ranker for the GitHub trending pipeline.

Converts a list of RawRepo objects into a ranked list of RankedRepo objects
sorted by a composite trending_score.  Entirely new logic — no Project 1/2
equivalent.

Public API
----------
score(repo: RawRepo) -> float
    Compute a composite trending score for a single repo.

rank_repos(raw_list: list[RawRepo], top_n: int) -> list[RankedRepo]
    Deduplicate, score, sort, cap, and assign ranks.
"""
from __future__ import annotations

import logging
from datetime import date
from typing import Dict, List

from models import RawRepo, RankedRepo

logger = logging.getLogger(__name__)


def score(repo: RawRepo) -> float:
    """Compute a composite trending score for a single repository.

    The score blends five signals, each weighted to reflect how strongly it
    indicates a repo is genuinely trending *right now*:

    1. **star_velocity** (weight 0.50) — stars per day of repo age, capped at
       365 days.  High velocity means the repo is accumulating stars quickly
       relative to how long it has been around.

    2. **fork_ratio** (weight 0.15) — forks / max(1, stars).  Reflects how
       often people copy the code to contribute or build on top of it.

    3. **watcher_ratio** (weight 0.10) — watchers / max(1, stars).  A high
       ratio indicates sustained community interest beyond a one-time star.

    4. **recency_bonus** (weight 0.20) — linear decay from 1.0 to 0.0 over 30
       days since the last push.  Rewards repos that are actively maintained.

    5. **issue_health** (weight 0.05) — 1 / (1 + open_issues/stars * 10).
       Penalises repos drowning in unresolved issues relative to their size.

    Args:
        repo: A RawRepo instance.

    Returns:
        A non-negative float composite score.
    """
    today = date.today()

    # --- Component 1: star velocity ---
    try:
        created = date.fromisoformat(repo.created_at[:10])
    except (ValueError, IndexError):
        created = today
    repo_age_days = max(1, min(365, (today - created).days))
    star_velocity = repo.stars / repo_age_days

    # --- Component 2: fork ratio ---
    fork_ratio = repo.forks / max(1, repo.stars)

    # --- Component 3: watcher ratio ---
    watcher_ratio = repo.watchers / max(1, repo.stars)

    # --- Component 4: recency bonus ---
    try:
        pushed = date.fromisoformat(repo.pushed_at[:10])
    except (ValueError, IndexError):
        pushed = today
    days_since_push = (today - pushed).days
    recency_bonus = max(0.0, 1.0 - days_since_push / 30)

    # --- Component 5: issue health ---
    issue_health = 1.0 / (1.0 + (repo.open_issues / max(1, repo.stars)) * 10)

    # --- Composite ---
    composite = (
        star_velocity  * 0.50
        + fork_ratio   * 0.15
        + watcher_ratio * 0.10
        + recency_bonus * 0.20
        + issue_health  * 0.05
    )
    return composite


def rank_repos(raw_list: List[RawRepo], top_n: int) -> List[RankedRepo]:
    """Convert, deduplicate, score, sort, and cap a list of RawRepo objects.

    Steps:
    1. Deduplicate by github_id, keeping the entry with the higher star count
       (the API occasionally returns duplicates across pages).
    2. Compute trending_score for each unique repo.
    3. Sort descending by trending_score.
    4. Slice to the top *top_n* entries.
    5. Assign rank 1 … N.

    Args:
        raw_list: List of RawRepo objects (may contain duplicates).
        top_n:    Maximum number of repos to return.

    Returns:
        A list of RankedRepo objects, sorted by trending_score descending,
        with rank fields set to 1-based position.
    """
    if not raw_list:
        logger.warning("rank_repos received an empty list")
        return []

    # Deduplicate by github_id (keep highest star count)
    seen: Dict[int, RawRepo] = {}
    for repo in raw_list:
        gid = repo.github_id
        if gid not in seen or repo.stars > seen[gid].stars:
            seen[gid] = repo

    unique = list(seen.values())
    logger.info("rank_repos: %d raw repos -> %d unique after deduplication", len(raw_list), len(unique))

    # Score each repo
    scored: List[tuple[float, RawRepo]] = []
    for repo in unique:
        try:
            s = score(repo)
        except Exception as exc:
            logger.warning("score() failed for %s: %s — using 0.0", repo.full_name, exc)
            s = 0.0
        scored.append((s, repo))

    # Sort descending by score
    scored.sort(key=lambda t: t[0], reverse=True)

    # Cap to top_n and assign ranks
    top = scored[:top_n]
    result: List[RankedRepo] = []
    for rank_idx, (trending_score, raw) in enumerate(top, start=1):
        ranked = RankedRepo.from_raw(
            raw=raw,
            trending_score=round(trending_score, 6),
            stars_7d=None,   # populated later by fetcher after delta computation
            rank=rank_idx,
        )
        result.append(ranked)

    logger.info(
        "rank_repos: returning top %d repos (top score=%.4f, bottom score=%.4f)",
        len(result),
        result[0].trending_score if result else 0.0,
        result[-1].trending_score if result else 0.0,
    )
    return result
