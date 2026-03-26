import time
from PySide6.QtCore import QThread, Signal

from .api import (
    fetch_current_user,
    get_all_streams,
    get_raw_stream_list,
    get_roomid_by_room_url_key,
    parse_room_url_key,
    session,
)


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

            if not is_live:
                self.error.emit(f'"{room_name}" is offline')
                return

            self.status.emit('Loading streams...')
            url_list = get_raw_stream_list(room_id)
            streams = get_all_streams(url_list)
            self.streams_ready.emit(streams, room_name, room_id, live_id)
        except Exception as e:
            self.error.emit(str(e))


class LiveCommentsThread(QThread):
    new_comments = Signal(list)
    error = Signal(str)

    def __init__(self, room_id):
        super().__init__()
        self.room_id = room_id
        self._running = True
        self._seen_ids = set()

    @staticmethod
    def _comment_id(c):
        cid = c.get('id')
        if cid is not None:
            return cid
        return f"{c.get('created_at')}_{c.get('name')}_{c.get('comment', '')[:32]}"

    def run(self):
        while self._running:
            try:
                response = session.get(
                    f'https://www.showroom-live.com/api/live/comment_log?room_id={self.room_id}',
                    timeout=10,
                )
                data = response.json()
                comments = data.get('comment_log', [])
                comments = sorted(comments, key=lambda c: c.get('created_at', 0))
                new = [c for c in comments if self._comment_id(c) not in self._seen_ids]
                if new:
                    for c in new:
                        self._seen_ids.add(self._comment_id(c))
                    self.new_comments.emit(new)
            except Exception as e:
                self.error.emit(str(e))
            for _ in range(50):
                if not self._running:
                    break
                time.sleep(0.1)

    def stop(self):
        self._running = False


class LiveRoomsThread(QThread):
    rooms_ready = Signal(list)
    error = Signal(str)

    def run(self):
        try:
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
                    rooms.append((key, name, viewers))
            rooms.sort(key=lambda x: x[2], reverse=True)
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
            csrf_token = resp.json().get('csrf_token', '')
            if not csrf_token:
                self.error.emit('Failed to get CSRF token')
                return
            resp = session.post(
                'https://www.showroom-live.com/api/live/post_live_comment',
                data={
                    'csrf_token': csrf_token,
                    'live_id': self.live_id,
                    'comment': self.comment,
                    'recommend_comment_id': '',
                    'comment_type': '',
                    'is_delay': '0',
                },
                timeout=10,
            )
            data = resp.json()
            if data.get('ok') == 1:
                self.success.emit()
            else:
                msg = data.get('message') or data.get('error') or 'Failed to post comment'
                self.error.emit(msg)
        except Exception as e:
            self.error.emit(str(e))


class LoadCurrentUserThread(QThread):
    done = Signal(dict, bool)

    def run(self):
        try:
            data = fetch_current_user()
            self.done.emit(data, bool(data.get('is_login')))
        except Exception:
            self.done.emit({}, False)
