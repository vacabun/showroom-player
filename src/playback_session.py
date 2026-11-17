from PySide6.QtCore import QObject, Qt, QUrl
from PySide6.QtGui import QDesktopServices, QGuiApplication, QImage
from PySide6.QtMultimedia import QAudioOutput, QMediaPlayer, QVideoFrame

from PySide6.QtWidgets import QMenu, QMessageBox

from .recording import StreamRecorder


class PlaybackSession(QObject):
    def __init__(self, video_widget, volume, parent):
        super().__init__(parent)
        self._player = QMediaPlayer(self)
        self._audio = QAudioOutput(self)
        self._audio.setVolume(volume)
        self._player.setAudioOutput(self._audio)
        self._player.setVideoOutput(video_widget)
        self._sink = video_widget.videoSink()
        self.recorder = StreamRecorder(self)
        self._request = 0

    def install_video_menu(self, video_widget, room_key, selected_stream):
        video_widget.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        video_widget.customContextMenuRequested.connect(
            lambda position: self._show_video_menu(video_widget, position, room_key(), selected_stream())
        )

    def _show_video_menu(self, widget, position, room_key, stream):
        menu = QMenu(widget)
        statistics = menu.addAction('Playback Statistics')
        statistics.setEnabled(stream is not None)
        copy_stream = menu.addAction('Copy M3U8 URL')
        copy_stream.setEnabled(stream is not None)
        open_room = menu.addAction('Open SHOWROOM Room in Browser')
        open_room.setEnabled(bool(room_key))
        action = menu.exec(widget.mapToGlobal(position))
        if action is statistics and stream:
            self._show_statistics(widget, stream[0])
        elif action is copy_stream and stream:
            QGuiApplication.clipboard().setText(stream[1])
        elif action is open_room and room_key:
            entered_url = QUrl(room_key)
            key = entered_url.path().rstrip('/').rsplit('/', 1)[-1] if entered_url.host() else room_key
            url = QUrl('https://www.showroom-live.com')
            url.setPath('/r/' + key)
            if not QDesktopServices.openUrl(url):
                QMessageBox.warning(widget, 'Open SHOWROOM Room', 'Could not open the system browser.')
        menu.deleteLater()

    def _show_statistics(self, widget, quality):
        size = self._sink.videoSize()
        state = self.state
        lines = [f'Selected quality: {quality}', f'Playback: {state.name.removesuffix("State")}']
        if state != QMediaPlayer.PlaybackState.StoppedState:
            if not size.isEmpty():
                lines.append(f'Video size: {size.width()} × {size.height()}')
            lines.append(f'Buffer: {self._player.bufferProgress():.0%}')
        dialog = QMessageBox(widget)
        dialog.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)
        dialog.setWindowTitle('Playback Statistics')
        dialog.setTextFormat(Qt.TextFormat.PlainText)
        dialog.setText('\n'.join(lines))
        dialog.open()

    @property
    def state_changed(self):
        return self._player.playbackStateChanged

    @property
    def error_occurred(self):
        return self._player.errorOccurred

    @property
    def state(self):
        return self._player.playbackState()

    def set_volume(self, volume):
        self._audio.setVolume(volume)

    def set_muted(self, muted):
        self._audio.setMuted(muted)

    def reset_room(self):
        self._request += 1
        self.recorder.stop_recording(silent=True)
        self.stop(clear_source=True)
        return self._request

    def is_current_request(self, request):
        return request == self._request

    def play(self, url):
        source = QUrl(url)
        if source != self._player.source():
            self._sink.setVideoFrame(QVideoFrame())
            self._player.setSource(source)
        self._player.play()

    def toggle(self, play_selected):
        if self.state == QMediaPlayer.PlaybackState.PlayingState:
            self._player.pause()
        elif self.state == QMediaPlayer.PlaybackState.PausedState:
            self._player.play()
        else:
            play_selected()

    def stop(self, *, clear_source=False):
        self._player.stop()
        if clear_source:
            self._player.setSource(QUrl())
        self._sink.setVideoFrame(QVideoFrame())

    def screenshot(self):
        if self.state == QMediaPlayer.PlaybackState.StoppedState:
            return QImage()
        return self._sink.videoFrame().toImage()
