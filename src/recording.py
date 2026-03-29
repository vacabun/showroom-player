import shutil

from PySide6.QtCore import QObject, QProcess, QTimer, Signal

from .media_output import build_timestamped_download_path


class StreamRecorder(QObject):
    state_changed = Signal(bool)
    started = Signal(str)
    stopped = Signal(str)
    error = Signal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._process = None
        self._output_path = ''
        self._log_buffer = ''
        self._has_started = False
        self._stopping = False
        self._announce_stop = True

    def is_recording(self):
        return (
            self._process is not None
            and self._process.state() != QProcess.ProcessState.NotRunning
        )

    def start_recording(self, room_name, stream_url):
        if self.is_recording():
            return False

        ffmpeg_path = shutil.which('ffmpeg')
        if not ffmpeg_path:
            self.error.emit('Recording requires ffmpeg to be available in PATH.')
            return False

        self._output_path = self._build_output_path(room_name)
        self._log_buffer = ''
        self._has_started = False
        self._stopping = False
        self._announce_stop = True

        process = QProcess(self)
        process.setProgram(ffmpeg_path)
        process.setArguments([
            '-hide_banner',
            '-loglevel',
            'error',
            '-i',
            stream_url,
            '-c',
            'copy',
            '-movflags',
            '+faststart',
            self._output_path,
        ])
        process.setProcessChannelMode(QProcess.ProcessChannelMode.MergedChannels)
        process.started.connect(self._on_started)
        process.readyReadStandardOutput.connect(self._consume_output)
        process.errorOccurred.connect(self._on_error)
        process.finished.connect(self._on_finished)
        self._process = process
        process.start()
        return True

    def stop_recording(self, silent=False):
        if not self.is_recording():
            return
        self._stopping = True
        self._announce_stop = not silent
        self._process.write(b'q\n')
        QTimer.singleShot(3000, self._terminate_if_needed)

    def wait_for_stop(self, timeout_ms=5000):
        if self._process is None:
            return
        if self._process.state() == QProcess.ProcessState.NotRunning:
            return
        if self._process.waitForFinished(timeout_ms):
            return
        self._process.terminate()
        if self._process.waitForFinished(1500):
            return
        self._process.kill()
        self._process.waitForFinished(1500)

    def _on_started(self):
        self._has_started = True
        self.state_changed.emit(True)
        self.started.emit(self._output_path)

    def _consume_output(self):
        if self._process is None:
            return
        data = self._process.readAllStandardOutput().data().decode('utf-8', errors='replace')
        if not data:
            return
        self._log_buffer = (self._log_buffer + '\n' + data).strip()[-4000:]

    def _on_error(self, process_error):
        if process_error != QProcess.ProcessError.FailedToStart:
            return
        self._consume_output()
        message = self._last_log_line() or 'Failed to start ffmpeg.'
        self._cleanup_process()
        self.error.emit(message)

    def _on_finished(self, exit_code, exit_status):
        if self._process is None:
            return
        self._consume_output()
        was_started = self._has_started
        was_stopping = self._stopping
        announce_stop = self._announce_stop
        output_path = self._output_path
        normal_exit = exit_status == QProcess.ExitStatus.NormalExit and exit_code == 0
        message = self._last_log_line()
        self._cleanup_process()

        if was_started:
            self.state_changed.emit(False)

        if was_stopping:
            if announce_stop and output_path:
                self.stopped.emit(output_path)
            return

        if normal_exit and output_path:
            self.stopped.emit(output_path)
            return

        if output_path and not Path(output_path).exists():
            self.error.emit(message or 'Recording stopped before the file was created.')
            return

        self.error.emit(message or f'Recording stopped unexpectedly (exit code {exit_code}).')

    def _terminate_if_needed(self):
        if not self.is_recording():
            return
        self._process.terminate()
        QTimer.singleShot(1500, self._kill_if_needed)

    def _kill_if_needed(self):
        if self.is_recording():
            self._process.kill()

    def _cleanup_process(self):
        if self._process is not None:
            self._process.deleteLater()
        self._process = None
        self._output_path = ''
        self._log_buffer = ''
        self._has_started = False
        self._stopping = False
        self._announce_stop = True

    def _last_log_line(self):
        for line in reversed(self._log_buffer.splitlines()):
            text = line.strip()
            if text:
                return text
        return ''

    @classmethod
    def _build_output_path(cls, room_name):
        return str(build_timestamped_download_path(room_name, 'mp4'))
