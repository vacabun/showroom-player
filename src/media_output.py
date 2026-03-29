import re
from datetime import datetime
from pathlib import Path

from PySide6.QtCore import QStandardPaths


def downloads_dir():
    path = QStandardPaths.writableLocation(QStandardPaths.StandardLocation.DownloadLocation)
    if path:
        return Path(path)
    return Path.home() / 'Downloads'


def sanitize_media_name(name, fallback='showroom-room'):
    cleaned = re.sub(r'[<>:"/\\|?*\x00-\x1f]+', '_', str(name or '')).strip(' .')
    return cleaned or fallback


def build_timestamped_download_path(name, extension, suffix=''):
    target_dir = downloads_dir()
    target_dir.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
    normalized_extension = str(extension or '').lstrip('.') or 'dat'
    base_name = f'{sanitize_media_name(name)}_{timestamp}{suffix}'
    path = target_dir / f'{base_name}.{normalized_extension}'
    counter = 1
    while path.exists():
        path = target_dir / f'{base_name}_{counter:02d}.{normalized_extension}'
        counter += 1
    return path
