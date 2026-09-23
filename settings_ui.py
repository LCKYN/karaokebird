import copy
import json
import logging
import os
import re

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtGui import QColor, QFont, QKeySequence
from PyQt6.QtWidgets import (
    QApplication,
    QCheckBox,
    QColorDialog,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QDoubleSpinBox,
    QFontComboBox,
    QFormLayout,
    QFrame,
    QGroupBox,
    QHBoxLayout,
    QInputDialog,
    QKeySequenceEdit,
    QLabel,
    QPushButton,
    QScrollArea,
    QSlider,
    QSpinBox,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

import autostart
from ui_components import StrokedLabel


def get_app_data_dir():
    """Directory for persisted app state (settings, logs), outside the CWD.

    PyInstaller builds are commonly launched from arbitrary/read-only
    directories, so writing next to the executable isn't reliable.
    """
    base = os.getenv("APPDATA") or os.getcwd()
    app_dir = os.path.join(base, "KaraokeBird")
    os.makedirs(app_dir, exist_ok=True)
    return app_dir


SETTINGS_FILE = os.path.join(get_app_data_dir(), "settings.json")
TRACK_OFFSETS_FILE = os.path.join(get_app_data_dir(), "track_offsets.json")
LYRICS_CACHE_DIR = os.path.join(get_app_data_dir(), "lyrics_cache")

OVERLAY_WIDTH = 1200
OVERLAY_MIN_HEIGHT = 400
# Layout margins plus breathing room above/below the lyric lines.
OVERLAY_PADDING = 40


def clamp(value, min_value, max_value):
    return max(min_value, min(value, max_value))


def color_button_style(color):
    """Swatch style with a visible border so light/white colors don't
    disappear against the dialog background."""
    return (
        f"background-color: {color}; border: 1px solid #888888; border-radius: 4px;"
    )


def get_track_info_height(font_size):
    return max(60, int(font_size * 3))


def get_track_info_width(screen_width):
    return min(screen_width, max(OVERLAY_WIDTH, int(screen_width * 0.9)))


def get_track_info_gap(font_size):
    return max(6, int(font_size * 0.5))


def get_line_height(font_size):
    """Approximate pixel height of one lyric line at ``font_size`` points.

    1pt is 4/3px at Qt's logical 96 DPI; a rendered line (ascent, descent and
    leading) is about 1.5x the pixel size, plus the layout's 6px spacing.
    """
    return int(font_size * 4 / 3 * 1.5) + 6


def get_overlay_height(settings):
    """Height that fits the highlight line and every context line."""
    num_context = settings.get("num_history_lines", 0) + settings.get(
        "num_future_lines", 1
    )
    content = get_line_height(settings.get("font_size_highlight", 24))
    content += num_context * get_line_height(settings.get("font_size_normal", 14))
    if settings.get("translation_lang"):
        content += get_line_height(settings.get("font_size_normal", 14))
    return max(OVERLAY_MIN_HEIGHT, content + OVERLAY_PADDING)


def get_overlay_position_bounds(settings, screen_geom):
    """Return offset bounds that let the lyric anchor reach every screen edge.

    The overlay is deliberately larger than an individual lyric line.  Positioning
    the *window* inside the screen therefore leaves the lyrics stranded away from
    the left and right edges.  These bounds are based on the window's centre, so
    the visible lyric anchor can travel across the whole selected display even
    when the overlay itself extends beyond an edge.
    """
    height = get_overlay_height(settings)
    return {
        "min_x_offset": -(screen_geom.width() // 2),
        "max_x_offset": screen_geom.width() // 2,
        # A negative Y offset moves the overlay down.  At the minimum, the
        # centre of the lyric overlay is exactly at the display's bottom edge.
        "min_y_offset": -(height // 2),
        "max_y_offset": screen_geom.height() - (height // 2),
    }


def compute_overlay_geometry(settings, screen_geom):
    width = OVERLAY_WIDTH
    height = get_overlay_height(settings)

    position_bounds = get_overlay_position_bounds(settings, screen_geom)
    min_y_offset = position_bounds["min_y_offset"]
    max_y_offset = position_bounds["max_y_offset"]
    y_offset = clamp(
        settings.get("window_y_offset", 0), min_y_offset, max_y_offset
    )
    base_y = screen_geom.y() + (screen_geom.height() - height)
    y_pos = base_y - y_offset

    min_x_offset = position_bounds["min_x_offset"]
    max_x_offset = position_bounds["max_x_offset"]
    x_offset = clamp(
        settings.get("window_x_offset", 0), min_x_offset, max_x_offset
    )

    center_x = screen_geom.x() + (screen_geom.width() - width) // 2
    x_pos = center_x + x_offset

    return {
        "x": x_pos,
        "y": y_pos,
        "width": width,
        "height": height,
        "min_x_offset": min_x_offset,
        "max_x_offset": max_x_offset,
        "min_y_offset": min_y_offset,
        "max_y_offset": max_y_offset,
    }


def compute_track_info_geometry(settings, screen_geom):
    overlay_geom = compute_overlay_geometry(settings, screen_geom)

    font_size = settings.get("font_size_normal", 14)
    width = get_track_info_width(screen_geom.width())
    height = get_track_info_height(font_size)
    gap = get_track_info_gap(font_size)

    base_x = overlay_geom["x"] + (overlay_geom["width"] - width) // 2
    base_y = overlay_geom["y"] + (overlay_geom["height"] // 2) - height - gap

    min_x_offset = screen_geom.x() - base_x
    max_x_offset = screen_geom.x() + screen_geom.width() - width - base_x
    min_y_offset = screen_geom.y() - base_y
    max_y_offset = screen_geom.y() + screen_geom.height() - height - base_y

    x_offset = clamp(
        settings.get("track_info_x_offset", 0), min_x_offset, max_x_offset
    )
    y_offset = clamp(
        settings.get("track_info_y_offset", 0), min_y_offset, max_y_offset
    )

    return {
        "x": base_x + x_offset,
        "y": base_y + y_offset,
        "width": width,
        "height": height,
        "base_x": base_x,
        "base_y": base_y,
        "min_x_offset": min_x_offset,
        "max_x_offset": max_x_offset,
        "min_y_offset": min_y_offset,
        "max_y_offset": max_y_offset,
    }


def overlay_offsets_from_pos(settings, screen_geom, x, y):
    """Inverse of compute_overlay_geometry: the (clamped) offsets that put
    the overlay's top-left corner at (x, y)."""
    geom = compute_overlay_geometry(settings, screen_geom)
    center_x = screen_geom.x() + (screen_geom.width() - geom["width"]) // 2
    base_y = screen_geom.y() + (screen_geom.height() - geom["height"])
    return {
        "window_x_offset": clamp(
            x - center_x, geom["min_x_offset"], geom["max_x_offset"]
        ),
        "window_y_offset": clamp(
            base_y - y, geom["min_y_offset"], geom["max_y_offset"]
        ),
    }


def track_info_offsets_from_pos(settings, screen_geom, x, y):
    """Inverse of compute_track_info_geometry: the (clamped) offsets that put
    the track info's top-left corner at (x, y), relative to the overlay's
    current position in ``settings``."""
    geom = compute_track_info_geometry(settings, screen_geom)
    return {
        "track_info_x_offset": clamp(
            x - geom["base_x"], geom["min_x_offset"], geom["max_x_offset"]
        ),
        "track_info_y_offset": clamp(
            y - geom["base_y"], geom["min_y_offset"], geom["max_y_offset"]
        ),
    }


DEFAULT_SETTINGS = {
    "highlight_color": "#ffff00",
    "stroke_color": "#000000",
    "normal_color": "#ebebeb",
    "background_enabled": False,
    "background_color": "#64000000",  # #AARRGGBB
    "track_info_background_enabled": False,
    "font_family": "Century Gothic",
    "font_size_highlight": 24,
    "font_size_normal": 14,
    "window_y_offset": 0,
    "window_x_offset": 0,
    "screen_index": 0,
    "num_history_lines": 0,
    "num_future_lines": 1,
    "sync_offset_ms": 0,
    "enable_animations": False,
    "animation_type": "fade",
    "stroke_enabled_highlight": True,
    "stroke_enabled_context": False,
    "toggle_hotkey": "",
    "track_info_enabled": False,
    "track_info_x_offset": 0,
    "track_info_y_offset": 0,
    "track_info_hotkey": "",
    "auto_hide_idle": True,
    "overlay_visible": True,
    "track_info_manually_hidden": False,
    "word_highlight": False,
    "translation_lang": "",
    "offset_back_hotkey": "",
    "offset_fwd_hotkey": "",
    "preferred_source": "",  # SMTC app id; "" = Auto
    "presets": {},  # name -> {appearance key: value}
    "update_check_enabled": True,
    "last_update_check": 0.0,  # time.time() of the last check
}

# Settings changed outside the dialog (tray, hotkeys, background checks)
# while it may be open. The dialog keeps their current values on Save
# instead of the ones it copied when it opened, and Restore Defaults leaves
# them alone.
RUNTIME_STATE_KEYS = (
    "overlay_visible",
    "track_info_manually_hidden",
    "preferred_source",
    "presets",
    "last_update_check",
)

# What a style preset saves and restores.
APPEARANCE_KEYS = (
    "font_family",
    "font_size_highlight",
    "font_size_normal",
    "highlight_color",
    "normal_color",
    "stroke_color",
    "stroke_enabled_highlight",
    "stroke_enabled_context",
    "enable_animations",
    "animation_type",
    "background_enabled",
    "background_color",
    "track_info_background_enabled",
)


def make_preset(settings):
    return {key: copy.deepcopy(settings[key]) for key in APPEARANCE_KEYS}


def apply_preset(settings, preset):
    """Copy a preset's appearance values into ``settings``, skipping unknown
    or badly typed ones. Returns the rejected keys."""
    rejected = []
    if not isinstance(preset, dict):
        return ["<preset>"]
    for key, value in preset.items():
        if key not in APPEARANCE_KEYS:
            rejected.append(key)
            continue
        try:
            settings[key] = _coerce_setting(value, DEFAULT_SETTINGS[key])
        except (ValueError, OverflowError):
            rejected.append(key)
    if "background_color" in preset:
        settings["background_color"] = normalize_color(
            settings["background_color"], DEFAULT_SETTINGS["background_color"]
        )
    return rejected

_HEX_COLOR_RE = re.compile(r"^#(?:[0-9a-fA-F]{6}|[0-9a-fA-F]{8})$")
_CSS_RGBA_RE = re.compile(
    r"^rgba?\(\s*(\d+)\s*,\s*(\d+)\s*,\s*(\d+)\s*(?:,\s*([\d.]+)\s*)?\)$",
    re.IGNORECASE,
)


def normalize_color(value, default):
    """Return ``value`` as ``#RRGGBB`` / ``#AARRGGBB`` (which QColor reads).

    Older settings files store CSS ``rgba(r, g, b, a)`` strings, with alpha
    either 0-255 or a 0-1 fraction; those are converted. Anything else that
    isn't a hex color falls back to ``default``.
    """
    if isinstance(value, str):
        value = value.strip()
        if _HEX_COLOR_RE.match(value):
            return value.lower()
        match = _CSS_RGBA_RE.match(value)
        if match:
            r, g, b = (min(255, int(c)) for c in match.groups()[:3])
            alpha = match.group(4)
            if alpha is None:
                a = 255
            elif "." in alpha:
                a = round(min(1.0, float(alpha)) * 255)
            else:
                a = min(255, int(alpha))
            return f"#{a:02x}{r:02x}{g:02x}{b:02x}"
    return default


def css_color(value):
    """Convert a ``#AARRGGBB`` color to ``rgba(...)`` for Qt style sheets."""
    if isinstance(value, str) and len(value) == 9 and value.startswith("#"):
        a, r, g, b = (int(value[i : i + 2], 16) for i in (1, 3, 5, 7))
        return f"rgba({r}, {g}, {b}, {a})"
    return value


def _coerce_setting(value, default):
    """Return ``value`` converted to the type of ``default``, or raise
    ValueError when the types aren't compatible.

    int and float are interchangeable (converted to the default's type), but
    bool must stay bool, since ``isinstance(True, int)`` is true.
    """
    if isinstance(default, bool) or isinstance(value, bool):
        if isinstance(default, bool) and isinstance(value, bool):
            return value
        raise ValueError
    if isinstance(default, (int, float)) and isinstance(value, (int, float)):
        return type(default)(round(value) if isinstance(default, int) else value)
    if isinstance(value, type(default)):
        return value
    raise ValueError


def validate_settings(data, defaults):
    """Merge ``data`` over ``defaults``, keeping only well-typed known keys.

    Returns ``(settings, rejected_keys)``.
    """
    settings = copy.deepcopy(defaults)
    rejected = []
    if not isinstance(data, dict):
        return settings, ["<root>"]
    for key, value in data.items():
        if key not in defaults:
            rejected.append(key)
            continue
        try:
            settings[key] = _coerce_setting(value, defaults[key])
        except (ValueError, OverflowError):
            rejected.append(key)
    return settings, rejected


class SettingsManager:
    def __init__(self):
        self.settings = copy.deepcopy(DEFAULT_SETTINGS)
        self.load()

    def load(self):
        if not os.path.exists(SETTINGS_FILE):
            return
        try:
            with open(SETTINGS_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
        except Exception:
            logging.exception("Error loading settings")
            return

        self.settings, rejected = validate_settings(data, DEFAULT_SETTINGS)
        for key in rejected:
            logging.warning(f"Ignoring invalid or unknown setting: {key!r}")

        # Older versions stored a CSS "rgba(...)" string, which QColor can't
        # read.
        self.settings["background_color"] = normalize_color(
            self.settings["background_color"], DEFAULT_SETTINGS["background_color"]
        )

    def save(self):
        tmp_path = SETTINGS_FILE + ".tmp"
        try:
            with open(tmp_path, "w", encoding="utf-8") as f:
                json.dump(self.settings, f, indent=4)
            os.replace(tmp_path, SETTINGS_FILE)
        except Exception:
            logging.exception("Error saving settings")

    def set(self, key, value):
        self.settings[key] = value


# (setting key, label, help text) for each global hotkey.
HOTKEY_FIELDS = (
    (
        "toggle_hotkey",
        "Toggle Lyrics Hotkey:",
        "Click and press a key combination (e.g., Ctrl+L) to hide/show the lyrics.",
    ),
    (
        "track_info_hotkey",
        "Toggle Track Info Hotkey:",
        "Hide/show the song and artist independently of the lyrics.",
    ),
    (
        "offset_back_hotkey",
        "Song Offset −100 ms:",
        "Shows this song's lyrics 100 ms later (saved per song).",
    ),
    (
        "offset_fwd_hotkey",
        "Song Offset +100 ms:",
        "Shows this song's lyrics 100 ms earlier (saved per song).",
    ),
)

# (code, name) for the translation combo; "" turns translations off.
TRANSLATION_LANGUAGES = (
    ("", "Off"),
    ("en", "English"),
    ("th", "Thai"),
    ("ja", "Japanese"),
    ("ko", "Korean"),
    ("zh", "Chinese"),
    ("es", "Spanish"),
    ("fr", "French"),
    ("de", "German"),
    ("pt", "Portuguese"),
    ("it", "Italian"),
    ("ru", "Russian"),
    ("id", "Indonesian"),
    ("vi", "Vietnamese"),
)

PREVIEW_CONTEXT_LINES = 2
# (previous lines, current line, upcoming lines); "Preview animation" swaps
# between the two.
PREVIEW_SAMPLES = (
    (
        ["Earlier Lyric Line", "Previous Lyric Line"],
        "Current Active Lyric",
        ["Upcoming Lyric Line", "Later Lyric Line"],
    ),
    (
        ["Previous Lyric Line", "Current Active Lyric"],
        "The Next Line Arrives",
        ["Later Lyric Line", "Even Later Line"],
    ),
)


def _scrollable(widget):
    area = QScrollArea()
    area.setWidget(widget)
    area.setWidgetResizable(True)
    area.setFrameShape(QFrame.Shape.NoFrame)
    area.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
    return area


class SettingsDialog(QDialog):
    settings_changed = pyqtSignal(dict)
    clear_lyrics_cache_requested = pyqtSignal()

    def __init__(self, settings_manager, parent=None):
        super().__init__(parent)
        self.manager = settings_manager
        self.temp_settings = copy.deepcopy(self.manager.settings)
        # Set by the "Drag on screen…" button.
        self.edit_position_requested = False
        self.setWindowTitle("KaraokeBird Settings")
        self.setFixedWidth(450)

        # Keep window on top
        self.setWindowFlags(Qt.WindowType.WindowStaysOnTopHint)

        main_layout = QVBoxLayout()

        # --- Preview Section (Always Visible) ---
        self.preview_frame = QFrame()
        self.preview_frame.setMinimumHeight(140)

        preview_layout = QVBoxLayout()
        preview_layout.setAlignment(Qt.AlignmentFlag.AlignCenter)

        # Up to PREVIEW_CONTEXT_LINES context lines on each side; update_preview
        # shows as many as the history/upcoming counts ask for.
        self.preview_prev_labels = []
        for _ in range(PREVIEW_CONTEXT_LINES):
            lbl = StrokedLabel("")
            lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
            self.preview_prev_labels.append(lbl)
            preview_layout.addWidget(lbl)

        self.preview_label = StrokedLabel("")
        self.preview_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        preview_layout.addWidget(self.preview_label)

        self.preview_next_labels = []
        for _ in range(PREVIEW_CONTEXT_LINES):
            lbl = StrokedLabel("")
            lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
            self.preview_next_labels.append(lbl)
            preview_layout.addWidget(lbl)

        self._preview_sample = 0
        self._set_preview_texts(PREVIEW_SAMPLES[0])

        self.preview_frame.setLayout(preview_layout)
        main_layout.addWidget(QLabel("<b>Live Preview:</b>"))
        main_layout.addWidget(self.preview_frame)
        main_layout.addSpacing(10)

        # --- Tabbed Categories ---
        # Each tab scrolls so the dialog fits on short (768px) screens.
        self.tabs = QTabWidget()

        self.tabs.addTab(_scrollable(self.create_appearance_tab()), "Appearance")
        self.tabs.addTab(_scrollable(self.create_layout_tab()), "Layout")
        self.tabs.addTab(_scrollable(self.create_system_tab()), "System")

        main_layout.addWidget(self.tabs)

        # --- Buttons ---
        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Save
            | QDialogButtonBox.StandardButton.Cancel
            | QDialogButtonBox.StandardButton.RestoreDefaults
        )
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        buttons.button(QDialogButtonBox.StandardButton.RestoreDefaults).clicked.connect(
            self.reset_defaults
        )
        main_layout.addWidget(buttons)

        self.setLayout(main_layout)
        self.update_preview()

    def create_appearance_tab(self):
        widget = QWidget()
        layout = QVBoxLayout()

        # Style presets: named snapshots of the appearance settings, saved
        # immediately (they're a library, not part of this edit session).
        preset_group = QGroupBox("Style Presets")
        preset_layout = QHBoxLayout()

        self.combo_presets = QComboBox()
        self.combo_presets.setMinimumWidth(160)
        self._refresh_presets()
        self.combo_presets.activated.connect(self._apply_selected_preset)
        preset_layout.addWidget(self.combo_presets, 1)

        btn_save_preset = QPushButton("Save…")
        btn_save_preset.setToolTip("Save the current look as a preset")
        btn_save_preset.clicked.connect(self._save_preset)
        preset_layout.addWidget(btn_save_preset)

        self.btn_delete_preset = QPushButton("Delete")
        self.btn_delete_preset.clicked.connect(self._delete_preset)
        preset_layout.addWidget(self.btn_delete_preset)

        preset_group.setLayout(preset_layout)
        layout.addWidget(preset_group)

        # Text Style Group
        font_group = QGroupBox("Typography")
        font_layout = QFormLayout()

        self.font_combo = QFontComboBox()
        self.font_combo.setCurrentFont(QFont(self.temp_settings["font_family"]))
        self.font_combo.currentFontChanged.connect(self.update_font)
        font_layout.addRow("Font Family:", self.font_combo)

        self.spin_size_high = QSpinBox()
        self.spin_size_high.setRange(8, 72)
        self.spin_size_high.setValue(self.temp_settings["font_size_highlight"])
        self.spin_size_high.valueChanged.connect(
            lambda v: self.update_setting("font_size_highlight", v)
        )
        font_layout.addRow("Highlight Size:", self.spin_size_high)

        self.spin_size_norm = QSpinBox()
        self.spin_size_norm.setRange(8, 72)
        self.spin_size_norm.setValue(self.temp_settings["font_size_normal"])
        self.spin_size_norm.valueChanged.connect(
            lambda v: self.update_setting("font_size_normal", v)
        )
        font_layout.addRow("Context Size:", self.spin_size_norm)

        font_group.setLayout(font_layout)
        layout.addWidget(font_group)

        # Colors & Effects Group
        color_group = QGroupBox("Colors & Visuals")
        color_layout = QFormLayout()

        self.btn_color_high = QPushButton("Choose...")
        self.btn_color_high.setFixedWidth(100)
        self.btn_color_high.setStyleSheet(
            color_button_style(self.temp_settings["highlight_color"])
        )
        self.btn_color_high.clicked.connect(
            lambda: self.pick_color("highlight_color", self.btn_color_high)
        )
        color_layout.addRow("Highlight Color:", self.btn_color_high)

        self.btn_color_norm = QPushButton("Choose...")
        self.btn_color_norm.setFixedWidth(100)
        self.btn_color_norm.setStyleSheet(
            color_button_style(self.temp_settings["normal_color"])
        )
        self.btn_color_norm.clicked.connect(
            lambda: self.pick_color("normal_color", self.btn_color_norm)
        )
        color_layout.addRow("Context Color:", self.btn_color_norm)

        self.btn_color_stroke = QPushButton("Choose...")
        self.btn_color_stroke.setFixedWidth(100)
        self.btn_color_stroke.setStyleSheet(
            color_button_style(self.temp_settings.get("stroke_color", "#000000"))
        )
        self.btn_color_stroke.clicked.connect(
            lambda: self.pick_color("stroke_color", self.btn_color_stroke)
        )
        color_layout.addRow("Stroke Color:", self.btn_color_stroke)

        self.check_anim = QCheckBox("Enable Transitions")
        self.check_anim.setChecked(self.temp_settings.get("enable_animations", True))
        self.check_anim.toggled.connect(
            lambda v: self.update_setting("enable_animations", v)
        )
        color_layout.addRow("Visuals:", self.check_anim)
        color_layout.addRow(
            QLabel("<small>Apply smooth motion when the lyric line changes.</small>")
        )

        self.combo_anim_type = QComboBox()
        self.combo_anim_type.addItems(["fade", "slide", "zoom"])
        self.combo_anim_type.setCurrentText(
            self.temp_settings.get("animation_type", "fade")
        )
        self.combo_anim_type.currentTextChanged.connect(
            lambda v: self.update_setting("animation_type", v)
        )
        color_layout.addRow("Effect Style:", self.combo_anim_type)

        self.btn_preview_anim = QPushButton("Preview animation")
        self.btn_preview_anim.clicked.connect(self.preview_animation)
        color_layout.addRow("", self.btn_preview_anim)

        color_group.setLayout(color_layout)
        layout.addWidget(color_group)

        # Readability background
        bg_group = QGroupBox("Background")
        bg_layout = QFormLayout()

        self.check_background = QCheckBox("Draw a background behind the lyrics")
        self.check_background.setChecked(
            self.temp_settings.get("background_enabled", False)
        )
        self.check_background.toggled.connect(
            lambda v: self.update_setting("background_enabled", v)
        )
        bg_layout.addRow(self.check_background)

        self.check_track_background = QCheckBox(
            "Draw a background behind the track info"
        )
        self.check_track_background.setChecked(
            self.temp_settings.get("track_info_background_enabled", False)
        )
        self.check_track_background.toggled.connect(
            lambda v: self.update_setting("track_info_background_enabled", v)
        )
        bg_layout.addRow(self.check_track_background)

        self.btn_color_bg = QPushButton("Choose...")
        self.btn_color_bg.setFixedWidth(100)
        self.btn_color_bg.setStyleSheet(
            color_button_style(css_color(self.temp_settings["background_color"]))
        )
        self.btn_color_bg.clicked.connect(
            lambda: self.pick_color(
                "background_color", self.btn_color_bg, with_alpha=True
            )
        )
        bg_layout.addRow("Background Color:", self.btn_color_bg)
        bg_layout.addRow(
            QLabel("<small>Keeps lyrics readable over bright windows.</small>")
        )

        bg_group.setLayout(bg_layout)
        layout.addWidget(bg_group)
        layout.addStretch()
        widget.setLayout(layout)
        return widget

    def create_layout_tab(self):
        widget = QWidget()
        layout = QVBoxLayout()

        # Position Group
        pos_group = QGroupBox("Screen Position")
        pos_layout = QVBoxLayout()

        screen_layout = QFormLayout()
        self.screen_combo = QComboBox()
        screens = QApplication.screens()
        for i, screen in enumerate(screens):
            geometry = screen.geometry()
            name = screen.name() or f"Display {i + 1}"
            self.screen_combo.addItem(
                f"{name} ({geometry.width()}x{geometry.height()})", i
            )

        screen_index = self.temp_settings.get("screen_index", 0)
        if 0 <= screen_index < self.screen_combo.count():
            self.screen_combo.setCurrentIndex(screen_index)
        self.screen_combo.currentIndexChanged.connect(self.update_screen_selection)
        screen_layout.addRow("Display:", self.screen_combo)
        pos_layout.addLayout(screen_layout)

        # Vertical Position
        y_layout = QHBoxLayout()
        self.slider_offset_y = QSlider(Qt.Orientation.Horizontal)
        self.spin_offset_y = QSpinBox()
        self._bind_slider_spin(
            self.slider_offset_y, self.spin_offset_y, "window_y_offset"
        )

        y_layout.addWidget(QLabel("Screen Bottom"))
        y_layout.addWidget(self.slider_offset_y)
        y_layout.addWidget(QLabel("Screen Top"))
        y_layout.addWidget(self.spin_offset_y)

        pos_layout.addLayout(y_layout)
        pos_layout.addWidget(
            QLabel(
                "<small>Adjust the vertical height. Negative values push lower.</small>"
            )
        )

        # Horizontal Position
        x_layout = QHBoxLayout()
        self.slider_offset_x = QSlider(Qt.Orientation.Horizontal)
        self.spin_offset_x = QSpinBox()
        self._bind_slider_spin(
            self.slider_offset_x, self.spin_offset_x, "window_x_offset"
        )

        x_layout.addWidget(QLabel("Screen Left"))
        x_layout.addWidget(self.slider_offset_x)
        x_layout.addWidget(QLabel("Screen Right"))
        x_layout.addWidget(self.spin_offset_x)

        pos_layout.addLayout(x_layout)
        pos_layout.addWidget(
            QLabel(
                "<small>Move the lyric anchor anywhere across the selected display. "
                "0 = center; the transparent overlay may extend past an edge.</small>"
            )
        )

        self.btn_drag_position = QPushButton("Drag on screen…")
        self.btn_drag_position.setToolTip(
            "Save these settings, close this window and drag the lyrics and "
            "track info into place."
        )
        self.btn_drag_position.clicked.connect(self.request_edit_position)
        pos_layout.addWidget(self.btn_drag_position)

        pos_group.setLayout(pos_layout)
        layout.addWidget(pos_group)

        # Context Group
        ctx_group = QGroupBox("Lyric Lines")
        ctx_layout = QFormLayout()

        self.spin_history = QSpinBox()
        self.spin_history.setRange(0, 5)
        self.spin_history.setValue(self.temp_settings.get("num_history_lines", 1))
        self.spin_history.valueChanged.connect(
            lambda v: self.update_setting("num_history_lines", v)
        )
        ctx_layout.addRow("History Lines:", self.spin_history)
        ctx_layout.addRow(
            QLabel(
                "<small>Number of previous lines to keep visible above the current one.</small>"
            )
        )

        self.spin_future = QSpinBox()
        self.spin_future.setRange(0, 5)
        self.spin_future.setValue(self.temp_settings.get("num_future_lines", 1))
        self.spin_future.valueChanged.connect(
            lambda v: self.update_setting("num_future_lines", v)
        )
        ctx_layout.addRow("Upcoming Lines:", self.spin_future)
        ctx_layout.addRow(
            QLabel(
                "<small>Number of future lines to show ahead of time below the current one.</small>"
            )
        )

        ctx_group.setLayout(ctx_layout)
        layout.addWidget(ctx_group)

        # Track Info Group
        track_group = QGroupBox("Track Info")
        track_layout = QVBoxLayout()

        self.check_track_info = QCheckBox("Show song and artist permanently")
        self.check_track_info.setChecked(
            self.temp_settings.get("track_info_enabled", False)
        )
        self.check_track_info.toggled.connect(
            lambda v: self.update_setting("track_info_enabled", v)
        )
        track_layout.addWidget(self.check_track_info)

        track_x_layout = QHBoxLayout()
        self.slider_track_x = QSlider(Qt.Orientation.Horizontal)
        self.spin_track_x = QSpinBox()
        self._bind_slider_spin(
            self.slider_track_x, self.spin_track_x, "track_info_x_offset"
        )

        track_x_layout.addWidget(QLabel("Left"))
        track_x_layout.addWidget(self.slider_track_x)
        track_x_layout.addWidget(QLabel("Right"))
        track_x_layout.addWidget(self.spin_track_x)
        track_layout.addLayout(track_x_layout)

        track_y_layout = QHBoxLayout()
        self.slider_track_y = QSlider(Qt.Orientation.Horizontal)
        self.spin_track_y = QSpinBox()
        self._bind_slider_spin(
            self.slider_track_y, self.spin_track_y, "track_info_y_offset"
        )

        # Stored offsets grow downward; flip only the slider's appearance so
        # it reads "down <- -> up" like the lyrics slider.
        self.slider_track_y.setInvertedAppearance(True)
        track_y_layout.addWidget(QLabel("Down"))
        track_y_layout.addWidget(self.slider_track_y)
        track_y_layout.addWidget(QLabel("Up"))
        track_y_layout.addWidget(self.spin_track_y)
        track_layout.addLayout(track_y_layout)

        track_layout.addWidget(
            QLabel("<small>Offsets are relative to the lyrics overlay.</small>")
        )

        track_group.setLayout(track_layout)
        layout.addWidget(track_group)

        # Stroke Toggle Group
        stroke_group = QGroupBox("Stroke/Outline Toggles")
        stroke_layout = QHBoxLayout()

        self.check_stroke_high = QCheckBox("On Highlight")
        self.check_stroke_high.setChecked(
            self.temp_settings.get("stroke_enabled_highlight", True)
        )
        self.check_stroke_high.toggled.connect(
            lambda v: self.update_setting("stroke_enabled_highlight", v)
        )

        self.check_stroke_context = QCheckBox("On Context")
        self.check_stroke_context.setChecked(
            self.temp_settings.get("stroke_enabled_context", True)
        )
        self.check_stroke_context.toggled.connect(
            lambda v: self.update_setting("stroke_enabled_context", v)
        )

        stroke_layout.addWidget(self.check_stroke_high)
        stroke_layout.addWidget(self.check_stroke_context)
        stroke_group.setLayout(stroke_layout)
        layout.addWidget(stroke_group)

        layout.addStretch()
        widget.setLayout(layout)
        return widget

    def create_system_tab(self):
        widget = QWidget()
        layout = QVBoxLayout()

        # Sync Group
        sync_group = QGroupBox("Timing & Sync")
        sync_layout = QFormLayout()

        self.spin_sync = QDoubleSpinBox()
        self.spin_sync.setRange(-10.0, 10.0)
        self.spin_sync.setSingleStep(0.1)
        self.spin_sync.setValue(self.temp_settings.get("sync_offset_ms", 0) / 1000.0)
        self.spin_sync.valueChanged.connect(
            lambda v: self.update_setting("sync_offset_ms", int(v * 1000))
        )
        sync_layout.addRow("Sync Offset (sec):", self.spin_sync)
        sync_layout.addRow(
            QLabel("<small>Use this if lyrics are consistently early or late.</small>")
        )

        sync_group.setLayout(sync_layout)
        layout.addWidget(sync_group)

        # Behavior Group
        behavior_group = QGroupBox("Behavior")
        behavior_layout = QVBoxLayout()

        self.check_auto_hide = QCheckBox("Fade out when nothing is playing")
        self.check_auto_hide.setChecked(self.temp_settings.get("auto_hide_idle", True))
        self.check_auto_hide.toggled.connect(
            lambda v: self.update_setting("auto_hide_idle", v)
        )
        behavior_layout.addWidget(self.check_auto_hide)
        behavior_layout.addWidget(
            QLabel(
                "<small>Hides the overlay and track info 5 seconds after playback "
                "pauses or stops, and brings them back when it resumes.</small>"
            )
        )

        # Reflects the registry, not settings.json; applied on Save.
        self.check_autostart = QCheckBox("Start with Windows")
        self.check_autostart.setChecked(autostart.is_enabled())
        behavior_layout.addWidget(self.check_autostart)

        self.check_update_check = QCheckBox("Check for updates once a day")
        self.check_update_check.setChecked(
            self.temp_settings.get("update_check_enabled", True)
        )
        self.check_update_check.toggled.connect(
            lambda v: self.update_setting("update_check_enabled", v)
        )
        behavior_layout.addWidget(self.check_update_check)
        behavior_layout.addWidget(
            QLabel(
                "<small>Asks GitHub for the latest release number. Nothing about "
                "you or your music is sent.</small>"
            )
        )

        behavior_group.setLayout(behavior_layout)
        layout.addWidget(behavior_group)

        # Lyrics Group
        lyrics_group = QGroupBox("Lyrics")
        lyrics_layout = QFormLayout()

        self.check_word_highlight = QCheckBox("Highlight word by word")
        self.check_word_highlight.setChecked(
            self.temp_settings.get("word_highlight", False)
        )
        self.check_word_highlight.toggled.connect(
            lambda v: self.update_setting("word_highlight", v)
        )
        lyrics_layout.addRow(self.check_word_highlight)
        lyrics_layout.addRow(
            QLabel(
                "<small>When word timing is available (Musixmatch); otherwise "
                "whole lines are highlighted.</small>"
            )
        )

        self.combo_translation = QComboBox()
        for code, name in TRANSLATION_LANGUAGES:
            self.combo_translation.addItem(name, code)
        index = self.combo_translation.findData(
            self.temp_settings.get("translation_lang", "")
        )
        self.combo_translation.setCurrentIndex(max(0, index))
        self.combo_translation.currentIndexChanged.connect(
            lambda _: self.update_setting(
                "translation_lang", self.combo_translation.currentData()
            )
        )
        lyrics_layout.addRow("Translation:", self.combo_translation)
        lyrics_layout.addRow(
            QLabel(
                "<small>Shows a translated line under the current one when "
                "Musixmatch has a translation.</small>"
            )
        )

        self.btn_clear_cache = QPushButton("Clear lyrics cache")
        self.btn_clear_cache.clicked.connect(self._clear_lyrics_cache)
        lyrics_layout.addRow(self.btn_clear_cache)

        lyrics_group.setLayout(lyrics_layout)
        layout.addWidget(lyrics_group)

        # Controls Group
        ctrl_group = QGroupBox("Controls")
        ctrl_layout = QFormLayout()

        # setting key -> QKeySequenceEdit
        self.hotkey_edits = {}
        for key, label, help_text in HOTKEY_FIELDS:
            edit = self._make_hotkey_edit(key)
            ctrl_layout.addRow(label, edit)
            ctrl_layout.addRow(QLabel(f"<small>{help_text}</small>"))

        self.hotkey_warning_label = QLabel("")
        self.hotkey_warning_label.setStyleSheet("color: #ff5555;")
        ctrl_layout.addRow(self.hotkey_warning_label)

        ctrl_group.setLayout(ctrl_layout)
        layout.addWidget(ctrl_group)

        layout.addStretch()
        widget.setLayout(layout)
        return widget

    def update_preview(self):
        self.update_position_bounds()
        context_font = QFont(
            self.temp_settings["font_family"], self.temp_settings["font_size_normal"]
        )
        context_color = self.temp_settings["normal_color"]
        stroke_color = self.temp_settings.get("stroke_color", "#000000")
        anim_type = self.temp_settings.get("animation_type", "fade")

        num_history = self.temp_settings.get("num_history_lines", 0)
        num_future = self.temp_settings.get("num_future_lines", 1)

        # The frame stands in for the desktop; with the readability
        # background on, it shows that color instead.
        if self.temp_settings.get("background_enabled", False):
            frame_color = css_color(self.temp_settings["background_color"])
        else:
            frame_color = "rgba(0, 0, 0, 100)"
        self.preview_frame.setStyleSheet(
            f"QFrame {{ background-color: {frame_color}; border-radius: 8px; }}"
        )

        # Context previews: show the lines closest to the current one, up to
        # the configured counts.
        num_slots = len(self.preview_prev_labels)
        for i, lbl in enumerate(self.preview_prev_labels):
            lbl.setVisible(i >= num_slots - num_history)
        for i, lbl in enumerate(self.preview_next_labels):
            lbl.setVisible(i < num_future)
        for lbl in self.preview_prev_labels + self.preview_next_labels:
            lbl.setFont(context_font)
            lbl.setStyleSheet(f"color: {context_color};")
            lbl.setStrokeColor(stroke_color)
            lbl.setStrokeEnabled(
                self.temp_settings.get("stroke_enabled_context", True)
            )
            lbl.animation_type = anim_type

        # Update Highlight Preview
        font = QFont(
            self.temp_settings["font_family"],
            self.temp_settings["font_size_highlight"],
            QFont.Weight.Bold,
        )
        self.preview_label.setFont(font)
        self.preview_label.setStyleSheet(
            f"color: {self.temp_settings['highlight_color']};"
        )
        self.preview_label.setStrokeColor(stroke_color)
        self.preview_label.setStrokeEnabled(
            self.temp_settings.get("stroke_enabled_highlight", True)
        )
        self.preview_label.animation_type = anim_type

    def _bind_slider_spin(self, slider, spin, key):
        """Keep a slider and spinbox in step. Only the spinbox writes the
        setting; the slider just drives it. Ranges and initial values are
        set (with signals blocked) by update_position_bounds."""
        slider.valueChanged.connect(spin.setValue)
        spin.valueChanged.connect(slider.setValue)
        spin.valueChanged.connect(lambda v: self.update_setting(key, v))

    def update_setting(self, key, value):
        self.temp_settings[key] = value
        self.update_preview()

    def _make_hotkey_edit(self, key):
        edit = QKeySequenceEdit()
        edit.setClearButtonEnabled(True)
        edit.setMaximumSequenceLength(1)
        current = self.temp_settings.get(key, "")
        if current:
            edit.setKeySequence(QKeySequence(current))
        edit.keySequenceChanged.connect(lambda seq: self.update_hotkey(key, seq))
        self.hotkey_edits[key] = edit
        return edit

    def update_hotkey(self, key, sequence):
        hotkey_str = sequence.toString(QKeySequence.SequenceFormat.PortableText)
        self.temp_settings[key] = hotkey_str
        self.check_hotkey_conflict()

    def check_hotkey_conflict(self):
        seen = {}
        duplicates = []
        for key, label, _ in HOTKEY_FIELDS:
            hotkey = self.temp_settings.get(key, "")
            if not hotkey:
                continue
            if hotkey in seen:
                duplicates.append(f"{seen[hotkey]} / {label.rstrip(':')}")
            else:
                seen[hotkey] = label.rstrip(":")
        if duplicates:
            self.hotkey_warning_label.setText(
                "<small>Same combination used for "
                + "; ".join(duplicates)
                + " — both actions will trigger.</small>"
            )
        else:
            self.hotkey_warning_label.setText("")

    def _clear_lyrics_cache(self):
        self.clear_lyrics_cache_requested.emit()
        self.btn_clear_cache.setText("Lyrics cache cleared")
        self.btn_clear_cache.setEnabled(False)

    def update_font(self, font):
        self.temp_settings["font_family"] = font.family()
        self.update_preview()

    def pick_color(self, key, button, with_alpha=False):
        options = QColorDialog.ColorDialogOption(0)
        if with_alpha:
            options = QColorDialog.ColorDialogOption.ShowAlphaChannel
        color = QColorDialog.getColor(
            QColor(self.temp_settings[key]), self, "Select Color", options
        )
        if color.isValid():
            if with_alpha:
                hex_color = color.name(QColor.NameFormat.HexArgb)
            else:
                hex_color = color.name()
            self.temp_settings[key] = hex_color
            button.setStyleSheet(color_button_style(css_color(hex_color)))
            self.update_preview()

    def _set_preview_texts(self, sample):
        prev_texts, current, next_texts = sample
        for lbl, text in zip(self.preview_prev_labels, prev_texts):
            lbl.setText(text)
        self.preview_label.setText(current)
        for lbl, text in zip(self.preview_next_labels, next_texts):
            lbl.setText(text)

    def preview_animation(self):
        """Run the selected transition on the preview labels."""
        anim_type = self.temp_settings.get("animation_type", "fade")
        for lbl in self._preview_labels():
            lbl.enable_animation = True
            lbl.animation_type = anim_type
        self._preview_sample = 1 - self._preview_sample
        self._set_preview_texts(PREVIEW_SAMPLES[self._preview_sample])

    def _preview_labels(self):
        return self.preview_prev_labels + [self.preview_label] + self.preview_next_labels

    def reset_defaults(self):
        runtime_state = {key: self.temp_settings[key] for key in RUNTIME_STATE_KEYS}
        self.temp_settings = copy.deepcopy(DEFAULT_SETTINGS)
        self.temp_settings.update(runtime_state)

        self._load_appearance_widgets()

        # Layout
        self.spin_history.setValue(self.temp_settings.get("num_history_lines", 1))
        self.spin_future.setValue(self.temp_settings.get("num_future_lines", 1))
        if 0 <= self.temp_settings.get("screen_index", 0) < self.screen_combo.count():
            self.screen_combo.setCurrentIndex(self.temp_settings.get("screen_index", 0))
        self.slider_offset_y.setValue(self.temp_settings["window_y_offset"])
        self.slider_offset_x.setValue(self.temp_settings.get("window_x_offset", 0))
        self.check_track_info.setChecked(
            self.temp_settings.get("track_info_enabled", False)
        )
        self.slider_track_x.setValue(self.temp_settings.get("track_info_x_offset", 0))
        self.slider_track_y.setValue(self.temp_settings.get("track_info_y_offset", 0))

        self.update_position_bounds()

        # System
        self.spin_sync.setValue(self.temp_settings.get("sync_offset_ms", 0) / 1000.0)
        self.check_auto_hide.setChecked(self.temp_settings["auto_hide_idle"])
        self.check_word_highlight.setChecked(self.temp_settings["word_highlight"])
        self.combo_translation.setCurrentIndex(
            max(0, self.combo_translation.findData(self.temp_settings["translation_lang"]))
        )
        self.check_update_check.setChecked(self.temp_settings["update_check_enabled"])
        for key, edit in self.hotkey_edits.items():
            edit.setKeySequence(QKeySequence(self.temp_settings.get(key, "")))
        self.check_hotkey_conflict()

        self.update_preview()

    # --- Style presets ---

    def _refresh_presets(self, select=None):
        self.combo_presets.clear()
        self.combo_presets.addItem("Choose a preset…", None)
        for name in sorted(self.temp_settings["presets"], key=str.lower):
            self.combo_presets.addItem(name, name)
        index = self.combo_presets.findData(select) if select else 0
        self.combo_presets.setCurrentIndex(max(0, index))

    def _store_presets(self, presets):
        """Presets are saved right away, not on the dialog's Save."""
        self.temp_settings["presets"] = presets
        self.manager.set("presets", copy.deepcopy(presets))
        self.manager.save()

    def _apply_selected_preset(self, index):
        name = self.combo_presets.itemData(index)
        if not name:
            return
        rejected = apply_preset(self.temp_settings, self.temp_settings["presets"][name])
        for key in rejected:
            logging.warning(f"Preset {name!r}: ignoring invalid value for {key!r}")
        self._load_appearance_widgets()
        self.update_preview()

    def _save_preset(self):
        current = self.combo_presets.currentData() or ""
        name, ok = QInputDialog.getText(self, "Save Preset", "Preset name:", text=current)
        name = name.strip()
        if not ok or not name:
            return
        presets = dict(self.temp_settings["presets"])
        presets[name] = make_preset(self.temp_settings)
        self._store_presets(presets)
        self._refresh_presets(select=name)

    def _delete_preset(self):
        name = self.combo_presets.currentData()
        if not name:
            return
        presets = dict(self.temp_settings["presets"])
        presets.pop(name, None)
        self._store_presets(presets)
        self._refresh_presets()

    def _load_appearance_widgets(self):
        """Show temp_settings' appearance values (APPEARANCE_KEYS) in the
        widgets."""
        self.font_combo.setCurrentFont(QFont(self.temp_settings["font_family"]))
        self.spin_size_high.setValue(self.temp_settings["font_size_highlight"])
        self.spin_size_norm.setValue(self.temp_settings["font_size_normal"])
        self.btn_color_high.setStyleSheet(
            color_button_style(self.temp_settings["highlight_color"])
        )
        self.btn_color_stroke.setStyleSheet(
            color_button_style(self.temp_settings["stroke_color"])
        )
        self.btn_color_norm.setStyleSheet(
            color_button_style(self.temp_settings["normal_color"])
        )
        self.check_anim.setChecked(self.temp_settings.get("enable_animations", True))
        self.combo_anim_type.setCurrentText(
            self.temp_settings.get("animation_type", "fade")
        )
        self.check_background.setChecked(self.temp_settings["background_enabled"])
        self.check_track_background.setChecked(
            self.temp_settings["track_info_background_enabled"]
        )
        self.btn_color_bg.setStyleSheet(
            color_button_style(css_color(self.temp_settings["background_color"]))
        )
        self.check_stroke_high.setChecked(
            self.temp_settings.get("stroke_enabled_highlight", True)
        )
        self.check_stroke_context.setChecked(
            self.temp_settings.get("stroke_enabled_context", True)
        )

    def get_selected_screen_geometry(self):
        screens = QApplication.screens()
        screen_index = self.temp_settings.get("screen_index", 0)
        if 0 <= screen_index < len(screens):
            return screens[screen_index].geometry()
        return QApplication.primaryScreen().geometry()

    def update_screen_selection(self, index):
        self.temp_settings["screen_index"] = index
        self.update_preview()

    def update_position_bounds(self):
        if not hasattr(self, "slider_offset_y"):
            return

        screen_geom = self.get_selected_screen_geometry()
        overlay_geom = compute_overlay_geometry(self.temp_settings, screen_geom)
        min_y_offset = overlay_geom["min_y_offset"]
        max_y_offset = overlay_geom["max_y_offset"]
        min_x_offset = overlay_geom["min_x_offset"]
        max_x_offset = overlay_geom["max_x_offset"]

        y_value = clamp(
            self.temp_settings.get("window_y_offset", 0), min_y_offset, max_y_offset
        )
        x_value = clamp(
            self.temp_settings.get("window_x_offset", 0), min_x_offset, max_x_offset
        )

        self.slider_offset_y.blockSignals(True)
        self.spin_offset_y.blockSignals(True)
        self.slider_offset_x.blockSignals(True)
        self.spin_offset_x.blockSignals(True)

        self.slider_offset_y.setRange(min_y_offset, max_y_offset)
        self.spin_offset_y.setRange(min_y_offset, max_y_offset)
        self.slider_offset_x.setRange(min_x_offset, max_x_offset)
        self.spin_offset_x.setRange(min_x_offset, max_x_offset)

        self.slider_offset_y.setValue(y_value)
        self.spin_offset_y.setValue(y_value)
        self.slider_offset_x.setValue(x_value)
        self.spin_offset_x.setValue(x_value)

        self.slider_offset_y.blockSignals(False)
        self.spin_offset_y.blockSignals(False)
        self.slider_offset_x.blockSignals(False)
        self.spin_offset_x.blockSignals(False)

        self.temp_settings["window_y_offset"] = y_value
        self.temp_settings["window_x_offset"] = x_value

        track_geom = compute_track_info_geometry(self.temp_settings, screen_geom)
        min_track_x = track_geom["min_x_offset"]
        max_track_x = track_geom["max_x_offset"]
        min_track_y = track_geom["min_y_offset"]
        max_track_y = track_geom["max_y_offset"]

        track_x_value = clamp(
            self.temp_settings.get("track_info_x_offset", 0),
            min_track_x,
            max_track_x,
        )
        track_y_value = clamp(
            self.temp_settings.get("track_info_y_offset", 0),
            min_track_y,
            max_track_y,
        )

        self.slider_track_x.blockSignals(True)
        self.spin_track_x.blockSignals(True)
        self.slider_track_y.blockSignals(True)
        self.spin_track_y.blockSignals(True)

        self.slider_track_x.setRange(min_track_x, max_track_x)
        self.spin_track_x.setRange(min_track_x, max_track_x)
        self.slider_track_y.setRange(min_track_y, max_track_y)
        self.spin_track_y.setRange(min_track_y, max_track_y)

        self.slider_track_x.setValue(track_x_value)
        self.spin_track_x.setValue(track_x_value)
        self.slider_track_y.setValue(track_y_value)
        self.spin_track_y.setValue(track_y_value)

        self.slider_track_x.blockSignals(False)
        self.spin_track_x.blockSignals(False)
        self.slider_track_y.blockSignals(False)
        self.spin_track_y.blockSignals(False)

        self.temp_settings["track_info_x_offset"] = track_x_value
        self.temp_settings["track_info_y_offset"] = track_y_value

    def request_edit_position(self):
        """Save and close; the caller then enters drag-to-position mode."""
        self.edit_position_requested = True
        self.accept()

    def accept(self):
        # Keep state that the tray/hotkeys may have changed while the dialog
        # was open.
        for key in RUNTIME_STATE_KEYS:
            if key in self.manager.settings:
                self.temp_settings[key] = self.manager.settings[key]
        self.manager.settings = self.temp_settings

        if self.check_autostart.isChecked() != autostart.is_enabled():
            autostart.set_enabled(self.check_autostart.isChecked())
        self.manager.save()
        self.settings_changed.emit(self.manager.settings)
        super().accept()
