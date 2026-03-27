#!/usr/bin/env python3
import sys

from PySide6.QtWidgets import QApplication

from .api import session
from .branding import app_icon
from .proxy import configure_qt_system_proxy, configure_requests_system_proxy
from .window import MainWindow


def main():
    configure_qt_system_proxy()
    configure_requests_system_proxy(session)
    app = QApplication(sys.argv)
    app.setStyle('Fusion')
    app.setWindowIcon(app_icon())
    win = MainWindow()
    win.show()
    sys.exit(app.exec())
