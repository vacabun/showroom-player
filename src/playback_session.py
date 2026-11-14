from PySide6.QtCore import QObject, QUrl
from PySide6.QtGui import QImage
from PySide6.QtMultimedia import QAudioOutput, QMediaPlayer, QVideoFrame

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
