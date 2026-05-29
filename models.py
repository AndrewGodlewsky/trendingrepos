"""Core dataclass definitions for the GitHub trending pipeline.

RawRepo  — one repository as returned by the GitHub Search API (after field mapping).
RankedRepo — extends RawRepo with computed trending_score, optional star delta, and rank.

Both classes provide:
  from_api_dict(d)  — construct from a raw GitHub API item dict
  to_dict()         — return a plain dict safe for json.dumps()
"""
from __future__ import annotations

from dataclasses import dataclass, field, asdict
from typing import List, Optional


@dataclass
class RawRepo:
    github_id: int
    full_name: str
    name: str
    description: Optional[str]
    stars: int
    forks: int
    watchers: int
    open_issues: int
    language: Optional[str]
    topics: List[str]
    license: Optional[str]
    homepage: Optional[str]
    html_url: str
    created_at: str
    updated_at: str
    pushed_at: str

    @classmethod
    def from_api_dict(cls, d: dict) -> "RawRepo":
        """Construct a RawRepo from a single GitHub Search API item dict.

        Handles the field-name differences between the API response
        (stargazers_count, forks_count, etc.) and our flat schema.
        """
        license_obj = d.get("license")
        license_id: Optional[str] = None
        if isinstance(license_obj, dict):
            license_id = license_obj.get("spdx_id") or None

        return cls(
            github_id=d["id"],
            full_name=d["full_name"],
            name=d["name"],
            description=d.get("description") or None,
            stars=d.get("stargazers_count", 0),
            forks=d.get("forks_count", 0),
            watchers=d.get("watchers_count", 0),
            open_issues=d.get("open_issues_count", 0),
            language=d.get("language") or None,
            topics=list(d.get("topics") or []),
            license=license_id,
            homepage=d.get("homepage") or None,
            html_url=d["html_url"],
            created_at=d.get("created_at", ""),
            updated_at=d.get("updated_at", ""),
            pushed_at=d.get("pushed_at", ""),
        )

    def to_dict(self) -> dict:
        """Return a plain dict suitable for json.dumps()."""
        return asdict(self)


@dataclass
class RankedRepo(RawRepo):
    trending_score: float = 0.0
    stars_7d: Optional[int] = None  # star delta vs. 7-day-old snapshot (None if unknown)
    rank: int = 0

    @classmethod
    def from_api_dict(cls, d: dict) -> "RankedRepo":
        """Construct a RankedRepo from a raw API item; trending fields default to zero/None."""
        raw = RawRepo.from_api_dict(d)
        return cls(
            **raw.to_dict(),
            trending_score=0.0,
            stars_7d=None,
            rank=0,
        )

    @classmethod
    def from_raw(cls, raw: RawRepo, trending_score: float = 0.0,
                 stars_7d: Optional[int] = None, rank: int = 0) -> "RankedRepo":
        """Promote a RawRepo to a RankedRepo, attaching computed ranking fields."""
        return cls(
            **raw.to_dict(),
            trending_score=trending_score,
            stars_7d=stars_7d,
            rank=rank,
        )

    @classmethod
    def from_dict(cls, d: dict) -> "RankedRepo":
        """Reconstruct a RankedRepo from a previously serialised to_dict() output.

        Used by storage.py when reading back repos.json from disk.
        """
        return cls(
            github_id=d["github_id"],
            full_name=d["full_name"],
            name=d["name"],
            description=d.get("description"),
            stars=d.get("stars", 0),
            forks=d.get("forks", 0),
            watchers=d.get("watchers", 0),
            open_issues=d.get("open_issues", 0),
            language=d.get("language"),
            topics=list(d.get("topics") or []),
            license=d.get("license"),
            homepage=d.get("homepage"),
            html_url=d["html_url"],
            created_at=d.get("created_at", ""),
            updated_at=d.get("updated_at", ""),
            pushed_at=d.get("pushed_at", ""),
            trending_score=float(d.get("trending_score", 0.0)),
            stars_7d=d.get("stars_7d"),
            rank=int(d.get("rank", 0)),
        )

    def to_dict(self) -> dict:
        """Return a plain dict suitable for json.dumps()."""
        return asdict(self)
