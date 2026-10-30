"""Resume-friendly progress tracker backed by a JSON file."""

import json
import tempfile
import threading
from pathlib import Path


class ProgressTracker:
    """Thread-safe, per-market ingestion status tracker with atomic file writes."""

    def __init__(self, filepath: Path):
        self._filepath = filepath
        self._lock = threading.Lock()
        self._state: dict[str, dict] = {}
        if filepath.exists():
            self._state = json.loads(filepath.read_text())

    def mark_started(self, slug: str) -> None:
        with self._lock:
            self._state.setdefault(slug, {})
            self._state[slug]["status"] = "in_progress"
            self._save()

    def mark_completed(self, slug: str) -> None:
        with self._lock:
            self._state.setdefault(slug, {})
            self._state[slug]["status"] = "completed"
            self._save()

    def is_completed(self, slug: str) -> bool:
        with self._lock:
            return self._state.get(slug, {}).get("status") == "completed"

    def completed_count(self) -> int:
        with self._lock:
            return sum(1 for v in self._state.values() if v.get("status") == "completed")

    def save_checkpoint(self, slug: str, data: dict) -> None:
        with self._lock:
            self._state.setdefault(slug, {})
            self._state[slug]["checkpoint"] = data
            self._save()

    def get_checkpoint(self, slug: str) -> dict | None:
        with self._lock:
            return self._state.get(slug, {}).get("checkpoint")

    def _save(self) -> None:
        # Atomic write: write to temp file in same directory, then rename
        parent = self._filepath.parent
        parent.mkdir(parents=True, exist_ok=True)
        fd, tmp_path = tempfile.mkstemp(dir=parent, suffix=".tmp")
        try:
            with open(fd, "w") as f:
                json.dump(self._state, f, indent=2)
            Path(tmp_path).replace(self._filepath)
        except BaseException:
            Path(tmp_path).unlink(missing_ok=True)
            raise
