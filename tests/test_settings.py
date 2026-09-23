import json

import pytest
from PyQt6.QtCore import QRect

import settings_ui
from settings_ui import (
    DEFAULT_SETTINGS,
    OVERLAY_MIN_HEIGHT,
    SettingsManager,
    compute_overlay_geometry,
    css_color,
    get_line_height,
    get_overlay_height,
    get_overlay_position_bounds,
    normalize_color,
    validate_settings,
)

SCREEN = QRect(0, 0, 1920, 1080)


# --- Geometry ---


def test_overlay_height_has_a_minimum():
    assert get_overlay_height(DEFAULT_SETTINGS) == OVERLAY_MIN_HEIGHT


def test_overlay_height_grows_with_context_lines():
    settings = dict(
        DEFAULT_SETTINGS,
        num_history_lines=5,
        num_future_lines=5,
        font_size_normal=30,
        font_size_highlight=48,
    )
    height = get_overlay_height(settings)
    content = get_line_height(48) + 10 * get_line_height(30)
    assert height > OVERLAY_MIN_HEIGHT
    assert height >= content


def test_overlay_geometry_uses_dynamic_height():
    settings = dict(DEFAULT_SETTINGS, num_history_lines=5, num_future_lines=5,
                    font_size_normal=30)
    geom = compute_overlay_geometry(settings, SCREEN)
    height = get_overlay_height(settings)
    assert geom["height"] == height
    # Offset 0 puts the overlay's bottom edge on the screen's bottom edge.
    assert geom["y"] + geom["height"] == SCREEN.height()


def test_overlay_bounds_depend_on_height():
    settings = dict(DEFAULT_SETTINGS, num_history_lines=5, num_future_lines=5,
                    font_size_normal=30)
    height = get_overlay_height(settings)
    bounds = get_overlay_position_bounds(settings, SCREEN)
    assert bounds["min_y_offset"] == -(height // 2)
    assert bounds["max_y_offset"] == SCREEN.height() - height // 2


def test_overlay_offsets_are_clamped():
    settings = dict(DEFAULT_SETTINGS, window_y_offset=99999, window_x_offset=-99999)
    geom = compute_overlay_geometry(settings, SCREEN)
    assert geom["y"] == SCREEN.height() - geom["height"] - geom["max_y_offset"]
    center_x = (SCREEN.width() - geom["width"]) // 2
    assert geom["x"] == center_x + geom["min_x_offset"]


# --- Validation ---


def test_validate_keeps_well_typed_values():
    settings, rejected = validate_settings(
        {"font_size_normal": 18, "highlight_color": "#ff0000"}, DEFAULT_SETTINGS
    )
    assert rejected == []
    assert settings["font_size_normal"] == 18
    assert settings["highlight_color"] == "#ff0000"


def test_validate_rejects_wrong_types():
    settings, rejected = validate_settings(
        {"font_size_normal": "14", "stroke_enabled_highlight": 1}, DEFAULT_SETTINGS
    )
    assert sorted(rejected) == ["font_size_normal", "stroke_enabled_highlight"]
    assert settings["font_size_normal"] == DEFAULT_SETTINGS["font_size_normal"]
    assert settings["stroke_enabled_highlight"] is True


def test_validate_bool_is_not_an_int():
    settings, rejected = validate_settings({"window_y_offset": True}, DEFAULT_SETTINGS)
    assert rejected == ["window_y_offset"]
    assert settings["window_y_offset"] == DEFAULT_SETTINGS["window_y_offset"]


def test_validate_int_and_float_are_compatible():
    settings, rejected = validate_settings({"window_y_offset": 12.6}, DEFAULT_SETTINGS)
    assert rejected == []
    assert settings["window_y_offset"] == 13
    assert isinstance(settings["window_y_offset"], int)


def test_validate_ignores_unknown_keys_and_nulls():
    settings, rejected = validate_settings(
        {"bogus": 1, "normal_color": None}, DEFAULT_SETTINGS
    )
    assert sorted(rejected) == ["bogus", "normal_color"]
    assert "bogus" not in settings
    assert settings["normal_color"] == DEFAULT_SETTINGS["normal_color"]


def test_validate_rejects_non_dict_root():
    settings, rejected = validate_settings([1, 2], DEFAULT_SETTINGS)
    assert rejected == ["<root>"]
    assert settings == DEFAULT_SETTINGS


# --- SettingsManager load/save ---


@pytest.fixture
def settings_file(tmp_path, monkeypatch):
    path = tmp_path / "settings.json"
    monkeypatch.setattr(settings_ui, "SETTINGS_FILE", str(path))
    return path


def test_manager_load_rejects_bad_values(settings_file, caplog):
    settings_file.write_text(
        json.dumps({"font_size_normal": "14", "font_size_highlight": 30}),
        encoding="utf-8",
    )
    manager = SettingsManager()
    assert manager.settings["font_size_normal"] == DEFAULT_SETTINGS["font_size_normal"]
    assert manager.settings["font_size_highlight"] == 30
    assert "font_size_normal" in caplog.text


def test_manager_load_survives_corrupt_json(settings_file):
    settings_file.write_text("{not json", encoding="utf-8")
    manager = SettingsManager()
    assert manager.settings == DEFAULT_SETTINGS


def test_manager_save_round_trip_is_atomic(settings_file):
    manager = SettingsManager()
    manager.set("font_family", "Ünïcødé Sans")
    manager.save()
    assert not (settings_file.parent / "settings.json.tmp").exists()
    assert SettingsManager().settings["font_family"] == "Ünïcødé Sans"


# --- Colors ---


@pytest.mark.parametrize(
    "value, expected",
    [
        ("rgba(0, 0, 0, 100)", "#64000000"),
        ("rgba(255,128,0,255)", "#ffff8000"),
        ("rgba(10, 20, 30, 0.5)", "#800a141e"),
        ("rgb(1, 2, 3)", "#ff010203"),
        ("#AABBCC", "#aabbcc"),
        ("#80AABBCC", "#80aabbcc"),
    ],
)
def test_normalize_color(value, expected):
    assert normalize_color(value, "#00000000") == expected


@pytest.mark.parametrize("value", ["", "black", "#12345", "rgba(1,2)", None, 5])
def test_normalize_color_falls_back(value):
    assert normalize_color(value, "#11223344") == "#11223344"


def test_css_color():
    assert css_color("#64000000") == "rgba(0, 0, 0, 100)"
    assert css_color("#aabbcc") == "#aabbcc"


def test_manager_migrates_css_background_color(settings_file):
    settings_file.write_text(
        json.dumps({"background_color": "rgba(0, 0, 0, 100)"}), encoding="utf-8"
    )
    assert SettingsManager().settings["background_color"] == "#64000000"
