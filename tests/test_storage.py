"""Unit tests for storage.py — JSON I/O and delta computation."""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

# Allow importing project modules from the parent directory
sys.path.insert(0, str(Path(__file__).parent.parent))

import storage


# ---------------------------------------------------------------------------
# write_json / read_json round-trip
# ---------------------------------------------------------------------------

def test_write_then_read_roundtrip(tmp_path):
    """write_json followed by read_json returns an identical object."""
    target = tmp_path / "test.json"
    original = {"key": "value", "number": 42, "list": [1, 2, 3], "nested": {"a": True}}

    storage.write_json(target, original)
    result = storage.read_json(target)

    assert result == original


def test_write_creates_parent_directories(tmp_path):
    """write_json creates any missing parent directories automatically."""
    target = tmp_path / "deep" / "nested" / "dir" / "output.json"
    storage.write_json(target, {"ok": True})
    assert target.exists()
    assert json.loads(target.read_text(encoding="utf-8")) == {"ok": True}


def test_write_overwrites_existing_file(tmp_path):
    """write_json atomically replaces an existing file."""
    target = tmp_path / "overwrite.json"
    storage.write_json(target, {"v": 1})
    storage.write_json(target, {"v": 2})
    result = storage.read_json(target)
    assert result == {"v": 2}


# ---------------------------------------------------------------------------
# read_json missing / corrupt file
# ---------------------------------------------------------------------------

def test_read_missing_file_returns_default(tmp_path):
    """read_json returns the provided default when the file does not exist."""
    absent = tmp_path / "nonexistent.json"
    assert storage.read_json(absent, default=[]) == []
    assert storage.read_json(absent, default=None) is None
    assert storage.read_json(absent, default={"x": 1}) == {"x": 1}


def test_read_corrupt_json_returns_default(tmp_path):
    """read_json returns the default when the file contains invalid JSON."""
    bad_file = tmp_path / "corrupt.json"
    bad_file.write_text("{ this is not valid json !!!}", encoding="utf-8")
    result = storage.read_json(bad_file, default="fallback")
    assert result == "fallback"


# ---------------------------------------------------------------------------
# compute_star_deltas — happy path
# ---------------------------------------------------------------------------

def test_compute_star_deltas_matches_by_id():
    """Delta is correctly computed as current_stars minus previous_stars."""
    current = [{"github_id": 1, "stars": 1100, "full_name": "owner/repo"}]
    previous = [{"github_id": 1, "stars": 1000, "full_name": "owner/repo"}]

    deltas = storage.compute_star_deltas(current, previous)

    assert deltas == {1: 100}


def test_compute_star_deltas_missing_previous_returns_none():
    """A repo present in current but absent from previous gets a None delta."""
    current = [{"github_id": 99, "stars": 500, "full_name": "owner/new-repo"}]
    previous = [{"github_id": 1, "stars": 1000, "full_name": "owner/old-repo"}]

    deltas = storage.compute_star_deltas(current, previous)

    assert deltas[99] is None


def test_compute_star_deltas_multiple_repos():
    """Deltas are computed independently for each repo in the current list."""
    current = [
        {"github_id": 1, "stars": 1100},
        {"github_id": 2, "stars": 800},
        {"github_id": 3, "stars": 200},   # new — not in previous
    ]
    previous = [
        {"github_id": 1, "stars": 1000},
        {"github_id": 2, "stars": 750},
    ]

    deltas = storage.compute_star_deltas(current, previous)

    assert deltas[1] == 100
    assert deltas[2] == 50
    assert deltas[3] is None


def test_compute_star_deltas_negative_delta_is_allowed():
    """Stars can decrease (e.g. starred-then-unstarred) — negative delta is valid."""
    current = [{"github_id": 5, "stars": 900}]
    previous = [{"github_id": 5, "stars": 1000}]

    deltas = storage.compute_star_deltas(current, previous)

    assert deltas[5] == -100


def test_compute_star_deltas_empty_inputs():
    """Empty current list returns an empty dict; empty previous treats all as new."""
    assert storage.compute_star_deltas([], []) == {}
    assert storage.compute_star_deltas([], [{"github_id": 1, "stars": 500}]) == {}

    result = storage.compute_star_deltas([{"github_id": 1, "stars": 500}], [])
    assert result == {1: None}


# ---------------------------------------------------------------------------
# rotate_snapshots
# ---------------------------------------------------------------------------

def test_rotate_snapshots_copies_repos_to_previous(tmp_path):
    """rotate_snapshots copies repos.json to previous_repos.json."""
    repos_file = tmp_path / storage.REPOS_FILE
    repos_file.write_text('[{"github_id": 1}]', encoding="utf-8")

    result = storage.rotate_snapshots(tmp_path)

    assert result is True
    previous_file = tmp_path / storage.PREVIOUS_REPOS_FILE
    assert previous_file.exists()
    assert json.loads(previous_file.read_text()) == [{"github_id": 1}]


def test_rotate_snapshots_no_op_when_missing(tmp_path):
    """rotate_snapshots returns False when repos.json does not exist yet."""
    result = storage.rotate_snapshots(tmp_path)
    assert result is False
    assert not (tmp_path / storage.PREVIOUS_REPOS_FILE).exists()


# ---------------------------------------------------------------------------
# load_repos / save_repos
# ---------------------------------------------------------------------------

def test_save_and_load_repos_roundtrip(tmp_path):
    """save_repos writes, load_repos reads the exact same list back."""
    repos = [
        {"github_id": 1, "full_name": "a/b", "stars": 100},
        {"github_id": 2, "full_name": "c/d", "stars": 200},
    ]
    storage.save_repos(tmp_path, repos)
    loaded = storage.load_repos(tmp_path)
    assert loaded == repos


def test_load_repos_returns_empty_list_when_missing(tmp_path):
    """load_repos returns [] when repos.json does not yet exist."""
    result = storage.load_repos(tmp_path)
    assert result == []


# ---------------------------------------------------------------------------
# load_summaries / save_summaries
# ---------------------------------------------------------------------------

def test_save_and_load_summaries_roundtrip(tmp_path):
    """save_summaries writes, load_summaries reads the exact same dict back."""
    summaries = {
        "https://github.com/owner/repo": {
            "summary": "A useful tool.",
            "tags": ["python", "cli"],
        }
    }
    storage.save_summaries(tmp_path, summaries)
    loaded = storage.load_summaries(tmp_path)
    assert loaded == summaries


def test_load_summaries_returns_empty_dict_when_missing(tmp_path):
    """load_summaries returns {} when summaries.json does not yet exist."""
    result = storage.load_summaries(tmp_path)
    assert result == {}
