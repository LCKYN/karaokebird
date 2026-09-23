import asyncio
import logging
import os
import sys
import time
import webbrowser
from logging.handlers import RotatingFileHandler

import keyboard
import qasync
import syncedlyrics
import winsdk.windows.media.control as wmc
from PyQt6.QtCore import (
    QLockFile,
    QObject,
    QPointF,
    QPropertyAnimation,
    QRectF,
    Qt,
    QTimer,
    pyqtSignal,
)
from PyQt6.QtGui import (
    QAction,
    QActionGroup,
    QColor,
    QFont,
    QIcon,
    QPainter,
    QPen,
    QPixmap,
)
from PyQt6.QtWidgets import (
    QApplication,
    QGraphicsDropShadowEffect,
    QMenu,
    QMessageBox,
    QPushButton,
    QSystemTrayIcon,
    QVBoxLayout,
    QWidget,
)

from lyrics import (
    PlaybackClock,
    attach_translations,
    clean_search_terms,
    lyrics_fit_duration,
    make_track_id,
    parse_lrc,
    parse_translations,
    word_progress,
)
from settings_ui import (
    LYRICS_CACHE_DIR,
    SETTINGS_FILE,
    TRACK_OFFSETS_FILE,
    SettingsDialog,
    SettingsManager,
    compute_overlay_geometry,
    compute_track_info_geometry,
    get_app_data_dir,
    overlay_offsets_from_pos,
    track_info_offsets_from_pos,
)
import updates
from storage import LyricsDiskCache, TrackOffsets
from ui_components import MarqueeLabel, StrokedLabel

APP_VERSION = "1.1.2.0"

# --- Backend Logic ---

# "Search again" cycles through these syncedlyrics providers.
PROVIDER_ROTATION = ("Lrclib", "Musixmatch", "NetEase", "Megalobiz")

SYNC_OFFSET_STEP_MS = 100
SYNC_OFFSET_LIMIT_MS = 10_000  # matches the settings dialog's range

PLAYING_STATUS = wmc.GlobalSystemMediaTransportControlsSessionPlaybackStatus.PLAYING

# Substrings of SMTC app ids (lower-case) -> names shown in the Source menu.
SOURCE_NAMES = (
    ("spotify", "Spotify"),
    ("chrome", "Chrome"),
    ("msedge", "Edge"),
    ("firefox", "Firefox"),
    ("308046b0af4a39cb", "Firefox"),  # Firefox's AppUserModelID
    ("applemusic", "Apple Music"),
)


def friendly_source_name(app_id):
    lowered = (app_id or "").lower()
    for key, name in SOURCE_NAMES:
        if key in lowered:
            return name
    return app_id or "Unknown"


def setup_logging():
    log_path = os.path.join(get_app_data_dir(), "karaokebird.log")
    handler = RotatingFileHandler(
        log_path, maxBytes=1_000_000, backupCount=3, encoding="utf-8"
    )
    handler.setFormatter(
        logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s")
    )
    root_logger = logging.getLogger()
    root_logger.setLevel(logging.INFO)
    root_logger.addHandler(handler)


class SpotifyReader(QObject):
    # Signals to update UI from async loop
    track_changed = pyqtSignal(str, str)  # title, artist
    # is_playing, current_ms, duration_ms, capture_time_ms
    playback_sync = pyqtSignal(bool, int, int, float)
    lyrics_found = pyqtSignal(list)  # list of {"time", "text", ...} dicts
    status_message = pyqtSignal(str)  # shown on the overlay
    notice = pyqtSignal(str)  # shown as a tray notification
    session_lost = pyqtSignal()  # the media player went away

    ACTIVE_POLL_INTERVAL = 0.2  # 5Hz while a session is actively playing
    IDLE_POLL_INTERVAL = 1.0  # back off when there's nothing to track
    IDLE_AFTER_SECONDS = 5  # how long paused/inactive before backing off
    SETUP_RETRY_SECONDS = 5  # retry connecting to SMTC this often

    def __init__(self, settings_manager, disk_cache):
        super().__init__()
        self.settings_manager = settings_manager
        self.disk_cache = disk_cache
        self.manager = None
        self.current_session = None
        self.current_track_id = None
        self.current_title = ""
        self.current_artist = ""
        self.current_duration = 0
        # cache key -> parsed lyrics ([] = not found); backed by disk_cache.
        self.lyrics_cache = {}
        # track_id -> index into PROVIDER_ROTATION last used by "Search again".
        self._rotation_index = {}
        # (enhanced, lang) the current track's lyrics were fetched with.
        self._current_options = None
        self.last_active_time = time.time()
        # Strong references to in-flight lyrics searches; asyncio only keeps
        # weak references to tasks, so an unreferenced one can be collected.
        self._tasks = set()
        # Last status emitted via _emit_status, so it isn't repeated on
        # every poll.
        self._last_status = None

    def get_poll_interval(self):
        if not self.current_session:
            return self.IDLE_POLL_INTERVAL
        if time.time() - self.last_active_time > self.IDLE_AFTER_SECONDS:
            return self.IDLE_POLL_INTERVAL
        return self.ACTIVE_POLL_INTERVAL

    def _emit_status(self, msg):
        """Emit a status message only if it differs from the last one."""
        if msg != self._last_status:
            self._last_status = msg
            self.status_message.emit(msg)

    async def setup(self):
        try:
            self.manager = await wmc.GlobalSystemMediaTransportControlsSessionManager.request_async()
            self._emit_status("Connected to Windows Media Controls")
        except Exception as e:
            logging.exception("Error connecting to Windows Media Controls")
            self.manager = None
            self._emit_status(f"Error connecting to Windows: {e}")

    async def poll_status(self):
        if not self.manager:
            return
        try:
            await self._poll_status()
        except Exception:
            logging.exception("Error polling media session")

    def _pick_session(self):
        """The session to follow: the preferred source when it's running,
        otherwise (Auto) the first playing session, else Windows' current
        one."""
        preferred = self.settings_manager.settings.get("preferred_source", "")
        sessions = list(self.manager.get_sessions())
        if preferred:
            for session in sessions:
                if session.source_app_user_model_id == preferred:
                    return session
        for session in sessions:
            info = session.get_playback_info()
            if info and info.playback_status == PLAYING_STATUS:
                return session
        return self.manager.get_current_session()

    def list_sources(self):
        """App ids of the media sessions currently published to Windows."""
        if not self.manager:
            return []
        try:
            return [s.source_app_user_model_id for s in self.manager.get_sessions()]
        except Exception:
            logging.exception("Error listing media sessions")
            return []

    @property
    def current_source(self):
        try:
            return self.current_session.source_app_user_model_id
        except Exception:
            return ""

    async def _poll_status(self):
        self.current_session = self._pick_session()
        if not self.current_session:
            if self.current_track_id is not None:
                # The player closed (or stopped publishing a session).
                self.current_track_id = None
                self.session_lost.emit()
            self._emit_status("Waiting for media...")
            return

        # A session exists again, so the waiting message may be shown the
        # next time it goes away.
        self._last_status = None

        # Get Timeline & Playback Status
        try:
            timeline = self.current_session.get_timeline_properties()
            playback_info = self.current_session.get_playback_info()
            # Capture timestamps as close to the call as possible
            capture_time = time.perf_counter() * 1000
            sys_now_ms = time.time() * 1000

            if timeline and playback_info:
                raw_position_ms = timeline.position.total_seconds() * 1000

                # Handle varying SMTC timestamp formats
                try:
                    last_update_dt = timeline.last_updated_time
                    if hasattr(last_update_dt, "timestamp"):
                        last_update_ms = last_update_dt.timestamp() * 1000
                    else:
                        last_update_ms = last_update_dt.to_datetime().timestamp() * 1000
                except (AttributeError, ValueError, Exception):
                    last_update_ms = sys_now_ms

                elapsed_since_update = sys_now_ms - last_update_ms

                # Determine playback state before applying the elapsed correction.
                is_playing = playback_info.playback_status == PLAYING_STATUS
                if is_playing:
                    self.last_active_time = sys_now_ms / 1000.0

                # Apply correction for the time passed since the media player last updated
                # its position. Only correct when actually playing — when paused, the raw
                # position is already the correct frozen value and adding elapsed time would
                # cause the lyrics to drift forward while the song is stopped.
                if is_playing and 0 <= elapsed_since_update < 10000:
                    position = raw_position_ms + elapsed_since_update
                else:
                    position = raw_position_ms

                duration = timeline.end_time.total_seconds() * 1000
                self.current_duration = int(duration)
                self.playback_sync.emit(
                    is_playing, int(position), int(duration), capture_time
                )
        except Exception:
            logging.exception("Error polling SMTC")

        # Get Metadata (Title/Artist)
        try:
            props = await self.current_session.try_get_media_properties_async()
            title = props.title
            artist = props.artist

            # Simple ID generation to detect change
            track_id = make_track_id(title, artist)

            if track_id != self.current_track_id and title:
                self.current_track_id = track_id
                self.current_title = title
                self.current_artist = artist
                self.track_changed.emit(title, artist)

                # Fetch lyrics in background
                self._start_task(self.fetch_lyrics(track_id, title, artist))

        except Exception:
            logging.exception("Error reading metadata")

    def _start_task(self, coro):
        task = asyncio.ensure_future(coro)
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

    # --- Lyrics ---

    def _search_options(self):
        """(enhanced, translation language) from the current settings."""
        settings = self.settings_manager.settings
        return (
            bool(settings.get("word_highlight", False)),
            settings.get("translation_lang", "") or "",
        )

    @staticmethod
    def cache_key(track_id, enhanced, lang):
        # Plain lookups key on the track id alone.
        key = track_id
        if enhanced:
            key += "\nenhanced"
        if lang:
            key += f"\nlang={lang}"
        return key

    def _cached(self, key):
        lyrics = self.lyrics_cache.get(key)
        if lyrics is None:
            lyrics = self.disk_cache.get(key)
            if lyrics is not None:
                self.lyrics_cache[key] = lyrics
        return lyrics

    def _store(self, key, lyrics):
        self.lyrics_cache[key] = lyrics
        self.disk_cache.put(key, lyrics)

    def clear_cache(self):
        self.lyrics_cache.clear()
        self.disk_cache.clear()
        logging.info("Lyrics cache cleared")

    def _emit_if_current(self, track_id, lyrics):
        # The track may have changed while we were searching; only the
        # current track's result may reach the overlay.
        if track_id != self.current_track_id:
            logging.info(f"Discarding stale lyrics for: {track_id}")
            return
        # An empty list makes the overlay show "No synced lyrics found".
        self.lyrics_found.emit(lyrics)

    @staticmethod
    def _search_term(title, artist):
        title, artist = clean_search_terms(title, artist)
        return " ".join(part for part in (title, artist) if part)

    async def _search(self, term, enhanced=False, providers=None):
        # syncedlyrics blocks on network I/O, so run it in a thread.
        kwargs = {"synced_only": True}
        if enhanced:
            kwargs["enhanced"] = True
        if providers:
            kwargs["providers"] = providers
        lrc_str = await asyncio.to_thread(syncedlyrics.search, term, **kwargs)
        return parse_lrc(lrc_str) if lrc_str else []

    async def _add_translations(self, term, lang, lyrics):
        # Only Musixmatch has translations.
        try:
            lrc_str = await asyncio.to_thread(
                syncedlyrics.search,
                term,
                synced_only=True,
                lang=lang,
                providers=["Musixmatch"],
            )
        except Exception:
            logging.exception("Translation fetch error")
            return
        matched = attach_translations(lyrics, parse_translations(lrc_str))
        logging.info(f"Matched {matched} translated lines ({lang}) for: {term}")

    def _fits_track(self, track_id, lyrics):
        """Reject lyrics that run well past the end of the song."""
        if track_id != self.current_track_id:
            return True  # duration unknown
        if lyrics_fit_duration(lyrics, self.current_duration):
            return True
        logging.warning(
            f"Rejecting lyrics for {track_id!r}: last line at "
            f"{lyrics[-1]['time']} ms, track is {self.current_duration} ms"
        )
        return False

    async def fetch_lyrics(self, track_id, title, artist):
        enhanced, lang = self._search_options()
        if track_id == self.current_track_id:
            self._current_options = (enhanced, lang)
        key = self.cache_key(track_id, enhanced, lang)
        cached = self._cached(key)
        if cached is not None:
            self._emit_if_current(track_id, cached)
            return

        search_term = self._search_term(title, artist)
        logging.info(f"Searching lyrics for: {search_term}")
        self.status_message.emit("Searching lyrics…")

        try:
            parsed = await self._search(search_term, enhanced=enhanced)
            if parsed and not self._fits_track(track_id, parsed):
                parsed = []
            logging.info(f"Lyrics search finished for: {search_term} ({len(parsed)} lines)")

            if parsed and lang:
                # Show the lyrics now; the translations follow.
                self._emit_if_current(track_id, parsed)
                await self._add_translations(search_term, lang, parsed)

            self._store(key, parsed)
            self._emit_if_current(track_id, parsed)
        except Exception:
            logging.exception("Lyrics fetch error")
            if track_id == self.current_track_id:
                self.lyrics_found.emit([])

    def refresh_if_options_changed(self):
        """Re-fetch the current song's lyrics when the word-highlight or
        translation settings changed."""
        if not self.current_track_id:
            return
        if self._search_options() != self._current_options:
            self._start_task(
                self.fetch_lyrics(
                    self.current_track_id, self.current_title, self.current_artist
                )
            )

    def request_search_again(self):
        if not self.current_track_id:
            self.notice.emit("Nothing is playing.")
            return
        self._start_task(self.search_again())

    async def search_again(self):
        """Handle "Wrong lyrics?": try the next provider in PROVIDER_ROTATION
        whose result differs from what's shown, and replace the cache entry."""
        track_id = self.current_track_id
        title, artist = self.current_title, self.current_artist
        enhanced, lang = self._search_options()
        key = self.cache_key(track_id, enhanced, lang)
        current_texts = [entry["text"] for entry in self._cached(key) or []]
        search_term = self._search_term(title, artist)

        start = self._rotation_index.get(track_id, -1)
        for step in range(1, len(PROVIDER_ROTATION) + 1):
            index = (start + step) % len(PROVIDER_ROTATION)
            provider = PROVIDER_ROTATION[index]
            logging.info(f"Searching again on {provider} for: {search_term}")
            try:
                lyrics = await self._search(
                    search_term, enhanced=enhanced, providers=[provider]
                )
            except Exception:
                logging.exception(f"Lyrics fetch error on {provider}")
                continue
            if track_id != self.current_track_id:
                return  # the song changed meanwhile
            if not lyrics or [e["text"] for e in lyrics] == current_texts:
                continue
            if not self._fits_track(track_id, lyrics):
                continue

            self._rotation_index[track_id] = index
            if lang:
                await self._add_translations(search_term, lang, lyrics)
            self._store(key, lyrics)
            self.notice.emit(f"Now using lyrics from {provider}.")
            self._emit_if_current(track_id, lyrics)
            return

        self.notice.emit("No other synced lyrics found for this song.")


# --- Frontend GUI ---


# Sentinel indices used in place of a real lyrics_data index to request a
# system message on the current-line label (see OverlayWindow.get_line_text).
NOW_PLAYING_INDEX = -1
# Guaranteed not to match any real or system-message index, used to force
# update_frame() to recompute and redisplay the active line.
FORCE_REFRESH_INDEX = -999


BACKGROUND_PADDING = 12
BACKGROUND_RADIUS = 10


def draw_background(painter, text_rect, color):
    """Paint the readability background: a rounded rect around the text."""
    rect = text_rect.adjusted(
        -BACKGROUND_PADDING, -BACKGROUND_PADDING, BACKGROUND_PADDING, BACKGROUND_PADDING
    )
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    painter.setPen(Qt.PenStyle.NoPen)
    painter.setBrush(QColor(color))
    painter.drawRoundedRect(rect, BACKGROUND_RADIUS, BACKGROUND_RADIUS)


class EditableWindowMixin:
    """Drag-to-position edit mode shared by the overlay and track-info
    windows. Subclasses must define ``edit_finished`` and ``drag_finished``
    signals and call ``_init_edit_mode`` and ``_paint_edit_decorations``."""

    EDIT_HINT = "Drag to move · Enter/Esc to finish"
    EDIT_FILL = QColor(0, 0, 0, 90)  # also makes the whole window grabbable
    EDIT_BORDER = QColor(255, 255, 255, 200)

    def _init_edit_mode(self):
        self.edit_mode = False
        self._drag_offset = None
        self.done_button = QPushButton("Done", self)
        self.done_button.hide()
        self.done_button.clicked.connect(self.edit_finished.emit)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)

    def set_edit_mode(self, enabled):
        """Toggle click-through. Changing window flags hides the window, so
        the caller must show() it again as needed."""
        self.edit_mode = enabled
        self._drag_offset = None
        self.setWindowFlag(Qt.WindowType.WindowTransparentForInput, not enabled)
        self.done_button.setVisible(enabled)
        self._place_done_button()
        self.update()

    def _place_done_button(self):
        margin = 8
        self.done_button.adjustSize()
        self.done_button.move(
            self.width() - self.done_button.width() - margin,
            (self.height() - self.done_button.height()) // 2,
        )

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._place_done_button()

    def mousePressEvent(self, event):
        if self.edit_mode and event.button() == Qt.MouseButton.LeftButton:
            self._drag_offset = event.globalPosition().toPoint() - self.pos()
            event.accept()
            return
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event):
        if self.edit_mode and self._drag_offset is not None:
            self.move(event.globalPosition().toPoint() - self._drag_offset)
            event.accept()
            return
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event):
        if self.edit_mode and self._drag_offset is not None:
            self._drag_offset = None
            event.accept()
            self.drag_finished.emit()
            return
        super().mouseReleaseEvent(event)

    def keyPressEvent(self, event):
        if self.edit_mode and event.key() in (
            Qt.Key.Key_Return,
            Qt.Key.Key_Enter,
            Qt.Key.Key_Escape,
        ):
            self.edit_finished.emit()
            return
        super().keyPressEvent(event)

    def _paint_edit_decorations(self, painter):
        rect = QRectF(self.rect()).adjusted(1, 1, -1, -1)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        pen = QPen(self.EDIT_BORDER, 2, Qt.PenStyle.DashLine)
        painter.setPen(pen)
        painter.setBrush(self.EDIT_FILL)
        painter.drawRoundedRect(rect, BACKGROUND_RADIUS, BACKGROUND_RADIUS)

        font = QFont(self.font())
        font.setPointSize(10)
        painter.setFont(font)
        painter.setPen(self.EDIT_BORDER)
        painter.drawText(
            rect.adjusted(12, 6, -12, -6),
            Qt.AlignmentFlag.AlignTop | Qt.AlignmentFlag.AlignLeft,
            self.EDIT_HINT,
        )


def get_target_screen(settings):
    screens = QApplication.screens()
    screen_index = settings.get("screen_index", 0)
    if 0 <= screen_index < len(screens):
        return screens[screen_index]
    return QApplication.primaryScreen()


class OverlayWindow(EditableWindowMixin, QWidget):
    visibility_toggled = pyqtSignal()
    visibility_changed = pyqtSignal(bool)
    edit_finished = pyqtSignal()
    drag_finished = pyqtSignal()

    # Shown in edit mode when no lyrics are loaded, so there's something to
    # position: (previous, current, upcoming).
    SAMPLE_LINES = ("Previous lyric line", "Current lyric line", "Upcoming lyric line")

    def __init__(self, settings_manager):
        super().__init__()
        self.visibility_toggled.connect(self.toggle_visibility)
        self.settings_manager = settings_manager
        self.settings = self.settings_manager.settings

        # Window Setup
        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
            | Qt.WindowType.Tool  # Does not appear in taskbar
            | Qt.WindowType.WindowTransparentForInput  # Click-through!
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)

        # Layout container
        self.main_layout = QVBoxLayout()
        self.main_layout.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.setLayout(self.main_layout)

        # Widget placeholders
        self.prev_labels = []
        self.next_labels = []
        self.curr_label = StrokedLabel("Waiting for music...")
        self.curr_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.curr_label.setWordWrap(False)

        # Shadow effect (curr_label is never recreated, so this is set once)
        shadow = QGraphicsDropShadowEffect()
        shadow.setBlurRadius(10)
        shadow.setColor(QColor(0, 0, 0, 200))
        shadow.setOffset(2, 2)
        self.curr_label.setGraphicsEffect(shadow)
        self.curr_label.displayedTextChanged.connect(self._on_label_text_changed)

        # Translation of the current line, shown under it when enabled.
        self.translation_label = StrokedLabel("")
        self.translation_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.translation_label.displayedTextChanged.connect(
            self._on_label_text_changed
        )

        # Tracks the settings that last triggered a label rebuild, so
        # apply_settings only rebuilds when one of them actually changed.
        self._last_num_history = None
        self._last_num_future = None
        self._last_font_family = None

        # Internal State
        self.lyrics_data = []
        self.current_lyric_index = NOW_PLAYING_INDEX
        self.current_title = ""
        self.current_artist = ""

        # Playback state for interpolation
        self.is_playing = False
        self.clock = PlaybackClock()
        self.duration = 0
        # Per-song sync offset, added to the global sync_offset_ms.
        self.track_offset_ms = 0
        self.system_message_time = 0

        # High-frequency timer for smooth updates
        self.timer = QTimer(self)
        self.timer.setInterval(50)  # Update every 50ms (20fps)
        self.timer.timeout.connect(self.update_frame)
        # Started in showEvent, stopped in hideEvent.

        self._init_edit_mode()

        # Apply initial settings
        self.apply_settings()

    def set_edit_mode(self, enabled):
        super().set_edit_mode(enabled)
        if self.lyrics_data:
            return
        if enabled:
            prev_text, curr_text, next_text = self.SAMPLE_LINES
            self.system_message_time = 0  # don't let the sample fade out
            self.curr_label.setText(curr_text)
            for lbl in self.prev_labels:
                lbl.setText(prev_text)
            for lbl in self.next_labels:
                lbl.setText(next_text)
        else:
            for lbl in self._all_labels():
                lbl.setText("")

    def toggle_visibility(self):
        if self.isVisible():
            self.hide()
        else:
            self.show()
        self.visibility_changed.emit(self.isVisible())
        self.settings_manager.set("overlay_visible", self.isVisible())
        self.settings_manager.save()

    def _all_labels(self):
        return (
            self.prev_labels
            + [self.curr_label, self.translation_label]
            + self.next_labels
        )

    def _visible_labels(self):
        return [
            lbl
            for lbl in self._all_labels()
            if lbl.isVisible() and lbl.text()
        ]

    def _on_label_text_changed(self):
        # The readability background hugs the text, so it must be redrawn
        # whenever a line's text changes.
        if self.settings.get("background_enabled", False):
            self.update()

    def paintEvent(self, event):
        painter = QPainter(self)
        if self.edit_mode:
            self._paint_edit_decorations(painter)
        if not self.settings.get("background_enabled", False):
            return
        rect = QRectF()
        for lbl in self._visible_labels():
            text_rect = lbl.textRect().translated(QPointF(lbl.pos()))
            rect = text_rect if rect.isNull() else rect.united(text_rect)
        if rect.isNull():
            return
        draw_background(painter, rect, self.settings["background_color"])

    def showEvent(self, event):
        super().showEvent(event)
        if not self.timer.isActive():
            self.timer.start()
            self.update_frame()

    def hideEvent(self, event):
        # Nothing to draw while hidden; stop the 20fps timer.
        self.timer.stop()
        super().hideEvent(event)

    def apply_settings(self, settings_override=None):
        """Full settings application: rebuilds labels if needed, then
        applies geometry/position and re-styles them. Use this on Save/Cancel;
        use apply_geometry directly for cheap live-preview ticks."""
        if settings_override:
            self.settings = settings_override
        else:
            self.settings = self.settings_manager.settings

        num_history = self.settings.get("num_history_lines", 1)
        num_future = self.settings.get("num_future_lines", 1)
        font_family = self.settings["font_family"]

        if (
            num_history != self._last_num_history
            or num_future != self._last_num_future
            or font_family != self._last_font_family
        ):
            self.rebuild_labels(self.settings)

        self.apply_geometry(self.settings)

    def rebuild_labels(self, settings):
        """Delete/recreate the previous/next context labels. Only needed
        when the line counts or font family change — expensive because it
        tears down and rebuilds widgets, unlike apply_geometry."""
        # Clear existing widgets from layout
        while self.main_layout.count():
            child = self.main_layout.takeAt(0)
            if child.widget():
                if child.widget() in (self.curr_label, self.translation_label):
                    child.widget().setParent(None)  # Detach, don't delete
                else:
                    child.widget().deleteLater()

        self.prev_labels = []
        self.next_labels = []

        num_history = settings.get("num_history_lines", 1)
        num_future = settings.get("num_future_lines", 1)

        for _ in range(num_history):
            l = StrokedLabel("")
            l.setAlignment(Qt.AlignmentFlag.AlignCenter)
            l.displayedTextChanged.connect(self._on_label_text_changed)
            self.prev_labels.append(l)
            self.main_layout.addWidget(l)

        self.main_layout.addWidget(self.curr_label)
        self.main_layout.addWidget(self.translation_label)

        for _ in range(num_future):
            l = StrokedLabel("")
            l.setAlignment(Qt.AlignmentFlag.AlignCenter)
            l.displayedTextChanged.connect(self._on_label_text_changed)
            self.next_labels.append(l)
            self.main_layout.addWidget(l)

        self._last_num_history = num_history
        self._last_num_future = num_future
        self._last_font_family = settings["font_family"]

    def apply_geometry(self, settings):
        """Cheap path: recompute geometry and re-style existing labels
        (font/color/stroke) without deleting/recreating them. Safe to call
        on every live-preview tick."""
        self.settings = settings
        screen = get_target_screen(settings)
        screen_geom = screen.geometry()
        overlay_geom = compute_overlay_geometry(settings, screen_geom)
        self.setGeometry(
            overlay_geom["x"],
            overlay_geom["y"],
            overlay_geom["width"],
            overlay_geom["height"],
        )

        font_family = settings["font_family"]

        for l in self.prev_labels + self.next_labels:
            l.setFont(QFont(font_family, settings["font_size_normal"]))
            l.setStyleSheet(f"color: {settings['normal_color']};")
            l.setStrokeColor(settings.get("stroke_color", "#000000"))
            l.setStrokeEnabled(settings.get("stroke_enabled_context", True))
            l.enable_animation = settings.get("enable_animations", True)
            l.animation_type = settings.get("animation_type", "fade")

        self.curr_label.setFont(
            QFont(font_family, settings["font_size_highlight"], QFont.Weight.Bold)
        )
        self.curr_label.setStyleSheet(f"color: {settings['highlight_color']};")
        self.curr_label.setStrokeColor(settings.get("stroke_color", "#000000"))
        self.curr_label.setStrokeEnabled(
            settings.get("stroke_enabled_highlight", True)
        )
        self.curr_label.enable_animation = settings.get("enable_animations", True)
        self.curr_label.animation_type = settings.get("animation_type", "fade")
        # Word-by-word mode draws the unsung part in the context color.
        self.curr_label.setBaseColor(settings["normal_color"])
        if not settings.get("word_highlight", False):
            self.curr_label.setProgress(None)

        translation_font = QFont(font_family, settings["font_size_normal"])
        translation_font.setItalic(True)
        self.translation_label.setFont(translation_font)
        self.translation_label.setStyleSheet(f"color: {settings['normal_color']};")
        self.translation_label.setStrokeColor(settings.get("stroke_color", "#000000"))
        self.translation_label.setStrokeEnabled(
            settings.get("stroke_enabled_context", True)
        )
        self.translation_label.enable_animation = settings.get("enable_animations", True)
        self.translation_label.animation_type = settings.get("animation_type", "fade")
        self.translation_label.setVisible(bool(settings.get("translation_lang")))

        # Refill the context labels (they may have just been rebuilt) when a
        # lyric line is showing; system messages on the current label are
        # left alone.
        if self.current_lyric_index >= 0:
            self.update_display(self.current_lyric_index)
        self.update()

    def set_track_info(self, title, artist):
        self.current_title = title
        self.current_artist = artist
        # Reset lyrics and show title immediately
        self.lyrics_data = []
        self.current_lyric_index = NOW_PLAYING_INDEX
        self.update_display(NOW_PLAYING_INDEX)

    def update_status(self, msg):
        # Status messages are shown on the lyric lines, only while no lyrics
        # are loaded (and not over the edit-mode sample text).
        if self.lyrics_data or self.edit_mode:
            return
        self.system_message_time = time.time()
        if (
            self.current_lyric_index == NOW_PLAYING_INDEX
            and self.curr_label.text()
            and self.next_labels
        ):
            # Keep "Now Playing" on the current line; show the status below.
            self.next_labels[0].setText(msg)
            return
        self.curr_label.setText(msg)
        # Clear context lines when showing status
        for lbl in self.prev_labels:
            lbl.setText("")
        for lbl in self.next_labels:
            lbl.setText("")

    def _clear_system_message(self):
        # Fade out system messages after 5 seconds
        if time.time() - self.system_message_time > 5:
            for lbl in self._all_labels():
                if lbl.text():
                    lbl.setText("")

    def on_lyrics_found(self, data):
        self.lyrics_data = data
        if not data:
            self.system_message_time = time.time()
            self.curr_label.setProgress(None)
            for lbl in self._all_labels():
                lbl.setText("")
            self.curr_label.setText("No synced lyrics found")
        else:
            # Let update_frame determine the correct starting point
            # based on current playback position. We force an update by
            # setting current index to an impossible value.
            self.current_lyric_index = FORCE_REFRESH_INDEX
            self.update_frame()

    def on_session_lost(self):
        """The media player closed: stop advancing and clear everything."""
        self.is_playing = False
        self.clock.reset()
        self.lyrics_data = []
        self.current_title = ""
        self.current_artist = ""
        self.current_lyric_index = NOW_PLAYING_INDEX
        for lbl in self._all_labels():
            lbl.setText("")

    def on_playback_sync(self, is_playing, position, duration, capture_time):
        self.clock.sync(is_playing, position, capture_time)
        self.is_playing = is_playing
        self.duration = duration
        self.update_frame()

    def update_frame(self):
        if not self.lyrics_data:
            if self.system_message_time > 0:
                self._clear_system_message()
            return

        # Calculate interpolated time
        current_time = self.clock.now(time.perf_counter() * 1000)

        # Apply sync offsets (global + this song's)
        current_time += self.settings.get("sync_offset_ms", 0) + self.track_offset_ms

        # Find current line
        # We look for the last line that has a start time <= current_time
        active_index = NOW_PLAYING_INDEX
        for i, line in enumerate(self.lyrics_data):
            if line["time"] <= current_time:
                active_index = i
            else:
                break

        if active_index != self.current_lyric_index:
            self.current_lyric_index = active_index
            self.update_display(active_index)
        elif active_index < 0 and self.system_message_time > 0:
            self._clear_system_message()

        self._update_word_progress(active_index, current_time)

    def _update_word_progress(self, index, current_time):
        words = None
        if self.settings.get("word_highlight", False) and 0 <= index < len(
            self.lyrics_data
        ):
            words = self.lyrics_data[index].get("words")
        if not words:
            # No word timing: highlight the whole line.
            self.curr_label.setProgress(None)
            return
        next_index = index + 1
        line_end = (
            self.lyrics_data[next_index]["time"]
            if next_index < len(self.lyrics_data)
            else None
        )
        self.curr_label.setProgress(word_progress(words, current_time, line_end))

    def get_line_text(self, index, is_context=False):
        """
        is_context=True means this is for a previous/next label.
        """
        if 0 <= index < len(self.lyrics_data):
            return self.lyrics_data[index]["text"]

        # If it's a context label (prev/next), don't show system messages
        if is_context:
            return ""

        # Main label (index < 0) system messages
        if index == NOW_PLAYING_INDEX:
            if self.settings.get("track_info_enabled", False):
                return ""
            if not self.current_title:
                return ""
            if self.current_artist:
                return f"Now Playing: {self.current_title} — {self.current_artist}"
            return f"Now Playing: {self.current_title}"
        return ""

    def update_display(self, index):
        # index is the index of the CURRENT line in self.lyrics_data
        if index < 0:
            self.system_message_time = time.time()
        else:
            self.system_message_time = 0

        # 1. Update Current (and its translation)
        self.curr_label.setText(self.get_line_text(index, is_context=False))
        translation = ""
        if 0 <= index < len(self.lyrics_data):
            translation = self.lyrics_data[index].get("translation", "")
        self.translation_label.setText(translation)

        # Context labels (previous/next) should be hidden if showing a system message
        # UNLESS that system message is the "..." gap marker (which has index >= 0)
        show_context = index >= 0

        # 2. Update Previous Labels
        num_prev = len(self.prev_labels)
        for i, lbl in enumerate(self.prev_labels):
            offset = i - num_prev
            target_idx = index + offset
            text = (
                self.get_line_text(target_idx, is_context=True) if show_context else ""
            )
            lbl.setText(text)

        # 3. Update Next Labels
        for i, lbl in enumerate(self.next_labels):
            offset = i + 1
            target_idx = index + offset
            text = (
                self.get_line_text(target_idx, is_context=True) if show_context else ""
            )
            lbl.setText(text)


class TrackInfoWindow(EditableWindowMixin, QWidget):
    visibility_toggled = pyqtSignal()
    visibility_changed = pyqtSignal(bool)
    edit_finished = pyqtSignal()
    drag_finished = pyqtSignal()

    SAMPLE_TEXT = "Song Title — Artist"

    def __init__(self, settings_manager):
        super().__init__()
        self.visibility_toggled.connect(self.toggle_visibility)
        self.settings_manager = settings_manager
        self.settings = self.settings_manager.settings
        self.current_title = ""
        self.current_artist = ""
        # Manual show/hide state, independent of the lyric overlay's own
        # visibility. Toggled via its own hotkey/tray action and remembered
        # across restarts.
        self.manually_hidden = self.settings.get("track_info_manually_hidden", False)

        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
            | Qt.WindowType.Tool
            | Qt.WindowType.WindowTransparentForInput
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)

        self.label = MarqueeLabel("")
        self.label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.label.setWordWrap(False)
        # Track information must remain in a fixed position.  MarqueeLabel only
        # moves overflow text left when scrolling is enabled, which made long
        # titles visibly shift while short titles stayed centered.
        self.label.setScrollEnabled(False)

        layout = QVBoxLayout()
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(self.label)
        self.setLayout(layout)

        self._init_edit_mode()

        self.apply_settings()

    def set_edit_mode(self, enabled):
        super().set_edit_mode(enabled)
        self.update_text()
        self.update_visibility()

    def set_track_info(self, title, artist):
        self.current_title = title
        self.current_artist = artist
        self.update_text()

    def update_text(self):
        if self.current_title and self.current_artist:
            text = f"{self.current_title} — {self.current_artist}"
        else:
            text = self.current_title or ""
        if not text and self.edit_mode:
            text = self.SAMPLE_TEXT
        self.label.setText(text)
        self.update()  # the background hugs the text

    def paintEvent(self, event):
        painter = QPainter(self)
        if self.edit_mode:
            self._paint_edit_decorations(painter)
        if not self.settings.get("track_info_background_enabled", False):
            return
        if not self.label.text():
            return
        rect = self.label.textRect().translated(QPointF(self.label.pos()))
        draw_background(painter, rect, self.settings["background_color"])

    def toggle_visibility(self):
        self.manually_hidden = not self.manually_hidden
        self.settings_manager.set("track_info_manually_hidden", self.manually_hidden)
        self.settings_manager.save()
        self.update_visibility()

    def update_visibility(self):
        # In edit mode it's shown even if manually hidden, so it can be placed.
        if self.settings.get("track_info_enabled", False) and (
            self.edit_mode or not self.manually_hidden
        ):
            self.show()
        else:
            self.hide()
        self.visibility_changed.emit(self.isVisible())

    def apply_settings(self, settings_override=None):
        """Full settings application: re-styles the label (font/color/
        stroke) and applies geometry. Use this on Save/Cancel; use
        apply_geometry directly for cheap live-preview ticks where only
        position offsets may have changed."""
        if settings_override:
            self.settings = settings_override
        else:
            self.settings = self.settings_manager.settings

        font_family = self.settings.get("font_family", "Century Gothic")
        font_size = self.settings.get("font_size_normal", 14)
        self.label.setFont(QFont(font_family, font_size))
        self.label.setStyleSheet(f"color: {self.settings['normal_color']};")
        self.label.setStrokeColor(self.settings.get("stroke_color", "#000000"))
        self.label.setStrokeEnabled(self.settings.get("stroke_enabled_context", True))

        self.apply_geometry(self.settings)

    def apply_geometry(self, settings):
        self.settings = settings
        screen = get_target_screen(settings)
        screen_geom = screen.geometry()
        geom = compute_track_info_geometry(settings, screen_geom)

        self.setGeometry(geom["x"], geom["y"], geom["width"], geom["height"])
        self.update_text()
        self.update_visibility()


class TrackOffsetController(QObject):
    """Per-song sync offsets: tracks the current song, applies its offset to
    the overlay and persists changes (debounced for hotkey nudges)."""

    # Emitted from the keyboard hook thread; delivered on the GUI thread.
    nudge_requested = pyqtSignal(int)

    SAVE_DELAY_MS = 1000

    def __init__(self, store, window):
        super().__init__()
        self.store = store
        self.window = window
        self.track_id = None
        self._save_timer = QTimer(self)
        self._save_timer.setSingleShot(True)
        self._save_timer.setInterval(self.SAVE_DELAY_MS)
        self._save_timer.timeout.connect(self.store.save)
        self.nudge_requested.connect(self.adjust)

    def set_track(self, title, artist):
        self.track_id = make_track_id(title, artist) if title else None
        self._apply()

    def clear_track(self):
        self.track_id = None
        self._apply()

    def current(self):
        return self.store.get(self.track_id) if self.track_id else 0

    def adjust(self, delta_ms, save_now=False):
        """Nudge this song's offset; hotkey nudges save after a pause."""
        if not self.track_id:
            return
        value = max(
            -SYNC_OFFSET_LIMIT_MS, min(SYNC_OFFSET_LIMIT_MS, self.current() + delta_ms)
        )
        self._set(value, save_now)

    def reset(self):
        if self.track_id:
            self._set(0, save_now=True)

    def _set(self, value, save_now):
        self.store.set(self.track_id, value)
        self._apply()
        logging.info(f"Song offset for {self.track_id!r} set to {value} ms")
        if save_now:
            self.flush()
        else:
            self._save_timer.start()

    def flush(self):
        self._save_timer.stop()
        self.store.save()

    def _apply(self):
        self.window.track_offset_ms = self.current()


class IdleFader(QObject):
    """Fades the overlay windows out when nothing has played for a while.

    Uses windowOpacity rather than hide(), so the manual show/hide toggles
    (and their tray checkmarks) stay independent of it.
    """

    IDLE_MS = 5000
    FADE_MS = 400

    def __init__(self, windows, settings_manager):
        super().__init__()
        self.windows = windows
        self.settings_manager = settings_manager
        self._playing = False
        self._faded = False
        self._target = 1.0
        # While suspended (e.g. edit mode) the windows stay fully opaque.
        self._suspended = False
        self._anims = []

        self._idle_timer = QTimer(self)
        self._idle_timer.setSingleShot(True)
        self._idle_timer.setInterval(self.IDLE_MS)
        self._idle_timer.timeout.connect(self._on_idle)
        # Nothing is playing yet at startup.
        self._idle_timer.start()

    def _enabled(self):
        return self.settings_manager.settings.get("auto_hide_idle", True)

    def _start_idle_countdown(self):
        if not self._faded and not self._idle_timer.isActive():
            self._idle_timer.start()

    def on_playback_sync(self, is_playing, *_):
        self._playing = is_playing
        if is_playing:
            self._idle_timer.stop()
            self._fade_to(1.0)
        else:
            self._start_idle_countdown()

    def on_session_lost(self):
        self._playing = False
        self._start_idle_countdown()

    def apply_settings(self):
        if not self._enabled():
            self._idle_timer.stop()
            self._fade_to(1.0)
        elif not self._playing:
            self._start_idle_countdown()

    def set_suspended(self, suspended):
        self._suspended = suspended
        if suspended:
            self._idle_timer.stop()
            self._fade_to(1.0, animate=False)
        elif not self._playing:
            self._start_idle_countdown()

    def _on_idle(self):
        if self._enabled() and not self._suspended and not self._playing:
            self._fade_to(0.0)

    def _fade_to(self, opacity, animate=True):
        # Called on every poll while playing; don't restart a fade that's
        # already heading to the same place.
        if animate and opacity == self._target:
            return
        self._target = opacity
        self._faded = opacity == 0.0
        for anim in self._anims:
            anim.stop()
        self._anims = []
        for window in self.windows:
            if not animate:
                window.setWindowOpacity(opacity)
                continue
            if window.windowOpacity() == opacity:
                continue
            anim = QPropertyAnimation(window, b"windowOpacity", self)
            anim.setDuration(self.FADE_MS)
            anim.setStartValue(window.windowOpacity())
            anim.setEndValue(opacity)
            anim.start()
            self._anims.append(anim)


# Qt PortableText key names that the keyboard library doesn't accept (or
# maps to a different key), keyed by lower-case Qt name.
_QT_TO_KEYBOARD_KEYS = {
    "meta": "windows",
    "return": "enter",
    "del": "delete",
    "ins": "insert",
    "esc": "esc",
    "pgup": "page up",
    "pgdown": "page down",
    "capslock": "caps lock",
    "numlock": "num lock",
    "scrolllock": "scroll lock",
    "print": "print screen",
    "+": "plus",
}


def _normalize_hotkey(hotkey):
    """Map a Qt PortableText key sequence (e.g. "Ctrl+Shift+PgUp") to the
    keyboard library's syntax (e.g. "ctrl+shift+page up")."""
    # "+" separates keys, so a literal plus key shows up as an empty token
    # ("Ctrl++" -> ["Ctrl", "", ""]).
    tokens = hotkey.split("+")
    keys = []
    i = 0
    while i < len(tokens):
        token = tokens[i]
        if token == "":
            keys.append("+")
            i += 2  # skip the empty token after the literal "+"
            continue
        keys.append(token)
        i += 1
    return "+".join(_QT_TO_KEYBOARD_KEYS.get(k.lower(), k.lower()) for k in keys)


def register_hotkeys(window, track_info_window=None, offsets=None):
    """(Re)binds all global hotkeys in one place and returns the list of
    hotkeys that failed to register.

    keyboard.unhook_all() clears every previously registered hotkey, so every
    hotkey must be (re)registered together whenever any one changes. Each is
    registered separately so one bad hotkey doesn't disable the others.
    Callbacks run on the keyboard hook thread, so they only emit signals.
    """
    try:
        keyboard.unhook_all()
    except Exception:
        logging.exception("Error clearing hotkeys")

    settings = window.settings
    bindings = [(settings.get("toggle_hotkey", ""), window.visibility_toggled.emit)]
    if track_info_window:
        bindings.append(
            (
                track_info_window.settings.get("track_info_hotkey", ""),
                track_info_window.visibility_toggled.emit,
            )
        )
    if offsets:
        bindings += [
            (
                settings.get("offset_back_hotkey", ""),
                lambda: offsets.nudge_requested.emit(-SYNC_OFFSET_STEP_MS),
            ),
            (
                settings.get("offset_fwd_hotkey", ""),
                lambda: offsets.nudge_requested.emit(SYNC_OFFSET_STEP_MS),
            ),
        ]

    failed = []
    for hotkey, callback in bindings:
        if not hotkey:
            continue
        try:
            keyboard.add_hotkey(_normalize_hotkey(hotkey), callback)
        except Exception:
            logging.exception(f"Error registering hotkey {hotkey!r}")
            failed.append(hotkey)
    return failed


def warn_failed_hotkeys(tray, failed):
    if failed:
        tray.showMessage(
            "Some hotkeys couldn't be registered",
            "Not working: " + ", ".join(failed),
            QSystemTrayIcon.MessageIcon.Warning,
            8000,
        )


# --- Main Entry ---


async def main_loop(reader):
    while True:
        try:
            if reader.manager is None:
                await reader.setup()
                if reader.manager is None:
                    await asyncio.sleep(reader.SETUP_RETRY_SECONDS)
                    continue
            await reader.poll_status()
        except Exception:
            # Never let the polling task exit; log and keep going.
            logging.exception("Error in main loop")
        await asyncio.sleep(reader.get_poll_interval())


def resource_path(relative_path):
    """Get absolute path to resource, works for dev and for PyInstaller"""
    try:
        # PyInstaller creates a temp folder and stores path in _MEIPASS
        base_path = sys._MEIPASS
    except Exception:
        base_path = os.path.dirname(os.path.abspath(__file__))

    return os.path.join(base_path, relative_path)


def watch_screens(app, windows):
    """Re-apply window geometry when displays are added, removed or
    resized. get_target_screen falls back to the primary screen when the
    saved screen_index no longer exists."""

    def reapply():
        for window in windows:
            window.apply_geometry(window.settings)

    def reapply_later(*_):
        # Deferred so QApplication.screens() reflects the change.
        QTimer.singleShot(0, reapply)

    def watch(screen):
        screen.geometryChanged.connect(reapply_later)

    for screen in app.screens():
        watch(screen)
    app.screenAdded.connect(lambda screen: (watch(screen), reapply_later()))
    app.screenRemoved.connect(reapply_later)


class UpdateChecker(QObject):
    """At most once a day (when enabled), asks GitHub for the latest release
    and tells the tray if it's newer than APP_VERSION."""

    RECHECK_MS = 3600 * 1000  # re-evaluate hourly; is_check_due limits it
    STARTUP_DELAY_MS = 10_000

    def __init__(self, settings_manager, tray):
        super().__init__()
        self.settings_manager = settings_manager
        self.tray = tray
        self._task = None
        self._timer = QTimer(self)
        self._timer.setInterval(self.RECHECK_MS)
        self._timer.timeout.connect(self.start_check)
        self._timer.start()
        QTimer.singleShot(self.STARTUP_DELAY_MS, self.start_check)

    def start_check(self):
        if self._task is None or self._task.done():
            self._task = asyncio.ensure_future(self.check())

    async def check(self):
        settings = self.settings_manager.settings
        if not settings.get("update_check_enabled", True):
            return
        now = time.time()
        if not updates.is_check_due(settings.get("last_update_check", 0.0), now):
            return
        # Count the attempt even if it fails, so we never retry in a loop.
        self.settings_manager.set("last_update_check", now)
        self.settings_manager.save()
        try:
            tag, url = await asyncio.to_thread(updates.fetch_latest_release)
        except Exception as e:
            logging.warning(f"Update check failed: {e}")
            return
        logging.info(f"Latest release: {tag} (running {APP_VERSION})")
        if updates.is_newer(tag, APP_VERSION):
            self.tray.set_update_available(tag, url)


APP_NAME = "KaraokeBird"
RELEASES_URL = "https://github.com/joshshiman/karaokebird/releases"
# Windows truncates tray tooltips longer than this.
MAX_TOOLTIP_LENGTH = 127


def format_offset(ms):
    return f"{ms:+d} ms" if ms else "0 ms"


def _add_menu_label(menu, text):
    """A disabled, non-clickable heading (QMenu.addSection shows no text on
    the Windows style)."""
    action = menu.addAction(text)
    action.setEnabled(False)
    return action


class TrayController(QObject):
    """The system tray icon, its menu, and the actions behind it."""

    def __init__(
        self, app, window, track_info_window, settings_manager, fader, reader, offsets
    ):
        super().__init__()
        self.app = app
        self.window = window
        self.track_info_window = track_info_window
        self.settings_manager = settings_manager
        self.fader = fader
        self.reader = reader
        self.offsets = offsets
        self.editing = False

        reader.notice.connect(lambda text: self.show_message(APP_NAME, text))

        for win in (window, track_info_window):
            win.edit_finished.connect(lambda: self.set_edit_mode(False))
        window.drag_finished.connect(self._on_overlay_dragged)
        track_info_window.drag_finished.connect(self._on_track_info_dragged)

        self.tray = QSystemTrayIcon(app)

        # Check for custom logo
        logo_path = resource_path("KaraokeBirdLogo.png")
        if os.path.exists(logo_path):
            icon = QIcon(logo_path)
        else:
            # Fallback: Create a simple icon (Green Square)
            pixmap = QPixmap(64, 64)
            pixmap.fill(QColor("#1DB954"))
            icon = QIcon(pixmap)

        self.tray.setIcon(icon)
        app.setWindowIcon(icon)
        self.tray.setToolTip(APP_NAME)
        self.tray.activated.connect(self._on_activated)

        self.menu = QMenu()
        self._build_menu()
        self.tray.setContextMenu(self.menu)
        self.tray.show()

    # --- Menu ---

    def _build_menu(self):
        menu = self.menu

        # Title Action
        title_action = QAction("Karaoke Bird", self.menu)
        title_action.setEnabled(False)
        menu.addAction(title_action)
        menu.addSeparator()

        # Toggle Visibility
        action_toggle = QAction("Show/Hide Overlay", self.menu)
        action_toggle.setCheckable(True)
        action_toggle.setChecked(self.window.isVisible())
        action_toggle.triggered.connect(self.window.toggle_visibility)
        self.window.visibility_changed.connect(action_toggle.setChecked)
        menu.addAction(action_toggle)

        # Toggle Track Info Visibility (independent of the lyrics overlay)
        action_toggle_track_info = QAction("Show/Hide Track Info", self.menu)
        action_toggle_track_info.setCheckable(True)
        action_toggle_track_info.setChecked(self.track_info_window.isVisible())
        action_toggle_track_info.triggered.connect(
            self.track_info_window.toggle_visibility
        )
        self.track_info_window.visibility_changed.connect(
            action_toggle_track_info.setChecked
        )
        menu.addAction(action_toggle_track_info)

        self.action_edit = QAction("Edit Position", self.menu)
        self.action_edit.setCheckable(True)
        self.action_edit.triggered.connect(self.set_edit_mode)
        menu.addAction(self.action_edit)

        # Sync offset, rebuilt each time it opens so it shows current values.
        self.offset_menu = menu.addMenu("Sync offset")
        self.offset_menu.aboutToShow.connect(self._refresh_offset_menu)
        self._refresh_offset_menu()

        action_search_again = QAction("Wrong lyrics? Search again", self.menu)
        action_search_again.triggered.connect(self.reader.request_search_again)
        menu.addAction(action_search_again)

        # Media source, rebuilt each time it opens (players come and go).
        self.source_menu = menu.addMenu("Source")
        self.source_menu.aboutToShow.connect(self._refresh_source_menu)
        self._refresh_source_menu()

        menu.addSeparator()

        # Settings Action
        action_settings = QAction("Settings...", self.menu)
        action_settings.triggered.connect(self.show_settings)
        menu.addAction(action_settings)

        # Check for Updates
        self._release_url = RELEASES_URL
        self.action_updates = QAction("Check for Updates...", self.menu)
        self.action_updates.triggered.connect(self._open_releases)
        menu.addAction(self.action_updates)

        menu.addSeparator()

        # Exit Action
        action_exit = QAction("Exit KaraokeBird", self.menu)
        action_exit.triggered.connect(self.app.quit)
        menu.addAction(action_exit)

    def _refresh_offset_menu(self):
        menu = self.offset_menu
        menu.clear()

        current = self.settings_manager.settings.get("sync_offset_ms", 0)
        _add_menu_label(menu, f"All songs: {format_offset(current)}")
        menu.addAction(
            f"−{SYNC_OFFSET_STEP_MS} ms (lyrics later)",
            lambda: self._change_global_offset(-SYNC_OFFSET_STEP_MS),
        )
        menu.addAction(
            f"+{SYNC_OFFSET_STEP_MS} ms (lyrics earlier)",
            lambda: self._change_global_offset(SYNC_OFFSET_STEP_MS),
        )
        menu.addAction("Reset", lambda: self._change_global_offset(None))

        # Per-song offset, added on top of the global one.
        has_track = self.offsets.track_id is not None
        song_label = format_offset(self.offsets.current()) if has_track else "no song"
        menu.addSeparator()
        _add_menu_label(menu, f"This song: {song_label}")
        song_actions = [
            menu.addAction(
                f"−{SYNC_OFFSET_STEP_MS} ms",
                lambda: self.offsets.adjust(-SYNC_OFFSET_STEP_MS, save_now=True),
            ),
            menu.addAction(
                f"+{SYNC_OFFSET_STEP_MS} ms",
                lambda: self.offsets.adjust(SYNC_OFFSET_STEP_MS, save_now=True),
            ),
            menu.addAction("Reset", self.offsets.reset),
        ]
        for action in song_actions:
            action.setEnabled(has_track)

    def _refresh_source_menu(self):
        menu = self.source_menu
        menu.clear()
        preferred = self.settings_manager.settings.get("preferred_source", "")
        group = QActionGroup(menu)

        auto = menu.addAction("Auto (whatever is playing)")
        auto.setCheckable(True)
        auto.setChecked(not preferred)
        auto.triggered.connect(lambda: self._set_source(""))
        group.addAction(auto)

        sources = self.reader.list_sources()
        if preferred and preferred not in sources:
            sources.append(preferred)  # pinned but not running right now
        if sources:
            menu.addSeparator()
        current = self.reader.current_source
        for app_id in dict.fromkeys(sources):  # de-duplicate, keep order
            label = friendly_source_name(app_id)
            if app_id not in self.reader.list_sources():
                label += " (not running)"
            elif app_id == current:
                label += " (now following)"
            action = menu.addAction(label)
            action.setCheckable(True)
            action.setChecked(app_id == preferred)
            action.setToolTip(app_id)
            action.triggered.connect(lambda _=False, a=app_id: self._set_source(a))
            group.addAction(action)

    def _set_source(self, app_id):
        self.settings_manager.set("preferred_source", app_id)
        self.settings_manager.save()
        logging.info(f"Media source set to {app_id or 'Auto'}")

    def set_update_available(self, tag, url):
        self.action_updates.setText(f"Update available — {tag}")
        self._release_url = url or RELEASES_URL
        self.show_message(
            "Update available",
            f"KaraokeBird {tag} is available. Choose “Update available” in the "
            "tray menu to download it.",
        )

    def _open_releases(self):
        webbrowser.open(self._release_url)

    def _change_global_offset(self, delta_ms):
        """Adjust (or reset, with None) the global sync offset and save it.
        The overlay reads sync_offset_ms live, so this applies at once."""
        settings = self.settings_manager.settings
        if delta_ms is None:
            value = 0
        else:
            value = settings.get("sync_offset_ms", 0) + delta_ms
        value = max(-SYNC_OFFSET_LIMIT_MS, min(SYNC_OFFSET_LIMIT_MS, value))
        self.settings_manager.set("sync_offset_ms", value)
        self.settings_manager.save()
        logging.info(f"Global sync offset set to {value} ms")

    def _on_activated(self, reason):
        # Left-click toggles the overlay; right-click opens the menu.
        if reason == QSystemTrayIcon.ActivationReason.Trigger:
            self.window.toggle_visibility()

    def set_track(self, title, artist):
        parts = [APP_NAME] + [p for p in (title, artist) if p]
        tooltip = " — ".join(parts)
        if len(tooltip) > MAX_TOOLTIP_LENGTH:
            tooltip = tooltip[: MAX_TOOLTIP_LENGTH - 1] + "…"
        self.tray.setToolTip(tooltip)

    def show_message(self, title, text, icon=QSystemTrayIcon.MessageIcon.Information):
        self.tray.showMessage(title, text, icon, 8000)

    # --- Edit mode (drag to position) ---

    def set_edit_mode(self, enabled):
        if enabled == self.editing:
            return
        self.editing = enabled
        self.action_edit.setChecked(enabled)
        self.fader.set_suspended(enabled)

        self.window.set_edit_mode(enabled)  # hides the window (flag change)
        self.track_info_window.set_edit_mode(enabled)

        if enabled:
            self.window.show()
            self.window.raise_()
            self.window.activateWindow()
            self.window.setFocus()
            return

        # Leaving: click-through is restored; save and re-apply everything,
        # then restore each window's own visibility.
        self.settings_manager.save()
        self.apply_settings_everywhere()
        self.window.setVisible(self.settings_manager.settings.get("overlay_visible", True))
        self.track_info_window.update_visibility()

    def _on_overlay_dragged(self):
        settings = self.settings_manager.settings
        # Dropped on another display: switch to it.
        screens = QApplication.screens()
        screen = QApplication.screenAt(self.window.geometry().center())
        if screen is not None and screen in screens:
            settings["screen_index"] = screens.index(screen)
        else:
            screen = get_target_screen(settings)

        settings.update(
            overlay_offsets_from_pos(
                settings, screen.geometry(), self.window.x(), self.window.y()
            )
        )
        # Snap to the clamped position; the track info follows the overlay.
        self.window.apply_geometry(settings)
        self.track_info_window.apply_geometry(settings)

    def _on_track_info_dragged(self):
        settings = self.settings_manager.settings
        screen_geom = get_target_screen(settings).geometry()
        settings.update(
            track_info_offsets_from_pos(
                settings,
                screen_geom,
                self.track_info_window.x(),
                self.track_info_window.y(),
            )
        )
        self.track_info_window.apply_geometry(settings)

    # --- Settings ---

    def apply_settings_everywhere(self):
        """Re-apply the saved settings to every window and the hotkeys."""
        self.window.apply_settings()
        self.track_info_window.apply_settings()
        self.fader.apply_settings()
        self.reader.refresh_if_options_changed()
        warn_failed_hotkeys(
            self.tray,
            register_hotkeys(self.window, self.track_info_window, self.offsets),
        )

    def show_settings(self):
        # The dialog's live preview and edit mode both move the windows.
        self.set_edit_mode(False)
        dlg = SettingsDialog(self.settings_manager)
        dlg.clear_lyrics_cache_requested.connect(self.reader.clear_cache)

        # Hook into the dialog's update_preview to trigger live updates on the main window
        original_update_preview = dlg.update_preview

        def live_update_proxy():
            original_update_preview()
            # apply_settings only rebuilds labels when the line counts or
            # font change, so this stays cheap on every tick.
            self.window.apply_settings(dlg.temp_settings)
            self.track_info_window.apply_settings(dlg.temp_settings)

        dlg.update_preview = live_update_proxy

        # Save or Cancel: apply the saved settings (Cancel reverts the
        # live preview to them).
        dlg.exec()
        self.apply_settings_everywhere()
        if dlg.edit_position_requested:
            self.set_edit_mode(True)


def main():
    setup_logging()
    logging.info("Starting KaraokeBird")

    is_first_run = not os.path.exists(SETTINGS_FILE)

    app = QApplication(sys.argv)
    app.setQuitOnLastWindowClosed(False)  # Keep running for tray

    # Single instance: a second copy would register duplicate hotkeys and
    # draw a second overlay. The lock is released when the process exits.
    instance_lock = QLockFile(os.path.join(get_app_data_dir(), "karaokebird.lock"))
    if not instance_lock.tryLock(100):
        logging.info("Another instance is already running; exiting")
        QMessageBox.information(
            None,
            "KaraokeBird",
            "KaraokeBird is already running (see the system tray).",
        )
        return

    loop = qasync.QEventLoop(app)
    asyncio.set_event_loop(loop)

    settings_manager = SettingsManager()

    window = OverlayWindow(settings_manager)
    # Restore the visibility from the last session.
    if settings_manager.settings.get("overlay_visible", True):
        window.show()

    track_info_window = TrackInfoWindow(settings_manager)

    fader = IdleFader([window, track_info_window], settings_manager)

    offsets = TrackOffsetController(TrackOffsets(TRACK_OFFSETS_FILE), window)
    app.aboutToQuit.connect(offsets.flush)

    reader = SpotifyReader(settings_manager, LyricsDiskCache(LYRICS_CACHE_DIR))

    # Create tray icon
    tray = TrayController(
        app, window, track_info_window, settings_manager, fader, reader, offsets
    )

    if is_first_run:
        tray.show_message(
            "KaraokeBird is running",
            "Look for the bird icon in the system tray — right-click it to "
            "open Settings and position the lyrics overlay.",
        )

    warn_failed_hotkeys(
        tray.tray, register_hotkeys(window, track_info_window, offsets)
    )

    watch_screens(app, [window, track_info_window])

    # Held in a local so it lives as long as main() (the whole app run).
    update_checker = UpdateChecker(settings_manager, tray)

    # Connect signals
    reader.status_message.connect(window.update_status)
    reader.track_changed.connect(offsets.set_track)
    reader.track_changed.connect(window.set_track_info)
    reader.track_changed.connect(track_info_window.set_track_info)
    reader.track_changed.connect(tray.set_track)
    reader.lyrics_found.connect(window.on_lyrics_found)
    reader.playback_sync.connect(window.on_playback_sync)
    reader.playback_sync.connect(fader.on_playback_sync)
    reader.session_lost.connect(window.on_session_lost)
    reader.session_lost.connect(offsets.clear_track)
    reader.session_lost.connect(lambda: track_info_window.set_track_info("", ""))
    reader.session_lost.connect(lambda: tray.set_track("", ""))
    reader.session_lost.connect(fader.on_session_lost)

    with loop:
        loop.create_task(main_loop(reader))
        loop.run_forever()


if __name__ == "__main__":
    main()
