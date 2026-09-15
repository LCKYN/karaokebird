# Fix UX/UI/Performance/Reliability findings — split into 3 implement sessions

## Context

A code review of the KaraokeBird PyQt6 app (`main.py`, `ui_components.py`, `settings_ui.py`) surfaced 11 concrete issues spanning UX polish, rendering performance, and reliability/packaging hygiene. The user wants all of them fixed, but split into 3 separate implement sessions rather than one big change. This plan groups the 11 fixes into 3 self-contained sessions, ordered so each session touches a distinct area of the code and later sessions don't fight earlier ones over the same functions.

**Recommended run order:** Session 1 → Session 2 → Session 3 (run sequentially, not in parallel worktrees, since all three touch `main.py`, just different classes/functions within it).

---

## Session 1 — Overlay rendering & geometry performance

**Why this first:** it's the highest-impact fix (visible stutter/flicker today) and touches the core windows classes once, cleanly, before the other sessions add logging/validation around them.

**Files:** `main.py` (`OverlayWindow`, `TrackInfoWindow`, `create_tray_icon`/`show_settings`), `settings_ui.py` (geometry helpers).

1. **Stop rebuilding labels on every settings tick.**
   Split `OverlayWindow.apply_settings` (`main.py:268`) into:
   - `apply_geometry(settings)` — just recomputes `compute_overlay_geometry` and calls `setGeometry`, plus re-styling existing labels (font/color/stroke) without deleting them.
   - `rebuild_labels(settings)` — the current label delete/recreate logic, only invoked when `num_history_lines`, `num_future_lines`, or `font_family` actually change from the previous value (track last-applied values on `self`).
   Do the same split for `TrackInfoWindow.apply_settings` (`main.py:580`) — it doesn't rebuild widgets today, but should skip re-styling work when only position offsets changed.
   Update the live-preview wiring in `show_settings` (`main.py:730`, the `live_update_proxy` closure) to call the cheap `apply_geometry` path, not full `apply_settings`, on every preview tick. Full `apply_settings`/`rebuild_labels` should still run on Save/Cancel (`apply_saved_settings`, `main.py:739`).

2. **De-duplicate track-info geometry math.**
   Extract the bounds/position math currently duplicated between `TrackInfoWindow.apply_settings` (`main.py:592-623`) and `SettingsDialog.update_position_bounds` (`settings_ui.py:769-816`) into one function in `settings_ui.py`, e.g. `compute_track_info_geometry(settings, screen_geom)`, mirroring the existing `compute_overlay_geometry` pattern (`settings_ui.py:72`). Both call sites consume the same dict of results (`x`, `y`, `width`, `height`, `min_x_offset`, `max_x_offset`, `min_y_offset`, `max_y_offset`).

3. **Named constants for system-message sentinels.**
   Replace the magic `-1`/`-2` index checks in `get_line_text` (`main.py:462-484`) with named constants (e.g. `NOW_PLAYING_INDEX = -1`, `LYRICS_LOADED_INDEX = -2`) used at both the check sites and wherever `current_lyric_index` is set to these values (`on_lyrics_found`, `main.py:391`).

4. **Tray menu reflects live state.**
   Make "Show/Hide Overlay" and "Show/Hide Track Info" (`main.py:711-719`) checkable `QAction`s (or dynamically-updated text) driven off `OverlayWindow.visibility_changed` and `TrackInfoWindow`'s visibility state, so the tray always shows current state without needing to alt-tab to check.

**Verification:** Run the app (`python main.py`), open Settings, and drag the position/size sliders — confirm no visible flicker/animation-reset on the overlay while dragging, and that Save/Cancel still correctly apply/revert. Toggle overlay/track-info visibility via tray and hotkeys and confirm the tray menu text/checkmark updates.

---

## Session 2 — Backend reliability: caching, polling, logging

**Why second:** independent of the rendering classes; mostly touches `SpotifyReader` and adds a logging setup used everywhere.

**Files:** `main.py` (`SpotifyReader`, `main_loop`, `main()`), `settings_ui.py` (`SettingsManager.load`/`save`).

1. **Use the existing `lyrics_cache`.**
   `SpotifyReader.lyrics_cache` (`main.py:58`) is declared but never read/written. In `fetch_lyrics` (`main.py:140`), key the cache by `track_id` (the same `f"{title} - {artist}"` id used for change detection): check the cache before calling `syncedlyrics.search`, and store the parsed result after a successful fetch (including `[]` for "no lyrics found" so repeated skip-back-and-forth doesn't re-query).

2. **Adaptive polling.**
   In `main_loop` (`main.py:668-672`), back off the poll interval (e.g. to ~1s) when there is no active media session or playback has been paused for a while, and return to the current 200ms cadence once a session becomes active/playing again. Keep this simple — a counter or last-active timestamp on `SpotifyReader`, no new threading.

3. **Replace silent `print()` diagnostics with `logging`.**
   Add a small `setup_logging()` in `main.py` (called once from `main()`) that configures the `logging` module to write to a rotating file in the same app-data directory used for settings (see Session 3, item 1 — coordinate the path so both use one `get_app_data_dir()` helper introduced here or in Session 3, whichever lands first; if this session lands first, add the helper here and have Session 3 reuse it). Replace the existing `print(...)` calls in exception handlers with `logging.exception(...)`/`logging.warning(...)`:
   - `SpotifyReader.poll_status` (`main.py:118`), `fetch_lyrics` (`main.py:154`), metadata read (`main.py:137`)
   - `register_hotkeys` (`main.py:661`)
   - `SettingsManager.load`/`save` (`settings_ui.py:143`, `150`)

**Verification:** Play/pause/skip tracks (or simulate via a script) and confirm lyrics for a previously-seen track load instantly from cache (add a temporary log line or breakpoint to confirm no second network call). Kill the media session and confirm CPU/log activity drops to the slower poll rate. Trigger a forced exception (e.g. temporarily rename `settings.json` to a directory) and confirm it's captured in the log file instead of vanishing.

---

## Session 3 — Settings dialog UX, persistence path, and repo/packaging hygiene

**Why third:** cosmetic/packaging cleanup that's independent of the runtime behavior changed in Sessions 1–2, safest to do last so it doesn't need to be redone if earlier sessions shift line numbers.

**Files:** `settings_ui.py` (`SettingsManager`, `SettingsDialog`), `main.py` (`main()` first-run check), `requirements.txt`, `.gitignore`/tracked files.

1. **Move `settings.json` out of the CWD.**
   Replace the relative `SETTINGS_FILE = "settings.json"` (`settings_ui.py:31`) with a path under `%APPDATA%\KaraokeBird\settings.json` (via `os.getenv("APPDATA")`, falling back to CWD if unset, e.g. non-Windows dev). Create the directory if missing. If Session 2's `get_app_data_dir()` helper already exists, reuse it here instead of duplicating the APPDATA lookup.

2. **Prevent duplicate hotkey assignment.**
   In `SettingsDialog.update_hotkey` (`settings_ui.py:648`), compare the new sequence against the *other* hotkey field's current value (`toggle_hotkey` vs `track_info_hotkey`) and show an inline warning label (or briefly styled red border) if they collide, without blocking entry — mirrors the non-blocking style already used elsewhere in the dialog (e.g. the `<small>` hint labels).

3. **Color-picker button contrast.**
   Add a subtle border to the color swatch buttons' stylesheets (`btn_color_high`, `btn_color_norm`, `btn_color_stroke`, `settings_ui.py:263-291`) so light/white colors remain visible against the dialog background.

4. **First-run guidance.**
   In `main()` (`main.py:776`), detect first run (no settings file existed before `SettingsManager()` construction) and either auto-open the Settings dialog or show a `QSystemTrayIcon.showMessage(...)` balloon pointing the user to the tray icon and default overlay position.

5. **Repo hygiene.**
   - `git rm -r --cached __pycache__` to stop tracking the two committed `.pyc` files (already covered by `.gitignore`, just need untracking).
   - Pin minimum versions in `requirements.txt` (currently unpinned: `PyQt6`, `winsdk`, `syncedlyrics`, `qasync`, `keyboard`) to whatever versions are currently installed/working (`pip freeze` for the four/five packages), using `>=` pins rather than exact `==` unless the user wants strict reproducibility.

**Verification:** Delete any local `settings.json`, confirm a fresh run creates it under `%APPDATA%\KaraokeBird\` and the first-run notice appears. Assign the same hotkey to both fields and confirm the warning shows. Run `git status` after the `rm --cached` to confirm the `.pyc` files show as deleted-from-index but the working files remain (still ignored). Run `pip install -r requirements.txt` in a clean venv to confirm pins resolve.
