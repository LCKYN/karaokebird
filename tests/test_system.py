import sys

import pytest

import autostart
from main import friendly_source_name
from settings_ui import APPEARANCE_KEYS, DEFAULT_SETTINGS, apply_preset, make_preset
from updates import is_check_due, is_newer, parse_version

# --- Presets ---


def test_preset_round_trip():
    look = dict(
        DEFAULT_SETTINGS,
        font_family="Arial",
        highlight_color="#ff0000",
        background_enabled=True,
        window_y_offset=123,
    )
    preset = make_preset(look)
    assert set(preset) == set(APPEARANCE_KEYS)
    assert "window_y_offset" not in preset

    target = dict(DEFAULT_SETTINGS, window_y_offset=-50)
    assert apply_preset(target, preset) == []
    assert target["font_family"] == "Arial"
    assert target["highlight_color"] == "#ff0000"
    assert target["background_enabled"] is True
    assert target["window_y_offset"] == -50  # layout untouched


def test_apply_preset_skips_bad_values():
    target = dict(DEFAULT_SETTINGS)
    rejected = apply_preset(
        target,
        {
            "font_size_normal": "big",
            "window_y_offset": 10,
            "stroke_enabled_context": True,
            "background_color": "rgba(0, 0, 0, 100)",
        },
    )
    assert sorted(rejected) == ["font_size_normal", "window_y_offset"]
    assert target["font_size_normal"] == DEFAULT_SETTINGS["font_size_normal"]
    assert target["window_y_offset"] == DEFAULT_SETTINGS["window_y_offset"]
    assert target["stroke_enabled_context"] is True
    assert target["background_color"] == "#64000000"
    assert apply_preset(target, "nope") == ["<preset>"]


# --- Update check ---


@pytest.mark.parametrize(
    "text, expected",
    [
        ("v1.2.3", (1, 2, 3)),
        ("1.1.2.0", (1, 1, 2)),
        ("v2", (2,)),
        ("v1.0.0.0", (1,)),
        ("release-3.4", (3, 4)),
        ("", None),
        (None, None),
    ],
)
def test_parse_version(text, expected):
    assert parse_version(text) == expected


def test_is_newer():
    assert is_newer("v1.2.0.0", "1.1.2.0")
    assert is_newer("v1.1.3", "1.1.2.0")
    assert not is_newer("v1.1.2", "1.1.2.0")
    assert not is_newer("v1.0.0.0", "1.1.2.0")
    assert is_newer("v0.0.1", "0.0.0")
    assert not is_newer("garbage", "1.0")


def test_is_check_due():
    day = 24 * 3600
    assert is_check_due(0.0, 1_000_000)
    assert not is_check_due(1_000_000, 1_000_000 + day - 1)
    assert is_check_due(1_000_000, 1_000_000 + day)


# --- Sources ---


@pytest.mark.parametrize(
    "app_id, name",
    [
        ("Spotify.exe", "Spotify"),
        ("Chrome", "Chrome"),
        ("MSEdge", "Edge"),
        ("308046B0AF4A39CB", "Firefox"),
        ("AppleInc.AppleMusicWin_nzyj5cx40ttba!App", "Apple Music"),
        ("vlc.exe", "vlc.exe"),
        ("", "Unknown"),
    ],
)
def test_friendly_source_name(app_id, name):
    assert friendly_source_name(app_id) == name


# --- Start with Windows ---


def test_autostart_command_from_source(monkeypatch):
    monkeypatch.delattr(sys, "frozen", raising=False)
    command = autostart.launch_command()
    assert command.endswith('main.py"')
    assert command.startswith('"')


def test_autostart_command_frozen(monkeypatch):
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "executable", r"C:\Apps\KaraokeBird.exe")
    assert autostart.launch_command() == r'"C:\Apps\KaraokeBird.exe"'
