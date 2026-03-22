#!/usr/bin/env python3
import sys

from PySide6.QtWidgets import QApplication

from .window import MainWindow


def main():
    app = QApplication(sys.argv)
    app.setStyle('Fusion')
    win = MainWindow()
    win.show()
    sys.exit(app.exec())
