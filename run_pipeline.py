"""GitHub Trending Repos Pipeline — main entry point.

Chains all pipeline steps in sequence:
  1. Fetch and rank repos from GitHub Search API
  2. Generate AI summaries via Claude
  3. Generate the static website (docs/index.html)

Logs progress and timing to both stdout and logs/run_YYYY-MM-DD.log.
Exits with code 0 on success, 1 on any step failure so Windows Task
Scheduler registers the failure correctly.

Usage
-----
    python run_pipeline.py [--skip-fetch] [--skip-summarize] [--skip-site]

Optional flags let you re-run individual stages during development without
repeating expensive API calls.
"""
from __future__ import annotations

import argparse
import logging
import sys
import time
from datetime import date
from pathlib import Path


def _setup_logging(log_level: str) -> None:
    """Configure root logger with file + stdout handlers.

    Args:
        log_level: String level name (DEBUG / INFO / WARNING / ERROR).
    """
    logs_dir = Path("logs")
    logs_dir.mkdir(exist_ok=True)

    log_file = logs_dir / f"run_{date.today().isoformat()}.log"

    level = getattr(logging, log_level.upper(), logging.INFO)

    formatter = logging.Formatter("%(asctime)s %(levelname)s %(message)s")

    file_handler = logging.FileHandler(log_file, encoding="utf-8")
    file_handler.setLevel(level)
    file_handler.setFormatter(formatter)

    stream_handler = logging.StreamHandler(sys.stdout)
    stream_handler.setLevel(level)
    stream_handler.setFormatter(formatter)

    root = logging.getLogger()
    root.setLevel(level)
    root.addHandler(file_handler)
    root.addHandler(stream_handler)

    logging.info("Logging to %s (level=%s)", log_file, log_level.upper())


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="GitHub Trending Repos Pipeline",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "Examples:\n"
            "  python run_pipeline.py                  # full run\n"
            "  python run_pipeline.py --skip-fetch     # re-summarize + regenerate site\n"
            "  python run_pipeline.py --skip-summarize # fetch + regenerate site only\n"
            "  python run_pipeline.py --skip-site      # fetch + summarize only\n"
        ),
    )
    parser.add_argument(
        "--skip-fetch",
        action="store_true",
        help="Skip the GitHub fetch/rank step (reuse existing data/repos.json)",
    )
    parser.add_argument(
        "--skip-summarize",
        action="store_true",
        help="Skip the Claude summarization step",
    )
    parser.add_argument(
        "--skip-site",
        action="store_true",
        help="Skip the static site generation step",
    )
    return parser.parse_args()


def main() -> None:
    """Run the full pipeline and exit with the appropriate code."""
    args = _parse_args()
    t_start = time.monotonic()

    # ------------------------------------------------------------------
    # 1. Load configuration (validates required env vars)
    # ------------------------------------------------------------------
    try:
        import config as _config
        cfg = _config.load_config()
    except EnvironmentError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        print(
            "Ensure GITHUB_PAT and ANTHROPIC_API_KEY are set in your .env file "
            "or environment before running the pipeline.",
            file=sys.stderr,
        )
        sys.exit(1)

    # ------------------------------------------------------------------
    # 2. Set up logging (uses LOG_LEVEL from config)
    # ------------------------------------------------------------------
    _setup_logging(cfg.get("LOG_LEVEL", "INFO"))
    logger = logging.getLogger(__name__)
    logger.info("=" * 60)
    logger.info("GitHub Trending Pipeline starting")
    logger.info("=" * 60)

    # ------------------------------------------------------------------
    # 3. Ensure output directories exist
    # ------------------------------------------------------------------
    Path("data").mkdir(exist_ok=True)
    Path("docs").mkdir(exist_ok=True)

    # ------------------------------------------------------------------
    # Step 1/3: Fetch and rank repos
    # ------------------------------------------------------------------
    repos: list = []

    if args.skip_fetch:
        logger.info("Step 1/3: SKIPPED (--skip-fetch) — loading existing repos.json")
        import storage as _storage
        repos = _storage.load_repos(Path("data"))
        logger.info("Loaded %d repos from existing data/repos.json", len(repos))
    else:
        logger.info("Step 1/3: Fetching and ranking repos...")
        try:
            import fetcher as _fetcher
            repos = _fetcher.fetch_and_rank(cfg)
            logger.info("Step 1/3 complete: %d repos fetched and ranked", len(repos))
        except Exception as exc:
            logger.critical("Step 1/3 FAILED: %s", exc, exc_info=True)
            logger.critical("Pipeline aborted at fetch step.")
            sys.exit(1)

    # ------------------------------------------------------------------
    # Step 2/3: Generate AI summaries
    # ------------------------------------------------------------------
    if args.skip_summarize:
        logger.info("Step 2/3: SKIPPED (--skip-summarize)")
    else:
        logger.info("Step 2/3: Generating AI summaries...")
        try:
            import summarizer as _summarizer
            n_new = _summarizer.summarize_all(cfg)
            logger.info("Step 2/3 complete: %d new summaries generated", n_new)
        except Exception as exc:
            logger.error("Step 2/3 FAILED: %s", exc, exc_info=True)
            logger.error(
                "Summarization error — continuing to site generation with existing summaries."
            )
            # Summarization failure is non-fatal: the site can still render
            # with whatever summaries already exist in data/summaries.json.

    # ------------------------------------------------------------------
    # Step 3/3: Generate static site
    # ------------------------------------------------------------------
    if args.skip_site:
        logger.info("Step 3/3: SKIPPED (--skip-site)")
    else:
        logger.info("Step 3/3: Generating static site...")
        try:
            import site_generator as _site_generator
            _site_generator.generate(cfg)
            logger.info("Step 3/3 complete: site written to docs/index.html")
        except Exception as exc:
            logger.critical("Step 3/3 FAILED: %s", exc, exc_info=True)
            logger.critical("Pipeline aborted at site-generation step.")
            sys.exit(1)

    # ------------------------------------------------------------------
    # Done
    # ------------------------------------------------------------------
    elapsed = time.monotonic() - t_start
    logger.info("=" * 60)
    logger.info(
        "Pipeline finished successfully in %.1f seconds (%.1f minutes)",
        elapsed,
        elapsed / 60,
    )
    logger.info("=" * 60)


if __name__ == "__main__":
    main()
