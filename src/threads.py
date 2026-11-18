from collections import OrderedDict
from threading import Event
import time
from PySide6.QtCore import QThread, Signal

from .api import (
    fetch_latest_release_info,
    fetch_followed_rooms,
    fetch_current_user,
    get_all_streams,
    get_raw_stream_list,
    is_version_newer,
    get_roomid_by_room_url_key,
    parse_room_url_key,
    session,
)
from .app_meta import APP_VERSION, LATEST_RELEASE_URL


class LoadRoomThread(QThread):
    status = Signal(str)
    streams_ready = Signal(list, str, int, int)  # (streams, room_name, room_id, live_id)
    error = Signal(str)

    def __init__(self, room_input):
        super().__init__()
        self.room_input = room_input

    def run(self):
        try:
            room_url_key = parse_room_url_key(self.room_input)
            self.status.emit('Loading room info...')
            room_id, room_name, is_live, live_id = get_roomid_by_room_url_key(room_url_key)

            if self.isInterruptionRequested():
                return

            if not is_live:
                self.error.emit(f'"{room_name}" is offline')
                return

            self.status.emit('Loading streams...')
            url_list = get_raw_stream_list(room_id)
            if self.isInterruptionRequested():
                return
            streams = get_all_streams(url_list, interrupted=self.isInterruptionRequested)
            if not self.isInterruptionRequested():
                self.streams_ready.emit(streams, room_name, room_id, live_id)
        except Exception as e:
            if not self.isInterruptionRequested():
                self.error.emit(str(e))


class LiveCommentsThread(QThread):
    POLL_INTERVAL = 1
    MAX_BACKOFF = 30
    MAX_SEEN = 4096

    new_comments = Signal(list)
    reconnecting = Signal(bool)

    def __init__(self, room_id):
        super().__init__()
        self.room_id = room_id
        self._stopped = Event()
        self._wake = Event()
        self._seen = OrderedDict()

    @staticmethod
    def _comment_id(comment):
        if comment.get('id') is not None:
            return ('id', str(comment['id']))
        return ('fallback', str(comment.get('created_at')), str(comment.get('user_id')),
                str(comment.get('name')), str(comment.get('comment')))

    def _collect_new(self, comments):
        new = []
        for comment in sorted(comments, key=lambda item: int(item.get('created_at') or 0)):
            key = self._comment_id(comment)
            if key not in self._seen:
                new.append(comment)
            self._seen[key] = None
            self._seen.move_to_end(key)
        while len(self._seen) > self.MAX_SEEN:
            self._seen.popitem(last=False)
        return new

    def run(self):
        failures = 0
        while not self._stopped.is_set():
            self._wake.clear()
            started = time.monotonic()
            try:
                response = session.get(
                    'https://www.showroom-live.com/api/live/comment_log',
                    params={'room_id': self.room_id},
                    timeout=10,
                )
                response.raise_for_status()
                comments = response.json()['comment_log']
                new = self._collect_new(comments)
                if failures:
                    self.reconnecting.emit(False)
                failures = 0
                if new and not self._stopped.is_set():
                    self.new_comments.emit(new)
                delay = max(0, self.POLL_INTERVAL - (time.monotonic() - started))
            except (ValueError, TypeError, KeyError, AttributeError, OSError):
                failures = min(failures + 1, 5)
                if failures == 1:
                    self.reconnecting.emit(True)
                delay = min(2 ** failures, self.MAX_BACKOFF)
            if not self._stopped.is_set():
                self._wake.wait(delay)

    def refresh(self):
        self._wake.set()

    def stop(self):
        self._stopped.set()
        self._wake.set()


class LiveRoomsThread(QThread):
    rooms_ready = Signal(list)
    error = Signal(str)

    def __init__(self, followed_only=False):
        super().__init__()
        self.followed_only = followed_only

    def run(self):
        try:
            if self.followed_only:
                self.rooms_ready.emit(fetch_followed_rooms())
                return

            url = 'https://www.showroom-live.com/api/live/onlives'
            response = session.get(url, timeout=10)
            data = response.json()
            rooms = []
            seen = set()
            for genre in data.get('onlives', []):
                for live in genre.get('lives', []):
                    key = live.get('room_url_key', '')
                    if not key or key in seen:
                        continue
                    seen.add(key)
                    name = live.get('main_name', key)
                    viewers = live.get('view_num', 0)
                    rooms.append({
                        'key': key,
                        'name': name,
                        'viewers': viewers,
                        'is_online': True,
                        'next_live': '',
                    })
            rooms.sort(key=lambda item: item.get('viewers', 0), reverse=True)
            self.rooms_ready.emit(rooms)
        except Exception as e:
            self.error.emit(str(e))


class SendCommentThread(QThread):
    success = Signal()
    error = Signal(str)

    def __init__(self, live_id, comment):
        super().__init__()
        self.live_id = live_id
        self.comment = comment

    def run(self):
        try:
            resp = session.get('https://www.showroom-live.com/api/csrf_token', timeout=10)
            resp.raise_for_status()
            csrf_token = resp.json().get('csrf_token', '')
            if not csrf_token:
                self.error.emit('Failed to get CSRF token')
                return
            resp = session.post(
                'https://www.showroom-live.com/api/live/post_live_comment',
                json={
                    'csrf_token': csrf_token,
                    'live_id': self.live_id,
                    'comment': self.comment,
                    'recommend_comment_id': None,
                    'comment_type': None,
                    'is_delay': 0,
                },
                timeout=10,
            )
            try:
                data = resp.json()
            except ValueError:
                resp.raise_for_status()
                raise ValueError('SHOWROOM returned an unreadable response')
            if not resp.ok:
                errors = data.get('errors') if isinstance(data, dict) else None
                if isinstance(errors, list):
                    messages = [
                        str(item.get('message') or item.get('error_type') or '')
                        for item in errors if isinstance(item, dict)
                    ]
                    message = ' / '.join(filter(None, messages))
                else:
                    message = ''
                self.error.emit(message or f'SHOWROOM rejected the comment (HTTP {resp.status_code})')
                return
            if data.get('ok') in (1, '1') or ('comment' in data and not data.get('errors')):
                self.success.emit()
            else:
                msg = data.get('message') or data.get('error') or 'Failed to post comment'
                self.error.emit(str(msg))
        except Exception as e:
            self.error.emit(str(e))


class LoadCurrentUserThread(QThread):
    done = Signal(dict, bool)

    def run(self):
        try:
            data = fetch_current_user()
            self.done.emit(data, data.get('is_login') in (True, 1, '1'))
        except Exception:
            self.done.emit({}, False)


class CheckForUpdatesThread(QThread):
    checked = Signal(dict)

    def run(self):
        try:
            release = fetch_latest_release_info()
            latest_version = release.get('tag_name') or ''
            payload = {
                'ok': True,
                'current_version': APP_VERSION,
                'latest_version': latest_version,
                'release_name': release.get('name') or latest_version,
                'release_url': release.get('html_url') or LATEST_RELEASE_URL,
                'published_at': release.get('published_at') or '',
                'has_update': bool(latest_version) and is_version_newer(latest_version, APP_VERSION),
            }
        except Exception as exc:
            payload = {
                'ok': False,
                'current_version': APP_VERSION,
                'latest_version': '',
                'release_name': '',
                'release_url': LATEST_RELEASE_URL,
                'published_at': '',
                'has_update': False,
                'error': str(exc),
            }
        self.checked.emit(payload)
