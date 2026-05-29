"""AI summarization pipeline step.

Generates Claude AI summaries for repos listed in data/repos.json that do not
yet have a valid entry in data/summaries.json.  Adapted directly from
A:\\Claude\\github summary\\summarize.py — collect_repo_context(), the Claude
prompt, and JSON-parsing/fence-stripping logic are preserved verbatim.  Adds
incremental-save behaviour (write after every repo) so partial progress is
never lost.

Public API
----------
summarize_all(cfg: dict) -> int
    Summarize all un-summarized repos; return count of newly added summaries.
"""
from __future__ import annotations

import base64
import json
import logging
import re
from pathlib import Path
from typing import Dict, List, Optional

import requests
from anthropic import Anthropic

import storage

logger = logging.getLogger(__name__)

# Default data directory
DATA_DIR = Path("data")

# ---------------------------------------------------------------------------
# Constants (verbatim from Project 2 summarize.py)
# ---------------------------------------------------------------------------

KEY_CONFIG_FILES = [
    "package.json", "pyproject.toml", "requirements.txt", "setup.py", "setup.cfg",
    "Cargo.toml", "go.mod", "pom.xml", "build.gradle", "Gemfile", "composer.json",
    "Makefile", "CMakeLists.txt", "Dockerfile", "docker-compose.yml",
    ".github/workflows",
]

ENTRY_POINT_PATTERNS = [
    r"^(src/)?main\.(py|js|ts|go|rs|rb|java|cpp|c)$",
    r"^(src/)?index\.(py|js|ts)$",
    r"^(src/)?app\.(py|js|ts)$",
    r"^(src/)?server\.(py|js|ts)$",
    r"^(src/)?cli\.(py|js|ts)$",
    r"^(src/)?__main__\.py$",
    r"^(src/)?lib\.(py|js|ts|rs)$",
]

MAX_FILE_CHARS = 3000
MAX_TOTAL_CHARS = 20_000

MODELS: Dict[str, str] = {
    "haiku": "claude-haiku-4-5-20251001",
    "sonnet": "claude-sonnet-4-6",
}
DEFAULT_MODEL = "haiku"


# ---------------------------------------------------------------------------
# GitHub helper functions (verbatim from Project 2 summarize.py)
# ---------------------------------------------------------------------------

def _github_get(path: str, token: Optional[str], params: dict = None) -> Optional[object]:
    """Make a GitHub API GET request; returns parsed JSON or None on 404."""
    headers = {
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
    }
    if token:
        headers["Authorization"] = f"Bearer {token}"
    url = f"https://api.github.com{path}"
    try:
        response = requests.get(url, headers=headers, params=params, timeout=30)
    except requests.RequestException as exc:
        logger.warning("GitHub API request failed for %s: %s", path, exc)
        return None
    if response.status_code == 404:
        return None
    try:
        response.raise_for_status()
    except requests.HTTPError as exc:
        logger.warning("GitHub API HTTP error for %s: %s", path, exc)
        return None
    return response.json()


def _fetch_file_content(owner: str, repo: str, path: str, token: Optional[str]) -> Optional[str]:
    """Fetch a file's decoded text content from GitHub (base64-encoded)."""
    data = _github_get(f"/repos/{owner}/{repo}/contents/{path}", token)
    if not data or isinstance(data, list):
        return None
    if data.get("encoding") != "base64":
        return None
    try:
        content = base64.b64decode(data["content"]).decode("utf-8", errors="replace")
        return content[:MAX_FILE_CHARS] + ("\n... [truncated]" if len(content) > MAX_FILE_CHARS else "")
    except Exception as exc:
        logger.debug("Could not decode %s/%s/%s: %s", owner, repo, path, exc)
        return None


def _fetch_directory_listing(owner: str, repo: str, path: str, token: Optional[str]) -> Optional[str]:
    """Return a simple directory listing for a path."""
    data = _github_get(f"/repos/{owner}/{repo}/contents/{path}", token)
    if not data or not isinstance(data, list):
        return None
    names = [item["name"] + ("/" if item["type"] == "dir" else "") for item in data[:30]]
    return "\n".join(names)


# ---------------------------------------------------------------------------
# Context collector (verbatim from Project 2 collect_repo_context)
# ---------------------------------------------------------------------------

def collect_repo_context(owner: str, repo: str, token: Optional[str]) -> str:
    """Gather useful context from the repo and return it as a single string.

    Fetches (in order): repo metadata, README (up to 8,000 chars), file tree
    (top 200 paths), key config files, GitHub Actions workflow listing, and
    entry-point source files.  Stops adding sections once MAX_TOTAL_CHARS is
    reached.

    Args:
        owner: Repository owner (GitHub username or org).
        repo:  Repository name.
        token: GitHub PAT for authenticated requests (higher rate limit).

    Returns:
        A multi-section string suitable for inclusion in a Claude prompt.

    Raises:
        ValueError: If the repository is not found or inaccessible.
    """
    sections: List[str] = []
    total_chars = 0

    def add(label: str, content: str) -> None:
        nonlocal total_chars
        if not content or total_chars >= MAX_TOTAL_CHARS:
            return
        remaining = MAX_TOTAL_CHARS - total_chars
        chunk = content[:remaining]
        sections.append(f"### {label}\n{chunk}")
        total_chars += len(chunk)

    # 1. Repo metadata
    logger.debug("  Fetching metadata for %s/%s", owner, repo)
    meta = _github_get(f"/repos/{owner}/{repo}", token)
    if not meta:
        raise ValueError(f"Repository {owner}/{repo} not found or inaccessible.")

    meta_text = "\n".join(filter(None, [
        f"Name: {meta.get('full_name')}",
        f"Description: {meta.get('description') or 'None'}",
        f"Primary language: {meta.get('language') or 'Unknown'}",
        f"Stars: {meta.get('stargazers_count', 0):,}",
        f"Topics: {', '.join(meta.get('topics', [])) or 'None'}",
        f"License: {(meta.get('license') or {}).get('spdx_id', 'None')}",
        f"Default branch: {meta.get('default_branch', 'main')}",
        f"Open issues: {meta.get('open_issues_count', 0)}",
        f"Fork: {meta.get('fork', False)}",
        f"Archived: {meta.get('archived', False)}",
    ]))
    add("Repository Metadata", meta_text)

    # 2. README (up to 8,000 chars)
    logger.debug("  Fetching README for %s/%s", owner, repo)
    readme_data = _github_get(f"/repos/{owner}/{repo}/readme", token)
    if readme_data and readme_data.get("encoding") == "base64":
        try:
            readme = base64.b64decode(readme_data["content"]).decode("utf-8", errors="replace")
            add("README", readme[:8000] + ("\n... [truncated]" if len(readme) > 8000 else ""))
        except Exception as exc:
            logger.debug("README decode error for %s/%s: %s", owner, repo, exc)

    # 3. File tree (top 200 entries)
    logger.debug("  Fetching file tree for %s/%s", owner, repo)
    default_branch = meta.get("default_branch", "main")
    tree_data = _github_get(
        f"/repos/{owner}/{repo}/git/trees/{default_branch}",
        token,
        {"recursive": "1"},
    )
    all_paths: List[str] = []
    if tree_data and "tree" in tree_data:
        all_paths = [item["path"] for item in tree_data["tree"] if item["type"] == "blob"]
        tree_preview = "\n".join(all_paths[:200])
        if len(all_paths) > 200:
            tree_preview += f"\n... and {len(all_paths) - 200} more files"
        add("File Tree", tree_preview)

    # 4. Key config files
    logger.debug("  Fetching config files for %s/%s", owner, repo)
    for filepath in KEY_CONFIG_FILES:
        if total_chars >= MAX_TOTAL_CHARS:
            break
        # Handle special directory entries
        if not "." in filepath.split("/")[-1] and filepath in ("Makefile", "Dockerfile"):
            content = _fetch_file_content(owner, repo, filepath, token)
        elif filepath.endswith("/"):
            content = _fetch_directory_listing(owner, repo, filepath.rstrip("/"), token)
        else:
            # Skip files not present in the tree to avoid unnecessary 404 calls
            if all_paths and filepath not in all_paths:
                continue
            content = _fetch_file_content(owner, repo, filepath, token)
        if content:
            add(f"File: {filepath}", content)

    # 4b. GitHub Actions workflows listing
    if total_chars < MAX_TOTAL_CHARS:
        workflows = _fetch_directory_listing(owner, repo, ".github/workflows", token)
        if workflows:
            add("CI/CD Workflows (.github/workflows/)", workflows)

    # 5. Entry-point source files
    logger.debug("  Looking for entry points in %s/%s", owner, repo)
    for path in all_paths:
        if total_chars >= MAX_TOTAL_CHARS:
            break
        for pattern in ENTRY_POINT_PATTERNS:
            if re.match(pattern, path, re.IGNORECASE):
                content = _fetch_file_content(owner, repo, path, token)
                if content:
                    add(f"Source: {path}", content)
                break

    return "\n\n".join(sections)


# ---------------------------------------------------------------------------
# URL parser (verbatim from Project 2)
# ---------------------------------------------------------------------------

def _parse_github_url(url: str):
    """Extract (owner, repo) from a GitHub URL or 'owner/repo' shorthand."""
    url = url.strip().rstrip("/")
    patterns = [
        r"github\.com[:/]([^/]+)/([^/\s.]+?)(?:\.git)?$",
        r"^([^/]+)/([^/]+)$",
    ]
    for pattern in patterns:
        match = re.search(pattern, url)
        if match:
            return match.group(1), match.group(2)
    raise ValueError(f"Could not parse GitHub URL: {url}")


# ---------------------------------------------------------------------------
# Claude summarizer (adapted from Project 2)
# ---------------------------------------------------------------------------

def _summarize_one(
    html_url: str,
    github_pat: str,
    anthropic_api_key: str,
    model_alias: str = DEFAULT_MODEL,
) -> dict:
    """Fetch context and generate a Claude summary for one repo.

    Args:
        html_url:          The repo's GitHub URL, e.g. ``https://github.com/owner/repo``.
        github_pat:        GitHub PAT for authenticated API calls.
        anthropic_api_key: Anthropic API key for Claude.
        model_alias:       ``"haiku"`` or ``"sonnet"`` (default ``"haiku"``).

    Returns:
        Dict with keys ``"summary"`` (str) and ``"tags"`` (list[str]).

    Raises:
        ValueError: If the URL cannot be parsed or the repo is inaccessible.
        Exception:  Propagates any Anthropic API error to the caller.
    """
    owner, repo = _parse_github_url(html_url)
    logger.info("Summarizing %s/%s with model=%s", owner, repo, model_alias)

    context = collect_repo_context(owner, repo, github_pat)

    model_id = MODELS.get(model_alias, MODELS[DEFAULT_MODEL])
    client = Anthropic(api_key=anthropic_api_key)

    # Prompt verbatim from Project 2 summarize.py lines 214-225
    prompt = (
        "You are analyzing a GitHub repository. Based on the context below, write a single "
        "paragraph of 2 to 4 sentences that clearly describes what this repository does — its "
        "purpose, who it is for, and what makes it useful. Then choose 1 to 5 short tags "
        "(lowercase, no spaces, use hyphens if needed) that best categorize the repository "
        "(e.g. by language, domain, or type of tool).\n\n"
        "Return only valid JSON with exactly two keys:\n"
        '- "summary": a string containing the 2-4 sentence paragraph\n'
        '- "tags": an array of 1-5 tag strings\n\n'
        'Example format:\n{"summary": "...", "tags": ["tag1", "tag2"]}\n\n'
        "---\n\n"
        f"{context}"
    )

    response = client.messages.create(
        model=model_id,
        max_tokens=300,
        messages=[{"role": "user", "content": prompt}],
    )

    raw = response.content[0].text.strip()

    # Strip markdown code fences (verbatim from Project 2)
    if raw.startswith("```"):
        raw = re.sub(r"^```[a-z]*\s*", "", raw)
        raw = re.sub(r"\s*```$", "", raw.strip())

    try:
        parsed = json.loads(raw)
        summary_text = parsed["summary"]
        tags = parsed["tags"]
    except (json.JSONDecodeError, KeyError):
        logger.warning("JSON parse failed for %s/%s — using raw text as summary", owner, repo)
        summary_text = raw
        tags = []

    return {"summary": summary_text, "tags": tags}


# ---------------------------------------------------------------------------
# Pipeline entry point
# ---------------------------------------------------------------------------

def summarize_all(cfg: dict) -> int:
    """Summarize all repos in data/repos.json that lack a valid summary.

    Reads repos from ``data/repos.json`` and existing summaries from
    ``data/summaries.json``.  For each repo whose ``html_url`` is absent from
    the summaries dict (or whose previous summary starts with ``"ERROR:"``),
    fetches GitHub context and generates a Claude summary.  Writes
    ``data/summaries.json`` incrementally after every successfully summarized
    repo so partial progress is never lost.

    Args:
        cfg: Config dict from ``config.load_config()``.  Keys used:
             ``GITHUB_PAT``, ``ANTHROPIC_API_KEY``, ``SUMMARIZE_MODEL``.

    Returns:
        Count of newly summarized repos in this run.
    """
    data_dir = DATA_DIR
    data_dir.mkdir(parents=True, exist_ok=True)

    repos: List[dict] = storage.load_repos(data_dir)
    summaries: Dict[str, dict] = storage.load_summaries(data_dir)

    github_pat = cfg["GITHUB_PAT"]
    anthropic_api_key = cfg["ANTHROPIC_API_KEY"]
    model_alias = cfg.get("SUMMARIZE_MODEL", DEFAULT_MODEL)

    logger.info(
        "summarize_all: %d repos to consider, %d already summarized",
        len(repos), len(summaries),
    )

    newly_summarized = 0

    for idx, repo in enumerate(repos, start=1):
        html_url = repo.get("html_url", "")
        if not html_url:
            logger.warning("Repo at index %d has no html_url — skipping", idx)
            continue

        # Skip repos that already have a valid (non-error) summary
        existing = summaries.get(html_url, {})
        existing_summary = existing.get("summary", "")
        if existing_summary and not existing_summary.startswith("ERROR:"):
            logger.debug("[%d/%d] Already summarized: %s", idx, len(repos), html_url)
            continue

        full_name = repo.get("full_name", html_url)
        logger.info("[%d/%d] Summarizing: %s", idx, len(repos), full_name)

        try:
            result = _summarize_one(html_url, github_pat, anthropic_api_key, model_alias)
            summaries[html_url] = result
            newly_summarized += 1
            logger.info(
                "  -> tags: %s | summary: %.80s...",
                ", ".join(result.get("tags", [])),
                result.get("summary", ""),
            )
        except ValueError as exc:
            # Repo not found / URL parse failure — record error, do not retry next run
            logger.error("  -> Repo not accessible (%s) — recording error", exc)
            summaries[html_url] = {"summary": f"ERROR: {exc}", "tags": []}
        except Exception as exc:
            # API errors, network issues, etc. — record error so the next run retries
            logger.error("  -> Summarization failed for %s: %s", html_url, exc, exc_info=True)
            summaries[html_url] = {"summary": f"ERROR: {exc}", "tags": []}

        # Incremental save after every repo (preserves partial progress)
        try:
            storage.save_summaries(data_dir, summaries)
        except Exception as exc:
            logger.error("Failed to write summaries.json after %s: %s", html_url, exc)

    logger.info(
        "summarize_all complete: %d new summaries, %d total in store",
        newly_summarized,
        len(summaries),
    )
    return newly_summarized
