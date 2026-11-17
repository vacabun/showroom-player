from html import escape

from PySide6.QtCore import QEvent, Qt, QTimer, QUrl, Signal
from PySide6.QtGui import QBrush, QColor, QDesktopServices, QFont, QGuiApplication, QPalette
from PySide6.QtMultimedia import QMediaPlayer
from PySide6.QtMultimediaWidgets import QVideoWidget
from PySide6.QtWidgets import (
    QComboBox,
    QDialog,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMainWindow,
    QMenu,
    QPushButton,
    QSizePolicy,
    QSlider,
    QSplitter,
    QStackedWidget,
    QStatusBar,
    QTextEdit,
    QVBoxLayout,
    QWidget,
    QGridLayout,
)

from .api import clear_session_cookies, load_session_cookies, save_session_cookies
from .app_meta import APP_NAME, APP_VERSION, LATEST_RELEASE_URL
from .branding import app_icon
from .dialogs import LoginDialog, UpdateDialog, UserInfoDialog
from .media_output import build_timestamped_download_path
from .playback_session import PlaybackSession
from .threads import (
    CheckForUpdatesThread,
    LiveCommentsThread,
    LoadCurrentUserThread,
    LiveRoomsThread,
    LoadRoomThread,
    SendCommentThread,
)


def selected_stream(combo, streams):
    index = combo.currentIndex()
    return streams[index] if 0 <= index < len(streams) else None


class MultiRoomTile(QWidget):
    selected = Signal(int)
    recording_notice = Signal(str)
    screenshot_requested = Signal(int)

    def __init__(self, index):
        super().__init__()
        self.index = index
        self.room_key = ''
        self.room_name = ''
        self.room_id = 0
        self.live_id = 0
        self.streams = []
        self._selected = False

        self.setObjectName(f'multiRoomTile{index}')
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(7, 7, 7, 7)
        layout.setSpacing(4)

        header = QHBoxLayout()
        header.setContentsMargins(0, 0, 0, 0)
        self.indicator_label = QLabel()
        self.indicator_label.setFixedSize(10, 10)
        self.title_label = QLabel(f'Slot {index + 1}')
        self.title_label.setStyleSheet('font-weight: 600;')
        self.title_label.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        self.meta_label = QLabel('Empty')
        self.meta_label.setFont(QFont('Menlo', 9))
        self.record_indicator = QLabel()
        self.record_indicator.setFixedSize(10, 10)
        self.shot_btn = QPushButton('Shot')
        self.shot_btn.setFixedWidth(68)
        self.shot_btn.setEnabled(False)
        self.shot_btn.clicked.connect(self._request_screenshot)
        self.record_btn = QPushButton('Start REC')
        self.record_btn.setFixedWidth(88)
        self.record_btn.setEnabled(False)
        self.record_btn.clicked.connect(self._toggle_recording)
        header.addWidget(self.indicator_label)
        header.addSpacing(4)
        header.addWidget(self.title_label, stretch=1)
        header.addWidget(self.meta_label)
        header.addSpacing(8)
        header.addWidget(self.shot_btn)
        header.addWidget(self.record_indicator)
        header.addWidget(self.record_btn)
        layout.addLayout(header)

        self.video_widget = QVideoWidget()
        self.video_widget.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self.video_widget.setMinimumHeight(18)
        layout.addWidget(self.video_widget, stretch=1)

        controls = QHBoxLayout()
        controls.setContentsMargins(0, 0, 0, 0)
        controls.setSpacing(6)
        self.play_btn = QPushButton('▶')
        self.play_btn.setFixedWidth(36)
        self.play_btn.setEnabled(False)
        self.play_btn.clicked.connect(self._toggle_play)
        self.stop_btn = QPushButton('■')
        self.stop_btn.setFixedWidth(36)
        self.stop_btn.setEnabled(False)
        self.stop_btn.clicked.connect(self._stop_player)
        self.stream_combo = QComboBox()
        self.stream_combo.setEnabled(False)
        self.stream_combo.setMinimumWidth(120)
        self.stream_combo.currentIndexChanged.connect(self._on_stream_changed)
        self.volume_slider = QSlider(Qt.Orientation.Horizontal)
        self.volume_slider.setRange(0, 100)
        self.volume_slider.setValue(80)
        self.volume_slider.setFixedWidth(76)
        self.volume_slider.valueChanged.connect(self._on_volume_changed)
        controls.addWidget(self.play_btn)
        controls.addWidget(self.stop_btn)
        controls.addWidget(self.stream_combo, stretch=1)
        controls.addWidget(self.volume_slider)
        layout.addLayout(controls)

        self.status_label = QLabel('Double-click a room on the left to load here.')
        self.status_label.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Fixed)
        layout.addWidget(self.status_label)

        self.playback = PlaybackSession(self.video_widget, self.volume_slider.value() / 100, self)
        self.playback.install_video_menu(
            self.video_widget, lambda: self.room_key if self.room_id else '',
            lambda: selected_stream(self.stream_combo, self.streams),
        )
        self.playback.state_changed.connect(self._on_playback_state_changed)
        self.playback.error_occurred.connect(self._on_player_error)
        self.recorder = self.playback.recorder
        self.recorder.state_changed.connect(self._update_record_ui)
        self.recorder.started.connect(self._on_recording_started)
        self.recorder.stopped.connect(self._on_recording_stopped)
        self.recorder.error.connect(self._on_recording_error)

        for widget in (
            self,
            self.video_widget,
            self.title_label,
            self.meta_label,
            self.status_label,
            self.indicator_label,
            self.record_indicator,
        ):
            widget.installEventFilter(self)

    def eventFilter(self, obj, event):
        if event.type() == QEvent.Type.MouseButtonPress and event.button() == Qt.MouseButton.LeftButton:
            self.selected.emit(self.index)
        return super().eventFilter(obj, event)

    def apply_theme(self, base, window, text, border, highlight):
        panel_bg = MainWindow._mix_colors(base, window, 0.12)
        selected_bg = MainWindow._mix_colors(panel_bg, highlight, 0.12)
        soft_border = MainWindow._color_to_rgba(border, 62)
        active_border = MainWindow._color_to_css(MainWindow._mix_colors(border, highlight, 0.52))
        title_color = MainWindow._color_to_css(text)
        meta_color = MainWindow._color_to_css(MainWindow._mix_colors(text, border, 0.32))
        body_color = MainWindow._color_to_css(MainWindow._mix_colors(text, border, 0.22))
        combo_bg = MainWindow._mix_colors(base, window, 0.18)
        button_bg = MainWindow._mix_colors(combo_bg, highlight, 0.04)
        button_hover = MainWindow._mix_colors(combo_bg, highlight, 0.10)
        recording = self.recorder.is_recording()
        bg = selected_bg if self._selected else panel_bg
        border_color = active_border if self._selected else soft_border
        self.setStyleSheet(
            f'QWidget#{self.objectName()} {{'
            f'background: {MainWindow._color_to_css(bg)};'
            f'border: 1px solid {border_color};'
            'border-radius: 16px;'
            '}'
            f'QWidget#{self.objectName()} QLabel {{'
            'background: transparent;'
            'border: none;'
            f'color: {body_color};'
            '}'
        )
        self.video_widget.setStyleSheet(
            'background: black;'
            f'border: 1px solid {soft_border};'
            'border-radius: 12px;'
        )
        indicator_fill = MainWindow._color_to_css(
            MainWindow._mix_colors(highlight, QColor('#ffffff'), 0.18)
            if self._selected else MainWindow._mix_colors(border, window, 0.28)
        )
        self.indicator_label.setStyleSheet(
            f'background: {indicator_fill};'
            f'border: 1px solid {active_border if self._selected else soft_border};'
            'border-radius: 5px;'
        )
        record_fill = '#e74c3c' if recording else MainWindow._color_to_css(
            MainWindow._mix_colors(border, window, 0.34)
        )
        self.record_indicator.setStyleSheet(
            f'background: {record_fill};'
            f'border: 1px solid {active_border if recording else soft_border};'
            'border-radius: 5px;'
        )
        self.title_label.setStyleSheet(f'color: {title_color}; font-weight: 700;')
        self.meta_label.setStyleSheet(f'color: {meta_color};')
        self.status_label.setStyleSheet(f'color: {body_color};')
        tile_button_style = (
            'QPushButton {'
            f' background: {MainWindow._color_to_css(button_bg)};'
            f' color: {title_color};'
            f' border: 1px solid {soft_border};'
            ' border-radius: 10px;'
            ' padding: 6px 8px;'
            '}'
            'QPushButton:hover {'
            f' background: {MainWindow._color_to_css(button_hover)};'
            '}'
            'QPushButton:disabled {'
            f' color: {MainWindow._color_to_rgba(text, 110)};'
            f' background: {MainWindow._color_to_rgba(combo_bg, 180)};'
            '}'
        )
        record_button_bg = '#b63a30' if recording else MainWindow._color_to_css(button_bg)
        record_button_hover = '#cf473c' if recording else MainWindow._color_to_css(button_hover)
        record_button_text = 'white' if recording else title_color
        record_button_style = (
            'QPushButton {'
            f' background: {record_button_bg};'
            f' color: {record_button_text};'
            f' border: 1px solid {active_border if recording else soft_border};'
            ' border-radius: 10px;'
            ' padding: 6px 8px;'
            '}'
            'QPushButton:hover {'
            f' background: {record_button_hover};'
            '}'
            'QPushButton:disabled {'
            f' color: {MainWindow._color_to_rgba(text, 110)};'
            f' background: {MainWindow._color_to_rgba(combo_bg, 180)};'
            '}'
        )
        combo_style = (
            'QComboBox {'
            f' background: {MainWindow._color_to_css(combo_bg)};'
            f' color: {title_color};'
            f' border: 1px solid {soft_border};'
            ' border-radius: 10px;'
            ' padding: 6px 10px;'
            '}'
            'QComboBox::drop-down {'
            ' border: none;'
            ' width: 18px;'
            '}'
            'QComboBox:disabled {'
            f' color: {MainWindow._color_to_rgba(text, 110)};'
            f' background: {MainWindow._color_to_rgba(combo_bg, 180)};'
            '}'
        )
        self.play_btn.setStyleSheet(tile_button_style)
        self.stop_btn.setStyleSheet(tile_button_style)
        self.shot_btn.setStyleSheet(tile_button_style)
        self.record_btn.setStyleSheet(record_button_style)
        self.stream_combo.setStyleSheet(combo_style)
        groove_color = MainWindow._color_to_css(MainWindow._mix_colors(border, window, 0.30))
        fill_color = MainWindow._color_to_css(MainWindow._mix_colors(highlight, QColor('#ffffff'), 0.10))
        handle_color = MainWindow._color_to_css(MainWindow._mix_colors(highlight, text, 0.12))
        self.volume_slider.setStyleSheet(
            'QSlider::groove:horizontal {'
            f' background: {groove_color};'
            ' height: 4px;'
            ' border-radius: 2px;'
            '}'
            'QSlider::sub-page:horizontal {'
            f' background: {fill_color};'
            ' height: 4px;'
            ' border-radius: 2px;'
            '}'
            'QSlider::handle:horizontal {'
            f' background: {handle_color};'
            ' width: 12px;'
            ' margin: -5px 0;'
            ' border-radius: 6px;'
            '}'
        )

    def set_selected(self, selected):
        self._selected = selected

    def current_frame_image(self):
        return self.playback.screenshot()

    def _request_screenshot(self):
        self.screenshot_requested.emit(self.index)

    def start_loading(self, room_input):
        request = self.playback.reset_room()
        self.room_key = room_input
        self.room_name = ''
        self.room_id = 0
        self.live_id = 0
        self.streams = []
        self.stream_combo.blockSignals(True)
        self.stream_combo.clear()
        self.stream_combo.blockSignals(False)
        self.stream_combo.setEnabled(False)
        self.play_btn.setEnabled(False)
        self.stop_btn.setEnabled(False)
        self.play_btn.setText('▶')
        self.shot_btn.setEnabled(False)
        self.record_btn.setEnabled(False)
        self.title_label.setText(f'Slot {self.index + 1}')
        self.title_label.setToolTip('')
        self.meta_label.setText('Loading')
        self.status_label.setText(f'Opening {room_input}...')
        return request

    def set_loading_status(self, message):
        self.meta_label.setText('Loading')
        self.status_label.setText(message)

    def set_room(self, streams, room_name, room_id, live_id, room_key):
        self.streams = list(streams)
        self.room_name = room_name
        self.room_id = room_id
        self.live_id = live_id
        self.room_key = room_key
        self.title_label.setText(room_name)
        self.title_label.setToolTip(room_name)
        self.meta_label.setText(f'#{self.index + 1}  {len(self.streams)} streams')
        self.stream_combo.blockSignals(True)
        self.stream_combo.clear()
        for label, _url in self.streams:
            self.stream_combo.addItem(label)
        self.stream_combo.blockSignals(False)
        self.stream_combo.setEnabled(bool(self.streams))
        self.play_btn.setEnabled(bool(self.streams))
        self.stop_btn.setEnabled(bool(self.streams))
        self.shot_btn.setEnabled(bool(self.streams))
        self.record_btn.setEnabled(bool(self.streams))
        if self.streams:
            self.stream_combo.setCurrentIndex(0)
            self._play_current()
        else:
            self.status_label.setText('No playable stream found.')

    def set_error(self, message):
        self.playback.reset_room()
        self.room_name = ''
        self.room_id = 0
        self.live_id = 0
        self.streams = []
        self.stream_combo.blockSignals(True)
        self.stream_combo.clear()
        self.stream_combo.blockSignals(False)
        self.stream_combo.setEnabled(False)
        self.play_btn.setEnabled(False)
        self.stop_btn.setEnabled(False)
        self.play_btn.setText('▶')
        self.shot_btn.setEnabled(False)
        self.record_btn.setEnabled(False)
        self.title_label.setText(f'Slot {self.index + 1}')
        self.title_label.setToolTip('')
        self.meta_label.setText('Error')
        self.status_label.setText(message)

    def clear_tile(self):
        self.playback.reset_room()
        self.room_key = ''
        self.room_name = ''
        self.room_id = 0
        self.live_id = 0
        self.streams = []
        self.stream_combo.blockSignals(True)
        self.stream_combo.clear()
        self.stream_combo.blockSignals(False)
        self.stream_combo.setEnabled(False)
        self.play_btn.setEnabled(False)
        self.stop_btn.setEnabled(False)
        self.play_btn.setText('▶')
        self.shot_btn.setEnabled(False)
        self.record_btn.setEnabled(False)
        self.title_label.setText(f'Slot {self.index + 1}')
        self.title_label.setToolTip('')
        self.meta_label.setText('Empty')
        self.status_label.setText('Double-click a room on the left to load here.')

    def is_loaded(self):
        return bool(self.room_id and self.live_id and self.streams)

    def stop(self):
        self.playback.stop(clear_source=True)
        self.play_btn.setText('▶')
        self.stop_btn.setEnabled(False)

    def resume_loaded(self):
        if not self.is_loaded():
            return
        if self.stream_combo.currentIndex() < 0:
            self.stream_combo.setCurrentIndex(0)
        self._play_current()

    def _on_player_error(self, _error, error_string):
        if error_string:
            self.status_label.setText(f'Playback error: {error_string}')

    def _toggle_play(self):
        self.playback.toggle(self._play_current)

    def _play_current(self):
        idx = self.stream_combo.currentIndex()
        if idx < 0 or idx >= len(self.streams):
            return
        label, url = self.streams[idx]
        self.playback.play(url)
        self.status_label.setText(label)

    def _stop_player(self):
        self.playback.stop()

    def _on_stream_changed(self, idx):
        if idx < 0 or idx >= len(self.streams):
            self.play_btn.setEnabled(False)
            self.stop_btn.setEnabled(False)
            return
        self.play_btn.setEnabled(True)
        if self.playback.state != QMediaPlayer.PlaybackState.StoppedState:
            self._play_current()
        else:
            label, _url = self.streams[idx]
            self.status_label.setText(label)

    def _on_playback_state_changed(self, state):
        playing = state == QMediaPlayer.PlaybackState.PlayingState
        paused = state == QMediaPlayer.PlaybackState.PausedState
        self.play_btn.setText('⏸' if playing else '▶')
        self.stop_btn.setEnabled(bool(self.streams) and (playing or paused))

    def _on_volume_changed(self, value):
        self.playback.set_volume(value / 100)

    def stop_recording(self, silent=False):
        self.recorder.stop_recording(silent=silent)

    def _toggle_recording(self):
        if self.recorder.is_recording():
            self.stop_recording()
            return
        idx = self.stream_combo.currentIndex()
        if idx < 0 or idx >= len(self.streams):
            self.recording_notice.emit(f'Tile {self.index + 1} has no stream to record.')
            return
        _label, url = self.streams[idx]
        self.recorder.start_recording(self.room_name or f'slot-{self.index + 1}', url)

    def _on_recording_started(self, output_path):
        self.recording_notice.emit(f'Tile {self.index + 1} recording: {output_path}')

    def _on_recording_stopped(self, output_path):
        self.recording_notice.emit(f'Tile {self.index + 1} saved: {output_path}')

    def _on_recording_error(self, message):
        self.recording_notice.emit(f'Tile {self.index + 1} recording error: {message}')

    def _update_record_ui(self, _recording=None):
        recording = self.recorder.is_recording()
        self.record_btn.setText('Stop REC' if recording else 'Start REC')
        self.record_btn.setEnabled(bool(self.streams) or recording)
        self.stream_combo.setEnabled(bool(self.streams) and not recording)
        palette = self.palette()
        self.apply_theme(
            palette.color(QPalette.ColorRole.Base),
            palette.color(QPalette.ColorRole.Window),
            palette.color(QPalette.ColorRole.Text),
            palette.color(QPalette.ColorRole.Mid),
            palette.color(QPalette.ColorRole.Highlight),
        )


class MainWindow(QMainWindow):
    COMMENT_ACCENTS = (
        '#df6d57',
        '#2f7f6d',
        '#4e72c9',
        '#9b5ab6',
        '#bd8b2f',
        '#c24b7a',
        '#3b8f9d',
        '#7b8c34',
    )
    MAX_COMMENT_ENTRIES = 240
    COMMENT_PRUNE_BATCH = 40
    MULTI_LAYOUTS = {
        '2 x 2': {'rows': 2, 'cols': 2, 'positions': ((0, 0), (0, 1), (1, 0), (1, 1))},
        '2 x 3': {'rows': 2, 'cols': 3, 'positions': ((0, 0), (0, 1), (0, 2), (1, 0), (1, 1), (1, 2))},
        '3 x 3': {
            'rows': 3,
            'cols': 3,
            'positions': (
                (0, 0), (0, 1), (0, 2),
                (1, 0), (1, 1), (1, 2),
                (2, 0), (2, 1), (2, 2),
            ),
        },
    }

    def __init__(self):
        super().__init__()
        self.setWindowTitle(APP_NAME)
        self.setWindowIcon(app_icon())
        self.setMinimumSize(1040, 700)
        self.resize(1720, 980)

        self._mode = 'single'
        self._streams = []
        self._single_room_key = ''
        self._single_room_name = ''
        self._single_room_id = 0
        self._single_live_id = 0
        self._logged_in = False
        self._rooms_source = 'all'
        self._current_user_name = ''
        self._current_user_info = {}
        self._comment_room_id = 0
        self._comment_live_id = 0
        self._comment_entries = []
        self._rendering_chat = False
        self._chat_unread = 0
        self._comment_target_version = 0
        self._post_thread = None
        self._active_comment_tile_index = None
        self._selected_multi_tile_index = 0
        self._update_check_thread = None
        self._update_dialog_visible = False

        self._live_comments_thread = None
        self._comment_threads = set()
        self._closing = False
        self._single_load_threads = {}
        self._live_rooms_thread = None
        self._multi_load_threads = {}

        self._build_ui()
        self._setup_player()
        self._setup_multi_mode()
        self._set_mode('single')
        self._restore_cached_login()
        self._refresh_rooms()
        QTimer.singleShot(0, self._check_for_updates)

    # ── UI ──────────────────────────────────────────────────────────────────

    def _build_ui(self):
        central = QWidget()
        self.setCentralWidget(central)
        root = QVBoxLayout(central)
        root.setContentsMargins(14, 12, 14, 8)
        root.setSpacing(6)

        toolbar = QWidget()
        toolbar.setObjectName('roomToolbar')
        toolbar.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        top = QHBoxLayout(toolbar)
        top.setContentsMargins(6, 3, 6, 3)
        top.setSpacing(8)
        self.url_input = QLineEdit()
        self.url_input.setPlaceholderText('Room key or https://www.showroom-live.com/r/...')
        self.url_input.setClearButtonEnabled(True)
        self.url_input.setAccessibleName('SHOWROOM room address')
        self.url_input.returnPressed.connect(self.load_room)

        self.load_btn = QPushButton('Open')
        self.load_btn.setMinimumWidth(76)
        self.load_btn.setProperty('primary', True)
        self.load_btn.clicked.connect(self.load_room)

        self.mode_btn = QPushButton('Multi View')
        self.mode_btn.setFixedWidth(96)
        self.mode_btn.clicked.connect(self._toggle_mode)

        self.login_btn = QPushButton('Sign In')
        self.login_btn.setMinimumWidth(88)
        self.login_btn.setMaximumWidth(180)
        self.login_btn.clicked.connect(self._on_auth_button_clicked)

        self.account_menu = QMenu(self.login_btn)
        self.account_menu.addAction('Profile', self._open_user_info)
        self.account_menu.addSeparator()
        self.account_menu.addAction('Sign Out', self._logout)

        top.addWidget(self.url_input)
        top.addWidget(self.load_btn)
        top.addWidget(self.mode_btn)
        top.addWidget(self.login_btn)
        root.addWidget(toolbar)

        self.outer_splitter = QSplitter(Qt.Orientation.Horizontal)
        self.outer_splitter.setChildrenCollapsible(False)
        self.outer_splitter.setHandleWidth(10)

        self.rooms_box = QGroupBox('Live Rooms')
        self.rooms_box.setMinimumWidth(180)
        rooms_layout = QVBoxLayout(self.rooms_box)
        rooms_layout.setContentsMargins(6, 6, 6, 6)
        rooms_layout.setSpacing(6)

        self.refresh_btn = QPushButton('Reload')
        self.refresh_btn.clicked.connect(self._refresh_rooms)
        rooms_layout.addWidget(self.refresh_btn)

        source_row = QHBoxLayout()
        source_row.setContentsMargins(0, 0, 0, 0)
        source_row.setSpacing(6)
        self.rooms_all_btn = QPushButton('All')
        self.rooms_all_btn.setCheckable(True)
        self.rooms_all_btn.clicked.connect(lambda: self._set_rooms_source('all'))
        self.rooms_followed_btn = QPushButton('Followed')
        self.rooms_followed_btn.setCheckable(True)
        self.rooms_followed_btn.clicked.connect(lambda: self._set_rooms_source('followed'))
        source_row.addWidget(self.rooms_all_btn)
        source_row.addWidget(self.rooms_followed_btn)
        rooms_layout.addLayout(source_row)

        self.rooms_list = QListWidget()
        self.rooms_list.itemDoubleClicked.connect(self._on_room_double_clicked)
        rooms_layout.addWidget(self.rooms_list)
        self.outer_splitter.addWidget(self.rooms_box)

        self.content_splitter = QSplitter(Qt.Orientation.Horizontal)
        self.content_splitter.setChildrenCollapsible(False)
        self.content_splitter.setHandleWidth(10)

        self.center_stack = QStackedWidget()
        self.single_page = self._build_single_page()
        self.multi_page = self._build_multi_page()
        self.center_stack.addWidget(self.single_page)
        self.center_stack.addWidget(self.multi_page)
        self.content_splitter.addWidget(self.center_stack)

        self.comment_box = self._build_comment_box()
        self.content_splitter.addWidget(self.comment_box)

        self.outer_splitter.addWidget(self.content_splitter)
        self.outer_splitter.setStretchFactor(0, 1)
        self.outer_splitter.setStretchFactor(1, 6)
        self.content_splitter.setStretchFactor(0, 5)
        self.content_splitter.setStretchFactor(1, 2)
        root.addWidget(self.outer_splitter, stretch=1)
        self.outer_splitter.setSizes([250, 1470])
        self.content_splitter.setSizes([1110, 360])

        self.status_bar = QStatusBar()
        self.setStatusBar(self.status_bar)
        for tile in self._multi_tiles:
            tile.recording_notice.connect(self.status_bar.showMessage)

        self._apply_ui_theme()
        self._update_auth_button()
        self._update_room_source_buttons()
        self._update_identity_label()
        self._update_comment_input_state()

    def _build_single_page(self):
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(6)

        self.video_widget = QVideoWidget()
        self.video_widget.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        layout.addWidget(self.video_widget, stretch=1)
        layout.addWidget(self._build_single_controls())
        return page

    def _build_single_controls(self):
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

        self.stream_combo = QComboBox()
        self.stream_combo.setMinimumWidth(90)
        self.stream_combo.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon)
        self.stream_combo.currentIndexChanged.connect(self._on_stream_changed)

        self.single_room_label = QLabel('No room loaded')
        self.single_room_label.setStyleSheet('font-weight: 600;')
        self.single_room_label.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        self.single_record_indicator = QLabel()
        self.single_record_indicator.setFixedSize(10, 10)
        self.single_shot_btn = QPushButton('Shot')
        self.single_shot_btn.setFixedWidth(72)
        self.single_shot_btn.setEnabled(False)
        self.single_shot_btn.clicked.connect(self._take_single_screenshot)
        self.single_record_btn = QPushButton('Start REC')
        self.single_record_btn.setFixedWidth(96)
        self.single_record_btn.setEnabled(False)
        self.single_record_btn.clicked.connect(self._toggle_single_recording)
        layout.addWidget(self.play_btn)
        layout.addWidget(self.stop_btn)
        layout.addWidget(self.mute_btn)
        layout.addWidget(self.volume_slider)
        self.stream_combo.setAccessibleName('Stream quality')
        layout.addWidget(self.stream_combo)
        layout.addWidget(self.single_room_label, stretch=1)
        layout.addWidget(self.single_shot_btn)
        layout.addWidget(self.single_record_indicator)
        layout.addWidget(self.single_record_btn)
        return bar

    def _build_multi_page(self):
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(8)

        toolbar = QWidget()
        toolbar_layout = QHBoxLayout(toolbar)
        toolbar_layout.setContentsMargins(4, 0, 4, 0)
        toolbar_layout.setSpacing(8)
        self.multi_layout_combo = QComboBox()
        self.multi_layout_combo.addItems(self.MULTI_LAYOUTS.keys())
        self.multi_layout_combo.currentTextChanged.connect(self._apply_multi_layout)
        self.multi_hint_label = QLabel('Double-click a room on the left to load into the selected tile.')
        self.multi_hint_label.setFont(QFont('Menlo', 10))
        toolbar_layout.addWidget(QLabel('Layout:'))
        toolbar_layout.addWidget(self.multi_layout_combo)
        toolbar_layout.addWidget(self.multi_hint_label, stretch=1)
        layout.addWidget(toolbar)

        self.multi_grid_host = QWidget()
        self.multi_grid = QGridLayout(self.multi_grid_host)
        self.multi_grid.setContentsMargins(0, 0, 0, 0)
        self.multi_grid.setHorizontalSpacing(8)
        self.multi_grid.setVerticalSpacing(8)
        layout.addWidget(self.multi_grid_host, stretch=1)

        self._multi_tiles = [MultiRoomTile(index) for index in range(9)]
        for tile in self._multi_tiles:
            tile.selected.connect(self._on_multi_tile_selected)
            tile.screenshot_requested.connect(self._take_multi_screenshot)
        return page

    def _build_comment_box(self):
        box = QGroupBox('Live Chat')
        box.setMinimumWidth(260)
        layout = QVBoxLayout(box)
        layout.setContentsMargins(6, 6, 6, 6)
        layout.setSpacing(6)

        header = QHBoxLayout()
        header.setContentsMargins(0, 0, 0, 0)
        self.comment_target_label = QLabel('Room:')
        self.comment_target_label.setFont(QFont('Menlo', 10))
        self.comment_target_combo = QComboBox()
        self.comment_target_combo.currentIndexChanged.connect(self._on_comment_target_changed)
        header.addWidget(self.comment_target_label)
        header.addWidget(self.comment_target_combo, stretch=1)
        layout.addLayout(header)

        self.chat_connection = QLabel('Reconnecting…')
        self.chat_connection.hide()
        layout.addWidget(self.chat_connection)
        self.comment_view = QTextEdit()
        self.comment_view.setReadOnly(True)
        self.comment_view.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.comment_view.customContextMenuRequested.connect(self._show_comment_menu)
        self.comment_view.setFont(QFont('Menlo', 10))
        layout.addWidget(self.comment_view, stretch=1)

        self.jump_to_latest = QPushButton('Jump to Latest')
        self.jump_to_latest.clicked.connect(self._scroll_chat_to_bottom)
        self.jump_to_latest.hide()
        layout.addWidget(self.jump_to_latest)
        self.comment_view.verticalScrollBar().valueChanged.connect(self._on_chat_scrolled)

        self.identity_label = QLabel()
        self.identity_label.setTextFormat(Qt.TextFormat.PlainText)
        self.identity_label.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        layout.addWidget(self.identity_label)

        composer = QHBoxLayout()
        self.comment_input = QTextEdit()
        self.comment_input.setAcceptRichText(False)
        self.comment_input.setFixedHeight(58)
        self.comment_input.installEventFilter(self)
        self.comment_input.textChanged.connect(self._update_comment_input_state)
        composer.addWidget(self.comment_input, stretch=1)
        self.send_btn = QPushButton('Send')
        self.send_btn.clicked.connect(self._post_comment)
        composer.addWidget(self.send_btn)
        layout.addLayout(composer)
        self.chat_feedback = QLabel()
        self.chat_feedback.setTextFormat(Qt.TextFormat.PlainText)
        self.chat_feedback.setWordWrap(True)
        self.chat_feedback.hide()
        layout.addWidget(self.chat_feedback)
        self.chat_sign_in = QPushButton('Sign In to Comment')
        self.chat_sign_in.clicked.connect(self._open_login)
        layout.addWidget(self.chat_sign_in)

        return box

    # ── Updates ─────────────────────────────────────────────────────────────

    def _check_for_updates(self):
        if self._update_check_thread is not None and self._update_check_thread.isRunning():
            return
        thread = CheckForUpdatesThread(self)
        thread.checked.connect(self._on_update_check_finished)
        thread.finished.connect(lambda: setattr(self, '_update_check_thread', None))
        self._update_check_thread = thread
        thread.start()

    def _on_update_check_finished(self, result):
        if not result.get('ok'):
            return
        if not result.get('has_update'):
            return
        if self._update_dialog_visible:
            return
        self._show_update_dialog(result)

    def _show_update_dialog(self, result):
        self._update_dialog_visible = True
        try:
            latest_version = result.get('latest_version') or 'unknown'
            current_version = result.get('current_version') or APP_VERSION
            release_url = result.get('release_url') or LATEST_RELEASE_URL

            dialog = UpdateDialog(current_version, latest_version, release_url, self)
            if dialog.exec() == QDialog.DialogCode.Accepted and dialog.should_open_release_page():
                QDesktopServices.openUrl(QUrl(release_url))
        finally:
            self._update_dialog_visible = False

    # ── Player ──────────────────────────────────────────────────────────────

    def _setup_player(self):
        self.playback = PlaybackSession(self.video_widget, self.volume_slider.value() / 100, self)
        self.playback.install_video_menu(
            self.video_widget, lambda: self._single_room_key,
            lambda: selected_stream(self.stream_combo, self._streams),
        )
        self.playback.state_changed.connect(self._on_playback_state_changed)
        self.playback.error_occurred.connect(self._on_player_error)
        self._single_recorder = self.playback.recorder
        self._single_recorder.state_changed.connect(self._update_single_record_ui)
        self._single_recorder.started.connect(self._on_single_recording_started)
        self._single_recorder.stopped.connect(self._on_single_recording_stopped)
        self._single_recorder.error.connect(self._on_single_recording_error)

    def _setup_multi_mode(self):
        self._apply_multi_layout(self.multi_layout_combo.currentText())
        self._select_multi_tile(0, sync_comments=False)

    def _toggle_play(self):
        self.playback.toggle(self._play_current)

    def _play_current(self):
        idx = self.stream_combo.currentIndex()
        if idx < 0 or idx >= len(self._streams):
            return
        label, url = self._streams[idx]
        self.playback.play(url)
        self.status_bar.showMessage(f'Now playing: {label}')

    def _stop_player(self):
        self.playback.stop(clear_source=True)

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
        self.playback.set_muted(muted)
        self.mute_btn.setText('🔇' if muted else '🔊')

    def _on_volume_changed(self, value):
        self.playback.set_volume(value / 100)

    def _capture_screenshot(self, image, room_name, label):
        if image is None or image.isNull():
            self.status_bar.showMessage(f'{label} screenshot failed: no video frame available.')
            return False

        output_path = build_timestamped_download_path(room_name or 'showroom-room', 'png', '_screenshot')
        if not image.save(str(output_path), 'PNG'):
            self.status_bar.showMessage(f'{label} screenshot failed: could not save PNG.')
            return False

        QGuiApplication.clipboard().setImage(image)
        self.status_bar.showMessage(f'{label} screenshot copied and saved: {output_path}')
        return True

    def _take_single_screenshot(self):
        if not self._streams:
            self.status_bar.showMessage('No stream available to capture.')
            return
        self._capture_screenshot(self.playback.screenshot(), self._single_room_name, 'Single view')

    def _take_multi_screenshot(self, tile_index):
        tile = self._multi_tiles[tile_index]
        if not tile.streams:
            self.status_bar.showMessage(f'Tile {tile.index + 1} has no stream to capture.')
            return
        self._capture_screenshot(tile.current_frame_image(), tile.room_name, f'Tile {tile.index + 1}')

    def _toggle_single_recording(self):
        if self._single_recorder.is_recording():
            self._stop_single_recording()
            return
        idx = self.stream_combo.currentIndex()
        if idx < 0 or idx >= len(self._streams):
            self.status_bar.showMessage('No stream available to record.')
            return
        _label, url = self._streams[idx]
        self._single_recorder.start_recording(self._single_room_name or 'showroom-room', url)

    def _stop_single_recording(self, silent=False):
        self._single_recorder.stop_recording(silent=silent)

    def _on_single_recording_started(self, output_path):
        self.status_bar.showMessage(f'Recording: {output_path}')

    def _on_single_recording_stopped(self, output_path):
        self.status_bar.showMessage(f'Recording saved: {output_path}')

    def _on_single_recording_error(self, message):
        self.status_bar.showMessage(f'Recording error: {message}')

    def _update_single_record_ui(self, _recording=None):
        recorder = getattr(self, '_single_recorder', None)
        recording = recorder.is_recording() if recorder is not None else False
        self.single_shot_btn.setEnabled(bool(self._streams))
        self.single_record_btn.setText('Stop REC' if recording else 'Start REC')
        self.single_record_btn.setEnabled(bool(self._streams) or recording)
        self.stream_combo.setEnabled(bool(self._streams) and not recording)
        palette = self.palette()
        border = palette.color(QPalette.ColorRole.Mid)
        window = palette.color(QPalette.ColorRole.Window)
        highlight = palette.color(QPalette.ColorRole.Highlight)
        button_bg = '#b63a30' if recording else self._color_to_css(
            self._mix_colors(
                self._mix_colors(
                    palette.color(QPalette.ColorRole.Base),
                    window,
                    0.16,
                ),
                highlight,
                0.04,
            )
        )
        button_hover = '#cf473c' if recording else self._color_to_css(
            self._mix_colors(
                self._mix_colors(
                    palette.color(QPalette.ColorRole.Base),
                    window,
                    0.16,
                ),
                highlight,
                0.10,
            )
        )
        button_text = 'white' if recording else self._color_to_css(palette.color(QPalette.ColorRole.Text))
        border_color = self._color_to_css(self._mix_colors(border, highlight, 0.52)) if recording else self._color_to_rgba(border, 55)
        self.single_record_btn.setStyleSheet(
            'QPushButton {'
            f' background: {button_bg};'
            f' color: {button_text};'
            f' border: 1px solid {border_color};'
            ' border-radius: 12px;'
            ' padding: 8px 12px;'
            '}'
            'QPushButton:hover {'
            f' background: {button_hover};'
            '}'
            'QPushButton:disabled {'
            f' color: {self._color_to_rgba(palette.color(QPalette.ColorRole.Text), 110)};'
            f' background: {self._color_to_rgba(self._mix_colors(palette.color(QPalette.ColorRole.Base), window, 0.16), 180)};'
            '}'
        )
        self.single_record_indicator.setStyleSheet(
            f'background: {"#e74c3c" if recording else self._color_to_css(self._mix_colors(border, window, 0.34))};'
            f'border: 1px solid {self._color_to_css(self._mix_colors(border, highlight, 0.52)) if recording else self._color_to_rgba(border, 55)};'
            'border-radius: 5px;'
        )

    # ── Single Stream ───────────────────────────────────────────────────────

    def _on_stream_changed(self, idx):
        if idx < 0 or idx >= len(self._streams):
            self.play_btn.setEnabled(False)
            return
        self.play_btn.setEnabled(True)
        if self.playback.state == QMediaPlayer.PlaybackState.PlayingState:
            self._play_current()

    def load_room(self):
        room_input = self.url_input.text().strip()
        if not room_input:
            return
        if self._mode == 'multi':
            self._load_room_into_selected_tile(room_input)
        else:
            self._load_single_room(room_input)

    def _load_single_room(self, room_input):
        for thread in self._single_load_threads.values():
            thread.requestInterruption()
        request = self.playback.reset_room()
        self.stream_combo.clear()
        self._streams = []
        self._single_room_key = ''
        self._single_room_name = ''
        self._single_room_id = 0
        self._single_live_id = 0
        self.single_room_label.setText('Loading room...')
        self.single_room_label.setToolTip('')
        self.play_btn.setEnabled(False)
        self.stop_btn.setEnabled(False)
        self.load_btn.setEnabled(False)
        self.status_bar.showMessage('Opening room...')

        thread = LoadRoomThread(room_input)
        self._single_load_threads[request] = thread
        thread.status.connect(
            lambda message: self._handle_single_load(request, self.status_bar.showMessage, message)
        )
        thread.streams_ready.connect(
            lambda *result: self._handle_single_load(request, self._on_single_streams_ready, *result, room_input)
        )
        thread.error.connect(
            lambda message: self._handle_single_load(request, self._on_room_load_error, message)
        )
        thread.finished.connect(
            lambda: self._on_single_load_finished(request)
        )
        thread.start()

    def _on_single_load_finished(self, request):
        self._single_load_threads.pop(request, None)
        self._handle_single_load(request, self.load_btn.setEnabled, True)

    def _handle_single_load(self, request, callback, *args):
        if self.playback.is_current_request(request):
            callback(*args)

    def _on_single_streams_ready(self, streams, room_name, room_id, live_id, room_key):
        self._single_room_key = room_key
        self._streams = streams
        self._single_room_name = room_name
        self._single_room_id = room_id
        self._single_live_id = live_id
        self.single_room_label.setText(room_name)
        self.single_room_label.setToolTip(room_name)

        self.stream_combo.blockSignals(True)
        self.stream_combo.clear()
        for label, _url in streams:
            self.stream_combo.addItem(label)
        self.stream_combo.blockSignals(False)

        if streams:
            self.stream_combo.setCurrentIndex(0)
            self.play_btn.setEnabled(True)
            self._play_current()

        self.setWindowTitle(f'{APP_NAME} - {room_name}')
        self.status_bar.showMessage(f'{room_name}  ·  {len(streams)} quality options')
        self._update_single_record_ui()
        if self._mode == 'single':
            self._set_comment_room(room_id, live_id, room_name, mode='single')

    def _on_room_load_error(self, msg):
        self.single_room_label.setText('No room loaded')
        self.single_room_label.setToolTip('')
        self._update_single_record_ui()
        self.status_bar.showMessage(f'Error: {msg}')

    # ── Multi Mode ──────────────────────────────────────────────────────────

    def _set_mode(self, mode):
        self._mode = mode
        is_multi = mode == 'multi'
        if is_multi:
            self._stop_single_recording(silent=True)
            self._stop_player()
            for tile in self._visible_multi_tiles():
                tile.resume_loaded()
        else:
            for tile in self._multi_tiles:
                tile.stop_recording(silent=True)
                tile.stop()
        self.center_stack.setCurrentWidget(self.multi_page if is_multi else self.single_page)
        self.mode_btn.setText('Single View' if is_multi else 'Multi View')
        self.comment_target_label.setVisible(is_multi)
        self.comment_target_combo.setVisible(is_multi)
        self.comment_target_combo.setEnabled(is_multi)

        if is_multi:
            self._refresh_comment_targets(preferred_tile_index=self._selected_multi_tile_index)
            if self.comment_target_combo.count() == 0:
                self._set_comment_room(0, 0, '', mode='multi')
        else:
            if self._single_room_id:
                self._set_comment_room(
                    self._single_room_id,
                    self._single_live_id,
                    self._single_room_name,
                    mode='single',
                )
            else:
                self._set_comment_room(0, 0, '', mode='single')

    def _toggle_mode(self):
        self._set_mode('multi' if self._mode == 'single' else 'single')

    def _apply_multi_layout(self, layout_name):
        info = self.MULTI_LAYOUTS[layout_name]
        visible_count = len(info['positions'])

        for index, tile in enumerate(self._multi_tiles):
            self.multi_grid.removeWidget(tile)
            if index < visible_count:
                row, col = info['positions'][index]
                self.multi_grid.addWidget(tile, row, col)
                tile.show()
            else:
                self._interrupt_tile_loads(tile.index)
                tile.clear_tile()
                tile.hide()

        for row in range(3):
            self.multi_grid.setRowStretch(row, 1 if row < info['rows'] else 0)
        for col in range(3):
            self.multi_grid.setColumnStretch(col, 1 if col < info['cols'] else 0)

        if self._selected_multi_tile_index >= visible_count:
            self._selected_multi_tile_index = 0
        self._select_multi_tile(self._selected_multi_tile_index, sync_comments=False)
        if self._mode == 'multi':
            self._refresh_comment_targets(preferred_tile_index=self._selected_multi_tile_index)

    def _visible_multi_tiles(self):
        visible_count = len(self.MULTI_LAYOUTS[self.multi_layout_combo.currentText()]['positions'])
        return self._multi_tiles[:visible_count]

    def _select_multi_tile(self, index, sync_comments=True):
        self._selected_multi_tile_index = index
        for tile in self._multi_tiles:
            tile.set_selected(tile.index == index and tile.isVisible())

        palette = self.palette()
        base = palette.color(QPalette.ColorRole.Base)
        window = palette.color(QPalette.ColorRole.Window)
        text = palette.color(QPalette.ColorRole.Text)
        border = palette.color(QPalette.ColorRole.Mid)
        highlight = palette.color(QPalette.ColorRole.Highlight)
        for tile in self._multi_tiles:
            tile.apply_theme(base, window, text, border, highlight)

        if sync_comments and self._mode == 'multi':
            tile = self._multi_tiles[index]
            if tile.is_loaded():
                self._set_comment_target_combo_index(index)

    def _set_comment_target_combo_index(self, tile_index):
        for combo_index in range(self.comment_target_combo.count()):
            if self.comment_target_combo.itemData(combo_index) == tile_index:
                self.comment_target_combo.setCurrentIndex(combo_index)
                return

    def _target_multi_tile(self):
        visible_tiles = self._visible_multi_tiles()
        if not visible_tiles:
            return None
        selected_tile = self._multi_tiles[self._selected_multi_tile_index]
        if selected_tile in visible_tiles:
            return selected_tile
        for tile in visible_tiles:
            if not tile.is_loaded():
                return tile
        return visible_tiles[0]

    def _load_room_into_selected_tile(self, room_input):
        tile = self._target_multi_tile()
        if tile is None:
            return
        self._select_multi_tile(tile.index, sync_comments=False)
        self._interrupt_tile_loads(tile.index)
        token = tile.start_loading(room_input)
        self.status_bar.showMessage(f'Opening room for tile {tile.index + 1}...')

        thread = LoadRoomThread(room_input)
        self._multi_load_threads[(tile.index, token)] = thread
        thread.status.connect(lambda msg, t=tile, s=token: self._on_multi_tile_status(t, s, msg))
        thread.streams_ready.connect(
            lambda streams, room_name, room_id, live_id, t=tile, s=token, key=room_input:
            self._on_multi_tile_ready(t, s, streams, room_name, room_id, live_id, key)
        )
        thread.error.connect(lambda msg, t=tile, s=token: self._on_multi_tile_error(t, s, msg))
        thread.finished.connect(lambda t=tile, s=token: self._on_multi_thread_finished(t, s))
        thread.start()

    def _interrupt_tile_loads(self, tile_index):
        for (index, _), thread in self._multi_load_threads.items():
            if index == tile_index:
                thread.requestInterruption()

    def _on_multi_tile_selected(self, index):
        self._select_multi_tile(index, sync_comments=True)

    def _on_multi_tile_status(self, tile, token, message):
        if not tile.playback.is_current_request(token):
            return
        tile.set_loading_status(message)

    def _on_multi_tile_ready(self, tile, token, streams, room_name, room_id, live_id, room_key):
        if not tile.playback.is_current_request(token):
            return
        tile.set_room(streams, room_name, room_id, live_id, room_key)
        self.status_bar.showMessage(f'Loaded "{room_name}" into tile {tile.index + 1}')
        self._refresh_comment_targets(preferred_tile_index=tile.index)
        if self._mode == 'multi':
            self._set_comment_target_combo_index(tile.index)

    def _on_multi_tile_error(self, tile, token, message):
        if not tile.playback.is_current_request(token):
            return
        tile.set_error(message)
        self.status_bar.showMessage(f'Tile {tile.index + 1} error: {message}')
        self._refresh_comment_targets(preferred_tile_index=self._active_comment_tile_index)

    def _on_multi_thread_finished(self, tile, token):
        self._multi_load_threads.pop((tile.index, token), None)

    # ── Login ────────────────────────────────────────────────────────────────

    def _on_auth_button_clicked(self):
        if not self._logged_in:
            self._open_login()

    def _update_auth_button(self, loading=False):
        if loading:
            self.login_btn.setText('Loading...')
            self.login_btn.setEnabled(False)
            self.chat_sign_in.setEnabled(False)
            return
        name = str(self._current_user_name or 'Account')
        caption = name[:14] + '…' if len(name) > 15 else name
        self.login_btn.setMenu(self.account_menu if self._logged_in else None)
        self.login_btn.setText(caption if self._logged_in else 'Sign In')
        self.login_btn.setToolTip(name if self._logged_in else '')
        self.login_btn.setEnabled(True)
        self.chat_sign_in.setEnabled(True)

    def _open_login(self):
        if self._closing or not self.login_btn.isEnabled():
            return
        dlg = LoginDialog(self)
        if dlg.exec() == QDialog.DialogCode.Accepted:
            self._logged_in = True
            self._rooms_source = 'followed'
            self._current_user_name = ''
            self._current_user_info = {}
            self._update_auth_button(loading=True)
            self._update_room_source_buttons()
            self._update_identity_label(loading=True)
            self._update_comment_input_state()
            self.status_bar.showMessage('Signed in. Loading profile...')
            self._refresh_rooms()
            self._current_user_thread = LoadCurrentUserThread()
            self._current_user_thread.done.connect(self._on_login_user_fetched)
            self._current_user_thread.start()

    @staticmethod
    def _display_name_from_user_info(user_info):
        return (
            user_info.get('user_name')
            or user_info.get('account_id')
            or user_info.get('name')
            or ''
        )

    def _on_login_user_fetched(self, user_info, ok):
        if ok:
            self._current_user_info = dict(user_info)
            self._current_user_name = self._display_name_from_user_info(user_info)
            save_session_cookies()
            self.status_bar.showMessage(f'Signed in as {self._current_user_name or "Account"}')
        else:
            self._current_user_info = {}
            self._current_user_name = ''
            self.status_bar.showMessage('Signed in.')
        self._update_auth_button()
        self._update_identity_label()
        self._update_comment_input_state()

    def _on_restore_user_fetched(self, user_info, ok):
        if ok:
            self._logged_in = True
            self._rooms_source = 'followed'
            self._current_user_info = dict(user_info)
            self._current_user_name = self._display_name_from_user_info(user_info)
            save_session_cookies()
            self.status_bar.showMessage(f'Signed in as {self._current_user_name or "Account"}')
            self._refresh_rooms()
        else:
            self._logged_in = False
            self._rooms_source = 'all'
            self._current_user_name = ''
            self._current_user_info = {}
            if user_info.get('is_login') in (False, 0, '0'):
                clear_session_cookies()
                self.status_bar.showMessage('Saved session expired. Please sign in again.')
            else:
                self.status_bar.showMessage(
                    'Could not verify saved sign-in. It will be retried on the next launch.'
                )
            self._refresh_rooms()
        self._update_auth_button()
        self._update_room_source_buttons()
        self._update_identity_label()
        self._update_comment_input_state()

    def _logout(self):
        clear_session_cookies()
        self._logged_in = False
        self._rooms_source = 'all'
        self._current_user_name = ''
        self._current_user_info = {}
        self.chat_feedback.hide()
        self._comment_target_version += 1
        self.comment_input.clear()
        self._update_auth_button()
        self._update_room_source_buttons()
        self._update_identity_label()
        self._update_comment_input_state()
        self.status_bar.showMessage('Signed out.')
        self._refresh_rooms()

    def _restore_cached_login(self):
        if not load_session_cookies():
            return
        self._logged_in = False
        self._update_auth_button(loading=True)
        self._update_room_source_buttons()
        self._update_identity_label(loading=True)
        self.status_bar.showMessage('Restoring saved sign-in...')
        self._current_user_thread = LoadCurrentUserThread()
        self._current_user_thread.done.connect(self._on_restore_user_fetched)
        self._current_user_thread.start()

    def _open_user_info(self):
        if not self._logged_in:
            self.status_bar.showMessage('Sign in to view account details.')
            return
        dlg = UserInfoDialog(self._current_user_info, self)
        dlg.refresh_requested.connect(lambda: self._refresh_profile(dlg))
        dlg.sign_out_requested.connect(dlg.accept)
        dlg.sign_out_requested.connect(self._logout)
        dlg.exec()
        dlg.deleteLater()

    def _refresh_profile(self, dialog):
        active = getattr(self, '_current_user_thread', None)
        if self._closing or not self._logged_in or (active is not None and active.isRunning()):
            return
        dialog.set_refresh_state(loading=True)
        thread = LoadCurrentUserThread()
        self._current_user_thread = thread
        thread.done.connect(self._on_profile_refreshed)
        thread.done.connect(dialog.apply_refresh_result)
        thread.start()

    def _on_profile_refreshed(self, data, ok):
        if ok and self._logged_in and not self._closing:
            self._current_user_info = dict(data)
            self._current_user_name = self._display_name_from_user_info(data)
            self._update_auth_button()
            self._update_identity_label()

    # ── Comments ─────────────────────────────────────────────────────────────

    def _set_comment_room(self, room_id, live_id, room_name, mode):
        if mode == 'single':
            self._active_comment_tile_index = None
        if (room_id or 0, live_id or 0) == (self._comment_room_id, self._comment_live_id):
            self._update_comment_input_state()
            return
        self.chat_feedback.hide()
        self._comment_target_version += 1
        self.comment_input.clear()
        self._comment_room_id = room_id or 0
        self._comment_live_id = live_id or 0
        self.comment_box.setTitle(f'Live Chat - {room_name}' if room_name else 'Live Chat')
        self._clear_comment_entries()
        self._stop_comments()
        if room_id:
            self._start_comments(room_id)
        self._update_comment_input_state()

    def _refresh_comment_targets(self, preferred_tile_index=None):
        if self._mode != 'multi':
            return
        current_index = preferred_tile_index
        if current_index is None:
            current_index = self._active_comment_tile_index

        self.comment_target_combo.blockSignals(True)
        self.comment_target_combo.clear()
        for tile in self._visible_multi_tiles():
            if tile.is_loaded():
                self.comment_target_combo.addItem(tile.room_name, tile.index)
        self.comment_target_combo.setCurrentIndex(-1)
        self.comment_target_combo.blockSignals(False)
        self.comment_target_combo.setEnabled(self.comment_target_combo.count() > 0)

        if self.comment_target_combo.count() == 0:
            self._active_comment_tile_index = None
            self._set_comment_room(0, 0, '', mode='multi')
            return

        for combo_index in range(self.comment_target_combo.count()):
            if self.comment_target_combo.itemData(combo_index) == current_index:
                self.comment_target_combo.setCurrentIndex(combo_index)
                return
        self.comment_target_combo.setCurrentIndex(0)

    def _on_comment_target_changed(self, combo_index):
        if self._mode != 'multi' or combo_index < 0:
            return
        tile_index = self.comment_target_combo.itemData(combo_index)
        if tile_index is None:
            return
        tile = self._multi_tiles[tile_index]
        self._active_comment_tile_index = tile_index
        self._select_multi_tile(tile_index, sync_comments=False)
        self._set_comment_room(tile.room_id, tile.live_id, tile.room_name, mode='multi')

    def _post_comment(self):
        text = self.comment_input.toPlainText().strip()
        if (not text or not self._logged_in or not self._comment_live_id
                or self._post_thread is not None or self._closing):
            return
        self.send_btn.setEnabled(False)
        self.comment_input.setEnabled(False)
        thread = SendCommentThread(self._comment_live_id, text)
        target = self._comment_target_version
        thread.success.connect(lambda: self._on_comment_sent(target))
        self.chat_feedback.hide()
        thread.error.connect(lambda msg: self._on_comment_failed(target, msg))
        thread.finished.connect(self._on_post_finished)
        self._post_thread = thread
        self._update_comment_input_state()
        thread.start()

    def _on_comment_failed(self, target, message):
        if target == self._comment_target_version and not self._closing:
            self.chat_feedback.setText(f'Could not send. {message}')
            self.chat_feedback.show()

    def _on_post_finished(self):
        self._post_thread = None
        self._update_comment_input_state()

    def _on_comment_sent(self, target):
        if target == self._comment_target_version:
            self.comment_input.clear()
        self.status_bar.showMessage('Message sent.')

    def _start_comments(self, room_id):
        if self._closing:
            return
        thread = LiveCommentsThread(room_id)
        self._live_comments_thread = thread
        self._comment_threads.add(thread)
        thread.new_comments.connect(
            lambda comments: self._handle_comment_result(thread, self._on_new_comments, comments)
        )
        thread.reconnecting.connect(
            lambda active: self._handle_comment_result(thread, self.chat_connection.setVisible, active)
        )
        thread.finished.connect(lambda: self._comment_threads.discard(thread))
        thread.finished.connect(thread.deleteLater)
        thread.start()

    def _handle_comment_result(self, thread, callback, result):
        if thread is self._live_comments_thread and not self._closing:
            callback(result)

    def _show_comment_menu(self, position):
        menu = self.comment_view.createStandardContextMenu(position)
        menu.addSeparator()
        refresh = menu.addAction('Refresh Comments')
        refresh.setEnabled(self._live_comments_thread is not None)
        refresh.triggered.connect(self._refresh_comments)
        menu.exec(self.comment_view.viewport().mapToGlobal(position))
        menu.deleteLater()

    def _refresh_comments(self):
        if self._live_comments_thread is not None:
            self._live_comments_thread.refresh()

    def _stop_comments(self):
        self.chat_connection.hide()
        thread = self._live_comments_thread
        self._live_comments_thread = None
        if thread is not None:
            thread.stop()

    def _on_new_comments(self, comments):
        self._append_entries([
            {'kind': 'comment', 'name': comment.get('name', '?'),
             'text': comment.get('comment', ''), 'comment': dict(comment)}
            for comment in comments
        ], unread=len(comments))

    @staticmethod
    def _plain_to_html(text):
        return escape(str(text)).replace('\n', '<br/>')

    @staticmethod
    def _color_to_css(color):
        return color.name(QColor.NameFormat.HexRgb)

    @staticmethod
    def _color_to_rgba(color, alpha):
        return f'rgba({color.red()}, {color.green()}, {color.blue()}, {alpha})'

    @staticmethod
    def _mix_colors(left, right, ratio):
        ratio = max(0.0, min(1.0, ratio))
        return QColor(
            round(left.red() * (1 - ratio) + right.red() * ratio),
            round(left.green() * (1 - ratio) + right.green() * ratio),
            round(left.blue() * (1 - ratio) + right.blue() * ratio),
        )

    def _apply_ui_theme(self):
        palette = self.palette()
        base = palette.color(QPalette.ColorRole.Base)
        text = palette.color(QPalette.ColorRole.Text)
        border = palette.color(QPalette.ColorRole.Mid)
        highlight = palette.color(QPalette.ColorRole.Highlight)
        window = palette.color(QPalette.ColorRole.Window)

        input_base = self._mix_colors(base, window, 0.18)
        selected_bg = self._mix_colors(highlight, base, 0.18)
        selected_text = palette.color(QPalette.ColorRole.HighlightedText)
        panel_bg = self._mix_colors(base, window, 0.08)
        surface_bg = self._mix_colors(base, window, 0.16)
        subtle_border = self._color_to_rgba(border, 82)
        soft_border = self._color_to_rgba(border, 55)
        hover_bg = self._mix_colors(self._mix_colors(base, window, 0.10), highlight, 0.06)
        button_bg = self._mix_colors(surface_bg, highlight, 0.04)
        button_hover = self._mix_colors(surface_bg, highlight, 0.10)
        label_color = self._color_to_css(self._mix_colors(text, border, 0.24))

        group_box_style = (
            'QGroupBox {'
            f' background: {self._color_to_css(panel_bg)};'
            f' border: 1px solid {soft_border};'
            ' border-radius: 16px;'
            ' margin-top: 14px;'
            ' padding-top: 10px;'
            '}'
            'QGroupBox::title {'
            f' color: {self._color_to_css(self._mix_colors(text, border, 0.18))};'
            ' subcontrol-origin: margin;'
            ' left: 14px;'
            ' padding: 0 6px;'
            '}'
        )
        self.centralWidget().setStyleSheet(
            'QWidget#roomToolbar {'
            f' background: {self._color_to_css(panel_bg)};'
            f' border: 1px solid {soft_border};'
            ' border-radius: 12px;'
            '}'
        )
        self.rooms_box.setStyleSheet(group_box_style)
        self.comment_box.setStyleSheet(group_box_style)

        control_style = (
            'QLineEdit, QComboBox {'
            f' background: {self._color_to_css(surface_bg)};'
            f' color: {self._color_to_css(text)};'
            f' border: 1px solid {soft_border};'
            ' border-radius: 12px;'
            ' padding: 8px 12px;'
            '}'
            'QLineEdit:focus, QComboBox:focus {'
            f' border: 1px solid {self._color_to_css(self._mix_colors(border, highlight, 0.45))};'
            '}'
            'QComboBox::drop-down {'
            ' border: none;'
            ' width: 20px;'
            '}'
        )
        for widget in (
            self.url_input,
            self.stream_combo,
            self.multi_layout_combo,
            self.comment_target_combo,
        ):
            widget.setStyleSheet(control_style)

        button_style = (
            'QPushButton {'
            f' background: {self._color_to_css(button_bg)};'
            f' color: {self._color_to_css(text)};'
            f' border: 1px solid {soft_border};'
            ' border-radius: 12px;'
            ' padding: 8px 12px;'
            '}'
            'QPushButton:hover {'
            f' background: {self._color_to_css(button_hover)};'
            '}'
            'QPushButton:pressed {'
            f' background: {self._color_to_css(self._mix_colors(button_hover, highlight, 0.14))};'
            '}'
            'QPushButton:checked {'
            f' background: {self._color_to_css(self._mix_colors(button_hover, highlight, 0.22))};'
            f' border: 1px solid {self._color_to_css(self._mix_colors(border, highlight, 0.56))};'
            '}'
            'QPushButton[primary="true"]:enabled {'
            f' background: {self._color_to_css(highlight)};'
            f' color: {self._color_to_css(selected_text)};'
            ' font-weight: 600;'
            '}'
            'QPushButton[primary="true"]:hover:enabled {'
            f' background: {self._color_to_css(self._mix_colors(highlight, base, 0.12))};'
            '}'
            'QPushButton:disabled {'
            f' color: {self._color_to_rgba(text, 110)};'
            f' background: {self._color_to_rgba(surface_bg, 180)};'
            '}'
        )
        for button in (
            self.load_btn,
            self.mode_btn,
            self.login_btn,
            self.refresh_btn,
            self.rooms_all_btn,
            self.rooms_followed_btn,
            self.play_btn,
            self.stop_btn,
            self.mute_btn,
            self.single_shot_btn,
            self.single_record_btn,
            self.send_btn,
            self.chat_sign_in,
            self.jump_to_latest,
        ):
            button.setStyleSheet(button_style)

        for button in (self.load_btn, self.mode_btn, self.login_btn):
            button.setStyleSheet(button_style.replace('padding: 8px 12px;', 'padding: 4px 8px;'))
        self.url_input.setStyleSheet(control_style.replace('padding: 8px 12px;', 'padding: 4px 8px;'))

        self.comment_view.setStyleSheet(
            'QTextEdit {'
            f' background: {self._color_to_css(base)};'
            f' color: {self._color_to_css(text)};'
            f' border: 1px solid {soft_border};'
            ' border-radius: 14px;'
            ' padding: 8px 10px;'
            f' selection-background-color: {self._color_to_css(highlight)};'
            '}'
        )
        self.comment_input.setStyleSheet(
            'QTextEdit {'
            f' background: {self._color_to_css(input_base)};'
            f' color: {self._color_to_css(text)};'
            f' border: 1px solid {soft_border};'
            ' border-radius: 14px;'
            ' padding: 8px 12px;'
            '}'
            'QTextEdit:focus {'
            f' border: 1px solid {self._color_to_css(self._mix_colors(border, highlight, 0.45))};'
            '}'
        )
        self.identity_label.setStyleSheet(f'color: {label_color};')
        self.comment_target_label.setStyleSheet(f'color: {label_color};')
        self.multi_hint_label.setStyleSheet(f'color: {label_color};')


        self.rooms_list.setStyleSheet(
            'QListWidget {'
            f' background: {self._color_to_css(base)};'
            f' color: {self._color_to_css(text)};'
            f' border: 1px solid {soft_border};'
            ' border-radius: 14px;'
            ' outline: 0;'
            '}'
            'QListWidget::item {'
            f' border-bottom: 1px solid {subtle_border};'
            ' padding: 11px 10px;'
            f' background: {self._color_to_css(base)};'
            '}'
            'QListWidget::item:selected {'
            f' background: {self._color_to_css(selected_bg)};'
            f' color: {self._color_to_css(selected_text)};'
            '}'
            'QListWidget::item:hover {'
            f' background: {self._color_to_css(hover_bg)};'
            '}'
        )

        self.video_widget.setStyleSheet(
            'background: black;'
            f'border: 1px solid {soft_border};'
            'border-radius: 16px;'
        )
        self._update_single_record_ui()
        for tile in self._multi_tiles:
            tile.apply_theme(base, window, text, border, highlight)

    def _style_for_key(self, key):
        key_text = str(key or '')
        style_index = sum(ord(ch) for ch in key_text) % len(self.COMMENT_ACCENTS)
        accent = QColor(self.COMMENT_ACCENTS[style_index])
        palette = self.palette()
        text = palette.color(QPalette.ColorRole.Text)
        border = palette.color(QPalette.ColorRole.Mid)
        return {
            'name': self._color_to_css(self._mix_colors(text, accent, 0.36)),
            'meta': self._color_to_css(self._mix_colors(text, border, 0.35)),
            'text': self._color_to_css(text),
            'divider': self._color_to_rgba(self._mix_colors(border, accent, 0.14), 82),
        }

    def _comment_identity(self, comment, fallback_name):
        user_info = comment.get('user')
        if not isinstance(user_info, dict):
            user_info = {}
        user_id = comment.get('user_id') or comment.get('uid') or user_info.get('id')
        account_id = comment.get('account_id') or user_info.get('account_id')
        if account_id:
            return f'@{account_id}', account_id
        if user_id is not None:
            return f'ID {user_id}', str(user_id)
        comment_id = comment.get('id')
        if comment_id is not None:
            return f'MSG {comment_id}', f'msg-{comment_id}'
        return 'Guest', fallback_name

    def _clear_comment_entries(self):
        self._comment_entries.clear()
        self.comment_view.clear()
        self._scroll_chat_to_bottom()

    def _append_entry(self, entry):
        self._append_entries([entry])

    def _append_entries(self, entries, unread=0):
        scroll = self.comment_view.verticalScrollBar()
        position = scroll.value()
        follow = scroll.maximum() - position <= 4
        self._comment_entries.extend(entries)
        limit = self.MAX_COMMENT_ENTRIES + self.COMMENT_PRUNE_BATCH if follow else 480
        if len(self._comment_entries) > limit:
            del self._comment_entries[:-self.MAX_COMMENT_ENTRIES]
            self._rerender_comment_entries()
        else:
            self._rendering_chat = True
            try:
                for entry in entries:
                    self._render_entry(entry)
            finally:
                self._rendering_chat = False
        if follow:
            self._scroll_chat_to_bottom()
        else:
            scroll.setValue(position)
            self._chat_unread += unread
            self.jump_to_latest.setText(f'View {self._chat_unread} new comments')
            self.jump_to_latest.setVisible(self._chat_unread > 0)

    def _rerender_comment_entries(self):
        scroll = self.comment_view.verticalScrollBar()
        position = scroll.value()
        follow = scroll.maximum() - position <= 4
        self._rendering_chat = True
        try:
            self.comment_view.clear()
            for entry in self._comment_entries:
                self._render_entry(entry)
        finally:
            self._rendering_chat = False
        scroll.setValue(scroll.maximum() if follow else position)

    def _on_chat_scrolled(self):
        if self._rendering_chat:
            return
        scroll = self.comment_view.verticalScrollBar()
        if scroll.maximum() - scroll.value() <= 4:
            self._chat_unread = 0
        self.jump_to_latest.setVisible(self._chat_unread > 0)

    def _scroll_chat_to_bottom(self):
        scroll = self.comment_view.verticalScrollBar()
        self._chat_unread = 0
        self.jump_to_latest.hide()
        scroll.setValue(scroll.maximum())

    def _render_entry(self, entry):
        if entry['kind'] == 'comment':
            self._render_comment(entry['name'], entry['text'], entry['comment'])
            return
        self._render_system_message(entry['text'])

    def _append_rich_block(self, html):
        cursor = self.comment_view.textCursor()
        cursor.movePosition(cursor.MoveOperation.End)
        cursor.insertHtml(html)
        cursor.insertBlock()

    def _render_comment(self, name, text, comment):
        identity, color_key = self._comment_identity(comment, name)
        style = self._style_for_key(color_key)
        safe_name = self._plain_to_html(name)
        safe_identity = self._plain_to_html(identity)
        safe_text = self._plain_to_html(text)
        html = f'''
        <table width="100%" cellspacing="0" cellpadding="0" style="margin:0;">
          <tr>
            <td style="padding:0 0 2px 0;">
              <span style="font-size:12px; font-weight:700; color:{style["name"]};">{safe_name}</span>
              <span style="font-size:10px; color:{style["meta"]};">  /  {safe_identity}</span>
            </td>
          </tr>
          <tr>
            <td style="
              padding:0 0 4px 0;
              color:{style["text"]};
              font-size:12px;
              line-height:1.3;
            ">{safe_text}</td>
          </tr>
          <tr>
            <td style="padding:0 0 4px 0;">
              <div style="border-top:1px dashed {style["divider"]}; height:1px;"></div>
            </td>
          </tr>
        </table>
        '''
        self._append_rich_block(html)

    def _append_system_message(self, text):
        self._append_entry({'kind': 'system', 'text': text})

    def _render_system_message(self, text):
        safe_text = self._plain_to_html(text)
        palette = self.palette()
        border = palette.color(QPalette.ColorRole.Mid)
        text_color = palette.color(QPalette.ColorRole.Text)
        meta = self._mix_colors(text_color, border, 0.42)
        html = f'''
        <table width="100%" cellspacing="0" cellpadding="0" style="margin:0;">
          <tr>
            <td style="
              color:{self._color_to_css(meta)};
              padding:1px 0 4px 0;
              font-size:11px;
            ">{safe_text}</td>
          </tr>
          <tr>
            <td style="padding:0 0 4px 0;">
              <div style="border-top:1px dashed {self._color_to_rgba(border, 82)}; height:1px;"></div>
            </td>
          </tr>
        </table>
        '''
        self._append_rich_block(html)

    def _update_identity_label(self, loading=False):
        if loading:
            self.identity_label.show()
            self.identity_label.setText('Loading account…')
            return
        if self._logged_in:
            display_name = (
                self._current_user_info.get('user_name')
                or self._current_user_info.get('name')
                or self._current_user_name
                or '...'
            )
            self.identity_label.show()
            self.identity_label.setText(f'Commenting as {display_name}')
            return
        self.identity_label.clear()
        self.identity_label.hide()

    def _update_comment_input_state(self):
        can_post = self._logged_in and bool(self._comment_live_id) and not self._closing
        pending = self._post_thread is not None
        self.chat_sign_in.setVisible(not self._logged_in)
        self.chat_sign_in.setEnabled(self.login_btn.isEnabled())
        self.comment_input.setVisible(self._logged_in)
        self.send_btn.setVisible(self._logged_in)
        self.comment_input.setEnabled(can_post and not pending)
        self.send_btn.setEnabled(can_post and not pending and bool(self.comment_input.toPlainText().strip()))
        self.send_btn.setText('Sending…' if pending else 'Send')
        self.comment_input.setPlaceholderText(
            'Write a message · Ctrl/⌘ + Enter' if self._comment_live_id else 'Select a live room'
        )

    # ── Rooms ────────────────────────────────────────────────────────────────

    def _effective_rooms_source(self):
        if self._rooms_source == 'followed' and self._logged_in:
            return 'followed'
        return 'all'

    def _update_room_source_buttons(self):
        followed_available = self._logged_in
        effective_source = self._effective_rooms_source()
        self.rooms_all_btn.setChecked(effective_source == 'all')
        self.rooms_followed_btn.setChecked(effective_source == 'followed')
        self.rooms_followed_btn.setEnabled(followed_available)

    def _set_rooms_source(self, source):
        normalized = 'followed' if source == 'followed' else 'all'
        if normalized == 'followed' and not self._logged_in:
            normalized = 'all'
        if self._rooms_source == normalized:
            self._update_room_source_buttons()
            return
        self._rooms_source = normalized
        self._update_room_source_buttons()
        self._refresh_rooms()

    def _rooms_box_title(self, source):
        return 'Followed Rooms' if source == 'followed' else 'Live Rooms'

    def _room_list_subtitle(self, room, source):
        if source != 'followed':
            return f'{room.get("viewers", 0):,} watching'
        if room.get('is_online'):
            return 'LIVE now'
        next_live = (room.get('next_live') or '').strip()
        if next_live and next_live != '未定':
            return f'Next: {next_live}'
        return 'Offline'

    def _refresh_rooms(self):
        source = self._effective_rooms_source()
        followed_only = source == 'followed'
        self._update_room_source_buttons()
        self.refresh_btn.setEnabled(False)
        self.rooms_list.clear()
        self.rooms_box.setTitle(self._rooms_box_title(source))
        self.status_bar.showMessage(
            'Loading followed rooms...' if followed_only else 'Loading live rooms...'
        )
        thread = LiveRoomsThread(followed_only=followed_only)
        self._live_rooms_thread = thread
        thread.rooms_ready.connect(
            lambda rooms, source_thread=thread, source_name=source:
            self._on_rooms_ready(source_thread, rooms, source_name)
        )
        thread.error.connect(
            lambda error_text, source_thread=thread:
            self._on_rooms_error(source_thread, error_text)
        )
        thread.finished.connect(
            lambda source_thread=thread: self._on_rooms_thread_finished(source_thread)
        )
        thread.start()

    def _on_rooms_ready(self, source_thread, rooms, source):
        if source_thread is not self._live_rooms_thread:
            return
        self.rooms_list.clear()
        self.rooms_box.setTitle(self._rooms_box_title(source))
        palette = self.palette()
        live_bg = self._mix_colors(
            palette.color(QPalette.ColorRole.Base),
            palette.color(QPalette.ColorRole.Highlight),
            0.20,
        )
        live_fg = self._mix_colors(
            palette.color(QPalette.ColorRole.Text),
            palette.color(QPalette.ColorRole.Highlight),
            0.18,
        )
        for room in rooms:
            item = QListWidgetItem(
                f'{room.get("name", room.get("key", ""))}\n'
                f'{self._room_list_subtitle(room, source)}'
            )
            item.setData(Qt.ItemDataRole.UserRole, room.get('key', ''))
            if source == 'followed' and room.get('is_online'):
                font = item.font()
                font.setBold(True)
                item.setFont(font)
                item.setBackground(QBrush(live_bg))
                item.setForeground(QBrush(live_fg))
                item.setToolTip('Currently live')
            self.rooms_list.addItem(item)
        summary = 'followed rooms' if source == 'followed' else 'live rooms'
        self.status_bar.showMessage(f'{len(rooms)} {summary}')

    def _on_rooms_error(self, source_thread, message):
        if source_thread is not self._live_rooms_thread:
            return
        self.status_bar.showMessage(f'Rooms error: {message}')

    def _on_rooms_thread_finished(self, source_thread):
        if source_thread is not self._live_rooms_thread:
            return
        self.refresh_btn.setEnabled(True)

    def _on_room_double_clicked(self, item):
        key = item.data(Qt.ItemDataRole.UserRole)
        self.url_input.setText(key)
        self.load_room()

    # ── Lifecycle ────────────────────────────────────────────────────────────

    def changeEvent(self, event):
        if event.type() in {
            QEvent.Type.PaletteChange,
            getattr(QEvent.Type, 'ApplicationPaletteChange', QEvent.Type.PaletteChange),
        }:
            self._apply_ui_theme()
            self._rerender_comment_entries()
        super().changeEvent(event)

    def eventFilter(self, obj, event):
        try:
            event_type = event.type() if event is not None else None
            comment_input = getattr(self, 'comment_input', None)

            if comment_input is not None and obj is comment_input and event_type == QEvent.Type.KeyPress:
                if event.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
                    modifiers = event.modifiers()
                    if modifiers & (Qt.KeyboardModifier.ControlModifier | Qt.KeyboardModifier.MetaModifier):
                        self._post_comment()
                        return True

            return super().eventFilter(obj, event)
        except Exception:
            return False

    def closeEvent(self, event):
        if not self._closing:
            self._closing = True
            self.setEnabled(False)
            self.status_bar.showMessage('Closing. Waiting for background tasks…')
            self._stop_comments()
            for thread in (*self._single_load_threads.values(), *self._multi_load_threads.values()):
                thread.requestInterruption()
            self.playback.reset_room()
            for tile in self._multi_tiles:
                tile.playback.reset_room()

        threads = (
            self._live_rooms_thread,
            getattr(self, '_current_user_thread', None),
            getattr(self, '_post_thread', None),
            self._update_check_thread,
            *self._comment_threads,
            *self._single_load_threads.values(),
            *self._multi_load_threads.values(),
        )
        recorders = (self._single_recorder, *(tile.recorder for tile in self._multi_tiles))
        if any(thread is not None and thread.isRunning() for thread in threads) or any(
            recorder.is_recording() for recorder in recorders
        ):
            event.ignore()
            QTimer.singleShot(100, self.close)
            return
        event.accept()
