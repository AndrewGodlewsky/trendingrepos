import os
from dotenv import load_dotenv


_REQUIRED = ["GITHUB_PAT", "ANTHROPIC_API_KEY"]

_DEFAULTS = {
    "FETCH_MIN_STARS": 50,
    "FETCH_DAYS_BACK": 30,
    "TOP_N_REPOS": 200,
    "SUMMARIZE_MODEL": "haiku",
    "LOG_LEVEL": "INFO",
    "SITE_TITLE": "GitHub Trending",
}


def _int_env(key: str, default: int) -> int:
    """Read an env var as int, falling back to default on missing or invalid value."""
    raw = os.getenv(key, "").strip()
    if not raw:
        return default
    try:
        return int(raw)
    except ValueError:
        return default


def load_config() -> dict:
    """Load and validate all environment variables.

    Reads .env via python-dotenv, checks for required keys, casts numeric
    optional keys to int, and returns a fully populated config dict.

    Raises:
        EnvironmentError: if any required key is missing or empty.
    """
    load_dotenv()

    missing = [k for k in _REQUIRED if not os.getenv(k, "").strip()]
    if missing:
        raise EnvironmentError(
            f"Missing required environment variables: {', '.join(missing)}"
        )

    return {
        # Required
        "GITHUB_PAT": os.environ["GITHUB_PAT"],
        "ANTHROPIC_API_KEY": os.environ["ANTHROPIC_API_KEY"],
        # Optional — numeric
        "FETCH_MIN_STARS": _int_env("FETCH_MIN_STARS", _DEFAULTS["FETCH_MIN_STARS"]),
        "FETCH_DAYS_BACK": _int_env("FETCH_DAYS_BACK", _DEFAULTS["FETCH_DAYS_BACK"]),
        "TOP_N_REPOS": _int_env("TOP_N_REPOS", _DEFAULTS["TOP_N_REPOS"]),
        # Optional — string
        "SUMMARIZE_MODEL": os.getenv("SUMMARIZE_MODEL", _DEFAULTS["SUMMARIZE_MODEL"]),
        "LOG_LEVEL": os.getenv("LOG_LEVEL", _DEFAULTS["LOG_LEVEL"]),
        "SITE_TITLE": os.getenv("SITE_TITLE", _DEFAULTS["SITE_TITLE"]),
    }
