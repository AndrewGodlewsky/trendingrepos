"""Unit tests for ranker.py — scoring and deduplication logic."""
from __future__ import annotations

import sys
from datetime import date, timedelta
from pathlib import Path

import pytest

# Allow importing project modules from the parent directory
sys.path.insert(0, str(Path(__file__).parent.parent))

from models import RawRepo
import ranker


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_raw(
    github_id: int = 1,
    full_name: str = "owner/repo",
    stars: int = 1000,
    forks: int = 100,
    watchers: int = 1000,
    open_issues: int = 10,
    created_at: str | None = None,
    pushed_at: str | None = None,
) -> RawRepo:
    """Build a minimal RawRepo with sensible defaults."""
    today = date.today()
    if created_at is None:
        created_at = (today - timedelta(days=30)).isoformat() + "T00:00:00Z"
    if pushed_at is None:
        pushed_at = today.isoformat() + "T00:00:00Z"

    return RawRepo(
        github_id=github_id,
        full_name=full_name,
        name=full_name.split("/")[-1],
        description="Test repo",
        stars=stars,
        forks=forks,
        watchers=watchers,
        open_issues=open_issues,
        language="Python",
        topics=[],
        license=None,
        homepage=None,
        html_url=f"https://github.com/{full_name}",
        created_at=created_at,
        updated_at=pushed_at,
        pushed_at=pushed_at,
    )


# ---------------------------------------------------------------------------
# test_score_recent_repo_higher_than_old
# ---------------------------------------------------------------------------

def test_score_recent_repo_higher_than_old():
    """A repo with the same stars but a shorter lifetime has higher star_velocity."""
    today = date.today()

    # Repo created 10 days ago — high star velocity
    new_repo = _make_raw(
        github_id=1,
        stars=1000,
        created_at=(today - timedelta(days=10)).isoformat() + "T00:00:00Z",
    )

    # Repo created 300 days ago — same stars, lower velocity
    old_repo = _make_raw(
        github_id=2,
        stars=1000,
        created_at=(today - timedelta(days=300)).isoformat() + "T00:00:00Z",
    )

    new_score = ranker.score(new_repo)
    old_score = ranker.score(old_repo)

    assert new_score > old_score, (
        f"Expected new_repo score ({new_score:.4f}) > old_repo score ({old_score:.4f})"
    )


# ---------------------------------------------------------------------------
# test_recency_bonus_decays
# ---------------------------------------------------------------------------

def test_recency_bonus_decays():
    """Repo pushed today gets recency_bonus=1.0; pushed 30+ days ago gets 0.0."""
    today = date.today()

    fresh_repo = _make_raw(
        github_id=1,
        pushed_at=today.isoformat() + "T00:00:00Z",
    )

    stale_repo = _make_raw(
        github_id=2,
        pushed_at=(today - timedelta(days=30)).isoformat() + "T00:00:00Z",
    )

    # We can't inspect individual components directly, but the composite score
    # for two identical repos that differ only in pushed_at should reflect the decay.
    fresh_score = ranker.score(fresh_repo)
    stale_score = ranker.score(stale_repo)

    # With weight=0.20, a bonus of 1.0 vs 0.0 means a difference of 0.20.
    assert fresh_score > stale_score, (
        f"Expected fresh score ({fresh_score:.4f}) > stale score ({stale_score:.4f})"
    )
    assert abs(fresh_score - stale_score) >= 0.19, (
        "Recency bonus difference should be close to 0.20 when all other fields are equal"
    )


# ---------------------------------------------------------------------------
# test_deduplication_keeps_highest_score
# ---------------------------------------------------------------------------

def test_deduplication_keeps_highest_score():
    """Two RawRepo objects sharing github_id — rank_repos keeps only one."""
    today = date.today()

    # Duplicate with lower stars
    low_star = _make_raw(github_id=42, full_name="owner/repo", stars=500)

    # Duplicate with higher stars (should be kept)
    high_star = _make_raw(github_id=42, full_name="owner/repo", stars=2000)

    result = ranker.rank_repos([low_star, high_star], top_n=10)

    assert len(result) == 1, f"Expected 1 repo after dedup, got {len(result)}"
    assert result[0].stars == 2000, (
        f"Expected the higher-star copy to be retained, got stars={result[0].stars}"
    )


# ---------------------------------------------------------------------------
# test_top_n_limit
# ---------------------------------------------------------------------------

def test_top_n_limit():
    """rank_repos respects the top_n cap."""
    repos = [
        _make_raw(
            github_id=i,
            full_name=f"owner/repo-{i}",
            stars=1000 + i,
        )
        for i in range(300)
    ]

    result = ranker.rank_repos(repos, top_n=10)

    assert len(result) == 10, f"Expected 10 repos, got {len(result)}"


# ---------------------------------------------------------------------------
# test_rank_assigned_sequentially
# ---------------------------------------------------------------------------

def test_rank_assigned_sequentially():
    """Returned repos have rank=1, 2, 3, ... assigned in score order."""
    repos = [
        _make_raw(
            github_id=i,
            full_name=f"owner/repo-{i}",
            stars=1000 + i,
        )
        for i in range(5)
    ]

    result = ranker.rank_repos(repos, top_n=5)

    assert len(result) == 5
    for expected_rank, repo in enumerate(result, start=1):
        assert repo.rank == expected_rank, (
            f"Expected rank {expected_rank}, got {repo.rank} for {repo.full_name}"
        )

    # Verify descending score order
    scores = [r.trending_score for r in result]
    assert scores == sorted(scores, reverse=True), "Repos are not sorted by score descending"


# ---------------------------------------------------------------------------
# test_empty_input
# ---------------------------------------------------------------------------

def test_empty_input_returns_empty_list():
    """rank_repos returns an empty list without raising when given no repos."""
    result = ranker.rank_repos([], top_n=10)
    assert result == []


# ---------------------------------------------------------------------------
# test_score_does_not_raise_on_edge_cases
# ---------------------------------------------------------------------------

def test_score_does_not_raise_on_zero_stars():
    """score() handles zero-star repos gracefully (no ZeroDivisionError)."""
    repo = _make_raw(github_id=99, stars=0, forks=0, watchers=0, open_issues=0)
    s = ranker.score(repo)
    assert isinstance(s, float)
    assert s >= 0.0
