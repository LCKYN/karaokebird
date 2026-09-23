import pytest
from PyQt6.QtCore import QRect

from settings_ui import (
    DEFAULT_SETTINGS,
    compute_overlay_geometry,
    compute_track_info_geometry,
    overlay_offsets_from_pos,
    track_info_offsets_from_pos,
)

SCREENS = [QRect(0, 0, 1920, 1080), QRect(1920, -200, 2560, 1440)]


@pytest.mark.parametrize("screen", SCREENS)
@pytest.mark.parametrize("x_off, y_off", [(0, 0), (-300, 120), (450, -150), (10, 800)])
def test_overlay_offsets_round_trip(screen, x_off, y_off):
    settings = dict(DEFAULT_SETTINGS, window_x_offset=x_off, window_y_offset=y_off)
    geom = compute_overlay_geometry(settings, screen)
    offsets = overlay_offsets_from_pos(settings, screen, geom["x"], geom["y"])
    assert offsets == {"window_x_offset": x_off, "window_y_offset": y_off}


def test_overlay_offsets_are_clamped():
    screen = SCREENS[0]
    settings = dict(DEFAULT_SETTINGS)
    offsets = overlay_offsets_from_pos(settings, screen, -100_000, 100_000)
    geom = compute_overlay_geometry(settings, screen)
    assert offsets["window_x_offset"] == geom["min_x_offset"]
    assert offsets["window_y_offset"] == geom["min_y_offset"]


@pytest.mark.parametrize("screen", SCREENS)
@pytest.mark.parametrize("x_off, y_off", [(0, 0), (-80, -50), (60, 80)])
def test_track_info_offsets_round_trip(screen, x_off, y_off):
    settings = dict(
        DEFAULT_SETTINGS,
        window_y_offset=300,
        track_info_x_offset=x_off,
        track_info_y_offset=y_off,
    )
    geom = compute_track_info_geometry(settings, screen)
    offsets = track_info_offsets_from_pos(settings, screen, geom["x"], geom["y"])
    assert offsets == {"track_info_x_offset": x_off, "track_info_y_offset": y_off}


def test_track_info_offsets_are_clamped():
    screen = SCREENS[0]
    settings = dict(DEFAULT_SETTINGS, window_y_offset=300)
    offsets = track_info_offsets_from_pos(settings, screen, 100_000, -100_000)
    geom = compute_track_info_geometry(settings, screen)
    assert offsets["track_info_x_offset"] == geom["max_x_offset"]
    assert offsets["track_info_y_offset"] == geom["min_y_offset"]
