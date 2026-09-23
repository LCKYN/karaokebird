import keyboard
import pytest
from PyQt6.QtCore import Qt
from PyQt6.QtGui import QKeySequence

from main import _normalize_hotkey

PORTABLE = QKeySequence.SequenceFormat.PortableText
CTRL = Qt.KeyboardModifier.ControlModifier.value
SHIFT = Qt.KeyboardModifier.ShiftModifier.value
META = Qt.KeyboardModifier.MetaModifier.value


def qt_name(*parts):
    combo = 0
    for part in parts:
        combo |= part
    return QKeySequence(combo).toString(PORTABLE)


@pytest.mark.parametrize(
    "key",
    [
        Qt.Key.Key_Delete,
        Qt.Key.Key_Insert,
        Qt.Key.Key_Escape,
        Qt.Key.Key_PageUp,
        Qt.Key.Key_PageDown,
        Qt.Key.Key_Return,
        Qt.Key.Key_Backspace,
        Qt.Key.Key_Space,
        Qt.Key.Key_Print,
        Qt.Key.Key_CapsLock,
        Qt.Key.Key_NumLock,
        Qt.Key.Key_ScrollLock,
        Qt.Key.Key_Home,
        Qt.Key.Key_Left,
        Qt.Key.Key_F5,
        Qt.Key.Key_L,
        Qt.Key.Key_Plus,
        Qt.Key.Key_Minus,
    ],
)
def test_qt_key_names_are_accepted_by_keyboard(key):
    name = qt_name(CTRL, SHIFT, key.value)
    keyboard.parse_hotkey(_normalize_hotkey(name))


def test_meta_maps_to_windows():
    assert _normalize_hotkey("Meta+L") == "windows+l"
    keyboard.parse_hotkey(_normalize_hotkey(qt_name(META, Qt.Key.Key_L.value)))


def test_specific_mappings():
    assert _normalize_hotkey("Ctrl+Del") == "ctrl+delete"
    assert _normalize_hotkey("Ins") == "insert"
    assert _normalize_hotkey("Esc") == "esc"
    assert _normalize_hotkey("Shift+PgUp") == "shift+page up"
    assert _normalize_hotkey("PgDown") == "page down"
    assert _normalize_hotkey("Ctrl++") == "ctrl+plus"
    assert _normalize_hotkey("+") == "plus"
