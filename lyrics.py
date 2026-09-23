"""Pure lyrics logic (no Qt / SMTC): LRC parsing, search-term cleanup and
the playback clock."""

import re

# One or more leading [mm:ss(.xx)] timestamps, followed by the lyric text.
_TIMESTAMPS_LINE_RE = re.compile(r"^((?:\[\d+:\d+(?:\.\d+)?\])+)(.*)$")
_TIMESTAMP_RE = re.compile(r"\[(\d+):(\d+(?:\.\d+)?)\]")
_OFFSET_RE = re.compile(r"^\[offset:\s*([+-]?\d+)\s*\]$", re.IGNORECASE)
# Enhanced (word-by-word) LRC: "<mm:ss.xx> word <mm:ss.xx> word".
_WORD_TAG_RE = re.compile(r"<(\d+):(\d+(?:\.\d+)?)>")
# Musixmatch translations: an untimed "(translation)" line after its line.
_TRANSLATION_RE = re.compile(r"^\((.*)\)$")

INTRO_MARKER = "..."
INTRO_MIN_MS = 8000
INTRO_MARKER_MS = 5000
# An empty line followed by at least this much silence is an instrumental
# break and is shown as GAP_MARKER.
GAP_MARKER = "♪"
GAP_MIN_MS = 4000
# How long the last word of a line is highlighted over, at most.
LAST_WORD_MAX_MS = 1000


def _to_ms(minutes, seconds):
    return int(round((int(minutes) * 60 + float(seconds)) * 1000))


def make_track_id(title, artist):
    """Identifier used for caches and per-song offsets."""
    return f"{title} - {artist}"


def _split_words(text):
    """Split a line's text on <mm:ss.xx> word tags.

    Returns ``(display_text, words)`` where ``words`` is a list of
    ``(time_ms, text)`` whose texts concatenate to ``display_text``
    (whitespace collapsed). ``words`` is None when the line has no tags, and
    text before the first tag gets time None (the line's own start).
    """
    parts = _WORD_TAG_RE.split(text)
    if len(parts) == 1:
        return " ".join(text.split()), None

    # parts = [pre, m, s, seg, m, s, seg, ...]
    segments = [(None, parts[0])]
    for i in range(1, len(parts), 3):
        segments.append((_to_ms(parts[i], parts[i + 1]), parts[i + 2]))

    out = ""
    words = []
    for time_ms, seg in segments:
        seg = re.sub(r"\s+", " ", seg)
        if not out or out.endswith(" "):
            seg = seg.lstrip()
        if not seg:
            continue
        if not seg.strip():
            # Whitespace-only segment: part of the previous word's gap.
            if words:
                words[-1] = (words[-1][0], words[-1][1] + seg)
                out += seg
            continue
        words.append((time_ms, seg))
        out += seg

    stripped = out.rstrip()
    if words and len(stripped) < len(out):
        last_time, last_text = words[-1]
        words[-1] = (last_time, last_text[: len(last_text) - (len(out) - len(stripped))])
    return stripped, words


def parse_lrc(lrc_string):
    """Parse an LRC string into a time-sorted list of {"time", "text"} dicts.

    - Lines with several leading timestamps produce one entry per timestamp.
    - ``[offset:±N]`` is honoured; per the LRC spec a positive offset shows
      lyrics earlier, so ``time = t - offset``.
    - Other ``[tag:...]`` metadata lines are ignored.
    - Enhanced ``<mm:ss.xx>`` word tags are removed from the text and stored
      as ``"words": [(time_ms, text), ...]``.
    - A "..." marker is injected when the first lyric starts late.
    - An empty line followed by a gap of ``GAP_MIN_MS`` or more (or at the
      very end) becomes ``GAP_MARKER``.
    """
    if not lrc_string:
        return []

    offset = 0
    entries = []
    for raw_line in lrc_string.splitlines():
        line = raw_line.strip()
        if not line:
            continue

        offset_match = _OFFSET_RE.match(line)
        if offset_match:
            offset = int(offset_match.group(1))
            continue

        match = _TIMESTAMPS_LINE_RE.match(line)
        if not match:
            # Metadata ([ar:...], [ti:...]) or unsynced text.
            continue

        stamps, raw_text = match.groups()
        text, words = _split_words(raw_text)
        times = [_to_ms(m, s) for m, s in _TIMESTAMP_RE.findall(stamps)]
        for time_ms in times:
            entry = {"time": time_ms, "text": text}
            if words:
                # Word times belong to the first timestamp; shift them for
                # repeats of the line.
                shift = time_ms - times[0]
                entry["words"] = [
                    ((time_ms if t is None else t + shift), w) for t, w in words
                ]
            entries.append(entry)

    if not entries:
        return []

    for entry in entries:
        entry["time"] = max(0, entry["time"] - offset)
        if "words" in entry:
            entry["words"] = [(max(0, t - offset), w) for t, w in entry["words"]]

    # Stable sort keeps file order for identical timestamps.
    entries.sort(key=lambda e: e["time"])

    for i, entry in enumerate(entries):
        if entry["text"]:
            continue
        next_time = entries[i + 1]["time"] if i + 1 < len(entries) else None
        if next_time is None or next_time - entry["time"] >= GAP_MIN_MS:
            entry["text"] = GAP_MARKER

    if entries[0]["time"] > INTRO_MIN_MS:
        entries.insert(0, {"time": INTRO_MARKER_MS, "text": INTRO_MARKER})

    return entries


def has_word_timing(entries):
    return any(entry.get("words") for entry in entries)


def word_progress(words, now_ms, line_end_ms=None):
    """Fraction (0..1) of a line's characters sung at ``now_ms``.

    Progress moves through each word between its start and the next word's
    start; the last word ends at ``line_end_ms`` (at most
    ``LAST_WORD_MAX_MS`` after it starts).
    """
    if not words:
        return 0.0
    total = sum(len(text) for _, text in words)
    if total == 0:
        return 0.0

    done = 0
    for i, (start, text) in enumerate(words):
        if now_ms < start:
            break
        if i + 1 < len(words):
            end = words[i + 1][0]
        else:
            end = start + LAST_WORD_MAX_MS
            if line_end_ms is not None:
                end = min(end, line_end_ms)
        if end <= start or now_ms >= end:
            done += len(text)
            continue
        done += len(text) * (now_ms - start) / (end - start)
        break
    return max(0.0, min(1.0, done / total))


def parse_translations(lrc_string):
    """Extract Musixmatch-style translations: an untimed ``(text)`` line
    right after a timed line. Returns ``{time_ms: translation}``."""
    if not lrc_string:
        return {}
    offset = 0
    translations = {}
    last_times = []
    for raw_line in lrc_string.splitlines():
        line = raw_line.strip()
        if not line:
            continue
        offset_match = _OFFSET_RE.match(line)
        if offset_match:
            offset = int(offset_match.group(1))
            continue
        match = _TIMESTAMPS_LINE_RE.match(line)
        if match:
            last_times = [_to_ms(m, s) for m, s in _TIMESTAMP_RE.findall(match.group(1))]
            continue
        tr_match = _TRANSLATION_RE.match(line)
        if tr_match and last_times:
            text = tr_match.group(1).strip()
            if text:
                for t in last_times:
                    translations[t] = text
            last_times = []
    return {max(0, t - offset): text for t, text in translations.items()}


def attach_translations(entries, translations, tolerance_ms=1000):
    """Set ``entry["translation"]`` from the translation nearest in time
    (within ``tolerance_ms``). Returns the number of lines matched."""
    if not translations:
        return 0
    times = sorted(translations)
    matched = 0
    for entry in entries:
        if not entry["text"] or entry["text"] in (INTRO_MARKER, GAP_MARKER):
            continue
        nearest = min(times, key=lambda t: abs(t - entry["time"]))
        if abs(nearest - entry["time"]) <= tolerance_ms:
            entry["translation"] = translations[nearest]
            matched += 1
    return matched


def lyrics_fit_duration(entries, duration_ms, slack_ms=15000):
    """False when the lyrics run well past the track's end (likely the wrong
    song). Unknown durations (<= 0) always fit."""
    if not entries or duration_ms <= 0:
        return True
    return entries[-1]["time"] <= duration_ms + slack_ms


# --- Search-term cleanup ---

_BRACKETED_RE = re.compile(r"\s*[\(\[]([^\)\]]*)[\)\]]")
_NOISE_WORDS_RE = re.compile(
    r"\b(official|lyrics?|audio|video|visuali[sz]er|hd|hq|4k|mv|m/v|"
    r"remaster(ed)?|explicit|clean|feat\.?|ft\.?|featuring)\b",
    re.IGNORECASE,
)
_DASH_SUFFIX_NOISE_RE = re.compile(
    r"\s+[-–—]\s+((\d{4}\s+)?remaster(ed)?(\s+\d{4})?(\s+version)?|"
    r"official\s+(music\s+)?(video|audio)|lyrics?(\s+video)?)\s*$",
    re.IGNORECASE,
)
_TRAILING_QUALITY_RE = re.compile(r"\s+(hd|hq|4k)\s*$", re.IGNORECASE)
_FEAT_RE = re.compile(r"\s+(feat\.?|ft\.?|featuring)\s+[^\(\[\-–—]*", re.IGNORECASE)
_ARTIST_TITLE_RE = re.compile(r"^(.+?)\s+[-–—]\s+(.+)$")
_CHANNEL_RE = re.compile(
    r"(vevo$|\s-\s*topic$|\b(official|records|music|tv|channel|entertainment)\b)",
    re.IGNORECASE,
)


def _strip_noise(text):
    def drop_noisy(match):
        return "" if _NOISE_WORDS_RE.search(match.group(1)) else match.group(0)

    text = _BRACKETED_RE.sub(drop_noisy, text)
    text = _TRAILING_QUALITY_RE.sub("", text)
    text = _DASH_SUFFIX_NOISE_RE.sub("", text)
    text = _FEAT_RE.sub(" ", text)
    return " ".join(text.split())


def _loose(text):
    return re.sub(r"[^0-9a-z]", "", text.lower())


def _clean_artist(artist):
    artist = re.sub(r"\s*-\s*Topic$", "", artist, flags=re.IGNORECASE)
    artist = re.sub(r"\s*VEVO$", "", artist, flags=re.IGNORECASE)
    return _strip_noise(artist)


def clean_search_terms(title, artist):
    """Clean up SMTC metadata (often a YouTube video title and channel name)
    for a lyrics search. Returns ``(title, artist)``."""
    title = (title or "").strip()
    artist = (artist or "").strip()
    channel_like = not artist or bool(_CHANNEL_RE.search(artist))

    title = _strip_noise(title)
    clean_artist = _clean_artist(artist)

    split = _ARTIST_TITLE_RE.match(title)
    if split:
        left, right = split.group(1).strip(), split.group(2).strip()
        left_loose, artist_loose = _loose(left), _loose(clean_artist)
        same_artist = bool(left_loose) and bool(artist_loose) and (
            left_loose in artist_loose or artist_loose in left_loose
        )
        if channel_like or same_artist:
            return _strip_noise(right), _clean_artist(left)

    return title, clean_artist


class PlaybackClock:
    """Interpolates the track position between SMTC polls.

    ``sync`` takes the reported position and the perf-counter time (ms) at
    which it was captured; ``now`` returns the interpolated position.
    Small backward jitter is ignored so lyrics don't stutter, but a
    consistent backward jump (e.g. a short rewind) is honoured after
    ``BACKWARD_CONFIRM_POLLS`` consecutive samples.
    """

    SEEK_THRESHOLD_MS = 2000
    FORWARD_SNAP_MS = 150
    BACKWARD_SNAP_MS = 300
    BACKWARD_CONFIRM_POLLS = 2

    def __init__(self):
        self.is_playing = False
        self.track_time = 0
        self.sys_time = 0
        self._behind_count = 0

    def _snap(self, position, capture_ms):
        self.track_time = position
        self.sys_time = capture_ms
        self._behind_count = 0

    def sync(self, is_playing, position, capture_ms):
        if self.is_playing and is_playing:
            expected = self.track_time + (capture_ms - self.sys_time)
            diff = position - expected

            if abs(diff) > self.SEEK_THRESHOLD_MS:
                # Seek / skip: snap instantly.
                self._snap(position, capture_ms)
            elif diff > self.FORWARD_SNAP_MS:
                # We're behind the music: snap forward.
                self._snap(position, capture_ms)
            elif diff < -self.BACKWARD_SNAP_MS:
                # Reported position is behind us. A single sample may be
                # jitter; only snap back once it repeats.
                self._behind_count += 1
                if self._behind_count >= self.BACKWARD_CONFIRM_POLLS:
                    self._snap(position, capture_ms)
            else:
                self._behind_count = 0
        else:
            # Not playing, or just started: trust the reported position.
            self._snap(position, capture_ms)

        self.is_playing = is_playing

    def now(self, perf_ms):
        if self.is_playing:
            return self.track_time + (perf_ms - self.sys_time)
        return self.track_time

    def reset(self):
        self.is_playing = False
        self._snap(0, 0)
