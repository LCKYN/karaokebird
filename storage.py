"""Small JSON stores in the app data directory (no Qt)."""

import hashlib
import json
import logging
import os
import shutil
import time


def atomic_write_json(path, data):
    """Write JSON to ``path`` via a temp file, so a crash can't leave a
    half-written file behind."""
    tmp_path = path + ".tmp"
    with open(tmp_path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=1)
    os.replace(tmp_path, path)


def _read_json(path):
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


class TrackOffsets:
    """Per-song sync offsets, ``{track_id: ms}``, stored in one JSON file."""

    def __init__(self, path):
        self.path = path
        self.offsets = {}
        self.load()

    def load(self):
        if not os.path.exists(self.path):
            return
        try:
            data = _read_json(self.path)
        except Exception:
            logging.exception("Error loading per-song offsets")
            return
        if not isinstance(data, dict):
            logging.warning("Ignoring malformed per-song offsets file")
            return
        self.offsets = {
            str(k): int(v)
            for k, v in data.items()
            if isinstance(v, (int, float)) and not isinstance(v, bool)
        }

    def save(self):
        try:
            atomic_write_json(self.path, self.offsets)
        except Exception:
            logging.exception("Error saving per-song offsets")

    def get(self, track_id):
        return self.offsets.get(track_id, 0)

    def set(self, track_id, ms):
        if ms:
            self.offsets[track_id] = int(ms)
        else:
            self.offsets.pop(track_id, None)


class LyricsDiskCache:
    """One JSON file per lookup: ``<sha1(key)>.json`` holding the parsed
    lyrics. Found lyrics are kept indefinitely; "not found" results expire
    after ``NOT_FOUND_TTL`` so they're retried eventually."""

    NOT_FOUND_TTL = 7 * 24 * 3600

    def __init__(self, directory):
        self.directory = directory

    def _path(self, key):
        digest = hashlib.sha1(key.encode("utf-8")).hexdigest()
        return os.path.join(self.directory, f"{digest}.json")

    def get(self, key, now=None):
        """Return the cached lyrics list (possibly empty = not found), or
        None on a miss or an expired "not found"."""
        path = self._path(key)
        if not os.path.exists(path):
            return None
        try:
            data = _read_json(path)
            lyrics = data["lyrics"]
            fetched_at = float(data.get("fetched_at", 0))
        except Exception:
            logging.exception(f"Ignoring unreadable lyrics cache entry {path}")
            return None
        if not isinstance(lyrics, list):
            return None
        now = time.time() if now is None else now
        if not lyrics and now - fetched_at > self.NOT_FOUND_TTL:
            return None
        return lyrics

    def put(self, key, lyrics, now=None):
        try:
            os.makedirs(self.directory, exist_ok=True)
            atomic_write_json(
                self._path(key),
                {
                    "key": key,
                    "fetched_at": time.time() if now is None else now,
                    "lyrics": lyrics,
                },
            )
        except Exception:
            logging.exception("Error writing lyrics cache")

    def clear(self):
        if os.path.isdir(self.directory):
            shutil.rmtree(self.directory, ignore_errors=True)
