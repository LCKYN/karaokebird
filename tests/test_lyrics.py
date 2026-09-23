import pytest

from lyrics import (
    PlaybackClock,
    attach_translations,
    clean_search_terms,
    has_word_timing,
    lyrics_fit_duration,
    parse_lrc,
    parse_translations,
    word_progress,
)


def times_and_texts(entries):
    return [(e["time"], e["text"]) for e in entries]


# --- parse_lrc ---


def test_single_timestamp_lines():
    lrc = "[00:01.00] one\n[00:02.50] two"
    assert times_and_texts(parse_lrc(lrc)) == [(1000, "one"), (2500, "two")]


def test_multi_timestamp_line_emits_one_entry_each():
    lrc = "[00:01.00][00:05.00] chorus\n[00:03.00] verse"
    assert times_and_texts(parse_lrc(lrc)) == [
        (1000, "chorus"),
        (3000, "verse"),
        (5000, "chorus"),
    ]


def test_timestamp_without_fraction():
    assert times_and_texts(parse_lrc("[00:01.5] a\n[01:02] b")) == [
        (1500, "a"),
        (62000, "b"),
    ]


def test_positive_offset_shows_lyrics_earlier():
    lrc = "[offset:+500]\n[00:02.00] a\n[00:03.00] b"
    assert times_and_texts(parse_lrc(lrc)) == [(1500, "a"), (2500, "b")]


def test_negative_offset_shows_lyrics_later():
    lrc = "[offset:-250]\n[00:02.00] a"
    assert times_and_texts(parse_lrc(lrc)) == [(2250, "a")]


def test_offset_never_goes_negative():
    lrc = "[offset:5000]\n[00:01.00] a"
    assert times_and_texts(parse_lrc(lrc)) == [(0, "a")]


def test_metadata_lines_are_ignored():
    lrc = "[ar:Artist]\n[ti:Title]\n[length:03:20]\n[by:someone]\n[00:01.00] a"
    assert times_and_texts(parse_lrc(lrc)) == [(1000, "a")]


def test_empty_and_blank_lines():
    assert parse_lrc("") == []
    assert parse_lrc(None) == []
    assert parse_lrc("\n  \n") == []
    lrc = "[00:01.00] a\n\n   \n[00:02.00] b"
    assert times_and_texts(parse_lrc(lrc)) == [(1000, "a"), (2000, "b")]


def test_empty_lyric_text_is_kept():
    # Instrumental breaks are encoded as timestamps with no text.
    lrc = "[00:01.00] a\n[00:02.00]\n[00:03.00] b"
    assert times_and_texts(parse_lrc(lrc)) == [(1000, "a"), (2000, ""), (3000, "b")]


def test_empty_line_before_long_gap_becomes_gap_marker():
    lrc = "[00:01.00] a\n[00:02.00]\n[00:06.00] b"
    assert times_and_texts(parse_lrc(lrc)) == [(1000, "a"), (2000, "♪"), (6000, "b")]


def test_trailing_empty_line_becomes_gap_marker():
    lrc = "[00:01.00] a\n[00:02.00]"
    assert times_and_texts(parse_lrc(lrc)) == [(1000, "a"), (2000, "♪")]


def test_plain_text_is_not_parsed():
    assert parse_lrc("just some\nplain lyrics") == []


def test_intro_marker_for_late_first_line():
    entries = times_and_texts(parse_lrc("[00:20.00] late"))
    assert entries == [(5000, "..."), (20000, "late")]


def test_no_intro_marker_for_early_first_line():
    entries = times_and_texts(parse_lrc("[00:07.00] early"))
    assert entries == [(7000, "early")]


def test_crlf_line_endings():
    lrc = "[00:01.00] a\r\n[00:02.00] b\r\n"
    assert times_and_texts(parse_lrc(lrc)) == [(1000, "a"), (2000, "b")]


# --- PlaybackClock ---


def playing_clock(position=10_000, at=1_000):
    clock = PlaybackClock()
    clock.sync(True, position, at)
    return clock


def test_clock_interpolates_while_playing():
    clock = playing_clock(10_000, 1_000)
    assert clock.now(1_500) == 10_500


def test_clock_frozen_when_paused():
    clock = PlaybackClock()
    clock.sync(False, 10_000, 1_000)
    assert clock.now(5_000) == 10_000


def test_clock_ignores_small_jitter():
    clock = playing_clock(10_000, 1_000)
    # Expected 10_200 at t=1_200; report slightly ahead / behind.
    clock.sync(True, 10_300, 1_200)
    assert clock.now(1_200) == 10_200
    clock.sync(True, 10_200, 1_400)
    assert clock.now(1_400) == 10_400


def test_clock_snaps_forward_when_behind():
    clock = playing_clock(10_000, 1_000)
    clock.sync(True, 10_400, 1_200)  # 200ms ahead of expected
    assert clock.now(1_200) == 10_400


def test_clock_ignores_single_backward_sample():
    clock = playing_clock(10_000, 1_000)
    clock.sync(True, 9_700, 1_200)  # 500ms behind expected 10_200
    assert clock.now(1_200) == 10_200


def test_clock_snaps_after_two_backward_samples():
    clock = playing_clock(10_000, 1_000)
    clock.sync(True, 8_700, 1_200)  # expected 10_200, 1.5s behind
    assert clock.now(1_200) == 10_200
    clock.sync(True, 8_900, 1_400)  # expected 10_400, still behind
    assert clock.now(1_400) == 8_900


def test_clock_backward_counter_resets_between_samples():
    clock = playing_clock(10_000, 1_000)
    clock.sync(True, 9_700, 1_200)  # behind
    clock.sync(True, 10_400, 1_400)  # back in line
    clock.sync(True, 10_100, 1_600)  # behind once more
    assert clock.now(1_600) == 10_600


def test_clock_snaps_on_seek():
    clock = playing_clock(10_000, 1_000)
    clock.sync(True, 60_000, 1_200)
    assert clock.now(1_200) == 60_000
    clock.sync(True, 5_000, 1_400)
    assert clock.now(1_400) == 5_000


def test_clock_snaps_when_resuming():
    clock = PlaybackClock()
    clock.sync(False, 10_000, 1_000)
    clock.sync(True, 9_000, 2_000)
    assert clock.now(2_000) == 9_000


# --- Word-by-word (enhanced) LRC ---

ENHANCED = (
    "[00:12.00] <00:12.00> I <00:12.50>   <00:12.80> said, <00:13.40> ooh \n"
    "[00:15.00] plain line\n"
)


def test_word_tags_are_parsed_and_stripped():
    entries = parse_lrc(ENHANCED)
    line = entries[1]
    assert line["text"] == "I said, ooh"
    assert line["words"] == [(12000, "I "), (12800, "said, "), (13400, "ooh")]
    assert "".join(text for _, text in line["words"]) == line["text"]
    assert "words" not in entries[2]


def test_text_before_first_word_tag_starts_with_the_line():
    entries = parse_lrc("[00:01.00] Oh <00:01.50> yeah")
    assert entries[0]["words"] == [(1000, "Oh "), (1500, "yeah")]


def test_word_times_follow_offset_and_repeats():
    lrc = "[offset:+500]\n[00:02.00][00:10.00] <00:02.00> a <00:02.50> b"
    first, second = parse_lrc(lrc)
    assert first["words"] == [(1500, "a "), (2000, "b")]
    assert second["words"] == [(9500, "a "), (10000, "b")]


def test_has_word_timing():
    assert has_word_timing(parse_lrc(ENHANCED))
    assert not has_word_timing(parse_lrc("[00:01.00] plain"))


def test_word_progress():
    words = [(1000, "ab"), (2000, "cd")]
    assert word_progress(words, 500) == 0.0
    assert word_progress(words, 1000) == 0.0
    assert word_progress(words, 1500) == 0.25
    assert word_progress(words, 2000) == 0.5
    # Last word ends at the next line (or after at most 1s).
    assert word_progress(words, 2250, line_end_ms=2500) == 0.75
    assert word_progress(words, 2500) == 0.75
    assert word_progress(words, 5000) == 1.0
    assert word_progress([], 1000) == 0.0


# --- Translations ---


def test_parse_translations():
    lrc = "[00:12.00] Hola\n(Hello)\n[00:15.00] Adios\n(Goodbye)\n[00:18.00] nada\n"
    assert parse_translations(lrc) == {12000: "Hello", 15000: "Goodbye"}
    assert parse_translations(None) == {}


def test_attach_translations_by_nearest_time():
    entries = parse_lrc("[00:12.00] Hola\n[00:15.00] Adios\n[00:30.00] nada")
    matched = attach_translations(entries, {12300: "Hello", 15000: "Goodbye"})
    assert matched == 2
    texts = {e["text"]: e.get("translation") for e in entries}
    assert texts == {"...": None, "Hola": "Hello", "Adios": "Goodbye", "nada": None}


# --- Duration sanity check ---


def test_lyrics_fit_duration():
    entries = parse_lrc("[00:01.00] a\n[03:00.00] b")
    assert lyrics_fit_duration(entries, 180_000)
    assert lyrics_fit_duration(entries, 170_000)  # within the 15s slack
    assert not lyrics_fit_duration(entries, 120_000)
    assert lyrics_fit_duration(entries, 0)  # unknown duration
    assert lyrics_fit_duration([], 1000)


# --- Search-term cleanup ---


@pytest.mark.parametrize(
    "title, artist, expected",
    [
        ("Mr. Brightside", "The Killers", ("Mr. Brightside", "The Killers")),
        (
            "Queen - Bohemian Rhapsody (Official Video)",
            "Queen Official",
            ("Bohemian Rhapsody", "Queen"),
        ),
        ("Bohemian Rhapsody - Remastered 2011", "Queen", ("Bohemian Rhapsody", "Queen")),
        ("Here Comes The Sun (Remastered 2009)", "The Beatles", ("Here Comes The Sun", "The Beatles")),
        ("Blinding Lights", "The Weeknd - Topic", ("Blinding Lights", "The Weeknd")),
        ("Song (feat. Someone) [Lyrics]", "ArtistVEVO", ("Song", "Artist")),
        ("Bad Guy (Official Music Video) HD", "Billie Eilish", ("Bad Guy", "Billie Eilish")),
        (
            "Dua Lipa - Levitating ft. DaBaby (Official Video)",
            "Dua Lipa",
            ("Levitating", "Dua Lipa"),
        ),
        ("Adele - Hello", "", ("Hello", "Adele")),
        ("Song Title (Live at Wembley)", "Band", ("Song Title (Live at Wembley)", "Band")),
        # A dash in a real title, with a real artist, is left alone.
        ("Part 1 - The Beginning", "Some Band", ("Part 1 - The Beginning", "Some Band")),
    ],
)
def test_clean_search_terms(title, artist, expected):
    assert clean_search_terms(title, artist) == expected
