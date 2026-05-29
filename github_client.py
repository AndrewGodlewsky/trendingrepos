"""GitHub Search API client for the trending-repos pipeline.

Adapted from A:\\Claude\\Github API\\github.py.  The STAR_RANGES sweep is
replaced with a single time-bounded query targeting recently-pushed repos.

Public API
----------
build_query(min_stars, days_back) -> str
    Return a single GitHub Search query string.

fetch_trending(pat, min_stars, days_back) -> Iterator[list[dict]]
    Yield batches of raw API item dicts.
"""
from __future__ import annotations

import logging
import time
from datetime import date, timedelta
from typing import Iterator, List, Optional

import requests

logger = logging.getLogger(__name__)


class AuthenticationError(Exception):
    """Raised on HTTP 401 — bad or expired PAT.  Pipeline should exit immediately."""


# ---------------------------------------------------------------------------
# Constants (identical to Project 1 where applicable)
# ---------------------------------------------------------------------------
SEARCH_URL = "https://api.github.com/search/repositories"
MAX_RESULTS_PER_QUERY = 1000
PER_PAGE = 100
COURTESY_DELAY = 2.5   # seconds between every request (polite rate-limiting)
MAX_RETRIES = 3


# ---------------------------------------------------------------------------
# Query builder
# ---------------------------------------------------------------------------

def build_query(min_stars: int, days_back: int) -> str:
    """Build a GitHub Search query string for recently-pushed repos.

    Args:
        min_stars: Minimum star count (inclusive).
        days_back: How many days back to set the pushed:>= cutoff.

    Returns:
        A query string suitable for the GitHub Search API ``q`` parameter,
        e.g. ``'pushed:>=2026-04-28 stars:>=50'``.
    """
    cutoff = (date.today() - timedelta(days=days_back)).isoformat()
    return f"pushed:>={cutoff} stars:>={min_stars}"


# ---------------------------------------------------------------------------
# Low-level page fetcher (verbatim logic from Project 1 github.py)
# ---------------------------------------------------------------------------

def _fetch_page(
    session: requests.Session,
    query: str,
    page: int,
    sort: str = "stars",
    order: str = "desc",
) -> Optional[dict]:
    """Fetch one page of search results with rate-limit / retry handling.

    Implements the same logic as Project 1:
    - 2.5 s courtesy delay before every request
    - Reads X-RateLimit-Remaining and X-RateLimit-Reset headers
    - Sleeps until reset on HTTP 403 (rate-limit exceeded) without consuming a retry
    - Retries up to MAX_RETRIES times with exponential backoff (2 s, 4 s, 8 s) on 5xx
    - Raises AuthenticationError immediately on HTTP 401
    - Returns None on HTTP 422 (unprocessable query) or after all retries exhausted

    Args:
        session: Authenticated requests.Session.
        query:   GitHub Search ``q`` parameter value.
        page:    1-based page number.
        sort:    Sort field (default: ``"stars"``).
        order:   Sort direction (default: ``"desc"``).

    Returns:
        Parsed JSON dict on success, or None on unrecoverable error.
    """
    params = {
        "q": query,
        "sort": sort,
        "order": order,
        "per_page": PER_PAGE,
        "page": page,
    }
    attempt = 0
    while attempt < MAX_RETRIES:
        time.sleep(COURTESY_DELAY)
        try:
            resp = session.get(SEARCH_URL, params=params, timeout=30)
        except requests.RequestException as exc:
            logger.warning("Network error fetching page %d (attempt %d): %s", page, attempt + 1, exc)
            attempt += 1
            time.sleep(2 ** attempt)
            continue

        remaining = int(resp.headers.get("X-RateLimit-Remaining", 999))
        reset_time = int(resp.headers.get("X-RateLimit-Reset", 0))

        if resp.status_code == 200:
            if remaining < 5:
                wait = max(0, reset_time - time.time()) + 1
                logger.info("Rate limit low (%d remaining), sleeping %.0f s", remaining, wait)
                time.sleep(wait)
            return resp.json()

        if resp.status_code == 401:
            raise AuthenticationError("GitHub PAT is invalid or expired (HTTP 401)")

        if resp.status_code == 403:
            wait = max(0, reset_time - time.time()) + 1
            logger.warning("Rate limit hit (403), sleeping %.0f s", wait)
            time.sleep(wait)
            continue  # does NOT increment attempt — retry indefinitely until reset

        if resp.status_code == 422:
            logger.warning("422 Unprocessable for query '%s' page %d", query, page)
            return None

        if resp.status_code in (500, 502, 503, 504):
            wait = 2 ** (attempt + 1)
            logger.warning("HTTP %d attempt %d, retrying in %d s", resp.status_code, attempt + 1, wait)
            time.sleep(wait)
            attempt += 1
            continue

        resp.raise_for_status()

    logger.error("Failed after %d retries for '%s' page %d", MAX_RETRIES, query, page)
    return None


# ---------------------------------------------------------------------------
# High-level fetch generator
# ---------------------------------------------------------------------------

def fetch_trending(
    pat: str,
    min_stars: int = 50,
    days_back: int = 30,
) -> Iterator[List[dict]]:
    """Fetch recently-pushed repos from the GitHub Search API.

    Yields batches (lists) of raw item dicts as returned by the API.  The
    caller accumulates these into a flat list for downstream ranking.

    Unlike Project 1's star-range sweep, a single time-bounded query is used.
    If total_count exceeds 1,000 (API cap), a warning is logged but no
    recursive bisect is performed — the time window naturally limits results
    and the top-N repos by star count are still captured via sort=stars.

    Args:
        pat:       GitHub Personal Access Token (Bearer auth).
        min_stars: Minimum star count filter (default 50).
        days_back: How many days back to look for pushed repos (default 30).

    Yields:
        Lists of raw repo dicts (GitHub Search API ``items``).

    Raises:
        AuthenticationError: If the PAT is invalid (HTTP 401).
    """
    query = build_query(min_stars, days_back)
    logger.info("Starting fetch: query='%s'", query)

    with requests.Session() as session:
        session.headers.update({
            "Authorization": f"Bearer {pat}",
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
        })

        # Fetch page 1 to get total_count
        first_data = _fetch_page(session, query, 1)
        if first_data is None:
            logger.error("First page fetch returned None — aborting")
            return

        total_count = first_data.get("total_count", 0)
        logger.info("Total matching repos: %d", total_count)

        if total_count > MAX_RESULTS_PER_QUERY:
            logger.warning(
                "Query '%s' has %d results (cap=%d). "
                "Only the top %d repos (sorted by stars) will be fetched.",
                query, total_count, MAX_RESULTS_PER_QUERY, MAX_RESULTS_PER_QUERY,
            )

        items = first_data.get("items", [])
        if items:
            logger.debug("Page 1: %d items", len(items))
            yield items
        if len(items) < PER_PAGE:
            logger.info("Fetch complete after page 1 (%d items)", len(items))
            return

        # Pages 2-10 (API hard cap: max 10 pages × 100 = 1,000 results)
        for page in range(2, 11):
            data = _fetch_page(session, query, page)
            if data is None:
                logger.warning("Page %d returned None — stopping pagination early", page)
                break
            items = data.get("items", [])
            if items:
                logger.debug("Page %d: %d items", page, len(items))
                yield items
            if len(items) < PER_PAGE:
                logger.info("Fetch complete after page %d", page)
                break

    logger.info("fetch_trending finished")
