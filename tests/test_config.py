"""Unit tests for config.py — environment variable loading.

Mirrors the 5-test pattern from A:\\Claude\\Github API\\tests\\test_config.py.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

# Allow importing project modules from the parent directory
sys.path.insert(0, str(Path(__file__).parent.parent))

import config


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_REQUIRED_VARS = {
    "GITHUB_PAT": "github_pat_test_token",
    "ANTHROPIC_API_KEY": "sk-ant-test-key",
}


def _set_required(monkeypatch):
    """Set all required env vars so load_config() does not raise."""
    for key, val in _REQUIRED_VARS.items():
        monkeypatch.setenv(key, val)


# ---------------------------------------------------------------------------
# test_load_config_success
# ---------------------------------------------------------------------------

def test_load_config_success(monkeypatch):
    """load_config() returns a complete dict when both required vars are set."""
    _set_required(monkeypatch)

    cfg = config.load_config()

    # Required keys
    assert cfg["GITHUB_PAT"] == "github_pat_test_token"
    assert cfg["ANTHROPIC_API_KEY"] == "sk-ant-test-key"

    # Optional keys are present
    assert "FETCH_MIN_STARS" in cfg
    assert "FETCH_DAYS_BACK" in cfg
    assert "TOP_N_REPOS" in cfg
    assert "SUMMARIZE_MODEL" in cfg
    assert "LOG_LEVEL" in cfg
    assert "SITE_TITLE" in cfg


# ---------------------------------------------------------------------------
# test_load_config_missing_github_pat
# ---------------------------------------------------------------------------

def test_load_config_missing_github_pat(monkeypatch):
    """load_config() raises EnvironmentError when GITHUB_PAT is absent."""
    monkeypatch.delenv("GITHUB_PAT", raising=False)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-test-key")

    with pytest.raises(EnvironmentError, match="GITHUB_PAT"):
        config.load_config()


# ---------------------------------------------------------------------------
# test_load_config_missing_anthropic_key
# ---------------------------------------------------------------------------

def test_load_config_missing_anthropic_key(monkeypatch):
    """load_config() raises EnvironmentError when ANTHROPIC_API_KEY is absent."""
    monkeypatch.setenv("GITHUB_PAT", "github_pat_test_token")
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)

    with pytest.raises(EnvironmentError, match="ANTHROPIC_API_KEY"):
        config.load_config()


# ---------------------------------------------------------------------------
# test_numeric_defaults_cast_to_int
# ---------------------------------------------------------------------------

def test_numeric_defaults_cast_to_int(monkeypatch):
    """Default FETCH_MIN_STARS, FETCH_DAYS_BACK, and TOP_N_REPOS are int, not str."""
    _set_required(monkeypatch)
    # Remove optional overrides so defaults are used
    monkeypatch.delenv("FETCH_MIN_STARS", raising=False)
    monkeypatch.delenv("FETCH_DAYS_BACK", raising=False)
    monkeypatch.delenv("TOP_N_REPOS", raising=False)

    cfg = config.load_config()

    assert isinstance(cfg["FETCH_MIN_STARS"], int), "FETCH_MIN_STARS should be int"
    assert cfg["FETCH_MIN_STARS"] == 50

    assert isinstance(cfg["FETCH_DAYS_BACK"], int), "FETCH_DAYS_BACK should be int"
    assert cfg["FETCH_DAYS_BACK"] == 30

    assert isinstance(cfg["TOP_N_REPOS"], int), "TOP_N_REPOS should be int"
    assert cfg["TOP_N_REPOS"] == 200


# ---------------------------------------------------------------------------
# test_optional_overrides_respected
# ---------------------------------------------------------------------------

def test_optional_overrides_respected(monkeypatch):
    """When optional env vars are set, load_config() uses those values."""
    _set_required(monkeypatch)
    monkeypatch.setenv("FETCH_MIN_STARS", "100")
    monkeypatch.setenv("FETCH_DAYS_BACK", "7")
    monkeypatch.setenv("TOP_N_REPOS", "50")
    monkeypatch.setenv("SUMMARIZE_MODEL", "sonnet")
    monkeypatch.setenv("LOG_LEVEL", "DEBUG")
    monkeypatch.setenv("SITE_TITLE", "My Trending")

    cfg = config.load_config()

    assert cfg["FETCH_MIN_STARS"] == 100
    assert cfg["FETCH_DAYS_BACK"] == 7
    assert cfg["TOP_N_REPOS"] == 50
    assert cfg["SUMMARIZE_MODEL"] == "sonnet"
    assert cfg["LOG_LEVEL"] == "DEBUG"
    assert cfg["SITE_TITLE"] == "My Trending"


# ---------------------------------------------------------------------------
# test_empty_string_treated_as_missing
# ---------------------------------------------------------------------------

def test_empty_string_treated_as_missing(monkeypatch):
    """An empty string for a required var is treated the same as absent."""
    monkeypatch.setenv("GITHUB_PAT", "")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-test-key")

    with pytest.raises(EnvironmentError):
        config.load_config()
