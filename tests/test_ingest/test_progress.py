"""Tests for the ProgressTracker."""

import json

from papermarkets.ingest.progress import ProgressTracker


def test_mark_started_and_completed(tmp_path):
    tracker = ProgressTracker(tmp_path / "progress.json")
    tracker.mark_started("market-1")
    assert not tracker.is_completed("market-1")
    tracker.mark_completed("market-1")
    assert tracker.is_completed("market-1")


def test_checkpoint_roundtrip(tmp_path):
    tracker = ProgressTracker(tmp_path / "progress.json")
    tracker.save_checkpoint("market-1", {"page": 3, "count": 500})
    assert tracker.get_checkpoint("market-1") == {"page": 3, "count": 500}


def test_get_checkpoint_returns_none_for_unknown(tmp_path):
    tracker = ProgressTracker(tmp_path / "progress.json")
    assert tracker.get_checkpoint("nonexistent") is None


def test_persistence_across_instances(tmp_path):
    filepath = tmp_path / "progress.json"
    t1 = ProgressTracker(filepath)
    t1.mark_completed("market-1")
    t1.save_checkpoint("market-2", {"offset": 42})

    t2 = ProgressTracker(filepath)
    assert t2.is_completed("market-1")
    assert t2.get_checkpoint("market-2") == {"offset": 42}


def test_file_is_valid_json(tmp_path):
    filepath = tmp_path / "progress.json"
    tracker = ProgressTracker(filepath)
    tracker.mark_started("a")
    tracker.mark_completed("b")

    data = json.loads(filepath.read_text())
    assert data["a"]["status"] == "in_progress"
    assert data["b"]["status"] == "completed"
