from pathlib import Path

from PySide6.QtGui import QIcon


def logo_path():
    return Path(__file__).resolve().parent.parent / 'assets' / 'app-icon.png'


def app_icon():
    return QIcon(str(logo_path()))
