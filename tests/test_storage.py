import json

from storage import LyricsDiskCache, TrackOffsets


def test_track_offsets_round_trip(tmp_path):
    path = str(tmp_path / "track_offsets.json")
    offsets = TrackOffsets(path)
    assert offsets.get("Song - Artist") == 0
    offsets.set("Song - Artist", 300)
    offsets.set("Other - Artist", -200)
    offsets.save()

    reloaded = TrackOffsets(path)
    assert reloaded.get("Song - Artist") == 300
    assert reloaded.get("Other - Artist") == -200
    assert reloaded.get("Unknown - Artist") == 0


def test_track_offsets_zero_removes_entry(tmp_path):
    path = str(tmp_path / "track_offsets.json")
    offsets = TrackOffsets(path)
    offsets.set("Song - Artist", 300)
    offsets.set("Song - Artist", 0)
    offsets.save()
    assert json.loads((tmp_path / "track_offsets.json").read_text("utf-8")) == {}


def test_track_offsets_ignore_bad_file(tmp_path):
    path = tmp_path / "track_offsets.json"
    path.write_text(json.dumps({"a": "x", "b": True, "c": 5}), encoding="utf-8")
    assert TrackOffsets(str(path)).offsets == {"c": 5}
    path.write_text("not json", encoding="utf-8")
    assert TrackOffsets(str(path)).offsets == {}


def test_lyrics_cache_hit_and_miss(tmp_path):
    cache = LyricsDiskCache(str(tmp_path / "lyrics_cache"))
    assert cache.get("Song - Artist") is None
    lyrics = [{"time": 1000, "text": "hi", "words": [[1000, "hi"]]}]
    cache.put("Song - Artist", lyrics)
    assert cache.get("Song - Artist") == lyrics
    assert cache.get("Song - Artist\nenhanced") is None


def test_lyrics_cache_not_found_expires(tmp_path):
    cache = LyricsDiskCache(str(tmp_path / "lyrics_cache"))
    cache.put("Song - Artist", [], now=1_000_000)
    week = LyricsDiskCache.NOT_FOUND_TTL
    assert cache.get("Song - Artist", now=1_000_000 + week - 1) == []
    assert cache.get("Song - Artist", now=1_000_000 + week + 1) is None


def test_found_lyrics_never_expire(tmp_path):
    cache = LyricsDiskCache(str(tmp_path / "lyrics_cache"))
    cache.put("Song - Artist", [{"time": 1, "text": "a"}], now=0)
    assert cache.get("Song - Artist", now=10**10) == [{"time": 1, "text": "a"}]


def test_lyrics_cache_clear(tmp_path):
    cache = LyricsDiskCache(str(tmp_path / "lyrics_cache"))
    cache.put("Song - Artist", [{"time": 1, "text": "a"}])
    cache.clear()
    assert cache.get("Song - Artist") is None
    cache.put("Song - Artist", [])  # the directory is recreated
    assert cache.get("Song - Artist") == []
