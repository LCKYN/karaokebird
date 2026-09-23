"""Start with Windows, via the per-user Run registry key."""

import logging
import os
import sys

RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"
VALUE_NAME = "KaraokeBird"


def launch_command():
    """Command line that starts this copy of KaraokeBird."""
    if getattr(sys, "frozen", False):
        return f'"{sys.executable}"'
    # From source: pythonw.exe (no console window) running main.py.
    python_dir = os.path.dirname(sys.executable)
    pythonw = os.path.join(python_dir, "pythonw.exe")
    if not os.path.exists(pythonw):
        pythonw = sys.executable
    main_py = os.path.join(os.path.dirname(os.path.abspath(__file__)), "main.py")
    return f'"{pythonw}" "{main_py}"'


def is_enabled():
    try:
        import winreg

        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY) as key:
            winreg.QueryValueEx(key, VALUE_NAME)
        return True
    except FileNotFoundError:
        return False
    except Exception:
        logging.exception("Error reading the startup registry entry")
        return False


def set_enabled(enabled):
    """Add or remove the Run entry. Returns True on success."""
    try:
        import winreg

        with winreg.OpenKey(
            winreg.HKEY_CURRENT_USER, RUN_KEY, 0, winreg.KEY_SET_VALUE
        ) as key:
            if enabled:
                winreg.SetValueEx(key, VALUE_NAME, 0, winreg.REG_SZ, launch_command())
            else:
                try:
                    winreg.DeleteValue(key, VALUE_NAME)
                except FileNotFoundError:
                    pass
        return True
    except Exception:
        logging.exception("Error updating the startup registry entry")
        return False
