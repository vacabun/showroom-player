from PySide6.QtWidgets import (
    QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
    QLineEdit, QPushButton, QLabel, QComboBox, QTextEdit, QGroupBox,
    QSlider, QSplitter, QSizePolicy, QStatusBar, QListWidget, QListWidgetItem,
    QDialog,
)
from PySide6.QtCore import Qt
from PySide6.QtGui import QFont
from PySide6.QtMultimedia import QMediaPlayer, QAudioOutput
from PySide6.QtMultimediaWidgets import QVideoWidget
from PySide6.QtCore import QUrl

from .api import session
from .dialogs import LoginDialog
from .threads import (
    LiveCommentsThread,
    LiveRoomsThread,
    LoadRoomThread,
    LoadUsernameThread,
    SendCommentThread,
)


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle('Showroom Player')
        self.setMinimumSize(960, 620)
        self._streams = []       # [(label, url)]
        self._live_id = 0
        self._logged_in = False
        self._live_comments_thread = None
        self._load_room_thread = None
        self._live_rooms_thread = None
        self._build_ui()
        self._setup_player()
        self._refresh_rooms()

    # ── UI ──────────────────────────────────────────────────────────────────

    def _build_ui(self):
        central = QWidget()
        self.setCentralWidget(central)
        root = QVBoxLayout(central)
        root.setContentsMargins(10, 10, 10, 6)
        root.setSpacing(8)

        # Top bar: room input
        top = QHBoxLayout()
        self.url_input = QLineEdit()
        self.url_input.setPlaceholderText('Room key or https://www.showroom-live.com/r/...')
        self.url_input.returnPressed.connect(self.load_room)
        self.load_btn = QPushButton('Open')
        self.load_btn.setFixedWidth(70)
        self.load_btn.clicked.connect(self.load_room)
        self.login_btn = QPushButton('Sign In')
        self.login_btn.setFixedWidth(70)
        self.login_btn.clicked.connect(self._open_login)
        self.logout_btn = QPushButton('Sign Out')
        self.logout_btn.setFixedWidth(70)
        self.logout_btn.setEnabled(False)
        self.logout_btn.clicked.connect(self._logout)
        top.addWidget(self.url_input)
        top.addWidget(self.load_btn)
        top.addWidget(self.login_btn)
        top.addWidget(self.logout_btn)
        root.addLayout(top)

        # Outer splitter: rooms | (video + comments)
        outer_splitter = QSplitter(Qt.Orientation.Horizontal)

        # ── Rooms panel ──
        rooms_box = QGroupBox('Live Rooms')
        rooms_box.setMinimumWidth(160)
        rooms_layout = QVBoxLayout(rooms_box)
        rooms_layout.setContentsMargins(6, 6, 6, 6)
        rooms_layout.setSpacing(4)

        self.refresh_btn = QPushButton('Reload')
        self.refresh_btn.clicked.connect(self._refresh_rooms)
        rooms_layout.addWidget(self.refresh_btn)

        self.rooms_list = QListWidget()
        self.rooms_list.itemDoubleClicked.connect(self._on_room_double_clicked)
        rooms_layout.addWidget(self.rooms_list)
        outer_splitter.addWidget(rooms_box)

        # ── Inner splitter: video+controls | comments ──
        inner_splitter = QSplitter(Qt.Orientation.Horizontal)

        left = QWidget()
        left_layout = QVBoxLayout(left)
        left_layout.setContentsMargins(0, 0, 0, 0)
        left_layout.setSpacing(6)

        self.video_widget = QVideoWidget()
        self.video_widget.setStyleSheet('background: black;')
        self.video_widget.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        left_layout.addWidget(self.video_widget, stretch=1)
        left_layout.addWidget(self._build_controls())
        inner_splitter.addWidget(left)

        comment_box = QGroupBox('Live Chat')
        comment_box.setMinimumWidth(200)
        cl = QVBoxLayout(comment_box)
        cl.setContentsMargins(6, 6, 6, 6)
        cl.setSpacing(4)
        self.comment_view = QTextEdit()
        self.comment_view.setReadOnly(True)
        self.comment_view.setFont(QFont('Menlo', 10))
        cl.addWidget(self.comment_view)

        comment_input_row = QHBoxLayout()
        self.comment_input = QLineEdit()
        self.comment_input.setPlaceholderText('Sign in to join the chat...')
        self.comment_input.setEnabled(False)
        self.comment_input.returnPressed.connect(self._post_comment)
        self.send_btn = QPushButton('Send')
        self.send_btn.setFixedWidth(50)
        self.send_btn.setEnabled(False)
        self.send_btn.clicked.connect(self._post_comment)
        comment_input_row.addWidget(self.comment_input)
        comment_input_row.addWidget(self.send_btn)
        cl.addLayout(comment_input_row)
        inner_splitter.addWidget(comment_box)

        inner_splitter.setStretchFactor(0, 3)
        inner_splitter.setStretchFactor(1, 1)

        outer_splitter.addWidget(inner_splitter)
        outer_splitter.setStretchFactor(0, 1)
        outer_splitter.setStretchFactor(1, 5)
        root.addWidget(outer_splitter)

        self.status_bar = QStatusBar()
        self.setStatusBar(self.status_bar)

    def _build_controls(self):
        bar = QWidget()
        layout = QHBoxLayout(bar)
        layout.setContentsMargins(4, 0, 4, 0)
        layout.setSpacing(8)

        self.play_btn = QPushButton('▶')
        self.play_btn.setFixedWidth(40)
        self.play_btn.setEnabled(False)
        self.play_btn.clicked.connect(self._toggle_play)

        self.stop_btn = QPushButton('■')
        self.stop_btn.setFixedWidth(40)
        self.stop_btn.setEnabled(False)
        self.stop_btn.clicked.connect(self._stop_player)

        self.mute_btn = QPushButton('🔊')
        self.mute_btn.setFixedWidth(36)
        self.mute_btn.setCheckable(True)
        self.mute_btn.toggled.connect(self._on_mute_toggled)

        self.volume_slider = QSlider(Qt.Orientation.Horizontal)
        self.volume_slider.setRange(0, 100)
        self.volume_slider.setValue(80)
        self.volume_slider.setFixedWidth(90)
        self.volume_slider.valueChanged.connect(self._on_volume_changed)

        sep = QLabel('|')
        sep.setStyleSheet('color: gray')

        self.stream_combo = QComboBox()
        self.stream_combo.setMinimumWidth(240)
        self.stream_combo.currentIndexChanged.connect(self._on_stream_changed)

        layout.addWidget(self.play_btn)
        layout.addWidget(self.stop_btn)
        layout.addWidget(sep)
        layout.addWidget(self.mute_btn)
        layout.addWidget(self.volume_slider)
        layout.addWidget(QLabel('|'))
        layout.addWidget(QLabel('Quality:'))
        layout.addWidget(self.stream_combo)
        layout.addStretch()
        return bar

    # ── Player ──────────────────────────────────────────────────────────────

    def _setup_player(self):
        self.player = QMediaPlayer()
        self.audio_output = QAudioOutput()
        self.audio_output.setVolume(self.volume_slider.value() / 100)
        self.player.setAudioOutput(self.audio_output)
        self.player.setVideoOutput(self.video_widget)
        self.player.playbackStateChanged.connect(self._on_playback_state_changed)
        self.player.errorOccurred.connect(self._on_player_error)

    def _toggle_play(self):
        state = self.player.playbackState()
        if state == QMediaPlayer.PlaybackState.PlayingState:
            self.player.pause()
        elif state == QMediaPlayer.PlaybackState.PausedState:
            self.player.play()
        else:
            self._play_current()

    def _play_current(self):
        idx = self.stream_combo.currentIndex()
        if idx < 0 or idx >= len(self._streams):
            return
        label, url = self._streams[idx]
        self.player.setSource(QUrl(url))
        self.player.play()
        self.status_bar.showMessage(f'Now playing: {label}')

    def _stop_player(self):
        self.player.stop()
        self.player.setSource(QUrl())

    def _on_playback_state_changed(self, state):
        playing = state == QMediaPlayer.PlaybackState.PlayingState
        paused = state == QMediaPlayer.PlaybackState.PausedState
        self.play_btn.setText('⏸' if playing else '▶')
        self.stop_btn.setEnabled(playing or paused)
        if state == QMediaPlayer.PlaybackState.StoppedState:
            self.status_bar.showMessage('Playback stopped.')

    def _on_player_error(self, _error, error_string):
        self.status_bar.showMessage(f'Playback error: {error_string}')

    def _on_mute_toggled(self, muted):
        self.audio_output.setMuted(muted)
        self.mute_btn.setText('🔇' if muted else '🔊')

    def _on_volume_changed(self, value):
        self.audio_output.setVolume(value / 100)

    # ── Stream combo ────────────────────────────────────────────────────────

    def _on_stream_changed(self, idx):
        if idx < 0 or idx >= len(self._streams):
            self.play_btn.setEnabled(False)
            return
        self.play_btn.setEnabled(True)
        if self.player.playbackState() == QMediaPlayer.PlaybackState.PlayingState:
            self._play_current()

    # ── Room Loading ─────────────────────────────────────────────────────────

    def load_room(self):
        room_input = self.url_input.text().strip()
        if not room_input:
            return
        self._stop_player()
        self._stop_comments()
        self.stream_combo.clear()
        self._streams = []
        self.comment_view.clear()
        self.play_btn.setEnabled(False)
        self.stop_btn.setEnabled(False)
        self.load_btn.setEnabled(False)
        self.status_bar.showMessage('Opening room...')

        self._load_room_thread = LoadRoomThread(room_input)
        self._load_room_thread.status.connect(self.status_bar.showMessage)
        self._load_room_thread.streams_ready.connect(self._on_streams_ready)
        self._load_room_thread.error.connect(self._on_room_load_error)
        self._load_room_thread.finished.connect(lambda: self.load_btn.setEnabled(True))
        self._load_room_thread.start()

    def _on_streams_ready(self, streams, room_name, room_id, live_id):
        self._live_id = live_id
        self._streams = streams
        self.stream_combo.blockSignals(True)
        for label, _url in streams:
            self.stream_combo.addItem(label)
        self.stream_combo.blockSignals(False)

        if streams:
            self.stream_combo.setCurrentIndex(0)
            self.play_btn.setEnabled(True)
            self._play_current()

        self.setWindowTitle(f'Showroom Player - {room_name}')
        self.status_bar.showMessage(f'{room_name}  ·  {len(streams)} quality options')
        self._start_comments(room_id)

    def _on_room_load_error(self, msg):
        self.status_bar.showMessage(f'Error: {msg}')

    # ── Login ────────────────────────────────────────────────────────────────

    def _open_login(self):
        dlg = LoginDialog(self)
        if dlg.exec() == QDialog.DialogCode.Accepted:
            self._logged_in = True
            self.login_btn.setEnabled(False)
            self.logout_btn.setEnabled(True)
            self.comment_input.setEnabled(True)
            self.comment_input.setPlaceholderText('Type a message...')
            self.send_btn.setEnabled(True)
            self.status_bar.showMessage('Signed in. Loading profile...')
            self._username_thread = LoadUsernameThread()
            self._username_thread.done.connect(self._on_username_fetched)
            self._username_thread.start()

    def _on_username_fetched(self, name):
        self.login_btn.setText(name)
        self.status_bar.showMessage(f'Signed in as {name}')

    def _logout(self):
        session.cookies.clear()
        self._logged_in = False
        self.login_btn.setText('Sign In')
        self.login_btn.setEnabled(True)
        self.logout_btn.setEnabled(False)
        self.comment_input.setEnabled(False)
        self.comment_input.setPlaceholderText('Sign in to join the chat...')
        self.send_btn.setEnabled(False)
        self.status_bar.showMessage('Signed out.')

    # ── Comments ─────────────────────────────────────────────────────────────

    def _post_comment(self):
        text = self.comment_input.text().strip()
        if not text or not self._live_id:
            return
        self.send_btn.setEnabled(False)
        self.comment_input.setEnabled(False)
        t = SendCommentThread(self._live_id, text)
        t.success.connect(self._on_comment_sent)
        t.error.connect(lambda msg: self.status_bar.showMessage(f'Chat error: {msg}'))
        t.finished.connect(lambda: (
            self.send_btn.setEnabled(True),
            self.comment_input.setEnabled(True),
        ))
        t.start()
        self._post_thread = t

    def _on_comment_sent(self):
        self.comment_input.clear()
        self.status_bar.showMessage('Message sent.')

    def _start_comments(self, room_id):
        self._live_comments_thread = LiveCommentsThread(room_id)
        self._live_comments_thread.new_comments.connect(self._on_new_comments)
        self._live_comments_thread.error.connect(
            lambda msg: self.comment_view.append(f'<i style="color:gray">[error: {msg}]</i>')
        )
        self._live_comments_thread.start()

    def _stop_comments(self):
        if self._live_comments_thread is not None:
            self._live_comments_thread.stop()
            self._live_comments_thread.wait()
            self._live_comments_thread = None

    def _on_new_comments(self, comments):
        for c in comments:
            name = c.get('name', '?')
            text = c.get('comment', '')
            self.comment_view.append(f'<b>{name}</b>: {text}')
        sb = self.comment_view.verticalScrollBar()
        sb.setValue(sb.maximum())

    # ── Rooms ────────────────────────────────────────────────────────────────

    def _refresh_rooms(self):
        self.refresh_btn.setEnabled(False)
        self.rooms_list.clear()
        self.status_bar.showMessage('Loading live rooms...')
        self._live_rooms_thread = LiveRoomsThread()
        self._live_rooms_thread.rooms_ready.connect(self._on_rooms_ready)
        self._live_rooms_thread.error.connect(lambda e: self.status_bar.showMessage(f'Rooms error: {e}'))
        self._live_rooms_thread.finished.connect(lambda: self.refresh_btn.setEnabled(True))
        self._live_rooms_thread.start()

    def _on_rooms_ready(self, rooms):
        self.rooms_list.clear()
        for key, name, viewers in rooms:
            item = QListWidgetItem(f'{name}\n{viewers:,} watching')
            item.setData(Qt.ItemDataRole.UserRole, key)
            self.rooms_list.addItem(item)
        self.status_bar.showMessage(f'{len(rooms)} live rooms')

    def _on_room_double_clicked(self, item):
        key = item.data(Qt.ItemDataRole.UserRole)
        self.url_input.setText(key)
        self.load_room()

    # ── Lifecycle ────────────────────────────────────────────────────────────

    def closeEvent(self, event):
        self._stop_comments()
        self.player.stop()
        event.accept()
