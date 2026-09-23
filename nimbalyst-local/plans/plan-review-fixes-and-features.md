# Review follow-up: bug fixes, UX polish, and new features — 6 implement sessions

## Context

A second review of KaraokeBird (`main.py`, `ui_components.py`, `settings_ui.py`) found 6 major bugs, a set of smaller bugs and cleanups, UX gaps, and feature opportunities. Two bugs were reproduced: the LRC multi-timestamp parse, and the StrokedLabel animation race. SMTC timestamps were checked against a live Spotify session and are correct: `last_updated_time` is tz-aware UTC and refreshes about every 0.5s, so the position math needs no change.

The work is split into 6 sessions. Sessions 1–2 fix bugs, session 3 polishes the UX, and sessions 4–6 add features. **Run them in order, one at a time.** They all touch `main.py`, and later sessions build on helpers added earlier (`PlaybackClock`, settings validation, the tray menu layout).

The repo has no tests. Session 1 adds `tests/` with pytest for pure logic. Each later session adds tests for any new pure logic (parsing, title cleanup, geometry, settings validation). Qt and SMTC behaviour is verified by running the app.

### Defaults chosen (change here before implementing if you disagree)
- **Update check:** on by default, can be turned off in settings, at most once per day, and makes only an anonymous GitHub API GET. The README privacy section must mention it.
- **Auto-hide when idle:** on by default, 5s after pause or when no media is playing.
- **Karaoke word highlight and translation line:** off by default (opt-in).
- **Track-info Y slider:** keep the stored meaning and flip only the slider's appearance, so existing `settings.json` files don't need a migration.

---

## Session 1 — Sync & lyrics correctness (backend)

**Files:** `main.py` (`SpotifyReader`, `main_loop`, `OverlayWindow.on_playback_sync` / `on_lyrics_found` / `update_status`), new `lyrics.py`, new `tests/`.

1. **Stale lyrics race.** In `fetch_lyrics` (`main.py:169`), still write the result to the cache, but emit only when `track_id == self.current_track_id`. Do the same in the exception path. Keep the `lyrics_found` signal signature unchanged.
2. **Keep references to lyrics tasks.** Store the tasks from `asyncio.create_task` (`main.py:164`) in `self._tasks`, a set, and remove each one with `add_done_callback(self._tasks.discard)`.
3. **Handle the player closing.** In `poll_status` (`main.py:98-101`), when there is no session and one existed before (`current_track_id is not None`), emit a new `session_lost` signal and set `current_track_id = None`. Connect it as follows:
   - `OverlayWindow.on_session_lost` sets `is_playing = False`, clears `lyrics_data` and the labels, and resets `current_title` and `current_artist`.
   - `TrackInfoWindow.set_track_info("", "")` clears the track info.
4. **Stop the status message spam.** Emit "Waiting for media..." only when the state changes from having a session to having none, not on every poll. Track the last emitted status on the reader.
5. **Keep polling alive after errors.**
   - Put the whole `poll_status` body in a `try`, including `get_current_session()`.
   - Make `main_loop` (`main.py:722`) catch and log errors inside its loop so the task never exits.
   - If `self.manager is None`, call `setup()` again every 5s instead of returning forever.
6. **LRC parser rewrite.** Move `parse_lrc` into a module-level function in new `lyrics.py`. It is currently a method that doesn't use `self`. The reader calls it from there.
   - Match every leading timestamp with the regex `^((?:\[\d+:\d+(?:\.\d+)?\])+)(.*)$`. Emit one entry for each timestamp.
   - Honour `[offset:±N]`. Per the LRC spec, a positive offset shows lyrics earlier, so compute `time = t - offset`.
   - Ignore other `[tag:…]` metadata lines.
   - Keep the existing `"..."` intro marker.
7. **Synced lyrics only.** Call `syncedlyrics.search(term, synced_only=True)` so plain lyrics are never parsed into `[]`.
8. **Correct small backward jumps.** Move the drift logic in `on_playback_sync` (`main.py:464-491`) into a pure `PlaybackClock` class in `lyrics.py` with `sync(is_playing, position, capture_ms)` and `now(perf_ms)`.
   - Keep the existing forward snap (more than 150ms behind) and the large-jump snap (more than 2000ms).
   - **New:** when the reported position is more than 300ms behind on **2 polls in a row**, snap backward.
   - Reset the counter on any other outcome.
   - `OverlayWindow` delegates to the clock.
9. **Tests** (`tests/test_lyrics.py`):
   - Multi-timestamp lines, the offset tag, metadata lines, and empty lines.
   - The intro marker.
   - `PlaybackClock`: jitter is ignored, one backward sample is ignored, two backward samples snap, and a seek snaps.

**Verification:**
- `pytest` passes.
- Skip tracks quickly, 4–5 times in 2s. The final track's lyrics win (check the log to see which search finished when).
- Close Spotify mid-song. The overlay clears and stops advancing.
- Rewind by about 1.5s. The lyrics catch up within about 0.5s.
- With no player open, "Waiting for media..." fades after 5s and doesn't come back.

---

## Session 2 — Overlay, settings dialog, and hotkey bugs + cleanup

**Files:** `ui_components.py`, `main.py` (`OverlayWindow`, `register_hotkeys`, `create_tray_icon`, `resource_path`, `main()`), `settings_ui.py`.

1. **Animation race** (`ui_components.py:185-188`). In the non-animated branch of `StrokedLabel.setText`:
   - Call `_anim_group.stop()`.
   - Set `_anim_stage = 0`, `_pending_text = text`, and reset opacity, offset and scale to 1.0, 0.0 and 1.0.
   - Then call `super().setText`.
2. **Long lines.** In `StrokedLabel.paintEvent`, if the text width is more than `width() - 2*margin`, draw with a copy of the font scaled by `available / tw`, but no smaller than 0.6×. Do the same in `MarqueeLabel` when scrolling is disabled.
3. **Overlay height fits its content.** Add `get_overlay_height(settings)` to `settings_ui.py`: the sum of line heights (highlight plus `num_history + num_future` context lines) plus padding, and at least 400.
   - Pass `settings` into `get_overlay_position_bounds` and replace every use of `OVERLAY_HEIGHT` (`settings_ui.py:89-90`, `95-104`) with it.
   - Add a geometry test.
4. **Live preview of every setting.**
   - `update_screen_selection` (`settings_ui.py:798`) must call `self.update_preview()`.
   - The live preview in `show_settings` (`main.py:792`) calls `window.apply_settings(dlg.temp_settings)`. That call only rebuilds labels when the line counts change, so it stays cheap.
5. **Remove the dead code in `apply_geometry`** (`main.py:423-427`). Don't overwrite the current label on geometry changes. Only call `update_display` when an index ≥ 0 is being shown.
6. **Hotkeys.**
   - Register each hotkey in its own `try` (`main.py:699-716`). `register_hotkeys` returns the list of hotkeys that failed. Callers show a tray `showMessage` naming them.
   - On both `QKeySequenceEdit`s call `setClearButtonEnabled(True)` and `setMaximumSequenceLength(1)` (`settings_ui.py:616`, `631`).
   - Change the duplicate warning to "…both actions will trigger" (`settings_ui.py:719`).
   - Extend `_normalize_hotkey` for Qt names that `keyboard` doesn't accept: Del, Ins, Esc, PgUp/PgDown. Test with `keyboard.parse_hotkey`.
7. **`resource_path`** (`main.py:735`): fall back to `os.path.dirname(os.path.abspath(__file__))`, not the current directory.
8. **Single instance.** In `main()`, take a `QLockFile(os.path.join(get_app_data_dir(), "karaokebird.lock"))`. If `tryLock(100)` fails, show a `QMessageBox.information` saying "KaraokeBird is already running (see the system tray)" and exit.
9. **Settings hardening** (`settings_ui.py:194-208`).
   - `load()` keeps a loaded value only when its type matches the default's type. Treat int and float as compatible, but a bool must stay a bool, because `isinstance(True, int)` is true. Ignore unknown keys and log each rejected key.
   - `save()` writes to `SETTINGS_FILE + ".tmp"`, then calls `os.replace`, with `encoding="utf-8"`.
   - Add tests.
10. **Cleanup.**
    - Remove unused imports (`QPainter`, `QPainterPath`, `QPen` in main.py), `LYRICS_LOADED_INDEX` and `SettingsManager.get`.
    - Connect `update_setting` only to each spinbox; the slider just drives the spinbox. This halves the updates per tick.
    - Start `MarqueeLabel`'s timer only while scrolling is enabled and needed.
    - Stop the overlay's 50ms timer in `hideEvent` and restart it in `showEvent`.

**Verification:**
- **Settings dialog:** pick another monitor and the overlay moves right away. Change the history/future counts and the overlay updates before you save. Cancel reverts everything.
- **Hotkeys:** clear a hotkey and save; it's gone. An invalid hotkey shows a tray warning, and the other hotkey still works.
- **Second launch:** shows the message and exits.
- **Corrupt settings:** put `"font_size_normal": "14"` in settings.json. The app starts, the log names the rejected key, and the default is used.
- **Long lines:** a very long lyric line shrinks and isn't clipped.
- **Idle timers:** with the overlay hidden, CPU wakeups drop (check in Task Manager or Process Explorer).

---

## Session 3 — UX polish

**Files:** `main.py`, `settings_ui.py`, `ui_components.py`.

1. **Auto-hide when idle.** New setting `auto_hide_idle: True`. When playback has been paused for 5s or more, or there's no session, animate `windowOpacity` to 0 on both windows. Animate back to 1 when playback resumes. Use opacity, not `hide()`, so the manual show/hide toggle and the tray checkmarks stay independent.
2. **Readability background.** New setting `background_enabled: False`. Reuse the existing `background_color` key.
   - Existing `"rgba(0, 0, 0, 100)"` values can't be read by `QColor`. Convert them to `#AARRGGBB` when loading.
   - Pick the color with `QColorDialog.ColorDialogOption.ShowAlphaChannel`.
   - In `OverlayWindow.paintEvent`, draw a rounded rect around the union of the visible labels' text bounds, with 12px padding.
   - Add a matching toggle to `TrackInfoWindow`.
3. **Gap indicator.** In `lyrics.parse_lrc`, an empty line followed by a gap of 4s or more becomes `"♪"`.
4. **"Searching lyrics…" status.** Emit it when a search starts, but not on a cache hit.
5. **Tray.**
   - Tooltip: `KaraokeBird — <title> — <artist>`, updated on `track_changed`.
   - Left-click (`ActivationReason.Trigger`) toggles the overlay.
   - "Sync offset" submenu with −100ms, +100ms and Reset. It shows the current value and saves right away. The overlay already reads `sync_offset_ms` live.
6. **Settings dialog.**
   - Put each tab in a `QScrollArea` so the dialog fits on 768px-tall screens.
   - Call `setInvertedAppearance(True)` on the track-info Y slider and relabel it "Down / Up". Both vertical sliders then read "down ← → up" without changing stored values.
   - The preview shows up to 2 history and 2 upcoming labels, matching the counts.
   - Add a "Preview animation" button that runs the selected transition on the preview labels.
7. **Remember visibility.** New settings `overlay_visible: True` and `track_info_manually_hidden: False`. Save them when toggled and apply them at startup.
8. **Display changes.** Connect `app.screenAdded`, `app.screenRemoved` and each screen's `geometryChanged` to reapply geometry on both windows. If the saved `screen_index` no longer exists, fall back to the primary screen; this is already handled in `get_target_screen`.

**Verification:**
- Pause and both windows fade out after 5s; press play and they fade back in. The show/hide hotkey still works independently.
- Turn on the background over a white web page and the text is readable.
- Tray left-click toggles the overlay; the tooltip shows the song; the offset submenu changes sync live and is saved.
- Unplug or replug a second monitor and the overlay moves to a valid screen.
- Restart the app and the visibility state is kept.

---

## Session 4 — Drag-to-position edit mode

**Files:** `main.py` (`OverlayWindow`, `TrackInfoWindow`, `create_tray_icon`), `settings_ui.py` (a reverse-geometry helper).

1. Add a tray action "Edit Position" (checkable), plus a "Done" button in edit mode.
2. **Entering edit mode:**
   - Clear `WindowTransparentForInput` with `setWindowFlag(..., False)`, then `show()`.
   - Force the windows fully opaque, ignoring auto-hide.
   - Draw a dashed rounded border and a hint ("Drag to move · Enter/Esc to finish").
   - If no lyrics are loaded, show sample text.
3. **Dragging:** `mousePressEvent`/`mouseMoveEvent` move the window. On release, convert the window position back to offsets with new helpers in `settings_ui.py`:
   - `overlay_offsets_from_pos(settings, screen_geom, x, y)`, which inverts `compute_overlay_geometry`: `x_off = x - center_x`, `y_off = base_y - y`.
   - `track_info_offsets_from_pos(...)`.
   - Clamp both to the existing bounds and add round-trip tests for both.
4. If the window is dropped on another monitor, set `screen_index` to `QApplication.screenAt(center)`.
5. **Leaving edit mode** (Enter, Esc, "Done" or the tray action): restore click-through, save settings and reapply them.
6. Add a "Drag on screen…" button to the settings dialog's Layout tab that closes the dialog with Save and enters edit mode.

**Verification:** drag both windows around, including to a second monitor, then restart; the positions are kept. The sliders in Settings match the dragged positions. After leaving edit mode, clicks pass through again.

---

## Session 5 — Lyrics features

**Files:** `lyrics.py`, `main.py`, `ui_components.py`, `settings_ui.py`.

**Before starting,** call `syncedlyrics.search(term, enhanced=True)` and `lang="en"` on 3–4 real songs and save the raw output to the scratchpad. The parsing steps below assume the usual formats (`<mm:ss.xx>` word tags, translation lines); adjust them to what actually comes back.

1. **Word-by-word highlighting.** New setting `word_highlight: False`.
   - When it's on, search with `enhanced=True`. `parse_lrc` fills `words: [(time_ms, text), …]` for each line. If the result has no word tags, fall back to line-level lyrics.
   - Add `StrokedLabel.setProgress(0..1)`. It draws the path in the context color, then again in the highlight color clipped to `x < progress * tw`.
   - `update_frame` computes progress between word times.
   - Tests for parsing word tags.
2. **Translation line.** New setting `translation_lang: ""` (off), a combo box of common codes (en, th, ja, ko, zh, es, …). Run a second search with `lang=`. Align the translations to lines by timestamp and show them in a new small label under the current line. If there's no translation, the label stays empty.
3. **Per-song sync offset.** Store `track_offsets.json` in the app data directory as `{track_id: ms}`.
   - Effective offset = global + per-track.
   - Add a "This song" section to the Session 3 tray submenu.
   - Add optional hotkeys `offset_back_hotkey` / `offset_fwd_hotkey` (±100ms per-track, saved after 1s of no changes).
4. **Better lyrics matching.** Add `clean_search_terms(title, artist)` to `lyrics.py`:
   - Remove bracketed noise: (Official Video/Audio/Lyric Video), [Lyrics], (HD), (Remastered 20xx), feat./ft. clauses.
   - Remove `VEVO` and ` - Topic` from artist names.
   - If the title looks like "Artist - Title" and the artist looks like a channel name, split the title.
   - **Sanity check:** reject a result whose last timestamp is more than the track duration + 15s. Pass `duration` through `playback_sync`; it isn't used yet.
   - **"Wrong lyrics? Search again"** tray action: retry with the next provider (`providers=[…]`, rotating through Lrclib, Musixmatch, NetEase, Megalobiz) and replace the cache entry.
   - Tests for `clean_search_terms`.
5. **Disk cache.** Store `lyrics_cache/<sha1(track_id)>.json` in the app data directory. Read it before any network search. Keep "not found" results for only 7 days. Add a "Clear lyrics cache" button to the System tab.

**Verification:**
- A song with word-level lyrics highlights word by word in sync; a song without them falls back to whole lines.
- With a translation language set, a translated line appears for a song that has one.
- The per-song offset is kept after a restart and doesn't affect other songs.
- A YouTube video titled "Artist - Song (Official Video)" finds lyrics.
- "Search again" changes the result.
- A song played before loads lyrics with no network access.

---

## Session 6 — System features

**Files:** `main.py`, `settings_ui.py`, `README.md`.

1. **Choose the media source.** New setting `preferred_source: ""` (Auto).
   - Auto: pick the first session from `manager.get_sessions()` whose status is PLAYING, else `get_current_session()`.
   - A "Source" tray submenu lists `source_app_user_model_id`s, with friendly names for Spotify, Chrome, Edge, Firefox and Apple Music. It's rebuilt on `aboutToShow`.
2. **Start with Windows.** Add a checkbox to the System tab that uses `winreg` on `HKCU\Software\Microsoft\Windows\CurrentVersion\Run`, value `KaraokeBird`.
   - Frozen build: `"sys.executable"`.
   - Dev: `"pythonw.exe" "…\main.py"`.
   - The checkbox reads the registry for its state.
3. **Style presets.** Add a combo box and Save/Delete buttons to the Appearance tab. A preset stores only the appearance keys (fonts, colors, stroke, animation, background) in `settings["presets"]`.
4. **Update check.**
   - Add `APP_VERSION` to `main.py`.
   - At most once per day (store `last_update_check`), when `update_check_enabled: True`, fetch `https://api.github.com/repos/joshshiman/karaokebird/releases/latest` with `urllib` inside `asyncio.to_thread`, using a 5s timeout.
   - If the release is newer, show a tray message and rename the menu item to "Update available — vX.Y.Z".
   - Log failures and do nothing else.
5. **README:** document the new settings, edit mode, sources and presets, and add the update check and lyrics disk cache to the privacy/safety section.

**Verification:**
- With Spotify paused and a YouTube tab playing, Auto follows YouTube; choosing Spotify pins to it.
- Start with Windows: the Run value appears and disappears as expected, and the app starts after sign-in.
- Presets restore their looks correctly.
- Set `APP_VERSION` to "0.0.0" and the update message appears once; with the check disabled, nothing is fetched.
